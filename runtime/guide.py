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
                info = self._read() or {}
                age = _hours_since(info.get("at"))
                if (age is not None and age < self.stale_hours
                        and self._alive(info.get("pid"))):
                    return self
                log(f"taking over a stale lock ({age:.1f}h old)" if age is not None
                    and age >= self.stale_hours
                    else "taking over a dead holder's lock")
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

    @staticmethod
    def _alive(pid) -> bool:
        """Is the lock's holder still a running process? The palette slot's
        timeout and the server's shutdown hook both kill the tree with
        `taskkill /T /F`, which skips __exit__ — without this probe the
        leftover lock blocked the documented resume for up to the 2h
        staleness window. Anything uncertain reads as alive (the
        conservative direction); os.kill is never used, because on Windows
        any signal but CTRL_* unconditionally TERMINATES the target."""
        if not pid:
            return True
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            import ctypes.wintypes
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
            if not handle:
                return False
            # An exited process stays openable while anything holds a handle
            # to it — the exit code, not the handle, says whether it lives.
            code = ctypes.wintypes.DWORD()
            ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
            k32.CloseHandle(handle)
            return not ok or code.value == 259          # STILL_ACTIVE
        except Exception:
            return True

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


def _call_safe(call, prompt: str) -> str | None:
    """A model call must cost a failed job, never the run (retro.py's own
    pattern). subprocess.TimeoutExpired from `claude -p` — or any transport
    error — comes back as None; the job records a failure and the pipeline
    keeps its progress record consistent instead of dying with a traceback
    that leaves state:"running" frozen on disk."""
    try:
        return call(prompt)
    except Exception as e:
        log(f"model call raised: {type(e).__name__}: {e}")
        return None


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


REDO_RE = re.compile(r"^\s*(M|CP)\s*0*(\d+)\s*$", re.I)


def parse_redo(spec: str) -> tuple[set[int], set[int], list[str]]:
    """`"M02,CP1"` → ({2}, {1}, []). The third element is whatever did not
    parse, returned rather than raised: a typo in one name should not decide
    the fate of the others silently, and the caller refuses the whole run."""
    mods: set[int] = set()
    cps: set[int] = set()
    bad: list[str] = []
    for part in (spec or "").split(","):
        if not part.strip():
            continue
        m = REDO_RE.match(part)
        if not m:
            bad.append(part.strip())
        elif m.group(1).upper() == "M":
            mods.add(int(m.group(2)))
        else:
            cps.add(int(m.group(2)))
    return mods, cps, bad


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
    # The prompt's own example sanctions `"unit": null`, and a null-unit row
    # in a united plan used to serialize as a literal `## Unit None` heading
    # that hid every row beneath it from the parser (S7 review, verified by
    # execution). Normalise before serialising: a checkpoint inherits the
    # unit of the last module it covers; anything else carries the previous
    # row's unit forward.
    if units:
        last = None
        for row in rows:
            if row["unit"] is None:
                if row["kind"] == "checkpoint" and row.get("covers"):
                    cu = [m["unit"] for m in rows
                          if m["kind"] == "module" and m["n"] in row["covers"]
                          and m["unit"] is not None]
                    row["unit"] = max(cu) if cu else last
                else:
                    row["unit"] = last
            last = row["unit"]
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
    waiting = _pending_proposal_for(rel)
    if waiting:
        return {"action": "waiting",
                "reason": f"unresolved proposal {waiting} already targets the "
                          f"blueprint — resolve it before drafting again"}
    call = compose or (lambda p: call_model(p, MODEL, timeout=BLUEPRINT_TIMEOUT,
                                            actor="guide"))
    errs: list[str] = ["no reply"]
    for attempt in (1, 2):
        out = _call_safe(call, prompt)
        if _just_rate_limited():
            return {"action": "failed", "reason": "rate limited", "paused": True}
        data = parse_model_json(out) if out is not None else None
        if out is None:
            errs = ["the model call failed or timed out — see guide.log"]
        elif data is None:
            errs = ["the reply was not parseable JSON"]
        else:
            d = _blueprint_rows_to_dict(folder.name, data)
            text = ln.serialize_blueprint(d)
            errs = ln.validate_blueprint(text, vault=vault)
            # Never write something the scanner cannot read (§13) includes
            # never LOSING something the scanner cannot see: the reparse must
            # account for every planned row, or a heading broke the grammar.
            lost = len(d["rows"]) - len(ln.parse_blueprint(text)["rows"])
            if lost:
                errs.append(f"round trip lost {lost} plan row(s) — a heading "
                            f"broke the row grammar")
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
        # EVERY row not re-emitted in the block survives, after it — not just
        # rows whose label the plan omits. Filtering by label deleted a
        # hand-written second row that merely began with a planned label
        # ("- [ ] M01 redo the derivation") — a scripted deletion of Zach's
        # content, the S7 review's reproduction. Nothing in this vault
        # deletes a record; the reconcile re-orders and adds, only.
        chosen = {row_at[lab] for lab, _ in desired if lab in row_at}
        extras = [lines[i] for i in row_idx if i not in chosen]
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
    # The two notes study mode writes *outside* the plan. Linked only once they
    # exist, unlike the planned modules below: a planned module is a promise the
    # blueprint made, while these two appear the first time a session is rolled
    # up, and a link waiting for a note that may never be written is the
    # dangling graph node the contract's linking rules forbid.
    for base, label in ((f"{code}-study-log", "the study sessions"),
                        (f"{code}-recall", "recall cards raised from misses")):
        if (folder / f"{base}.md").is_file():
            wanted.append((base, label))
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

## Mathematics — LaTeX, never Unicode art

Write every formula as LaTeX: `$…$` inline, `$$…$$` on its own lines for a
displayed equation. This is the vault's existing convention — Obsidian renders
it natively, and Zach reads these notes in Obsidian.

    Inline:   the magnitude $\\lVert \\mathbf{{V}} \\rVert$ is a scalar
    Display:  $$\\lVert \\mathbf{{V}} \\rVert = \\sqrt{{V_x^2 + V_y^2 + V_z^2}}$$

**Do NOT** build formulas out of Unicode symbols and spacing — no `√`, `²`, `‖`,
`ₓ`, `θ` in a plain-text block, and never a division written as a row of
hyphens with the numerator above it. That renders as clunky monospace in the
reader and cannot express a fraction, an integral or a matrix at all. Vectors
are `\\mathbf{{v}}`, norms are `\\lVert v \\rVert`, and units stay in prose
("$12$ N·m" is fine; the unit does not need to be inside the maths).

## Figures — draw one when the source describes an arrangement

A segment may carry ONE diagram, written directly after its `source::` lines:

    figure:: Free-body diagram of the bracket at O
    <svg viewBox="0 0 260 170" role="img" aria-label="A bracket pinned at O with a 40 N force applied 0.3 m along the arm">
      <line x1="30" y1="120" x2="200" y2="120" stroke="currentColor" stroke-width="2"/>
      <circle cx="30" cy="120" r="4" fill="currentColor"/>
      <text x="22" y="140" font-size="11" fill="currentColor">O</text>
    </svg>

Do this when the material describes something physical or spatial — a free-body
diagram, a loaded beam, an axis set, a geometric construction. Skip it when the
idea is not spatial; a diagram of nothing is worse than no diagram.

Hard rules, all enforced by a validator:
- The `<svg>` is **raw and unfenced** (so Obsidian draws it) and must be
  **well-formed XML** — every element closed, every attribute quoted.
- `viewBox` is required. Do not set `width` or `height` on the root.
- Stroke and fill with `currentColor` so the diagram inherits the reader's ink.
- Allowed elements ONLY: {tags}.
- No `<script>`, `<image>`, `<use>`, `<a>`, `<foreignObject>`, no `href`, no
  `on*` handler, no `url(...)` — a figure draws, it does not fetch or script.
- Keep it under about 40 elements, and label the parts with `<text>`.
- **Label the way the label is read.** SVG cannot hold LaTeX, so a `<text>`
  label is the one place in a note where maths is written as characters:
  use `θ`, `Aₐ`, `A⊥`, `F₁`, `α`, `Σ` — never `theta`, `A_a`, `A_perp`, `F_1`.
  An underscore in a diagram reads as source code, not as a subscript.
- The caption after `figure::` says what it shows; the `aria-label` describes it
  for someone who cannot see it. Both are required to be useful sentences. The
  `aria-label` is prose read aloud, so spell the symbols out there ("the angle
  theta between A and B") rather than repeating the glyphs.

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
                                  # Straight from the validator, so the prompt
                                  # cannot describe a different allow-list from
                                  # the one that will judge the answer.
                                  tags=", ".join(sorted(ln.SVG_TAGS)),
                                  material=material)
    call = compose or (lambda p: call_model(p, MODEL, timeout=JOB_TIMEOUT,
                                            actor="guide"))
    errs: list[str] = ["no reply"]
    for attempt in (1, 2):
        out = _call_safe(call, prompt)
        if _just_rate_limited():
            return None, ["rate limited"]
        if not (out or "").strip():
            errs = ["the model call failed, timed out, or returned nothing"]
        else:
            text, raw_problems = _assemble_module(folder.name, row, out)
            # The raw body's parse problems come FIRST: the parser flags and
            # drops malformed content (a `??q-` opener and everything under
            # it, prose after an item's keyed lines), so the canonical text
            # can validate clean while a question the model wrote has already
            # vanished. Laundering those problems away was the S7 review's
            # top finding — what the parser dropped is a repair, never a
            # silent deletion.
            errs = raw_problems + ln.validate_any(text, vault=vault)
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


def _assemble_module(course: str, row: dict, body: str) -> tuple[str, list[str]]:
    """(canonical note text, the RAW body's parse problems). Frontmatter is
    script-owned: every field comes from the approved blueprint row, never
    from the model — the estimate is re-derived from the segment ⏱ values so
    the sum rule is checked against what was actually written, and
    `verified:` is today because validation against the sources happens right
    after this. The raw problems ride along because serialising re-emits only
    what the parser KEPT — content the parser flagged and dropped must fail
    the job, not vanish from the committed note."""
    body = _strip_fence(body)
    parsed = ln.parse("---\ntype: module\n---\n\n" + body)
    total = sum(s["minutes"] for s in parsed["segments"]) or row["est"]
    d = {"type": "module", "course": course, "module": row["n"],
         "unit": row["unit"], "title": row["title"], "estimate": total,
         "sources": list(row["sources"]),
         "verified": datetime.date.today().isoformat(),
         "tags": "[guide]", "preamble": "", "segments": parsed["segments"]}
    return ln.serialize(d), list(parsed["problems"])


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

## Mathematics — LaTeX, never Unicode art

Every formula is LaTeX: `$…$` inline, `$$…$$` displayed. This is the vault's
own convention and Obsidian renders it natively.

    $$\\lVert \\mathbf{{V}} \\rVert = \\sqrt{{V_x^2 + V_y^2 + V_z^2}}$$

Never assemble a formula out of `√`, `²`, `‖`, `ₓ` and spacing, and never write
a division as a row of hyphens — it renders as clunky monospace and cannot
express a fraction or an integral at all.

## Figures

A segment may carry ONE diagram after its `source::` line, when a question needs
to show an arrangement — a loaded beam, a bracket, an axis set:

    figure:: The bracket for questions 1-3
    <svg viewBox="0 0 260 170" role="img" aria-label="…">…</svg>

Raw and unfenced, well-formed XML, `viewBox` required, stroke and fill with
`currentColor`, allowed elements ONLY: {tags}. No `<script>`, `<image>`,
`<use>`, `<a>`, `<foreignObject>`, no `href`, no `on*`, no `url(...)`.

`<text>` labels cannot hold LaTeX, so write them as they are read — `θ`, `Aₐ`,
`A⊥`, `F₁` — never `theta`, `A_a`, `A_perp`, `F_1`.

Assessment and module notes:

{material}
"""


def author_checkpoint(vault: Path, course: str, row: dict, bp: dict,
                      compose=None) -> tuple[str | None, list[str]]:
    folder = ln.course_folder(vault, course)
    # sources: the covered modules' notes, plus the course's own assessment
    # notes — the shapes a checkpoint mines (§13.4). Covered modules are
    # found by their frontmatter identity through the same scan the deferral
    # gate uses, never by reconstructing a filename from the blueprint title:
    # hand-authored modules are explicitly the resume record, and a renamed
    # or hand-named note must feed its own checkpoint.
    src_rels = [m["file"] for m in ln.scan(vault)
                if m["course"].lower() == folder.name.lower()
                and m["module"] in row["covers"]]
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
                                      tags=", ".join(sorted(ln.SVG_TAGS)),
                                      material=material)
    call = compose or (lambda p: call_model(p, MODEL, timeout=JOB_TIMEOUT,
                                            actor="guide"))
    errs: list[str] = ["no reply"]
    for attempt in (1, 2):
        out = _call_safe(call, prompt)
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


def run(course: str, vault: Path = DEFAULT_VAULT, compose=None,
        redo: str | None = None) -> int:
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
                # The same pause the module loop exits 0 on: a designed,
                # resumable stop, never a failure to a scripted caller.
                prog.update(state="paused", note="window exhausted",
                            resume_at=_resume_estimate())
            elif r.get("action") == "waiting":
                prog.update(state="blocked", note=r.get("reason"))
            elif ok:
                prog.update(state="done",
                            note="blueprint drafted — edit it, then set "
                                 "status: approved to generate")
            else:
                prog.update(state="failed", note=r.get("reason"))
            prog["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
            write_progress(prog)
            print(prog["note"] or "done")
            return 0 if (ok or r.get("paused")
                         or r.get("action") == "waiting") else 1

        # The approval gate guards *creation*, and only creation. The contract
        # says so outright — "a new module or checkpoint absent from the
        # approved blueprint is held by the applier … while an update to an
        # existing note needs only the grammar" — and `applier.apply_one`
        # implements exactly that, running `_blueprint_hold` under
        # `if ctype in (...) and not existed`. So --redo, which refuses to
        # create anything (checked below), does not need the plan approved. It
        # is re-writing notes whose existence was authorised long ago.
        if bp["status"] != "approved" and not redo:
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

        have_m, have_c = ln.existing_units(vault, course)

        if redo:
            want_m, want_c, bad = parse_redo(redo)
            if bad:
                note = (f"--redo: cannot read {', '.join(repr(b) for b in bad)} "
                        f"— names look like M02 or CP1")
                print(note)
                return 2
            todo = [r for r in bp["rows"]
                    if (r["n"] in want_m if r["kind"] == "module"
                        else r["n"] in want_c)]
            # Refuse to CREATE. This is the line that lets --redo skip the
            # approval gate above: it only ever rewrites notes that already
            # exist, so nothing here can put an unapproved module on disk.
            absent = [f"M{r['n']:02}" if r["kind"] == "module" else f"CP{r['n']}"
                      for r in todo
                      if (r["n"] not in have_m if r["kind"] == "module"
                          else r["n"] not in have_c)]
            if absent:
                note = (f"--redo only re-authors notes that exist; "
                        f"{', '.join(absent)} do(es) not. Approve the blueprint "
                        f"and run without --redo to create them.")
                print(note)
                return 2
            named = sorted(f"M{n:02}" for n in want_m) + sorted(f"CP{n}" for n in want_c)
            found = {f"M{r['n']:02}" if r["kind"] == "module" else f"CP{r['n']}"
                     for r in todo}
            unknown = [n for n in named if n not in found]
            if unknown:
                note = (f"--redo: {', '.join(unknown)} is not a row in "
                        f"{course}'s blueprint — the plan is what supplies the "
                        f"title, estimate and sources to re-author from")
                print(note)
                return 2
        else:
            # approval already happened — make the plan reachable (§13.2), then
            # author what is missing, in plan order, one commit per note.
            # GitBusy degrades to a blocked record, exactly as apply_one
            # degrades the same exception to a held proposal — never a
            # traceback that leaves the previous run's progress on screen.
            #
            # Deliberately NOT reached by --redo: reconciling writes every
            # planned row into the chain and the course index, and doing that
            # from a *draft* blueprint would publish 27 unapproved modules as
            # links — precisely what the gate above exists to prevent.
            try:
                reconcile_chain(vault, course, bp, run_id)
                reconcile_index(vault, course, bp, run_id)
            except gitops.GitBusy as e:
                note = (f"the git mutex is busy ({e}) — nothing written; "
                        f"re-run when it frees")
                print(note)
                write_progress({"state": "blocked", "course": course,
                                "note": note, "queue": [], "current": None,
                                "results": {}})
                return 1

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
            prog.update(state="done",
                        note="nothing named to re-author" if redo else
                             "nothing missing — the plan is fully authored")
            prog["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
            write_progress(prog)
            print(prog["note"])
            return 0
        if redo:
            print(f"{course}: re-authoring {len(todo)} existing note(s) — "
                  f"each overwrites its file in one revertible commit")
        else:
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
    ap.add_argument("--redo", metavar="ROWS",
                    help="re-author notes that ALREADY exist, e.g. "
                         "'M02,CP1'. Each overwrites its file in one revertible "
                         "commit. Refuses to create anything, and leaves the "
                         "chain and course index alone — so it does not need "
                         "the blueprint approved.")
    a = ap.parse_args(argv)
    if a.status or not a.course:
        if a.redo:
            print("--redo needs a course, e.g. `sigma guide AA-210 --redo M02`")
            return 2
        return status()
    return run(a.course, redo=a.redo)


if __name__ == "__main__":
    sys.exit(main())
