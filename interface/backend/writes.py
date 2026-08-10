#!/usr/bin/env python3
"""
writes.py — the dashboard's write API (Phase 4 of the dashboard plan).

Three endpoints, and the first two things this process has ever been allowed
to change:

    POST /api/tasks/toggle     tick/untick one checkbox, surgically
    GET  /api/activity         the ledger — everything Sigma changed, newest first
    POST /api/activity/revert  git-revert one ledger commit, in one click

The toggle is Zach acting through the UI, not an agent acting for him — the
guardrail "never tick a checkbox on Zach's behalf" is about agents, and the
ledger records the actor as `zach` so the distinction survives in the record.

Every write here goes through sigma.gitops: mutex, pull-before-write, a
path-scoped commit whose SHA lands in the ledger. The toggle is line-verified
— the client sends the exact line it saw, and if the note moved underneath it
(obsidian-git pulls every 15 minutes) the answer is 409 stale, never a write
to the wrong line.
"""
import asyncio
import datetime
import json
import re
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agent import VAULT
from privacy import sealed_paths

_RUNTIME = str(Path(__file__).resolve().parents[2] / "runtime")
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
from sigma import call_model, gitops, ledger, parse_model_json, write_note  # noqa: E402
import agenda as ag  # noqa: E402  — the calendar grammar and its serialiser
import leetcode as lc  # noqa: E402  — the practice habit's own write path
import lesson as ln  # noqa: E402  — the guide grammar and the practice sidecars
import recall as rc  # noqa: E402  — the card grammar and the measured pace
import todo as td  # noqa: E402  — section inference, destinations, line grammar

import commands  # noqa: E402  — the one spend-window policy, not a second copy
import panels  # noqa: E402  — to drop its caches after a write

router = APIRouter(prefix="/api")

# How long the reword may take. Measured, not guessed: `claude -p --model haiku`
# answering "reply with only the word OK" takes ~16s on this machine, because it
# is a CLI process start rather than an API call.
REWORD_TIMEOUT = 120


class ToggleReq(BaseModel):
    file: str          # vault-relative posix path, as /api/tasks reported it
    line: int          # 1-based line number
    raw: str           # the exact line the client saw — the staleness check
    done: bool = True


class RevertReq(BaseModel):
    sha: str


def _err(status: int, error: str, **extra):
    return JSONResponse({"error": error, **extra}, status_code=status)


def _vault_rel(file: str) -> str | None:
    """Resolve a client-supplied path to a vault-relative posix path, or None.
    Refuses colons outright — a drive letter cannot appear in a relative path,
    and an NTFS `::$DATA` stream must never reach the write path."""
    if not file or ":" in file:
        return None
    try:
        p = (VAULT / file).resolve()
        return p.relative_to(VAULT.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def _flip(line: str, done: bool) -> str | None:
    """Flip exactly the checkbox in a task line, or None if there isn't one."""
    m = re.match(r"^(\s*[-*]\s+)\[( |x|X)\](.*)$", line)
    if not m:
        return None
    return f"{m.group(1)}[{'x' if done else ' '}]{m.group(3)}"


@router.post("/tasks/toggle")
def api_toggle(req: ToggleReq):
    rel = _vault_rel(req.file)
    if rel is None:
        return _err(400, "bad path")
    if rel in sealed_paths(VAULT, [rel]):
        return _err(403, "sealed path")
    dest = VAULT / rel

    flipped = _flip(req.raw, req.done)
    if flipped is None:
        return _err(400, "not a task line")

    try:
        with gitops.vault_write(VAULT) as w:
            # verify AFTER the pull: the whole point is judging the file as it
            # is now, not as it was when the panel rendered.
            try:
                text = dest.read_text(encoding="utf-8")
            except OSError:
                return _err(409, "stale", detail="the note is gone or unreadable")
            segs = text.split("\n")
            i = req.line - 1
            if not (0 <= i < len(segs)):
                return _err(409, "stale", detail="the line number no longer exists")
            had_cr = segs[i].endswith("\r")
            core = segs[i][:-1] if had_cr else segs[i]
            if core != req.raw:
                return _err(409, "stale", detail="the line changed underneath you")
            segs[i] = flipped + ("\r" if had_cr else "")
            # write_note keeps the note's own line endings. write_text would
            # rewrite an LF note as CRLF, so ticking one box showed up in git as
            # every line changing — and buried the tick in the noise.
            write_note(dest, "\n".join(segs))
            res = w.commit(rel, f"zach (dashboard): "
                                f"{'tick' if req.done else 'untick'} task in {rel}")
    except gitops.GitBusy:
        return _err(409, "busy", detail="another Sigma write is in progress — retry")

    summary = f"{'ticked' if req.done else 'unticked'} a task in {rel}"
    ledger.record("zach", "toggle", rel, res["sha"], summary,
                  extra={"line": req.line})
    # Both task panels, not just /api/tasks. The work view's window promotes the
    # next task the moment one pops, and a 15s stale cache would make the queue
    # feel broken at exactly the moment it is meant to feel immediate. Since P2
    # this also drops the resolver's scan, which /api/tasks is now a projection
    # of — popping the panel key alone would recompute from a warm scan and hand
    # back the task that was just ticked.
    panels.drop_task_caches()
    return {"ok": True, "sha": res["sha"], "absorbed": res["absorbed"],
            "note": res["note"], "raw": flipped}


class SkipReq(BaseModel):
    file: str          # vault-relative posix path
    line: int          # 1-based line number
    raw: str           # the exact line the client saw — the staleness check


@router.post("/tasks/skip")
def api_skip(req: SkipReq):
    """Skip one task line: `[ ]` becomes `[-]` plus `skipped::<date>`.

    Deliberately NOT the `/api/queue/meta` archive. That write is index-only —
    no commit, no ledger row, no undo — and an archived head used to park its
    whole chain. This is a line edit like the toggle: one commit, one ledger
    row, one-click undo from the activity ledger, and because TASK_RE matches
    only ` |x|X`, a `[-]` row drops out of the scan entirely — the chain's
    frontier advances, no open box remains, and the note keeps the record.
    Suppression as a decision, visible, never hidden (invariant 6).

    A task-line mutation any chain gets for free, not a study-specific
    mechanism — which is why it lives here beside the toggle rather than in
    a study module (study plan §7/§10).
    """
    rel = _vault_rel(req.file)
    if rel is None:
        return _err(400, "bad path")
    dest = VAULT / rel

    skipped = td.skip_line(req.raw, datetime.date.today().isoformat())
    if skipped is None:
        return _err(400, "not an open task",
                    detail="only an open checkbox line can be skipped")

    try:
        with gitops.vault_write(VAULT) as w:
            # Inside the mutex and after the pull, refusing whether or not the
            # path is model_allow-exempt — the calendar writes' pattern, not
            # the toggle's pre-mutex sealed_paths(): exemption governs what
            # the model may see, never what may be written, and a gitignored
            # path would hand this the only ledger row in Sigma with no
            # commit behind it and no undo.
            sealed = _sealed_inside_mutex(rel)
            if sealed:
                return _err(403, "sealed path", detail=sealed)
            try:
                body = dest.read_text(encoding="utf-8")
            except OSError:
                return _err(409, "stale", detail="the note is gone or unreadable")
            out = td.replace_line(body, req.line, req.raw, skipped)
            if out is None:
                return _err(409, "stale", detail="the line changed underneath you")
            write_note(dest, out)
            res = w.commit(rel, f"zach (dashboard): skip task in {rel}")
            if not res["sha"]:
                # The line is flipped on disk but no commit exists (an
                # obsidian-git index.lock, most likely) — say so rather than
                # ledger a row that /api/activity/revert can never match.
                return _err(500, "commit failed",
                            detail=res["note"] or "no commit was created")
    except gitops.GitBusy:
        return _err(409, "busy", detail="another Sigma write is in progress — retry")

    ledger.record("zach", "skip", rel, res["sha"],
                  f"skipped a task in {rel}", extra={"line": req.line})
    panels.drop_task_caches()
    return {"ok": True, "sha": res["sha"], "absorbed": res["absorbed"],
            "note": res["note"], "raw": skipped}


class AddReq(BaseModel):
    text: str
    section: str | None = None     # explicit override; inferred when absent
    parent: str | None = None
    due: str | None = None         # YYYY-MM-DD
    urgency: str | None = None     # high | medium | low


@router.post("/queue/add")
def api_queue_add(req: AddReq):
    """Quick-add: one typed line becomes a real checkbox in a real note.

    It has to be a note rather than an index row, because that is what makes it
    toggleable, greppable, visible in Obsidian, committed, and undoable from the
    ledger — the sidecar deliberately holds nothing a checkbox can express.

    The destination is decided from the section, and the section is inferred
    from the words unless the client names one. Nothing here calls a model: a
    task must land the instant it is typed, whatever the window is doing.
    """
    text = " ".join((req.text or "").split())
    if not text:
        return _err(400, "empty")
    if len(text) > 500:
        return _err(400, "too long", detail="a task line is not a note")
    if req.due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.due):
        return _err(400, "bad date", detail="expected YYYY-MM-DD")
    if req.urgency not in (None, "high", "medium", "low"):
        return _err(400, "bad urgency")

    section, parent = (req.section, req.parent)
    if section not in td.SECTIONS:
        section, parent = td.infer_section(text, VAULT)
    # Same rule as the edit path: a per-parent section names a queue, not a
    # file. Naming one without a parent used to land the task in Misc and title
    # a freshly created note "None — Tasks", because the fallback that is right
    # for an unclassified task is wrong for an explicit choice. `infer_section`
    # always pairs these sections with a parent, so this only ever catches a
    # client that named the section itself.
    elif section in td.PER_PARENT:
        known = (td.active_courses(VAULT) if section == "courses"
                 else td.active_projects(VAULT))
        if not parent:
            return _err(400, "pick a parent",
                        detail=f"adding to {td.SECTION_TITLE[section]} needs a "
                               f"{td.PARENT_NOUN[section]}")
        if parent not in known:
            return _err(400, "unknown parent",
                        detail=f"no active {td.PARENT_NOUN[section]} named "
                               f"{parent!r}")
    rel, heading = td.destination(section, parent)

    if _vault_rel(rel) != rel:
        return _err(400, "bad path")
    if rel in sealed_paths(VAULT, [rel]):
        return _err(403, "sealed path")

    dest = VAULT / rel
    line = td.compose(text, req.due, req.urgency)

    try:
        with gitops.vault_write(VAULT) as w:
            # Read after the pull, like the toggle: appending to the file as it
            # was when the page rendered would drop whatever arrived since.
            existed = dest.exists()
            if existed:
                body = dest.read_text(encoding="utf-8", errors="replace")
            else:
                title = {"procertus": "ProCertus — Todo",
                         "misc": "Misc"}.get(section) or f"{parent} — Tasks"
                body = td.NEW_NOTE.format(
                    course=parent if section == "courses" else "",
                    title=title, heading=heading)
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_note(dest, td.splice(body, heading, line))
            res = w.commit(rel, f"zach (dashboard): add task to {rel}")
    except gitops.GitBusy as e:
        return _err(409, "busy", detail=f"another Sigma write is in progress ({e})")
    except OSError as e:
        return _err(500, "write failed", detail=str(e))

    ledger.record("zach", "append" if existed else "create", rel, res["sha"],
                  f"added a task: {text[:60]}")
    # A new note is a new node, so the graph is genuinely stale; appending to an
    # existing one is not, and relaying the sky over a one-line append would be
    # a visible jolt for nothing.
    panels.drop_task_caches(*(("graph",) if not existed else ()))
    return {"ok": True, "file": rel, "section": section, "parent": parent,
            "raw": line, "sha": res["sha"], "created_note": not existed}


class PracticeReq(BaseModel):
    n: str                          # the problem number, as typed
    again: bool = False             # log one already solved, as a revisit


@router.post("/practice/log")
def api_practice_log(req: PracticeReq):
    """Enter the number; the habit is met for the day.

    This is the affordance the whole design was pointing at. There is no open
    checkbox to tick — a daily habit is not a repeated task (see
    `runtime/leetcode.py`) — so *typing the number is the completion*, and the
    line it writes is simultaneously the record of what you solved.

    No new write path: `leetcode.add` already runs the mutex → pull → re-check
    → round-trip → commit → ledger sequence, so this endpoint validates, calls
    it, and drops the caches. Actor `zach`, like the checkbox toggle: a human
    typed this, and the ledger's undo should say so.

    A duplicate comes back 409 rather than 400, because it is a conflict with
    state and not a malformed request — the client turns it into the "you did
    this on <date>, log it again?" prompt, which is the point of keeping the
    record at all.
    """
    raw = (req.n or "").strip().lstrip("#")
    if not raw.isdigit():
        return _err(400, "not a number", detail="a LeetCode problem number")
    if len(raw) > 6:
        return _err(400, "not a number", detail="no problem has that many digits")

    rel = lc.LOG_REL
    if _vault_rel(rel) != rel:
        return _err(400, "bad path")
    if rel in sealed_paths(VAULT, [rel]):
        return _err(403, "sealed path")
    if not (VAULT / rel).exists():
        return _err(404, "no log note",
                    detail=f"{rel} does not exist — create it first")

    # The network lookup is inside a thread-free synchronous call with its own
    # timeout, and it degrades to a bare number rather than failing, so a slow
    # leetcode.com costs a plainer line and never the write.
    r = lc.add(raw, vault=VAULT, again=req.again, actor="zach")
    if not r.get("ok"):
        why = r.get("why") or "could not log it"
        if r.get("duplicate"):
            return _err(409, "already solved", detail=why,
                        duplicate=r["duplicate"])
        if "busy" in why:
            return _err(409, "busy", detail=why)
        return _err(500, "write failed", detail=why)

    # Both caches: the queue payload carries `practice`, and the resolver's
    # habit expansion reads the same note the write just changed.
    panels.drop_task_caches()
    return {"ok": True, **{k: v for k, v in r.items() if k != "duplicate"},
            "practice": lc.panel(VAULT)}


class RewordReq(BaseModel):
    text: str


class EditReq(BaseModel):
    file: str          # where the task is now
    line: int
    raw: str           # the exact line the client saw — the staleness check
    text: str          # the new title
    due: str | None = None
    urgency: str | None = None
    section: str | None = None     # a move when it differs from where it is
    parent: str | None = None
    raw_input: str | None = None   # what was originally typed, kept on reword


@router.post("/queue/reword")
async def api_queue_reword(req: RewordReq):
    """Ask a model to tidy one typed line. Zero side effects — it proposes.

    Two rules this endpoint exists inside:

    - **Nothing user-supplied reaches a command line.** That is why
      `sigma new "<description>"` is a disabled palette verb. The text arrives
      in a JSON body and goes to `claude -p` on **stdin**, never argv, so the
      rule holds rather than acquiring an exception.
    - **The window belongs to the fleet.** The same hold the palette enforces
      applies here: rate-limited in the last 45 minutes, or inside the 08:40
      reservation, and this refuses. A suggestion is a luxury; the 09:00 run is
      not.

    call_model is a blocking subprocess, so it runs off the event loop. The
    caller has already saved the task — this can fail, hang or be refused and
    nothing is lost.
    """
    text = " ".join((req.text or "").split())
    if not text:
        return _err(400, "empty")
    if len(text) > 500:
        return _err(400, "too long")

    hold = commands._window_hold()
    if hold:
        return _err(409, "window", detail=hold)

    prompt = td.reword_prompt(text, VAULT)
    try:
        # 45s was too tight and every suggestion timed out. `claude -p` is a CLI
        # cold start, not an API call: a one-word haiku prompt measures ~16s on
        # this machine, so a longer prompt returning JSON has no room under 45.
        # Nothing waits on this — the task is already filed — so the only cost of
        # a generous ceiling is a slot held open, and the only cost of a tight
        # one is the feature never working.
        raw = await asyncio.wait_for(
            asyncio.to_thread(call_model, prompt, "haiku", timeout=REWORD_TIMEOUT,
                              actor="reword"),
            timeout=REWORD_TIMEOUT + 10)
    except (asyncio.TimeoutError, Exception) as e:      # noqa: B014
        return _err(502, "model", detail=f"{type(e).__name__}")

    got = td.parse_reword(parse_model_json(raw), VAULT)
    if not got:
        # An unusable answer is not an error the user has to act on: the task is
        # already filed and unchanged. Say so plainly rather than showing a
        # broken suggestion.
        return {"ok": True, "suggestion": None}
    return {"ok": True, "suggestion": got}


@router.post("/queue/edit")
def api_queue_edit(req: EditReq):
    """Rewrite one task line, moving it between notes if its section changed.

    This is what Accept runs, and what the section fix runs. A move is one
    commit touching both files — two commits would let an undo leave the task
    in neither note or in both.
    """
    src = _vault_rel(req.file)
    if src is None:
        return _err(400, "bad path")
    text = " ".join((req.text or "").split())
    if not text:
        return _err(400, "empty")
    if len(text) > 500:
        return _err(400, "too long")
    if req.due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.due):
        return _err(400, "bad date", detail="expected YYYY-MM-DD")
    if req.urgency not in (None, "high", "medium", "low"):
        return _err(400, "bad urgency")

    section, parent = req.section, req.parent
    if section not in td.SECTIONS:
        section, parent = td.section_of(src)
    # A per-parent section without a parent has no destination of its own, and
    # td.destination falls through to misc when asked for one. That fallback is
    # right for an *unclassified* task and wrong for an explicit move: asking to
    # move something to Courses and silently landing it in Misc is worse than a
    # refusal, and when the task was already in Misc the computed destination
    # equalled the source, so the move became a no-op edit that reported success
    # and changed nothing. Refuse instead, and name the missing choice.
    if section in td.PER_PARENT:
        known = (td.active_courses(VAULT) if section == "courses"
                 else td.active_projects(VAULT))
        if not parent:
            return _err(400, "pick a parent",
                        detail=f"moving to {td.SECTION_TITLE[section]} needs a "
                               f"{td.PARENT_NOUN[section]}")
        if parent not in known:
            return _err(400, "unknown parent",
                        detail=f"no active {td.PARENT_NOUN[section]} named "
                               f"{parent!r}")
    dst, heading = td.destination(section, parent)
    if _vault_rel(dst) != dst:
        return _err(400, "bad path")
    moving = dst != src
    if set(sealed_paths(VAULT, [src, dst])):
        return _err(403, "sealed path")

    line = td.compose(text, req.due, req.urgency)
    src_p, dst_p = VAULT / src, VAULT / dst

    try:
        with gitops.vault_write(VAULT) as w:
            try:
                body = src_p.read_text(encoding="utf-8")
            except OSError:
                return _err(409, "stale", detail="the note is gone or unreadable")
            if not moving:
                out = td.replace_line(body, req.line, req.raw, line)
                if out is None:
                    return _err(409, "stale", detail="the line changed underneath you")
                write_note(src_p, out)
                touched = [src]
            else:
                cut = td.unsplice(body, req.line, req.raw)
                if cut is None:
                    return _err(409, "stale", detail="the line changed underneath you")
                existed = dst_p.exists()
                dst_body = (dst_p.read_text(encoding="utf-8", errors="replace")
                            if existed else td.NEW_NOTE.format(
                                course=parent if section == "courses" else "",
                                title={"procertus": "ProCertus — Todo",
                                       "misc": "Misc"}.get(section)
                                      or f"{parent} — Tasks",
                                heading=heading))
                write_note(src_p, cut)
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                write_note(dst_p, td.splice(dst_body, heading, line))
                touched = [src, dst]
            res = w.commit(touched, f"zach (dashboard): "
                                    f"{'move' if moving else 'edit'} task in {dst}")
    except gitops.GitBusy as e:
        return _err(409, "busy", detail=f"another Sigma write is in progress ({e})")
    except OSError as e:
        return _err(500, "write failed", detail=str(e))

    # Identity keys on (file, cleaned text), so a reword mints a new id. Seed it
    # with what was originally typed before the next scan adopts the line, or
    # the raw input is lost the moment the suggestion is accepted.
    new_id = td.task_id(dst, text)
    if req.raw_input:
        td.set_meta({new_id: {"raw_input": " ".join(req.raw_input.split())[:500]}})

    ledger.record("zach", "update", dst, res["sha"],
                  f"{'moved' if moving else 'edited'} a task: {text[:60]}")
    panels.drop_task_caches()
    return {"ok": True, "file": dst, "section": section, "parent": parent,
            "raw": line, "id": new_id, "moved": moving, "sha": res["sha"]}


class MetaReq(BaseModel):
    id: str
    snooze: str | None = None      # "YYYY-MM-DD" to defer, "" to wake
    pin: bool | None = None
    archive: bool | None = None
    # None everywhere means "leave it alone" — a partial update must not clear
    # the fields it did not mention.


@router.post("/queue/meta")
def api_queue_meta(req: MetaReq):
    """Snooze, pin, archive. Index-only, and deliberately not a git write.

    These are the three things a checkbox genuinely cannot say. Everything else
    a task carries — its title, its deadline, its urgency, which queue it is in —
    is expressible in the note, so changing those goes through /queue/edit and
    lands as a commit. Writing them here instead would give the note and the
    sidecar two different answers to the same question.

    Which is also why nothing here is in the ledger: the ledger indexes commits
    so they can be reverted, and there is no commit to revert. Undo is setting
    it back.
    """
    if not req.id:
        return _err(400, "no task")
    if req.snooze:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.snooze):
            return _err(400, "bad date", detail="expected YYYY-MM-DD")

    index, readable = td.load_index()
    if not readable:
        return _err(409, "index", detail="the task index did not parse")
    if req.id not in index["tasks"]:
        # A task the scan has never seen. Seeding one would create an entry that
        # matches no line in any note and never gets cleaned up.
        return _err(404, "unknown task", detail="run a refresh and try again")

    fields: dict = {}
    if req.snooze is not None:
        fields["snoozed_until"] = req.snooze or None
    if req.pin is not None:
        fields["pinned"] = bool(req.pin)
    if req.archive is not None:
        fields["status"] = "archived" if req.archive else "active"
    if not fields:
        return _err(400, "nothing to change")

    if not td.set_meta({req.id: fields}):
        return _err(500, "write failed", detail="the index could not be written")
    panels._cache.pop("queue", None)
    return {"ok": True, "id": req.id, **fields}


# --------------------------------------------------------------------------
# POST /api/agenda/add · /api/agenda/edit — the calendar's write path (P5)
# --------------------------------------------------------------------------
# **No new write path.** These compose a line, then go through the same
# splice → mutex → pull → verify → commit → ledger machinery `/api/queue/add`
# and `/api/capture` use. What is new is the *holds table*, in the spirit of
# applier.py's: every refusal below is a rule, not a judgement, and each one has
# an adversarial test.
#
#   line changed underneath the client   409 stale
#   the composed line does not re-parse  400   never write what the scanner cannot read
#   the line would span more than one    400   scope
#   the note's checkbox count changes    400   completion is a human signal
#   target escapes the vault             400   scope
#   target is gitignored                 403   checked INSIDE the mutex, failing closed
#   the occurrence is not an event row   400   rules are P6; notes are a different shape
#   the git mutex is busy                409   refuse, never queue
#
# The gitignored check runs *after* the pull and *inside* the mutex, the way
# applier.py does it and deliberately not the way the toggle does — a .gitignore
# that changed during the pull is only judged correctly there. And it refuses
# whether or not the path is `model_allow`-exempt: exemption governs what the
# model may see, never what may be written, and `gitops.commit()` returns no SHA
# for an ignored path, so such a write would be the only one in Sigma with no
# ledger row and no undo.

_CHECKBOX = re.compile(r"^\s*[-*]\s+\[[ xX]\]", re.M)


class EventAdd(BaseModel):
    date: str                      # YYYY-MM-DD
    title: str
    start: str | None = None       # HH:MM
    end: str | None = None
    end_date: str | None = None


class EventEdit(BaseModel):
    file: str                      # the month note it is in now
    line: int
    raw: str                       # the exact line the client saw
    date: str
    title: str
    start: str | None = None
    end: str | None = None
    end_date: str | None = None
    cancelled: str | None = None   # "" clears it, a date sets it


class RuleException(BaseModel):
    """One occurrence of a recurrence rule, skipped or moved (agenda P6).

    `to_date` is what separates the two: absent, the occurrence is simply
    skipped and the rule row is the only file touched. Present, the same skip
    happens *and* an override event row is written for the new day — two files,
    one commit, because an undo that restored only one of them would leave the
    occurrence in neither place or in both.
    """
    file: str                      # schedule.md — the only file a rule lives in
    line: int
    raw: str                       # the exact rule row the client saw
    date: str                      # the occurrence being excepted
    to_date: str | None = None     # set to also write the override event
    title: str | None = None       # override title; defaults to the rule's own
    start: str | None = None
    end: str | None = None


def _month_rel(date: str) -> str:
    return f"{ag.CALENDAR_DIR}/{date[:7]}.md"


def _compose_checked(**kw) -> tuple:
    """Compose a line and prove it reads back as the same event.

    Returns (line, error). This is the "never write something the scanner cannot
    read" hold, and it is a round-trip rather than a validation: the only
    definition of a well-formed line that matters is *the parser's*, so the
    parser is what is asked.
    """
    line = ag.compose_event(**kw)
    if "\n" in line or "\r" in line:
        return None, "a line may not span more than one line"
    got = ag.parse_event(line)
    if not got:
        return None, "the composed line does not parse as an event row"
    if (got["date"], got["start"], got["end"], got["end_date"],
            got["cancelled"]) != (kw["date"], kw.get("start"), kw.get("end"),
                                  kw.get("end_date") if kw.get("end_date") != kw["date"] else None,
                                  kw.get("cancelled")):
        return None, "the composed line does not read back as what was asked for"
    return line, None


def _except_checked(raw: str, date: str) -> tuple:
    """Add `date` to a rule's `except::` and prove the row still reads back the
    same rule. Returns (line, error).

    The round-trip is stricter than the event one, because this write edits a
    line it did not compose: everything except the exception list has to come
    back byte-identical in meaning. A serialiser bug that dropped `until::` or
    flattened `[[CSE-311]]` would otherwise write a rule that still parses —
    and quietly runs forever, or forever plus a broken link.
    """
    was = ag.parse_rule(raw)
    if not was:
        return None, "that line is not a recurrence rule"
    line, why = ag.except_rule(raw, date)
    if why:
        return None, why
    if "\n" in line or "\r" in line:
        return None, "a line may not span more than one line"
    got = ag.parse_rule(line)
    if not got:
        return None, "the edited line does not parse as a recurrence rule"
    if date not in got["except"]:
        return None, "the edited line does not carry the exception it was asked for"
    unchanged = ("weekdays", "start", "end", "title", "from", "until", "rule_id")
    if [got[k] for k in unchanged] != [was[k] for k in unchanged]:
        return None, "the edit changed more of the rule than its exception list"
    if set(got["except"]) != set(was["except"]) | {date}:
        return None, "the edit changed exceptions other than the one asked for"
    return line, None


def _sealed_inside_mutex(rel: str) -> str | None:
    """`git check-ignore`, failing closed. Runs inside the write's mutex."""
    r = gitops._git(VAULT, "check-ignore", "-q", rel)
    if r.returncode == 0:
        return "sealed path — gitignored, so a write here could never be committed or undone"
    if r.returncode not in (0, 1):
        return "could not verify the privacy boundary — failing closed"
    return None


@router.post("/agenda/add")
def api_agenda_add(req: EventAdd):
    """One typed event becomes a real line in a real month note."""
    title = " ".join((req.title or "").split())
    if not title:
        return _err(400, "empty")
    if len(title) > 300:
        return _err(400, "too long", detail="an event title is not a note")
    for label, value in (("date", req.date), ("end date", req.end_date)):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return _err(400, f"bad {label}", detail="expected YYYY-MM-DD")
    if not req.date:
        return _err(400, "bad date", detail="expected YYYY-MM-DD")
    for label, value in (("start", req.start), ("end", req.end)):
        if value and not re.fullmatch(r"\d{1,2}:\d{2}", value):
            return _err(400, f"bad {label}", detail="expected HH:MM")

    rel = _month_rel(req.date)
    if _vault_rel(rel) != rel:
        return _err(400, "bad path")

    line, why = _compose_checked(
        date=req.date, title=title, start=req.start, end=req.end,
        end_date=req.end_date,
        block_id=ag.new_block_id(rel, req.date, title))
    if why:
        return _err(400, "bad event", detail=why)

    dest = VAULT / rel
    try:
        with gitops.vault_write(VAULT) as w:
            sealed = _sealed_inside_mutex(rel)
            if sealed:
                return _err(403, "sealed path", detail=sealed)
            existed = dest.exists()
            body = (dest.read_text(encoding="utf-8", errors="replace") if existed
                    else ag.MONTH_NOTE.format(month=req.date[:7]))
            before = len(_CHECKBOX.findall(body))
            out = td.splice(body, ag.EVENTS_HEADING, line)
            if len(_CHECKBOX.findall(out)) != before:
                return _err(400, "checkbox", detail="a calendar write may not add a checkbox")
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_note(dest, out)
            res = w.commit(rel, f"zach (dashboard): add event to {rel}")
    except gitops.GitBusy as e:
        return _err(409, "busy", detail=f"another Sigma write is in progress ({e})")
    except OSError as e:
        return _err(500, "write failed", detail=str(e))

    ledger.record("zach", "append" if existed else "create", rel, res["sha"],
                  f"added an event: {title[:60]}")
    panels.drop_task_caches(*(("graph",) if not existed else ()))
    return {"ok": True, "file": rel, "raw": line, "sha": res["sha"],
            "created_note": not existed, "note": res["note"]}


@router.post("/agenda/edit")
def api_agenda_edit(req: EventEdit):
    """Rewrite one event line — retitle, retime, reschedule, or cancel.

    A reschedule across a month boundary moves the line between month notes in
    **one commit touching both**, the same rule `/api/queue/edit` follows: two
    commits would let an undo leave the event in neither note or in both.
    """
    src = _vault_rel(req.file)
    if src is None:
        return _err(400, "bad path")
    title = " ".join((req.title or "").split())
    if not title:
        return _err(400, "empty")
    if len(title) > 300:
        return _err(400, "too long")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.date or ""):
        return _err(400, "bad date", detail="expected YYYY-MM-DD")
    cancelled = (req.cancelled or "").strip() or None
    if cancelled and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cancelled):
        return _err(400, "bad cancelled", detail="expected YYYY-MM-DD")

    # The line the client claims to be editing has to *be* an event row. A rule
    # occurrence reaches this endpoint only through a UI bug or a hand-rolled
    # request, and moving one would silently rewrite a recurrence for every week
    # rather than this one — which is precisely what P6 exists to do properly.
    was = ag.parse_event(req.raw or "")
    if not was:
        if ag.parse_rule(req.raw or ""):
            return _err(400, "not an event",
                        detail="this is a recurrence rule — editing one occurrence "
                               "of it is P6, and moving the rule would move every week")
        return _err(400, "not an event", detail="that line is not an event row")

    line, why = _compose_checked(
        date=req.date, title=title, start=req.start, end=req.end,
        end_date=req.end_date, cancelled=cancelled,
        block_id=was["block_id"] or ag.new_block_id(src, req.date, title))
    if why:
        return _err(400, "bad event", detail=why)

    dst = _month_rel(req.date)
    if _vault_rel(dst) != dst:
        return _err(400, "bad path")
    moving = dst != src
    src_p, dst_p = VAULT / src, VAULT / dst

    try:
        with gitops.vault_write(VAULT) as w:
            for rel in ({src, dst} if moving else {src}):
                sealed = _sealed_inside_mutex(rel)
                if sealed:
                    return _err(403, "sealed path", detail=sealed)
            try:
                body = src_p.read_text(encoding="utf-8")
            except OSError:
                return _err(409, "stale", detail="the note is gone or unreadable")
            before = len(_CHECKBOX.findall(body))

            if not moving:
                out = td.replace_line(body, req.line, req.raw, line)
                if out is None:
                    return _err(409, "stale", detail="the line changed underneath you")
                if len(_CHECKBOX.findall(out)) != before:
                    return _err(400, "checkbox",
                                detail="a calendar write may not change a checkbox")
                write_note(src_p, out)
                touched = [src]
            else:
                cut = td.unsplice(body, req.line, req.raw)
                if cut is None:
                    return _err(409, "stale", detail="the line changed underneath you")
                existed = dst_p.exists()
                dst_body = (dst_p.read_text(encoding="utf-8", errors="replace")
                            if existed else ag.MONTH_NOTE.format(month=req.date[:7]))
                spliced = td.splice(dst_body, ag.EVENTS_HEADING, line)
                if (len(_CHECKBOX.findall(cut)) + len(_CHECKBOX.findall(spliced))
                        != before + len(_CHECKBOX.findall(dst_body))):
                    return _err(400, "checkbox",
                                detail="a calendar write may not change a checkbox")
                write_note(src_p, cut)
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                write_note(dst_p, spliced)
                touched = [src, dst]

            verb = "cancel" if cancelled and not was["cancelled"] else (
                "move" if moving else "edit")
            res = w.commit(touched, f"zach (dashboard): {verb} event in {dst}")
    except gitops.GitBusy as e:
        return _err(409, "busy", detail=f"another Sigma write is in progress ({e})")
    except OSError as e:
        return _err(500, "write failed", detail=str(e))

    # Spelled out rather than suffixed. `f"{verb}d"` gave "editd" in the ledger,
    # which is the morning view — the one place in this system whose readability
    # is a stated feature, so it does not get to have typos generated into it.
    said = {"cancel": "cancelled", "move": "moved", "edit": "edited"}[verb]
    ledger.record("zach", "update", dst, res["sha"],
                  f"{said} an event: {title[:60]}")
    panels.drop_task_caches()
    return {"ok": True, "file": dst, "raw": line, "sha": res["sha"],
            "moved": moving, "note": res["note"]}


@router.post("/agenda/except")
def api_agenda_except(req: RuleException):
    """Skip or move ONE occurrence of a recurrence rule (agenda P6).

    Not a new write path: the same `vault_write` mutex, pull, sealed check and
    ledger row every other calendar write uses. What is new is that the rule row
    and the override event are written in **one commit**, the rule
    `/api/agenda/edit` already follows for a cross-month move.

    Deliberately not here: *this and all future*, which would split the rule
    into two rows with `until::` and `from::`. That is a different operation on
    a different number of lines, and the plan scopes P6 to single occurrences —
    it is refused by name rather than approximated.
    """
    src = _vault_rel(req.file)
    if src is None:
        return _err(400, "bad path")
    # A rule lives in exactly one file. Anything else reaching here is a UI bug
    # or a hand-rolled request, and excepting a "rule" in a month note would
    # write a field the event grammar has no meaning for.
    if src != ag.SCHEDULE_REL:
        return _err(400, "not the schedule",
                    detail=f"recurrence rules live in {ag.SCHEDULE_REL}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.date or ""):
        return _err(400, "bad date", detail="expected YYYY-MM-DD")

    was = ag.parse_rule(req.raw or "")
    if not was:
        return _err(400, "not a rule", detail="that line is not a recurrence rule")

    # Order matters here. An already-excepted day is *also* a day `expand` no
    # longer produces, so checking occurrence first would answer "this rule does
    # not occur on 2026-09-14" for a lecture that does occur and that you have
    # already skipped — true of the expansion, misleading about the rule.
    if req.date in was["except"]:
        return _err(400, "already excepted",
                    detail=f"{req.date} is already an exception on this rule")

    # The rule has to actually produce the day being excepted. Excepting a
    # Tuesday from an MWF lecture parses, commits, and changes nothing anyone
    # can see — a write whose only effect is a ledger row saying it happened.
    if req.date not in ag.expand(was, req.date, req.date):
        return _err(400, "no such occurrence",
                    detail=f"this rule does not occur on {req.date}")

    rule_line, why = _except_checked(req.raw, req.date)
    if why:
        return _err(400, "bad rule edit", detail=why)

    moving = bool(req.to_date)
    dst = event_line = None
    if moving:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", req.to_date or ""):
            return _err(400, "bad date", detail="expected YYYY-MM-DD")
        title = " ".join((req.title or was["title"]).split())
        if not title:
            return _err(400, "empty")
        if len(title) > 300:
            return _err(400, "too long")
        # The override is an ordinary event row with its own block ID and no
        # back-reference to the rule (decided 2026-08-04). Naming its parent
        # would be new line grammar, and grammar is a contract change.
        event_line, why = _compose_checked(
            date=req.to_date, title=title,
            start=req.start if req.start is not None else was["start"],
            end=req.end if req.end is not None else was["end"],
            block_id=ag.new_block_id(req.to_date, title, req.date))
        if why:
            return _err(400, "bad event", detail=why)
        dst = _month_rel(req.to_date)
        if _vault_rel(dst) != dst:
            return _err(400, "bad path")

    src_p = VAULT / src
    try:
        with gitops.vault_write(VAULT) as w:
            for rel in ([src, dst] if moving else [src]):
                sealed = _sealed_inside_mutex(rel)
                if sealed:
                    return _err(403, "sealed path", detail=sealed)
            try:
                body = src_p.read_text(encoding="utf-8")
            except OSError:
                return _err(409, "stale", detail="the schedule is gone or unreadable")
            before = len(_CHECKBOX.findall(body))

            out = td.replace_line(body, req.line, req.raw, rule_line)
            if out is None:
                return _err(409, "stale", detail="the line changed underneath you")
            after = len(_CHECKBOX.findall(out))
            touched = [src]

            dst_body = spliced = None
            existed = True
            if moving:
                dst_p = VAULT / dst
                existed = dst_p.exists()
                dst_body = (dst_p.read_text(encoding="utf-8", errors="replace")
                            if existed else ag.MONTH_NOTE.format(month=req.to_date[:7]))
                spliced = td.splice(dst_body, ag.EVENTS_HEADING, event_line)
                before += len(_CHECKBOX.findall(dst_body))
                after += len(_CHECKBOX.findall(spliced))
            if after != before:
                return _err(400, "checkbox",
                            detail="a calendar write may not change a checkbox")

            write_note(src_p, out)
            if moving:
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                write_note(dst_p, spliced)
                touched.append(dst)

            verb = "move" if moving else "skip"
            res = w.commit(touched, f"zach (dashboard): {verb} one occurrence in {src}")
    except gitops.GitBusy as e:
        return _err(409, "busy", detail=f"another Sigma write is in progress ({e})")
    except OSError as e:
        return _err(500, "write failed", detail=str(e))

    said = ("moved one occurrence: {t} from {d} to {n}" if moving else
            "skipped one occurrence: {t} on {d}").format(
        t=ag.display_title(was["title"])[:60], d=req.date, n=req.to_date)
    ledger.record("zach", "update", src, res["sha"], said)
    # The graph only changes when a month note was created — a new node.
    panels.drop_task_caches(*(("graph",) if not existed else ()))
    return {"ok": True, "file": src, "raw": rule_line, "sha": res["sha"],
            "moved": moving, "event": event_line, "event_file": dst,
            "note": res["note"]}


# --------------------------------------------------------------------------
# study mode S3 — the practice engine's write half
# --------------------------------------------------------------------------
# Three endpoints, three different weights, and the split is the design (§8):
#
#   POST /api/lesson/state        sidecar only — never commits, never ledgered
#   POST /api/lesson/attempt      appends one fact to the machine-local log
#   POST /api/lesson/session-end  the one durable write: a digest row spliced
#                                 into <code>-study-log.md, one commit
#
# The state and attempt writes are deliberately outside git for todo.py's
# sidecar reason: they hold what a note cannot say, and losing them costs a
# few reveals — the digest is what survives, and it goes through the same
# mutex → splice → verify → commit → ledger machinery as every other vault
# write here.

RESULTS = ("correct", "wrong", "skipped")
SESSIONS_HEADING = "## Sessions"     # the study-log schema's one section


def _unit_key(module, checkpoint) -> str | None:
    """The sidecar key for one study unit: `<module>` or `cp<n>`. Exactly one
    of the two must be set — a request naming both is confused, not flexible."""
    if (module is None) == (checkpoint is None):
        return None
    return f"cp{checkpoint}" if checkpoint is not None else str(module)


class LessonState(BaseModel):
    course: str
    module: int | None = None
    checkpoint: int | None = None    # a checkpoint's state, keyed cp<n> (S6)
    state: dict


@router.post("/lesson/state")
def api_lesson_state(req: LessonState):
    """Depth chosen, reveals opened, per-question results — resume state.

    Not in the ledger for /api/queue/meta's reason: the ledger indexes commits
    so they can be reverted, and there is no commit here. Undo is clicking it
    back."""
    course = (req.course or "").strip()
    if not course:
        return _err(400, "no course")
    key = _unit_key(req.module, req.checkpoint)
    if key is None:
        return _err(400, "bad unit", detail="exactly one of module | checkpoint")
    try:
        blob = json.dumps(req.state)
    except (TypeError, ValueError):
        return _err(400, "bad state")
    if len(blob) > 20_000:
        return _err(413, "too large",
                    detail="view state is a few flags, not a document")
    st = ln.load_state()
    st["modules"][f"{course}/{key}"] = req.state
    if not ln.save_state(st):
        return _err(500, "write failed", detail="the sidecar could not be written")
    return {"ok": True}


class AttemptReq(BaseModel):
    course: str
    module: int | None = None
    checkpoint: int | None = None  # checkpoint attempts share the one log (S6)
    qid: str
    result: str                    # correct | wrong | skipped
    hints: int = 0                 # how many hints were open when it resolved
    revealed: bool = False         # the solution was shown before resolving
    answer: str | None = None      # what was typed, for the record


@router.post("/lesson/attempt")
def api_lesson_attempt(req: AttemptReq):
    """Append one row to the attempt log (§8/§10).

    The kind, the segment title and the question-text hash are derived from
    the parsed module here, never trusted from the client — the digest groups
    by segment title, and history surviving a regenerated module depends on
    the hash being the hash of what was actually asked."""
    if req.result not in RESULTS:
        return _err(400, "bad result", detail="correct | wrong | skipped")
    if _unit_key(req.module, req.checkpoint) is None:
        return _err(400, "bad unit", detail="exactly one of module | checkpoint")
    if req.checkpoint is not None:
        d = ln.load_checkpoint(VAULT, req.course, req.checkpoint,
                               split=panels._lesson_split)
        if d is None:
            return _err(404, "no such checkpoint",
                        detail=f"{req.course} CP{req.checkpoint}")
    else:
        d = ln.load(VAULT, req.course, req.module, split=panels._lesson_split)
        if d is None:
            return _err(404, "no such module", detail=f"{req.course} M{req.module}")
    hit = ln.find_item(d, req.qid)
    if hit is None:
        return _err(404, "no such question", detail=req.qid)
    seg, item = hit

    row = {
        "ts": ln.now_iso(), "course": d["course"],
        "module": None if req.checkpoint is not None else d["module"],
        "qid": req.qid, "qhash": ln.qhash(item["prompt"]),
        "kind": item["kind"], "seg": seg["n"], "seg_title": seg["title"],
        "result": req.result, "hints": max(0, req.hints),
        "revealed": bool(req.revealed),
    }
    if req.checkpoint is not None:
        row["checkpoint"] = req.checkpoint
    if req.answer:
        row["answer"] = " ".join(req.answer.split())[:200]
    if not ln.record_attempt(row):
        return _err(500, "write failed",
                    detail="the attempt log could not be written")
    # /api/study's third panel renders this log — a 60s-stale "nothing
    # recorded yet" right after a recorded miss is the write-then-stale-panel
    # bug class the task caches already exist for.
    panels._cache.pop("study", None)
    return {"ok": True, "row": row}


class SessionEnd(BaseModel):
    course: str
    seconds: int = 0    # active study time, measured by the workbench (S8)


# Six hours of one sitting is not a study session, it is a tab. The workbench
# already pauses its accumulator when the tab is hidden or nothing has been
# touched for five minutes; this is the server-side floor and ceiling, because
# the one number the pace multiplier is computed from must not be a client's
# word alone.
MIN_SESSION_SECONDS = 30
MAX_SESSION_MINUTES = 360


def _row_signature(line: str) -> str:
    """A rollup row without its `⏱` field.

    The content check compares whole rows to catch a duplicate the watermark
    missed, and the measured minutes are the one field that can legitimately
    differ between two calls covering the same attempts — a retry a minute later
    measures a minute more. Comparing the rows whole would let that retry write
    the digest twice, which is the exact failure the check exists to prevent.
    Stripping it restores precisely the pre-S8 comparison.
    """
    return " ".join(rc.ROW_TIME_RE.sub("", line).split())


def _recall_write(course: str, wrong: list, today: str) -> dict:
    """Raise cards for this session's misses, retire what has gone stale.

    Called *inside* the vault mutex and deliberately does not commit: the cards
    and the study-log row are one session's record, so they land in one commit
    with one undo. Returns what changed; `changed=False` means there is nothing
    to add to the caller's path list.
    """
    out = {"changed": False, "file": None, "raised": [], "expired": 0,
           "withheld": 0, "created": False, "index": None}
    rel = rc.rel_for(course)
    if _vault_rel(rel) != rel:
        return out
    if _sealed_inside_mutex(rel):
        return out                     # a sealed course keeps its cards to itself
    p = VAULT / rel
    existed = p.is_file()
    try:
        text = (p.read_text(encoding="utf-8-sig", errors="replace") if existed
                else rc.note_text(course, today))
    except OSError:
        return out
    units = rc.units_for(VAULT, course, split=panels._lesson_split)
    res = rc.reconcile(text, rc.wanted_from(wrong, units), today)
    # A course that missed nothing does not get an empty note minted for it.
    if not existed and not res["raised"]:
        return out
    if res["text"] == text and existed:
        return out
    write_note(p, res["text"])
    # A brand-new note gets linked from the course index in the same breath.
    # Only on creation: after that the link is there, and re-checking a manifest
    # on every session would be a write path looking for work.
    index_rel = None
    if not existed:
        ip = VAULT / f"02-Areas/Academics/{course}/{course.lower()}.md"
        if ip.is_file() and not _sealed_inside_mutex(
                ip.relative_to(VAULT).as_posix()):
            try:
                itext = ip.read_text(encoding="utf-8-sig", errors="replace")
                linked = rc.link_in_index(itext, course)
            except OSError:
                linked = None
            if linked:
                write_note(ip, linked)
                index_rel = ip.relative_to(VAULT).as_posix()
    return {"changed": True, "file": rel, "raised": res["raised"],
            "expired": len(res["expired"]), "withheld": res["withheld"],
            "created": not existed, "index": index_rel}


@router.post("/lesson/session-end")
def api_lesson_session_end(req: SessionEnd):
    """Roll the session up: one digest row spliced into `<code>-study-log.md`,
    one commit, one ledger row (§8).

    Idempotent twice over, because the two layers fail in different
    directions. The rollup *watermark* makes the common case cheap: the close
    handler and the explicit button can both fire, and the second call finds
    nothing to cover. The *content check* — an identical row already in the
    note, judged inside the mutex — covers everything the watermark cannot:
    two calls racing past the same watermark read, a watermark whose sidecar
    save failed, a commit that failed after the row was written. In every one
    of those, the next call finds the row above, advances the watermark, and
    writes nothing — never the same digest twice. The watermark advances only
    once the row provably exists in the note (committed, or found already
    there); a failed commit refuses loudly instead. Both sidecars are
    gitignored; this row's digest is the trace that survives a wipe, which is
    the whole reason it exists."""
    folder = ln.course_folder(VAULT, (req.course or "").strip())
    if folder is None:
        return _err(404, "no such course", detail=req.course or "(blank)")
    course = folder.name
    rel = f"02-Areas/Academics/{course}/{course.lower()}-study-log.md"
    if _vault_rel(rel) != rel:
        return _err(400, "bad path")
    if rel in sealed_paths(VAULT, [rel]):
        return _err(403, "sealed path")
    dest = VAULT / rel
    if not dest.exists():
        return _err(404, "no study log note",
                    detail=f"{rel} does not exist — create it first")

    st = ln.load_state()
    rows = ln.attempts_for(course, after=(st.get("rollup") or {}).get(course))
    if not rows:
        return {"ok": True, "rows": 0, "wrote": False}

    answered = [r for r in rows if r.get("result") in ("correct", "wrong")]
    correct = [r for r in answered if r["result"] == "correct"]
    wrong = [r for r in answered if r["result"] == "wrong"]
    skipped = [r for r in rows if r.get("result") == "skipped"]
    mods = sorted({int(r["module"]) for r in rows
                   if isinstance(r.get("module"), int)})
    cps = sorted({int(r["checkpoint"]) for r in rows
                  if isinstance(r.get("checkpoint"), int)})
    mod_label = "+".join([f"M{m:02d}" for m in mods]
                         + [f"CP{c}" for c in cps]) or "M?"

    today = datetime.date.today().isoformat()
    # Active minutes, and only when something was actually measured: a session
    # with no clock writes the pre-S8 row unchanged, and `recall.pace` counts
    # what was measured rather than back-filling what was not.
    minutes = None
    if req.seconds and req.seconds >= MIN_SESSION_SECONDS:
        minutes = max(1, min(MAX_SESSION_MINUTES, round(req.seconds / 60)))

    bits = [f"- {today} · {mod_label}"]
    if minutes:
        bits.append(f"⏱ {minutes} min")
    bits.append(f"{len(answered)} answered, {len(correct)} correct")
    if skipped:
        bits.append(f"skipped {len(skipped)}")
    bits.append(f"missed {len(wrong)}")
    if wrong:
        bits.append(ln.digest(wrong))
    line = " · ".join(bits)

    wrote, sha, saved, cards = False, None, True, None
    try:
        with gitops.vault_write(VAULT) as w:
            sealed = _sealed_inside_mutex(rel)
            if sealed:
                return _err(403, "sealed path", detail=sealed)
            try:
                body = dest.read_text(encoding="utf-8")
            except OSError:
                return _err(409, "stale", detail="the note is gone or unreadable")

            # The content half of the idempotency (see the docstring): an
            # identical row already in the note means these attempts are
            # covered — repair the watermark, write nothing.
            sig = _row_signature(line)
            already = any(_row_signature(ln_) == sig for ln_ in body.split("\n")
                          if ln_.strip())
            paths = []
            if not already:
                before = len(_CHECKBOX.findall(body))
                out = td.splice(body, SESSIONS_HEADING, line)
                if len(_CHECKBOX.findall(out)) != before:
                    return _err(400, "checkbox",
                                detail="a rollup row may not add a checkbox")
                write_note(dest, out)
                paths.append(rel)

            # The same misses become recall cards, in the same commit — one
            # session, one record, one undo (S8). A failure here must never
            # cost the digest, which is the durable half.
            try:
                cards = _recall_write(course, wrong, today)
            except OSError:
                cards = None
            if cards and cards["changed"]:
                paths.append(cards["file"])
                if cards["index"]:
                    paths.append(cards["index"])

            if paths:
                said = f"study session rollup in {rel}"
                if cards and cards["changed"]:
                    said += f" + {len(cards['raised'])} recall card(s)"
                res = w.commit(paths, f"zach (dashboard): {said}")
                if not res["sha"]:
                    # The row is in the note but no commit exists (an
                    # obsidian-git index.lock, most likely). Refuse without
                    # advancing the watermark — the next call finds the row
                    # above and repairs.
                    return _err(500, "commit failed",
                                detail=res["note"] or "no commit was created")
                wrote, sha = not already, res["sha"]

            # Re-read the sidecar *inside* the mutex before advancing: two
            # session-ends serialise here, and advancing over a state loaded
            # before the other's save would silently drop its watermark.
            st = ln.load_state()
            st.setdefault("rollup", {})[course] = max(str(r.get("ts") or "")
                                                      for r in rows)
            saved = ln.save_state(st)
    except gitops.GitBusy as e:
        return _err(409, "busy", detail=f"another Sigma write is in progress ({e})")
    except OSError as e:
        return _err(500, "write failed", detail=str(e))

    if wrote:
        ledger.record("zach", "append", rel, sha,
                      f"study session: {course} {mod_label} — {len(answered)} "
                      f"answered, {len(wrong)} missed"
                      + (f", {minutes} min" if minutes else ""))
        panels._cache.pop("study", None)   # the panel now renders this row
    if cards and cards["changed"]:
        if not wrote:
            # Cards landed without a new digest row (a repaired watermark).
            # They are still a commit, so they are still one click to undo.
            ledger.record("recall", "update" if not cards["created"] else "create",
                          cards["file"], sha,
                          f"{len(cards['raised'])} recall card(s) in {course}")
        # A card is a task, and the queue and the course card both count them.
        panels.drop_task_caches("courses")
        _snooze_new_cards(cards)
    resp = {"ok": True, "rows": len(rows), "wrote": wrote, "file": rel,
            "raw": line, "sha": sha, "digest": ln.digest(wrong),
            "minutes": minutes,
            "recall": ({"file": cards["file"], "raised": len(cards["raised"]),
                        "expired": cards["expired"], "withheld": cards["withheld"]}
                       if cards and cards["changed"] else None)}
    if not saved:
        resp["warning"] = ("the watermark could not be saved — the next "
                           "rollup finds this row in the note and repairs it")
    return resp


def _snooze_new_cards(cards: dict):
    """A fresh card sleeps one night.

    The review is never the same sitting as the mistake — that is the whole
    "spaced" in spaced recall — and the deferral goes in the queue's own snooze
    field rather than into the line, because a `📅` on a card would become a
    deadline in the adherence scan and the note has no other way to say "not
    yet". Losing the sidecar costs a day of spacing and nothing else, which is
    the right failure direction for state that is not the record.
    """
    when = (datetime.date.today() + datetime.timedelta(
        days=rc.FIRST_REVIEW_DAYS)).isoformat()
    updates = {}
    for card in cards["raised"]:
        tid = rc.card_task_id(cards["file"], card["raw"])
        if tid:
            updates[tid] = {"snoozed_until": when}
    if updates:
        td.set_meta(updates)


@router.get("/activity")
def api_activity(limit: int = 60):
    return {"entries": ledger.entries(limit)}


@router.post("/activity/revert")
def api_revert(req: RevertReq):
    """Undo is `git revert` of that one commit — the mechanism the whole vault
    already runs on, not a bespoke rollback. Only commits the ledger owns are
    revertible from here: this button must never become a general git console."""
    target = next((e for e in ledger.entries(500)
                   if e.get("sha") == req.sha and e.get("action") != "revert"), None)
    if target is None:
        return _err(404, "not a ledger commit")
    if target.get("reverted"):
        return _err(409, "already reverted")

    try:
        r = gitops.revert(VAULT, req.sha)
    except gitops.GitBusy:
        return _err(409, "busy", detail="another Sigma write is in progress — retry")
    if not r["ok"]:
        # surfaced, never forced: the tree is already back to clean
        return _err(409, "conflict", detail=r["note"])

    ledger.record("zach", "revert", target.get("target", ""), r["sha"],
                  f"reverted: {target.get('summary', target.get('sha', ''))}",
                  extra={"reverts": req.sha})
    panels.drop_task_caches("proposals", "projects")
    return {"ok": True, "sha": r["sha"]}


# --------------------------------------------------------------------------
# POST /api/capture — quick capture (Phase 6)
# --------------------------------------------------------------------------
# [[dashboard-vision]]: "an idea, a task, a link, a screenshot → the inbox,
# without leaving what you were doing."
#
# **Type choice, stated because it is a judgement call.** The contract has no
# schema for a captured fragment, and 00-Inbox is defined as "fast capture,
# unsorted — triage into the right place later". `resource` is the closest
# existing type and the one whose required fields a fragment can honestly fill
# (`source` blank, `course` blank). Inventing a ninth type for something whose
# whole purpose is to stop existing after triage would be the wrong trade — the
# note is meant to be re-typed when it is filed.
#
# Like every other write here it goes through gitops: one path-scoped commit,
# recorded in the ledger, revertible from Ctrl+J. A capture you did not mean is
# one click from gone.

_SLUG_MAX = 48


class Capture(BaseModel):
    text: str


@router.post("/capture")
def api_capture(body: Capture):
    text = (body.text or "").strip()
    if not text:
        return _err(400, "empty", detail="nothing to capture")
    if len(text) > 20_000:
        return _err(413, "too long", detail="capture is for a fragment, not a document")

    import datetime
    import re as _re

    now = datetime.datetime.now()
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "capture")
    # Strip a leading checkbox or bullet so the filename is about the idea.
    first = _re.sub(r"^\s*[-*]\s*(\[[ xX]\]\s*)?", "", first)
    slug = _re.sub(r"[^A-Za-z0-9]+", "-", first).strip("-").lower()[:_SLUG_MAX] or "capture"
    rel = f"00-Inbox/{now:%Y-%m-%d-%H%M}-{slug}.md"

    dest = VAULT / rel
    if dest.exists():
        return _err(409, "exists", detail=f"{rel} already exists")

    note = (f"---\ntype: resource\ncourse: \nsource: \ntags: [resource]\n---\n\n"
            f"# {first[:120]}\n\n"
            f"> Captured {now:%Y-%m-%d %H:%M} from the dashboard. "
            f"Triage into the right folder and re-type it.\n\n"
            f"{text}\n")

    try:
        with gitops.vault_write(VAULT) as w:
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_note(dest, note)
            res = w.commit(rel, f"zach (dashboard): capture {rel}")
    except gitops.GitBusy as e:
        return _err(409, "busy", detail=f"another Sigma write is in progress ({e})")
    except OSError as e:
        return _err(500, "write failed", detail=str(e))

    ledger.record("zach", "create", rel, res["sha"], f"captured: {first[:60]}")
    panels.drop_task_caches("graph", "study")
    return {"ok": True, "file": rel, "sha": res["sha"], "note": res["note"]}
