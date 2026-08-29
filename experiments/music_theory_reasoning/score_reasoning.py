#!/usr/bin/env python3
"""Score long-form harmonic explanations against deterministic fact checklists."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def normalize(text: str) -> str:
    return (
        text.replace("♭", "b")
        .replace("♯", "#")
        .replace("ø", " half-diminished ")
        .replace("°", " diminished ")
    )


PITCH_TOKEN = re.compile(r"(?<![A-Za-z])([A-G](?:bb|##|b|#)?)(?![A-Za-z#b])")


def make_strict_pattern(pattern: str) -> tuple[str, int]:
    """Prevent pitch-name checks from matching letters inside ordinary words."""
    contains_pitch = bool(PITCH_TOKEN.search(pattern))
    strict = PITCH_TOKEN.sub(
        lambda match: rf"(?<![A-Za-z#b]){re.escape(match.group(1))}(?![A-Za-z#b])",
        pattern,
    )
    return strict, 0 if contains_pitch else re.IGNORECASE


def fact_passes(fact: dict, completion: str) -> bool:
    text = normalize(completion)
    for pattern in fact["patterns"]:
        strict, flags = make_strict_pattern(pattern)
        if "analysis" in fact["label"]:
            flags = 0
        if re.search(strict, text, flags):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-data", required=True, type=Path)
    parser.add_argument(
        "--prediction",
        action="append",
        nargs=2,
        metavar=("LABEL", "PATH"),
        required=True,
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    cases = read_jsonl(args.eval_data)
    report = {
        "scoring": "deterministic required-fact coverage for harmonic explanations",
        "total_cases": len(cases),
        "facts_per_case": sorted({len(row["facts"]) for row in cases}),
        "models": {},
    }

    for label, path in args.prediction:
        predictions = read_jsonl(Path(path))
        if len(predictions) != len(cases):
            raise ValueError(f"{label}: expected {len(cases)} predictions, got {len(predictions)}")

        total_facts = 0
        passed_facts = 0
        full_cases = 0
        word_counts = []
        by_family = defaultdict(lambda: {"passed_facts": 0, "total_facts": 0, "full_cases": 0, "cases": 0})
        results = []
        for case, prediction in zip(cases, predictions):
            if prediction.get("prompt") != case["prompt"]:
                raise ValueError(f"{label}: predictions are not prompt-aligned at {case['id']}")
            checks = [
                {
                    "label": fact["label"],
                    "passed": fact_passes(fact, prediction.get("completion", "")),
                }
                for fact in case["facts"]
            ]
            passed = sum(check["passed"] for check in checks)
            total = len(checks)
            full = passed == total
            family = by_family[case["family"]]
            family["passed_facts"] += passed
            family["total_facts"] += total
            family["full_cases"] += int(full)
            family["cases"] += 1
            passed_facts += passed
            total_facts += total
            full_cases += int(full)
            word_counts.append(len(prediction.get("completion", "").split()))
            results.append({
                "id": case["id"],
                "case_id": case["case_id"],
                "family": case["family"],
                "passed_facts": passed,
                "total_facts": total,
                "full": full,
                "checks": checks,
            })

        family_report = {}
        for family_name, values in sorted(by_family.items()):
            family_report[family_name] = {
                **values,
                "fact_coverage": values["passed_facts"] / values["total_facts"],
                "full_case_rate": values["full_cases"] / values["cases"],
            }
        report["models"][label] = {
            "passed_facts": passed_facts,
            "total_facts": total_facts,
            "fact_coverage": passed_facts / total_facts,
            "full_cases": full_cases,
            "total_cases": len(cases),
            "full_case_rate": full_cases / len(cases),
            "average_words": sum(word_counts) / len(word_counts),
            "by_family": family_report,
            "cases": results,
        }

    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for label, result in report["models"].items():
        print(
            label,
            f"facts={result['passed_facts']}/{result['total_facts']}",
            f"coverage={result['fact_coverage']:.1%}",
            f"full={result['full_cases']}/{result['total_cases']}",
            f"words={result['average_words']:.1f}",
        )


if __name__ == "__main__":
    main()
