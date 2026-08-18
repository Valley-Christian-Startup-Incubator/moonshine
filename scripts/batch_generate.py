#!/usr/bin/env python3
"""Batch teacher generation: reads a JSONL of prompts, runs each through an
MLX model via mlx_lm's Python API, and writes a JSONL of completions.

This is the script teacher-gen.yaml invokes. It loads the model once and
generates sequentially (mlx-lm doesn't batch-decode across prompts of
differing length as of this writing), which is fine for the serial,
single-job-at-a-time model this scheduler assumes.

Input JSONL row shape:
    {"prompt": "..."}

Output JSONL row shape:
    {"prompt": "...", "completion": "..."}
"""

import argparse
import json
import sys

# mlx_lm's generate() signature has shifted across releases (e.g. `temp` vs
# a `sampler=make_sampler(temp=...)` argument in newer versions). Verify
# against the pinned mlx-lm version in setup.sh if generation errors on temp.
from mlx_lm import generate, load


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="HF repo id or local path")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.7)
    args = parser.parse_args()

    print(f"Loading model: {args.model}", file=sys.stderr)
    model, tokenizer = load(args.model)

    with open(args.input) as infile:
        rows = [json.loads(line) for line in infile if line.strip()]

    print(f"Generating {len(rows)} completions", file=sys.stderr)
    with open(args.output, "w") as outfile:
        for i, row in enumerate(rows):
            prompt = row["prompt"]
            if tokenizer.chat_template is not None:
                messages = [{"role": "user", "content": prompt}]
                formatted = tokenizer.apply_chat_template(
                    messages, add_generation_prompt=True, tokenize=False
                )
            else:
                formatted = prompt

            completion = generate(
                model,
                tokenizer,
                prompt=formatted,
                max_tokens=args.max_tokens,
                temp=args.temperature,
            )

            outfile.write(json.dumps({"prompt": prompt, "completion": completion}) + "\n")
            outfile.flush()
            print(f"[{i + 1}/{len(rows)}] done", file=sys.stderr)


if __name__ == "__main__":
    main()
