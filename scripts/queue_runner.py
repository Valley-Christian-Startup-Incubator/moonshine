#!/usr/bin/env python3
"""Moonshine queue runner (launchd: com.distill.queue).

One job at a time, first in first out. The web app never starts processes;
it writes jobs/<id>/meta.json + results/<id>/status.json {"status":"queued"}
and drops control requests in queue/control/<id>.json:
  {"action": "pause" | "resume" | "cancel" | "purge"}

Rules:
- If a job is RUNNING, nothing else starts.
- If any job is PAUSED, the whole queue waits until it is resumed or cancelled.
- PAUSE sends SIGTERM to the job's process group; the job saves progress at a
  safe point and exits with code 75 -> status "paused".
- RESUME puts the paused job back at the head of the queue; it restarts from
  its saved progress.
- CANCEL stops (if running) and marks "cancelled"; the job leaves the queue.
- PURGE = cancel + delete the job's files and the Ollama models it registered
  (used when an admin deletes a user).
This runner is the ONLY writer of status.json for v2 jobs.

GGUF exports ("Build Ollama download" on a finished train job): the web app
drops queue/gguf-requests/<id>.json; the runner starts scripts/export_gguf.py
in the background (one export at a time, independent of the job queue). The
export reports progress in results/<id>/gguf_status.json.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moonshine_common as mc  # noqa: E402

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
POLL = 1.5
STOP_GRACE_SEC = 90
TERMINAL = {"completed", "failed", "cancelled"}
JOB_SCRIPTS = {"generate": "gen_questions.py", "train": "train_eval.py"}
GGUF_REQ_DIR = os.path.join(mc.QUEUE_DIR, "gguf-requests")


def status_path(job_id):
    return os.path.join(mc.RESULTS_DIR, job_id, "status.json")


def read_status(job_id):
    return mc.read_json(status_path(job_id), {}) or {}


def write_status(job_id, **fields):
    st = read_status(job_id)
    st.update(fields)
    st["updated_at"] = mc.now_iso()
    mc.write_json_atomic(status_path(job_id), st)
    return st


def v2_jobs():
    out = []
    try:
        ids = os.listdir(mc.JOBS_DIR)
    except OSError:
        return out
    for jid in ids:
        meta = mc.read_json(os.path.join(mc.JOBS_DIR, jid, "meta.json"))
        if not meta or meta.get("version") != 2 or meta.get("type") not in JOB_SCRIPTS:
            continue
        out.append((jid, meta, read_status(jid)))
    return out


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # zombie check
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(out) and not out.startswith("Z")
    except Exception:  # noqa: BLE001
        return True


def dagu_busy() -> bool:
    """Avoid overlapping with legacy jobs that the old UI submitted straight to Dagu."""
    env = mc.load_env_file()
    base = env.get("DAGU_BASE_URL", "http://127.0.0.1:8081")
    import base64
    auth = base64.b64encode(f"{env.get('DAGU_USER', 'admin')}:{env.get('DAGU_PASSWORD', '')}".encode()).decode()
    try:
        for status in (1,):  # 1 = running
            req = urllib.request.Request(f"{base}/api/v2/dag-runs?status={status}", headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                if (json.loads(r.read().decode()).get("dagRuns") or []):
                    return True
    except Exception:  # noqa: BLE001
        return False
    return False


def delete_ollama_tag(tag):
    try:
        req = urllib.request.Request(f"{mc.OLLAMA_URL}/api/delete", data=json.dumps({"model": tag}).encode(),
                                     method="DELETE", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=60).read()
        mc.log(f"Deleted Ollama model {tag}")
    except Exception as e:  # noqa: BLE001
        mc.log(f"Could not delete Ollama model {tag}: {e}")


class Runner:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.cur: str | None = None
        self.stop_reason: str | None = None  # 'pause' | 'cancel' | 'purge'
        self.stop_sent_at = 0.0
        os.makedirs(mc.CONTROL_DIR, exist_ok=True)
        os.makedirs(GGUF_REQ_DIR, exist_ok=True)
        self.gguf_proc: subprocess.Popen | None = None

    # ---------------------------------------------------------- process control
    def start(self, jid, meta):
        rd = mc.results_dir(jid)
        for stale in ("diagnosis.md", "suggested_retry.json", "exit.json"):
            try:
                os.remove(os.path.join(rd, stale))
            except OSError:
                pass
        log = open(os.path.join(rd, "log.txt"), "a")
        st = read_status(jid)
        attempt = int(st.get("attempts", 0)) + 1
        log.write(f"\n===== {mc.now_iso()} start attempt {attempt} ({'resume' if attempt > 1 else 'fresh'}) =====\n")
        log.flush()
        env = {**os.environ, "DISTILL_HOME": mc.DISTILL_HOME, "PYTHONUNBUFFERED": "1",
               "PATH": f"{os.path.dirname(PY)}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
        cmd = [PY, os.path.join(SCRIPTS, JOB_SCRIPTS[meta["type"]]), "--job-id", jid]
        self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     cwd=rd, env=env, start_new_session=True)
        log.close()
        self.cur, self.stop_reason = jid, None
        write_status(jid, status="running", pid=self.proc.pid, attempts=attempt,
                     started_at=st.get("started_at") or mc.now_iso(), last_started_at=mc.now_iso(),
                     error=None, pause_requested=False)
        mc.log(f"Started {meta['type']} job {jid} (pid {self.proc.pid}, attempt {attempt})")

    def signal_stop(self, reason):
        if not self.proc:
            return
        self.stop_reason = reason
        self.stop_sent_at = time.time()
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        write_status(self.cur, stopping=reason)
        mc.log(f"Sent stop ({reason}) to job {self.cur}")

    def reap(self):
        """Check the running child; handle exit."""
        if not self.proc:
            return
        rc = self.proc.poll()
        if rc is None:
            if self.stop_reason and time.time() - self.stop_sent_at > STOP_GRACE_SEC:
                mc.log(f"Job {self.cur} ignored SIGTERM for {STOP_GRACE_SEC}s; killing")
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return
        jid, reason = self.cur, self.stop_reason
        # make sure stray children (mlx_lm.lora etc.) are gone
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc, self.cur, self.stop_reason = None, None, None
        self.finish(jid, rc, reason)

    def finish(self, jid, rc, reason):
        ex = mc.read_json(os.path.join(mc.RESULTS_DIR, jid, "exit.json"), {}) or {}
        common = {"pid": None, "stopping": None, "exit_code": rc}
        if reason == "pause":
            write_status(jid, status="paused", paused_at=mc.now_iso(), **common)
            mc.log(f"Job {jid} paused (exit {rc})")
        elif reason in ("cancel", "purge"):
            write_status(jid, status="cancelled", completed_at=mc.now_iso(), **common)
            mc.log(f"Job {jid} cancelled")
            if reason == "purge":
                self.purge_files(jid)
        elif rc == 0:
            out = self.output_path(jid)
            write_status(jid, status="completed", completed_at=mc.now_iso(), output_path=out, **common)
            mc.log(f"Job {jid} completed")
        else:
            err = ex.get("reason") or f"exit code {rc}"
            write_status(jid, status="failed", completed_at=mc.now_iso(), error=err, **common)
            mc.log(f"Job {jid} failed: {err}")
            self.diagnose_async(jid)

    @staticmethod
    def output_path(jid):
        rd = os.path.join(mc.RESULTS_DIR, jid)
        for name in ("eval_report.md", "output.jsonl"):
            if os.path.exists(os.path.join(rd, name)):
                return os.path.join(rd, name)
        return None

    @staticmethod
    def diagnose_async(jid):
        script = os.path.join(SCRIPTS, "diagnose_job.py")
        if not os.path.exists(script):
            return

        def go():
            try:
                subprocess.run([PY, script, "--job-id", jid], timeout=180, capture_output=True,
                               env={**os.environ, "DISTILL_HOME": mc.DISTILL_HOME})
            except Exception as e:  # noqa: BLE001
                mc.log(f"diagnosis for {jid} failed: {e}")
        threading.Thread(target=go, daemon=True).start()

    @staticmethod
    def purge_files(jid):
        reg = mc.read_json(mc.OLLAMA_REGISTRY, []) or []
        keep = []
        for r in reg:
            if r.get("jobId") == jid:
                delete_ollama_tag(r["tag"])
            else:
                keep.append(r)
        mc.write_json_atomic(mc.OLLAMA_REGISTRY, keep)
        for d in (os.path.join(mc.JOBS_DIR, jid), os.path.join(mc.RESULTS_DIR, jid)):
            shutil.rmtree(d, ignore_errors=True)
        mc.log(f"Purged job {jid}")

    # ---------------------------------------------------------- control requests
    def handle_controls(self):
        try:
            files = sorted(os.listdir(mc.CONTROL_DIR))
        except OSError:
            return
        for fn in files:
            if not fn.endswith(".json"):
                continue
            path = os.path.join(mc.CONTROL_DIR, fn)
            req = mc.read_json(path, {}) or {}
            jid, action = fn[:-5], req.get("action")
            try:
                os.remove(path)
            except OSError:
                pass
            st = read_status(jid).get("status")
            mc.log(f"Control: {action} {jid} (status {st})")
            if action == "pause":
                if self.cur == jid and self.proc:
                    write_status(jid, pause_requested=True)
                    self.signal_stop("pause")
            elif action == "resume":
                # paused -> continue; failed -> retry in place from saved progress
                if st in ("paused", "failed") and self.cur != jid:
                    write_status(jid, status="queued", resumed_at=mc.now_iso(), priority=1)
            elif action in ("cancel", "purge"):
                if self.cur == jid and self.proc:
                    self.signal_stop(action)
                elif st not in TERMINAL or action == "purge":
                    if st not in TERMINAL:
                        write_status(jid, status="cancelled", completed_at=mc.now_iso(), pid=None)
                    if action == "purge":
                        self.purge_files(jid)

    # ---------------------------------------------------------- GGUF export requests
    def handle_gguf_requests(self):
        if self.gguf_proc and self.gguf_proc.poll() is None:
            return  # one export at a time; later requests wait in the folder
        self.gguf_proc = None
        try:
            files = sorted(f for f in os.listdir(GGUF_REQ_DIR) if f.endswith(".json"))
        except OSError:
            return
        for fn in files:
            jid = fn[:-5]
            try:
                os.remove(os.path.join(GGUF_REQ_DIR, fn))
            except OSError:
                pass
            meta = mc.read_json(os.path.join(mc.JOBS_DIR, jid, "meta.json"), {}) or {}
            rd = os.path.join(mc.RESULTS_DIR, jid)
            if meta.get("type") != "train" or read_status(jid).get("status") != "completed":
                mc.log(f"GGUF request for {jid} ignored (not a completed train job)")
                if os.path.isdir(rd):
                    mc.write_json_atomic(os.path.join(rd, "gguf_status.json"),
                                         {"state": "failed", "message": "Build failed", "pid": None,
                                          "error": "Only finished train jobs can be exported.",
                                          "updated_at": mc.now_iso()})
                continue
            log = open(os.path.join(rd, "gguf_log.txt"), "a")
            log.write(f"\n===== {mc.now_iso()} GGUF export requested from the web page =====\n")
            log.flush()
            env = {**os.environ, "DISTILL_HOME": mc.DISTILL_HOME, "PYTHONUNBUFFERED": "1",
                   "PATH": f"{os.path.dirname(PY)}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
            self.gguf_proc = subprocess.Popen([PY, os.path.join(SCRIPTS, "export_gguf.py"), jid, "--force"],
                                              stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                              cwd=rd, env=env, start_new_session=True)
            log.close()
            mc.log(f"Started GGUF export for {jid} (pid {self.gguf_proc.pid})")
            return

    # ---------------------------------------------------------- recovery / scheduling
    def recover(self):
        """On runner start: jobs marked running without a live runner child."""
        for jid, meta, st in v2_jobs():
            if st.get("status") == "running":
                pid = st.get("pid")
                if pid_alive(pid):
                    mc.log(f"Orphaned job {jid} (pid {pid}) still alive; stopping it and requeueing")
                    try:
                        os.killpg(pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                    for _ in range(60):
                        if not pid_alive(pid):
                            break
                        time.sleep(1)
                    else:
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            pass
                if st.get("pause_requested"):
                    write_status(jid, status="paused", pid=None)
                else:
                    write_status(jid, status="queued", pid=None, priority=1)
                    mc.log(f"Requeued interrupted job {jid} at the head of the queue")

    def schedule(self):
        if self.proc:
            return
        jobs = v2_jobs()
        if any(st.get("status") == "paused" for _, _, st in jobs):
            return
        queued = [(jid, meta, st) for jid, meta, st in jobs if st.get("status") == "queued"]
        if not queued:
            return
        if dagu_busy():
            return
        queued.sort(key=lambda x: (-int(x[2].get("priority", 0)), x[1].get("submittedAt", "")))
        jid, meta, _ = queued[0]
        if not os.path.exists(meta.get("inputFile", "")):
            write_status(jid, status="failed", error="Input file is missing", completed_at=mc.now_iso())
            return
        write_status(jid, priority=0)
        self.start(jid, meta)

    def loop(self):
        mc.log(f"Queue runner started (DISTILL_HOME={mc.DISTILL_HOME})")
        self.recover()

        def on_term(signum, frame):
            mc.log("Runner stopping; pausing the running job so it can resume later")
            if self.proc:
                self.signal_stop("pause")
                try:
                    self.proc.wait(timeout=STOP_GRACE_SEC)
                except subprocess.TimeoutExpired:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                # runner shutdown is not a user pause: requeue at the head
                write_status(self.cur, status="queued", pid=None, priority=1, stopping=None)
            sys.exit(0)

        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
        while True:
            try:
                self.handle_controls()
                self.handle_gguf_requests()
                self.reap()
                self.schedule()
                with open(os.path.join(mc.QUEUE_DIR, "runner-heartbeat"), "w") as f:
                    f.write(mc.now_iso())
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                mc.log(f"runner loop error: {e}")
            time.sleep(POLL)


if __name__ == "__main__":
    os.makedirs(mc.QUEUE_DIR, exist_ok=True)
    Runner().loop()
