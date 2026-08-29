#!/usr/bin/env python3
"""Batch teacher generation: reads a JSONL of prompts, runs each through an
MLX model via mlx_lm's Python API, and writes a JSONL of completions.

This is the script teacher-gen.yaml invokes. It loads the model once and
generates sequentially (mlx-lm doesn't batch-decode across prompts of
differing length as of this writing), which is fine for the serial,
single-job-at-a-time model this scheduler assumes.

Resumable by design: teacher-gen.yaml gives this step a Dagu retryPolicy,
and a retry re-runs this exact command from scratch. Since a run can take
hours, we skip prompts already present in --output (by row index) and
append rather than truncate, so a retry after e.g. a transient OOM or
network blip continues instead of redoing already-generated completions.

Input JSONL row shape:
    {"prompt": "..."}

Output JSONL row shape:
    {"prompt": "...", "completion": "..."}
"""

import argparse
import json
import os
import sys

# mlx-lm 0.31.3 accepts a sampler rather than a direct temperature argument.
from mlx_lm import generate, load
from mlx_lm.sample_utils import make_sampler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="HF repo id or local path")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help="Explicitly disable reasoning channels in chat templates that support it",
    )
    args = parser.parse_args()

    with open(args.input) as infile:
        rows = [json.loads(line) for line in infile if line.strip()]

    already_done = 0
    if os.path.exists(args.output):
        with open(args.output) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    # Truncated write from a crash mid-line; stop counting
                    # here so this row gets regenerated.
                    break
                already_done += 1
    if already_done:
        # Drop any trailing truncated/partial line before appending.
        with open(args.output) as f:
            good_lines = f.readlines()[:already_done]
        with open(args.output, "w") as f:
            f.writelines(good_lines)
    remaining = rows[already_done:]

    if already_done:
        print(
            f"Resuming: {already_done}/{len(rows)} completions already on disk, "
            f"{len(remaining)} remaining",
            file=sys.stderr,
        )

    if not remaining:
        print("Nothing to do, output already complete", file=sys.stderr)
        return

    print(f"Loading model: {args.model}", file=sys.stderr)
    model, tokenizer = load(args.model)
    sampler = make_sampler(temp=args.temperature)

    print(f"Generating {len(remaining)} completions", file=sys.stderr)
    with open(args.output, "a") as outfile:
        for i, row in enumerate(remaining):
            prompt = row["prompt"]
            if tokenizer.chat_template is not None:
                messages = [{"role": "user", "content": prompt}]
                template_kwargs = (
                    {"enable_thinking": False} if args.disable_thinking else {}
                )
                formatted = tokenizer.apply_chat_template(
                    messages,
                    add_generation_prompt=True,
                    tokenize=False,
                    **template_kwargs,
                )
            else:
                formatted = prompt

            completion = generate(
                model,
                tokenizer,
                prompt=formatted,
                max_tokens=args.max_tokens,
                sampler=sampler,
            )

            outfile.write(json.dumps({"prompt": prompt, "completion": completion}) + "\n")
            outfile.flush()
            print(f"[{already_done + i + 1}/{len(rows)}] done", file=sys.stderr)


if __name__ == "__main__":
    main()
