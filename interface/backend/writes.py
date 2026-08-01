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
import todo as td  # noqa: E402  — section inference, destinations, line grammar

import commands  # noqa: E402  — the one spend-window policy, not a second copy
import panels  # noqa: E402  — to drop its caches after a write

router = APIRouter(prefix="/api")


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
    # feel broken at exactly the moment it is meant to feel immediate.
    for key in panels.TASK_PANELS:
        panels._cache.pop(key, None)
    return {"ok": True, "sha": res["sha"], "absorbed": res["absorbed"],
            "note": res["note"], "raw": flipped}


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
    for key in (*panels.TASK_PANELS, *(("graph",) if not existed else ())):
        panels._cache.pop(key, None)
    return {"ok": True, "file": rel, "section": section, "parent": parent,
            "raw": line, "sha": res["sha"], "created_note": not existed}


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
        raw = await asyncio.wait_for(
            asyncio.to_thread(call_model, prompt, "haiku", timeout=45,
                              actor="reword"),
            timeout=50)
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
    for key in panels.TASK_PANELS:
        panels._cache.pop(key, None)
    return {"ok": True, "file": dst, "section": section, "parent": parent,
            "raw": line, "id": new_id, "moved": moving, "sha": res["sha"]}


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
    for key in (*panels.TASK_PANELS, "proposals", "projects"):
        panels._cache.pop(key, None)
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
    for key in (*panels.TASK_PANELS, "graph", "study"):
        panels._cache.pop(key, None)
    return {"ok": True, "file": rel, "sha": res["sha"], "note": res["note"]}
