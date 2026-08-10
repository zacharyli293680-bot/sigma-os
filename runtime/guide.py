#!/usr/bin/env python3
"""
guide.py — the study-guide generation pipeline (study mode, S7).

Not a button — a pipeline, sequenced like the fleet: blueprint pass → Zach's
approval (he flips `status: approved` by hand — nothing here ever does) →
module pass → checkpoint pass, one revertible commit per note, resumable with
no state file because the notes on disk say which jobs already ran.

**The model composes; script code does everything else.** Each job is one
`claude -p` call through `sigma.call_model` (the same chokepoint reflect and
retro spend through), with its grounding pasted into the prompt by THIS script
— the tutor's pin doctrine, applied to generation: the backend assembles
context, the model never chooses its own sources. The reply is parsed with
`lesson.py`'s own parser, serialised canonically, and validated BEFORE a
proposal is written (§13: never write something the scanner cannot read).
The proposal then goes through `applier.apply_one` like every other write —
where the two S7 holds check the same grammar and the approved blueprint
again, so a drifting generator is held with a reason, not committed.

**Window economics: pause, never degrade.** The fleet degrades to haiku on a
first rate limit because a maintenance specialist on a small model still finds
drift. A module authored by a degraded model is worse *course content* that
auto-applies silently — so this pipeline reads §13's "degrade→pause" as the
transition it names: when the window degrades, the run pauses, records when
it can resume, and a later run picks up at the first missing note. Resume is
free here by design; quality loss is not.

Nothing here imports the Agent SDK — `claude -p` is the whole model surface,
so this runs under any interpreter, like reflect and retro. That is also why
the fleet's Lock is copied (40 lines) rather than imported: importing fleet
drags in the backend's propose tool and with it the SDK.
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from sigma import (DEFAULT_VAULT, call_model, kebab, make_logger,  # noqa: E402
                   parse_model_json, write_note, write_state)
from sigma import gitops, ledger, spend  # noqa: E402

import lesson as ln  # noqa: E402

log = make_logger(HERE / "guide.log", "sigma guide")

MODEL = "sonnet"          # the authoring tier every other composer here uses
BLUEPRINT_TIMEOUT = 420
JOB_TIMEOUT = 600
SOURCE_CAP = 12000        # chars pasted per source note — intake's lesson:
                          # one oversized paste must not blow the whole call
PROGRESS_PATH = HERE / "guide.progress.json"
LOCK_PATH = HERE / "guide.lock"


def write_progress(prog: dict):
    prog["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    write_state(PROGRESS_PATH, prog)


def _hours_since(stamp) -> float | None:
    try:
        return (datetime.datetime.now()
                - datetime.datetime.fromisoformat(str(stamp))).total_seconds() / 3600
    except Exception:
        return None


class Lock:
    """fleet.py's stale-tolerant lock, its own copy (see the module docstring
    for why it is not imported). Two generation runs at once would race the
    same missing-module list into duplicate proposals."""

    def __init__(self, path: Path | None = None, stale_hours: float = 2.0):
        # LOCK_PATH is read at call time, not def time, so tests can re-point
        # it — gitops._Mutex's own rule.
        self.path = Path(path or LOCK_PATH)
        self.stale_hours, self.held = stale_hours, False

    def __enter__(self):
        for _attempt in (1, 2):
            try:
                with self.path.open("x", encoding="utf-8") as f:
                    f.write(json.dumps(
                        {"pid": os.getpid(),
                         "at": datetime.datetime.now().isoformat(timespec="seconds")}))
                self.held = True
                return self
            except FileExistsError:
                age = _hours_since((self._read() or {}).get("at"))
                if age is not None and age < self.stale_hours:
                    return self
                log(f"taking over a stale lock ({age:.1f}h old)" if age is not None
                    else "taking over an unreadable lock")
                try:
                    self.path.unlink()
                except OSError:
                    return self
            except OSError as e:
                log(f"could not take lock: {e}")
                return self
        return self

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def __exit__(self, *exc):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


# --------------------------------------------------------------------------
# the window — the palette enforces its hold at POST; this is the CLI's copy
# of the same arithmetic, the way fleet.py runs its own pre-check
# --------------------------------------------------------------------------
def window_refusal() -> str | None:
    try:
        if spend.rate_limited_within(45):
            return "window: rate-limited in the last 45 min — resumes when it rolls"
    except Exception:
        pass                        # metering failure must not block a run
    now = datetime.datetime.now()
    if now.hour == 8 and now.minute >= 40:
        return "reserved for the 09:00 fleet run"
    if now.hour == 5 and now.minute >= 40:
        return "reserved for the 06:00 review"
    return None


def _just_rate_limited() -> bool:
    """Did the call that just returned hit the limit? call_model records every
    call in the spend log with a rate_limited flag — reading it back beats
    growing a second detector."""
    try:
        return spend.rate_limited_within(1)
    except Exception:
        return False


# --------------------------------------------------------------------------
# course material — the deterministic half of every prompt
# --------------------------------------------------------------------------
def _cap(text: str, n: int = SOURCE_CAP) -> str:
    return text if len(text) <= n else text[:n] + "\n\n[... truncated ...]"


def course_notes(vault: Path, folder: Path) -> list[dict]:
    """Every readable, unsealed markdown note in the course folder except the
    guide/ family itself — the material a blueprint plans from."""
    paths = [p for p in sorted(folder.rglob("*.md"))
             if p.parent.name != "guide"
             and not p.name.endswith(("-guide.md", "-guide-blueprint.md",
                                      "-study-log.md", "-timeline.md"))]
    rels = [p.relative_to(vault).as_posix() for p in paths]
    sealed, _ = ln._default_split(vault, rels)
    out = []
    for p, rel in zip(paths, rels):
        if rel in sealed:
            continue
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        fm = ln.frontmatter(text) or {}
        out.append({"rel": rel, "type": str(fm.get("type") or "").strip(),
                    "title": ln._h1(text) or p.stem, "text": text})
    return out


def _read_rel(vault: Path, rel: str) -> str:
    try:
        return (vault / rel).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


# --------------------------------------------------------------------------
# proposals — script code writes them (reflect.py's own precedent), so the
# re-parse gate sits BEFORE the write, by construction
# --------------------------------------------------------------------------
def _pending_proposal_for(rel: str) -> str | None:
    """An unresolved proposal already targeting this path — regenerating on
    top of it would pile duplicates into WAITING ON YOU."""
    import reflect as rf
    try:
        candidates = sorted(Path(rf.PROPOSALS).glob("*.md"))
    except OSError:
        return None
    for p in candidates:
        try:
            fm = ln.frontmatter(p.read_text(encoding="utf-8", errors="replace")) or {}
        except OSError:
            continue
        if (str(fm.get("target") or "").strip().strip('"') == rel
                and str(fm.get("status") or "").strip() in ("pending", "approved")):
            return p.name
    return None


def _propose_and_apply(title: str, rel: str, content: str, rationale: str,
                       run_id: str) -> dict:
    import applier
    import reflect as rf
    today = datetime.date.today().isoformat()
    path = rf.write_proposal({"title": title, "kind": "note", "target": rel,
                              "content": content, "rationale": rationale,
                              "risk": "low", "insight": ""}, today)
    log(f"proposed {path.name} -> {rel}")
    return applier.apply_one(path, actor="guide", extra={"run": run_id})


# --------------------------------------------------------------------------
# the blueprint pass — inventory in, one draft blueprint note out (§13.1)
# --------------------------------------------------------------------------
BLUEPRINT_PROMPT = """You are planning a study guide for the course {course}.

Below is an inventory of every note in the course folder: its vault-relative
path, its type, and its title. Lecture-family notes are the teaching material;
assignment/exam notes tell you where the course's own assessments fall.

Plan the guide as MODULES: one lecture's worth each, 30-60 minutes to complete
(estimate 30-60). Group into UNITS of 4-6 modules only if there are more than
8 modules, drawing unit boundaries at the course's own seams (exam coverage).
Place one CHECKPOINT per unit (for a flat course, one every 4 modules), each
covering the modules before it.

Rules:
- Every module lists 1-3 source paths, chosen ONLY from the inventory below,
  preferring lecture-family notes. Never invent a path.
- Titles are short and name the concept, not the file.
- Module numbers start at 1 and follow teaching order.
- If the inventory already names existing guide modules, keep their numbers
  and titles exactly as given.

Answer with ONLY a JSON object, no prose:
{{"units": [{{"n": 1, "title": "..."}}] or [],
  "rows": [
    {{"kind": "module", "n": 1, "title": "...", "unit": 1 or null,
      "est": 45, "sources": ["02-Areas/Academics/.../x.md"]}},
    {{"kind": "checkpoint", "n": 1, "title": "...", "unit": 1 or null,
      "covers": [1, 2, 3, 4]}}
  ]}}

Existing guide notes (keep these rows verbatim in your plan):
{existing}

Inventory:
{inventory}
"""


def _blueprint_rows_to_dict(course: str, data: dict) -> dict:
    rows, units = [], []
    for u in (data.get("units") or []):
        try:
            units.append({"n": int(u["n"]),
                          "title": str(u.get("title") or "").strip() or None,
                          "line": 0})
        except (KeyError, TypeError, ValueError):
            continue
    for r in (data.get("rows") or []):
        try:
            kind = r["kind"]
            row = {"kind": kind, "n": int(r["n"]),
                   "title": str(r.get("title") or "").strip(),
                   "unit": int(r["unit"]) if r.get("unit") else None,
                   "line": 0}
            if kind == "module":
                row["est"] = int(r.get("est") or 0)
                row["sources"] = [str(s).replace("\\", "/")
                                  for s in (r.get("sources") or [])]
            elif kind == "checkpoint":
                row["covers"] = sorted({int(c) for c in (r.get("covers") or [])})
            else:
                continue
            rows.append(row)
        except (KeyError, TypeError, ValueError):
            continue
    return {"course": course, "status": "draft", "rows": rows, "units": units}


def blueprint_pass(vault: Path, course: str, run_id: str, compose=None) -> dict:
    """One model call → a validated draft blueprint, landed as a proposal
    through the applier (its own commit). Returns the applier's record, or
    {"action": "failed", "reason": ...}."""
    folder = ln.course_folder(vault, course)
    code = folder.name.lower()
    rel = f"02-Areas/Academics/{folder.name}/{code}-guide-blueprint.md"
    notes = course_notes(vault, folder)
    if not notes:
        return {"action": "failed", "reason": "no readable notes in the course "
                                              "folder — nothing to plan from"}
    have_m, have_c = ln.existing_units(vault, course)
    existing = []
    for row in ln.scan(vault) + ln.scan_checkpoints(vault):
        if row["course"].lower() != folder.name.lower():
            continue
        if "checkpoint" in row:
            existing.append(f"- CP{row['checkpoint']} · {row['title']} · "
                            f"covers {row['covers']}")
        else:
            existing.append(f"- M{row['module']:02} · {row['title']}")
    inventory = "\n".join(f"- {n['rel']} · type: {n['type'] or '?'} · {n['title']}"
                          for n in notes)
    prompt = BLUEPRINT_PROMPT.format(course=folder.name,
                                     existing="\n".join(existing) or "(none)",
                                     inventory=inventory)
    call = compose or (lambda p: call_model(p, MODEL, timeout=BLUEPRINT_TIMEOUT,
                                            actor="guide"))
    errs: list[str] = ["no reply"]
    for attempt in (1, 2):
        out = call(prompt)
        if _just_rate_limited():
            return {"action": "failed", "reason": "rate limited", "paused": True}
        data = parse_model_json(out)
        if data is None:
            errs = ["the reply was not parseable JSON"]
        else:
            d = _blueprint_rows_to_dict(folder.name, data)
            text = ln.serialize_blueprint(d)
            errs = ln.validate_blueprint(text, vault=vault)
        if not errs:
            return _propose_and_apply(
                f"{folder.name} study-guide blueprint",
                rel, text,
                f"The S7 blueprint pass planned "
                f"{sum(1 for r in d['rows'] if r['kind'] == 'module')} "
                f"modules from {len(notes)} course notes. Edit the rows, then "
                f"set `status: approved` to authorise generation.",
                run_id)
        if attempt == 1:
            prompt = (prompt + "\n\nYour previous plan failed validation:\n"
                      + "\n".join(f"- {e}" for e in errs[:12])
                      + "\n\nAnswer again with corrected JSON only.")
            log(f"blueprint attempt 1 failed validation ({len(errs)}) — repairing")
    return {"action": "failed",
            "reason": "the plan failed validation twice: " + "; ".join(errs[:3])}


# --------------------------------------------------------------------------
# approval-time scaffold — the chain and the course index (§13.2). Script
# writes, one commit each, because these are deterministic derivations of the
# approved blueprint — no model, exactly like the applier itself.
# --------------------------------------------------------------------------
def _row_label(text: str) -> str | None:
    m = re.match(r"^(M\d+|CP\d+)\b", text.strip())
    return m.group(1) if m else None


def module_basename(code: str, n: int, title: str) -> str:
    return f"{code}-m{n:02}-{kebab(title)}"


def chain_lines(code: str, bp: dict) -> list[tuple[str, str]]:
    """(label, row line) per blueprint row, in plan order."""
    out = []
    for r in bp["rows"]:
        if r["kind"] == "module":
            base = module_basename(code, r["n"], r["title"])
            out.append((f"M{r['n']:02}",
                        f"- [ ] M{r['n']:02} · [[{base}|{r['title']}]]"))
        else:
            base = f"{code}-checkpoint-{r['n']}"
            title = r.get("title") or f"Checkpoint {r['n']}"
            out.append((f"CP{r['n']}", f"- [ ] CP{r['n']} · [[{base}|{title}]]"))
    return out


def reconcile_chain(vault: Path, course: str, bp: dict, run_id: str) -> dict | None:
    """Create the chain, or fold the approved plan's rows into an existing one
    — never touching a present row's line (its checked state, its skip marker,
    its wording are Zach's), never deleting a row the plan no longer names.
    Returns the commit record, or None when nothing changed."""
    folder = ln.course_folder(vault, course)
    code = folder.name.lower()
    p = folder / f"{code}-guide.md"
    rel = p.relative_to(vault).as_posix()
    desired = chain_lines(code, bp)

    if p.is_file():
        text = p.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        row_at = {}                  # label -> line index
        row_idx = []
        in_fence = False
        for i, line in enumerate(lines):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not ln.CHAIN_ROW_RE.match(line):
                continue
            row_idx.append(i)
            m = ln.CHAIN_ROW_RE.match(line)
            label = _row_label(m.group(2))
            if label:
                row_at.setdefault(label, i)
        block = [lines[row_at[lab]] if lab in row_at else new
                 for lab, new in desired]
        # a hand row the plan does not name stays, after the planned block
        extras = [lines[i] for i in row_idx
                  if _row_label(ln.CHAIN_ROW_RE.match(lines[i]).group(2))
                  not in {lab for lab, _ in desired}]
        insert_at = row_idx[0] if row_idx else len(lines)
        kept = [line for i, line in enumerate(lines) if i not in set(row_idx)]
        new_lines = kept[:insert_at] + block + extras + kept[insert_at:]
        new_text = "\n".join(new_lines).rstrip("\n") + "\n"
        if new_text == text:
            return None
        verb, summary = "update", f"chain reconciled with the approved blueprint"
    else:
        new_text = ("---\n"
                    "type: guide\n"
                    f"course: {folder.name}\n"
                    "tags: [guide]\n"
                    "---\n\n"
                    f"# {folder.name} — study guide chain\n\n"
                    f"One checkbox row per module, in teaching order, from the "
                    f"approved [[{code}-guide-blueprint|blueprint]] "
                    f"([[sigma-os-study-plan]] §5.2). Rows are undated; "
                    f"completing one is Zach's click, skipping flips it to "
                    f"`[-]` + `skipped::date`.\n\n"
                    "## Modules\n\n"
                    + "\n".join(line for _, line in desired) + "\n")
        verb, summary = "create", "chain created from the approved blueprint"

    with gitops.vault_write(vault) as w:
        write_note(p, new_text)
        res = w.commit(rel, f"sigma(guide): {verb} {rel} - {summary}")
    ledger.record("guide", verb, rel, res["sha"], summary,
                  extra={"run": run_id,
                         **({"absorbed": True} if res["absorbed"] else {})})
    log(f"{verb} {rel} ({(res['sha'] or 'no commit')[:10]})")
    return res


def reconcile_index(vault: Path, course: str, bp: dict, run_id: str) -> dict | None:
    """The course index gains (or completes) a `### Guide` group under
    `## Notes` — every planned note linked, links to not-yet-written notes
    included, because an unreachable module is the exact failure AA-210's
    intake already taught the contract (§13.2)."""
    folder = ln.course_folder(vault, course)
    code = folder.name.lower()
    p = folder / f"{code}.md"
    if not p.is_file():
        log(f"no course index {code}.md — skipping the ### Guide group")
        return None
    rel = p.relative_to(vault).as_posix()
    text = p.read_text(encoding="utf-8-sig", errors="replace")

    wanted = [(f"{code}-guide", "the module chain"),
              (f"{code}-guide-blueprint", "the approved plan generation runs against")]
    for r in bp["rows"]:
        if r["kind"] == "module":
            wanted.append((module_basename(code, r["n"], r["title"]),
                           f"M{r['n']:02} · {r['title']}"))
        else:
            title = r.get("title") or f"Checkpoint {r['n']}"
            wanted.append((f"{code}-checkpoint-{r['n']}", f"CP{r['n']} · {title}"))
    missing = [(base, label) for base, label in wanted
               if f"[[{base}]" not in text and f"[[{base}|" not in text]
    if not missing:
        return None
    add = "\n".join(f"- [[{base}|{label}]]" for base, label in missing)

    m = re.search(r"^### Guide\s*$", text, re.M)
    if m:
        # append to the existing group: insert before the group's end (the
        # next heading or EOF)
        tail = text[m.end():]
        nxt = re.search(r"^#{2,3} ", tail, re.M)
        at = m.end() + (nxt.start() if nxt else len(tail))
        new_text = text[:at].rstrip("\n") + "\n" + add + "\n\n" + text[at:].lstrip("\n")
    else:
        notes_m = re.search(r"^## Notes\s*$", text, re.M)
        block = f"\n### Guide\n{add}\n"
        if notes_m:
            tail = text[notes_m.end():]
            nxt = re.search(r"^## ", tail, re.M)
            at = notes_m.end() + (nxt.start() if nxt else len(tail))
            new_text = text[:at].rstrip("\n") + "\n" + block + "\n" + text[at:].lstrip("\n")
        else:
            new_text = text.rstrip("\n") + f"\n\n## Notes\n{block}"
    with gitops.vault_write(vault) as w:
        write_note(p, new_text)
        res = w.commit(rel, f"sigma(guide): update {rel} - link {len(missing)} "
                            f"guide note(s) from ### Guide")
    ledger.record("guide", "update", rel, res["sha"],
                  f"### Guide group linked {len(missing)} planned note(s)",
                  extra={"run": run_id,
                         **({"absorbed": True} if res["absorbed"] else {})})
    log(f"index: linked {len(missing)} guide note(s)")
    return res


# --------------------------------------------------------------------------
# the module pass — one job per missing blueprint row (§13.3)
# --------------------------------------------------------------------------
MODULE_PROMPT = """You are authoring ONE module of a study guide for {course},
strictly grounded in the source notes pasted below. Do not use knowledge the
sources do not state; every claim must be traceable to them.

Module M{n:02} · "{title}" — target about {est} minutes of study.

Write ONLY the module BODY (no frontmatter, no H1), in exactly this grammar:

## S1 · <segment title> ⏱ <minutes>
source:: <one of the source paths below>

### Summary
<2-4 sentences — the idea at a glance>

### Normal
<the full teaching pass: definitions, formulas, the reasoning>

### In depth
<the deeper pass: edge cases, derivations, connections>

### Example
<a fully worked example from the sources (at least one segment must have this)>

?? q-{n}-1 · <kind>
<the question prompt; for mcq include options A)–D)>
- hint:: <optional hint>
- answer:: <the answer>
- solution:: <the worked solution>

Rules (a parser rejects violations mechanically):
- 3-7 segments; their ⏱ minutes are integers and must sum to {est}.
- Every segment needs at least one `source::` line, chosen ONLY from:
{sources}
- All three of `### Summary`, `### Normal`, `### In depth` in every segment,
  each with real content. `### Example` in at least one segment.
- 8-12 practice items across the module, ids q-{n}-1, q-{n}-2, ... unique,
  kind one of: mcq | numeric | short | code | proof.
- Every item has `- answer::` and `- solution::` with real content.
- Use `·` (U+00B7) and `⏱` exactly as shown in headings and item lines.

Source notes:

{material}
"""


def author_module(vault: Path, course: str, row: dict, compose=None) -> tuple[str | None, list[str]]:
    """One module job: compose → parse → canonicalise → validate → (text,
    errors). The text is proposal-ready only when errors is empty."""
    folder = ln.course_folder(vault, course)
    material = "\n\n".join(f"--- {src} ---\n{_cap(_read_rel(vault, src))}"
                           for src in row["sources"])
    prompt = MODULE_PROMPT.format(course=folder.name, n=row["n"],
                                  title=row["title"], est=row["est"],
                                  sources="\n".join(f"  - {s}" for s in row["sources"]),
                                  material=material)
    call = compose or (lambda p: call_model(p, MODEL, timeout=JOB_TIMEOUT,
                                            actor="guide"))
    errs: list[str] = ["no reply"]
    for attempt in (1, 2):
        out = call(prompt)
        if _just_rate_limited():
            return None, ["rate limited"]
        if not (out or "").strip():
            errs = ["the model returned nothing"]
        else:
            text = _assemble_module(folder.name, row, out)
            errs = ln.validate_any(text, vault=vault)
            if not errs:
                return text, []
        if attempt == 1:
            prompt = (prompt + "\n\nYour previous attempt failed validation:\n"
                      + "\n".join(f"- {e}" for e in errs[:12])
                      + "\n\nWrite the corrected module body again, in full.")
            log(f"M{row['n']:02} attempt 1 failed validation ({len(errs)}) — repairing")
    return None, errs


def _strip_fence(out: str) -> str:
    s = (out or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\n?|\n?```$", "", s).strip()
    return s


def _assemble_module(course: str, row: dict, body: str) -> str:
    """Frontmatter is script-owned: every field comes from the approved
    blueprint row, never from the model — the estimate is re-derived from the
    segment ⏱ values so the sum rule is checked against what was actually
    written, and `verified:` is today because validation against the sources
    happens right after this."""
    body = _strip_fence(body)
    parsed = ln.parse("---\ntype: module\n---\n\n" + body)
    total = sum(s["minutes"] for s in parsed["segments"]) or row["est"]
    d = {"type": "module", "course": course, "module": row["n"],
         "unit": row["unit"], "title": row["title"], "estimate": total,
         "sources": list(row["sources"]),
         "verified": datetime.date.today().isoformat(),
         "tags": "[guide]", "preamble": "", "segments": parsed["segments"]}
    return ln.serialize(d)


CHECKPOINT_PROMPT = """You are authoring checkpoint CP{n} of {course}'s study
guide — a practice-only assessment over modules {covered}. Mine the QUESTION
SHAPES from the course's own assessment notes pasted below (what the course
actually asks), grounded in the module material.

Write ONLY the checkpoint BODY (no frontmatter, no H1):

## S1 · <topic group> ⏱ <minutes>
source:: <one of the source paths below>

?? q-cp{n}-1 · <kind>
<the question; for mcq include options A)–D)>
- hint:: <optional>
- answer:: <the answer>
- solution:: <the worked solution>

Rules (a parser rejects violations mechanically):
- 1-4 segments grouping the items by topic; each needs a `source::` line
  chosen ONLY from:
{sources}
- PRACTICE ONLY: no `### Summary`, `### Normal`, `### In depth`, and no
  `### Example` anywhere — any of those holds the checkpoint.
- 6-12 items, ids q-cp{n}-1, q-cp{n}-2, ... unique, kind one of:
  mcq | numeric | short | code | proof.
- Every item has `- answer::` and `- solution::` with real content.
- Use `·` (U+00B7) and `⏱` exactly as shown.

Assessment and module notes:

{material}
"""


def author_checkpoint(vault: Path, course: str, row: dict, bp: dict,
                      compose=None) -> tuple[str | None, list[str]]:
    folder = ln.course_folder(vault, course)
    code = folder.name.lower()
    # sources: the covered modules' notes, plus the course's own assessment
    # notes — the shapes a checkpoint mines (§13.4)
    src_rels = []
    for r in bp["rows"]:
        if r["kind"] == "module" and r["n"] in row["covers"]:
            base = module_basename(code, r["n"], r["title"])
            rel = f"02-Areas/Academics/{folder.name}/guide/{base}.md"
            if (vault / rel).is_file():
                src_rels.append(rel)
    exam_notes = [n for n in course_notes(vault, folder)
                  if n["type"] in ("exam-prep", "assignment")][:6]
    src_rels += [n["rel"] for n in exam_notes]
    if not src_rels:
        return None, ["no covered module notes or assessment notes to mine"]
    material = "\n\n".join(f"--- {src} ---\n{_cap(_read_rel(vault, src), 8000)}"
                           for src in src_rels)
    covered = ", ".join(f"M{c:02}" for c in row["covers"])
    prompt = CHECKPOINT_PROMPT.format(course=folder.name, n=row["n"],
                                      covered=covered,
                                      sources="\n".join(f"  - {s}" for s in src_rels),
                                      material=material)
    call = compose or (lambda p: call_model(p, MODEL, timeout=JOB_TIMEOUT,
                                            actor="guide"))
    errs: list[str] = ["no reply"]
    for attempt in (1, 2):
        out = call(prompt)
        if _just_rate_limited():
            return None, ["rate limited"]
        text = _assemble_checkpoint(folder.name, row, _strip_fence(out or ""))
        errs = ln.validate_any(text, vault=vault)
        if not errs:
            return text, []
        if attempt == 1:
            prompt = (prompt + "\n\nYour previous attempt failed validation:\n"
                      + "\n".join(f"- {e}" for e in errs[:12])
                      + "\n\nWrite the corrected checkpoint body again, in full.")
            log(f"CP{row['n']} attempt 1 failed validation ({len(errs)}) — repairing")
    return None, errs


def _assemble_checkpoint(course: str, row: dict, body: str) -> str:
    covers = ", ".join(str(c) for c in row["covers"])
    title = row.get("title") or f"Checkpoint {row['n']}"
    fm = ("---\n"
          "type: checkpoint\n"
          f"course: {course}\n"
          f"checkpoint: {row['n']}\n"
          f"covers: [{covers}]\n"
          "date:\n"
          "tags: [guide]\n"
          "---\n\n"
          f"# {title}\n\n")
    return fm + body.rstrip("\n") + "\n"


# --------------------------------------------------------------------------
# the run — sequenced like the fleet, resumable from the notes on disk
# --------------------------------------------------------------------------
def _job_rel(vault: Path, course: str, row: dict) -> str:
    folder = ln.course_folder(vault, course)
    code = folder.name.lower()
    if row["kind"] == "module":
        base = module_basename(code, row["n"], row["title"])
    else:
        base = f"{code}-checkpoint-{row['n']}"
    return f"02-Areas/Academics/{folder.name}/guide/{base}.md"


def run(course: str, vault: Path = DEFAULT_VAULT, compose=None) -> int:
    folder = ln.course_folder(vault, course)
    if folder is None:
        print(f"no course folder for {course!r} under 02-Areas/Academics/")
        return 1
    course = folder.name

    with Lock() as lock:
        if not lock.held:
            print("a generation run is already holding guide.lock — not starting")
            return 1
        refusal = window_refusal()
        if refusal:
            print(refusal)
            write_progress({"state": "blocked", "course": course, "note": refusal,
                            "queue": [], "current": None, "results": {}})
            return 1

        run_id = f"gen-{datetime.datetime.now():%Y%m%d-%H%M%S}-{course.lower()}"
        started = datetime.datetime.now()
        bp = ln.load_blueprint(vault, course)

        if bp is None:
            prog = {"state": "running", "course": course, "run_id": run_id,
                    "phase": "blueprint", "queue": ["blueprint"],
                    "current": "blueprint",
                    "current_started": started.isoformat(timespec="seconds"),
                    "run_started": started.isoformat(timespec="seconds"),
                    "results": {}, "note": None, "resume_at": None,
                    "finished": None}
            write_progress(prog)
            print(f"blueprint pass for {course} — planning from the course folder")
            r = blueprint_pass(vault, course, run_id, compose=compose)
            ok = r.get("action") in ("create", "update")
            prog["results"]["blueprint"] = {
                "ok": ok, "action": r.get("action"),
                "note": r.get("reason"),
                "seconds": int((datetime.datetime.now() - started).total_seconds())}
            prog["current"] = None
            if r.get("paused"):
                prog.update(state="paused", note="window exhausted",
                            resume_at=_resume_estimate())
            elif ok:
                prog.update(state="done",
                            note="blueprint drafted — edit it, then set "
                                 "status: approved to generate")
            else:
                prog.update(state="failed", note=r.get("reason"))
            prog["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
            write_progress(prog)
            print(prog["note"] or "done")
            return 0 if ok else 1

        if bp["status"] != "approved":
            note = (f"{course}'s blueprint is {bp['status'] or 'unstated'!r} — "
                    f"edit {bp['file']} and set status: approved to generate")
            print(note)
            write_progress({"state": "blocked", "course": course, "note": note,
                            "queue": [], "current": None, "results": {}})
            return 0
        if bp["problems"]:
            note = (f"the approved blueprint fails validation "
                    f"({len(bp['problems'])}) — e.g. {bp['problems'][0]}")
            print(note)
            write_progress({"state": "blocked", "course": course, "note": note,
                            "queue": [], "current": None, "results": {}})
            return 1

        # approval already happened — make the plan reachable (§13.2), then
        # author what is missing, in plan order, one commit per note
        reconcile_chain(vault, course, bp, run_id)
        reconcile_index(vault, course, bp, run_id)

        have_m, have_c = ln.existing_units(vault, course)
        todo = [r for r in bp["rows"]
                if (r["n"] not in have_m if r["kind"] == "module"
                    else r["n"] not in have_c)]
        labels = [f"M{r['n']:02}" if r["kind"] == "module" else f"CP{r['n']}"
                  for r in todo]
        prog = {"state": "running", "course": course, "run_id": run_id,
                "phase": "modules", "queue": labels, "current": None,
                "current_started": None,
                "run_started": started.isoformat(timespec="seconds"),
                "results": {}, "note": None, "resume_at": None, "finished": None}
        write_progress(prog)
        if not todo:
            prog.update(state="done", note="nothing missing — the plan is "
                                           "fully authored")
            prog["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
            write_progress(prog)
            print(prog["note"])
            return 0
        print(f"{course}: {len(todo)} of {len(bp['rows'])} planned notes "
              f"missing — authoring in plan order")

        consecutive_failures = 0
        paused = False
        for row, label in zip(todo, labels):
            job_started = datetime.datetime.now()
            prog.update(current=label,
                        current_started=job_started.isoformat(timespec="seconds"))
            write_progress(prog)
            rel = _job_rel(vault, course, row)

            waiting = _pending_proposal_for(rel)
            if waiting:
                prog["results"][label] = {"ok": False, "action": "waiting",
                                          "note": f"unresolved proposal {waiting}",
                                          "seconds": 0}
                prog["current"] = None
                write_progress(prog)
                print(f"{label}: skipped — {waiting} is still waiting on you")
                continue
            if row["kind"] == "checkpoint":
                have_m, _ = ln.existing_units(vault, course)
                missing_cov = [c for c in row["covers"] if c not in have_m]
                if missing_cov:
                    prog["results"][label] = {
                        "ok": False, "action": "deferred",
                        "note": "waiting on " + ", ".join(f"M{c:02}"
                                                          for c in missing_cov),
                        "seconds": 0}
                    prog["current"] = None
                    write_progress(prog)
                    print(f"{label}: deferred — its modules are not authored yet")
                    continue

            print(f"{label}: authoring …")
            if row["kind"] == "module":
                text, errs = author_module(vault, course, row, compose=compose)
            else:
                text, errs = author_checkpoint(vault, course, row, bp,
                                               compose=compose)
            seconds = int((datetime.datetime.now() - job_started).total_seconds())

            if errs == ["rate limited"]:
                prog["results"][label] = {"ok": False, "action": "paused",
                                          "note": "rate limited", "seconds": seconds}
                paused = True
            elif text is None:
                prog["results"][label] = {"ok": False, "action": "failed",
                                          "note": "; ".join(errs[:3]),
                                          "seconds": seconds}
                consecutive_failures += 1
                log(f"{label} failed: " + "; ".join(errs[:6]))
                print(f"{label}: failed validation twice — see guide.log")
            else:
                title = (row["title"] if row["kind"] == "module"
                         else row.get("title") or f"Checkpoint {row['n']}")
                r = _propose_and_apply(
                    f"{course} {label} · {title}", rel, text,
                    f"Authored by the S7 module pass from the approved "
                    f"blueprint's sources, validated before proposing.",
                    run_id)
                ok = r.get("action") in ("create", "update")
                prog["results"][label] = {"ok": ok, "action": r.get("action"),
                                          "note": r.get("reason"),
                                          "seconds": seconds}
                consecutive_failures = 0 if ok else consecutive_failures
                if r.get("action") == "held":
                    print(f"{label}: HELD — {r.get('reason')}")
                else:
                    print(f"{label}: {r.get('action')} "
                          f"({(r.get('sha') or 'no commit')[:10]}) · {seconds}s")
            prog["current"] = None
            write_progress(prog)

            if paused:
                prog.update(state="paused", note="window exhausted",
                            resume_at=_resume_estimate())
                prog["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
                write_progress(prog)
                print(f"paused on the rate limit — resumes ~{prog['resume_at']}; "
                      f"re-run to pick up at the next missing note")
                return 0
            if consecutive_failures >= 2:
                prog.update(state="failed",
                            note="two consecutive jobs failed — stopping; "
                                 "see guide.log")
                prog["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
                write_progress(prog)
                print(prog["note"])
                return 1

        done = sum(1 for v in prog["results"].values() if v["ok"])
        held = sum(1 for v in prog["results"].values() if v["action"] == "held")
        prog.update(state="done",
                    note=f"{done} of {len(todo)} authored"
                         + (f" · {held} held" if held else ""))
        prog["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
        write_progress(prog)
        print(prog["note"])
        return 0


def _resume_estimate() -> str | None:
    try:
        return spend.resume_estimate()
    except Exception:
        return None


# --------------------------------------------------------------------------
# status — what a human or the palette asks first
# --------------------------------------------------------------------------
def status(vault: Path = DEFAULT_VAULT) -> int:
    try:
        prog = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prog = None
    if prog:
        print(f"last run: {prog.get('state')} · {prog.get('course')} · "
              f"{prog.get('note') or ''}".rstrip(" ·"))
    root = vault.joinpath(*ln.ACADEMICS)
    if not root.is_dir():
        return 0
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        bp = ln.load_blueprint(vault, d.name)
        if bp is None:
            continue
        have_m, have_c = ln.existing_units(vault, d.name)
        missing = sum(1 for r in bp["rows"]
                      if (r["n"] not in have_m if r["kind"] == "module"
                          else r["n"] not in have_c))
        probs = f" · {len(bp['problems'])} problem(s)" if bp["problems"] else ""
        print(f"{d.name}: blueprint {bp['status'] or '?'} · "
              f"{len(bp['rows'])} planned · {missing} missing{probs}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="the S7 study-guide generation "
                                             "pipeline")
    ap.add_argument("course", nargs="?", help="course code, e.g. AA-210")
    ap.add_argument("--status", action="store_true",
                    help="blueprint + coverage per course, and the last run")
    a = ap.parse_args(argv)
    if a.status or not a.course:
        return status()
    return run(a.course)


if __name__ == "__main__":
    sys.exit(main())
