#!/usr/bin/env python3
"""
panels.py — the dashboard's read API (Phase 0 of the dashboard plan).

Five endpoints, all read-only, all serving panels in the web dashboard:

    GET /api/fleet       who the specialists are, when they last ran, who is due
    GET /api/tasks       open checkboxes with due dates, pulled from the notes
    GET /api/proposals   what is waiting on Zach — pending, approved, staged
    GET /api/projects    project hubs + live git state of their repos
    GET /api/window      rate-limit headroom — honestly unknown until the Phase 4 spike

Nothing here writes, and nothing here calls a model. Two boundaries hold:

- **The sealed carve-out stays sealed.** Gitignored notes never appear in these
  responses, even though this process could read them — the dashboard's Today
  and Projects panels are general surfaces, and sealed material gets its own
  deliberate lane in Phase 5. Same rule as everywhere else: if git will not
  sync it, it does not show up here.
- **`fleet.py` is imported, never invoked.** Phase 1 touches the fleet; Phase 0
  deliberately does not (the first unattended 09:00 run had not happened when
  this was written, and you do not rewire the thing you are about to observe).
"""
import asyncio
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agent import VAULT

# The runtime modules own the facts these panels display; recomputing them here
# would be a second copy that can disagree (the watchdog's cardinal rule).
_RUNTIME = str(Path(__file__).resolve().parents[2] / "runtime")
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
import fleet as fl          # noqa: E402
import reflect as rf        # noqa: E402
import specialists as sp    # noqa: E402
from sigma import frontmatter  # noqa: E402

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------
# small shared machinery
# --------------------------------------------------------------------------

_cache: dict = {}


def _cached(key: str, ttl: float, compute):
    """A tiny TTL cache. Several panels shell out (schtasks, git); the browser
    may refetch on focus, and a 1s subprocess per panel per refetch adds up."""
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = compute()
    _cache[key] = (now, value)
    return value


def _git(args: list, cwd: Path) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _gitignored(paths: list) -> set:
    """Which of these vault-relative paths are gitignored — one subprocess for
    the whole batch. Fails closed: if git cannot answer, everything is treated
    as ignored, because the unsafe direction is showing sealed material."""
    if not paths:
        return set()
    try:
        # -z (NUL-separated) is not cosmetic: in text mode Windows rewrites
        # "\n" to "\r\n" on stdin, so newline-separated paths reach git with a
        # trailing \r and match nothing — which silently un-seals everything.
        r = subprocess.run(["git", "-C", str(VAULT), "check-ignore", "--stdin", "-z"],
                           input="\0".join(paths), capture_output=True,
                           text=True, timeout=15)
        # exit 0 = some ignored, 1 = none ignored; anything else is failure
        if r.returncode in (0, 1):
            return {s for s in r.stdout.split("\0") if s}
    except Exception:
        pass
    return set(paths)


def _rel(p: Path) -> str:
    return p.relative_to(VAULT).as_posix()


# --------------------------------------------------------------------------
# GET /api/fleet
# --------------------------------------------------------------------------

@router.get("/fleet")
def api_fleet():
    state = fl.load_state()
    now = datetime.datetime.now()
    specs = state.get("specialists") or {}
    return {
        "last_run": state.get("last_run"),
        "stopped_early_at": state.get("stopped_early_at"),
        # schtasks takes ~1s; the answer changes roughly never
        "task_installed": _cached("fleet.task", 300, fl._task_installed),
        "specialists": [
            {"key": s.key, "title": s.title, "cadence": s.cadence,
             "model": s.model,
             "last_ok": (specs.get(s.key) or {}).get("last_ok"),
             "last_run": (specs.get(s.key) or {}).get("last_run"),
             "last_result": (specs.get(s.key) or {}).get("last_result"),
             "last_proposals": (specs.get(s.key) or {}).get("last_proposals"),
             "due": fl.is_due(s, state, now)}
            for s in sp.in_run_order()
        ],
    }


# --------------------------------------------------------------------------
# GET /api/fleet/progress — the reactor's live feed (dashboard-plan D1)
# --------------------------------------------------------------------------

_PROGRESS_PATH = Path(_RUNTIME) / "fleet.progress.json"


@router.get("/fleet/progress")
async def api_fleet_progress():
    """The progress file fleet.py rewrites at each transition, streamed as SSE.

    A file poll rather than any coupling to the fleet process, because the case
    that matters is a run this server did not launch — the 09:00 scheduled one.
    The file is pretty-printed on disk, so each event re-serialises it compact
    (SSE data must be one line), and a torn mid-write read is skipped rather
    than forwarded: the browser holds the last good state until the next write.
    """
    async def stream():
        last, quiet = None, 0
        while True:
            payload = None
            try:
                obj = json.loads(_PROGRESS_PATH.read_text(encoding="utf-8"))
                payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            except (OSError, ValueError):
                pass                        # no file yet, or a torn write
            if payload is not None and payload != last:
                last = payload
                quiet = 0
                yield f"data: {payload}\n\n"
            else:
                quiet += 1
                if quiet >= 30:             # ~15s — keeps the connection alive
                    quiet = 0
                    yield ": ping\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------
# GET /api/tasks
# --------------------------------------------------------------------------

# The Tasks-plugin grammar the vault actually uses (see CLAUDE.md "Tasks"):
# a checkbox, a due date, optional priority. Same folder exclusions as
# Home.md's own query, so the dashboard and the vault agree on what "due" means.
_TASK_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*\S)\s*$")
_DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
_PRIORITY = {"🔺": 4, "⏫": 3, "🔼": 2, "🔽": 1, "⏬": 0}
_META_RE = re.compile(          # strip task-plugin metadata out of display text
    r"\s*(?:📅|✅|⏳|🛫|➕|🔁)\s*\d{4}-\d{2}-\d{2}|\s*[🔺⏫🔼🔽⏬]")
_EXCLUDED_TOPS = {"05-Archive", "06-System", "99-Meta", ".obsidian", ".claude",
                  ".git", "Excalidraw"}


def _scan_tasks() -> dict:
    today = datetime.date.today().isoformat()
    files = [p for p in VAULT.rglob("*.md")
             if not (set(p.relative_to(VAULT).parts[:-1]) & _EXCLUDED_TOPS)
             and not p.relative_to(VAULT).parts[0].startswith(".")
             and p.relative_to(VAULT).parts[0] not in _EXCLUDED_TOPS]
    rels = [_rel(p) for p in files]
    sealed = _gitignored(rels)

    tasks = []
    for p, rel in zip(files, rels):
        if rel in sealed:
            continue                      # the sealed lane is Phase 5, not Today
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        in_fence = False
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue     # CLAUDE.md's fenced example task must not become
                             # a phantom overdue item on the Today panel
            m = _TASK_RE.match(line)
            if not m or m.group(1) != " ":
                continue                  # done tasks stay in the notes, not here
            due = _DUE_RE.search(m.group(2))
            if not due:
                continue                  # the Today panel shows dated work only
            text = _META_RE.sub("", m.group(2)).strip()
            prio = next((v for e, v in _PRIORITY.items() if e in m.group(2)), None)
            tasks.append({"text": text, "due": due.group(1), "priority": prio,
                          "overdue": due.group(1) < today,
                          "file": rel, "line": i})

    tasks.sort(key=lambda t: (t["due"], -(t["priority"] if t["priority"] is not None else -1)))

    # Block position for the top strip — the daily note's own header line is the
    # source of truth ("📚 Study Day 6/60 — Block 1, day 6 of 6"). Null when
    # there is no daily note yet; the strip renders the absence honestly.
    block = None
    daily = VAULT / "01-Daily" / f"{today}.md"
    try:
        for line in daily.read_text(encoding="utf-8").splitlines():
            if line.startswith(">") and "📚" in line:
                block = line.lstrip("> ").strip("* ").strip()
                break
    except OSError:
        pass

    return {"today": today, "block": block, "tasks": tasks}


@router.get("/tasks")
def api_tasks():
    return _cached("tasks", 15, _scan_tasks)


# --------------------------------------------------------------------------
# GET /api/proposals
# --------------------------------------------------------------------------

def _title_of(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _scan_proposals() -> dict:
    out = {"pending": [], "approved": [], "staged": [], "applied_recent": []}
    for p in sorted(rf.PROPOSALS.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = frontmatter(text)
        row = {"file": p.name, "title": _title_of(text, p.stem),
               "kind": fm.get("kind"), "target": fm.get("target"),
               "risk": fm.get("risk"), "date": fm.get("date"),
               "status": fm.get("status")}
        status = fm.get("status")
        if status == "pending":
            out["pending"].append(row)
        elif status == "approved" and fm.get("staged"):
            row["staged"] = fm.get("staged")
            out["staged"].append(row)
        elif status == "approved":
            out["approved"].append(row)
        elif status == "applied":
            out["applied_recent"].append(row)
    # Applied history matters for the ledger later, not for "waiting on you" —
    # keep only enough to show the loop is alive.
    out["applied_recent"] = sorted(out["applied_recent"],
                                   key=lambda r: r["date"] or "", reverse=True)[:5]
    return out


@router.get("/proposals")
def api_proposals():
    return _cached("proposals", 15, _scan_proposals)


# --------------------------------------------------------------------------
# GET /api/projects
# --------------------------------------------------------------------------

def _repo_state(repo: Path) -> dict | None:
    if not (repo / ".git").exists():
        return None
    porcelain = _git(["status", "--porcelain"], repo)
    last = _git(["log", "-1", "--format=%cI\x1f%s"], repo)
    when, subject = (last.split("\x1f", 1) + [""])[:2] if last else (None, "")
    unpushed = _git(["rev-list", "--count", "@{u}..HEAD"], repo)  # None = no upstream
    return {"branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], repo),
            "dirty": len(porcelain.splitlines()) if porcelain is not None else None,
            "last_commit": when, "last_subject": subject,
            "unpushed": int(unpushed) if unpushed and unpushed.isdigit() else None}


def _scan_projects() -> dict:
    hubs = sorted((VAULT / "03-Projects").glob("*.md"))
    rels = [_rel(p) for p in hubs]
    sealed = _gitignored(rels)
    projects = []
    for p, rel in zip(hubs, rels):
        if rel in sealed:
            continue        # the internship hub renders in its own lane, Phase 5
        try:
            fm = frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if fm.get("type") != "project":
            continue
        repo = fm.get("repo")
        projects.append({
            "name": p.stem, "status": fm.get("status"), "area": fm.get("area"),
            "started": fm.get("started"), "due": fm.get("due"), "repo": repo,
            "git": _repo_state(Path(repo)) if repo else None,
        })
    return {"projects": projects}


@router.get("/projects")
def api_projects():
    return _cached("projects", 30, _scan_projects)


# --------------------------------------------------------------------------
# GET /api/window
# --------------------------------------------------------------------------

@router.get("/window")
def api_window():
    """Honestly unknown. Nothing in the system meters the subscription window
    today and no documented API exposes headroom — see dashboard-plan §8. The
    shape is fixed now so the top strip does not change when the Phase 4 spike
    fills it in; until then the meter renders the absence, not a guess."""
    return {"known": False, "percent": None, "reserved": None,
            "note": "no data source yet — metering is the Phase 4 spike"}
