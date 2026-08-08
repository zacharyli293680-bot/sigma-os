#!/usr/bin/env python3
"""
intake.py — study intake (dashboard-plan Phase 6).

Drop course material into `00-Inbox/intake/<COURSE>/`, run `sigma intake`, and
get back notes filed to that course, shaped by the vault contract.

    00-Inbox/intake/CSE-311/lecture-04-induction.pdf
        -> 02-Areas/Academics/CSE-311/lectures/strong-induction.md
           02-Areas/Academics/CSE-311/lectures/structural-induction.md

**Why this is not `tools/convert_pdfs.py`.** That converter already existed and
already did the mechanical half well: markitdown/pdftotext extraction, the right
frontmatter block, the right destination folder, the PDF kept in Attachments. It
is imported here rather than reimplemented. What it cannot do is the half the
vision actually asked for — "notes you can actually revise from". Its output is
the raw text dump with a heading on top, `number:` and `date:` left blank, no
links to anything, and one note per PDF however many separate ideas the PDF held.
This module adds the comprehension layer on top of that extraction.

**Why a drop folder rather than a path argument.** The palette is a fixed verb
list whose security property is that a verb is a dictionary key and nothing
user-supplied ever reaches a command line (dashboard-plan Phase 3). An `intake
--source <whatever>` verb would be the first exception to that, so there is no
path argument: the source is a known directory, and the only thing that varies
is what the human put in it.

**Why the course comes from the folder name.** The contract's rule for filing is
"if unsure where it goes, put it in `00-Inbox/` and flag it — do not invent a
location". A subfolder that does not match a real course folder is skipped and
reported, never guessed at.

**Writes.** None, directly. The model raises `propose_change` proposals exactly
as the fleet's specialists do, and then the same `applier.py` lands each one as
its own revertible commit in the activity ledger. Study intake gets no write
primitive of its own, because the funnel being singular is the property that
makes it trustworthy.
"""
import argparse
import asyncio
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import reflect as rf                    # noqa: E402  (owns VAULT and the proposal dir)
import specialists as sp                # noqa: E402
from sigma import make_logger           # noqa: E402

VAULT = rf.VAULT
DROP = VAULT / "00-Inbox" / "intake"
ATTACH = VAULT / "99-Meta" / "Attachments"
COURSES_ROOT = VAULT / "02-Areas" / "Academics"

SUPPORTED = {".pdf", ".pptx", ".md", ".txt"}

# One document's extracted text, capped. A 200-page textbook would otherwise
# blow the window on a single call and fail the whole run. Truncation is
# reported, never silent — the note that comes back says it saw a prefix.
#
# The cap is per *pass*, not per document: `--continue` reads the next
# TEXT_BUDGET characters of a source that ran past it, so a long deck ends up
# fully covered without any single call carrying it all.
TEXT_BUDGET = 60_000

# How much of each source has been read, keyed "<COURSE>/<filename>".
# `.state.json` so .gitignore's existing `runtime/*.state.json` rule covers it —
# this names course files and is machine-local operating state, not a record.
# The notes themselves are the record; losing this costs a re-read, not work.
COVERAGE_PATH = _HERE / "intake.state.json"

# Generous: reading a slide deck and drafting several linked notes is more work
# than any scheduled specialist does in one turn.
TIMEOUT_S = 900

log = make_logger(_HERE / "intake.log", "intake", stream=sys.stdout)


# --------------------------------------------------------------------------
# what is waiting
# --------------------------------------------------------------------------

def _privacy():
    """The model-boundary guard, borrowed from the interface rather than copied.

    Intake is the one place in the system where *script code* reads a file and
    puts its contents straight into a prompt. The agent never calls Read on the
    source, so `privacy.py`'s PreToolUse hook — which guards tool calls — would
    never fire, and a sealed PDF would reach a model through a side door that
    every existing check is blind to.

    `00-Inbox/` is not gitignored today, so nothing is refused right now. But
    gitignoring a folder of lecture PDFs to keep them out of the repo is an
    entirely reasonable thing to do later, and it must not silently turn intake
    into the leak. The check costs one cached subprocess call per file.
    """
    backend = _HERE.parent / "interface" / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from privacy import VaultPrivacy
    return VaultPrivacy(VAULT)


def known_courses() -> dict:
    """lowercased code -> the real folder name, so `cse-311/` files correctly."""
    try:
        return {p.name.lower(): p.name for p in COURSES_ROOT.iterdir() if p.is_dir()}
    except OSError:
        return {}


def scan() -> tuple:
    """(items, problems). An item is (path, course). Problems are named, not
    silently dropped — a PDF that sat in the drop folder doing nothing because
    its folder was misspelled is exactly the failure this reports."""
    items, problems = [], []
    if not DROP.exists():
        return items, problems
    courses = known_courses()
    try:
        guard = _privacy()
    except Exception as e:
        # Fail closed, like every other reading of this boundary: if the guard
        # cannot be loaded, intake reads nothing rather than reading blind.
        problems.append((DROP.name + "/", f"privacy guard unavailable ({e}) — "
                                          f"refusing to read anything"))
        return [], problems
    for child in sorted(DROP.iterdir()):
        if child.is_file():
            if child.suffix.lower() in SUPPORTED:
                problems.append((child.name, "sits at the top level — put it in "
                                             "a <COURSE>/ subfolder"))
            continue
        real = courses.get(child.name.lower())
        if not real:
            problems.append((child.name + "/", "no such course folder in "
                                               "02-Areas/Academics/"))
            continue
        for f in sorted(child.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower() not in SUPPORTED:
                problems.append((f"{child.name}/{f.name}",
                                 f"unsupported type {f.suffix or '(none)'}"))
                continue
            why = guard.verdict(str(f))
            if why:
                problems.append((f"{child.name}/{f.name}",
                                 f"refused at the model boundary: {why}"))
                continue
            items.append((f, real))
    return items, problems


# --------------------------------------------------------------------------
# reading the source
# --------------------------------------------------------------------------

def extractor_name() -> str:
    """Which extractor this process will actually get for PDFs.

    `markitdown` lives in the machine-local `~/.obsidian-tools/venv` that
    `tools/convert.cmd` uses, not in the interface venv intake runs on — so
    intake silently gets the `pdftotext -layout` fallback. That is good enough
    for text PDFs and is why this is not a bug, but note quality tracks
    extraction quality, and a mysterious drop in one is worth being able to
    explain by the other. So the run says which one it used.
    """
    import importlib.util
    return "markitdown" if importlib.util.find_spec("markitdown") else "pdftotext -layout"


# Equations in a deck are OMML, not text. PowerPoint stores them as
# `<a14:m><m:oMath>` inline inside the paragraph, and python-pptx's `.text`
# walks `a:t` runs only — so every equation comes back as a gap. On AA-210's
# 49 statics decks that is 4,710 equations, and three slides of the friction
# deck extract completely empty because the whole slide *is* the formula.
# Silent loss is the worst kind: the note reads as if the lecturer never
# stated `F = mu_s N`. So linearise the math instead.
# ...and the shapes holding them are invisible to python-pptx twice over:
# PowerPoint wraps any shape containing an equation in `<mc:AlternateContent>`
# (a live `mc:Choice` plus a flattened `mc:Fallback` picture for old readers),
# and `slide.shapes` skips that element rather than descending into it. So the
# walk below goes over the shape tree XML directly, taking the Choice branch
# and recursing into groups — which `slide.shapes` also declines to do.
_MATH_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_DRAW_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_PML_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_MC_NS = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def _m_part(el, name: str) -> str:
    """The linearised content of `el`'s first <m:name> child, or ""."""
    child = el.find(_MATH_NS + name)
    return _omml(child) if child is not None else ""


def _m_val(el, *path: str) -> str:
    """The m:val attribute of a nested property element, or ""."""
    node = el
    for step in path:
        node = node.find(_MATH_NS + step) if node is not None else None
    return (node.get(_MATH_NS + "val") or "") if node is not None else ""


def _omml(el) -> str:
    """One OMML element as linear text — `(a)/(b)`, `x_i`, `x^2`, `sqrt(x)`.

    Deliberately plain rather than LaTeX: the consumer is a model reading a
    slide, and `mu_s` beside its bullet is as legible as `\\mu_s` without
    inventing markup the rest of the extraction does not use. Unknown
    constructs fall through to their concatenated children, so a shape this
    does not model degrades to its text rather than to nothing.
    """
    out = []
    for child in el:
        tag = child.tag
        if not isinstance(tag, str) or not tag.startswith(_MATH_NS):
            continue                    # a:rPr and friends carry formatting only
        name = tag[len(_MATH_NS):]
        if name == "t":
            out.append(child.text or "")
        elif name == "ctrlPr":
            continue                    # run properties, no content
        elif name == "f":
            out.append(f"({_m_part(child, 'num')})/({_m_part(child, 'den')})")
        elif name == "sSub":
            out.append(f"{_m_part(child, 'e')}_{_m_part(child, 'sub')}")
        elif name == "sSup":
            out.append(f"{_m_part(child, 'e')}^{_m_part(child, 'sup')}")
        elif name == "sSubSup":
            out.append(f"{_m_part(child, 'e')}_{_m_part(child, 'sub')}"
                       f"^{_m_part(child, 'sup')}")
        elif name == "rad":
            deg = _m_part(child, "deg")
            body = _m_part(child, "e")
            out.append(f"root{deg}({body})" if deg else f"sqrt({body})")
        elif name == "d":
            beg = _m_val(child, "dPr", "begChr") or "("
            end = _m_val(child, "dPr", "endChr") or ")"
            inner = ", ".join(_omml(e) for e in child.findall(_MATH_NS + "e"))
            out.append(f"{beg}{inner}{end}")
        elif name == "nary":
            op = _m_val(child, "naryPr", "chr") or "∑"
            lo, hi = _m_part(child, "sub"), _m_part(child, "sup")
            span = (f"_{lo}" if lo else "") + (f"^{hi}" if hi else "")
            out.append(f"{op}{span} {_m_part(child, 'e')}")
        elif name == "func":
            out.append(f"{_m_part(child, 'fName')}({_m_part(child, 'e')})")
        elif name in ("limLow", "limUpp"):
            joint = "_" if name == "limLow" else "^"
            out.append(f"{_m_part(child, 'e')}{joint}{_m_part(child, 'lim')}")
        elif name == "acc":
            out.append(_m_part(child, "e"))     # the arrow/hat is a glyph, not text
        elif name == "bar":
            out.append(f"bar({_m_part(child, 'e')})")
        elif name == "m":
            rows = [" | ".join(_omml(e) for e in r.findall(_MATH_NS + "e"))
                    for r in child.findall(_MATH_NS + "mr")]
            out.append("[" + "; ".join(rows) + "]")
        else:
            out.append(_omml(child))
    return "".join(out)


def _para_text(p) -> str:
    """One <a:p> as text, with its inline equations in place.

    Document order matters: "it is NOT true that F = mu_s N" is one sentence
    split across an `a:r` and an `a14:m`, and appending the math after the
    paragraph would reverse the lecturer's point on the slides that negate a
    formula.
    """
    import unicodedata
    parts = []
    for node in p.iter():
        tag = node.tag
        if not isinstance(tag, str):
            continue
        if tag == _DRAW_NS + "t":
            # An a:t inside m:r is math, already claimed by the oMath below it.
            parts.append(node.text or "")
        elif tag == _MATH_NS + "oMath":
            parts.append(unicodedata.normalize("NFKC", _omml(node)))
    return "".join(parts)


def _frame_text(frame) -> str:
    """A text frame's paragraphs, math included, one per line."""
    try:
        paras = [_para_text(p) for p in frame._txBody.findall(_DRAW_NS + "p")]
    except Exception:
        return (frame.text or "").strip()       # any surprise: the old behaviour
    return "\n".join(paras).strip()


def _body_text(el) -> str:
    """Every <a:p> under `el`, one per line — el is a txBody or a table cell."""
    return "\n".join(_para_text(p) for p in el.iter(_DRAW_NS + "p")).strip()


def _walk_shapes(el):
    """Shape elements under `el`, in document order, through the wrappers.

    Yields `p:sp` and `p:graphicFrame` only — the two that carry text — after
    unwrapping `mc:AlternateContent` and flattening groups.
    """
    for child in el:
        tag = child.tag
        if not isinstance(tag, str):
            continue                            # comments and PIs
        if tag == _MC_NS + "AlternateContent":
            branch = child.find(_MC_NS + "Choice")
            if branch is None:
                branch = child.find(_MC_NS + "Fallback")
            if branch is not None:
                yield from _walk_shapes(branch)
        elif tag in (_PML_NS + "grpSp", _PML_NS + "spTree"):
            yield from _walk_shapes(child)
        elif tag in (_PML_NS + "sp", _PML_NS + "graphicFrame"):
            yield child


def _is_title(sp) -> bool:
    ph = sp.find(f"{_PML_NS}nvSpPr/{_PML_NS}nvPr/{_PML_NS}ph")
    return ph is not None and (ph.get("type") or "") in ("title", "ctrTitle")


def extract_pptx(path: Path) -> str:
    """Lecture slides, with their structure kept.

    Slides are not prose, and flattening a deck into one text blob loses the
    thing that makes it readable: which words were the title, which were the
    body, and what the lecturer wrote in the notes pane — often the only place
    a deck explains itself rather than listing bullets. So this walks the deck
    and emits per-slide markdown the model can actually reason about.

    python-pptx rather than markitdown deliberately — see requirements.txt.
    """
    from pptx import Presentation

    deck = Presentation(str(path))
    slides = []
    for n, slide in enumerate(deck.slides, 1):
        tree = slide._element.find(
            f"{_PML_NS}cSld/{_PML_NS}spTree")
        shapes = list(_walk_shapes(tree)) if tree is not None else []

        title = ""
        for sp in shapes:
            if sp.tag == _PML_NS + "sp" and _is_title(sp):
                body = sp.find(_PML_NS + "txBody")
                if body is not None:
                    title = _body_text(body)
                break

        lines, rows = [], []
        for sp in shapes:
            try:
                if sp.tag == _PML_NS + "graphicFrame":
                    for tr in sp.iter(_DRAW_NS + "tr"):
                        cells = [_body_text(tc).replace("\n", " ")
                                 for tc in tr.findall(_DRAW_NS + "tc")]
                        if any(cells):
                            rows.append("| " + " | ".join(cells) + " |")
                    continue
                body = sp.find(_PML_NS + "txBody")
                if body is None:
                    continue
                text = _body_text(body)
                # The title is already the heading; repeating it reads as a
                # duplicate bullet and the model treats it as emphasis.
                if not text or text == title:
                    continue
                lines.extend(ln.strip() for ln in text.splitlines() if ln.strip())
            except Exception:
                continue                      # one odd shape must not lose the slide

        notes = ""
        try:
            if slide.has_notes_slide:
                notes = (slide.notes_slide.notes_text_frame.text or "").strip()
        except Exception:
            pass
        slides.append({"n": n, "title": title, "lines": lines,
                       "rows": rows, "notes": notes})

    # Strip master-slide furniture. A deck repeats its copyright line and page
    # number on every slide, which in the sample deck was ~30% of the extracted
    # text — pure noise that crowds real content out of the character budget and
    # reads to the model as something emphasised by repetition. Anything on more
    # than half the slides is furniture, not content; the threshold is a
    # majority rather than "all" because title and section slides often use a
    # different master.
    counts: dict = {}
    for s in slides:
        for ln in set(s["lines"]):
            counts[ln] = counts.get(ln, 0) + 1
    cutoff = max(2, len(slides) // 2)
    boilerplate = {ln for ln, c in counts.items() if c > cutoff}

    out = []
    for s in slides:
        out.append(f"### Slide {s['n']}" + (f" — {s['title']}" if s["title"] else ""))
        for ln in s["lines"]:
            # Bare page numbers survive the frequency test (each is distinct)
            # but are just as useless.
            if ln in boilerplate or ln == str(s["n"]) or ln.isdigit():
                continue
            out.append(f"- {ln}")
        out.extend(s["rows"])
        if s["notes"]:
            out.append(f"> Speaker notes: {s['notes']}")
        out.append("")
    return "\n".join(out).strip()


def extract_all(path: Path) -> str:
    """The whole extraction, with no budget applied.

    Split out from `extract` so a caller that needs both the slice and the total
    length — every continuation does — pays for one extraction rather than two.
    PDFs go through the existing converter's extractor, the one place that knows
    the markitdown -> pdftotext fallback.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        sys.path.insert(0, str(_HERE.parent / "tools"))
        try:
            from convert_pdfs import convert_text
            text = convert_text(path)
        except Exception as e:
            log(f"   extraction failed for {path.name}: {type(e).__name__}: {e}")
            text = ""
    elif suffix == ".pptx":
        try:
            text = extract_pptx(path)
        except ImportError:
            log(f"   cannot read {path.name}: python-pptx is not installed "
                f"(pip install -r interface/backend/requirements.txt)")
            text = ""
        except Exception as e:
            log(f"   extraction failed for {path.name}: {type(e).__name__}: {e}")
            text = ""
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    return (text or "").strip()


def slice_of(full: str, offset: int = 0) -> tuple:
    """(text, truncated) for one pass over an already-extracted document."""
    chunk = full[offset:offset + TEXT_BUDGET]
    return chunk, offset + len(chunk) < len(full)


def extract(path: Path, offset: int = 0) -> tuple:
    """(text, truncated) — one pass's worth of `path`, starting at `offset`.

    `offset` is what turns the budget from a ceiling on a *document* into a
    ceiling on a *pass*, which is how a deck too long for one read gets finished
    rather than abandoned at 75%. Truncation stays reported, never silent.
    """
    return slice_of(extract_all(path), offset)


# --------------------------------------------------------------------------
# coverage: how much of a source has been read
# --------------------------------------------------------------------------

def load_coverage(path=None) -> dict:
    try:
        d = json.loads(Path(path or COVERAGE_PATH).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_coverage(cov: dict, path=None) -> bool:
    p = Path(path or COVERAGE_PATH)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(cov, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except (OSError, TypeError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


CITE_RE = None      # compiled lazily; `re` is not otherwise needed here


def citations(course: str) -> dict:
    """{source filename: [note paths]} for one course, from one pass over it.

    Intake makes every note carry `> Source: ![[<file>]]`, so the notes
    themselves record which source they came from — no sidecar is needed for
    the question that actually matters, which is *what already exists* for a
    continuation to avoid duplicating.

    One read per note rather than one per (note, attachment) pair: the naive
    shape is quadratic and this runs over every course's whole folder.
    """
    global CITE_RE
    if CITE_RE is None:
        import re
        CITE_RE = re.compile(r"!\[\[([^\]|]+?)\]\]")
    folder = COURSES_ROOT / course
    out: dict = {}
    if not folder.is_dir():
        return out
    for p in sorted(folder.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = p.relative_to(VAULT).as_posix()
        for name in set(CITE_RE.findall(text)):
            out.setdefault(name.strip(), []).append(rel)
    return out


def pending(course: str | None = None, cov_path=None) -> list:
    """Sources with material nobody has read yet.

    An attachment with notes but no coverage record was taken in before this
    existed, and the old code always read exactly TEXT_BUDGET — so that is the
    honest default rather than a guess. It also means the first `--continue`
    seeds its own state from history instead of needing a migration.
    """
    cov = load_coverage(cov_path)
    out = []
    if not ATTACH.is_dir() or not COURSES_ROOT.is_dir():
        return out
    courses = [course] if course else [d.name for d in sorted(COURSES_ROOT.iterdir())
                                       if d.is_dir()]
    for c in courses:
        cites = citations(c)
        if not cites:
            continue
        for src in sorted(ATTACH.iterdir()):
            if not src.is_file() or src.suffix.lower() not in SUPPORTED:
                continue
            cited = cites.get(src.name)
            if not cited:
                continue                    # not this course's source
            key = f"{c}/{src.name}"
            rec = cov.get(key) or {}
            # One full extraction to learn the size, recorded after the first
            # pass so later runs do not pay for it again.
            total = int(rec.get("total") or _full_length(src))
            read_to = int(rec.get("read_to") or min(TEXT_BUDGET, total))
            if read_to < total:
                out.append({"source": src, "course": c, "key": key,
                            "read_to": read_to, "total": total, "notes": cited})
    return out


def _full_length(path: Path) -> int:
    """Length of the whole extraction, ignoring the per-pass budget."""
    return len(extract_all(path))


def course_context(course: str) -> str:
    """What already exists for this course, so the model links instead of
    duplicating. The course-index is the contract's designated entry point."""
    folder = COURSES_ROOT / course
    parts = []
    index = folder / f"{course.lower()}.md"
    if index.exists():
        try:
            parts.append(f"### The course index (`{index.relative_to(VAULT).as_posix()}`)\n\n"
                         + index.read_text(encoding="utf-8", errors="replace")[:4000])
        except OSError:
            pass
    existing = []
    for p in sorted(folder.rglob("*.md")):
        rel = p.relative_to(VAULT).as_posix()
        if p != index:
            existing.append(rel)
    parts.append("### Notes that already exist for this course\n\n"
                 + ("\n".join(f"- `{e}`" for e in existing[:80])
                    if existing else "_none yet — this course folder is scaffolding._"))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# the brief
# --------------------------------------------------------------------------

RULES = """
You are Sigma's study intake. Zach has dropped a piece of course material into
the vault and triggered this run himself — he is watching the output land, so
say what you are doing plainly and briefly.

Three rules bound everything you do:

1. **You do not write files.** `propose_change` is the only tool you have that
   touches disk. Each call drafts one note; the runner applies it afterwards as
   its own revertible commit. Never claim you created a file.
2. **Never invent content.** Everything in a note must come from the source
   document you were given. If the extraction is garbled or too thin to work
   with, say so and propose nothing — that is a correct outcome, and far better
   than a confident note built on OCR noise.
3. **Follow the vault contract exactly.** It is at `CLAUDE.md` and you should
   read it if anything below is unclear. The frontmatter schemas are a contract
   the dashboards query against, not decoration.
""".strip()

BRIEF = """
## The source

- **File:** `{name}`  (already copied to `99-Meta/Attachments/{name}`)
- **Course:** `{course}`  → its folder is `02-Areas/Academics/{course}/`
{truncnote}
## What this course already has

{context}

## Your job

Turn the source below into the notes Zach can actually revise from. Concretely:

1. **Decide the type** — `lecture`, `assignment`, `exam-prep`, or `resource` —
   from the content, and use the matching frontmatter schema and folder:
   - lecture     -> `02-Areas/Academics/{course}/lectures/<kebab-name>.md`
   - assignment  -> `02-Areas/Academics/{course}/assignments/<kebab-name>.md`
   - exam-prep   -> `02-Areas/Academics/{course}/exams/<kebab-name>.md`
   - resource    -> `02-Areas/Academics/{course}/<kebab-name>.md`
2. **Fill every frontmatter field you can infer** from the document — lecture
   `number` and `date`, assignment `assigned`/`due`, exam `exam`/`date`. Leave a
   key present but blank when the document genuinely does not say. Blank fields
   the source *did* state are the main way this job goes wrong.
3. **Be atomic.** If the material covers several ideas that each stand alone and
   would be linked from elsewhere, write one note per idea, named for the idea
   (`strong-induction.md`, not `lecture-4-part-2.md`), and link them to each
   other. If it is one unit — a single problem set, one exam's coverage, a
   step-by-step derivation — one note is correct. Do not split for the sake of
   splitting, and do not merge distinct ideas to save calls.
4. **Explain, do not transcribe.** A dumped wall of slide text is what the old
   converter already produced and is exactly what this replaces. Write the idea
   out properly: what it says, why it holds, a worked example where the source
   gives one, and the definitions it depends on.
5. **Link.** Every note links back to the course index
   `[[{course_index}]]`, and to the related notes listed above where they are
   genuinely related. Only ever wikilink notes that exist or that you are
   creating in this same run — a bracketed non-note is a permanent dangling node.
6. **Cite the source** near the top of each note:
   `> Source: ![[{name}]]`

Call `propose_change` once per note, with `kind: note` and `target` set to the
full vault-relative path. Then finish with one sentence naming what you made.

The extracted document arrives as the **first message of this conversation**,
under a `## The source document` heading. Work from that.
""".strip()


def build_brief(path: Path, course: str, truncated: bool) -> str:
    """The instructions only. **The document itself must never go in here.**

    `brief` becomes the system prompt, which the SDK passes as
    `--append-system-prompt` on the command line, and Windows caps a command
    line at 32,767 characters. A lecture PDF extracts to 40-65k, so embedding it
    here made every intake fail — and fail as `CLINotFoundError: Claude Code not
    found`, naming a binary that was sitting right there and running fine, which
    is why it read as a broken install rather than an oversized argument.

    `fleet.run_one` already had `material` for exactly this. Its docstring
    documents this precise failure mode and names "an extracted document" as the
    use case; intake was simply the one caller that never used it. The mechanism
    was built and then not wired up — the same shape as this project's other
    silent failures, where something was configured but never executed.
    """
    trunc = ""
    if truncated:
        trunc = (f"- **Note:** the extraction was longer than {TEXT_BUDGET:,} characters "
                 f"and you are seeing the first part only. Say so in the note "
                 f"rather than implying you covered the whole document.\n")
    return BRIEF.format(name=path.name, course=course, truncnote=trunc,
                        context=course_context(course),
                        course_index=f"{course.lower()}")


def build_material(text: str) -> str:
    """The document, as the conversation turn `run_one` sends over the CLI's
    stdin — which has no command-line ceiling."""
    return f"## The source document\n\n{text}"


CONTINUE_NOTE = """
## This is a continuation, not a fresh document

`{name}` was too long for one pass. Characters **{start:,}–{end:,} of {total:,}**
are below; everything before {start:,} has already been turned into notes, and
those notes exist now:

{existing}

So:

- **Write notes only for material the earlier part did not cover.** If this
  section finishes an idea those notes began, say so by *editing nothing* and
  instead proposing a new note only when the idea genuinely stands on its own.
- **Link to the existing notes** by name instead of restating them. They are
  real notes; `[[propositional-logic]]`-style links to the list above resolve.
- The slides may open mid-sentence or repeat a summary slide. If this section
  turns out to hold nothing that is not already covered, **propose nothing and
  say so** — that is a correct outcome here, not a failure.
- The `number:` frontmatter field is the lecture/topic number, which does not
  change just because this is a later part of the same deck.
"""


def build_continue_brief(path: Path, course: str, start: int, end: int,
                         total: int, existing: list) -> str:
    """The ordinary brief plus what makes a continuation different."""
    listed = "\n".join(f"- `{e}`" for e in existing) or "- _(none found)_"
    return (build_brief(path, course, truncated=end < total)
            + "\n\n"
            + CONTINUE_NOTE.format(name=path.name, start=start, end=end,
                                   total=total, existing=listed).strip())


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

async def continue_one(src: Path, course: str, start: int, total: int,
                       existing: list, model: str = "sonnet") -> dict:
    """Read the next TEXT_BUDGET characters of a source already partly taken in.

    The source is the copy in `99-Meta/Attachments/` — the drop-folder original
    is gone by now, which is exactly why intake archives it before reading. No
    new write path and no new applier rules: this produces `propose_change`
    calls like any other intake, so a continuation that re-derives a note that
    already exists is *held* rather than overwriting it. That safety net is what
    makes it acceptable for the model to judge overlap.
    """
    import fleet as fl

    full = extract_all(src)
    text, truncated = slice_of(full, start)
    if len(text) < 200:
        return {"ok": True, "proposals": 0, "files": [],
                "summary": f"only {len(text)} characters left after {start:,} — "
                           f"nothing worth a pass"}

    end = start + len(text)
    spec = sp.Specialist(
        key="intake", title="Study intake", cadence="manual",
        model=model, effort="medium",
        brief=build_continue_brief(src, course, start, end, total, existing),
        max_turns=44)
    r = await fl.run_one(spec, timeout_s=TIMEOUT_S, rules=RULES,
                         material=build_material(text))
    r["read_to"] = end
    r["total"] = total
    return r


def run_continue(only_course: str | None = None, dry_run: bool = False,
                 limit: int = 0, model: str = "sonnet", cov_path=None) -> int:
    """Finish every source that ran past one pass.

    Coverage advances only on a run that actually came back ok. A pass that
    failed leaves the record where it was, so re-running resumes from the same
    place instead of skipping the part nobody read — the same principle as
    leaving a source in the drop folder when its notes did not land.
    """
    todo = pending(only_course, cov_path)
    if not todo:
        log("every source is fully read")
        return 0

    if limit and len(todo) > limit:
        log(f"  {len(todo)} source(s) unfinished; taking the first {limit}")
        todo = todo[:limit]

    log(f"{len(todo)} source(s) with material still unread")
    for t in todo:
        pct = 100.0 * t["read_to"] / max(1, t["total"])
        log(f"  {t['key']}: read to {t['read_to']:,} of {t['total']:,} ({pct:.0f}%)"
            f" — {t['total'] - t['read_to']:,} left")
    if dry_run:
        return 0

    import applier
    made = failed = 0
    for t in todo:
        log(f"-> {t['key']} from {t['read_to']:,}")
        try:
            r = asyncio.run(continue_one(t["source"], t["course"], t["read_to"],
                                         t["total"], t["notes"], model=model))
        except Exception as e:
            log(f"   failed — {type(e).__name__}: {e}")
            failed += 1
            continue
        if not r.get("ok"):
            log(f"   failed — {r.get('error')}")
            failed += 1
            continue

        applied = []
        try:
            applied = applier.apply_run(r["files"], actor="intake")
        except Exception as e:
            log(f"   proposals written but applying failed: {type(e).__name__}: {e}")
        landed = [a for a in applied if a.get("action") in ("create", "update")]
        held = [a for a in applied if a.get("action") == "held"]
        log(f"   {r.get('proposals', 0)} note(s) proposed, {len(landed)} applied"
            + (f", {len(held)} held" if held else ""))
        for a in landed:
            log(f"      {a.get('target') or a.get('proposal')}")
        for a in held:
            log(f"      HELD {a.get('target') or a.get('proposal')} — {a.get('reason')}")
        if r.get("summary"):
            log(f"   {' '.join(str(r['summary']).split())[:200]}")

        # Coverage advances even when this pass proposed nothing: the
        # characters *were* read, and a section that genuinely added nothing new
        # is a correct outcome. Not advancing would loop on it forever.
        cov = load_coverage(cov_path)
        cov[t["key"]] = {"read_to": int(r.get("read_to") or t["read_to"]),
                         "total": t["total"],
                         "last": datetime.date.today().isoformat()}
        save_coverage(cov, cov_path)
        made += 1

    log(f"continue finished: {made} pass(es)"
        + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


async def intake_one(path: Path, course: str, model: str = "sonnet") -> dict:
    import fleet as fl

    text, truncated = extract(path)
    if len(text) < 200:
        return {"ok": False, "proposals": 0, "files": [],
                "error": f"only {len(text)} characters of text came out — "
                         f"a scanned PDF with no text layer, or an empty file"}

    # Copy first, so the `![[...]]` the notes carry resolves the moment they
    # land. Idempotent, and a stray attachment from a failed run is harmless
    # next to a note whose source link is dead.
    try:
        ATTACH.mkdir(parents=True, exist_ok=True)
        target = ATTACH / path.name
        if not target.exists():
            shutil.copy2(path, target)
    except OSError as e:
        log(f"   could not archive {path.name}: {e}")

    spec = sp.Specialist(
        key="intake", title="Study intake", cadence="manual",
        model=model, effort="medium",
        brief=build_brief(path, course, truncated),
        # Several notes, each a propose_change call, plus the reads it makes to
        # check what it is linking to.
        max_turns=44)
    # The document rides `material` (stdin), never the brief (the command line).
    # See build_brief for what embedding it here cost.
    return await fl.run_one(spec, timeout_s=TIMEOUT_S, rules=RULES,
                            material=build_material(text))


def run(only_course: str | None = None, dry_run: bool = False,
        keep: bool = False, limit: int = 0, model: str = "sonnet") -> int:
    items, problems = scan()
    if only_course:
        want = only_course.lower()
        items = [(p, c) for p, c in items if c.lower() == want]

    for name, why in problems:
        log(f"  skipped {name} — {why}")

    if not items:
        log(f"nothing to intake in {DROP.relative_to(VAULT).as_posix()}/")
        return 0 if not problems else 1

    if limit and len(items) > limit:
        log(f"  {len(items)} waiting; taking the first {limit} "
            f"(the rest stay in the drop folder)")
        items = items[:limit]

    kinds = {p.suffix.lower() for p, _ in items}
    how = []
    if ".pdf" in kinds:
        how.append(f"pdf via {extractor_name()}")
    if ".pptx" in kinds:
        how.append("pptx via python-pptx")
    log(f"{len(items)} file(s) to intake"
        + (f" ({', '.join(how)})" if how else ""))
    if dry_run:
        for p, c in items:
            log(f"  would read {p.name} -> {c}")
        return 0

    # `declined` is deliberately not `failed`. The brief tells the model that
    # material too thin to work with should produce nothing — "that is a
    # correct outcome, and far better than a confident note built on OCR
    # noise". Counting that as a failure made the code contradict the
    # instructions it ships: a 548-character slide deck was correctly refused
    # and the run still exited 1, which the palette renders as "✗ failed" and a
    # scheduler would read as broken. Judgement is not an error.
    made = failed = declined = 0
    for p, c in items:
        log(f"-> {p.name} ({c})")
        try:
            r = asyncio.run(intake_one(p, c, model=model))
        except Exception as e:
            r = {"ok": False, "proposals": 0, "files": [],
                 "error": f"{type(e).__name__}: {e}"}

        if not r.get("ok"):
            failed += 1
            log(f"   left in place — {r.get('error') or 'the run failed'}")
            continue
        if not r.get("proposals"):
            declined += 1
            log(f"   nothing worth writing — left in place"
                + (f" ({r['summary'][:160]})" if r.get("summary") else ""))
            continue

        # Land them, through the one applier the fleet already uses: one commit
        # per note, in the ledger, revertible from Ctrl+J.
        applied, apply_broke = [], None
        try:
            import applier
            applied = applier.apply_run(r["files"], actor="intake")
        except Exception as e:
            apply_broke = f"{type(e).__name__}: {e}"
            log(f"   proposals written but applying failed: {apply_broke}")

        landed = [a for a in applied if a.get("action") in ("create", "update")]
        held = [a for a in applied if a.get("action") == "held"]
        log(f"   {r['proposals']} note(s) proposed, {len(landed)} applied")
        for a in landed:
            log(f"      {a.get('target') or a.get('proposal')}")
        # A hold is the interesting outcome, not a silent one: it usually means
        # the note already existed, so the change is staged for `reflect diff`
        # rather than overwritten.
        for a in held:
            log(f"      HELD {a.get('target') or a.get('proposal')} — {a.get('reason')}")
        if r.get("summary"):
            log(f"   {r['summary'][:300]}")

        # The source is cleared only when a note actually reached the vault.
        # Proposing is not landing: the first real run proposed a note and then
        # could not apply it (a hanging git remote), and clearing the drop
        # folder on "the model finished" left the source gone with nothing to
        # show for it. Held counts — a hold means the change is staged and
        # recorded, not lost.
        if not landed and not held:
            failed += 1
            why = apply_broke or "nothing was applied"
            log(f"   left in place — {r['proposals']} proposal(s) written but "
                f"{why}; fix it and run `sigma reflect apply`")
            continue

        if not keep:
            try:
                p.unlink()
            except OSError as e:
                log(f"   could not clear {p.name} from the drop folder: {e}")
        made += 1

    tally = f"intake finished: {made} filed"
    if declined:
        tally += f", {declined} with nothing worth writing"
    if failed:
        tally += f", {failed} failed"
    log(tally)
    # Only real failures are a non-zero exit. A declined document is a working
    # run that found nothing, which is not the same thing as a broken one.
    return 1 if failed else 0


def status() -> int:
    items, problems = scan()
    rel = DROP.relative_to(VAULT).as_posix()
    if not DROP.exists():
        log(f"{rel}/ does not exist yet — create it and drop course material in "
            f"a <COURSE>/ subfolder")
        return 0
    by_course: dict = {}
    for _, c in items:
        by_course[c] = by_course.get(c, 0) + 1
    if items:
        log(f"{len(items)} file(s) waiting in {rel}/")
        for c, n in sorted(by_course.items()):
            log(f"   {c:<10} {n}")
    else:
        log(f"nothing waiting in {rel}/")
    for name, why in problems:
        log(f"   ! {name} — {why}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Turn dropped course material into notes.")
    ap.add_argument("--status", action="store_true", help="what is waiting, and file it nowhere")
    ap.add_argument("--dry-run", action="store_true", help="name what would be read; call no model")
    ap.add_argument("--course", default="", help="only this course code")
    ap.add_argument("--keep", action="store_true", help="leave sources in the drop folder")
    ap.add_argument("--max", type=int, default=0, help="stop after N files")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--continue", dest="cont", action="store_true",
                    help="read the rest of any source that ran past one pass")
    a = ap.parse_args()
    if a.status:
        return status()
    if a.cont:
        return run_continue(only_course=a.course or None, dry_run=a.dry_run,
                            limit=a.max, model=a.model)
    return run(only_course=a.course or None, dry_run=a.dry_run,
               keep=a.keep, limit=a.max, model=a.model)


if __name__ == "__main__":
    raise SystemExit(main())
