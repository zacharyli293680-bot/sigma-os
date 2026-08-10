#!/usr/bin/env python3
"""
commands.py — the palette's backend (dashboard-plan D3).

A fixed verb list, not a shell. Every verb maps to a pre-built argv for the
script that already owns the job (the cli.py doctrine: one implementation of
each behaviour), and nothing user-supplied ever reaches a command line — the
verb is a dictionary key, and an unknown key is a 404. That is the whole
security model, and it is testable: there is no string to escape because
there is no string.

One job at a time, deliberately. Concurrency is the budget under
subscription-only, and the fleet already holds its own lock — a second gate
here means the palette cannot even *try* to overlap work. A busy runner
answers 409 with what is running.

Three CLI verbs are exposed as *disabled* entries rather than omitted —
`reflect merge` (the one operation that overwrites a note; Phase 4 brings
the diff UI it deserves), `install` (system configuration), and `ui` (you
are already in it). Disabled beats hidden: a greyed row teaches the
boundary, a missing one leaves you guessing. Same doctrine as the sealed lane.
"""
import asyncio
import datetime
import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

RUNTIME = Path(__file__).resolve().parents[2] / "runtime"

router = APIRouter(prefix="/api/commands")
guide_router = APIRouter(prefix="/api/guide")


def _script(name: str, *flags: str) -> list:
    # sys.executable is the interface venv's python — the superset interpreter
    # cli.py prefers for every subcommand, so one rule holds here too.
    return [sys.executable, str(RUNTIME / name), *flags]


# The whitelist. `writes` marks verbs that can change the vault (through the
# OS's existing gates — apply only executes what Zach approved); `model` marks
# verbs that spend rate-limit window. The palette shows both honestly.
VERBS: dict = {
    "status":          {"title": "Status — what needs attention",
                        "hint": "doctor + fleet + waiting proposals",
                        "argv": _script("cli.py", "status"), "timeout": 180},
    "doctor":          {"title": "Doctor — run the health checks",
                        "hint": "the seven watchdog checks, on demand",
                        "argv": _script("doctor.py"), "timeout": 180},
    "fleet-status":    {"title": "Fleet — status",
                        "hint": "who ran, when, what they raised",
                        "argv": _script("fleet.py", "--status"), "timeout": 60},
    "fleet-run":       {"title": "Fleet — run everything due",
                        "hint": "sequenced; the reactor shows it live",
                        # 4 specialists × 420s + SDK cold starts; 1800 left a
                        # legitimate full run ~2 minutes from being killed.
                        "argv": _script("fleet.py"), "timeout": 2400, "model": True},
    # `fleet-run-planner` was here. The planner was retired when the todo list
    # became self-maintaining queues — see specialists.py. What answers the
    # question it used to is `review`, below, and the WORK view (Ctrl+;).
    "review":            {"title": "Review — score yesterday",
                          "hint": "the 06:00 retrospective, run now",
                          "argv": _script("retro.py"),
                          "timeout": 300, "model": True},
    "review-status":     {"title": "Review — status",
                          "hint": "last scored day, and the schedule",
                          "argv": _script("retro.py", "--status"), "timeout": 60},
    "fleet-run-coach":   {"title": "Fleet — run the coach now",
                          "hint": "timeline drift check",
                          "argv": _script("fleet.py", "--only", "coach"),
                          "timeout": 600, "model": True},
    "fleet-run-auditor": {"title": "Fleet — run the auditor now",
                          "hint": "frontmatter vs the contract",
                          "argv": _script("fleet.py", "--only", "auditor"),
                          "timeout": 600, "model": True},
    "fleet-run-tracker": {"title": "Fleet — run the tracker now",
                          "hint": "stale application pipeline check",
                          "argv": _script("fleet.py", "--only", "tracker"),
                          "timeout": 600, "model": True},
    "reflect-status":  {"title": "Reflect — status",
                        "hint": "unreflected logs, skills, proposals",
                        "argv": _script("reflect.py", "--status"), "timeout": 60},
    "reflect-run":     {"title": "Reflect — distil insights + proposals",
                        "hint": "the weekly loop, on demand",
                        "argv": _script("reflect.py"), "timeout": 900, "model": True},
    "reflect-apply":   {"title": "Reflect — apply approved proposals",
                        "hint": "executes only what you approved",
                        "argv": _script("reflect.py", "--apply"),
                        "timeout": 300, "writes": True},
    "reflect-diff":    {"title": "Reflect — diff staged changes",
                        "hint": "what each staged change would do",
                        "argv": _script("reflect.py", "--diff"), "timeout": 120},
    "capture-status":  {"title": "Capture — status",
                        "hint": "session-logging self-check + backlog",
                        "argv": _script("session_logger.py", "--status"), "timeout": 120},
    "capture-sweep":   {"title": "Capture — sweep missed sessions",
                        "hint": "log anything the hook missed",
                        "argv": _script("session_logger.py", "--sweep"),
                        "timeout": 600, "model": True},
    # Study intake (Phase 6). No path argument by design — the source is the
    # drop folder, so the verb stays a dictionary key like every other one.
    "intake-status":   {"title": "Intake — what is waiting",
                        "hint": "files dropped in 00-Inbox/intake/<COURSE>/",
                        "argv": _script("intake.py", "--status"), "timeout": 60},
    "intake":          {"title": "Intake — read the drop folder",
                        "hint": "course material in, contract-shaped notes out",
                        # Several documents × a multi-note conversation each.
                        "argv": _script("intake.py"),
                        "timeout": 2400, "model": True, "writes": True},
    # Dev log (Phase 6). No argument for the same reason intake has none: the
    # input is discovered (hubs with a `repo:` and commits past their last
    # entry), never named, so the verb stays a dictionary key.
    "devlog-status":   {"title": "Dev log — what is unlogged",
                        "hint": "projects whose commits are not in their hub yet",
                        "argv": _script("devlog.py", "--status"), "timeout": 120},
    "devlog":          {"title": "Dev log — write up recent work",
                        "hint": "commits + session logs → the project hub's dev log",
                        "argv": _script("devlog.py"),
                        "timeout": 1800, "model": True, "writes": True},
    "map-status":      {"title": "Map — what has no architecture notes",
                        "hint": "active projects with a repo and no notes folder",
                        "argv": _script("mapper.py", "--status"), "timeout": 120},
    "map":             {"title": "Map — a codebase into architecture notes",
                        "hint": "surveys the repo; 4–9 linked notes per project",
                        # Several notes per project, and the survey is large.
                        "argv": _script("mapper.py"),
                        "timeout": 2400, "model": True, "writes": True},
    # Study S7. The course-taking sibling verb is POST /api/guide/generate
    # below — a palette verb is a fixed argv and cannot name a course, the
    # same reason `new` sits in DISABLED.
    "guide-status":    {"title": "Guide — blueprint + coverage per course",
                        "hint": "which courses have a plan, and what is missing",
                        "argv": _script("guide.py", "--status"), "timeout": 60},
}

DISABLED = [
    {"verb": "new", "title": "New — scaffold a project from a description",
     # The one verb whose whole input is a sentence Zach writes. Every other
     # verb is a fixed argv, which is the property that makes this list safe to
     # POST to by name; a description has to arrive some other way. Same
     # doctrine as reflect-merge below — disabled and explained, not hidden.
     "reason": "needs a description, and a palette verb is a fixed argv — "
               "run `sigma new \"what it is\"` in the terminal"},
    {"verb": "reflect-merge", "title": "Reflect — merge a staged change",
     # Was "waiting on Phase 4's diff UI". That UI exists now (Phase 6), and it
     # is the better home: merging needs a *name*, and a palette verb is a
     # dictionary key with nothing user-supplied in it. Click a row in
     # WAITING ON YOU, read the diff, merge from there.
     "reason": "moved, not missing — review a proposal in WAITING ON YOU and "
               "merge from its diff; a palette verb cannot name one file"},
    {"verb": "install", "title": "Install hooks & schedules",
     "reason": "system configuration stays in the terminal"},
    {"verb": "ui", "title": "Start the interface",
     "reason": "you are already in it"},
]

MAX_LINES = 400


def _window_hold() -> str | None:
    """Why model verbs are unavailable right now, or None (dashboard-plan §8).

    Two reasons, both proxies because true headroom is not exposed anywhere:
    a rate limit inside the last 45 minutes means the window is spent enough
    that ad-hoc runs would burn what the fleet needs; and 08:40–09:00 is the
    reservation — ad-hoc work must not starve the scheduled run about to fire.
    Enforced at POST too, not just greyed in the listing: the listing is a
    courtesy, the refusal is the policy.
    """
    try:
        sys.path.insert(0, str(RUNTIME)) if str(RUNTIME) not in sys.path else None
        from sigma import spend
        if spend.rate_limited_within(45):
            return "window: rate-limited in the last 45 min — resumes when it rolls"
    except Exception:
        pass                       # metering failure must not disable the palette
    now = datetime.datetime.now()
    if now.hour == 8 and now.minute >= 40:
        return "reserved for the 09:00 fleet run"
    # Same reservation for the 06:00 retrospective. It is a much smaller call
    # than a fleet run, but it is the one that has to happen before the day
    # starts, and an ad-hoc verb that rate-limits the window at 05:55 costs the
    # whole review rather than delaying itself.
    if now.hour == 5 and now.minute >= 40:
        return "reserved for the 06:00 review"
    return None

# The single job slot. `_rev` bumps on every mutation so the SSE feed knows
# when to emit without diffing the whole record. `_task` is held on purpose:
# a fire-and-forget task swallows its own exceptions, and a runner that dies
# unseen leaves `_job` at "running" forever — every later POST 409s until
# the server restarts. `_proc` is held so shutdown can kill a live job.
_job: dict | None = None
_rev = 0
_task: asyncio.Task | None = None
_proc = None


def _bump():
    global _rev
    _rev += 1


def _kill_tree(proc):
    """Kill the job's whole process tree. proc.kill() alone reaches only the
    direct child — the actual work (cli.py → fleet.py → the claude CLI) kept
    running and, for fleet runs, kept the fleet.lock stranded."""
    if proc is None or proc.returncode is not None:
        return
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, timeout=15)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def kill_current_job(reason: str):
    """Called from the server's shutdown hook so a dying uvicorn does not
    orphan a live job's process tree."""
    global _job
    _kill_tree(_proc)
    if _job and _job["state"] == "running":
        _job.update(state="failed", exit=None,
                    finished=datetime.datetime.now().isoformat(timespec="seconds"))
        _job["lines"].append(reason)
        _bump()


def _finalise(task: asyncio.Task):
    """Done-callback: whatever happens to the runner, `_job` must leave
    "running" — a wedged slot is a wedged palette."""
    global _job
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):
        exc = None
    if _job and _job["state"] == "running":
        _job.update(state="failed", exit=None,
                    finished=datetime.datetime.now().isoformat(timespec="seconds"))
        _job["lines"].append(f"runner died: {exc!r}" if exc else "runner cancelled")
        _bump()


async def _run(spec: dict):
    global _job, _proc
    try:
        _proc = proc = await asyncio.create_subprocess_exec(
            *spec["argv"], cwd=str(RUNTIME),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except Exception as e:
        _job.update(state="failed", exit=None,
                    finished=datetime.datetime.now().isoformat(timespec="seconds"))
        _job["lines"].append(f"could not start: {type(e).__name__}: {e}")
        _bump()
        return

    async def read():
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                _job["lines"].append(line)
                del _job["lines"][:-MAX_LINES]
                _bump()

    try:
        await asyncio.wait_for(asyncio.gather(read(), proc.wait()),
                               timeout=spec["timeout"])
        _job.update(state="done" if proc.returncode == 0 else "failed",
                    exit=proc.returncode)
    except asyncio.TimeoutError:
        _kill_tree(proc)
        _job.update(state="failed", exit=None)
        _job["lines"].append(f"timed out after {spec['timeout']}s — process tree killed")
    _job["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    _bump()


@router.get("")
def api_commands():
    """The palette renders from this — the server's own whitelist is the single
    source of truth, so the UI cannot drift ahead of what is invokable."""
    hold = _window_hold()
    return {"verbs": [
        {"verb": v, "title": s["title"],
         "hint": hold if (hold and s.get("model")) else s["hint"],
         "writes": bool(s.get("writes")), "model": bool(s.get("model")),
         "enabled": not (hold and s.get("model"))}
        for v, s in VERBS.items()
    ] + [{**d, "hint": d["reason"], "writes": False, "model": False,
          "enabled": False} for d in DISABLED]}


@router.post("/{verb}")
async def api_run(verb: str):
    global _job
    spec = VERBS.get(verb)
    if spec is None:
        # The adversarial case the done-when names: anything off-list bounces.
        return JSONResponse({"error": f"unknown verb: {verb!r}"}, status_code=404)
    if spec.get("model"):
        hold = _window_hold()
        if hold:
            return JSONResponse({"error": "window", "reason": hold}, status_code=409)
    if _job and _job["state"] == "running":
        return JSONResponse({"error": "busy", "running": _job["verb"]},
                            status_code=409)
    global _task
    _job = {"verb": verb, "title": spec["title"], "state": "running",
            "started": datetime.datetime.now().isoformat(timespec="seconds"),
            "finished": None, "exit": None, "lines": []}
    _bump()
    _task = asyncio.get_running_loop().create_task(_run(spec))
    _task.add_done_callback(_finalise)
    return {"started": verb}


class GenerateReq(BaseModel):
    course: str


# The generation timeout is its own number: a full course is dozens of model
# calls, and the pipeline pauses/resumes cleanly if the slot kills it — but a
# kill mid-run wastes a window, so the budget errs long.
GENERATE_TIMEOUT = 5400


@guide_router.post("/generate")
async def api_guide_generate(req: GenerateReq):
    """Start the S7 generation pipeline for one course (study plan §10, §13).

    The whitelist doctrine, kept: the request's course string never reaches
    the command line — it only *selects* among the course folders discovered
    on disk, and the discovered name is what rides the argv. Everything else
    is the palette POST's own policy, inherited: the window hold refuses a
    model job, the single slot refuses a second one."""
    global _job, _task
    course = (req.course or "").strip()
    try:
        import panels
        root = Path(panels.VAULT) / "02-Areas" / "Academics"
        match = next((d.name for d in sorted(root.iterdir())
                      if d.is_dir() and d.name.lower() == course.lower()), None)
    except OSError:
        match = None
    if not course or match is None:
        return JSONResponse({"error": f"unknown course: {course!r}"},
                            status_code=404)
    hold = _window_hold()
    if hold:
        return JSONResponse({"error": "window", "reason": hold}, status_code=409)
    if _job and _job["state"] == "running":
        return JSONResponse({"error": "busy", "running": _job["verb"]},
                            status_code=409)
    spec = {"title": f"Guide — generate {match}",
            "argv": _script("guide.py", match),
            "timeout": GENERATE_TIMEOUT, "model": True, "writes": True}
    _job = {"verb": "guide-generate", "title": spec["title"], "state": "running",
            "started": datetime.datetime.now().isoformat(timespec="seconds"),
            "finished": None, "exit": None, "lines": []}
    _bump()
    _task = asyncio.get_running_loop().create_task(_run(spec))
    _task.add_done_callback(_finalise)
    return {"started": match}


@router.get("/events")
async def api_events():
    """The dock's feed for palette jobs: the current job record, re-emitted on
    every change. Same shape as the fleet progress stream on purpose."""
    async def stream():
        last, quiet = -1, 0
        while True:
            if _rev != last and _job is not None:
                last = _rev
                quiet = 0
                yield f"data: {json.dumps(_job, ensure_ascii=False)}\n\n"
            else:
                quiet += 1
                if quiet >= 50:
                    quiet = 0
                    yield ": ping\n\n"
            await asyncio.sleep(0.3)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
