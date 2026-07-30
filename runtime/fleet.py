#!/usr/bin/env python3
"""
fleet.py  —  Phase 4: run Sigma's specialists, one at a time.

    fleet.py                    run everything due now
    fleet.py --only planner     run one specialist
    fleet.py --all              run every specialist regardless of cadence
    fleet.py --status           what ran, when, and what it raised
    fleet.py --dry-run          pick and print the run order, call no model
    fleet.py --install-schedule register the 9 AM daily task

Why a runner exists at all, rather than four scheduled tasks: **concurrency is
the budget.** Sigma has one credential and it is a subscription, so the ceiling
is a rate-limit window rather than an invoice (see the vault's
`subscription-only` note). Four agents fired at 9 AM share one window and starve
each other, and a starved *scheduled* agent fails when nobody is watching --
which is precisely how Phases 1 and 2 each failed once already. So the fleet is
sequenced by construction: this process runs one specialist to completion before
starting the next, and holds a lock so two invocations cannot overlap.

What it deliberately does NOT do:

- **No fan-out.** There is no concurrency option to turn on later. The one design
  constraint the vault settled for Phase 4 is that the specialists are sequenced,
  and an option is a constraint you have already decided to break.
- **No applying.** Specialists propose; `reflect.py --apply` applies, after Zach
  approves. The fleet inherits that gate by using the same `propose_change` tool
  the interface uses, so there is one approval path in the whole OS.
- **No privacy of its own.** It reuses `VaultPrivacy` through
  `interface.backend.agent.build_options`. A scheduled agent reading the vault
  unguarded would walk into the carved-out internship notes on its first
  "what am I behind on" -- the exact thing that cost a history rewrite.

Runs under the interface's venv, because that is where the Agent SDK lives.
"""
import argparse
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# The interface owns the guarded agent construction; the fleet is its second
# consumer. Importing it is the point -- a specialist that built its own options
# could quietly be missing the privacy hook.
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "interface" / "backend"))

from sigma import make_logger, read_state, write_state           # noqa: E402
import specialists as sp                                         # noqa: E402

STATE_PATH = HERE / "fleet.state.json"
LOG_PATH = HERE / "fleet.log"
LOCK_PATH = HERE / "fleet.lock"
TASK_NAME = "SigmaOS-DailyFleet"

CADENCE_DAYS = {"daily": 1, "weekly": 7}

log = make_logger(LOG_PATH, "sigma fleet")


def _quiet_proactor_shutdown():
    """Stop a successful run from printing a traceback it did not earn.

    Not our bug: on Windows, CPython collects the SDK subprocess's pipe
    transports at interpreter shutdown, after the event loop is gone. Their
    `__del__` builds a ResourceWarning message via `__repr__`, `__repr__` calls
    `fileno()` on an already-closed socket, and that raises — so Python prints
    "Exception ignored in ... I/O operation on closed pipe" *after* a clean run.

    It is cosmetic, and that is exactly why it is worth removing. A scheduled job
    that prints a traceback on every success is a job whose stderr you learn to
    skim, and unread output is how both earlier phases failed silently. Only the
    shutdown-time exceptions are swallowed; anything else `__del__` raises still
    surfaces.
    """
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport as _T
    except Exception:
        return
    original = getattr(_T, "__del__", None)
    if original is None or getattr(original, "_sigma_wrapped", False):
        return

    def _safe_del(self, *a, **kw):
        try:
            original(self, *a, **kw)
        except (ValueError, OSError, AttributeError):
            pass

    _safe_del._sigma_wrapped = True
    _T.__del__ = _safe_del


# Applied in main(), NOT at import: panels.py imports this module for facts,
# and patching the proactor inside the uvicorn server would also silence the
# Agent SDK's own transport errors on /api/ask — a silent failure installed
# into the one process whose thesis is that silent failures are the enemy.


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def load_state() -> dict:
    return read_state(STATE_PATH)


def save_state(state: dict):
    write_state(STATE_PATH, state, on_error=lambda e: log(f"could not write state: {e}"))


# The dashboard's live view of a run (dashboard-plan D1): rewritten whole at
# every transition, streamed to the browser by /api/fleet/progress. Gitignored
# machine-local state like everything else here. Written best-effort by design —
# a broken progress write must cost a stale reactor, never the run itself
# (write_state swallows its own errors).
PROGRESS_PATH = HERE / "fleet.progress.json"


def write_progress(prog: dict):
    prog["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    write_state(PROGRESS_PATH, prog)


def _hours_since(stamp) -> float | None:
    try:
        return (datetime.datetime.now()
                - datetime.datetime.fromisoformat(str(stamp))).total_seconds() / 3600
    except Exception:
        return None


def is_due(spec, state: dict, now: datetime.datetime) -> bool:
    """Has this specialist's cadence elapsed since it last *succeeded*?

    Keyed on the last success rather than the last attempt: a specialist that
    errored should be retried on the next run, not skipped for a week because
    something touched its timestamp.
    """
    rec = (state.get("specialists") or {}).get(spec.key) or {}
    hours = _hours_since(rec.get("last_ok"))
    if hours is None:
        return True
    return hours >= CADENCE_DAYS.get(spec.cadence, 1) * 24 - 1   # an hour of slack


# --------------------------------------------------------------------------
# the lock: two fleets running at once is the one thing sequencing must prevent
# --------------------------------------------------------------------------

class Lock:
    """A stale-tolerant lock file.

    A crashed run must not wedge the fleet forever, so a lock older than
    `stale_hours` is taken over rather than obeyed -- with a log line, because a
    lock that keeps going stale is a fault worth seeing.
    """

    def __init__(self, path: Path, stale_hours: float = 2.0):
        self.path, self.stale_hours, self.held = path, stale_hours, False

    def __enter__(self):
        # Exclusive create ("x") is the actual mutual exclusion. The previous
        # check-then-write left a window in which the 09:00 scheduled run and
        # a palette-launched run could both pass the check — two concurrent
        # fleets, the one thing this class exists to prevent.
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
                    return self               # genuinely held elsewhere
                log(f"taking over a stale lock ({age:.1f}h old)" if age is not None
                    else "taking over an unreadable lock")
                try:
                    self.path.unlink()        # then retry the exclusive create;
                except OSError:               # losing that race means someone
                    return self               # else took over — defer to them
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
# running one specialist
# --------------------------------------------------------------------------

SHARED_RULES = """
You are one specialist in Sigma's fleet, running unattended on a schedule. Zach
is not watching this run — he will see only what you propose.

Three rules bound everything you do:

1. **You read the vault; you do not change it.** `propose_change` is the only
   tool you have that touches disk, and it writes a *pending proposal* for Zach
   to review. Never say you made a change. You did not.
2. **Ground every claim in a note you actually read this run.** You are running
   without supervision, so an invented fact will not be caught before it lands
   in a proposal.
3. **Proposing nothing is a valid, and often correct, outcome.** You are not
   being measured on output. A proposal raised to look busy costs Zach a review
   and teaches him to stop reading your proposals — which is worse than silence.
   If there is nothing worth raising, end your turn saying so, briefly.

Some paths are deliberately private and reading them is refused. Say so and work
from what you can legitimately see; do not try to reach the same content another
way.

Finish with one or two sentences on what you found. That line is what gets
logged.
""".strip()


async def run_one(spec, timeout_s: int = 420) -> dict:
    """Run a single specialist to completion. Never raises."""
    from claude_agent_sdk import (AssistantMessage, ClaudeSDKClient, ResultMessage,
                                  TextBlock, ToolUseBlock)
    from agent import build_options

    import reflect as rf

    started = datetime.datetime.now()
    result = {"key": spec.key, "started": started.isoformat(timespec="seconds"),
              "ok": False, "proposals": 0, "attempts": 0, "denials": 0,
              "cost_usd": None, "summary": "", "error": None}

    # Ground truth for "what did this specialist actually raise". Counting
    # `propose_change` *calls* instead would count refused ones too — and a run
    # that reports two proposals when one file exists is the same defect this
    # project already fixed in --dry-run: the line you read after a silent
    # failure must not overstate what happened.
    def _seen():
        try:
            return {p.name for p in rf.PROPOSALS.glob("*.md")}
        except Exception:
            return set()

    before = _seen()

    async def _stream(text):
        yield {"type": "user", "message": {"role": "user", "content": text},
               "parent_tool_use_id": None, "session_id": spec.key}

    said = []

    async def _converse():
        opts = build_options(allow_proposals=True,
                             orientation=f"{SHARED_RULES}\n\n## Your brief\n\n{spec.brief}",
                             model=spec.model, effort=spec.effort,
                             max_turns=spec.max_turns)
        # include_partial_messages is for the browser; a headless run does not
        # need token deltas and they are pure overhead here.
        opts.include_partial_messages = False

        async with ClaudeSDKClient(options=opts) as client:
            await client.connect(_stream(f"Run your brief now. Today is "
                                         f"{datetime.date.today().isoformat()}."))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            if str(block.name).endswith("propose_change"):
                                result["attempts"] += 1
                        elif isinstance(block, TextBlock) and block.text.strip():
                            said.append(block.text.strip())
                elif isinstance(msg, ResultMessage):
                    result["cost_usd"] = msg.total_cost_usd
                    result["denials"] = len(msg.permission_denials or [])
                    result["ok"] = not msg.is_error
                    if msg.is_error:
                        # The CLI's failure text (a rate limit says so here) rides
                        # the result payload, not an exception — copy it into the
                        # error channel so _is_rate_limited can see it.
                        text = " ".join(str(getattr(msg, "result", "") or "").split())
                        result["error"] = text[:300] or f"result subtype: {msg.subtype}"

    try:
        # The timeout has to be *applied*, not just caught. Wrapping the whole
        # conversation is what makes the except below reachable: a specialist that
        # hangs would otherwise block the 9 AM run forever while holding the lock,
        # and nothing downstream would ever report it.
        await asyncio.wait_for(_converse(), timeout=timeout_s)
        result["summary"] = " ".join(" ".join(said).split())[:400]
    except (asyncio.TimeoutError, TimeoutError):
        result["error"] = f"timed out after {timeout_s}s"
        # Whatever it managed to say before hanging is the only clue about where.
        result["summary"] = " ".join(" ".join(said).split())[:400]
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["summary"] = " ".join(" ".join(said).split())[:400]

    # Let the SDK's subprocess transports finish closing before this coroutine
    # returns. Without it, Windows' proactor loop tears down with the CLI's pipes
    # still open and prints an "Exception ignored in __del__ ... I/O operation on
    # closed pipe" traceback — on a *successful* run. That matters more than it
    # looks: a scheduled job that prints a traceback every time is a job whose
    # stderr you stop reading, and unread stderr is how both earlier phases
    # failed silently. Cosmetic noise on the happy path has a real cost.
    await asyncio.sleep(0.3)

    written = sorted(_seen() - before)
    result["proposals"] = len(written)
    result["files"] = written
    # A refused call is not a failure of the run, but it is worth seeing: it
    # usually means the specialist drafted a proposal with no content.
    if result["attempts"] > result["proposals"]:
        log(f"   {spec.key}: {result['attempts']} propose_change call(s) but "
            f"{result['proposals']} written - the rest were refused")

    result["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    result["seconds"] = round((datetime.datetime.now() - started).total_seconds(), 1)
    return result


def _is_rate_limited(result: dict) -> bool:
    """Did this fail because the window is spent, rather than because it broke?

    Worth distinguishing: a rate-limited run should stop the fleet and leave the
    rest for next time, while a broken specialist should not stop the others.

    Keyed on the error channel only. `summary` is the model's own prose, and the
    vault contains a whole note about rate limits — a specialist that merely
    *mentions* them must not halt the fleet. A real limit reaches `error` either
    as an exception or via the ResultMessage error text copied in _converse.
    """
    if result.get("ok"):
        return False
    blob = (result.get("error") or "").lower()
    # The SDK can error with no result text, leaving only the bare-subtype
    # fallback, which names nothing — a rate limit would sail through and the
    # fleet would burn the remaining specialists against a spent window. In
    # that opaque case only, consult the model's own words too: an *errored*
    # run's summary is evidence, an ok run's summary is prose (the earlier
    # false-halt bug this function was fixed for).
    if blob.startswith("result subtype:"):
        blob += " " + (result.get("summary") or "").lower()
    return any(s in blob for s in ("rate limit", "rate_limit", "429",
                                   "usage limit", "quota", "overloaded"))


# --------------------------------------------------------------------------
# the sequencer
# --------------------------------------------------------------------------

async def run_fleet(keys=None, force=False, dry_run=False) -> list:
    now = datetime.datetime.now()
    state = load_state()
    state.setdefault("specialists", {})

    queue = [s for s in sp.in_run_order(keys)
             if force or keys or is_due(s, state, now)]

    if dry_run:
        print(f"would run {len(queue)} specialist(s), in this order:")
        for s in queue:
            rec = state["specialists"].get(s.key) or {}
            last = rec.get("last_ok", "never")
            print(f"  {s.order:>3}  {s.key:<9} {s.model:<7} {s.cadence:<7} last ok: {last}")
        skipped = [s.key for s in sp.in_run_order(keys) if s not in queue]
        if skipped:
            print(f"  (not due: {', '.join(skipped)})")
        return []

    results = []
    with Lock(LOCK_PATH) as lock:
        if not lock.held:
            log("another fleet run is in progress - exiting rather than "
                "running two specialists at once")
            return []

        if not queue:
            log("nothing due")
            # Still a fact the dashboard should show — but written INSIDE the
            # lock: a nothing-due invocation racing a live run must not
            # overwrite the live run's progress file mid-flight.
            write_progress({"state": "done", "note": "nothing due",
                            "run_started": now.isoformat(timespec="seconds"),
                            "queue": [], "current": None, "current_started": None,
                            "results": {}, "stopped_early": False,
                            "finished": datetime.datetime.now().isoformat(timespec="seconds")})
            return []

        prog = {"state": "running", "note": None,
                "run_started": now.isoformat(timespec="seconds"),
                "queue": [s.key for s in queue],
                "current": None, "current_started": None, "current_model": None,
                "results": {}, "stopped_early": False, "finished": None}
        write_progress(prog)

        log(f"running {len(queue)} specialist(s): {', '.join(s.key for s in queue)}")
        for spec in queue:
            log(f"-> {spec.key} ({spec.model})")
            prog["current"] = spec.key
            prog["current_model"] = spec.model
            prog["current_started"] = datetime.datetime.now().isoformat(timespec="seconds")
            write_progress(prog)

            r = await run_one(spec)
            results.append(r)

            prog["current"] = None
            prog["current_model"] = None
            prog["current_started"] = None
            prog["results"][spec.key] = {"ok": r["ok"], "seconds": r["seconds"],
                                         "proposals": r["proposals"],
                                         "error": r["error"]}
            write_progress(prog)

            rec = state["specialists"].setdefault(spec.key, {})
            rec["last_run"] = r["finished"]
            rec["last_result"] = "ok" if r["ok"] else (r["error"] or "failed")
            rec["last_proposals"] = r["proposals"]
            if r["ok"]:
                rec["last_ok"] = r["finished"]
            save_state(state)          # after each one: a crash must not lose the lot

            note = (f"{spec.key}: {'ok' if r['ok'] else 'FAILED'} "
                    f"in {r['seconds']}s, {r['proposals']} proposal(s)"
                    + (f", {r['denials']} denial(s)" if r["denials"] else "")
                    + (f" - {r['error']}" if r["error"] else ""))
            log(note)
            if r["summary"]:
                log(f"   {r['summary'][:200]}")

            if _is_rate_limited(r):
                # The window is the budget. Burning the rest of it on retries
                # would take the whole fleet down instead of one specialist.
                log("rate limit reached - stopping here; the rest stay due and "
                    "will run on the next invocation")
                state["stopped_early_at"] = r["finished"]
                save_state(state)
                break
        else:
            state.pop("stopped_early_at", None)

        state["last_run"] = datetime.datetime.now().isoformat(timespec="seconds")
        save_state(state)

        prog["state"] = "done"
        prog["stopped_early"] = bool(state.get("stopped_early_at"))
        prog["finished"] = state["last_run"]
        write_progress(prog)

    return results


# --------------------------------------------------------------------------
# status + schedule
# --------------------------------------------------------------------------

def print_status():
    state = load_state()
    specs = state.get("specialists") or {}
    print("sigma fleet\n" + "-" * 62)
    print(f"{'specialist':<11}{'cadence':<9}{'model':<8}{'last ok':<21}last result")
    for s in sp.in_run_order():
        rec = specs.get(s.key) or {}
        due = "  (due)" if is_due(s, state, datetime.datetime.now()) else ""
        print(f"{s.key:<11}{s.cadence:<9}{s.model:<8}"
              f"{str(rec.get('last_ok', 'never')):<21}"
              f"{rec.get('last_result', '-')}{due}")
    print("-" * 62)
    print(f"last fleet run : {state.get('last_run', 'never')}")
    if state.get("stopped_early_at"):
        print(f"  ! last run stopped early on a rate limit at "
              f"{state['stopped_early_at']}")
    print(f"schedule       : task '{TASK_NAME}': "
          f"{'installed' if _task_installed() else 'NOT installed'}")
    print(f"log            : {LOG_PATH}")


def _task_installed() -> bool:
    import subprocess
    try:
        return subprocess.run(["schtasks", "/query", "/tn", TASK_NAME],
                              capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def install_schedule(time_of_day="09:00"):
    """Daily at 09:00 — the cadence gate inside decides who actually runs."""
    import subprocess
    python = sys.executable
    cmd = f'"{python}" "{Path(__file__).resolve()}"'
    r = subprocess.run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", cmd,
                        "/sc", "daily", "/st", time_of_day, "/f", "/rl", "limited"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        print(f"installed '{TASK_NAME}' - daily at {time_of_day}")
        print(f"  runs: {cmd}")
        # -RestartCount is what Phase 2 learned to add after one blip cost a week.
        # StartWhenAvailable is what the reflection task always had and this one
        # missed: without it, a machine asleep at 09:00 silently skips the day.
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"$s = Get-ScheduledTask -TaskName '{TASK_NAME}'; "
                        f"$s.Settings.RestartCount = 3; "
                        f"$s.Settings.RestartInterval = 'PT10M'; "
                        f"$s.Settings.StartWhenAvailable = $true; "
                        f"Set-ScheduledTask -TaskName '{TASK_NAME}' "
                        f"-Settings $s.Settings | Out-Null"],
                       capture_output=True, text=True, timeout=30)
    else:
        print(f"could not install: {(r.stderr or r.stdout).strip()}")
    return r.returncode


def main():
    _quiet_proactor_shutdown()      # CLI/scheduled runs only — never on import
    ap = argparse.ArgumentParser(description="Run Sigma's specialist fleet, sequenced.")
    ap.add_argument("--only", action="append", metavar="KEY",
                    help=f"run just this specialist ({', '.join(sp.BY_KEY)}); repeatable")
    ap.add_argument("--all", action="store_true",
                    help="run every specialist regardless of cadence")
    ap.add_argument("--status", action="store_true", help="what ran and when")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the run order and exit; calls no model")
    ap.add_argument("--install-schedule", action="store_true")
    ap.add_argument("--at", default="09:00", help="time for --install-schedule")
    a = ap.parse_args()

    if a.status:
        print_status()
        return 0
    if a.install_schedule:
        return install_schedule(a.at)

    bad = [k for k in (a.only or []) if k not in sp.BY_KEY]
    if bad:
        print(f"unknown specialist(s): {', '.join(bad)}\n"
              f"known: {', '.join(sp.BY_KEY)}", file=sys.stderr)
        return 2

    results = asyncio.run(run_fleet(keys=a.only, force=a.all, dry_run=a.dry_run))
    if a.dry_run:
        return 0

    total = sum(r["proposals"] for r in results)
    failed = [r["key"] for r in results if not r["ok"]]
    print(f"fleet: ran {len(results)}, raised {total} proposal(s)"
          + (f", failed: {', '.join(failed)}" if failed else ""))
    if total:
        print("review them in 06-System/proposals/, then `reflect.py --apply`")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        log(f"fleet crashed: {type(e).__name__}: {e}")
        sys.exit(1)
