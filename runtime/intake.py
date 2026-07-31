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

SUPPORTED = {".pdf", ".md", ".txt"}

# One document's extracted text, capped. A 200-page textbook would otherwise
# blow the window on a single call and fail the whole run. Truncation is
# reported, never silent — the note that comes back says it saw a prefix.
TEXT_BUDGET = 60_000

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
    """Which extractor this process will actually get.

    `markitdown` lives in the machine-local `~/.obsidian-tools/venv` that
    `tools/convert.cmd` uses, not in the interface venv intake runs on — so
    intake silently gets the `pdftotext -layout` fallback. That is good enough
    for text PDFs and is why this is not a bug, but note quality tracks
    extraction quality, and a mysterious drop in one is worth being able to
    explain by the other. So the run says which one it used.
    """
    import importlib.util
    return "markitdown" if importlib.util.find_spec("markitdown") else "pdftotext -layout"


def extract(path: Path) -> tuple:
    """(text, truncated). PDFs go through the existing converter's extractor —
    the one place that knows the markitdown -> pdftotext fallback."""
    if path.suffix.lower() == ".pdf":
        sys.path.insert(0, str(_HERE.parent / "tools"))
        try:
            from convert_pdfs import convert_text
            text = convert_text(path)
        except Exception as e:
            log(f"   extraction failed for {path.name}: {type(e).__name__}: {e}")
            text = ""
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    text = (text or "").strip()
    if len(text) > TEXT_BUDGET:
        return text[:TEXT_BUDGET], True
    return text, False


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

## The source document

{body}
""".strip()


def build_brief(path: Path, course: str, text: str, truncated: bool) -> str:
    trunc = ""
    if truncated:
        trunc = (f"- **Note:** the extraction was longer than {TEXT_BUDGET:,} characters "
                 f"and you are seeing the first part only. Say so in the note "
                 f"rather than implying you covered the whole document.\n")
    return BRIEF.format(name=path.name, course=course, truncnote=trunc,
                        context=course_context(course),
                        course_index=f"{course.lower()}", body=text)


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

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
        brief=build_brief(path, course, text, truncated),
        # Several notes, each a propose_change call, plus the reads it makes to
        # check what it is linking to.
        max_turns=44)
    return await fl.run_one(spec, timeout_s=TIMEOUT_S, rules=RULES)


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

    log(f"{len(items)} file(s) to intake (pdf text via {extractor_name()})")
    if dry_run:
        for p, c in items:
            log(f"  would read {p.name} -> {c}")
        return 0

    made = failed = 0
    for p, c in items:
        log(f"-> {p.name} ({c})")
        try:
            r = asyncio.run(intake_one(p, c, model=model))
        except Exception as e:
            r = {"ok": False, "proposals": 0, "files": [],
                 "error": f"{type(e).__name__}: {e}"}

        if not r.get("ok") or not r.get("proposals"):
            failed += 1
            why = r.get("error") or "the model proposed nothing"
            log(f"   left in place — {why}")
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

    log(f"intake finished: {made} filed, {failed} left in place")
    return 0 if failed == 0 else 1


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
    a = ap.parse_args()
    if a.status:
        return status()
    return run(only_course=a.course or None, dry_run=a.dry_run,
               keep=a.keep, limit=a.max, model=a.model)


if __name__ == "__main__":
    raise SystemExit(main())
