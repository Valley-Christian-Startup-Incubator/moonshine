#!/usr/bin/env python3
"""Run a private, objective holdout against base, distilled, and teacher models.

Evaluation JSONL rows contain a prompt and a scorer. See
tests/fixtures/eval_tiny.jsonl for examples. Generation is deterministic and
resumable. Exact prompt overlap with training data is rejected when
--training-data is supplied.
"""

import argparse
import gc
import hashlib
import json
import os
import random
import re
import statistics
import sys
from pathlib import Path
from typing import Any


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with open(path) as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: each row must be an object")
            rows.append(row)
    return rows


def normalized_text(value: Any, *, case_sensitive: bool = False) -> str:
    text = " ".join(str(value).strip().split())
    return text if case_sensitive else text.casefold()


def validate_eval_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("Evaluation file has no cases")
    seen_ids: set[str] = set()
    seen_prompts: set[str] = set()
    supported = {"exact", "contains", "regex", "numeric", "json"}
    for index, row in enumerate(rows, 1):
        case_id = str(row.get("id", index))
        prompt = row.get("prompt")
        scorer = row.get("scorer")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"Case {case_id}: prompt must be a non-empty string")
        if not isinstance(scorer, dict) or scorer.get("type") not in supported:
            raise ValueError(
                f"Case {case_id}: scorer.type must be one of {sorted(supported)}"
            )
        prompt_key = normalized_text(prompt)
        if case_id in seen_ids:
            raise ValueError(f"Duplicate case id: {case_id}")
        if prompt_key in seen_prompts:
            raise ValueError(f"Duplicate evaluation prompt in case {case_id}")
        seen_ids.add(case_id)
        seen_prompts.add(prompt_key)


def reject_training_overlap(
    eval_rows: list[dict[str, Any]], training_rows: list[dict[str, Any]]
) -> None:
    training_prompts = {
        normalized_text(row["prompt"])
        for row in training_rows
        if isinstance(row.get("prompt"), str)
    }
    overlaps = [
        str(row.get("id", index + 1))
        for index, row in enumerate(eval_rows)
        if normalized_text(row["prompt"]) in training_prompts
    ]
    if overlaps:
        preview = ", ".join(overlaps[:10])
        suffix = "..." if len(overlaps) > 10 else ""
        raise ValueError(
            f"Evaluation leakage: {len(overlaps)} prompt(s) also occur in training "
            f"data. Cases: {preview}{suffix}"
        )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _json_contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _json_contains(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            _json_contains(a, e) for a, e in zip(actual, expected)
        )
    return actual == expected


def score_completion(row: dict[str, Any], completion: str) -> dict[str, Any]:
    scorer = row["scorer"]
    scorer_type = scorer["type"]
    case_sensitive = bool(scorer.get("case_sensitive", False))
    actual = normalized_text(completion, case_sensitive=case_sensitive)
    reason = ""

    if scorer_type == "exact":
        expected_values = _as_list(scorer.get("expected"))
        passed = any(
            actual == normalized_text(value, case_sensitive=case_sensitive)
            for value in expected_values
        )
        reason = "exact match" if passed else f"expected one of {expected_values!r}"
    elif scorer_type == "contains":
        required = _as_list(scorer.get("required", scorer.get("expected")))
        missing = [
            value
            for value in required
            if normalized_text(value, case_sensitive=case_sensitive) not in actual
        ]
        passed = not missing
        reason = "required text present" if passed else f"missing {missing!r}"
    elif scorer_type == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = str(scorer.get("pattern", ""))
        match = re.fullmatch if scorer.get("fullmatch", False) else re.search
        passed = bool(match(pattern, completion.strip(), flags))
        reason = "regex matched" if passed else f"did not match /{pattern}/"
    elif scorer_type == "numeric":
        number_pattern = scorer.get("pattern", r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
        match = re.search(number_pattern, completion.replace(",", ""))
        expected = float(scorer["expected"])
        tolerance = float(scorer.get("tolerance", 0.0))
        if match:
            token = match.group(1) if match.lastindex else match.group(0)
            try:
                observed = float(token)
                passed = abs(observed - expected) <= tolerance
                reason = f"observed {observed:g}, expected {expected:g} +/- {tolerance:g}"
            except ValueError:
                passed = False
                reason = f"matched non-numeric value {token!r}"
        else:
            passed = False
            reason = "no numeric answer found"
    else:
        try:
            parsed = json.loads(completion.strip())
            expected = scorer["expected"]
            passed = parsed == expected if scorer.get("exact", True) else _json_contains(parsed, expected)
            reason = "JSON matched" if passed else f"JSON differed from {expected!r}"
        except json.JSONDecodeError as exc:
            passed = False
            reason = f"invalid JSON: {exc.msg}"

    required = _as_list(scorer.get("required")) if scorer_type != "contains" else []
    forbidden = _as_list(scorer.get("forbidden"))
    missing_global = [
        value
        for value in required
        if normalized_text(value, case_sensitive=case_sensitive) not in actual
    ]
    present_forbidden = [
        value
        for value in forbidden
        if normalized_text(value, case_sensitive=case_sensitive) in actual
    ]
    if missing_global or present_forbidden:
        passed = False
        extras = []
        if missing_global:
            extras.append(f"missing required {missing_global!r}")
        if present_forbidden:
            extras.append(f"contained forbidden {present_forbidden!r}")
        reason = f"{reason}; {'; '.join(extras)}"

    return {"passed": passed, "reason": reason}


def file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def adapter_fingerprint(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    adapter_dir = Path(path)
    result: dict[str, Any] = {"path": str(adapter_dir.resolve())}
    for name in ("adapter_config.json", "adapters.safetensors"):
        candidate = adapter_dir / name
        if candidate.exists():
            stat = candidate.stat()
            result[name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return result


def prepare_adapter_for_loading(
    adapter_path: str,
    output_dir: Path,
    *,
    rank: int,
    num_layers: int,
    scale: float,
) -> str:
    source = Path(adapter_path).resolve()
    weights = source / "adapters.safetensors"
    if not weights.exists():
        raise FileNotFoundError(f"Adapter weights not found: {weights}")
    if (source / "adapter_config.json").exists():
        return str(source)

    compatibility_dir = output_dir / "legacy-adapter"
    compatibility_dir.mkdir(parents=True, exist_ok=True)
    linked_weights = compatibility_dir / "adapters.safetensors"
    if linked_weights.is_symlink() and linked_weights.resolve() != weights:
        linked_weights.unlink()
    if not linked_weights.exists():
        os.symlink(weights, linked_weights)
    config = {
        "fine_tune_type": "lora",
        "num_layers": num_layers,
        "lora_parameters": {"rank": rank, "dropout": 0.0, "scale": scale},
    }
    (compatibility_dir / "adapter_config.json").write_text(
        json.dumps(config, indent=2) + "\n"
    )
    print(
        "warning: adapter_config.json was missing; using evaluation defaults "
        f"rank={rank}, layers={num_layers}, scale={scale:g}",
        file=sys.stderr,
    )
    return str(compatibility_dir)


def format_prompt(tokenizer: Any, prompt: str, system_prompt: str | None) -> str:
    if tokenizer.chat_template is None:
        return prompt
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=False
    )


def generate_predictions(
    *,
    label: str,
    model_path: str,
    adapter_path: str | None,
    rows: list[dict[str, Any]],
    eval_path: str,
    output_dir: Path,
    max_tokens: int,
    system_prompt: str | None,
) -> list[dict[str, Any]]:
    try:
        import mlx.core as mx
        from mlx_lm import generate, load
        from mlx_lm.sample_utils import make_sampler
    except ImportError as exc:
        raise RuntimeError(
            "Evaluation generation requires mlx-lm. Run this with the Moonshine "
            "virtualenv on an Apple Silicon Mac."
        ) from exc

    prediction_path = output_dir / f"{label}.jsonl"
    manifest_path = output_dir / f"{label}.manifest.json"
    manifest = {
        "eval_sha256": file_fingerprint(eval_path),
        "model": model_path,
        "adapter": adapter_fingerprint(adapter_path),
        "max_tokens": max_tokens,
        "system_prompt": system_prompt,
    }
    cached_manifest = None
    if manifest_path.exists():
        try:
            cached_manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            pass
    if cached_manifest != manifest:
        prediction_path.unlink(missing_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    existing: list[dict[str, Any]] = []
    if prediction_path.exists():
        try:
            existing = read_jsonl(prediction_path)
        except ValueError:
            existing = []
        valid_count = 0
        for prediction, row in zip(existing, rows):
            if prediction.get("prompt") != row["prompt"]:
                break
            valid_count += 1
        existing = existing[:valid_count]
        with open(prediction_path, "w") as f:
            for prediction in existing:
                f.write(json.dumps(prediction) + "\n")

    if len(existing) == len(rows):
        print(f"{label}: using {len(rows)} cached predictions", file=sys.stderr)
        return existing

    print(f"{label}: loading {model_path}", file=sys.stderr)
    model, tokenizer = load(model_path, adapter_path=adapter_path)
    sampler = make_sampler(temp=0.0)
    with open(prediction_path, "a") as f:
        for index, row in enumerate(rows[len(existing):], len(existing)):
            prompt = format_prompt(tokenizer, row["prompt"], system_prompt)
            completion = generate(
                model,
                tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                sampler=sampler,
                verbose=False,
            )
            prediction = {
                "id": str(row.get("id", index + 1)),
                "prompt": row["prompt"],
                "completion": completion,
            }
            f.write(json.dumps(prediction) + "\n")
            f.flush()
            existing.append(prediction)
            print(f"{label}: {index + 1}/{len(rows)}", file=sys.stderr)

    del model, tokenizer
    gc.collect()
    mx.clear_cache()
    return existing


def paired_bootstrap(
    base: list[bool], candidate: list[bool], iterations: int = 10_000
) -> tuple[float, float]:
    if not base:
        return 0.0, 0.0
    differences = [float(b) - float(a) for a, b in zip(base, candidate)]
    rng = random.Random(0)
    samples = []
    for _ in range(iterations):
        samples.append(statistics.fmean(rng.choice(differences) for _ in differences))
    samples.sort()
    return samples[int(iterations * 0.025)], samples[int(iterations * 0.975)]


def build_report(
    rows: list[dict[str, Any]], predictions: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for label, model_predictions in predictions.items():
        cases = []
        for row, prediction in zip(rows, model_predictions):
            score = score_completion(row, prediction["completion"])
            cases.append({**prediction, **score})
        passed = sum(case["passed"] for case in cases)
        models[label] = {
            "passed": passed,
            "total": len(cases),
            "accuracy": passed / len(cases),
            "cases": cases,
        }

    comparisons: dict[str, Any] = {}
    if "base" in models:
        base_scores = [case["passed"] for case in models["base"]["cases"]]
        for label, result in models.items():
            if label == "base":
                continue
            scores = [case["passed"] for case in result["cases"]]
            low, high = paired_bootstrap(base_scores, scores)
            comparisons[f"{label}_vs_base"] = {
                "delta": result["accuracy"] - models["base"]["accuracy"],
                "bootstrap_95_ci": [low, high],
                "wins": sum(not base and candidate for base, candidate in zip(base_scores, scores)),
                "regressions": sum(base and not candidate for base, candidate in zip(base_scores, scores)),
                "ties": sum(base == candidate for base, candidate in zip(base_scores, scores)),
            }
    return {"models": models, "comparisons": comparisons}


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Distillation evaluation", "", "| Model | Passed | Accuracy |", "|---|---:|---:|"]
    for label, result in report["models"].items():
        lines.append(
            f"| {label} | {result['passed']}/{result['total']} | {result['accuracy']:.1%} |"
        )
    if report["comparisons"]:
        lines.extend(["", "## Paired comparisons", ""])
        for name, comparison in report["comparisons"].items():
            low, high = comparison["bootstrap_95_ci"]
            lines.append(
                f"- {name}: {comparison['delta']:+.1%} "
                f"(95% bootstrap CI {low:+.1%} to {high:+.1%}), "
                f"{comparison['wins']} wins, {comparison['regressions']} regressions, "
                f"{comparison['ties']} ties"
            )
    lines.extend(["", "## Failures", ""])
    failures = 0
    for label, result in report["models"].items():
        for case in result["cases"]:
            if not case["passed"]:
                failures += 1
                completion = " ".join(case["completion"].split())
                lines.append(
                    f"- `{label}` / `{case['id']}`: {case['reason']}. Output: {completion!r}"
                )
    if not failures:
        lines.append("No failures.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-data", required=True, help="frozen evaluation JSONL")
    parser.add_argument("--training-data", help="training JSONL, used for leakage checks")
    parser.add_argument("--student-model", required=True)
    parser.add_argument("--adapter-path", required=True)
    parser.add_argument("--teacher-model")
    parser.add_argument("--output-dir", default="eval-results")
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--system-prompt")
    parser.add_argument("--adapter-rank", type=int, default=8)
    parser.add_argument("--adapter-layers", type=int, default=16)
    parser.add_argument("--adapter-scale", type=float, default=20.0)
    parser.add_argument(
        "--min-delta",
        type=float,
        help="exit nonzero unless distilled accuracy beats base by this amount",
    )
    args = parser.parse_args()

    rows = read_jsonl(args.eval_data)
    validate_eval_rows(rows)
    if args.training_data:
        reject_training_overlap(rows, read_jsonl(args.training_data))
    else:
        print(
            "warning: no --training-data supplied; prompt leakage was not checked",
            file=sys.stderr,
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter_path = prepare_adapter_for_loading(
        args.adapter_path,
        output_dir,
        rank=args.adapter_rank,
        num_layers=args.adapter_layers,
        scale=args.adapter_scale,
    )
    specs = {
        "base": (args.student_model, None),
        "distilled": (args.student_model, adapter_path),
    }
    if args.teacher_model:
        specs["teacher"] = (args.teacher_model, None)
    predictions = {
        label: generate_predictions(
            label=label,
            model_path=model,
            adapter_path=adapter,
            rows=rows,
            eval_path=args.eval_data,
            output_dir=output_dir,
            max_tokens=args.max_tokens,
            system_prompt=args.system_prompt,
        )
        for label, (model, adapter) in specs.items()
    }
    report = build_report(rows, predictions)
    report_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    markdown = render_markdown(report)
    markdown_path.write_text(markdown)
    print(markdown)

    if args.min_delta is not None:
        delta = report["comparisons"]["distilled_vs_base"]["delta"]
        if delta < args.min_delta:
            print(
                f"FAILED: distilled delta {delta:+.1%} is below required {args.min_delta:+.1%}",
                file=sys.stderr,
            )
            raise SystemExit(2)


if __name__ == "__main__":
    main()
