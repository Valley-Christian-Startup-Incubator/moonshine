#!/usr/bin/env python3
"""Generate Questions job: expand one teacher PROMPT into N {"prompt","completion"} pairs.

Calls the teacher (largest Ollama model) in batches, validates every JSON line,
dedupes prompts (case/whitespace-insensitive), appends to results/<id>/output.jsonl
and is fully resumable: on restart it re-reads output.jsonl and continues.

Usage:
  gen_questions.py --job-id ID                  (reads jobs/ID/meta.json)
  gen_questions.py --prompt-file P --output O --count N [--docs a.pdf b.md]   (standalone / tests)

Grounded mode (source documents uploaded with the job, meta.json "docs"): the documents are
extracted and split into ~800-1,500-word sections (results/<id>/chunks.jsonl, see doc_sources.py).
Each batch gets one section (two if short) appended to the teacher request; sections are visited
round-robin in a spread-out order, with repeated passes when the count exceeds the sections.
output.jsonl stays pure {"prompt","completion"}; the source of every line is recorded in the
line-aligned sidecar sources.jsonl ({"i","hash","chunks","label"}; hash = doc_sources.prompt_hash).

Answer check (always on): after each batch the Teacher re-solves every new item on its own. It sees only the
prompt (plus the source section for grounded jobs), never the original completion, with thinking off and a
bounded answer length. The two final answers are compared (choice letters for multiple choice, numbers with a
tolerance, otherwise a short SAME/DIFFERENT judgement by the Teacher; grounded items must also be supported by
the section). Matches are kept; mismatches are dropped, logged to rejected.jsonl with the reason, and replaced
by generating more, up to about 3x the planned number of batches (then the job finishes with a warning).
Counters live in check_state.json (resumable) and progress.json extra.check.

Refuse-and-refer items (the completion's final answer says the information is not available and/or sends the user
to a counselor, office or other source): the re-solve often answers "NOT IN SOURCE" for these, which is exactly what
a correct refusal expects. Instead of dropping them, a refusal judge reads the section and KEEPS the item only when
the section really does not contain the asked-for information (or itself sends people elsewhere) and every fact the
answer states is supported; a refusal of a question the section does answer, or one that adds unsupported facts, is
dropped (reasons wrong_refusal / unsupported_refusal). Factual answers that the re-solve could not find
("NOT IN SOURCE") get one support judgement against the section before being dropped. Kept refusals are counted as
their own stat (check.json "refusals_kept").

Document weights (grounded jobs): each uploaded document has a role, "main", "supporting" (default) or
"background". Sections of main documents are chosen DOC_WEIGHTS["main"] times as often as supporting ones and
background sections DOC_WEIGHTS["background"] times as often, with a smooth weighted round-robin so the choice is
spread out. Batches from supporting and background documents carry a relevance rule (only write items related to
the PROMPT's program). sources.jsonl records each question's main document ("doc"); per-document counts are in
progress extra.doc_counts and check.json.

Variety: near-exact repeats (word-shingle Jaccard >= NEAR_EXACT_J against earlier items) are dropped and replaced
during generation; at the end a near-duplicate scan (Jaccard >= NEAR_DUP_J) writes variety.json and
progress.json extra.variety (the job page warns above 5%).
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moonshine_common as mc  # noqa: E402
import doc_sources as ds  # noqa: E402

SYSTEM = (
    "You are the teacher model in a training-data pipeline. You write training items exactly as the "
    "user's PROMPT specifies. Output ONLY JSON Lines: one JSON object per line with exactly two string "
    "keys, \"prompt\" and \"completion\". No markdown fences, no numbering, no commentary before or after."
)

BATCH_INSTRUCTIONS = """=== BATCH INSTRUCTIONS (these override any total count or line ranges stated in the PROMPT above) ===
- This request is batch {batch_no} of about {batch_total} for a data set of {count} items in total. Items already written so far: {done}.
- Write EXACTLY {n} new items in this batch.
- Apply the PROMPT's percentages (item-type mix, topic mix, difficulty mix) within this batch. For this batch aim for roughly: {mix_hint}
- Make every item clearly different from earlier batches: new situations, numbers, names, sub-topics and wording. Variety seed: {seed}.
- Vary the items inside this batch too: different sub-topics, question types, difficulty levels, numbers and scenarios. No two items may be near-repeats (the same question with only a number or name changed).
- Before writing each item, verify the final answer is correct; if unsure, skip it and write a different item.
{avoid_block}- Output format: {n} lines, each a single JSON object {{"prompt": "...", "completion": "..."}} with only those two keys. Escape newlines inside strings as \\n. Nothing else."""


GROUNDING_RULES = """=== SOURCE RULES (this batch is grounded in the SOURCE PASSAGE above) ===
- Write every item so it can be answered from the SOURCE PASSAGE alone. Build each item on specific facts, rules, numbers, limits, names, definitions and procedures stated in the passage.
- Do not invent facts beyond the passage: no made-up numbers, rule numbers, penalties, dimensions or details. If the passage does not support something, leave it out.
- The student model will NEVER see the passage. Every prompt must be self-contained and read like a real question a user would ask: give any needed context inside the prompt, name things by their real names (rule numbers, section names, defined terms), and never write "according to the passage", "the text", "the document", "this section", "the excerpt" or "the source".
- Put the substance in the completion naturally: state the actual fact, number or rule (cite the rule/section number when the passage gives one) inside the worked reasoning and the final answer. In completions too, never say "the passage", "the text" or "the excerpt"; name the rule, section or document instead (for example "R301 says" or "the game manual says").
- Cover different parts of the passage; do not write several items about the same sentence. Ask about exact numbers in more than one phrasing when the passage has them.
- Still follow the PROMPT above for item types, mix, difficulty, notation and the completion format.
- Tables in the passage are markdown rows under a header row; every row is one complete record (read it across).
- If the PROMPT asks for refuse-and-refer items, write their final answer so it plainly says the information is not available here (for example "I don't have that information") and where to go instead; only write them for things this passage really does not state.
- If part of the passage is boilerplate (contents, legal notices, credits), skip that part."""

PASSAGE_REF = re.compile(r"\b(the|this|that|above|provided|given)\s+(passage|excerpt|source text|source passage|text above|section above|document above)\b"
                         r"|\baccording to the (passage|text|excerpt|document|source|section)\b", re.I)


# ---------------------------------------------------------------- answer check
CHECK_SYSTEM = """You are checking a practice question by solving it yourself from scratch.
- Work it out briefly: at most 5 short lines of plain-text work (no LaTeX, no markdown, no headings).
- Then write one last line: "Final Answer: <answer>". For multiple choice give the letter and the choice text, for example "Final Answer: B) 24 cm^2/s".
- If the question does not have exactly one clear correct answer (ambiguous, no correct choice, or more than one correct choice), write "Final Answer: INVALID".
- Stop immediately after the Final Answer line: no re-checking, notes or summary after it."""

CHECK_GROUNDED = """
- The question was written from the SOURCE PASSAGE below (an official document). Answer from the passage. Tables are written as markdown rows ("| a | b | c |"): read each row across, under the header row. If the passage does not contain what is needed to answer, write "Final Answer: NOT IN SOURCE".

=== SOURCE PASSAGE ===
{passage}
=== END SOURCE PASSAGE ==="""

JUDGE_SYSTEM = """You compare two final answers to the same question. Reply with exactly one word:
SAME if they give the same answer (equivalent values or expressions, the same choice, or the same fact or decision; ignore wording, formatting, units written differently and trivial rounding),
DIFFERENT otherwise."""

JUDGE_GROUNDED = """
A SOURCE PASSAGE (the official document the question was written from) is included. Reply SAME only if the two answers agree AND ANSWER 1 is supported by the passage; otherwise reply DIFFERENT.

=== SOURCE PASSAGE ===
{passage}
=== END SOURCE PASSAGE ==="""

REFUSAL_JUDGE_SYSTEM = """You check an answer that declines to answer or sends the user somewhere else (a counselor, an office,
a college, a catalog, a website). You get the QUESTION, the ANSWER and the SOURCE PASSAGE the question was written from.
Reply with exactly one word:
KEEP if BOTH are true: (1) the passage does not contain the specific information the question asks for, OR the passage
itself tells people to go elsewhere for it; and (2) every concrete fact the answer states (numbers, dates, names, rules)
appears in the passage. General referrals (ask your counselor, contact the school office or the college, check the
catalog) and contact details do not need support.
WRONG if the passage clearly contains the answer to the question, so refusing is wrong.
UNSUPPORTED if the answer states a concrete fact that the passage does not support or contradicts.

=== SOURCE PASSAGE ===
{passage}
=== END SOURCE PASSAGE ==="""

SUPPORT_JUDGE_SYSTEM = """You check whether an answer is supported by a source passage. You get the QUESTION, the ANSWER and the
SOURCE PASSAGE (tables are written as markdown rows: read each row across). Reply with exactly one word:
SUPPORTED if the passage states the facts in the answer (same values, names and rules; wording may differ),
UNSUPPORTED otherwise.

=== SOURCE PASSAGE ===
{passage}
=== END SOURCE PASSAGE ==="""

# The final answer declines ("I don't have that information", "the date isn't listed") ...
DECLINE_RE = re.compile(
    r"\b(?:i\s+(?:do\s+not|don'?t|cannot|can'?t)\s+(?:have|find|see|know|say|tell|confirm|guarantee|give|provide|calculate)"
    r"|(?:is|are)\s*(?:not|n'?t)\s+(?:listed|included|stated|specified|given|provided|covered|mentioned|documented)"
    r"|(?:not|isn'?t|aren'?t)\s+(?:in|on)\s+(?:the|this|these|my|our)\s+(?:[\w\-]+\s+){0,4}(?:information|documents?|list|page|materials?|sources?|catalog)"
    r"|no\s+information|not\s+sure|unable\s+to)", re.I)
# ... or refers the user elsewhere ("ask your counselor", "contact the Dual Credit office").
REFER_RE = re.compile(
    r"\b(?:ask|contact|check\s+with|talk\s+(?:to|with)|reach\s+out\s+to|email|consult|speak\s+(?:to|with))\s+(?:your|the|a|an)?\s*"
    r"(?:\w+\s+){0,3}(?:counselor|office|coordinator|registrar|college|university|school|teacher|advisor|staff|department)"
    r"|\brefer(?:red)?\s+(?:you|them)\s+to", re.I)


ALL_DOCS_MAX_WORDS = 9000  # documents up to this size are shown whole to the support and refusal judges


def is_refusal(ans: str | None) -> bool:
    """Declines and/or refers the user elsewhere (routing for the refusal judge)."""
    return bool(ans and (DECLINE_RE.search(ans) or REFER_RE.search(ans)))


CHECK_MAX_TOKENS = 320      # bounded re-solve (think off); one retry with RETRY_TOKENS if no final answer
CHECK_RETRY_TOKENS = 700
DOC_WEIGHTS = {"main": 3.0, "supporting": 1.0, "background": 0.3}  # how often a section is chosen, per role
ATTEMPT_FACTOR = 3          # cap on generation calls: about 3x the planned batch count
NEAR_EXACT_J = 0.9          # dropped and replaced during generation
NEAR_DUP_J = 0.8            # counted in the final variety scan
NEAR_DUP_WARN = 0.05

FINAL_RE = re.compile(r"(?:final\s+answer|answer)\s*(?:is)?\s*(?:[:=\-]|\bis\b)\s*(.+)", re.I)
OPTION_RE = re.compile(r"^\s*\(?([A-F])[\).:]\s+(.+?)\s*$", re.M)


def extract_final(text: str) -> str | None:
    """Text after the last 'Final Answer:' / 'Answer:' marker (or a \\boxed{}), else None."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = text.replace("**", "").replace("__", "")
    hits = FINAL_RE.findall(text)
    if hits:
        ans = hits[-1].strip()
    else:
        m = re.findall(r"\\boxed\{([^{}]+)\}", text)
        if not m:
            return None
        ans = m[-1]
    ans = ans.split("\n")[0].strip().strip("$").strip()
    ans = re.sub(r"\s*(Skill|Reasoning)\s*:.*$", "", ans, flags=re.I).strip()
    return ans.rstrip(".").strip() or None


def mc_options(prompt: str) -> dict[str, str]:
    opts = {}
    for letter, txt in OPTION_RE.findall(prompt or ""):
        opts.setdefault(letter.upper(), txt.strip())
    return opts if len(opts) >= 2 and "A" in opts and "B" in opts else {}


def _norm_text(s: str) -> str:
    s = s.lower().replace("−", "-").replace("π", "pi")
    s = re.sub(r"\\[a-z]+", " ", s)
    return re.sub(r"[^a-z0-9.+\-*/^=<>]+", "", s)


def letter_of(ans: str | None, opts: dict[str, str]) -> str | None:
    if not ans or not opts:
        return None
    letters = "".join(sorted(opts))
    m = re.match(rf"^\s*(?:option|choice)?\s*\(?([{letters}])\)?(?=$|[\s).:,;\-])", ans)
    if m:
        return m.group(1)
    m = re.search(rf"\b(?:option|choice|answer is)\s*\(?([{letters}])\b", ans, re.I)
    if m:
        return m.group(1).upper()
    na = _norm_text(ans)
    for k, v in opts.items():
        if na and na == _norm_text(v):
            return k
    return None


_NUM_TOKEN = re.compile(r"\s*(\d+(?:\.\d+)?|\.\d+|pi|π|sqrt|e(?![a-z])|[()+\-*/^])", re.I)


def number_of(ans: str | None) -> float | None:
    """Value of a simple numeric answer ('24', '-3/2', '8pi', '2 sqrt(3)', 'x = 4.5 cm'), else None."""
    if not ans:
        return None
    s = re.sub(r"^\s*\(?[A-F]\)\s*", "", ans)  # "B) 24" -> "24"
    s = re.sub(r"^[^=\d(\-.π]*=\s*", "", s)    # "x = 4" -> "4"
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    toks, pos = [], 0
    while pos < len(s):
        m = _NUM_TOKEN.match(s, pos)
        if not m:
            break
        toks.append(m.group(1).lower())
        pos = m.end()
    rest = s[pos:].strip()
    if not toks or not any(t[0].isdigit() or t[0] == "." or t in ("pi", "π") for t in toks):
        return None
    if rest and re.match(r"^[a-z0-9°%]", rest, re.I) and not (
            len(rest) <= 20 and re.match(r"^[a-zA-Z°%]+(?:[\^/·*][a-zA-Z0-9]+)*(?:\s+[a-zA-Z]+(?:[\^/][a-zA-Z0-9]+)*)?\.?$", rest)):
        return None  # more words follow: not a plain number
    expr = ""
    prev = ""
    for t in toks:
        val = {"pi": "PI", "π": "PI", "e": "E", "sqrt": "SQRT", "^": "**"}.get(t, t)
        if prev and (prev[-1].isdigit() or prev in ("PI", "E", ")")) and (val[0].isdigit() or val in ("PI", "E", "SQRT", "(")):
            expr += "*"
        expr += val
        prev = val
    expr = expr.rstrip("+-*/")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            return {"PI": math.pi, "E": math.e}[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 50:
                raise ValueError
            return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}.get(type(n.op)) if not isinstance(
                n.op, (ast.Div, ast.Pow)) else (a / b if isinstance(n.op, ast.Div) else a ** b)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "SQRT" and len(n.args) == 1:
            return math.sqrt(ev(n.args[0]))
        raise ValueError
    try:
        v = ev(tree)
        return v if isinstance(v, float) and math.isfinite(v) else None
    except (ValueError, KeyError, ZeroDivisionError, OverflowError, TypeError):
        return None


def _strip_letter(ans: str | None) -> str:
    return re.sub(r"^\s*\(?[A-F]\)\s*", "", re.sub(r"^[^=\d(\-.π]*=\s*", "", ans or ""))


class AnswerChecker:
    """Re-solves items with the Teacher and compares final answers. Stats persist in check_state.json."""

    def __init__(self, teacher: str, out_dir: str, all_docs: str | None = None):
        self.teacher = teacher
        # every section of the job's documents, when they are short enough to show at once (ALL_DOCS_MAX_WORDS):
        # facts the Teacher took from another section (often via the PROMPT) are then checked against the documents
        # instead of being dropped as not_in_source, and a refusal is wrong if ANY document answers it.
        self.all_docs = all_docs
        self.state_path = os.path.join(out_dir, "check_state.json")
        self.rej_path = os.path.join(out_dir, "rejected.jsonl")
        st = mc.read_json(self.state_path, {}) or {}
        self.s = {"checked": 0, "kept": 0, "dropped": 0, "reasons": {}, "methods": {}, "judge_calls": 0,
                  "retries": 0, "check_sec": 0.0, "gen_sec": 0.0, "attempts": 0, "cap_hit": False,
                  "near_exact_dropped": 0, "refusals_checked": 0, "refusals_kept": 0, "support_kept": 0, **st}

    def save(self):
        mc.write_json_atomic(self.state_path, self.s)

    def _chat(self, system: str, user: str, num_predict: int, ctx: int) -> str:
        for attempt in range(3):
            try:
                r = mc.teacher_chat(self.teacher, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                    temperature=0.0 if attempt == 0 else 0.3, num_predict=num_predict, num_ctx=ctx,
                                    timeout=600, think=False)
                return re.sub(r"<think>.*?</think>", "", (r.get("message") or {}).get("content", "") or "", flags=re.S)
            except mc.StopRequested:
                raise
            except Exception as e:  # noqa: BLE001
                mc.log(f"Answer check: teacher call failed ({e}); retry {attempt + 1}/3")
                time.sleep(3 * (attempt + 1))
        raise RuntimeError("teacher unavailable for the answer check")

    def solve(self, prompt: str, passage: str | None) -> tuple[str | None, str]:
        system = CHECK_SYSTEM + (CHECK_GROUNDED.format(passage=passage) if passage else "")
        ctx = 16384 if passage else 4096
        text = self._chat(system, prompt, CHECK_MAX_TOKENS, ctx)
        ans = extract_final(text)
        if ans is None:  # ran out of room: one retry with more room
            self.s["retries"] += 1
            text = self._chat(system, prompt + "\n\n(Be brief: at most 3 lines of work.)", CHECK_RETRY_TOKENS, ctx)
            ans = extract_final(text)
        return ans, text

    def judge(self, prompt: str, key: str, other: str, passage: str | None) -> bool:
        self.s["judge_calls"] += 1
        system = JUDGE_SYSTEM + (JUDGE_GROUNDED.format(passage=passage) if passage else "")
        user = f"QUESTION:\n{prompt[:3000]}\n\nANSWER 1: {key[:600]}\nANSWER 2: {other[:600]}\n\nSAME or DIFFERENT?"
        out = self._chat(system, user, 8, 16384 if passage else 4096).strip().upper()
        return out.startswith("SAME")

    def _verdict(self, system: str, prompt: str, answer: str, passage: str, words_: tuple[str, ...]) -> str:
        self.s["judge_calls"] += 1
        user = f"QUESTION:\n{prompt[:3000]}\n\nANSWER: {answer[:1200]}\n\nReply with one word: {' or '.join(words_)}."
        ctx = 16384 if len(passage) < 30000 else 32768
        out = self._chat(system.format(passage=passage), user, 8, ctx).strip().upper()
        return next((w for w in words_ if out.startswith(w)), "")

    def refusal_verdict(self, prompt: str, answer: str, passage: str) -> str:
        return self._verdict(REFUSAL_JUDGE_SYSTEM, prompt, answer, passage, ("KEEP", "WRONG", "UNSUPPORTED"))

    def supported(self, prompt: str, answer: str, passage: str) -> bool:
        return self._verdict(SUPPORT_JUDGE_SYSTEM, prompt, answer, passage, ("SUPPORTED", "UNSUPPORTED")) == "SUPPORTED"

    def compare(self, item: dict, key: str | None, chk: str | None, passage: str | None) -> tuple[bool, str, str]:
        """(keep, method, reason)."""
        if is_refusal(key) and passage and (chk is None or "NOT IN SOURCE" in chk.upper() or chk.upper().startswith("INVALID")):
            # refuse-and-refer answer and the re-solve found nothing either: that agrees with a correct refusal.
            # Keep it only if the passage really lacks the information (or itself sends people elsewhere) and the
            # answer adds no unsupported facts. (If the re-solve found a real answer, the normal comparison below
            # decides, so a refusal of something the passage answers is dropped as judge_different.)
            v = self.refusal_verdict(item["prompt"], key, self.all_docs or passage)
            if v == "KEEP":
                return True, "refusal", ""
            if v == "UNSUPPORTED" and not DECLINE_RE.search(key) and self.supported(item["prompt"], key, self.all_docs or passage):
                # a factual answer that ends with "contact ..." (troubleshooting), not a refusal: keep it if supported
                return True, "support_docs" if self.all_docs else "support", ""
            return False, "refusal", ("wrong_refusal" if v == "WRONG" else "unsupported_refusal")
        if chk is None:
            return False, "none", "no_answer"
        cu = chk.upper()
        if cu.startswith("INVALID"):
            return False, "none", "invalid_question"
        if "NOT IN SOURCE" in cu:
            # the short re-solve could not find it; give a factual answer one support check against the passage
            if key is not None and passage and self.supported(item["prompt"], key, passage):
                return True, "support", ""
            if key is not None and self.all_docs and self.supported(item["prompt"], key, self.all_docs):
                return True, "support_docs", ""  # stated in another section of the uploaded documents
            return False, "none", "not_in_source"
        if key is None:
            return False, "none", "no_final_answer_in_completion"
        opts = mc_options(item["prompt"])
        if opts:
            lk, lc = letter_of(key, opts), letter_of(chk, opts)
            if lk and lc:
                return (lk == lc), "letter", ("" if lk == lc else f"letter_mismatch ({lk} vs {lc})")
        nk, nc = number_of(key), number_of(chk)
        if nk is not None and nc is not None and not opts:
            ints = all(re.fullmatch(r"\s*-?[\d,]+\s*(?:[a-zA-Z°%][^\d]*)?", x or "") for x in (_strip_letter(key), _strip_letter(chk)))
            if ints or math.isclose(nk, nc, rel_tol=2e-3, abs_tol=1e-9):
                same = math.isclose(nk, nc, rel_tol=2e-3, abs_tol=1e-9) if not ints else nk == nc
                return same, "number", ("" if same else f"number_mismatch ({nk:g} vs {nc:g})")
            if abs(nk - nc) > 0.05 * max(abs(nk), abs(nc), 1e-9):
                return False, "number", f"number_mismatch ({nk:g} vs {nc:g})"
            # close but not equal (rounding?): let the Teacher judge
        if _norm_text(key) == _norm_text(chk):
            return True, "text", ""
        same = self.judge(item["prompt"], key, chk, passage)
        return same, "judge", ("" if same else "judge_different")

    def check_batch(self, items: list[dict], passage: str | None, meta: dict) -> list[dict]:
        kept = []
        t0 = time.time()
        rejected = []
        for it in items:
            mc.check_stop()
            key = extract_final(it["completion"])
            chk, raw = self.solve(it["prompt"], passage)
            ok, method, reason = self.compare(it, key, chk, passage)
            self.s["checked"] += 1
            self.s["methods"][method] = self.s["methods"].get(method, 0) + 1
            # refuse-and-refer stat: answers that decline, or referrals confirmed by the refusal judge
            refusal_item = bool(key and DECLINE_RE.search(key)) or method == "refusal"
            if refusal_item:
                self.s["refusals_checked"] = self.s.get("refusals_checked", 0) + 1
            if ok:
                kept.append(it)
                if refusal_item:
                    self.s["refusals_kept"] = self.s.get("refusals_kept", 0) + 1
                if method in ("support", "support_docs"):
                    self.s["support_kept"] = self.s.get("support_kept", 0) + 1
            else:
                r0 = reason.split(" ")[0]
                self.s["reasons"][r0] = self.s["reasons"].get(r0, 0) + 1
                rejected.append({"prompt": it["prompt"], "completion": it["completion"], "reason": reason,
                                 "method": method, "answer_key": key, "check_answer": chk,
                                 "check_work": raw.strip()[-600:], **meta, "at": mc.now_iso()})
        with mc.critical():
            if rejected:
                with open(self.rej_path, "a", encoding="utf-8") as f:
                    for o in rejected:
                        f.write(json.dumps(o, ensure_ascii=False) + "\n")
            self.s["kept"] += len(kept)
            self.s["dropped"] += len(rejected)
            self.s["check_sec"] += time.time() - t0
            self.save()
        return kept

    def summary(self, count: int, done: int) -> dict:
        d = self.s
        shortfall = max(0, count - done)
        return {"checked": d["checked"], "kept": d["kept"], "dropped": d["dropped"],
                "replaced": max(0, d["dropped"] - shortfall),
                "drop_rate": round(d["dropped"] / d["checked"], 4) if d["checked"] else None,
                "reasons": d["reasons"], "methods": d["methods"], "judge_calls": d["judge_calls"],
                "check_sec": round(d["check_sec"], 1), "gen_sec": round(d["gen_sec"], 1),
                "attempts": d["attempts"], "max_attempts": d.get("max_attempts"), "cap_hit": d["cap_hit"],
                "near_exact_dropped": d.get("near_exact_dropped", 0),
                "refusals_checked": d.get("refusals_checked", 0), "refusals_kept": d.get("refusals_kept", 0),
                "support_kept": d.get("support_kept", 0)}


# ---------------------------------------------------------------- variety (near-duplicates)
_WORD = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")


def shingles(text: str, k: int = 3) -> frozenset:
    w = _WORD.findall((text or "").lower())
    if len(w) < k:
        return frozenset([" ".join(w)]) if w else frozenset()
    return frozenset(" ".join(w[i:i + k]) for i in range(len(w) - k + 1))


class ShingleIndex:
    """Inverted index for word-shingle Jaccard lookups (no dependencies)."""

    def __init__(self):
        self.sets: list[frozenset] = []
        self.post: dict[str, list[int]] = {}

    def best(self, sh: frozenset) -> tuple[float, int]:
        if not sh:
            return 0.0, -1
        n = len(self.sets)
        stop = max(50, n // 5)
        counts: dict[int, int] = {}
        for g in sh:
            ids = self.post.get(g)
            if ids and len(ids) <= stop:
                for i in ids:
                    counts[i] = counts.get(i, 0) + 1
        best, bi = 0.0, -1
        for i, c in counts.items():
            j = len(sh & self.sets[i]) / len(sh | self.sets[i])
            if j > best:
                best, bi = j, i
        return best, bi

    def add(self, sh: frozenset):
        i = len(self.sets)
        self.sets.append(sh)
        for g in sh:
            self.post.setdefault(g, []).append(i)


def variety_stats(prompts: list[str], threshold: float = NEAR_DUP_J) -> dict:
    idx = ShingleIndex()
    near, examples = 0, []
    for i, p in enumerate(prompts):
        sh = shingles(p)
        j, bi = idx.best(sh)
        if j >= threshold:
            near += 1
            if len(examples) < 5:
                examples.append({"i": i, "similar_to": bi, "jaccard": round(j, 3),
                                 "prompt": re.sub(r"\s+", " ", p)[:160],
                                 "other": re.sub(r"\s+", " ", prompts[bi])[:160]})
        idx.add(sh)
    n = len(prompts)
    rate = near / n if n else 0.0
    return {"items": n, "near_duplicates": near, "rate": round(rate, 4), "threshold": threshold,
            "method": "word 3-shingle Jaccard", "warn": rate > NEAR_DUP_WARN, "examples": examples,
            "created_at": mc.now_iso()}



def doc_role(role) -> str:
    r = str(role or "").strip().lower()
    return r if r in DOC_WEIGHTS else "supporting"


def chunk_schedule(chunks: list[dict], roles: dict[str, str], length: int = 20000) -> list[int]:
    """Section visiting order with each section's share proportional to its document's role weight.

    Smooth weighted round-robin (the nginx algorithm) over the sections, taken in the low-discrepancy chunk_order so
    that sections of the same document are spread out; deterministic, so a resumed job continues the same order.
    """
    base = chunk_order(len(chunks))
    w = [DOC_WEIGHTS[doc_role(roles.get(chunks[i]["doc"]))] for i in base]
    total = sum(w) or 1.0
    cur = [0.0] * len(base)
    out = []
    for _ in range(length):
        for k in range(len(base)):
            cur[k] += w[k]
        k = max(range(len(base)), key=lambda j: cur[j])
        cur[k] -= total
        out.append(base[k])
    return out


def chunk_order(n: int) -> list[int]:
    """Low-discrepancy visiting order: any prefix is spread across the whole document."""
    phi = 0.6180339887498949
    return sorted(range(n), key=lambda i: ((i * phi) % 1.0, i))


def load_sources(src_path: str, items: list[dict]) -> list[dict]:
    """Realign sources.jsonl with the (possibly cleaned) output items by prompt hash; rewrite it."""
    by_hash = {}
    if os.path.exists(src_path):
        with open(src_path, encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                    by_hash.setdefault(o.get("hash"), o)
                except json.JSONDecodeError:
                    pass
    out = []
    for i, it in enumerate(items):
        h = ds.prompt_hash(it["prompt"])
        o = by_hash.get(h) or {"chunks": [], "label": None}
        row = {"i": i, "hash": h, "chunks": o.get("chunks") or [], "label": o.get("label")}
        if o.get("doc"):
            row["doc"] = o["doc"]
        out.append(row)
    tmp = src_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    os.replace(tmp, src_path)
    return out


def parse_mix(prompt_text: str) -> list[tuple[str, float]]:
    """Best-effort: find 'NN% something' lines in the prompt (item-type mix)."""
    out = []
    for m in re.finditer(r"(\d{1,3})\s*%\s*([A-Za-z][^\n;,.()]{2,60})", prompt_text):
        pct = int(m.group(1))
        if 0 < pct <= 100:
            out.append((m.group(2).strip(), pct))
    return out


def batch_mix_hint(prompt_text: str, n: int, batch_no: int) -> str:
    mix = parse_mix(prompt_text)
    # Keep only the first group that sums to ~100 (usually the item-type mix)
    group, total = [], 0
    for label, pct in mix:
        group.append((label, pct))
        total += pct
        if total >= 99:
            break
    if not group or not (95 <= total <= 105):
        return "follow the PROMPT's percentages as closely as a batch of this size allows."
    # Largest-remainder allocation, rotated by batch_no so rounding evens out across batches
    raw = [(label, pct * n / 100) for label, pct in group]
    base = [int(x) for _, x in raw]
    rem = n - sum(base)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i][1] - base[i]), (i + batch_no) % len(raw)))
    for i in order[:rem]:
        base[i] += 1
    return "; ".join(f"{base[i]} x {raw[i][0]}" for i in range(len(raw)) if base[i] > 0) + "."


def extract_items(text: str) -> tuple[list[dict], int]:
    """Return (valid items, count of rejected candidate lines)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```[a-zA-Z]*", "", text)
    items, bad = [], 0
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            arr = json.loads(stripped)
            for obj in arr:
                (items.append({"prompt": obj["prompt"].strip(), "completion": obj["completion"].strip()})
                 if mc.valid_pair(obj) else None)
            return items, max(0, len(arr) - len(items))
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if mc.valid_pair(obj):
            items.append({"prompt": obj["prompt"].strip(), "completion": obj["completion"].strip()})
        else:
            bad += 1
    return items, bad


def load_existing(out_path: str) -> tuple[list[dict], set[str]]:
    """Re-read output.jsonl, keep only valid unique lines, rewrite if anything was dropped."""
    items, seen, dropped = [], set(), 0
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    dropped += 1
                    continue
                if not mc.valid_pair(obj) or mc.norm_prompt(obj["prompt"]) in seen:
                    dropped += 1
                    continue
                seen.add(mc.norm_prompt(obj["prompt"]))
                items.append(obj)
        if dropped:
            tmp = out_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for obj in items:
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            os.replace(tmp, out_path)
            mc.log(f"Resume: dropped {dropped} invalid/duplicate line(s) from existing output")
    return items, seen


def stem_key(prompt: str) -> str:
    """Question text without its A-D option lines, normalized (same stem = same question)."""
    return _norm_text(OPTION_RE.sub("", prompt or ""))


def run(prompt_file: str, out_path: str, count: int, batch_size: int, job_id: str | None,
        teacher: str | None = None, max_batches: int | None = None, docs: list[dict] | None = None) -> dict:
    prompt_text = open(prompt_file, encoding="utf-8", errors="replace").read().strip()
    if len(prompt_text) < 20:
        raise SystemExit("The uploaded prompt file is empty or too short.")
    out_dir = os.path.dirname(os.path.abspath(out_path))
    chunks: list[dict] = []
    if docs:
        if job_id:
            chunks = ds.prepare_job_chunks(job_id, docs)
        else:
            chunks = ds.read_chunks(os.path.join(out_dir, "chunks.jsonl"))
            if not chunks:
                chunks, summary = ds.build_chunks(docs)
                with open(os.path.join(out_dir, "chunks.jsonl"), "w", encoding="utf-8") as f:
                    for c in chunks:
                        f.write(json.dumps(c, ensure_ascii=False) + "\n")
        if not chunks:
            raise SystemExit("None of the source documents contained readable text.")
    grounded = bool(chunks)
    teacher = teacher or mc.pick_teacher()
    items, seen = load_existing(out_path)
    done = len(items)
    src_path = os.path.join(out_dir, "sources.jsonl")
    sources = load_sources(src_path, items) if grounded else []
    state_path = os.path.join(out_dir, "gen_state.json")
    gstate = (mc.read_json(state_path, {}) or {}) if grounded else {}
    roles = {(d.get("name") or os.path.basename(d.get("file", ""))): doc_role(d.get("role")) for d in (docs or [])}
    order = chunk_schedule(chunks, roles) if grounded else []
    if grounded and (gstate.get("n_chunks") != len(chunks) or gstate.get("roles") != roles):
        gstate = {"cursor": 0, "n_chunks": len(chunks), "roles": roles}
    doc_counts: dict[str, int] = {}
    for s_ in sources:
        dn = s_.get("doc") or (chunks[s_["chunks"][0]]["doc"] if s_.get("chunks") and s_["chunks"][0] < len(chunks) else None)
        if dn:
            doc_counts[dn] = doc_counts.get(dn, 0) + 1
    # items per section visit: enough passes to reach the count, scaled by section length below
    per_visit = min(batch_size, max(3, -(-count // max(1, len(chunks))))) if grounded else batch_size
    all_docs = ds.all_documents_text(chunks, ALL_DOCS_MAX_WORDS) if grounded else None
    checker = AnswerChecker(teacher, out_dir, all_docs)
    if os.path.exists(checker.rej_path):  # never re-check a question the check already dropped
        with open(checker.rej_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    seen.add(mc.norm_prompt(json.loads(line)["prompt"]))
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
    batch_total = max(1, -(-count // (per_visit if grounded else batch_size)))
    max_attempts = max(3, ATTEMPT_FACTOR * batch_total)
    checker.s["max_attempts"] = max_attempts
    mc.log(f"Generate Questions: teacher={teacher} target={count} batch_size={per_visit if grounded else batch_size} "
           f"already_done={done} answer_check=on (cap {max_attempts} batches)"
           + (f" grounded in {len(chunks)} document sections "
              f"({len({c['doc'] for c in chunks})} document(s))" if grounded else ""))
    if checker.s["checked"]:
        mc.log(f"Resume: answer check so far: {checker.s['checked']} checked, {checker.s['dropped']} dropped, "
               f"{checker.s['attempts']} batches used")
    prog = mc.Progress(job_id, ["generate"]) if job_id else None
    if prog:
        prog.start_phase("generate", count, done, "items", "Generating and checking questions")
        prog.set_extra(teacher=teacher, check=checker.summary(count, done),
                       **({"grounded": True, "chunks": len(chunks),
                           "documents": sorted({c["doc"] for c in chunks}), "doc_roles": roles,
                           "doc_counts": doc_counts} if grounded else {}))
    # near-exact repeat detection against everything kept so far
    index = ShingleIndex()
    stems = set()
    for it in items:
        index.add(shingles(it["prompt"]))
        stems.add(stem_key(it["prompt"]))
    batch_no = max(done // (per_visit if grounded else batch_size), int(checker.s.get("attempts", 0)))
    consecutive_bad = 0
    stats = {"calls": 0, "gen_tokens": 0, "gen_sec": 0.0, "wall_sec": 0.0, "accepted": 0, "rejected": 0, "dupes": 0,
             "check_dropped": 0}
    rng = random.Random()
    batches_run = 0
    t_start = time.time()
    while done < count:
        mc.check_stop()
        if max_batches is not None and batches_run >= max_batches:
            break
        if checker.s["attempts"] >= max_attempts:
            checker.s["cap_hit"] = True
            checker.save()
            break
        visit: list[dict] = []
        if grounded:
            cur = int(gstate.get("cursor", 0))
            visit = [chunks[order[cur % len(order)]]]
            if visit[0]["words"] < 600 and len(chunks) > 1:
                # short section: add the next scheduled section that is a different one
                for k in range(1, len(order)):
                    c2 = chunks[order[(cur + k) % len(order)]]
                    if c2["id"] != visit[0]["id"]:
                        visit.append(c2)
                        break
            vw = sum(c["words"] for c in visit)
            n = min(count - done, batch_size, max(2, round(per_visit * vw / ds.TARGET_WORDS)))
        else:
            n = min(batch_size, count - done)
        ask = n  # shortfalls from rejects/dupes/check drops are filled by the next batch
        batch_no += 1
        recent = [it["prompt"] for it in items[-8:]]
        sample = rng.sample(items[:-8], min(4, max(0, len(items) - 8))) if len(items) > 8 else []
        avoid = recent + [it["prompt"] for it in sample]
        if grounded:  # earlier items from the same section(s): the main source of repeats on later passes
            ids = {c["id"] for c in visit}
            same = [items[k]["prompt"] for k, s_ in enumerate(sources) if ids & set(s_.get("chunks") or [])]
            avoid += [p for p in same[-10:] if p not in avoid]
        avoid_block = ""
        if avoid:
            avoid_block = "- Do NOT repeat or closely paraphrase any of these existing prompts:\n" + "\n".join(
                "  * " + re.sub(r"\s+", " ", p)[:160] for p in avoid) + "\n"
        instr = BATCH_INSTRUCTIONS.format(batch_no=batch_no, batch_total=batch_total, count=count, done=done, n=ask,
                                          mix_hint=batch_mix_hint(prompt_text, ask, batch_no),
                                          seed=f"{batch_no}-{rng.randint(1000, 9999)}", avoid_block=avoid_block)
        user_msg = "=== PROMPT ===\n" + prompt_text + "\n\n"
        label = None
        passage = None
        if grounded:
            label = "; ".join(c["label"] for c in visit)
            passage = "\n\n".join(f"[{c['label']}]\n{c['text']}" for c in visit)
            user_msg += "=== SOURCE PASSAGE ===\n" + passage + "\n=== END SOURCE PASSAGE ===\n\n"
            instr += "\n" + GROUNDING_RULES
            extra = []
            for c in visit:
                r = roles.get(c["doc"], "supporting")
                if r == "background":
                    extra.append(f"- [{c['label']}] is BACKGROUND material. Use it only for facts that relate to the PROMPT's "
                                 f"program and its users; skip unrelated facts (statistics, other programs, lists that do not "
                                 f"matter to them).")
                elif r == "supporting" and any(roles.get(x["doc"]) == "main" for x in visit):
                    extra.append(f"- [{c['label']}] is SUPPORTING material: use the facts in it that relate to the PROMPT's program.")
            if len(visit) > 1:
                extra.append(f"- This batch's own section is [{visit[0]['label']}]: build most items on it. The other section "
                             f"is extra context (use it to connect facts, not as the main subject).")
            if extra:
                instr += "\n" + "\n".join(extra)
        user_msg += instr
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_msg}]
        try:
            r = mc.teacher_chat(teacher, messages, temperature=0.8, num_predict=min(32000, 700 * ask + 500),
                                num_ctx=32768, timeout=3600)
        except mc.StopRequested:
            raise
        except Exception as e:  # noqa: BLE001
            consecutive_bad += 1
            mc.log(f"Batch {batch_no}: teacher call failed ({e}); retry {consecutive_bad}/5")
            if consecutive_bad >= 5:
                raise SystemExit(f"Teacher failed 5 times in a row: {e}")
            batch_no -= 1
            time.sleep(min(60, 5 * consecutive_bad))
            continue
        batches_run += 1
        checker.s["attempts"] += 1
        checker.s["gen_sec"] += r["_wall_sec"]
        stats["calls"] += 1
        stats["gen_tokens"] += r.get("eval_count", 0)
        stats["gen_sec"] += r.get("eval_duration", 0) / 1e9
        stats["wall_sec"] += r["_wall_sec"]
        content = (r.get("message") or {}).get("content", "")
        got, bad = extract_items(content)
        if grounded:  # the student never sees the passage, so drop items that refer to it
            kept = [it for it in got if not PASSAGE_REF.search(it["prompt"])]
            if len(kept) < len(got):
                mc.log(f"Batch {batch_no}: dropped {len(got) - len(kept)} item(s) that mention the passage")
            bad += len(got) - len(kept)
            got = kept
        stats["rejected"] += bad
        n_valid = len(got)
        # exact / near-exact repeats (against kept items and within the batch) before spending check calls
        fresh, near_exact = [], 0
        bidx_sets: list[frozenset] = []
        bstems: set[str] = set()
        for it in got:
            key = mc.norm_prompt(it["prompt"])
            sk = stem_key(it["prompt"])
            if key in seen or sk in stems or sk in bstems:
                stats["dupes"] += 1
                continue
            sh = shingles(it["prompt"])
            j, _ = index.best(sh)
            jb = max((len(sh & o) / len(sh | o) for o in bidx_sets if sh or o), default=0.0)
            if max(j, jb) >= NEAR_EXACT_J:
                near_exact += 1
                continue
            bidx_sets.append(sh)
            bstems.add(sk)
            fresh.append(it)
        if near_exact:
            checker.s["near_exact_dropped"] = checker.s.get("near_exact_dropped", 0) + near_exact
            stats["dupes"] += near_exact
        fresh = fresh[:max(0, count - done)]
        # answer check: the Teacher re-solves each item without seeing its completion
        t_chk = time.time()
        meta = {"batch": batch_no, **({"source": label} if grounded else {})}
        verified = checker.check_batch(fresh, passage, meta)
        chk_sec = time.time() - t_chk
        dropped_here = len(fresh) - len(verified)
        ok_ids = {id(it) for it in verified}
        for it in fresh:
            if id(it) not in ok_ids:
                seen.add(mc.norm_prompt(it["prompt"]))
        stats["check_dropped"] += dropped_here
        added = 0
        with mc.critical():
            new_src = []
            with open(out_path, "a", encoding="utf-8") as f:
                for it in verified:
                    if done >= count:
                        break
                    seen.add(mc.norm_prompt(it["prompt"]))
                    stems.add(stem_key(it["prompt"]))
                    index.add(shingles(it["prompt"]))
                    items.append(it)
                    f.write(json.dumps(it, ensure_ascii=False) + "\n")
                    if grounded:
                        new_src.append({"i": done, "hash": ds.prompt_hash(it["prompt"]),
                                        "chunks": [c["id"] for c in visit], "label": label, "doc": visit[0]["doc"]})
                        doc_counts[visit[0]["doc"]] = doc_counts.get(visit[0]["doc"], 0) + 1
                    done += 1
                    added += 1
                f.flush()
                os.fsync(f.fileno())
            if grounded:
                with open(src_path, "a", encoding="utf-8") as f:
                    for o in new_src:
                        f.write(json.dumps(o, ensure_ascii=False) + "\n")
                sources.extend(new_src)
                gstate["cursor"] = int(gstate.get("cursor", 0)) + 1  # a paired section keeps its own turn
                gstate["visits"] = int(gstate.get("visits", 0)) + 1
                mc.write_json_atomic(state_path, gstate)
            checker.save()
        stats["accepted"] += added
        tps = r.get("eval_count", 0) / max(1e-6, r.get("eval_duration", 1) / 1e9)
        mc.log(f"Batch {batch_no}: +{added} kept (asked {ask}, format-rejected {bad}, repeats "
               f"{len(got) - len(fresh)}, answer check dropped {dropped_here}/{len(fresh)}) "
               f"gen {r['_wall_sec']:.0f}s @ {tps:.1f} tok/s + check {chk_sec:.0f}s -> {done}/{count}"
               + (f"  [source: {label}]" if grounded else ""))
        # "weak" = the Teacher is not following the output format (answer-check drops are handled by the cap)
        if n_valid < max(1, n // 2):
            consecutive_bad += 1
            mc.log(f"Batch {batch_no} was weak ({n_valid}/{n} valid lines); will retry ({consecutive_bad}/6)")
            if consecutive_bad >= 6:
                raise SystemExit("Six weak batches in a row: the teacher is not following the output format. "
                                 "Check that the PROMPT asks for {\"prompt\",\"completion\"} JSONL lines.")
        else:
            consecutive_bad = 0
        if prog:
            if grounded:
                prog.set_extra(doc_counts=doc_counts)
            prog.update(done, force=True, check=checker.summary(count, done),
                        **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in stats.items()})
    summary = checker.summary(count, done)
    if grounded:
        summary["doc_counts"] = doc_counts
        summary["doc_roles"] = roles
    if checker.s["cap_hit"] and done < count:
        warn = (f"Stopped at {done} of {count} questions: the answer check dropped {summary['dropped']} of "
                f"{summary['checked']} items ({100 * (summary['drop_rate'] or 0):.0f}%) and the attempt cap "
                f"({max_attempts} batches, about {ATTEMPT_FACTOR}x the plan) was reached. The Teacher often disagrees "
                f"with its own answers for this PROMPT: make the questions clearer or less ambiguous, or check "
                f"rejected.jsonl to see why items were dropped.")
        mc.log("WARNING: " + warn)
        summary["warning"] = warn
    # variety scan over the whole set
    variety = variety_stats([it["prompt"] for it in items])
    mc.write_json_atomic(os.path.join(out_dir, "variety.json"), variety)
    mc.log(f"Variety: {variety['near_duplicates']}/{variety['items']} near-duplicate prompts "
           f"({100 * variety['rate']:.1f}%, {variety['method']} >= {variety['threshold']})"
           + ("  WARNING: above 5%" if variety["warn"] else ""))
    mc.write_json_atomic(os.path.join(out_dir, "check.json"), summary)
    mc.log(f"Answer check: {summary['checked']} checked, {summary['dropped']} dropped "
           f"({100 * (summary['drop_rate'] or 0):.1f}%), {summary['replaced']} replaced; reasons {summary['reasons']}; "
           f"refusals kept {summary['refusals_kept']} of {summary['refusals_checked']}, kept after support check "
           f"{summary['support_kept']}; check time {summary['check_sec']:.0f}s, generation time {summary['gen_sec']:.0f}s")
    if grounded:
        mc.log("Questions per document: " + "; ".join(f"{k} ({roles.get(k, 'supporting')}): {v}"
                                                        for k, v in sorted(doc_counts.items(), key=lambda x: -x[1])))
    if prog:
        prog.update(done, force=True, check=summary,
                    variety={k: v for k, v in variety.items() if k != "examples"} | {"examples": variety["examples"][:3]})
        prog.finish_phase()
    if grounded:
        covered = {i for s_ in sources for i in (s_.get("chunks") or [])}
        mc.log(f"Sources: {len(covered)}/{len(chunks)} document sections used; per-item sources in {src_path}")
        if prog:
            prog.set_extra(chunks_covered=len(covered))
    mc.log(f"Done: {done}/{count} items in {out_path} ({time.time() - t_start:.0f}s this run)")
    stats["check"] = summary
    stats["variety"] = {k: v for k, v in variety.items() if k != "examples"}
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-id")
    ap.add_argument("--prompt-file")
    ap.add_argument("--output")
    ap.add_argument("--count", type=int)
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--teacher")
    ap.add_argument("--max-batches", type=int)
    ap.add_argument("--docs", nargs="*", help="source documents (standalone mode)")
    args = ap.parse_args()
    if args.job_id:
        meta = mc.job_meta(args.job_id)
        p = meta.get("params", {})
        prompt_file = meta["inputFile"]
        out = os.path.join(mc.results_dir(args.job_id), "output.jsonl")
        count = int(p.get("COUNT", 3000))
        bs = int(args.batch_size or p.get("BATCH_SIZE", 0) or mc.setting("GEN_BATCH_SIZE", "20"))
        docs = [d for d in (meta.get("docs") or []) if isinstance(d, dict) and d.get("file")]
        mc.run_main(args.job_id, lambda: run(prompt_file, out, count, bs, args.job_id, args.teacher, args.max_batches,
                                             docs=docs))
    else:
        docs = [{"file": p, "name": os.path.basename(p)} for p in (args.docs or [])]
        stats = run(args.prompt_file, args.output, args.count, args.batch_size or 20, None, args.teacher, args.max_batches,
                    docs=docs)
        print(json.dumps(stats))


if __name__ == "__main__":
    main()
