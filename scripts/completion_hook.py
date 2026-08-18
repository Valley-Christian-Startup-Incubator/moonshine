#!/usr/bin/env python3
"""Writes/updates status.json for a job's results directory.

Called by every Dagu DAG (as a step and as success/failure handlers) to
record job lifecycle state that the SvelteKit app reads directly from disk.
"""

import argparse
import json
import os
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--status", required=True, choices=["running", "complete", "failed"])
    parser.add_argument("--output-path", default=None)
    parser.add_argument("--error", default=None)
    args = parser.parse_args()

    distill_home = os.environ.get("DISTILL_HOME", os.path.expanduser("~/.distill"))
    results_dir = os.path.join(distill_home, "results", args.job_id)
    os.makedirs(results_dir, exist_ok=True)
    status_path = os.path.join(results_dir, "status.json")

    existing = {}
    if os.path.exists(status_path):
        try:
            with open(status_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError):
            existing = {}

    payload = dict(existing)
    payload["status"] = args.status

    if args.status == "running":
        payload.setdefault("started_at", now_iso())
    else:
        payload.setdefault("started_at", existing.get("started_at", now_iso()))
        payload["completed_at"] = now_iso()
        if args.output_path:
            payload["output_path"] = args.output_path
        if args.status == "failed" and args.error:
            payload["error"] = args.error

    with open(status_path, "w") as f:
        json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
