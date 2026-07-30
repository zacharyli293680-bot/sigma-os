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
import sys
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

RUNTIME = Path(__file__).resolve().parents[2] / "runtime"

router = APIRouter(prefix="/api/commands")


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
                        "hint": "the six watchdog checks, on demand",
                        "argv": _script("doctor.py"), "timeout": 180},
    "fleet-status":    {"title": "Fleet — status",
                        "hint": "who ran, when, what they raised",
                        "argv": _script("fleet.py", "--status"), "timeout": 60},
    "fleet-run":       {"title": "Fleet — run everything due",
                        "hint": "sequenced; the reactor shows it live",
                        "argv": _script("fleet.py"), "timeout": 1800, "model": True},
    "fleet-run-planner": {"title": "Fleet — run the planner now",
                          "hint": "today's plan as a daily-note proposal",
                          "argv": _script("fleet.py", "--only", "planner"),
                          "timeout": 600, "model": True},
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
}

DISABLED = [
    {"verb": "reflect-merge", "title": "Reflect — merge a staged change",
     "reason": "the one operation that overwrites a note — Phase 4 brings the diff UI"},
    {"verb": "install", "title": "Install hooks & schedules",
     "reason": "system configuration stays in the terminal"},
    {"verb": "ui", "title": "Start the interface",
     "reason": "you are already in it"},
]

MAX_LINES = 400

# The single job slot. `_rev` bumps on every mutation so the SSE feed knows
# when to emit without diffing the whole record.
_job: dict | None = None
_rev = 0


def _bump():
    global _rev
    _rev += 1


async def _run(verb: str, spec: dict):
    global _job
    try:
        proc = await asyncio.create_subprocess_exec(
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
        proc.kill()
        _job.update(state="failed", exit=None)
        _job["lines"].append(f"timed out after {spec['timeout']}s — killed")
    _job["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    _bump()


@router.get("")
def api_commands():
    """The palette renders from this — the server's own whitelist is the single
    source of truth, so the UI cannot drift ahead of what is invokable."""
    return {"verbs": [
        {"verb": v, "title": s["title"], "hint": s["hint"],
         "writes": bool(s.get("writes")), "model": bool(s.get("model")),
         "enabled": True}
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
    if _job and _job["state"] == "running":
        return JSONResponse({"error": "busy", "running": _job["verb"]},
                            status_code=409)
    _job = {"verb": verb, "title": spec["title"], "state": "running",
            "started": datetime.datetime.now().isoformat(timespec="seconds"),
            "finished": None, "exit": None, "lines": []}
    _bump()
    asyncio.get_running_loop().create_task(_run(verb, spec))
    return {"started": verb}


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
