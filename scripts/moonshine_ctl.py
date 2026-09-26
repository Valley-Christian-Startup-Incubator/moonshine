#!/usr/bin/env python3
"""Command-line control for Moonshine v2 jobs (same file protocol as the web app).

  moonshine_ctl.py submit-generate --owner NAME --prompt-file P [--count 3000]
  moonshine_ctl.py submit-train --owner NAME --data FILE --student qwen3-0.6b [--param KEY=VAL ...]
  moonshine_ctl.py pause|resume|cancel JOB_ID
  moonshine_ctl.py list
  moonshine_ctl.py users [add NAME | delete NAME]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import string
import sys
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


def delete_user(name):
    users = [u for u in users_load() if u["name"] != name]
    mc.write_json_atomic(mc.USERS_FILE, users)
    reg = mc.read_json(mc.OLLAMA_REGISTRY, []) or []
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
    n = 0
    for jid in os.listdir(mc.JOBS_DIR):
        meta = mc.read_json(os.path.join(mc.JOBS_DIR, jid, "meta.json"))
        if meta and meta.get("version") == 2 and meta.get("ownerName") == name:
            control(jid, "purge")
            n += 1
    print(f"user {name!r} removed; {n} job(s) sent to purge")


def main():
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
