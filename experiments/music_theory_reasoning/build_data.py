#!/usr/bin/env python3
"""Build a deterministic long-form music-theory distillation dataset.

The split happens by harmonic case before prompt paraphrases are created. No
key and scenario pair appears in both training and evaluation.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path


SEED = 20260828

MAJOR_SCALES = {
    "C": ["C", "D", "E", "F", "G", "A", "B"],
    "G": ["G", "A", "B", "C", "D", "E", "F#"],
    "D": ["D", "E", "F#", "G", "A", "B", "C#"],
    "A": ["A", "B", "C#", "D", "E", "F#", "G#"],
    "E": ["E", "F#", "G#", "A", "B", "C#", "D#"],
    "B": ["B", "C#", "D#", "E", "F#", "G#", "A#"],
    "F#": ["F#", "G#", "A#", "B", "C#", "D#", "E#"],
    "F": ["F", "G", "A", "Bb", "C", "D", "E"],
    "Bb": ["Bb", "C", "D", "Eb", "F", "G", "A"],
    "Eb": ["Eb", "F", "G", "Ab", "Bb", "C", "D"],
    "Ab": ["Ab", "Bb", "C", "Db", "Eb", "F", "G"],
    "Db": ["Db", "Eb", "F", "Gb", "Ab", "Bb", "C"],
}

ACCIDENTAL_VALUE = {"bb": -2, "b": -1, "": 0, "#": 1, "##": 2}
VALUE_ACCIDENTAL = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}

TRAIN_PROMPT_FRAMES = [
    "Analyze this progression in {context}: {progression}. {question} Give Roman numerals, identify the chromatic mechanism, trace the tendency tones, and explain why {tonic} still sounds like home unless a new tonic is explicitly established.",
    "A composer uses {progression} while writing in {context}. {question} Explain the harmonic logic in a compact paragraph, including Roman numerals and the important semitone resolutions.",
    "Why does {progression} make tonal sense in {context}? {question} Name each chromatic function, show its voice leading, and distinguish tonicization from modulation where relevant.",
    "Teach a theory student how to hear {progression} in {context}. {question} Your explanation must connect the Roman-numeral analysis to the altered notes and their resolutions.",
    "Give a rigorous but concise harmonic explanation of {progression} in {context}. {question} Include functional labels, chromatic scale degrees, voice leading, and the tonal destination.",
]

EVAL_PROMPT_FRAMES = [
    "In {context}, explain why the progression {progression} works. {question} Support the explanation with Roman numerals, chromatic-note resolutions, and a clear statement about the perceived tonic.",
    "Consider {progression} in a piece centered on {tonic}. {question} Provide a short analytical explanation that links chord function, altered tones, and tonal direction.",
]


def accidental(note: str, delta: int) -> str:
    letter, suffix = note[0], note[1:]
    value = ACCIDENTAL_VALUE[suffix] + delta
    if value not in VALUE_ACCIDENTAL:
        raise ValueError(f"Cannot spell alteration of {note} by {delta}")
    return letter + VALUE_ACCIDENTAL[value]


def minor(root: str) -> str:
    return f"{root}m"


def diminished(root: str) -> str:
    return f"{root}dim7"


def join_chords(*chords: str) -> str:
    return " - ".join(chords)


def fact(label: str, *patterns: str) -> dict:
    return {"label": label, "patterns": list(patterns)}


def make_case(
    key: str,
    family: str,
    progression: str,
    question: str,
    reference: str,
    facts: list[dict],
    *,
    context: str | None = None,
    tonic: str | None = None,
) -> dict:
    return {
        "case_id": f"{key.replace('#', 's').replace('b', 'f')}-{family}",
        "family": family,
        "key": key,
        "context": context or f"{key} major",
        "tonic": tonic or key,
        "progression": progression,
        "question": question,
        "reference": reference,
        "facts": facts,
    }


def cases_for_key(key: str, scale: list[str]) -> list[dict]:
    one, two, three, four, five, six, seven = scale
    sharp_one = accidental(one, 1)
    sharp_two = accidental(two, 1)
    sharp_four = accidental(four, 1)
    sharp_five = accidental(five, 1)
    flat_three = accidental(three, -1)
    flat_five = accidental(five, -1)
    flat_six = accidental(six, -1)
    flat_seven = accidental(seven, -1)
    flat_flat_seven = accidental(seven, -2)

    I = one
    ii = minor(two)
    iii = minor(three)
    IV = four
    V7 = f"{five}7"
    vi = minor(six)
    vii_dim = diminished(seven)
    V7_ii = f"{six}7"
    V7_iii = f"{seven}7"
    V7_IV = f"{one}7"
    V7_V = f"{two}7"
    V7_vi = f"{three}7"
    borrowed_iv = minor(four)
    flat_VI = flat_six
    flat_VII = flat_seven
    ii_half_dim = f"{two}m7b5"
    N6 = f"{accidental(two, -1)}/{four}"
    it6 = f"{flat_six}-{one}-{sharp_four}"
    leading_V = diminished(sharp_four)
    ct_dim = f"{one}-{flat_three}-{flat_five}-{flat_flat_seven}"
    dominant_key = five
    relative_minor = minor(six)

    rows = []
    rows.append(make_case(
        key,
        "applied_dominant_ii",
        join_chords(I, V7_ii, ii, V7, I),
        f"Explain the arrival on {ii} and why it is a tonicization rather than a modulation.",
        f"The analysis is I - V7/ii - ii - V7 - I. {V7_ii} is an applied dominant of {ii}. Its {sharp_one} is raised scale degree 1 and acts as the leading tone to {two}, so {sharp_one} resolves upward to {two}; the chordal seventh {five} normally falls to {four}. The ii chord resumes its predominant role and {V7} resolves to {I}, so the passage tonicizes ii without replacing {key} as tonic.",
        [fact("analysis", "V7/ii"), fact("applied dominant", "applied dominant", "secondary dominant"), fact("raised leading tone", sharp_one), fact("leading-tone resolution", f"{sharp_one} resolves", f"{sharp_one}.*{two}"), fact("home tonic", f"{key} as tonic", "tonicizes ii")],
    ))
    rows.append(make_case(
        key,
        "applied_dominant_iii",
        join_chords(I, V7_iii, iii, vi, ii, V7, I),
        f"Explain how {V7_iii} points to {iii} and how the phrase returns to {key}.",
        f"The functional path is I - V7/iii - iii - vi - ii - V7 - I. {V7_iii} tonicizes {iii}; its altered pitch {sharp_two}, raised scale degree 2, is the local leading tone and rises to {three}. The chordal seventh {six} can descend to {five}. After that brief emphasis, the diatonic vi - ii - V7 - I sequence and final dominant-tonic cadence restore {key}.",
        [fact("analysis", "V7/iii"), fact("tonicization", "tonicizes"), fact("alteration", sharp_two), fact("resolution", f"{sharp_two}.*{three}"), fact("return", "V7 - I", "dominant-tonic")],
    ))
    rows.append(make_case(
        key,
        "applied_dominant_IV",
        join_chords(I, V7_IV, IV, borrowed_iv, I),
        f"Explain the temporary emphasis on {IV} and the effect of the borrowed {borrowed_iv} chord.",
        f"The analysis is I - V7/IV - IV - iv - I. {V7_IV} is the dominant seventh of {IV}; its borrowed {flat_seven} supplies the chordal seventh and normally resolves down to {six} in {IV}. Changing {IV} to {borrowed_iv} then introduces {flat_six}, the minor-mode third of iv, which falls to {five} as tonic returns. The applied dominant tonicizes IV, while the borrowed iv adds mode mixture without changing the home key.",
        [fact("analysis", "V7/IV"), fact("flat seventh", flat_seven), fact("borrowed iv", "borrowed iv", "mode mixture"), fact("flat-six resolution", f"{flat_six}.*{five}"), fact("home key", "without changing", "home key")],
    ))
    rows.append(make_case(
        key,
        "applied_dominant_V",
        join_chords(I, V7_V, V7, I),
        f"Explain the chain of dominants and the altered tone in {V7_V}.",
        f"The progression is I - V7/V - V7 - I. {V7_V} is the applied dominant of the home dominant {V7}. Its {sharp_four}, raised scale degree 4, is the leading tone to {five} and resolves upward; the applied chord's seventh {one} normally falls to {seven}. This dominant-of-the-dominant intensifies the ordinary V7 - I cadence rather than establishing a new tonic.",
        [fact("analysis", "V7/V"), fact("dominant chain", "dominant-of-the-dominant", "applied dominant"), fact("sharp four", sharp_four), fact("resolution", f"{sharp_four}.*{five}"), fact("cadence", "V7 - I")],
    ))
    rows.append(make_case(
        key,
        "applied_dominant_vi",
        join_chords(I, V7_vi, vi, ii, V7, I),
        f"Explain why {V7_vi} resolves convincingly to {vi} without producing a full modulation.",
        f"The analysis is I - V7/vi - vi - ii - V7 - I. {V7_vi} is the applied dominant of {vi}. Its {sharp_five}, raised scale degree 5, acts as a leading tone and rises to {six}; the chordal seventh {two} can fall to {one}. Because {vi} immediately returns to the home-key predominant and dominant, the event is a tonicization of vi, and the closing V7 - I confirms {key}.",
        [fact("analysis", "V7/vi"), fact("applied dominant", "applied dominant"), fact("sharp five", sharp_five), fact("resolution", f"{sharp_five}.*{six}"), fact("tonicization", "tonicization of vi", "tonicizes vi")],
    ))
    rows.append(make_case(
        key,
        "dominant_chain",
        join_chords(I, V7_vi, vi, V7_V, V7, I),
        "Explain both applied dominants, their tendency tones, and the large-scale tonal direction.",
        f"The Roman numerals are I - V7/vi - vi - V7/V - V7 - I. First, {V7_vi} uses {sharp_five} as the leading tone to {six}, so it tonicizes vi. Then {V7_V} uses {sharp_four} as the leading tone to {five}, preparing the home dominant. Both applied dominants resolve down a fifth, and the final {V7} - {I} cadence makes the entire sequence a dominant chain inside {key}, not a lasting modulation.",
        [fact("analysis one", "V7/vi"), fact("analysis two", "V7/V"), fact("first resolution", f"{sharp_five}.*{six}"), fact("second resolution", f"{sharp_four}.*{five}"), fact("final cadence", f"{V7} - {I}", "final")],
    ))
    rows.append(make_case(
        key,
        "deceptive_cadence",
        join_chords(I, ii, V7, vi, IV, V7, I),
        f"Explain why {V7} to {vi} sounds deceptive but remains functional.",
        f"The analysis is I - ii - V7 - vi - IV - V7 - I. At the deceptive motion V7 - vi, the leading tone {seven} still rises to {one} and the dominant seventh {four} still falls to {three}, but the bass moves from scale degree 5 to 6 instead of 1. Those expected tendency-tone resolutions preserve dominant function while {vi} delays tonic arrival. The later V7 - I supplies the withheld authentic cadence.",
        [fact("deceptive", "deceptive"), fact("leading tone", f"{seven}.*{one}"), fact("seventh", f"{four}.*{three}"), fact("bass", "5 to 6", "scale degree 5 to 6"), fact("later cadence", "later V7 - I", "withheld")],
    ))
    rows.append(make_case(
        key,
        "borrowed_iv",
        join_chords(I, IV, borrowed_iv, I),
        f"Explain why the change from {IV} to {borrowed_iv} creates a strong return to tonic.",
        f"The analysis is I - IV - iv - I. The minor subdominant {borrowed_iv} comes from the parallel minor, so this is mode mixture. Its defining pitch is {flat_six}, lowered scale degree 6, which creates a semitone pull down to dominant scale degree {five} in the tonic chord. The root {four} can remain common while the chromatic inner voice supplies the expressive plagal return.",
        [fact("analysis", "IV - iv - I"), fact("mode mixture", "parallel minor", "mode mixture"), fact("flat six", flat_six), fact("resolution", f"{flat_six}.*{five}"), fact("plagal", "plagal")],
    ))
    rows.append(make_case(
        key,
        "borrowed_flat_VI",
        join_chords(I, flat_VI, borrowed_iv, V7, I),
        f"Explain the source and predominant behavior of {flat_VI} and {borrowed_iv}.",
        f"The Roman numerals are I - bVI - iv - V7 - I. Both {flat_VI} and {borrowed_iv} are borrowed from the parallel minor. The shared {flat_six} links the two borrowed chords, then {flat_six} descends by semitone to {five} when the dominant arrives. Their minor-mode color expands the predominant area, while V7 - I restores ordinary major-key function.",
        [fact("analysis", "bVI - iv - V7 - I"), fact("borrowed source", "parallel minor"), fact("common pitch", flat_six), fact("resolution", f"{flat_six}.*{five}"), fact("predominant", "predominant")],
    ))
    rows.append(make_case(
        key,
        "borrowed_flat_VII",
        join_chords(I, flat_VII, IV, I),
        f"Explain why {flat_VII} can move naturally through {IV} back to {I} without acting like a classical dominant.",
        f"The analysis is I - bVII - IV - I. {flat_VII} is borrowed from the parallel minor or Mixolydian collection and contains lowered scale degree 7, {flat_seven}, rather than the leading tone {seven}. The descending-fifth motion bVII to IV and the following plagal IV - I create a coherent modal path. Because the progression avoids a leading-tone dominant, its pull is contextual and plagal rather than V - I function.",
        [fact("analysis", "bVII - IV - I"), fact("borrowed", "parallel minor", "Mixolydian"), fact("flat seven", flat_seven), fact("no leading tone", "rather than the leading tone", "avoids a leading-tone"), fact("plagal", "plagal")],
    ))
    rows.append(make_case(
        key,
        "flat_VI_flat_VII_I",
        join_chords(I, flat_VI, flat_VII, I),
        "Explain the modal color, the whole-step root motion, and why the final tonic can still sound conclusive.",
        f"The analysis is I - bVI - bVII - I. The bVI chord on {flat_six} and bVII chord on {flat_seven} are modal-mixture sonorities borrowed from the parallel minor. Their roots rise by whole step toward {one}, producing a broad bVI - bVII - I ascent. The bVII chord lacks the diatonic leading tone, so the cadence is not a classical dominant-tonic cadence; repetition, bass direction, and arrival on {I} supply closure.",
        [fact("analysis", "bVI - bVII - I"), fact("mode mixture", "parallel minor", "modal-mixture"), fact("roots", "whole step"), fact("no dominant", "not a classical dominant-tonic", "lacks the diatonic leading tone"), fact("closure", "arrival on")],
    ))
    rows.append(make_case(
        key,
        "borrowed_ii_half_diminished",
        join_chords(I, ii_half_dim, V7, I),
        f"Explain how {ii_half_dim} intensifies the predominant and trace its chromatic note.",
        f"The analysis is I - ii half-diminished 7 - V7 - I. {ii_half_dim} is borrowed from the parallel minor and functions as a predominant. Its altered chord member {flat_six}, lowered scale degree 6, resolves down by semitone to dominant scale degree {five}. That voice leading makes the approach to {V7} stronger while the final V7 - I remains fully anchored in {key} major.",
        [fact("analysis", "ii half-diminished", "iiø"), fact("borrowed", "parallel minor"), fact("predominant", "predominant"), fact("flat six", flat_six), fact("resolution", f"{flat_six}.*{five}")],
    ))
    rows.append(make_case(
        key,
        "neapolitan",
        join_chords(minor(one), N6, V7, minor(one)),
        f"Explain the construction and voice leading of the Neapolitan chord {N6} in the parallel minor.",
        f"In the minor mode, the analysis is i - N6 - V7 - i. N6 is the major chord on lowered scale degree 2, {accidental(two, -1)}, placed in first inversion with {four} in the bass. It functions as an intensified predominant. The characteristic {accidental(two, -1)} normally descends to the leading tone {seven} as the harmony moves to {V7}, and the dominant then resolves to minor tonic.",
        [fact("analysis", "i - N6 - V7 - i"), fact("flat two", accidental(two, -1)), fact("first inversion", "first inversion"), fact("predominant", "predominant"), fact("resolution", f"{accidental(two, -1)}.*{seven}")],
        context=f"{one} minor",
        tonic=f"{one} minor",
    ))
    rows.append(make_case(
        key,
        "italian_augmented_sixth",
        join_chords(minor(one), it6, V7, minor(one)),
        f"Explain why the sonority {it6} is an Italian augmented-sixth chord and how it resolves.",
        f"In the minor mode, the analysis is i - It+6 - V7 - i. The Italian augmented-sixth sonority contains {flat_six}, {one}, and {sharp_four}. Its defining outer voices {flat_six} and {sharp_four} form an augmented sixth and expand outward by semitone to the dominant pitch {five}. The remaining voice {one} normally descends to the leading tone {seven}, so It+6 acts as a chromatic predominant rather than as an independent tonic.",
        [fact("analysis", r"It\+6"), fact("flat six", flat_six), fact("sharp four", sharp_four), fact("outward resolution", f"{flat_six}.*{five}", f"{sharp_four}.*{five}"), fact("predominant", "chromatic predominant")],
        context=f"{one} minor",
        tonic=f"{one} minor",
    ))
    rows.append(make_case(
        key,
        "cadential_six_four",
        join_chords(I, f"{I}/{five}", V7, I),
        "Explain why the apparent tonic six-four is heard as part of the dominant rather than as a stable tonic inversion.",
        f"The analysis is I - cadential 6/4 - V7 - I. Over a sustained dominant bass {five}, the apparent tonic pitches {one} and {three} behave as accented dissonances: {one} resolves down to {seven}, and {three} resolves down to {two}. Because those voices resolve into {V7} while the bass remains dominant, the six-four prolongs dominant function. The following V7 - I cadence supplies tonic closure.",
        [fact("analysis", "cadential 6/4"), fact("dominant bass", f"dominant bass {five}"), fact("one-seven", f"{one}.*{seven}"), fact("three-two", f"{three}.*{two}"), fact("dominant function", "prolongs dominant function")],
    ))
    rows.append(make_case(
        key,
        "pivot_to_dominant",
        join_chords(I, vi, V7_V, dominant_key),
        f"Explain how {vi} can become a pivot chord and when {dominant_key} starts to sound like a new tonic.",
        f"The opening can be heard in {key} as I - vi. The chord {vi} is also ii in {dominant_key} major, so it can serve as a diatonic pivot. Reinterpreted in the new key, the remaining motion is ii - V7 - I: {V7_V} becomes the ordinary dominant seventh of {dominant_key}, and its {sharp_four} is the new leading tone resolving to {five}. A cadence and continued emphasis on {dominant_key}, not the pivot alone, confirm modulation to the dominant.",
        [fact("pivot", "pivot"), fact("dual function", "vi", "also ii"), fact("new analysis", "ii - V7 - I"), fact("leading tone", f"{sharp_four}.*{five}"), fact("modulation criterion", "cadence", "continued emphasis")],
    ))
    rows.append(make_case(
        key,
        "pivot_to_relative_minor",
        join_chords(I, ii, V7_vi, relative_minor),
        f"Explain the reinterpretation of {ii} and how the arrival on {relative_minor} can become a modulation.",
        f"The phrase begins in {key} with I - ii. The {ii} chord is also iv in the relative minor, {relative_minor}, so it can act as a pivot. After reinterpretation, {V7_vi} is heard as V7 of the new minor tonic, with {sharp_five} serving as its leading tone and resolving upward to {six}. If {relative_minor} receives a cadence or sustained continuation, the analysis becomes I - ii equals iv - V7 - i and the tonic has genuinely changed.",
        [fact("pivot", "pivot"), fact("dual function", "also iv"), fact("new dominant", "V7 of the new minor tonic", "V7 - i"), fact("leading tone", f"{sharp_five}.*{six}"), fact("modulation criterion", "cadence", "sustained continuation")],
    ))
    rows.append(make_case(
        key,
        "applied_leading_tone_V",
        join_chords(I, leading_V, V7, I),
        f"Explain how {leading_V} functions and why it resolves to {V7}.",
        f"The analysis is I - vii diminished 7/V - V7 - I. The root {sharp_four} is raised scale degree 4, the applied leading tone to dominant scale degree {five}, so it rises by semitone into {V7}. The diminished-seventh chord packs several tendency tones around the dominant and therefore intensifies V without establishing it as a lasting tonic. The final V7 - I closes in {key}.",
        [fact("analysis", "vii diminished 7/V", "vii°7/V"), fact("applied leading tone", "applied leading tone"), fact("sharp four", sharp_four), fact("resolution", f"{sharp_four}.*{five}"), fact("home cadence", "final V7 - I")],
    ))
    rows.append(make_case(
        key,
        "common_tone_diminished",
        join_chords(I, ct_dim, f"{I}/3"),
        "Explain the common-tone diminished chord as chromatic voice leading rather than a dominant substitute.",
        f"The outer harmony is tonic, so the middle sonority is best heard as a common-tone diminished seventh embellishment. The tonic pitch {one} remains common. Its chromatic neighbors {flat_three} and {flat_five} resolve upward to the tonic-chord tones {three} and {five}, while {flat_flat_seven} can fall to {five}. Because the chord decorates I and returns directly to I6, its logic is parsimonious voice leading rather than applied-dominant function.",
        [fact("common tone", "common-tone diminished"), fact("held tonic", f"{one} remains common"), fact("third resolution", f"{flat_three}.*{three}"), fact("fifth resolution", f"{flat_five}.*{five}"), fact("not dominant", "rather than applied-dominant", "not a dominant")],
    ))
    rows.append(make_case(
        key,
        "tonicization_not_modulation",
        join_chords(I, V7_vi, vi, ii, V7, I),
        f"Decide whether the emphasis on {vi} is tonicization or modulation and justify the distinction.",
        f"The Roman numerals remain I - V7/vi - vi - ii - V7 - I in {key}. {V7_vi} gives {vi} a local dominant and its {sharp_five} resolves as a leading tone to {six}, so vi is briefly tonicized. But there is no extended phrase or cadence in the relative minor; instead ii - V7 - I immediately restores the home syntax. The event is tonicization, not modulation, because the original tonic never loses structural control.",
        [fact("analysis", "V7/vi"), fact("resolution", f"{sharp_five}.*{six}"), fact("classification", "tonicization, not modulation", "tonicization"), fact("no cadence", "no extended phrase or cadence", "no cadence"), fact("home syntax", "ii - V7 - I")],
    ))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    cases = [case for key, scale in MAJOR_SCALES.items() for case in cases_for_key(key, scale)]
    assert len(cases) == 240
    assert len({case["case_id"] for case in cases}) == len(cases)

    rng = random.Random(SEED)
    by_family: dict[str, list[dict]] = {}
    for case in cases:
        by_family.setdefault(case["family"], []).append(case)
    train_cases = []
    eval_cases = []
    for family, family_cases in sorted(by_family.items()):
        rng.shuffle(family_cases)
        train_cases.extend(family_cases[:10])
        eval_cases.extend(family_cases[10:])
    assert len(train_cases) == 200
    assert len(eval_cases) == 40

    train_rows = []
    for case in train_cases:
        for frame_index, frame in enumerate(TRAIN_PROMPT_FRAMES, 1):
            train_rows.append({
                "id": f"reason-train-{len(train_rows) + 1:04d}",
                "case_id": case["case_id"],
                "family": case["family"],
                "prompt": frame.format(**case),
                "completion": " " + case["reference"],
            })
    assert len(train_rows) == 1000

    eval_rows = []
    for case in eval_cases:
        for frame_index, frame in enumerate(EVAL_PROMPT_FRAMES, 1):
            eval_rows.append({
                "id": f"reason-eval-{len(eval_rows) + 1:03d}",
                "case_id": case["case_id"],
                "family": case["family"],
                "key": case["key"],
                "prompt": frame.format(**case),
                "reference": case["reference"],
                "facts": case["facts"],
            })
    assert len(eval_rows) == 80

    train_prompts = {row["prompt"] for row in train_rows}
    eval_prompts = {row["prompt"] for row in eval_rows}
    train_case_ids = {row["case_id"] for row in train_rows}
    eval_case_ids = {row["case_id"] for row in eval_rows}
    assert len(train_prompts) == 1000
    assert len(eval_prompts) == 80
    assert not train_prompts & eval_prompts
    assert not train_case_ids & eval_case_ids

    rng.shuffle(train_rows)
    rng.shuffle(eval_rows)

    write_jsonl(out_dir / "train_verified.jsonl", [
        {"prompt": row["prompt"], "completion": row["completion"]} for row in train_rows
    ])
    write_jsonl(out_dir / "train_manifest.jsonl", train_rows)
    write_jsonl(out_dir / "eval.jsonl", eval_rows)
    write_jsonl(out_dir / "teacher_eval_prompts.jsonl", [
        {"prompt": row["prompt"]} for row in eval_rows
    ])

    metadata = {
        "seed": SEED,
        "training_prompts": len(train_rows),
        "training_cases": len(train_case_ids),
        "evaluation_prompts": len(eval_rows),
        "evaluation_cases": len(eval_case_ids),
        "prompt_overlap": 0,
        "case_overlap": 0,
        "training_by_family": dict(sorted(Counter(row["family"] for row in train_rows).items())),
        "evaluation_by_family": dict(sorted(Counter(row["family"] for row in eval_rows).items())),
    }
    for name in ("train_verified.jsonl", "eval.jsonl"):
        metadata[f"{name}_sha256"] = hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
