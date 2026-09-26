#!/usr/bin/env python3
"""Shared helpers for the Moonshine v2 job scripts (generate / train+eval).

Layout (all under DISTILL_HOME, default ~/.distill):
  jobs/<id>/meta.json          job definition (written by the web app / ctl)
  results/<id>/status.json     lifecycle state (written ONLY by queue_runner.py)
  results/<id>/progress.json   live progress (written by the job script)
  results/<id>/log.txt         stdout/stderr of the job script
  results/<id>/exit.json       exit record written by the job script
  queue/control/<id>.json      pause/resume/cancel/purge requests (web -> runner)
  users.json                   list of user names
  ollama-models.json           Ollama tags registered by the app
"""
from __future__ import annotations

import json
import math
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

DISTILL_HOME = os.environ.get("DISTILL_HOME", os.path.expanduser("~/.distill"))
JOBS_DIR = os.path.join(DISTILL_HOME, "jobs")
RESULTS_DIR = os.path.join(DISTILL_HOME, "results")
QUEUE_DIR = os.path.join(DISTILL_HOME, "queue")
CONTROL_DIR = os.path.join(QUEUE_DIR, "control")
USERS_FILE = os.path.join(DISTILL_HOME, "users.json")
OLLAMA_REGISTRY = os.path.join(DISTILL_HOME, "ollama-models.json")
# Runtime copies (APFS clones) of the student folders. launchd jobs cannot read
# ~/Documents (macOS privacy protection), so the canonical downloads in
# /Users/aimac/Documents/mlx_models are cloned here.
MLX_MODELS_DIR = os.environ.get("MOONSHINE_MLX_MODELS_DIR", os.path.join(DISTILL_HOME, "models", "students"))

EXIT_PAUSED = 75  # job stopped at a safe point because it was asked to

STUDENTS = {
    "qwen3-0.6b": {"label": "Qwen3 0.6B", "dir": "Qwen3-0.6B-4bit", "tag_prefix": "qwen3-0.6b"},
    "qwen3-1.7b": {"label": "Qwen3 1.7B", "dir": "Qwen3-1.7B-4bit", "tag_prefix": "qwen3-1.7b"},
    "qwen3-4b": {"label": "Qwen3 4B", "dir": "Qwen3-4B-4bit", "tag_prefix": "qwen3-4b"},
    "qwen3-8b": {"label": "Qwen3 8B", "dir": "Qwen3-8B-4bit", "tag_prefix": "qwen3-8b"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env_file() -> dict:
    """Parse ~/.distill/env (KEY=VALUE lines). Never printed."""
    out = {}
    try:
        with open(os.path.join(DISTILL_HOME, "env")) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.replace("export ", "").strip()
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k] = v
    except OSError:
        pass
    return out


def setting(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key) or load_env_file().get(key) or default


OLLAMA_URL = (setting("OLLAMA_URL") or setting("DIAGNOSTIC_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")


def read_json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def job_meta(job_id: str) -> dict:
    meta = read_json(os.path.join(JOBS_DIR, job_id, "meta.json"))
    if not meta:
        raise SystemExit(f"job {job_id}: meta.json missing")
    return meta


def results_dir(job_id: str) -> str:
    d = os.path.join(RESULTS_DIR, job_id)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- progress
class Progress:
    """Writes results/<id>/progress.json with a speed-based ETA.

    Speed is measured only over work done in THIS process (so a resume does
    not count previously finished items as instant)."""

    def __init__(self, job_id: str, phases: list[str] | None = None):
        self.path = os.path.join(results_dir(job_id), "progress.json")
        prev = read_json(self.path, {}) or {}
        self.data = {"phases": phases or [], "phase": prev.get("phase"), "phase_done": prev.get("phase_done", [])}
        for k in ("extra",):
            if k in prev:
                self.data[k] = prev[k]
        self._t0 = None
        self._n0 = 0
        self._last_write = 0.0

    def start_phase(self, phase: str, total: int, done: int = 0, unit: str = "items", label: str | None = None):
        self.data.update({"phase": phase, "phase_label": label or phase, "done": done, "total": total, "unit": unit,
                          "rate_per_sec": None, "eta_sec": None, "phase_started_at": now_iso()})
        self._t0 = time.time()
        self._n0 = done
        self.write(force=True)

    def update(self, done: int, force: bool = False, **extra):
        self.data["done"] = done
        elapsed = time.time() - (self._t0 or time.time())
        worked = done - self._n0
        if worked > 0 and elapsed > 0:
            rate = worked / elapsed
            self.data["rate_per_sec"] = round(rate, 4)
            remaining = max(0, (self.data.get("total") or 0) - done)
            self.data["eta_sec"] = int(remaining / rate) if rate > 0 else None
        if extra:
            self.data.setdefault("extra", {}).update(extra)
        self.write(force=force)

    def finish_phase(self):
        p = self.data.get("phase")
        if p and p not in self.data["phase_done"]:
            self.data["phase_done"].append(p)
        self.data["eta_sec"] = 0
        self.write(force=True)

    def set_extra(self, **extra):
        self.data.setdefault("extra", {}).update(extra)
        self.write(force=True)

    def write(self, force: bool = False):
        if not force and time.time() - self._last_write < 2:
            return
        self.data["updated_at"] = now_iso()
        write_json_atomic(self.path, self.data)
        self._last_write = time.time()


# ---------------------------------------------------------------- stop handling
class StopRequested(Exception):
    pass


_stop = {"requested": False, "critical": 0}


def _handler(signum, frame):
    _stop["requested"] = True
    if _stop["critical"] == 0:
        raise StopRequested()


def install_stop_handlers():
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


class critical:
    """Context manager: defer a stop until the block (e.g. a file append) finishes."""

    def __enter__(self):
        _stop["critical"] += 1

    def __exit__(self, *a):
        _stop["critical"] -= 1
        if _stop["critical"] == 0 and _stop["requested"]:
            raise StopRequested()


def check_stop():
    if _stop["requested"]:
        raise StopRequested()


def run_main(job_id: str, fn) -> None:
    """Run a job body, translating a stop into exit code 75 and recording exit.json."""
    install_stop_handlers()
    rd = results_dir(job_id)
    exit_path = os.path.join(rd, "exit.json")
    try:
        os.remove(exit_path)
    except OSError:
        pass
    code, reason = 0, "ok"
    try:
        fn()
    except StopRequested:
        code, reason = EXIT_PAUSED, "stopped at a safe point"
        log("Stop requested: progress saved, exiting.")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        reason = str(e.code) if not isinstance(e.code, int) else ("ok" if code == 0 else "exit")
        if code != 0:
            log(f"ERROR: {reason}")
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        code, reason = 1, f"{type(e).__name__}: {e}"
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    write_json_atomic(exit_path, {"code": code, "reason": reason[:2000], "at": now_iso()})
    sys.stdout.flush()
    os._exit(code)


# ---------------------------------------------------------------- ollama
def ollama_request(path: str, payload: dict | None = None, method: str | None = None, timeout: float = 1800):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{OLLAMA_URL}{path}", data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()
    return json.loads(body) if body.strip() else {}


def _param_count(model: dict) -> float:
    size = (model.get("details") or {}).get("parameter_size") or ""
    m = re.match(r"([\d.]+)\s*([KMBT])", size.upper())
    if m:
        return float(m.group(1)) * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)]
    try:
        info = ollama_request("/api/show", {"model": model["name"]}, timeout=30)
        for k, v in (info.get("model_info") or {}).items():
            if k.endswith("parameter_count") and isinstance(v, (int, float)):
                return float(v)
        m = re.search(r"parameters\s+([\d.]+)([KMBT])", info.get("parameters", "") + " " + json.dumps(info.get("details", {})))
        if m:
            return float(m.group(1)) * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)]
    except Exception:  # noqa: BLE001
        pass
    # Fallback: ~0.6 bytes/param at 4-bit
    return (model.get("size") or 0) / 0.6


def app_registered_tags() -> set[str]:
    reg = read_json(OLLAMA_REGISTRY, []) or []
    return {r.get("tag") for r in reg}


def pick_teacher() -> str:
    """TEACHER_MODEL in ~/.distill/env wins; otherwise the largest Ollama model
    by parameter count (models the app registered itself are excluded)."""
    configured = setting("TEACHER_MODEL")
    if configured and configured.lower() != "auto":
        return configured
    models = ollama_request("/api/tags", timeout=30).get("models", [])
    own = app_registered_tags()
    models = [m for m in models if m["name"] not in own and not m["name"].split(":")[0].startswith("moonshine-")]
    if not models:
        return "qwen3.8:27b-mlx"
    best = max(models, key=_param_count)
    return best["name"]


def teacher_chat(model: str, messages: list[dict], temperature: float = 0.7, num_predict: int = 4096,
                 num_ctx: int = 16384, fmt=None, timeout: float = 1800, think: bool = False) -> dict:
    payload = {
        "model": model, "messages": messages, "stream": False, "think": think,
        "keep_alive": "15m",
        "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": num_ctx},
    }
    if fmt is not None:
        payload["format"] = fmt
    t0 = time.time()
    r = ollama_request("/api/chat", payload, timeout=timeout)
    r["_wall_sec"] = time.time() - t0
    return r


# ---------------------------------------------------------------- data helpers
def norm_prompt(p: str) -> str:
    return re.sub(r"\s+", " ", p.strip().lower())


def slugify(name: str, maxlen: int = 24) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s[:maxlen].strip("-")) or "user"


def valid_pair(obj) -> bool:
    return (isinstance(obj, dict) and set(obj.keys()) == {"prompt", "completion"}
            and isinstance(obj["prompt"], str) and isinstance(obj["completion"], str)
            and obj["prompt"].strip() != "" and obj["completion"].strip() != "")


def read_pairs(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("prompt"), str) and isinstance(obj.get("completion"), str):
                out.append({"prompt": obj["prompt"], "completion": obj["completion"]})
    return out


def fmt_duration(sec: float | None) -> str:
    if sec is None or math.isnan(sec):
        return "?"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")
