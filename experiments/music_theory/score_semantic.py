#!/usr/bin/env python3
"""Exploratory semantic scoring for the frozen music-theory benchmark.

The primary benchmark remains strict exact match. This secondary scorer only
normalizes notation and short-answer formatting; it does not use an LLM judge.
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


INTERVAL_PHRASES = {
    "minor second": "m2",
    "major second": "M2",
    "minor third": "m3",
    "major third": "M3",
    "perfect fourth": "P4",
    "augmented fourth": "A4",
    "diminished fifth": "d5",
    "perfect fifth": "P5",
    "minor sixth": "m6",
    "major sixth": "M6",
    "minor seventh": "m7",
    "major seventh": "M7",
    "perfect octave": "P8",
}
INTERVAL_SYMBOLS = set(INTERVAL_PHRASES.values())
QUALITIES = {"major", "minor", "diminished", "augmented"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def note_tokens(text: str) -> list[str]:
    text = text.replace("♭", "b").replace("♯", "#")
    text = re.sub(
        r"\b([A-Ga-g])\s*[- ]?\s*flat\b",
        lambda match: match.group(1).upper() + "b",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b([A-Ga-g])\s*[- ]?\s*sharp\b",
        lambda match: match.group(1).upper() + "#",
        text,
        flags=re.IGNORECASE,
    )
    tokens = re.findall(r"(?<![A-Za-z])([A-Ga-g](?:#{1,2}|b{1,2})?)(?![A-Za-z])", text)
    return [token[0].upper() + token[1:] for token in tokens]


def score_interval(expected: str, completion: str) -> bool:
    found: set[str] = set()
    lowered = completion.casefold()
    for phrase, symbol in INTERVAL_PHRASES.items():
        if phrase in lowered:
            found.add(symbol)
    for match in re.finditer(
        r"(?<![A-Za-z0-9])(P8|M7|m7|M6|m6|P5|d5|A4|P4|M3|m3|M2|m2)(?![A-Za-z0-9])",
        completion,
    ):
        found.add(match.group(1))
    if re.search(r"(?<![A-Za-z0-9])dim(?:inished)?\s*5(?![A-Za-z0-9])", completion, re.I):
        found.add("d5")
    if re.search(r"(?<![A-Za-z0-9])aug(?:mented)?\s*4(?![A-Za-z0-9])", completion, re.I):
        found.add("A4")
    return found == {expected} and expected in INTERVAL_SYMBOLS


def score_quality(expected: str, completion: str) -> bool:
    found = {
        quality
        for quality in QUALITIES
        if re.search(rf"\b{quality}\b", completion, re.IGNORECASE)
    }
    return found == {expected}


def score_triad(expected: str, completion: str) -> bool:
    return note_tokens(completion) == expected.split("-")


def parse_signature(text: str):
    zero = re.search(r"\b0\s+accidentals?\b", text, re.IGNORECASE)
    if zero:
        return 0, None, []
    match = re.search(
        r"\b(\d+)\s+(sharp|sharps|flat|flats)\s*:\s*([^\n.]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    kind = "sharp" if match.group(2).casefold().startswith("sharp") else "flat"
    return int(match.group(1)), kind, note_tokens(match.group(3))


def score_case(row: dict, completion: str) -> bool:
    expected = row["scorer"]["expected"]
    family = row["family"]
    if family == "interval":
        return score_interval(expected, completion)
    if family == "chord_quality":
        return score_quality(expected, completion)
    if family == "triad_spelling":
        return score_triad(expected, completion)
    if family == "key_signature":
        return parse_signature(completion) == parse_signature(expected)
    raise ValueError(f"Unknown family: {family}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-data", required=True, type=Path)
    parser.add_argument("--prediction", action="append", nargs=2, metavar=("LABEL", "PATH"), required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    cases = read_jsonl(args.eval_data)
    report = {"scoring": "exploratory deterministic semantic normalization", "models": {}}
    for label, raw_path in args.prediction:
        predictions = read_jsonl(Path(raw_path))
        if len(predictions) != len(cases):
            raise ValueError(f"{label}: expected {len(cases)} predictions, got {len(predictions)}")
        by_family = defaultdict(lambda: {"passed": 0, "total": 0})
        results = []
        for row, prediction in zip(cases, predictions):
            if row["prompt"] != prediction["prompt"]:
                raise ValueError(f"{label}: prediction prompts are not aligned")
            passed = score_case(row, prediction["completion"])
            by_family[row["family"]]["passed"] += int(passed)
            by_family[row["family"]]["total"] += 1
            results.append({"id": row["id"], "passed": passed})
        passed = sum(result["passed"] for result in results)
        report["models"][label] = {
            "passed": passed,
            "total": len(cases),
            "accuracy": passed / len(cases),
            "by_family": dict(sorted(by_family.items())),
            "cases": results,
        }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for label, result in report["models"].items():
        print(label, f"{result['passed']}/{result['total']}", f"{result['accuracy']:.1%}", result["by_family"])


if __name__ == "__main__":
    main()
