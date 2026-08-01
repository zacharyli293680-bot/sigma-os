#!/usr/bin/env python3
"""
review.py — approving a proposal without leaving the dashboard (Phase 6).

    GET  /api/proposals/{name}          the proposal, its target, and the diff
    POST /api/proposals/{name}/decide   approve | reject | apply | merge

This is the loop that had no home. The coach and the auditor exist to correct
*existing* notes, and a correction to an existing note is staged rather than
applied — so reviewing their output meant `reflect.py --diff`, a terminal, and
a second window. Phase 3 shipped `reflect merge` as a *disabled* palette entry
whose stated reason was "waiting on Phase 4's diff UI"; Phase 4 deferred exactly
that. This is it.

**`name` is the only user-supplied string that reaches disk in this app, so it
never becomes a path.** It is looked up in a dictionary built from the
proposals directory listing — the same shape as the palette's whitelist, where
a verb is a dictionary key and an unknown key is a 404. There is no
`PROPOSALS / name` anywhere in this file, so there is no traversal to get
wrong: `..`, absolute paths, and NTFS streams all simply fail to match a key.

**It does not invent a second approval gate.** Approving sets `status:
approved` in the proposal note, which is what Zach does by hand in Obsidian.
Applying calls the same `applier.apply_one` the fleet uses; merging calls the
same `reflect --merge`. The rules about what may auto-apply live in those
modules and are not restated here — two gates that can disagree is the mistake
this project keeps refusing to make.
"""
import datetime
import difflib
import re
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_RUNTIME = str(Path(__file__).resolve().parents[2] / "runtime")
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)

import reflect as rf          # noqa: E402
from sigma import frontmatter  # noqa: E402

import panels                  # noqa: E402  — to drop its caches after a write

router = APIRouter(prefix="/api")

ACTIONS = ("approve", "reject", "apply", "merge")


def _err(status: int, error: str, **extra):
    return JSONResponse({"error": error, **extra}, status_code=status)


def _by_name() -> dict:
    """stem -> Path, built from the directory. The whitelist."""
    try:
        return {p.stem: p for p in Path(rf.PROPOSALS).glob("*.md") if p.is_file()}
    except OSError:
        return {}


def _content_block(text: str) -> str:
    """The fenced block under `<!-- proposal:content -->` — literally what would
    be written. Greedy to the *last* fence, because a proposal whose content
    contains a nested ```dataview fence truncated at the first one and shipped a
    half-written note (fixed in Phase 4; re-implemented here for the same
    reason, and bounded to the block so it cannot run past the marker)."""
    i = text.find("<!-- proposal:content -->")
    if i < 0:
        return ""
    tail = text[i:]
    m = re.search(r"```[a-zA-Z]*\n(.*)\n```", tail, re.S)
    return m.group(1) if m else ""


def _target_path(target: str) -> Path | None:
    """Resolve a proposal's declared target inside the vault. The target comes
    from a proposal Sigma wrote, not from the client, but it is still checked
    against the vault root — a proposal is a file on disk and files get edited."""
    if not target or target == "(manual)":
        return None
    try:
        p = (Path(rf.VAULT) / target).resolve()
        p.relative_to(Path(rf.VAULT).resolve())
        return p
    except (ValueError, OSError):
        return None


@router.get("/proposals/{name}")
def api_proposal(name: str):
    path = _by_name().get(name)
    if path is None:
        return _err(404, "unknown proposal")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return _err(500, "unreadable", detail=str(e))

    fm = frontmatter(text) or {}
    proposed = _content_block(text)
    target = str(fm.get("target") or "")
    tp = _target_path(target)
    current = ""
    exists = False
    if tp is not None and tp.exists():
        exists = True
        try:
            current = tp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            current = ""

    diff = ""
    if proposed:
        diff = "".join(difflib.unified_diff(
            current.splitlines(keepends=True) if exists else [],
            (proposed.rstrip() + "\n").splitlines(keepends=True),
            fromfile=f"a/{target}" if exists else "(new file)",
            tofile=f"b/{target}", n=3))

    return {
        "name": name,
        "title": next((l[2:].strip() for l in text.splitlines()
                       if l.startswith("# ")), name),
        "kind": fm.get("kind"), "status": fm.get("status"),
        "risk": fm.get("risk"), "date": str(fm.get("date") or ""),
        "target": target,
        "target_exists": exists,
        # A change to a note that already exists is *staged*, never applied —
        # so the UI can say which button will actually do something.
        "would_stage": bool(exists),
        "proposed": proposed,
        "diff": diff,
        "body": text,
    }


class Decision(BaseModel):
    action: str


def _set_status(path: Path, status: str) -> bool:
    """Rewrite only the `status:` line inside the frontmatter block. Line-scoped
    on purpose: a proposal's body can legitimately contain a `status:` line
    (its content block is a whole note), and a blanket replace would corrupt the
    very change being approved."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lines = text.splitlines(keepends=True)
    fence = 0
    for i, line in enumerate(lines):
        if line.strip() == "---":
            fence += 1
            if fence == 2:
                break
            continue
        if fence == 1 and line.startswith("status:"):
            lines[i] = f"status: {status}\n"
            try:
                path.write_text("".join(lines), encoding="utf-8")
                return True
            except OSError:
                return False
    return False


@router.post("/proposals/{name}/decide")
def api_decide(name: str, body: Decision):
    path = _by_name().get(name)
    if path is None:
        return _err(404, "unknown proposal")
    action = (body.action or "").strip().lower()
    if action not in ACTIONS:
        return _err(400, "unknown action", detail=f"expected one of {', '.join(ACTIONS)}")

    if action in ("approve", "reject"):
        ok = _set_status(path, "approved" if action == "approve" else "rejected")
        if not ok:
            return _err(500, "could not update status",
                        detail="no `status:` line inside the proposal's frontmatter")
        panels._cache.clear()
        return {"ok": True, "action": action, "status":
                "approved" if action == "approve" else "rejected"}

    if action == "apply":
        try:
            import applier
            res = applier.apply_one(path, actor="zach")
        except Exception as e:
            return _err(500, "apply failed", detail=f"{type(e).__name__}: {e}")
        panels._cache.clear()
        return {"ok": res.get("action") in ("create", "update"), "action": "apply",
                "result": res.get("action"), "target": res.get("target"),
                "sha": res.get("sha"), "detail": res.get("reason")}

    # merge — the one place Sigma overwrites a note, and deliberately
    # human-only. A click is a human; a schedule is not, and nothing scheduled
    # reaches this endpoint.
    #
    # `rf.do_merge` is called directly rather than reimplemented, because every
    # rule that makes merging safe lives inside it: it refuses anything not
    # already `approved`, refuses a missing staged file, refuses a target that
    # escapes its scope root, and records the result in the activity ledger so
    # the overwrite is revertible. A second copy of that list here would be a
    # second copy that can disagree. It is shaped as a CLI handler — namespace
    # in, exit code out, reasons on stdout — so it gets a namespace, and its
    # stdout becomes the message the UI shows.
    import contextlib
    import io
    import types

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = rf.do_merge(types.SimpleNamespace(merge=name))
    except Exception as e:
        return _err(500, "merge failed", detail=f"{type(e).__name__}: {e}")

    said = " ".join(buf.getvalue().split())
    panels._cache.clear()
    if rc != 0:
        # A refusal is not a server error — it is the guard doing its job, and
        # the reason it printed is the most useful thing to show.
        return _err(409, "merge refused", detail=said or "reflect refused the merge")
    return {"ok": True, "action": "merge", "detail": said or "merged"}
