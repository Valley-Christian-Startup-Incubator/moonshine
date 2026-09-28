#!/usr/bin/env python3
"""Source documents for grounded question generation (and grounded grading).

- extract_pages(path): [(page_no, text)] from a PDF (pdftotext, else pypdf / PyMuPDF) or .txt/.md.
- build_chunks(docs): split documents into ~800-1,500-word sections with labels such as
  "manual.pdf p.40-41 · 6.3 Setup" or "notes.md · Safety rules".
- prepare_job_chunks(job_id, docs): writes results/<id>/chunks.jsonl + documents.json (idempotent).
- lookup_sources(prompts): match training prompts back to source sections through any
  results/*/sources.jsonl + chunks.jsonl (used by train_eval.py for grounded grading).

CLI (testing): doc_sources.py chunk FILE [FILE...]    -> prints a chunk summary
               doc_sources.py lookup TRAIN.jsonl      -> prints how many prompts match a source
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moonshine_common as mc  # noqa: E402

TARGET_WORDS = 1100   # aim for this many words per section
MIN_WORDS = 800       # prefer to break at a heading once a section has this many words
MAX_WORDS = 1500      # never grow a section past this (except one over-long paragraph)
DOC_EXTS = (".pdf", ".txt", ".md", ".markdown")


def prompt_hash(prompt: str) -> str:
    return hashlib.sha1(mc.norm_prompt(prompt).encode("utf-8")).hexdigest()[:16]


def words(s: str) -> int:
    return len(s.split())


# ---------------------------------------------------------------- extraction
def _pdftotext_bin() -> str | None:
    return shutil.which("pdftotext") or next(
        (p for p in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext") if os.path.exists(p)), None)


def _pdf_pages(path: str) -> tuple[list[tuple[int, str]], str]:
    exe = _pdftotext_bin()
    if exe:
        try:
            out = subprocess.run([exe, "-enc", "UTF-8", path, "-"], capture_output=True, timeout=600, check=True).stdout
            pages = out.decode("utf-8", errors="replace").split("\f")
            if pages and not pages[-1].strip():
                pages = pages[:-1]
            return [(i + 1, t) for i, t in enumerate(pages)], "pdftotext"
        except Exception as e:  # noqa: BLE001
            mc.log(f"pdftotext failed on {os.path.basename(path)} ({e}); trying Python PDF libraries")
    try:
        import pypdf  # type: ignore
        r = pypdf.PdfReader(path)
        return [(i + 1, p.extract_text() or "") for i, p in enumerate(r.pages)], "pypdf"
    except ImportError:
        pass
    try:
        import fitz  # type: ignore  (PyMuPDF)
        d = fitz.open(path)
        return [(i + 1, p.get_text()) for i, p in enumerate(d)], "pymupdf"
    except ImportError:
        raise SystemExit("No PDF text extractor available (install poppler's pdftotext or pypdf).")


def _strip_running_lines(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Remove running headers/footers: lines that appear at the top/bottom of several pages."""
    if len(pages) < 4:
        return pages

    def norm(line):
        return re.sub(r"\d+", "#", line.strip().lower())

    edge = Counter()
    for _, t in pages:
        lines = [ln for ln in t.splitlines() if ln.strip()]
        for ln in set(lines[:3] + lines[-5:]):
            edge[norm(ln)] += 1
    thresh = max(4, int(0.05 * len(pages)))
    running = {k for k, v in edge.items() if v >= thresh and len(k) < 120}
    out = []
    for no, t in pages:
        lines = t.splitlines()
        nonempty = [i for i, ln in enumerate(lines) if ln.strip()]
        edge_idx = set(nonempty[:3] + nonempty[-5:])
        kept = [ln for i, ln in enumerate(lines) if not (i in edge_idx and norm(ln) in running)]
        out.append((no, "\n".join(kept)))
    return out


def extract_pages(path: str) -> tuple[list[tuple[int, str]], str]:
    """Return ([(page_no, text)], method). Text files are one 'page' (page_no 0)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        pages, method = _pdf_pages(path)
        pages = _strip_running_lines(pages)
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            pages, method = [(0, f.read())], "text"
    cleaned = []
    for no, t in pages:
        t = t.replace("\r\n", "\n").replace("\r", "\n")
        # drop table-of-contents leader lines ("5.2 FIELD ........ 18")
        t = "\n".join(ln for ln in t.split("\n") if not re.search(r"\.{5,}\s*\d+\s*$", ln))
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        cleaned.append((no, t.strip()))
    return cleaned, method


# ---------------------------------------------------------------- chunking
HEADING_NUM = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-Z][^\n]{1,80})$")
HEADING_MD = re.compile(r"^#{1,4}\s+(.{2,100})$")


def _heading(line: str) -> str | None:
    s = line.strip()
    m = HEADING_MD.match(s)
    if m:
        return m.group(1).strip().strip("#").strip()
    m = HEADING_NUM.match(s)
    if m and not s.endswith((".", ",", ";", ":")) and words(s) <= 12:
        num, rest = m.group(1), m.group(2)
        alpha = [w for w in rest.split() if re.search(r"[A-Za-z]{2,}", w)]
        if "." not in num and (len(alpha) < 2 or int(num) > 40):
            return None  # "12 AWG", "40 A" etc. are table cells, not chapter headings
        if not alpha:
            return None
        return s
    return None


def _blocks(pages: list[tuple[int, str]]):
    """Yield (page_no, heading_or_None, text) paragraph blocks; headings start their own block."""
    for no, t in pages:
        for para in re.split(r"\n\s*\n", t):
            buf: list[str] = []
            for ln in para.split("\n"):
                h = _heading(ln)
                if h:
                    if buf:
                        yield no, None, "\n".join(buf)
                        buf = []
                    yield no, h, ln.strip()
                elif ln.strip():
                    buf.append(ln.strip())
            if buf:
                yield no, None, "\n".join(buf)


def _split_long(text: str, limit: int) -> list[str]:
    toks = text.split(" ")
    return [" ".join(toks[i:i + limit]) for i in range(0, len(toks), limit)]


def chunk_document(name: str, pages: list[tuple[int, str]]) -> list[dict]:
    chunks: list[dict] = []
    cur: dict = {"parts": [], "words": 0, "pages": [], "headings": []}
    last_heading: str | None = None

    def flush():
        nonlocal cur
        if cur["words"] > 0:
            chunks.append(cur)
        cur = {"parts": [], "words": 0, "pages": [], "headings": [], "carry": last_heading}

    cur["carry"] = None
    for no, h, text in _blocks(pages):
        w = words(text)
        if h and cur["words"] >= MIN_WORDS:
            flush()
        if cur["words"] + w > MAX_WORDS and cur["words"] >= MIN_WORDS // 2:
            flush()
        pieces = _split_long(text, MAX_WORDS) if w > MAX_WORDS else [text]
        for piece in pieces:
            if cur["words"] and cur["words"] + words(piece) > MAX_WORDS:
                flush()
            cur["parts"].append(piece)
            cur["words"] += words(piece)
            if no and (not cur["pages"] or cur["pages"][-1] != no):
                cur["pages"].append(no)
        if h:
            cur["headings"].append(h)
            last_heading = h
    flush()
    # merge a tiny trailing section into the previous one
    if len(chunks) >= 2 and chunks[-1]["words"] < 300 and chunks[-2]["words"] + chunks[-1]["words"] <= MAX_WORDS + 300:
        a, b = chunks[-2], chunks.pop()
        a["parts"] += b["parts"]
        a["words"] += b["words"]
        a["pages"] += [p for p in b["pages"] if p not in a["pages"]]
        a["headings"] += b["headings"]
    out = []
    for c in chunks:
        heading = c["headings"][0] if c["headings"] else c.get("carry")
        if c["pages"]:
            p0, p1 = min(c["pages"]), max(c["pages"])
            label = f"{name} p.{p0}" + (f"-{p1}" if p1 != p0 else "")
        else:
            label = name
        if heading:
            label += f" · {heading[:70]}"
        out.append({"doc": name, "label": label, "pages": [min(c["pages"]), max(c["pages"])] if c["pages"] else None,
                    "heading": heading, "words": c["words"], "text": "\n\n".join(c["parts"])})
    return out


def build_chunks(docs: list[dict]) -> tuple[list[dict], list[dict]]:
    """docs: [{"file": path, "name": display name}] -> (chunks, per-document summary)."""
    chunks, summary = [], []
    for d in docs:
        name = d.get("name") or os.path.basename(d["file"])
        try:
            pages, method = extract_pages(d["file"])
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            mc.log(f"Could not read {name}: {e}")
            summary.append({"name": name, "error": str(e)[:300], "chunks": 0, "words": 0})
            continue
        total = sum(words(t) for _, t in pages)
        if total < 50:
            mc.log(f"{name}: almost no text could be extracted ({total} words); is it a scanned PDF? Skipping.")
            summary.append({"name": name, "error": "no extractable text (scanned PDF?)", "chunks": 0, "words": total,
                            "pages": len(pages) if method != "text" else None, "method": method})
            continue
        cs = chunk_document(name, pages)
        for c in cs:
            c["id"] = len(chunks)
            chunks.append(c)
        summary.append({"name": name, "pages": len(pages) if method != "text" else None, "words": total,
                        "chunks": len(cs), "method": method})
        mc.log(f"Source document {name}: {total:,} words via {method} -> {len(cs)} sections")
    return chunks, summary


def read_chunks(path: str) -> list[dict]:
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def prepare_job_chunks(job_id: str, docs: list[dict]) -> list[dict]:
    """Extract + chunk once per job; later runs (resume) reuse results/<id>/chunks.jsonl."""
    rd = mc.results_dir(job_id)
    path = os.path.join(rd, "chunks.jsonl")
    chunks = read_chunks(path)
    if chunks:
        return chunks
    chunks, summary = build_chunks(docs)
    if not chunks:
        raise SystemExit("None of the source documents contained readable text (scanned PDFs need OCR first).")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    mc.write_json_atomic(os.path.join(rd, "documents.json"),
                         {"docs": summary, "chunks": len(chunks), "words": sum(c["words"] for c in chunks),
                          "created_at": mc.now_iso()})
    return chunks


# ---------------------------------------------------------------- lookup for grading
def lookup_sources(prompts: list[str], exclude_job: str | None = None, max_chars: int = 12000) -> tuple[list[str | None], dict]:
    """For each prompt, return the source passage text (or None) from any grounded generate job."""
    want = {prompt_hash(p): i for i, p in enumerate(prompts)}
    found: dict[str, tuple[str, list[int]]] = {}
    jobs_used = Counter()
    try:
        dirs = sorted(os.listdir(mc.RESULTS_DIR),
                      key=lambda d: -os.path.getmtime(os.path.join(mc.RESULTS_DIR, d)))
    except OSError:
        dirs = []
    for d in dirs:
        if d == exclude_job or len(found) == len(want):
            continue
        sp = os.path.join(mc.RESULTS_DIR, d, "sources.jsonl")
        if not (os.path.exists(sp) and os.path.exists(os.path.join(mc.RESULTS_DIR, d, "chunks.jsonl"))):
            continue
        try:
            with open(sp, encoding="utf-8") as f:
                for line in f:
                    try:
                        o = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    h = o.get("hash")
                    if h in want and h not in found and o.get("chunks"):
                        found[h] = (d, [int(x) for x in o["chunks"]])
        except OSError:
            continue
    cache: dict[str, list[dict]] = {}
    out: list[str | None] = [None] * len(prompts)
    for h, (d, ids) in found.items():
        if d not in cache:
            cache[d] = read_chunks(os.path.join(mc.RESULTS_DIR, d, "chunks.jsonl"))
        cs = cache[d]
        parts = [f"[{cs[i]['label']}]\n{cs[i]['text']}" for i in ids if 0 <= i < len(cs)]
        if parts:
            out[want[h]] = "\n\n".join(parts)[:max_chars]
            jobs_used[d] += 1
    return out, {"matched": sum(1 for x in out if x), "total": len(prompts), "jobs": dict(jobs_used)}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "chunk":
        chunks, summary = build_chunks([{"file": p, "name": os.path.basename(p)} for p in sys.argv[2:]])
        for c in chunks:
            print(f"{c['id']:3d} {c['words']:5d}w  {c['label']}")
        print(json.dumps(summary, indent=1))
    elif len(sys.argv) >= 3 and sys.argv[1] == "lookup":
        rows = mc.read_pairs(sys.argv[2])
        passages, info = lookup_sources([r["prompt"] for r in rows])
        print(json.dumps(info))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
