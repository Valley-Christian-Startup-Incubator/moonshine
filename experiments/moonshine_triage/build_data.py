#!/usr/bin/env python3
"""Build a deterministic train/holdout benchmark for Moonshine job triage."""

import json
from pathlib import Path


CODES = {
    "MNS-101": "network or DNS failure",
    "MNS-202": "out of memory or memory allocation failure",
    "MNS-303": "disk full or no space left",
    "MNS-404": "malformed or invalid input data",
    "MNS-505": "authentication or permission denied",
    "MNS-606": "teacher and student tokenizer or vocabulary mismatch",
    "MNS-707": "job exceeded its time limit",
}

TRAIN_CASES = {
    "MNS-101": [
        "huggingface.co lookup failed: nodename nor servname provided",
        "connection reset by peer while fetching model weights",
        "temporary failure in name resolution during teacher download",
        "HTTP client exhausted retries after a DNS lookup error",
        "socket could not connect to the remote model registry",
        "network is unreachable while requesting tokenizer.json",
        "TLS connection closed unexpectedly during artifact download",
    ],
    "MNS-202": [
        "Metal GPU allocation failed because the process ran out of memory",
        "malloc returned null while creating the attention tensor",
        "process was killed after memory pressure reached the system limit",
        "MLX could not allocate 8589934592 bytes for logits",
        "peak memory exceeded the recommended working set and allocation failed",
        "out of memory during the teacher forward pass",
        "batch creation failed with insufficient memory available",
    ],
    "MNS-303": [
        "write failed: no space left on device",
        "checkpoint save stopped because the results volume is full",
        "disk quota exceeded while writing adapters.safetensors",
        "cannot append output.jsonl because available storage is zero",
        "artifact write returned ENOSPC",
        "the model download stopped after the filesystem filled up",
        "checkpoint serialization failed due to insufficient disk space",
    ],
    "MNS-404": [
        "JSONDecodeError on line 8 of the uploaded training file",
        "input row is missing the required prompt field",
        "expected JSONL but received a CSV document",
        "completion must be a string but the row contains an array",
        "uploaded dataset contains an unterminated JSON object",
        "no usable training examples were found in the input file",
        "schema validation rejected a row with an empty prompt",
    ],
    "MNS-505": [
        "model registry returned HTTP 401 Unauthorized",
        "access to the gated repository was denied",
        "the supplied API token has expired",
        "HTTP 403 while downloading private model weights",
        "credentials are missing for the protected artifact store",
        "permission denied when requesting the team model",
        "authentication failed because the bearer token is invalid",
    ],
    "MNS-606": [
        "teacher vocab size 151936 does not match student vocab size 128256",
        "logit distillation rejected models with different tokenizers",
        "student token ids do not align with the teacher vocabulary",
        "the selected model families use incompatible vocabularies",
        "shared-tokenizer validation failed before the first training step",
        "teacher and student tokenizer hashes are different",
        "KL loss cannot compare logits because vocabulary dimensions differ",
    ],
    "MNS-707": [
        "Dagu stopped the run after timeoutSec elapsed",
        "the training job exceeded its twelve hour limit",
        "generation was terminated when the workflow deadline expired",
        "job remained active past its configured maximum duration",
        "scheduler killed the step after 43200 seconds",
        "the process received a timeout signal before saving its checkpoint",
        "workflow deadline exceeded during iteration 900",
    ],
}

EVAL_CASES = {
    "MNS-101": [
        "curl: could not resolve host hf.co",
        "remote inference request failed with ECONNRESET",
        "all download attempts ended in a name-resolution error",
        "model fetch aborted because the machine had no route to the host",
    ],
    "MNS-202": [
        "failed to allocate a 12 GiB Metal buffer",
        "kernel terminated the worker under critical RAM pressure",
        "attention computation raised an OOM exception",
        "there was not enough unified memory for the requested batch",
    ],
    "MNS-303": [
        "safetensors writer returned errno 28",
        "only zero bytes remain on the checkpoint filesystem",
        "results partition reached 100 percent capacity",
        "output append failed after storage quota exhaustion",
    ],
    "MNS-404": [
        "line 14 is not valid JSON and cannot be parsed",
        "a training record has completion set to null",
        "the uploaded file has prompts but uses answer instead of completion",
        "dataset loader rejected a blank document",
    ],
    "MNS-505": [
        "private repository request returned 403 Forbidden",
        "download cannot continue until valid credentials are supplied",
        "the registry rejected an expired access token",
        "user does not have permission to read this gated model",
    ],
    "MNS-606": [
        "teacher emits 152064 logits but student emits 151936",
        "token index 32001 means different text in the two selected models",
        "distillation stopped at the shared vocabulary compatibility check",
        "a Llama teacher was paired with a Qwen student for KL training",
    ],
    "MNS-707": [
        "run was cancelled exactly when its maximum runtime expired",
        "step did not finish before the scheduler deadline",
        "wall clock limit was reached during checkpoint generation",
        "execution exceeded timeoutSec and Dagu terminated it",
    ],
}


def prompt_for(log: str) -> str:
    codebook = "; ".join(f"{code} = {meaning}" for code, meaning in CODES.items())
    return (
        "Moonshine incident codebook: "
        + codebook
        + ". Classify the log below. Reply with exactly one incident code and no other text. "
        + "Log: "
        + log
        + " /no_think"
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main() -> None:
    root = Path(__file__).resolve().parent
    train_rows = [
        {"id": f"train-{code}-{index}", "prompt": prompt_for(log), "expected": code}
        for code, logs in TRAIN_CASES.items()
        for index, log in enumerate(logs, 1)
    ]
    eval_rows = [
        {
            "id": f"eval-{code}-{index}",
            "prompt": prompt_for(log),
            "scorer": {
                "type": "exact",
                "expected": code,
                "case_sensitive": True,
            },
        }
        for code, logs in EVAL_CASES.items()
        for index, log in enumerate(logs, 1)
    ]
    write_jsonl(root / "teacher_prompts.jsonl", train_rows)
    write_jsonl(root / "eval.jsonl", eval_rows)
    print(f"wrote {len(train_rows)} training prompts and {len(eval_rows)} holdout cases")


if __name__ == "__main__":
    main()
