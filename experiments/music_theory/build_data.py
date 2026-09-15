#!/usr/bin/env python3
"""Build a deterministic music-theory distillation benchmark."""

import hashlib
import json
import random
from collections import Counter
from pathlib import Path


SEED = 20260828
LETTERS = ["C", "D", "E", "F", "G", "A", "B"]
NATURAL_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ACCIDENTAL = {"bb": -2, "b": -1, "": 0, "#": 1, "##": 2}
ACCIDENTAL_TEXT = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}

INTERVALS = {
    "m2": (1, 1),
    "M2": (1, 2),
    "m3": (2, 3),
    "M3": (2, 4),
    "P4": (3, 5),
    "A4": (3, 6),
    "d5": (4, 6),
    "P5": (4, 7),
    "m6": (5, 8),
    "M6": (5, 9),
    "m7": (6, 10),
    "M7": (6, 11),
    "P8": (7, 12),
}

MAJOR_SCALES = {
    "Cb": ["Cb", "Db", "Eb", "Fb", "Gb", "Ab", "Bb"],
    "Gb": ["Gb", "Ab", "Bb", "Cb", "Db", "Eb", "F"],
    "Db": ["Db", "Eb", "F", "Gb", "Ab", "Bb", "C"],
    "Ab": ["Ab", "Bb", "C", "Db", "Eb", "F", "G"],
    "Eb": ["Eb", "F", "G", "Ab", "Bb", "C", "D"],
    "Bb": ["Bb", "C", "D", "Eb", "F", "G", "A"],
    "F": ["F", "G", "A", "Bb", "C", "D", "E"],
    "C": ["C", "D", "E", "F", "G", "A", "B"],
    "G": ["G", "A", "B", "C", "D", "E", "F#"],
    "D": ["D", "E", "F#", "G", "A", "B", "C#"],
    "A": ["A", "B", "C#", "D", "E", "F#", "G#"],
    "E": ["E", "F#", "G#", "A", "B", "C#", "D#"],
    "B": ["B", "C#", "D#", "E", "F#", "G#", "A#"],
    "F#": ["F#", "G#", "A#", "B", "C#", "D#", "E#"],
    "C#": ["C#", "D#", "E#", "F#", "G#", "A#", "B#"],
}

MAJOR_KEY_SIGNATURES = {
    "Cb major": ["Bb", "Eb", "Ab", "Db", "Gb", "Cb", "Fb"],
    "Gb major": ["Bb", "Eb", "Ab", "Db", "Gb", "Cb"],
    "Db major": ["Bb", "Eb", "Ab", "Db", "Gb"],
    "Ab major": ["Bb", "Eb", "Ab", "Db"],
    "Eb major": ["Bb", "Eb", "Ab"],
    "Bb major": ["Bb", "Eb"],
    "F major": ["Bb"],
    "C major": [],
    "G major": ["F#"],
    "D major": ["F#", "C#"],
    "A major": ["F#", "C#", "G#"],
    "E major": ["F#", "C#", "G#", "D#"],
    "B major": ["F#", "C#", "G#", "D#", "A#"],
    "F# major": ["F#", "C#", "G#", "D#", "A#", "E#"],
    "C# major": ["F#", "C#", "G#", "D#", "A#", "E#", "B#"],
}

MINOR_KEY_SIGNATURES = {
    "Ab minor": ["Bb", "Eb", "Ab", "Db", "Gb", "Cb", "Fb"],
    "Eb minor": ["Bb", "Eb", "Ab", "Db", "Gb", "Cb"],
    "Bb minor": ["Bb", "Eb", "Ab", "Db", "Gb"],
    "F minor": ["Bb", "Eb", "Ab", "Db"],
    "C minor": ["Bb", "Eb", "Ab"],
    "G minor": ["Bb", "Eb"],
    "D minor": ["Bb"],
    "A minor": [],
    "E minor": ["F#"],
    "B minor": ["F#", "C#"],
    "F# minor": ["F#", "C#", "G#"],
    "C# minor": ["F#", "C#", "G#", "D#"],
    "G# minor": ["F#", "C#", "G#", "D#", "A#"],
    "D# minor": ["F#", "C#", "G#", "D#", "A#", "E#"],
    "A# minor": ["F#", "C#", "G#", "D#", "A#", "E#", "B#"],
}


def parse_note(note: str) -> tuple[str, int]:
    letter = note[0]
    accidental = note[1:]
    return letter, (NATURAL_PC[letter] + ACCIDENTAL[accidental]) % 12


def spell_above(root: str, letter_steps: int, semitones: int) -> str | None:
    root_letter, root_pc = parse_note(root)
    target_letter = LETTERS[(LETTERS.index(root_letter) + letter_steps) % 7]
    target_pc = (root_pc + semitones) % 12
    raw_delta = (target_pc - NATURAL_PC[target_letter]) % 12
    delta = raw_delta if raw_delta <= 6 else raw_delta - 12
    if delta not in ACCIDENTAL_TEXT:
        return None
    return target_letter + ACCIDENTAL_TEXT[delta]


def split_group(items: list[dict], train_count: int, eval_count: int, salt: str):
    rng = random.Random(f"{SEED}:{salt}")
    shuffled = list(items)
    rng.shuffle(shuffled)
    assert len(shuffled) >= train_count + eval_count
    return shuffled[:train_count], shuffled[train_count : train_count + eval_count]


def interval_cases() -> tuple[list[dict], list[dict]]:
    roots = ["C", "C#", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B"]
    train, holdout = [], []
    for symbol, (steps, semitones) in INTERVALS.items():
        candidates = []
        for root in roots:
            target = spell_above(root, steps, semitones)
            if target is None:
                continue
            endpoints = (
                f"{root} up one octave to {target}"
                if symbol == "P8"
                else f"{root} to {target}"
            )
            candidates.append(
                {
                    "family": "interval",
                    "prompt": (
                        f"Name the ascending interval from {endpoints}. "
                        "Reply only with a standard abbreviated interval such as m3, M3, P5, A4, or d5."
                    ),
                    "expected": symbol,
                }
            )
        group_train, group_eval = split_group(candidates, 6, 2, f"interval:{symbol}")
        train.extend(group_train)
        holdout.extend(group_eval)
    return train, holdout


def triad_spelling_cases() -> tuple[list[dict], list[dict]]:
    train, holdout = [], []
    for degree in range(1, 8):
        candidates = []
        for key, scale in MAJOR_SCALES.items():
            index = degree - 1
            notes = [scale[index], scale[(index + 2) % 7], scale[(index + 4) % 7]]
            candidates.append(
                {
                    "family": "triad_spelling",
                    "prompt": (
                        f"Spell the root-position diatonic triad on scale degree {degree} in {key} major. "
                        "Reply only with the three note names, root to fifth, joined by hyphens."
                    ),
                    "expected": "-".join(notes),
                }
            )
        group_train, group_eval = split_group(candidates, 11, 4, f"triad:{degree}")
        train.extend(group_train)
        holdout.extend(group_eval)
    return train, holdout


def chord_quality_cases() -> tuple[list[dict], list[dict]]:
    roots = ["C", "C#", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B"]
    qualities = {
        "major": (4, 7),
        "minor": (3, 7),
        "diminished": (3, 6),
        "augmented": (4, 8),
    }
    train, holdout = [], []
    for quality, (third_semitones, fifth_semitones) in qualities.items():
        candidates = []
        for root in roots:
            third = spell_above(root, 2, third_semitones)
            fifth = spell_above(root, 4, fifth_semitones)
            if third is None or fifth is None:
                continue
            chord = "-".join([root, third, fifth])
            candidates.append(
                {
                    "family": "chord_quality",
                    "prompt": (
                        f"Identify the triad quality of {chord}. "
                        "Reply only with major, minor, diminished, or augmented."
                    ),
                    "expected": quality,
                }
            )
        group_train, group_eval = split_group(candidates, 10, 3, f"quality:{quality}")
        train.extend(group_train)
        holdout.extend(group_eval)
    return train, holdout


def signature_answer(accidentals: list[str]) -> str:
    if not accidentals:
        return "0 accidentals"
    kind = "sharp" if accidentals[0].endswith("#") else "flat"
    plural = kind if len(accidentals) == 1 else kind + "s"
    return f"{len(accidentals)} {plural}: {', '.join(accidentals)}"


def key_signature_cases() -> tuple[list[dict], list[dict]]:
    candidates = []
    for key, accidentals in {**MAJOR_KEY_SIGNATURES, **MINOR_KEY_SIGNATURES}.items():
        candidates.append(
            {
                "family": "key_signature",
                "prompt": (
                    f"Give the key signature of {key}. List accidentals in conventional order. "
                    "Use exactly the format '3 flats: Bb, Eb, Ab', '2 sharps: F#, C#', "
                    "or '0 accidentals'."
                ),
                "expected": signature_answer(accidentals),
            }
        )
    return split_group(candidates, 22, 8, "key-signatures")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main() -> None:
    root = Path(__file__).resolve().parent
    train: list[dict] = []
    holdout: list[dict] = []
    for builder in (
        interval_cases,
        triad_spelling_cases,
        chord_quality_cases,
        key_signature_cases,
    ):
        family_train, family_holdout = builder()
        train.extend(family_train)
        holdout.extend(family_holdout)

    rng = random.Random(SEED)
    rng.shuffle(train)
    rng.shuffle(holdout)

    for index, row in enumerate(train, 1):
        row["id"] = f"music-train-{index:03d}"
    for index, row in enumerate(holdout, 1):
        row["id"] = f"music-eval-{index:03d}"

    train_prompts = {row["prompt"] for row in train}
    eval_prompts = {row["prompt"] for row in holdout}
    assert len(train_prompts) == len(train)
    assert len(eval_prompts) == len(holdout)
    assert not train_prompts & eval_prompts

    teacher_rows = [
        {"id": row["id"], "family": row["family"], "prompt": row["prompt"], "expected": row["expected"]}
        for row in train
    ]
    verified_rows = [
        {"prompt": row["prompt"], "completion": " " + row["expected"]}
        for row in train
    ]
    eval_rows = [
        {
            "id": row["id"],
            "family": row["family"],
            "prompt": row["prompt"],
            "scorer": {"type": "exact", "expected": row["expected"], "case_sensitive": True},
        }
        for row in holdout
    ]

    write_jsonl(root / "teacher_prompts.jsonl", teacher_rows)
    write_jsonl(root / "train_verified.jsonl", verified_rows)
    write_jsonl(root / "eval.jsonl", eval_rows)

    metadata = {
        "seed": SEED,
        "train_count": len(train),
        "eval_count": len(holdout),
        "train_by_family": dict(Counter(row["family"] for row in train)),
        "eval_by_family": dict(Counter(row["family"] for row in holdout)),
        "exact_prompt_overlap": 0,
        "train_sha256": hashlib.sha256((root / "train_verified.jsonl").read_bytes()).hexdigest(),
        "eval_sha256": hashlib.sha256((root / "eval.jsonl").read_bytes()).hexdigest(),
    }
    (root / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
