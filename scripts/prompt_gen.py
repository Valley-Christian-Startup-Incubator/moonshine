#!/usr/bin/env python3
"""Skeleton prompt-generation script.

Prompt generation is project-specific — teams should replace the body of
`generate_prompts()` with their own logic (templated expansion, sampling
from a seed corpus, calling a local model to bootstrap variations, etc).
This skeleton just reads a JSONL config and echoes one prompt per config
row so the pipeline is runnable end-to-end out of the box.

Input JSONL row shape (example):
    {"topic": "robotics", "n": 5}

Output JSONL row shape:
    {"prompt": "..."}
"""

import argparse
import json


def generate_prompts(config_row: dict) -> list[str]:
    topic = config_row.get("topic", "general")
    count = int(config_row.get("n", 1))
    return [f"Write a question about {topic} (variant {i + 1})." for i in range(count)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.input) as infile, open(args.output, "w") as outfile:
        for line in infile:
            line = line.strip()
            if not line:
                continue
            config_row = json.loads(line)
            for prompt in generate_prompts(config_row):
                outfile.write(json.dumps({"prompt": prompt}) + "\n")


if __name__ == "__main__":
    main()
