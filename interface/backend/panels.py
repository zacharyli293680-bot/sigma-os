#!/usr/bin/env python3
"""
panels.py — the dashboard's read API (Phase 0 of the dashboard plan).

Five endpoints, all read-only, all serving panels in the web dashboard:

    GET /api/fleet       who the specialists are, when they last ran, who is due
    GET /api/tasks       open checkboxes with due dates, pulled from the notes
    GET /api/proposals   what is waiting on Zach — pending, approved, staged
    GET /api/projects    project hubs + live git state of their repos
    GET /api/window      rate-limit headroom — honestly unknown until the Phase 4 spike
    GET /api/nosync      the audit view: everything that never leaves this machine
    GET /api/lesson      study mode S1: every guide module, with its problems
    GET /api/lesson/{course}/{module}   one parsed module for the workbench
    GET /api/courses     study mode S2: active courses with guide progress
    GET /api/guide/{course}             one course's chain + blueprint status

Nothing here writes, and nothing here calls a model. Two boundaries hold:

- **Sealed material stays sealed.** Gitignored notes do not appear in these
  responses — *except* the model-boundary exemptions in privacy.config.json
  (Option B, 2026-07-30): those are readable by the agent and shown by the
  panels while still never syncing. Everything gitignored and unlisted stays
  hidden, fail-closed. (Asymmetry worth knowing: the old ProCertus session
  logs remain sealed even though the material they summarize is exempt.)
- **What is shown but never syncs is marked** (Phase 5). Tasks, projects and
  graph nodes carry `no_sync`, and `/api/nosync` is the same fact totalled.
  The two halves come from one `git check-ignore` pass and fail in opposite
  directions on purpose — see privacy.gitignore_scan.
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
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse

from agent import VAULT
from privacy import gitignore_scan, model_allow_prefixes, model_allow_raw

# The runtime modules own the facts these panels display; recomputing them here
# would be a second copy that can disagree (the watchdog's cardinal rule).
_RUNTIME = str(Path(__file__).resolve().parents[2] / "runtime")
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
import agenda as ag         # noqa: E402  — the calendar resolver
import fleet as fl          # noqa: E402
import leetcode as lc       # noqa: E402  — the daily-practice habit
import lesson as ln         # noqa: E402  — the study-guide module grammar
import reflect as rf        # noqa: E402
import retro                # noqa: E402  — the 06:00 review; NOT backend/review.py
import specialists as sp    # noqa: E402
import todo as td           # noqa: E402
from sigma import frontmatter  # noqa: E402

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------
# small shared machinery
# --------------------------------------------------------------------------

_cache: dict = {}

# The panels derived from checkbox state. Both must be dropped together after
# any write that ticks, unticks or adds a task — they are two views of one fact,
# and three call sites in writes.py each spelled their own key list. Naming the
# coupling once is the difference between "the work view lags 15 seconds after a
# tick" being a bug someone finds and it being impossible.
#
# Deliberately not a blanket invalidate-everything: `graph` is cached for 300s
# because rebuilding it is expensive, and a new graph identity relays the sky —
# ticking a box must not do that.
#
# `courses` joined in S2: its progress counts are read off the same checkbox
# state, so a completed or skipped module must reach it in the same breath.
TASK_PANELS = ("tasks", "queue", "courses")


def drop_task_caches(*extra):
    """Drop every cache derived from checkbox state — including the resolver's.

    Since P2, `/api/tasks` is a projection of `agenda.collect()`, so there are
    now **two** caches behind one panel. Popping the panel key alone recomputes
    the projection from the resolver's still-warm 15s scan, and the task you
    just ticked comes straight back — which is the exact 15-second lag
    TASK_PANELS was named to make impossible. Two caches, one fact, one
    function that drops both.

    `/api/queue/meta` deliberately does not call this: snooze, pin and archive
    live in the sidecar, and the resolver never reads the sidecar.
    """
    for key in (*TASK_PANELS, *extra):
        _cache.pop(key, None)
    ag.invalidate(VAULT)


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
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _split(paths: list) -> tuple:
    """(sealed, no_sync) — what to hide, and what to mark, from one pass.

    Delegates to privacy.gitignore_scan, the single implementation of the
    boundary; a second copy here once disagreed with it about git's failure
    exit codes, which is exactly the drift the one-implementation rule exists
    to prevent."""
    sealed, no_sync, _ = gitignore_scan(VAULT, paths)
    return sealed, no_sync


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
# GET /api/guide/progress — the generation pipeline's live feed (study S7)
# --------------------------------------------------------------------------

_GUIDE_PROGRESS_PATH = Path(_RUNTIME) / "guide.progress.json"


@router.get("/guide/progress")
async def api_guide_progress():
    """guide.py's progress file, streamed the way the fleet's is — a file
    poll, deliberately uncoupled from the pipeline process, because a run can
    also be started from the CLI and must render here all the same."""
    async def stream():
        last, quiet = None, 0
        while True:
            payload = None
            try:
                obj = json.loads(_GUIDE_PROGRESS_PATH.read_text(encoding="utf-8"))
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
# GET /api/fleet/fire — what the fleet is touching (dashboard-plan section 5)
# --------------------------------------------------------------------------

_FIRE_PATH = Path(_RUNTIME) / "fleet.fire.jsonl"


@router.get("/fleet/fire")
async def api_fleet_fire():
    """Tool calls made by the fleet, tailed out of the file it appends to.

    The brain fires on these, so an unattended 09:00 run sweeps the clusters it
    reads instead of being represented only by four arcs. A file poll rather
    than any coupling to the fleet process, for the same reason the progress
    stream above is one: the run that matters is the one this server did not
    launch.

    It starts at the *end* of the file and never replays it. This view's single
    promise is that motion means something just happened; a page reload that
    re-lit this morning's reads would break exactly that. A partial trailing
    line is held back rather than parsed, so an append caught mid-write is
    delivered whole on the next poll instead of being dropped.
    """
    async def stream():
        try:
            pos = _FIRE_PATH.stat().st_size
        except OSError:
            pos = 0
        buf, quiet = "", 0
        while True:
            events = []
            try:
                size = _FIRE_PATH.stat().st_size
                if size < pos:
                    # Truncated: a new run just started. Skip to the end rather
                    # than re-reading from zero — the lines before this point
                    # belong to a run that has already finished.
                    pos, buf = size, ""
                if size > pos:
                    with _FIRE_PATH.open("rb") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        pos = fh.tell()
                    parts = (buf + chunk.decode("utf-8", "replace")).split("\n")
                    buf = parts.pop()
                    for line in parts:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            events.append(json.loads(line))
                        except ValueError:
                            pass
            except OSError:
                pass                        # no file yet: nothing has run
            if events:
                quiet = 0
                for e in events:
                    yield (f"data: {json.dumps(e, ensure_ascii=False, separators=(',', ':'))}"
                           f"\n\n")
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
#
# These lived here first and now live in todo.py, which is the other consumer of
# the same grammar. Two modules parsing one grammar with two regexes is exactly
# the drift the vault's one-implementation rule exists to stop — the same
# mistake this file's own _split() docstring records about the privacy boundary.
# Note todo.py excludes 01-Daily on top of these; that exclusion is the queue's
# alone, because the calendar strip is about dated work wherever it lives.
_TASK_RE = td.TASK_RE
_DUE_RE = td.DUE_RE
_PRIORITY = td.PRIORITY
_META_RE = td.META_RE
_EXCLUDED_TOPS = td.EXCLUDED_TOPS


def _ag_split(_vault, rels):
    """agenda.py passes the vault explicitly; `_split` closes over it. Same
    adapter `/api/queue` already uses, for the same reason."""
    return _split(rels)


def _scan_tasks() -> dict:
    """The Today panel, now a projection of the resolver rather than a second
    scan of the vault.

    This endpoint's own walk-and-parse is gone: it and `agenda.py` were two
    implementations of *what is due*, which is precisely the disagreement the
    calendar's one-resolver rule exists to prevent — and it had already drifted
    once, into `todo.py`, before that copy was pulled back out.

    The response shape is unchanged on purpose. `panels.tsx` and the 14-day
    strip read this today, and a phase that swaps the engine should not also
    move the wires; the shape changes at P3, when the rail replaces the strip.
    """
    today = datetime.date.today().isoformat()
    tasks = [{"text": o["title"], "due": o["date"], "priority": o["priority"],
              "overdue": o["date"] < today, "no_sync": o["no_sync"],
              # `raw` is the staleness token for POST /api/tasks/toggle: the
              # client hands back the exact line it saw, and the toggle refuses
              # if the note moved underneath it.
              "file": o["source"]["path"], "line": o["source"]["line"],
              "raw": o["raw"]}
             for o in ag.due_tasks(VAULT, split=_ag_split)]

    tasks.sort(key=lambda t: (t["due"], -(t["priority"] if t["priority"] is not None else -1)))

    # Block position for the top strip — the daily note's own header line is the
    # source of truth ("📚 Study Day 6/60 — Block 1, day 6 of 6"). Null when
    # there is no daily note yet; the strip renders the absence honestly.
    block = None
    daily = VAULT / "01-Daily" / f"{today}.md"
    try:
        for line in daily.read_text(encoding="utf-8").splitlines():
            if line.startswith(">") and "📚" in line:
                # bold markers render literally in a <span>, so they go
                block = line.lstrip("> ").replace("**", "").strip("* ").strip()
                break
    except OSError:
        pass

    return {"today": today, "block": block, "tasks": tasks}


@router.get("/tasks")
def api_tasks():
    return _cached("tasks", 15, _scan_tasks)


# --------------------------------------------------------------------------
# GET /api/agenda — the calendar (agenda.py)
# --------------------------------------------------------------------------

AGENDA_DEFAULT_DAYS = 14        # the horizon the 14-day strip already shows


def _is_date(s) -> bool:
    try:
        datetime.date.fromisoformat((s or "").strip())
        return True
    except (ValueError, TypeError):
        return False


@router.get("/agenda")
def api_agenda(frm: Annotated[str | None, Query(alias="from")] = None,
               to: Annotated[str | None, Query()] = None):
    """Everything on the calendar between two dates, from the one resolver.

    Deliberately **not** wrapped in `_cached`: the key would have to include the
    range, and `_cache` is a plain dict with no eviction — a month of navigating
    would leave a scan of the vault behind for every window visited. The cache
    that matters lives in `agenda.collect_cached`, keyed by vault, because the
    scan is the expensive part and projecting a range out of it is not.

    A refused range comes back as 400 carrying the resolver's own words, because
    "this range is not allowed" and "this range is empty" are different answers
    and a UI that cannot tell them apart will render the first as the second.
    """
    today = datetime.date.today()
    start = (frm or "").strip() or today.isoformat()
    if not _is_date(start):
        return JSONResponse({"error": "bad from", "detail": "expected YYYY-MM-DD"},
                            status_code=400)
    end = (to or "").strip() or (
        datetime.date.fromisoformat(start)
        + datetime.timedelta(days=AGENDA_DEFAULT_DAYS - 1)).isoformat()
    if not _is_date(end):
        return JSONResponse({"error": "bad to", "detail": "expected YYYY-MM-DD"},
                            status_code=400)

    got = ag.resolve(VAULT, start, end, split=_ag_split)
    if got.get("error"):
        return JSONResponse({"error": got["error"], "from": start, "to": end},
                            status_code=400)
    return {**got, "today": today.isoformat()}


# --------------------------------------------------------------------------
# GET /api/queue — the four priority queues (todo.py)
# --------------------------------------------------------------------------

@router.get("/queue")
def api_queue():
    """The Work view's whole payload.

    Unlike every other endpoint here this one has a side effect: todo.build
    writes the sidecar index. That is not incidental — reconciling the scan
    against the index is *how* a completion gets recorded, and the review that
    reads those records must see ticks made in Obsidian, not only ones made
    through the dashboard. The write is atomic and refuses to run over an index
    that existed but did not parse, so a torn read costs one stale cycle rather
    than every task's age.

    Same 15s TTL as /api/tasks, and the same privacy split — todo.py takes the
    splitter as an argument precisely so it does not grow its own copy of the
    boundary. `_split` closes over VAULT while todo.scan passes it explicitly,
    hence the adapter rather than the bare function.
    """
    return _cached("queue", 15, _queue_payload)


def _queue_payload() -> dict:
    """The four queues, plus the daily habit that is deliberately not in them.

    `practice` rides on this payload rather than getting its own endpoint
    because the Work view renders both in one grid and a second fetch would let
    them disagree for a frame after a completion. It is a *sibling* of
    `sections`, never a fifth section: the four queues hold checkbox lines that
    can be ticked, reordered, snoozed and moved, and a habit has none of those
    affordances — folding it in would mean every consumer of `sections`
    special-casing one member.
    """
    q = td.build(vault=VAULT, split=lambda _vault, rels: _split(rels))
    q["practice"] = lc.panel(VAULT)
    return q


# --------------------------------------------------------------------------
# GET /api/review — the latest 06:00 retrospective (retro.py)
# --------------------------------------------------------------------------

@router.get("/review")
def api_review():
    """The newest recorded review, or null when none has run.

    Reads the JSONL rather than the note, because the strip wants the numbers
    and the note is prose around them. Null is a real answer here — the strip
    says "no review yet" instead of rendering zero stars, which would claim a
    day was scored badly rather than not scored.
    """
    return {"latest": _cached("review", 60, retro.latest)}


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
    sealed, no_sync = _split(rels)
    projects = []
    for p, rel in zip(hubs, rels):
        if rel in sealed:
            continue        # gitignored and not exempted — hidden
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
            "no_sync": rel in no_sync,
            "git": _repo_state(Path(repo)) if repo else None,
        })
    return {"projects": projects}


@router.get("/projects")
def api_projects():
    return _cached("projects", 30, _scan_projects)


# --------------------------------------------------------------------------
# GET /api/graph — the brain's data (dashboard-plan D2)
# --------------------------------------------------------------------------
# The link resolver the plan once believed existed. The rules come from the
# vault contract's "Linking" section and the audit that checked it:
#   - case-insensitive; a target may be a bare basename or a full vault path
#   - `[[a\|b]]` (table-escaped pipe) and `[[a|b]]` both alias; `#heading` and
#     `#^block` anchors are stripped
#   - fenced code blocks and inline code spans are skipped entirely — a
#     backticked `[[wikilink]]` is the contract's own way of writing a
#     NON-link, so counting it would manufacture edges the vault refused
#   - a bare basename with several matches resolves same-folder first
#     (Obsidian's precedence, which the vault's in-folder links rely on)
#   - unresolved targets are dropped, matching graph.json's hideUnresolved

_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
_CODESPAN_RE = re.compile(r"`[^`]*`")
_GRAPH_SKIP_TOPS = {".obsidian", ".claude", ".git", "Excalidraw"}


def _bucket_of(rel: str) -> str:
    """The same nine colour groups as .obsidian/graph.json, so the web brain
    and Obsidian's graph are one picture of one vault."""
    if "/" not in rel:
        return "root"
    top = rel.split("/", 1)[0]
    if rel.startswith("02-Areas/Academics"):
        return "academics"
    return {"00-Inbox": "inbox", "01-Daily": "daily", "02-Areas": "areas",
            "03-Projects": "projects", "06-System": "system",
            "05-Archive": "archive"}.get(top, "meta")


def _link_target(raw: str) -> str | None:
    t = re.split(r"\\\||\|", raw, maxsplit=1)[0]      # alias off, escaped or not
    t = t.split("#", 1)[0].strip().rstrip("\\").strip()
    return t or None


def _build_graph() -> dict:
    files = []
    for p in VAULT.rglob("*.md"):
        rel = _rel(p)
        top = rel.split("/", 1)[0]
        if top in _GRAPH_SKIP_TOPS or top.startswith("."):
            continue
        files.append((p, rel))
    sealed, no_sync = _split([rel for _, rel in files])
    files = [(p, rel) for p, rel in files if rel not in sealed]

    nodes, idx_of = [], {}
    by_path: dict = {}
    by_base: dict = {}
    for p, rel in files:
        idx_of[rel] = len(nodes)
        by_path[rel[:-3].lower()] = rel                # path without .md
        by_base.setdefault(p.stem.lower(), []).append(rel)
        try:
            mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
            mtime = mtime.isoformat(timespec="seconds")
        except OSError:
            mtime = None
        # `no_sync` rides alongside `bucket` rather than replacing it: the
        # brain's colours are the vault's eight bucket groups (dashboard-plan
        # §2) and overloading one of them would make the picture disagree with
        # Obsidian's graph. The renderer draws a bronze ring instead.
        nodes.append({"id": rel, "label": p.stem, "bucket": _bucket_of(rel),
                      "no_sync": rel in no_sync, "inlinks": 0, "mtime": mtime})

    def resolve(target: str, src_rel: str) -> str | None:
        t = target.replace("\\", "/").strip("/").lower()
        if t.endswith(".md"):
            t = t[:-3]
        if "/" in t:
            return by_path.get(t)
        matches = by_base.get(t)
        if not matches:
            return None
        if len(matches) > 1:
            folder = src_rel.rsplit("/", 1)[0] if "/" in src_rel else ""
            same = [m for m in matches
                    if (m.rsplit("/", 1)[0] if "/" in m else "") == folder]
            if same:
                return same[0]
        return sorted(matches)[0]

    links = set()
    for p, rel in files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        in_fence = False
        for line in text.splitlines():
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for raw in _WIKILINK_RE.findall(_CODESPAN_RE.sub("", line)):
                target = _link_target(raw)
                dst = resolve(target, rel) if target else None
                if dst and dst != rel:
                    links.add((idx_of[rel], idx_of[dst]))

    for _, dst in links:
        nodes[dst]["inlinks"] += 1
    return {"notes": len(nodes), "edges": len(links),
            "nodes": nodes, "links": sorted(links)}


@router.get("/graph")
def api_graph():
    return _cached("graph", 300, _build_graph)


# --------------------------------------------------------------------------
# GET /api/nosync — the audit view (dashboard-plan §6, Phase 5)
# --------------------------------------------------------------------------
# The lens the marker earns: everything that never leaves this machine, in one
# list. Nothing exclusive lives here — every one of these files also appears in
# Today, Projects or the brain, marked. What this view adds is the *total*,
# which no other surface can show, and which is the honest answer to a question
# the inline marker cannot answer: what exists on one disk only?
#
# Not .md-only, deliberately. The carve-out holds PDFs and images too, and this
# is the list you would hand a backup job.

_NOSYNC_CAP = 500


def _scan_nosync() -> dict:
    files = []
    for p in VAULT.rglob("*"):
        if not p.is_file():
            continue
        rel = _rel(p)
        top = rel.split("/", 1)[0]
        if top.startswith(".") or top == ".git":
            continue
        files.append((p, rel))

    rels = [rel for _, rel in files]
    _, no_sync, answered = gitignore_scan(VAULT, rels)

    rows = []
    for p, rel in files:
        if rel not in no_sync:
            continue
        try:
            st = p.stat()
            mtime = datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
            size = st.st_size
        except OSError:
            mtime, size = None, None
        rows.append({"path": rel, "mtime": mtime, "bytes": size})
    rows.sort(key=lambda r: r["path"].lower())

    # Group under the declared prefixes, so the view reads as the carve-out
    # rather than as a flat pile. A file matching no prefix cannot happen —
    # membership in no_sync *is* prefix membership — but the bucket exists so a
    # future change cannot silently drop rows.
    # Match on the normalised prefixes, label with what the operator wrote.
    prefixes = model_allow_prefixes()
    shown = dict(zip(prefixes, model_allow_raw()))
    groups = {pre: [] for pre in prefixes}
    other = []
    for r in rows:
        low = r["path"].lower()
        hit = next((pre for pre in prefixes if low == pre or low.startswith(pre + "/")), None)
        (groups[hit] if hit else other).append(r)

    out = [{"prefix": shown.get(pre, pre), "count": len(items),
            "bytes": sum(i["bytes"] or 0 for i in items),
            "newest": max((i["mtime"] for i in items if i["mtime"]), default=None)}
           for pre, items in groups.items()]
    if other:
        out.append({"prefix": "(unlisted)", "count": len(other),
                    "bytes": sum(i["bytes"] or 0 for i in other),
                    "newest": max((i["mtime"] for i in other if i["mtime"]), default=None)})

    return {
        # False = git could not answer, so the boundary is unverified and the
        # UI must say so rather than render an empty, reassuring list.
        "ok": answered,
        "total": len(rows),
        "bytes": sum(r["bytes"] or 0 for r in rows),
        "groups": sorted(out, key=lambda g: g["prefix"]),
        "files": rows[:_NOSYNC_CAP],
        # No silent caps: if the list is trimmed, the view says by how much.
        "truncated": max(0, len(rows) - _NOSYNC_CAP),
    }


@router.get("/nosync")
def api_nosync():
    return _cached("nosync", 30, _scan_nosync)


# --------------------------------------------------------------------------
# GET /api/study — exam mode (dashboard-plan Phase 6)
# --------------------------------------------------------------------------
# [[dashboard-vision]] asks for "everything for one exam in one place: what is
# covered, what you have not practised, what you got wrong last time".
#
# The first two are computable and are what this returns. The third is not:
# nothing in the vault records a wrong answer, so inventing a "weak areas"
# number would be a confident guess dressed as data. It is left out rather than
# faked, and the UI says so.
#
# **"Not practised" is defined as: a source file no note embeds.** Intake writes
# `> Source: ![[the-file]]` into every note it produces, and hand-written notes
# use the same Obsidian embed, so the embed set *is* the record of what has been
# worked through. That makes coverage a fact about the vault rather than a
# heuristic about filenames — which matters here, because AA-210's `EX1*`/`SG1`
# naming is one course's convention and would not survive contact with the next.

_COVERAGE_SAMPLE = 12


def _embedded_sources(folder: Path) -> set:
    """Every `![[target]]` embedded by any note under this course."""
    out = set()
    for p in folder.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in re.findall(r"!\[\[([^\]|#]+)", text):
            out.add(raw.strip().rsplit("/", 1)[-1].lower())
    return out


def _scan_study() -> dict:
    root = VAULT / "02-Areas" / "Academics"
    exams, coverage = [], []
    if not root.is_dir():
        # Same shape as the full payload — study.tsx reads practice.length
        # unguarded, and a key that exists only on the happy path is a crash
        # in the one vault state (no Academics folder) no test naturally has.
        return {"exams": [], "coverage": [], "practice": []}

    today = datetime.date.today()
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        course = folder.name
        rels = [_rel(p) for p in folder.rglob("*.md")]
        sealed, _ = _split(rels)

        for p in sorted(folder.rglob("*.md")):
            rel = _rel(p)
            if rel in sealed:
                continue
            try:
                fm = frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if (fm or {}).get("type") != "exam-prep":
                continue
            raw_date = str(fm.get("date") or "").strip()
            days = None
            if raw_date:
                try:
                    days = (datetime.date.fromisoformat(raw_date) - today).days
                except ValueError:
                    days = None
            exams.append({
                "course": course, "exam": str(fm.get("exam") or "").strip() or None,
                # Blank is the honest answer when the source never stated one —
                # every AA-210 study guide is in exactly that position.
                "date": raw_date or None, "days": days,
                "status": str(fm.get("status") or "").strip() or None,
                "file": rel, "title": _title_of(p.read_text(encoding="utf-8",
                                                            errors="replace"), p.stem),
            })

        sources = [q for q in folder.rglob("*") if q.is_file() and q.suffix.lower() != ".md"]
        if not sources:
            continue
        embedded = _embedded_sources(folder)
        uncovered = [q for q in sources if q.name.lower() not in embedded]
        by_folder: dict = {}
        for q in sources:
            grp = q.parent.relative_to(folder).as_posix() or "."
            g = by_folder.setdefault(grp, {"total": 0, "covered": 0})
            g["total"] += 1
            if q.name.lower() in embedded:
                g["covered"] += 1
        coverage.append({
            "course": course,
            "sources": len(sources), "covered": len(sources) - len(uncovered),
            "by_folder": [{"folder": k, **v} for k, v in sorted(by_folder.items())],
            "uncovered_sample": [_rel(q) for q in uncovered[:_COVERAGE_SAMPLE]],
            # No silent caps.
            "uncovered_more": max(0, len(uncovered) - _COVERAGE_SAMPLE),
        })

    # Dated exams first and soonest-first; undated ones after, since an exam
    # with no date cannot be ranked against one that has one.
    exams.sort(key=lambda e: (e["days"] is None, e["days"] if e["days"] is not None else 0,
                              e["course"], e["exam"] or ""))
    return {"exams": exams, "coverage": coverage, "practice": _practice_history()}


def _practice_history() -> list:
    """What was actually missed, per course — exam mode's third panel
    (study S6), built on the attempt log the practice engine writes.

    Two sources, both named in the payload rather than blended: `by_topic`
    comes from the machine-local attempt log (fine-grained, gitignored,
    losable), and `sessions` are the durable digest rows the rollup spliced
    into the course's study log. When both are empty the course is omitted
    and the view keeps its honest empty state — this panel was deliberately
    absent until a real source existed, and it must never show a guess."""
    root = VAULT / "02-Areas" / "Academics"
    out = []
    if not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        course = folder.name
        # A sealed course stays sealed even in derived data: the attempt log
        # outlives a later carve-out of its source notes (the ProCertus
        # pattern), and segment titles copied into it at attempt time would
        # otherwise keep rendering here forever. The study log's sealing
        # stands in for the course's — a carved-out course folder seals every
        # file in it, this one included, whether or not it exists yet.
        rel = _rel(folder / f"{course.lower()}-study-log.md")
        sealed, _ = _split([rel])
        if rel in sealed:
            continue
        rows = ln.attempts_for(course)
        answered = [r for r in rows if r.get("result") in ("correct", "wrong")]
        wrong = [r for r in rows if r.get("result") == "wrong"]
        by: dict = {}
        for r in wrong:
            key = str(r.get("seg_title") or r.get("qid") or "?").strip()
            g = by.setdefault(key, {"topic": key, "wrong": 0, "last": ""})
            g["wrong"] += 1
            g["last"] = max(g["last"], str(r.get("ts") or "")[:10])

        sessions = []
        log_p = folder / f"{course.lower()}-study-log.md"
        if log_p.is_file():          # its sealing was judged above, for the course
            try:
                text = log_p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            in_sessions = False
            for line in text.splitlines():
                if line.strip() == "## Sessions":
                    in_sessions = True
                    continue
                if in_sessions and line.startswith("#"):
                    break
                if in_sessions and line.lstrip().startswith("- "):
                    sessions.append(line.lstrip()[2:].strip())
            sessions.reverse()               # newest rollup row last in the note

        if not rows and not sessions:
            continue
        out.append({
            "course": course,
            "attempts": len(rows), "answered": len(answered),
            "wrong": len(wrong),
            "last": max((str(r.get("ts") or "")[:10] for r in rows), default=None),
            "by_topic": sorted(by.values(),
                               key=lambda g: (-g["wrong"], g["topic"])),
            "sessions": sessions[:10],
            "sessions_more": max(0, len(sessions) - 10),
        })
    return out


@router.get("/study")
def api_study():
    return _cached("study", 60, _scan_study)


# --------------------------------------------------------------------------
# study mode S1 — guide modules, through the one grammar owner (lesson.py)
# --------------------------------------------------------------------------
def _lesson_split(_vault, rels):
    return _split(rels)


def _scan_lesson_list() -> dict:
    # Checkpoints ride the same list (study S6): the chain view joins its rows
    # to what is openable by wikilink basename, and a checkpoint row is
    # openable exactly like a module row.
    return {"modules": ln.scan(VAULT, split=_lesson_split),
            "checkpoints": ln.scan_checkpoints(VAULT, split=_lesson_split)}


@router.get("/lesson")
def api_lesson_list():
    # 30s: one parse per module note; the vault has a handful of modules.
    return _cached("lesson", 30, _scan_lesson_list)


@router.get("/lesson/{course}/{module_no}")
def api_lesson(course: str, module_no: int):
    # Parsed fresh on every call, never cached — editing the note in Obsidian
    # must never disagree with the workbench. The queue's rule, kept.
    d = ln.load(VAULT, course, module_no, split=_lesson_split)
    if d is None:
        return JSONResponse({"error": "no such module",
                             "detail": f"{course} M{module_no}"}, status_code=404)
    # The sidecar's view state rides along so a reopened module resumes where
    # it was — keyed on the note's own course spelling, which is what the
    # state write stores under.
    d["state"] = ln.load_state()["modules"].get(f"{d['course']}/{module_no}")
    return d


@router.get("/checkpoint/{course}/{cp_no}")
def api_checkpoint(course: str, cp_no: int):
    # Fresh like the lesson detail, and for the same reason. The sidecar key
    # is `<course>/cp<n>` — the `cp` prefix is what keeps checkpoint state
    # from colliding with module state in the same map.
    d = ln.load_checkpoint(VAULT, course, cp_no, split=_lesson_split)
    if d is None:
        return JSONResponse({"error": "no such checkpoint",
                             "detail": f"{course} CP{cp_no}"}, status_code=404)
    d["state"] = ln.load_state()["modules"].get(f"{d['course']}/cp{cp_no}")
    return d


# --------------------------------------------------------------------------
# study mode S2 — the course dashboard's two reads
# --------------------------------------------------------------------------

def _scan_courses() -> dict:
    """Every active course with its guide presence, progress and frontier —
    GET /api/courses (study plan §10). 'Active' is todo.active_courses'
    answer, the same one the queue gives, never a second definition."""
    modules = ln.scan(VAULT, split=_lesson_split)
    out = []
    for code, name in td.active_courses(VAULT).items():
        folder = VAULT / "02-Areas" / "Academics" / code
        g = ln.guide(VAULT, code, split=_lesson_split)
        if g is not None:
            g = {k: v for k, v in g.items() if k != "rows"}
        mine = [m for m in modules if m["course"].lower() == code.lower()]
        out.append({
            "course": code, "name": name,
            "timeline": (folder / f"{code.lower()}-timeline.md").is_file(),
            "modules": len(mine),
            "held": sum(1 for m in mine if m["problems"]),
            "guide": g,
        })
    return {"courses": out}


@router.get("/courses")
def api_courses():
    return _cached("courses", 30, _scan_courses)


@router.get("/guide/{course}")
def api_guide(course: str):
    # Fresh on every call, like the lesson detail: completing or skipping a
    # row must render on the very next fetch, and a chain is one small file.
    g = ln.guide(VAULT, course, split=_lesson_split)
    if g is None:
        return JSONResponse({"error": "no guide chain",
                             "detail": f"{course} has no <code>-guide.md"},
                            status_code=404)
    return g


# --------------------------------------------------------------------------
# GET /api/repos — repo awareness (dashboard-plan Phase 6)
# --------------------------------------------------------------------------
# [[dashboard-vision]]: "what is dirty, what is unpushed, what has not been
# touched in three weeks, which hub notes have gone stale against their code."
#
# The Projects panel already answers this for hubs that *declare* a `repo:`.
# What it cannot see is the other direction — a repo with no hub at all — and
# that is the more useful half, because a project you never wrote a hub for is
# exactly the one you will forget.
#
# **The scan root is derived, not configured.** The vault already knows where
# code lives: every project hub carries an absolute `repo:` path, so the folder
# most of them share is the code folder. That avoids both a hardcoded
# `C:\Users\...` and a config file nobody remembers to update — and it moves by
# itself when the hubs do. Sealed hubs never reach this, so an internship repo
# outside that folder stays invisible here as it does everywhere else.

STALE_DAYS = 21


def _hub_notes() -> list:
    """Project hubs, active and archived, minus anything sealed.

    `05-Archive/Projects/` counts. A finished project is not a gap: reporting an
    archived repo as "no hub note" would train you to ignore the one signal this
    panel exists to give. Found immediately — `team20` was archived the day this
    shipped and promptly reappeared as a fault.
    """
    out = []
    for folder in (VAULT / "03-Projects", VAULT / "05-Archive" / "Projects"):
        if not folder.is_dir():
            continue
        out.extend(sorted(folder.glob("*.md")))
    rels = [_rel(p) for p in out]
    sealed, _ = _split(rels)
    return [p for p, r in zip(out, rels) if r not in sealed]


def _archived(p: Path) -> bool:
    return "05-Archive" in p.parts


def _code_root() -> Path | None:
    """The folder most project hubs point into."""
    parents: dict = {}
    for p in _hub_notes():
        rel = _rel(p)
        sealed, _ = _split([rel])
        if rel in sealed:
            continue
        try:
            fm = frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        repo = str((fm or {}).get("repo") or "").strip()
        if not repo:
            continue
        try:
            parent = Path(repo).resolve().parent
        except (OSError, ValueError):
            continue
        parents[parent] = parents.get(parent, 0) + 1
    if not parents:
        return None
    return max(parents.items(), key=lambda kv: kv[1])[0]


def _scan_repos() -> dict:
    root = _code_root()
    if root is None or not root.is_dir():
        return {"root": None, "repos": [], "note": "no project hub declares a repo path"}

    # Hub notes by the repo path they claim, so a repo can find its hub.
    hubs: dict = {}
    for p in _hub_notes():
        rel = _rel(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = frontmatter(text) or {}
        except OSError:
            continue
        repo = str(fm.get("repo") or "").strip()
        if not repo:
            continue
        try:
            hubs[str(Path(repo).resolve()).lower()] = (p, fm)
        except (OSError, ValueError):
            continue

    now = datetime.datetime.now()
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / ".git").exists():
            continue
        state = _repo_state(d) or {}
        last = state.get("last_commit")
        idle = None
        if last:
            try:
                dt = datetime.datetime.fromisoformat(str(last))
                if dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                idle = (now - dt).days
            except ValueError:
                idle = None

        hub_p, hub_fm = hubs.get(str(d.resolve()).lower(), (None, None))
        hub_stale = None
        if hub_p is not None and last:
            # "Stale" = the code moved on after the hub note last did. Measured
            # against the note's own mtime rather than a field, because a hub
            # that is being maintained gets touched.
            try:
                hub_mtime = datetime.datetime.fromtimestamp(hub_p.stat().st_mtime)
                dt = datetime.datetime.fromisoformat(str(last))
                if dt.tzinfo:
                    dt = dt.replace(tzinfo=None)
                hub_stale = max(0, (dt - hub_mtime).days)
            except (OSError, ValueError):
                hub_stale = None

        out.append({
            "name": d.name, "path": str(d),
            "branch": state.get("branch"), "dirty": state.get("dirty"),
            "unpushed": state.get("unpushed"),
            "last_commit": last, "last_subject": state.get("last_subject", ""),
            "idle_days": idle,
            "hub": hub_p.stem if hub_p is not None else None,
            "hub_archived": bool(hub_p is not None and _archived(hub_p)),
            "hub_status": (hub_fm or {}).get("status") if hub_fm else None,
            "hub_stale_days": hub_stale,
        })

    return {
        "root": str(root),
        "repos": out,
        "orphans": [r["name"] for r in out if r["hub"] is None],
        # An archived project is finished, so neither a stale hub nor an idle
        # repo is a finding for it — that is what archiving means.
        "stale_hubs": [r["name"] for r in out
                       if r["hub"] and not r["hub_archived"]
                       and (r["hub_stale_days"] or 0) > 0],
        "idle": [r["name"] for r in out
                 if not r["hub_archived"] and r["idle_days"] is not None
                 and r["idle_days"] >= STALE_DAYS],
        "stale_days": STALE_DAYS,
    }


@router.get("/repos")
def api_repos():
    # 30s: every row shells out to git several times.
    return _cached("repos", 30, _scan_repos)


# --------------------------------------------------------------------------
# GET /api/window
# --------------------------------------------------------------------------

@router.get("/window")
def api_window():
    """Proxies, honestly labelled. The Phase 4 spike concluded no documented
    API exposes subscription headroom, so a percentage would be a guess dressed
    as a fact. What CAN be known is persisted by sigma/spend.py — observed
    calls, notional cost where the SDK reports one, and the load-bearing
    signal: when a call last hit the rate limit. `known` is the string "proxy"
    rather than True, so the UI can never mistake this for a real meter."""
    def compute():
        from sigma import spend
        win = spend.window()
        paused = (fl.load_state() or {}).get("paused") or None
        return {"known": "proxy", "percent": None,
                "calls": win["calls"], "cost_usd": win["cost_usd"],
                "last_rate_limit": win["last_rate_limit"],
                "paused": bool(paused),
                "resume_at": (paused or {}).get("resume_at"),
                "reserved": "the daily 09:00 fleet run",
                "note": "observed spend + last rate-limit event; "
                        "true headroom is not exposed by anything",
                # The UI's obsidian:// links need the vault's real name; guessing
                # it from a 45s health probe left links dead on first paint.
                "vault": VAULT.name}
    return _cached("window", 15, compute)
