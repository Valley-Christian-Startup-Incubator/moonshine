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

Input JSONL row shape for existing questions:
    {"prompt": "..."}

Input JSONL row shape for a reviewed dataset plan:
    {"generation_prompt": "...", "count": 8}

Output JSONL row shape:
    {"prompt": "...", "completion": "..."}
"""

import argparse
import json
import os
import re
import sys


def extract_qa_pairs(text: str) -> list[dict[str, str]]:
    """Extract the first JSON Q&A array from a teacher response."""
    candidate = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.I)
    start = candidate.find("[")
    if start < 0:
        raise ValueError("Teacher response did not contain a JSON array")
    try:
        value, _ = json.JSONDecoder().raw_decode(candidate[start:])
    except json.JSONDecodeError as error:
        raise ValueError(f"Teacher returned invalid JSON: {error.msg}") from error
    if not isinstance(value, list):
        raise ValueError("Teacher response was not a JSON array")
    pairs: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Teacher pair {index + 1} was not an object")
        question = item.get("question", item.get("prompt"))
        answer = item.get("answer", item.get("completion"))
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Teacher pair {index + 1} had no question")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"Teacher pair {index + 1} had no answer")
        pairs.append({"prompt": question.strip(), "completion": answer.strip()})
    return pairs


def format_chat_prompt(tokenizer, prompt: str, disable_thinking: bool = False) -> str:
    if tokenizer.chat_template is None:
        return prompt
    return tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt}],
        add_generation_prompt=True,
        tokenize=False,
        **({"enable_thinking": False} if disable_thinking else {}),
    )


def generate_from_plan(
    model, tokenizer, sampler, row: dict, output: str, max_tokens: int, disable_thinking: bool = False
) -> None:
    generation_prompt = row.get("generation_prompt")
    count = row.get("count")
    if not isinstance(generation_prompt, str) or not generation_prompt.strip():
        raise ValueError("Generation plan is missing generation_prompt")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > 20:
        raise ValueError("Generation plan count must be an integer from 1 to 20")

    request = f"""{generation_prompt.strip()}

Generate exactly {count} question-and-answer pairs now. Return only a JSON array. Each array item must be an object with a nonempty \"question\" string and a nonempty \"answer\" string. Do not wrap the JSON in Markdown."""
    print(f"Generating {count} question-and-answer pairs from the reviewed prompt", file=sys.stderr)
    from mlx_lm import generate
    response = generate(
        model,
        tokenizer,
        prompt=format_chat_prompt(tokenizer, request, disable_thinking),
        max_tokens=max_tokens * count,
        sampler=sampler,
    )
    pairs = extract_qa_pairs(response)
    if len(pairs) != count:
        raise ValueError(f"Teacher returned {len(pairs)} pairs; expected exactly {count}")

    temporary = f"{output}.tmp"
    with open(temporary, "w") as outfile:
        for index, pair in enumerate(pairs, 1):
            outfile.write(json.dumps(pair) + "\n")
            print(f"[{index}/{count}] Q&A pair ready", file=sys.stderr)
    os.replace(temporary, output)


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

    if len(rows) == 1 and "generation_prompt" in rows[0]:
        from mlx_lm import load
        from mlx_lm.sample_utils import make_sampler
        print(f"Loading model: {args.model}", file=sys.stderr)
        model, tokenizer = load(args.model)
        sampler = make_sampler(temp=args.temperature)
        generate_from_plan(
            model, tokenizer, sampler, rows[0], args.output, args.max_tokens, args.disable_thinking
        )
        return

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

    # mlx-lm 0.31.3 accepts a sampler rather than a direct temperature argument.
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler
    print(f"Loading model: {args.model}", file=sys.stderr)
    model, tokenizer = load(args.model)
    sampler = make_sampler(temp=args.temperature)

    print(f"Generating {len(remaining)} completions", file=sys.stderr)
    with open(args.output, "a") as outfile:
        for i, row in enumerate(remaining):
            prompt = row["prompt"]
            if tokenizer.chat_template is not None:
                template_kwargs = {"enable_thinking": False} if args.disable_thinking else {}
                formatted = tokenizer.apply_chat_template(
                    [{"role": "user", "content": prompt}],
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
