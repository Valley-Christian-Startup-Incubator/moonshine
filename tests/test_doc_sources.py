import importlib.util
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


doc_sources = _load("doc_sources")
gen_questions = _load("gen_questions")


def test_clean_table_merges_continuations_and_fills_down():
    rows = [
        ["Department", "Course", "Units"],
        ["Science", "Forensic Science", "3"],
        [None, "(Carner & Wilson only)", None],
        [None, "Biology", "4"],
    ]
    t = doc_sources._clean_table(rows)
    assert t[0] == ["Department", "Course", "Units"]
    assert ["Science", "Forensic Science (Carner & Wilson only)", "3"] in t
    assert ["Science", "Biology", "4"] in t


def test_split_table_keeps_rows_whole_and_repeats_header():
    rows = [["Course", "College", "Units"]] + [[f"Course number {i} with a long name", "Corban", "3"] for i in range(60)]
    md = doc_sources._table_md(rows, "Offerings")
    pieces = doc_sources._split_table(md, 120)
    assert len(pieces) > 1
    seen = []
    for p in pieces:
        lines = p.split("\n")
        assert "Course | College | Units" in "\n".join(lines[:3])
        seen += [ln for ln in lines if "long name" in ln]
    assert len(seen) == 60  # no row lost or split


def test_refusal_detection():
    assert gen_questions.is_refusal("Final Answer: I don't have that information; please ask your counselor.")
    assert gen_questions.is_refusal("That is not listed. Contact the registrar's office.")
    assert not gen_questions.is_refusal("Final Answer: CJU 273 for 3 units.")


def test_chunk_schedule_follows_role_weights():
    chunks = [{"doc": "main.pdf"}] * 2 + [{"doc": "sup.pdf"}] * 2 + [{"doc": "bg.pdf"}] * 2
    roles = {"main.pdf": "main", "sup.pdf": "supporting", "bg.pdf": "background"}
    order = gen_questions.chunk_schedule(chunks, roles, length=4300)
    c = Counter(chunks[i]["doc"] for i in order)
    assert abs(c["main.pdf"] / c["sup.pdf"] - 3) < 0.1
    assert abs(c["bg.pdf"] / c["sup.pdf"] - 0.3) < 0.05
    assert gen_questions.doc_role("MAIN") == "main" and gen_questions.doc_role("junk") == "supporting"


class _FakeChecker(gen_questions.AnswerChecker):
    def __init__(self, verdicts):
        self.s = {"judge_calls": 0}
        self.all_docs = "docs"
        self.verdicts = list(verdicts)

    def _verdict(self, system, prompt, answer, passage, words_):
        return self.verdicts.pop(0)


def test_refusal_checks():
    it = {"prompt": "q"}
    ref = "I don't have that information; please ask your counselor."
    assert _FakeChecker(["KEEP"]).compare(it, ref, "NOT IN SOURCE", "p") == (True, "refusal", "")
    assert _FakeChecker(["WRONG"]).compare(it, ref, None, "p")[2] == "wrong_refusal"
    assert _FakeChecker(["UNSUPPORTED"]).compare(it, ref, None, "p")[2] == "unsupported_refusal"
    fix = "Delete the old Parchment account and make a new one; if that fails, contact the Dual Credit office."
    assert _FakeChecker(["UNSUPPORTED", "SUPPORTED"]).compare(it, fix, None, "p") == (True, "support_docs", "")
    assert _FakeChecker(["UNSUPPORTED", "UNSUPPORTED"]).compare(it, fix, None, "p")[2] == "unsupported_refusal"
    fact = "It is CJU 273 for 3 units."
    assert _FakeChecker(["UNSUPPORTED", "SUPPORTED"]).compare(it, fact, "NOT IN SOURCE", "p") == (True, "support_docs", "")


def test_all_documents_text_and_grader_fallback_message():
    chunks = [{"doc": "a.pdf", "text": "alpha", "words": 1}, {"doc": "b.pdf", "text": "beta", "words": 1}]
    t = doc_sources.all_documents_text(chunks)
    assert "[a.pdf]" in t and "[b.pdf]" in t and "beta" in t
    assert doc_sources.all_documents_text([{"doc": "a", "text": "x", "words": 99999}]) is None
    train_eval = _load("train_eval")
    row = {"prompt": "q", "completion": "Final Answer: x"}
    u, s, ctx = train_eval.Job.grade_message(row, "x", "SECTION")
    assert ctx == 16384 and "SOURCE PASSAGE" in u
    u, s, ctx = train_eval.Job.grade_message(row, "x", "SECTION", "ALLDOCS")
    assert ctx == 32768 and "ALLDOCS" in s and "SECTION" in u and "ANY of the documents" in s
