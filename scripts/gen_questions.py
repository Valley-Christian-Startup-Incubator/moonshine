#!/usr/bin/env python3
"""Generate Questions job: expand one teacher PROMPT into N {"prompt","completion"} pairs.

Calls the teacher (largest Ollama model) in batches, validates every JSON line,
dedupes prompts (case/whitespace-insensitive), appends to results/<id>/output.jsonl
and is fully resumable: on restart it re-reads output.jsonl and continues.

Usage:
  gen_questions.py --job-id ID                  (reads jobs/ID/meta.json)
  gen_questions.py --prompt-file P --output O --count N   (standalone / tests)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moonshine_common as mc  # noqa: E402

SYSTEM = (
    "You are the teacher model in a training-data pipeline. You write training items exactly as the "
    "user's PROMPT specifies. Output ONLY JSON Lines: one JSON object per line with exactly two string "
    "keys, \"prompt\" and \"completion\". No markdown fences, no numbering, no commentary before or after."
)

BATCH_INSTRUCTIONS = """=== BATCH INSTRUCTIONS (these override any total count or line ranges stated in the PROMPT above) ===
- This request is batch {batch_no} of about {batch_total} for a data set of {count} items in total. Items already written so far: {done}.
- Write EXACTLY {n} new items in this batch.
- Apply the PROMPT's percentages (item-type mix, topic mix, difficulty mix) within this batch. For this batch aim for roughly: {mix_hint}
- Make every item clearly different from earlier batches: new situations, numbers, names, sub-topics and wording. Variety seed: {seed}.
{avoid_block}- Output format: {n} lines, each a single JSON object {{"prompt": "...", "completion": "..."}} with only those two keys. Escape newlines inside strings as \\n. Nothing else."""


def parse_mix(prompt_text: str) -> list[tuple[str, float]]:
    """Best-effort: find 'NN% something' lines in the prompt (item-type mix)."""
    out = []
    for m in re.finditer(r"(\d{1,3})\s*%\s*([A-Za-z][^\n;,.()]{2,60})", prompt_text):
        pct = int(m.group(1))
        if 0 < pct <= 100:
            out.append((m.group(2).strip(), pct))
    return out


def batch_mix_hint(prompt_text: str, n: int, batch_no: int) -> str:
    mix = parse_mix(prompt_text)
    # Keep only the first group that sums to ~100 (usually the item-type mix)
    group, total = [], 0
    for label, pct in mix:
        group.append((label, pct))
        total += pct
        if total >= 99:
            break
    if not group or not (95 <= total <= 105):
        return "follow the PROMPT's percentages as closely as a batch of this size allows."
    # Largest-remainder allocation, rotated by batch_no so rounding evens out across batches
    raw = [(label, pct * n / 100) for label, pct in group]
    base = [int(x) for _, x in raw]
    rem = n - sum(base)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i][1] - base[i]), (i + batch_no) % len(raw)))
    for i in order[:rem]:
        base[i] += 1
    return "; ".join(f"{base[i]} x {raw[i][0]}" for i in range(len(raw)) if base[i] > 0) + "."


def extract_items(text: str) -> tuple[list[dict], int]:
    """Return (valid items, count of rejected candidate lines)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```[a-zA-Z]*", "", text)
    items, bad = [], 0
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            arr = json.loads(stripped)
            for obj in arr:
                (items.append({"prompt": obj["prompt"].strip(), "completion": obj["completion"].strip()})
                 if mc.valid_pair(obj) else None)
            return items, max(0, len(arr) - len(items))
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if mc.valid_pair(obj):
            items.append({"prompt": obj["prompt"].strip(), "completion": obj["completion"].strip()})
        else:
            bad += 1
    return items, bad


def load_existing(out_path: str) -> tuple[list[dict], set[str]]:
    """Re-read output.jsonl, keep only valid unique lines, rewrite if anything was dropped."""
    items, seen, dropped = [], set(), 0
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    dropped += 1
                    continue
                if not mc.valid_pair(obj) or mc.norm_prompt(obj["prompt"]) in seen:
                    dropped += 1
                    continue
                seen.add(mc.norm_prompt(obj["prompt"]))
                items.append(obj)
        if dropped:
            tmp = out_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for obj in items:
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            os.replace(tmp, out_path)
            mc.log(f"Resume: dropped {dropped} invalid/duplicate line(s) from existing output")
    return items, seen


def run(prompt_file: str, out_path: str, count: int, batch_size: int, job_id: str | None,
        teacher: str | None = None, max_batches: int | None = None) -> dict:
    prompt_text = open(prompt_file, encoding="utf-8", errors="replace").read().strip()
    if len(prompt_text) < 20:
        raise SystemExit("The uploaded prompt file is empty or too short.")
    teacher = teacher or mc.pick_teacher()
    items, seen = load_existing(out_path)
    done = len(items)
    mc.log(f"Generate Questions: teacher={teacher} target={count} batch_size={batch_size} already_done={done}")
    prog = mc.Progress(job_id, ["generate"]) if job_id else None
    if prog:
        prog.start_phase("generate", count, done, "items", "Generating questions")
        prog.set_extra(teacher=teacher)
    batch_total = max(1, -(-count // batch_size))
    batch_no = done // batch_size
    consecutive_bad = 0
    stats = {"calls": 0, "gen_tokens": 0, "gen_sec": 0.0, "wall_sec": 0.0, "accepted": 0, "rejected": 0, "dupes": 0}
    rng = random.Random()
    batches_run = 0
    while done < count:
        mc.check_stop()
        if max_batches is not None and batches_run >= max_batches:
            break
        n = min(batch_size, count - done)
        ask = n  # shortfalls from rejects/dupes are filled by the next batch
        batch_no += 1
        recent = [it["prompt"] for it in items[-8:]]
        sample = rng.sample(items[:-8], min(4, max(0, len(items) - 8))) if len(items) > 8 else []
        avoid = recent + [it["prompt"] for it in sample]
        avoid_block = ""
        if avoid:
            avoid_block = "- Do NOT repeat or closely paraphrase any of these existing prompts:\n" + "\n".join(
                "  * " + re.sub(r"\s+", " ", p)[:160] for p in avoid) + "\n"
        instr = BATCH_INSTRUCTIONS.format(batch_no=batch_no, batch_total=batch_total, count=count, done=done, n=ask,
                                          mix_hint=batch_mix_hint(prompt_text, ask, batch_no),
                                          seed=f"{batch_no}-{rng.randint(1000, 9999)}", avoid_block=avoid_block)
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": "=== PROMPT ===\n" + prompt_text + "\n\n" + instr}]
        try:
            r = mc.teacher_chat(teacher, messages, temperature=0.8, num_predict=min(32000, 700 * ask + 500),
                                num_ctx=32768, timeout=3600)
        except mc.StopRequested:
            raise
        except Exception as e:  # noqa: BLE001
            consecutive_bad += 1
            mc.log(f"Batch {batch_no}: teacher call failed ({e}); retry {consecutive_bad}/5")
            if consecutive_bad >= 5:
                raise SystemExit(f"Teacher failed 5 times in a row: {e}")
            batch_no -= 1
            time.sleep(min(60, 5 * consecutive_bad))
            continue
        batches_run += 1
        stats["calls"] += 1
        stats["gen_tokens"] += r.get("eval_count", 0)
        stats["gen_sec"] += r.get("eval_duration", 0) / 1e9
        stats["wall_sec"] += r["_wall_sec"]
        content = (r.get("message") or {}).get("content", "")
        got, bad = extract_items(content)
        stats["rejected"] += bad
        added = 0
        with mc.critical():
            with open(out_path, "a", encoding="utf-8") as f:
                for it in got:
                    if done >= count:
                        break
                    key = mc.norm_prompt(it["prompt"])
                    if key in seen:
                        stats["dupes"] += 1
                        continue
                    seen.add(key)
                    items.append(it)
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
                    done += 1
                    added += 1
                f.flush()
                os.fsync(f.fileno())
        stats["accepted"] += added
        tps = r.get("eval_count", 0) / max(1e-6, r.get("eval_duration", 1) / 1e9)
        mc.log(f"Batch {batch_no}: +{added} valid (rejected {bad}, asked {ask}) in {r['_wall_sec']:.0f}s "
               f"@ {tps:.1f} tok/s -> {done}/{count}")
        if added < max(1, n // 2):
            consecutive_bad += 1
            mc.log(f"Batch {batch_no} was weak ({added}/{n}); will retry ({consecutive_bad}/6)")
            if consecutive_bad >= 6:
                raise SystemExit("Six weak batches in a row: the teacher is not following the output format. "
                                 "Check that the PROMPT asks for {\"prompt\",\"completion\"} JSONL lines.")
        else:
            consecutive_bad = 0
        if prog:
            prog.update(done, force=True, **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in stats.items()})
    if prog:
        prog.update(done, force=True)
        prog.finish_phase()
    mc.log(f"Done: {done}/{count} items in {out_path}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id")
    ap.add_argument("--prompt-file")
    ap.add_argument("--output")
    ap.add_argument("--count", type=int)
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--teacher")
    ap.add_argument("--max-batches", type=int)
    args = ap.parse_args()
    if args.job_id:
        meta = mc.job_meta(args.job_id)
        p = meta.get("params", {})
        prompt_file = meta["inputFile"]
        out = os.path.join(mc.results_dir(args.job_id), "output.jsonl")
        count = int(p.get("COUNT", 3000))
        bs = int(args.batch_size or p.get("BATCH_SIZE", 0) or mc.setting("GEN_BATCH_SIZE", "20"))
        mc.run_main(args.job_id, lambda: run(prompt_file, out, count, bs, args.job_id, args.teacher, args.max_batches))
    else:
        stats = run(args.prompt_file, args.output, args.count, args.batch_size or 20, None, args.teacher, args.max_batches)
        print(json.dumps(stats))


if __name__ == "__main__":
    main()
