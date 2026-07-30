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
from sigma import gitops, ledger  # noqa: E402

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
    import re
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
            dest.write_text("\n".join(segs), encoding="utf-8")
            res = w.commit(rel, f"zach (dashboard): "
                                f"{'tick' if req.done else 'untick'} task in {rel}")
    except gitops.GitBusy:
        return _err(409, "busy", detail="another Sigma write is in progress — retry")

    summary = f"{'ticked' if req.done else 'unticked'} a task in {rel}"
    ledger.record("zach", "toggle", rel, res["sha"], summary,
                  extra={"line": req.line})
    panels._cache.pop("tasks", None)
    return {"ok": True, "sha": res["sha"], "absorbed": res["absorbed"],
            "note": res["note"], "raw": flipped}


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
    for key in ("tasks", "proposals", "projects"):
        panels._cache.pop(key, None)
    return {"ok": True, "sha": r["sha"]}
