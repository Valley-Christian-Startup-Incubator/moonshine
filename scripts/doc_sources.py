#!/usr/bin/env python3
"""Source documents for grounded question generation (and grounded grading).

- extract_pages(path): [(page_no, text)] from a PDF (pdftotext, else pypdf / PyMuPDF) or .txt/.md.
  Tables in PDFs are kept whole: with pdfplumber installed, every real table on a page is rebuilt row by
  row (multi-line cells, merged "Colorado / Christian" cells and blank-down department cells are joined)
  and rendered as a markdown table ("| Course | College | Code | Units |"); the rest of that page is the
  text outside the tables. Without pdfplumber, `pdftotext -layout` keeps each table row on its own line.
- chunking never splits a table row across sections, and a table that must be split repeats its header
  row(s) at the top of every section.
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
    """Word count; table borders ("|", "---|") are not words."""
    return sum(1 for t in s.split() if t.strip("|-"))


# ---------------------------------------------------------------- extraction
def _pdftotext_bin() -> str | None:
    return shutil.which("pdftotext") or next(
        (p for p in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext") if os.path.exists(p)), None)


def _pdftotext_pages(exe: str, path: str, layout: bool) -> list[tuple[int, str]]:
    args = [exe, "-enc", "UTF-8"] + (["-layout"] if layout else []) + [path, "-"]
    out = subprocess.run(args, capture_output=True, timeout=600, check=True).stdout
    pages = out.decode("utf-8", errors="replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return [(i + 1, t) for i, t in enumerate(pages)]


# ---------------------------------------------------------------- tables
TABLE_MARK = "|"   # markdown table lines start with "|"


def _cell(v) -> str | None:
    if v is None:
        return None
    s = str(v).replace("\r", "")
    s = re.sub(r"/\s*\n\s*", "/", s)        # "BIO 201/\n211 LAB" -> "BIO 201/211 LAB"
    s = re.sub(r"\s*\n\s*", " ", s)
    s = re.sub(r"\s+", " ", s).replace("|", "/").strip()
    return s


def _clean_table(rows: list[list]) -> list[list[str]] | None:
    """Rebuild a pdfplumber table: join continuation rows and split columns, fill blank-down cells.

    pdfplumber marks cells covered by a merged cell as None and truly empty cells as "". A row with at most one
    filled cell (e.g. [None, None, None, None, "Christian", None, ...]) is the second line of a merged cell in the
    row above, so it is appended to that row. Columns with an empty header that never collide with a neighbour are
    folded into that neighbour ("DUAL CREDIT COLLEGE" spread over three grid columns). Finally, None cells in the
    first columns (row-spanning cells such as the department) are filled down from the row above.
    """
    rows = [[_cell(c) for c in r] for r in rows if r]
    if not rows:
        return None
    ncol = max(len(r) for r in rows)
    rows = [r + [None] * (ncol - len(r)) for r in rows]
    merged: list[list[str | None]] = []
    for r in rows:
        filled = [i for i, c in enumerate(r) if c]
        if merged and len(filled) <= 1 and ncol >= 3:
            for i in filled:
                prev = merged[-1][i]
                merged[-1][i] = f"{prev} {r[i]}".strip() if prev else r[i]
            continue
        merged.append(list(r))
    # fold columns whose header is empty into a neighbour when they never both have text in the same row
    changed = True
    while changed and len(merged[0]) > 1:
        changed = False
        head = merged[0]
        for j in range(len(head)):
            if head[j]:
                continue
            for k in (j + 1, j - 1):
                if 0 <= k < len(head) and all(not (r[j] and r[k]) for r in merged):
                    for r in merged:
                        if r[j]:
                            r[k] = r[j]
                    for r in merged:
                        del r[j]
                    changed = True
                    break
            if changed:
                break
    # drop columns that are empty everywhere
    keep = [j for j in range(len(merged[0])) if any(r[j] for r in merged)]
    merged = [[r[j] for j in keep] for r in merged]
    if not merged or len(merged[0]) < 2:
        return None
    body = merged[1:]
    real = [r for r in body if sum(1 for c in r if c) >= 2]
    if len(real) < 2:
        return None
    # fill down row-spanning cells (None) in the leading columns only
    for j in range(len(merged[0])):
        if not any(r[j] is None for r in body):
            continue
        last = None
        for r in body:
            if r[j] is None and last is not None and sum(1 for c in r if c) >= 2:
                r[j] = last
            elif r[j]:
                last = r[j]
    return [[c or "" for c in r] for r in merged]


def _table_md(rows: list[list[str]], title: str | None = None) -> str:
    head, body = rows[0], rows[1:]
    lines = ([f"Table: {title}"] if title else [])
    lines.append("| " + " | ".join(h or "-" for h in head) + " |")
    lines.append("|" + "---|" * len(head))
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _columns_text(page) -> str:
    """Text of a (filtered) pdfplumber page, one column after another.

    Multi-column pages (school profiles, brochures) read line-by-line across the page mix the columns together.
    Gutters are vertical strips that (almost) no word crosses; the page is cut there and each strip is read top
    to bottom, left to right.
    """
    words_ = page.extract_words()
    if not words_:
        return ""
    width = int(page.width) + 1
    cover = [0] * width
    for w in words_:
        for x in range(max(0, int(w["x0"])), min(width, int(w["x1"]) + 1)):
            cover[x] += 1
    lo, hi = int(width * 0.05), int(width * 0.95)
    cuts, run = [], 0
    for x in range(lo, hi):
        if cover[x] <= 1:
            run += 1
        else:
            if run >= 8:
                cuts.append(x - run // 2)
            run = 0
    if not cuts:
        return page.extract_text() or ""
    edges = [page.bbox[0]] + [page.bbox[0] + c for c in cuts] + [page.bbox[2]]
    parts = []
    for a, b in zip(edges, edges[1:]):
        try:
            t = page.crop((a, page.bbox[1], b, page.bbox[3])).extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        if t.strip():
            parts.append(t.strip())
    return "\n\n".join(parts)


def _plumber_pages(path: str) -> list[tuple[int, str]] | None:
    """Pages with real tables rebuilt as markdown; None when pdfplumber is unavailable or finds no table."""
    try:
        import logging
        import pdfplumber  # type: ignore
        logging.getLogger("pdfminer").setLevel(logging.ERROR)
    except ImportError:
        return None
    out, any_table = [], False
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            try:
                found = page.find_tables()
            except Exception:  # noqa: BLE001
                found = []
            tables = []
            for t in found:
                rows = _clean_table(t.extract())
                if rows:
                    tables.append((t.bbox, rows))
            if not tables:
                out.append((i + 1, None))  # filled from pdftotext below (better reading order for prose)
                continue
            any_table = True
            boxes = [b for b, _ in tables]

            def outside(obj, boxes=boxes):
                x = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
                y = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
                return not any(b[0] - 1 <= x <= b[2] + 1 and b[1] - 1 <= y <= b[3] + 1 for b in boxes)

            # text outside the tables, read column by column; tables go after it, in page order
            ordered = [_table_md(rows) for _, rows in sorted(tables, key=lambda x: x[0][1])]
            fp = page.filter(outside)
            n_out = len(fp.extract_words())
            if n_out >= 150:  # a real page of prose around the tables: read it column by column
                try:
                    rest = _columns_text(fp)
                except Exception:  # noqa: BLE001
                    rest = fp.extract_text() or ""
                parts = [rest.strip()] + ordered
            else:  # a table page with a title and footnotes: keep them above / below the tables
                first_top = min(b[1] for b in boxes)
                above, below = [], []
                try:
                    for ln in fp.extract_text_lines():
                        (above if ln.get("top", 0) < first_top else below).append(ln.get("text", ""))
                except Exception:  # noqa: BLE001
                    below = (fp.extract_text() or "").splitlines()
                parts = ["\n".join(above).strip()] + ordered + ["\n".join(below).strip()]
            out.append((i + 1, "\n\n".join(p for p in parts if p)))
    return out if any_table else None


def _pdf_pages(path: str) -> tuple[list[tuple[int, str]], str]:
    exe = _pdftotext_bin()
    plumber = None
    try:
        plumber = _plumber_pages(path)
    except Exception as e:  # noqa: BLE001
        mc.log(f"pdfplumber could not read tables in {os.path.basename(path)} ({e}); using pdftotext")
    if plumber is not None:
        plain = None
        if exe and any(t is None for _, t in plumber):
            try:
                plain = dict(_pdftotext_pages(exe, path, layout=False))
            except Exception:  # noqa: BLE001
                plain = None
        pages = []
        for no, t in plumber:
            if t is None:
                t = (plain or {}).get(no)
                if t is None:
                    import pdfplumber  # type: ignore
                    with pdfplumber.open(path) as pdf:
                        t = pdf.pages[no - 1].extract_text() or ""
            pages.append((no, t))
        return pages, "pdfplumber tables + pdftotext"
    if exe:
        try:
            pages = _pdftotext_pages(exe, path, layout=False)
            if _looks_tabular(pages):  # no pdfplumber: keep table rows on one line each
                pages = _pdftotext_pages(exe, path, layout=True)
                return [(no, _layout_to_rows(t)) for no, t in pages], "pdftotext -layout"
            return pages, "pdftotext"
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


def _looks_tabular(pages: list[tuple[int, str]]) -> bool:
    """Plain pdftotext output of a table: many very short lines (one cell per line)."""
    lines = [ln.strip() for _, t in pages for ln in t.splitlines() if ln.strip()]
    return len(lines) >= 30 and sum(1 for ln in lines if len(ln.split()) <= 3) / len(lines) > 0.6


def _layout_to_rows(text: str) -> str:
    """pdftotext -layout: columns separated by 3+ spaces become " | " so the whitespace cleanup keeps them apart."""
    return "\n".join(re.sub(r"\s{3,}", " | ", ln.strip()) for ln in text.splitlines())


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
            if _is_table(para):
                yield no, None, "\n".join(ln.strip() for ln in para.split("\n") if ln.strip())
                continue
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


def _is_table(text: str) -> bool:
    lines = text.split("\n")
    return sum(1 for ln in lines if ln.startswith(TABLE_MARK)) >= 3


def _split_table(text: str, limit: int) -> list[str]:
    """Split a markdown table between rows; every piece repeats the title and header rows."""
    lines = text.split("\n")
    pre = [ln for ln in lines[:1] if not ln.startswith(TABLE_MARK)]
    tl = [ln for ln in lines if ln.startswith(TABLE_MARK)]
    head, rows = tl[:2], tl[2:]
    head_w = words("\n".join(pre + head))
    pieces, cur, cw = [], [], head_w
    for r in rows:
        w = words(r)
        if cur and cw + w > limit:
            pieces.append("\n".join(pre + head + cur))
            cur, cw = [], head_w
        cur.append(r)
        cw += w
    if cur:
        pieces.append("\n".join(pre + head + cur))
    return pieces or [text]


def _split_long(text: str, limit: int) -> list[str]:
    if _is_table(text):
        return _split_table(text, limit)
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
ALL_DOCS_MAX_WORDS = 9000  # uploaded documents up to this size can be shown whole to a checker/grader


def all_documents_text(chunks: list[dict], max_words: int = ALL_DOCS_MAX_WORDS) -> str | None:
    """Every section of a job's documents, grouped by document, or None when they are too long to show at once."""
    if not chunks or sum(int(c.get("words") or 0) for c in chunks) > max_words:
        return None
    by_doc: dict[str, list[str]] = {}
    for c in chunks:
        by_doc.setdefault(c["doc"], []).append(c["text"])
    return "\n\n".join(f"[{d}]\n" + "\n\n".join(t) for d, t in by_doc.items())


def lookup_sources(prompts: list[str], exclude_job: str | None = None, max_chars: int = 12000) -> tuple[list[str | None], dict]:
    """For each prompt, return the source passage text (or None) from any grounded generate job.

    info["all_docs"][i] is the whole document set of that job (all_documents_text) or None: graders fall back to it
    so an answer that uses a fact from another uploaded document is not marked wrong."""
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
    whole: dict[str, str | None] = {}
    out: list[str | None] = [None] * len(prompts)
    all_docs: list[str | None] = [None] * len(prompts)
    for h, (d, ids) in found.items():
        if d not in cache:
            cache[d] = read_chunks(os.path.join(mc.RESULTS_DIR, d, "chunks.jsonl"))
            whole[d] = all_documents_text(cache[d])
        cs = cache[d]
        parts = [f"[{cs[i]['label']}]\n{cs[i]['text']}" for i in ids if 0 <= i < len(cs)]
        if parts:
            out[want[h]] = "\n\n".join(parts)[:max_chars]
            all_docs[want[h]] = whole[d]
            jobs_used[d] += 1
    return out, {"matched": sum(1 for x in out if x), "total": len(prompts), "jobs": dict(jobs_used),
                 "all_docs": all_docs, "all_docs_matched": sum(1 for x in all_docs if x)}


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
