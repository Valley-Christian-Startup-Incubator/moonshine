#!/usr/bin/env python3
"""Command-line control for Moonshine v2 jobs (same file protocol as the web app).

  moonshine_ctl.py submit-generate --owner NAME --prompt-file P [--count 3000]
  moonshine_ctl.py submit-train --owner NAME --data FILE --student qwen3-0.6b [--param KEY=VAL ...]
  moonshine_ctl.py pause|resume|cancel JOB_ID
  moonshine_ctl.py list
  moonshine_ctl.py users [add NAME | delete NAME]

Deleting a name also scrubs ~/.distill/logs/*.log the same way the admin web
delete does (web/src/lib/server/log-scrub.ts).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moonshine_common as mc  # noqa: E402


def new_id(n=10):
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def submit(job_type, owner, input_src, params):
    jid = new_id()
    jd = os.path.join(mc.JOBS_DIR, jid)
    os.makedirs(jd)
    dst = os.path.join(jd, os.path.basename(input_src))
    shutil.copy(input_src, dst)
    meta = {"version": 2, "id": jid, "type": job_type, "ownerName": owner, "params": params,
            "inputFile": dst, "submittedAt": mc.now_iso()}
    mc.write_json_atomic(os.path.join(jd, "meta.json"), meta)
    mc.write_json_atomic(os.path.join(mc.RESULTS_DIR, jid, "status.json"), {"status": "queued", "queued_at": mc.now_iso()})
    print(jid)
    return jid


def control(jid, action):
    os.makedirs(mc.CONTROL_DIR, exist_ok=True)
    mc.write_json_atomic(os.path.join(mc.CONTROL_DIR, f"{jid}.json"), {"action": action, "at": mc.now_iso()})


def list_jobs():
    rows = []
    for jid in os.listdir(mc.JOBS_DIR):
        meta = mc.read_json(os.path.join(mc.JOBS_DIR, jid, "meta.json"))
        if not meta or meta.get("version") != 2:
            continue
        st = mc.read_json(os.path.join(mc.RESULTS_DIR, jid, "status.json"), {}) or {}
        pr = mc.read_json(os.path.join(mc.RESULTS_DIR, jid, "progress.json"), {}) or {}
        rows.append((meta["submittedAt"], jid, meta["type"], meta.get("ownerName"), st.get("status"),
                     f"{pr.get('phase')}: {pr.get('done')}/{pr.get('total')} eta {mc.fmt_duration(pr.get('eta_sec'))}"))
    for r in sorted(rows):
        print(*r, sep="  ")


def users_load():
    return mc.read_json(mc.USERS_FILE, []) or []


# ---------------------------------------------------------------- log scrub
# Mirrors web/src/lib/server/log-scrub.ts: every *.log directly in
# ~/.distill/logs loses the lines that mention the deleted name (display form or
# the lowercase-hyphen slug used in Ollama tags; case-insensitive, on
# letter/digit boundaries, spaces/hyphens/underscores interchangeable), one of
# that person's job IDs (exact, alnum boundaries) or one of their Ollama model
# tags (exact). Files are rewritten in place (same inode) because launchd holds
# them open O_APPEND for the queue runner and the web app.
LOGS_DIR = os.path.join(mc.DISTILL_HOME, "logs")
MAX_LOG_BYTES = 256 * 1024 * 1024
RESCRUB_DELAYS = (10, 60, 300)


def name_slugs(name):
    full = re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", name.lower()))
    short = re.sub(r"^-+|-+$", "", full[:24])
    return list(dict.fromkeys(s for s in (full, short) if len(s) >= 2))


def build_scrub_matcher(name, job_ids=(), tags=()):
    res = []
    name_parts = []
    for w in dict.fromkeys(x.lower() for x in [name.strip(), *name_slugs(name)] if x):
        body = r"[\s_-]+".join(re.escape(p) for p in re.split(r"[\s_-]+", w) if p)
        if body:
            name_parts.append(rf"(?<![^\W_]){body}(?![^\W_])")
    if name_parts:
        res.append(re.compile("|".join(name_parts), re.IGNORECASE))
    exact = [rf"(?<![A-Za-z0-9]){re.escape(j)}(?![A-Za-z0-9])" for j in job_ids if j]
    exact += [re.escape(t) for t in tags if t]
    if exact:
        res.append(re.compile("|".join(exact)))
    if not res:
        return None
    return lambda line: any(r.search(line) for r in res)


def scrub_file_in_place(path, match):
    try:
        size = os.stat(path).st_size
    except OSError:
        return 0
    if not os.path.isfile(path) or size == 0 or size > MAX_LOG_BYTES:
        return 0

    def filt(data):
        lines = data.decode("utf-8", errors="surrogateescape").splitlines(keepends=True)
        kept = [ln for ln in lines if not match(ln)]
        return "".join(kept).encode("utf-8", errors="surrogateescape"), len(lines) - len(kept)

    with open(path, "r+b") as f:
        out, removed = filt(f.read(size))
        if removed == 0:
            return 0
        # pick up anything appended while filtering, then rewrite quickly
        f.seek(size)
        extra = f.read()
        if extra:
            out2, r2 = filt(extra)
            out += out2
            removed += r2
        f.seek(0)
        f.truncate(0)
        f.write(out)
        f.flush()
        os.fsync(f.fileno())
    return removed


def scrub_logs(name, job_ids=(), tags=()):
    match = build_scrub_matcher(name, job_ids, tags)
    result = {"files": 0, "linesRemoved": 0, "perFile": {}}
    if not match:
        return result
    try:
        files = sorted(os.path.join(LOGS_DIR, f) for f in os.listdir(LOGS_DIR) if f.endswith(".log"))
    except OSError:
        files = []
    for path in files:
        try:
            n = scrub_file_in_place(path, match)
        except OSError:
            continue  # never fail the delete over a log we cannot edit
        result["files"] += 1
        if n:
            result["linesRemoved"] += n
            result["perFile"][os.path.basename(path)] = n
    return result


def wait_for_purges(job_ids, timeout=30.0):
    """The queue runner consumes purge requests asynchronously; wait for them."""
    def pending():
        return any(os.path.exists(os.path.join(mc.CONTROL_DIR, f"{j}.json"))
                   or os.path.exists(os.path.join(mc.JOBS_DIR, j)) for j in job_ids)
    deadline = time.time() + timeout
    while job_ids and pending() and time.time() < deadline:
        time.sleep(0.5)
    if job_ids:
        time.sleep(0.75)
    return not pending()


def spawn_rescrub(name, job_ids, tags):
    """Detached background re-scrubs (10 s, 60 s, 5 min) to catch late runner lines.
    Terms go over stdin so the name never shows up in the process list."""
    try:
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "_rescrub"], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             close_fds=True)
        p.stdin.write(json.dumps({"name": name, "jobIds": list(job_ids), "tags": list(tags)}).encode())
        p.stdin.close()
    except OSError:
        pass


def rescrub_worker():
    terms = json.loads(sys.stdin.read() or "{}")
    if not terms.get("name"):
        return
    last = 0
    for d in RESCRUB_DELAYS:
        time.sleep(d - last)
        last = d
        scrub_logs(terms["name"], terms.get("jobIds", []), terms.get("tags", []))


def delete_user(name):
    users = users_load()
    match = next((u for u in users if u["name"].lower() == name.lower()), None)
    if match:
        name = match["name"]
    mc.write_json_atomic(mc.USERS_FILE, [u for u in users if u["name"] != name])
    job_ids = []
    for jid in os.listdir(mc.JOBS_DIR):
        meta = mc.read_json(os.path.join(mc.JOBS_DIR, jid, "meta.json"))
        if meta and meta.get("version") == 2 and meta.get("ownerName") == name:
            job_ids.append(jid)
    reg = mc.read_json(mc.OLLAMA_REGISTRY, []) or []
    tags = [r["tag"] for r in reg if r.get("tag") and (r.get("owner") == name or r.get("jobId") in job_ids)]
    keep = []
    for r in reg:
        if r.get("owner") == name:
            try:
                req = urllib.request.Request(f"{mc.OLLAMA_URL}/api/delete", data=json.dumps({"model": r["tag"]}).encode(),
                                             method="DELETE", headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=60).read()
                print("deleted ollama model", r["tag"])
            except Exception as e:  # noqa: BLE001
                print("could not delete", r["tag"], e)
        else:
            keep.append(r)
    mc.write_json_atomic(mc.OLLAMA_REGISTRY, keep)
    for jid in job_ids:
        control(jid, "purge")
    print(f"user {name!r} removed; {len(job_ids)} job(s) sent to purge")
    if not wait_for_purges(job_ids):
        print("note: queue runner has not finished purging yet; background re-scrub will catch its lines")
    res = scrub_logs(name, job_ids, tags)
    spawn_rescrub(name, job_ids, tags)
    print(f"log scrub: {res['linesRemoved']} line(s) removed {json.dumps(res['perFile'])} "
          f"(re-scrub at {', '.join(f'{d}s' for d in RESCRUB_DELAYS)})")


def main():
    if sys.argv[1:] == ["_rescrub"]:
        rescrub_worker()
        return
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("submit-generate")
    g.add_argument("--owner", required=True)
    g.add_argument("--prompt-file", required=True)
    g.add_argument("--count", type=int, default=3000)
    g.add_argument("--param", action="append", default=[])
    t = sub.add_parser("submit-train")
    t.add_argument("--owner", required=True)
    t.add_argument("--data", required=True)
    t.add_argument("--student", required=True, choices=list(mc.STUDENTS))
    t.add_argument("--param", action="append", default=[])
    for c in ("pause", "resume", "cancel", "purge"):
        sub.add_parser(c).add_argument("job_id")
    sub.add_parser("list")
    u = sub.add_parser("users")
    u.add_argument("action", nargs="?", choices=["add", "delete"])
    u.add_argument("name", nargs="?")
    a = ap.parse_args()

    def kv(items):
        out = {}
        for it in items:
            k, v = it.split("=", 1)
            out[k] = v
        return out

    if a.cmd == "submit-generate":
        submit("generate", a.owner, a.prompt_file, {"COUNT": a.count, **kv(a.param)})
    elif a.cmd == "submit-train":
        submit("train", a.owner, a.data, {"STUDENT": a.student, **kv(a.param)})
    elif a.cmd in ("pause", "resume", "cancel", "purge"):
        control(a.job_id, a.cmd)
        print("ok")
    elif a.cmd == "list":
        list_jobs()
    elif a.cmd == "users":
        if a.action == "add":
            us = users_load()
            if not any(x["name"] == a.name for x in us):
                us.append({"name": a.name, "createdAt": mc.now_iso()})
                mc.write_json_atomic(mc.USERS_FILE, us)
        elif a.action == "delete":
            delete_user(a.name)
        print(json.dumps(users_load(), indent=1))


if __name__ == "__main__":
    main()
