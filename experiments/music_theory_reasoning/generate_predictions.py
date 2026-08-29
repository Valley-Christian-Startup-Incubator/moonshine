#!/usr/bin/env python3
"""Generate resumable predictions for the qualitative music-theory holdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mlx_lm import generate, load
from mlx_lm.sample_utils import make_sampler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--adapter")
    parser.add_argument("--eval-data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--disable-thinking", action="store_true")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.eval_data.read_text().splitlines() if line.strip()]
    existing = []
    if args.output.exists():
        for line in args.output.read_text().splitlines():
            if not line.strip():
                continue
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                break
        valid = 0
        for prediction, row in zip(existing, rows):
            if prediction.get("prompt") != row["prompt"]:
                break
            valid += 1
        existing = existing[:valid]
        args.output.write_text("".join(json.dumps(row) + "\n" for row in existing))

    if len(existing) == len(rows):
        print(f"Using {len(rows)} cached predictions")
        return

    print(f"Loading {args.model} adapter={args.adapter or 'none'}")
    model, tokenizer = load(args.model, adapter_path=args.adapter)
    sampler = make_sampler(temp=0.0)
    with args.output.open("a") as outfile:
        for index, row in enumerate(rows[len(existing):], len(existing) + 1):
            messages = [{"role": "user", "content": row["prompt"]}]
            template_kwargs = {"enable_thinking": False} if args.disable_thinking else {}
            prompt = tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=False,
                **template_kwargs,
            )
            completion = generate(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=args.max_tokens,
                sampler=sampler,
            )
            outfile.write(json.dumps({"prompt": row["prompt"], "completion": completion}) + "\n")
            outfile.flush()
            print(f"{index}/{len(rows)}")


if __name__ == "__main__":
    main()
