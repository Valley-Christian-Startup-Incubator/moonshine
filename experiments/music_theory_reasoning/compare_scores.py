#!/usr/bin/env python3
"""Paired, case-clustered comparisons for music-theory reasoning scores."""

from __future__ import annotations

import argparse
import itertools
import json
import random
from collections import defaultdict
from pathlib import Path


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def clustered_values(model: dict) -> dict[str, tuple[float, float]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for result in model["cases"]:
        groups[result["case_id"]].append(result)
    return {
        case_id: (
            sum(row["passed_facts"] for row in rows) / sum(row["total_facts"] for row in rows),
            sum(int(row["full"]) for row in rows) / len(rows),
        )
        for case_id, rows in groups.items()
    }


def paired_test(differences: list[float], seed: int, samples: int) -> dict:
    rng = random.Random(seed)
    observed = sum(differences) / len(differences)
    bootstrap = [
        sum(rng.choice(differences) for _ in differences) / len(differences)
        for _ in range(samples)
    ]
    nonzero = [value for value in differences if value]
    if len(nonzero) <= 20:
        permutations = itertools.product((-1, 1), repeat=len(nonzero))
        permuted = [sum(sign * value for sign, value in zip(signs, nonzero)) / len(differences) for signs in permutations]
    else:
        permuted = [
            sum(rng.choice((-1, 1)) * value for value in nonzero) / len(differences)
            for _ in range(samples)
        ]
    extreme = sum(abs(value) >= abs(observed) - 1e-12 for value in permuted)
    return {
        "mean_delta": observed,
        "bootstrap_95_ci": [percentile(bootstrap, 0.025), percentile(bootstrap, 0.975)],
        "paired_randomization_two_sided_p": (extreme + 1) / (len(permuted) + 1),
        "clusters": len(differences),
        "nonzero_clusters": len(nonzero),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260828)
    args = parser.parse_args()

    report = json.loads(args.scores.read_text())
    baseline = clustered_values(report["models"][args.baseline])
    candidate = clustered_values(report["models"][args.candidate])
    if baseline.keys() != candidate.keys():
        raise ValueError("Candidate and baseline case clusters do not match")

    case_ids = sorted(baseline)
    fact_deltas = [candidate[key][0] - baseline[key][0] for key in case_ids]
    full_deltas = [candidate[key][1] - baseline[key][1] for key in case_ids]
    comparison = {
        "baseline": args.baseline,
        "candidate": args.candidate,
        "unit": "40 held-out harmonic cases; two prompt paraphrases are clustered",
        "fact_coverage": paired_test(fact_deltas, args.seed, args.samples),
        "full_case_rate": paired_test(full_deltas, args.seed + 1, args.samples),
    }
    args.output.write_text(json.dumps(comparison, indent=2) + "\n")
    for metric in ("fact_coverage", "full_case_rate"):
        result = comparison[metric]
        low, high = result["bootstrap_95_ci"]
        print(metric, f"delta={result['mean_delta']:+.1%}", f"95% CI [{low:+.1%}, {high:+.1%}]", f"p={result['paired_randomization_two_sided_p']:.4g}")


if __name__ == "__main__":
    main()
