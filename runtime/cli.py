#!/usr/bin/env python3
"""
cli.py  —  `sigma`, one front door for the whole OS.

    sigma                      what needs your attention right now
    sigma status               the same, in full
    sigma doctor               health check (what SessionStart runs)
    sigma fleet run            run the specialists that are due
    sigma reflect diff         review staged changes
    sigma todo                 the four priority queues
    sigma leetcode 217         log today's problem by number
    sigma leetcode --history   every problem you have solved
    sigma devlog               which projects have unlogged commits
    sigma new "<one line>"     scaffold a repo + hub note
    sigma install              hooks + scheduled tasks

Sigma grew as five scripts, each with its own flag vocabulary, and the seams
showed. `--status`, `--apply`, `--diff`, `--all` and `--only` each meant
different things depending on which script you happened to be in; `--diff` was
reflect-only and `--only` was fleet-only, with nothing to tell you that. Worse,
two of them need *different Python interpreters* — fleet needs the Agent SDK
from the interface's venv, the rest are stdlib — and picking wrong fails in a
way that looks like the script is broken rather than the invocation.

So this is a dispatcher, not a rewrite. Every subcommand shells out to the
script that already owns the job, which keeps exactly one implementation of
each behaviour. What it adds is the part that was missing: a single name, a
consistent verb-noun shape, and interpreter selection that is not the user's
problem.

Scheduled tasks should call this rather than a hardcoded interpreter path — the
runtime has already moved once (from ~/.obsidian-tools/ into this repo) and
every hardcoded path had to be found and repointed by hand.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# Also imported for the side effect: sigma reconfigures stdout to UTF-8. Without
# it even `sigma --help` mangles the em-dash in its own description, because a
# redirected stdout on Windows is cp1252. Every other entry point gets this by
# importing sigma for real; this one is a dispatcher and would not have.
sys.path.insert(0, str(HERE))
from sigma import DEFAULT_VAULT  # noqa: E402

DOCTOR = HERE / "doctor.py"
FLEET = HERE / "fleet.py"
REFLECT = HERE / "reflect.py"
CAPTURE = HERE / "session_logger.py"
INTAKE = HERE / "intake.py"
DEVLOG = HERE / "devlog.py"
MAPPER = HERE / "mapper.py"
GUIDE = HERE / "guide.py"
RECALL = HERE / "recall.py"
SCAFFOLD = HERE / "scaffold.py"
HOOKS = HERE / "install_hooks.py"
TODO = HERE / "todo.py"
LEETCODE = HERE / "leetcode.py"
# `sigma review` runs retro.py, not review.py — backend/review.py already owns
# that module name and runtime/ sits ahead of it on sys.path.
RETRO = HERE / "retro.py"


def interpreter(needs_sdk: bool = False) -> str:
    """The Python to run a subcommand with.

    The interface's venv is the superset: it has the Agent SDK *and* the stdlib
    everything else needs, so it can run all five scripts. Preferring it
    unconditionally means there is one answer rather than a per-script rule to
    get wrong. System Python is the fallback, and it is genuinely fine for
    everything except a real fleet run — fleet imports the SDK lazily, so even
    `fleet status` and `fleet run --dry-run` work without it.
    """
    venv = REPO / "interface" / "backend" / ".venv" / "Scripts" / "python.exe"
    if not venv.exists():                       # POSIX layout, for a future port
        venv = REPO / "interface" / "backend" / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    if needs_sdk:
        print("sigma: the interface venv is missing, so the Agent SDK is not "
              "available and a fleet run cannot start.\n"
              "       cd interface/backend && python -m venv .venv && "
              ".venv/Scripts/pip install -r requirements.txt", file=sys.stderr)
        raise SystemExit(2)
    return sys.executable


def run(script: Path, *args, needs_sdk: bool = False) -> int:
    cmd = [interpreter(needs_sdk), str(script), *[str(a) for a in args if a is not None]]
    # The child writes straight to this process's stdout, while our own prints
    # sit in Python's buffer — so without flushing first, a header printed here
    # appears *after* the output it is meant to introduce.
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        return subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        return 130
    except OSError as e:
        print(f"sigma: could not run {script.name}: {e}", file=sys.stderr)
        return 1


# --------------------------------------------------------------------------
# status — the one screen that answers "is anything waiting on me?"
# --------------------------------------------------------------------------

def cmd_status(a) -> int:
    """Everything, in the order you would want to hear it.

    Deliberately a summary rather than a fourth source of truth: it asks doctor
    for health and fleet for its own table, so nothing here can disagree with
    them. A status command that recomputes the facts is just another thing that
    can be wrong about them.
    """
    width = 64

    print("=" * width)
    print("  sigma")
    # Which vault, stated up front: every number below is about this folder, and
    # the path has moved once already.
    print(f"  {DEFAULT_VAULT}")
    print("=" * width)

    rc = run(DOCTOR)
    print()

    try:
        import fleet as fl
        st = fl.load_state()
        if st.get("last_run"):
            print(f"fleet          last run {st['last_run']}")
        else:
            print("fleet          never run  ->  sigma fleet run --all")
    except Exception as e:
        print(f"fleet          (unavailable: {type(e).__name__})")

    try:
        import reflect as rf
        staged = rf.staged_proposals()
        pend = appr = 0
        if rf.PROPOSALS.exists():
            for p in rf.PROPOSALS.glob("*.md"):
                fm = rf.frontmatter(p.read_text(encoding="utf-8", errors="replace"))
                pend += fm.get("status") == "pending"
                appr += fm.get("status") == "approved" and not fm.get("staged")
        bits = []
        if pend:
            bits.append(f"{pend} pending")
        if appr:
            bits.append(f"{appr} approved, not applied")
        if staged:
            bits.append(f"{len(staged)} staged for review")
        print("proposals      " + (", ".join(bits) if bits else "nothing waiting"))
        for p, fmm, _dest, _st in staged:
            print(f"                 - {p.stem}  ->  sigma reflect merge {p.stem}")
    except Exception as e:
        print(f"proposals      (unavailable: {type(e).__name__})")

    # The daily-problem nudge lives here and deliberately not in doctor.py: the
    # doctor reports whether *Sigma* is healthy, and a day without a LeetCode
    # problem is not a system failure. Putting a habit reminder in the health
    # check is how an all-clear stops meaning anything.
    try:
        import leetcode as lcm
        line = lcm.summary_line(DEFAULT_VAULT)
        if line:
            print(line)
    except Exception as e:
        print(f"leetcode       (unavailable: {type(e).__name__})")

    print("=" * width)
    return rc


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------

def cmd_doctor(a):
    return run(DOCTOR, "--json" if a.json else None, "--quiet" if a.quiet else None)


def cmd_fleet(a):
    if a.fleet_cmd == "status":
        return run(FLEET, "--status")
    if a.fleet_cmd == "run":
        args = []
        for k in (a.only or []):
            args += ["--only", k]
        if a.all:
            args.append("--all")
        if a.dry_run:
            args.append("--dry-run")
        # A dry run never reaches the SDK, so do not demand it.
        return run(FLEET, *args, needs_sdk=not a.dry_run)
    return run(FLEET, "--status")


def cmd_reflect(a):
    c = a.reflect_cmd
    if c == "diff":
        return run(REFLECT, "--diff", a.name) if a.name else run(REFLECT, "--diff")
    if c == "merge":
        return run(REFLECT, "--merge", a.name)
    if c == "apply":
        return run(REFLECT, "--apply")
    if c == "status":
        return run(REFLECT, "--status")
    args = []
    if a.all:
        args.append("--all")
    if a.since:
        args += ["--since", a.since]
    if a.dry_run:
        args.append("--dry-run")
    return run(REFLECT, *args)


def cmd_capture(a):
    if a.capture_cmd == "status":
        return run(CAPTURE, "--status")
    args = ["--sweep"]
    if a.max:
        args += ["--max", a.max]
    return run(CAPTURE, *args)


def cmd_intake(a):
    if a.intake_cmd == "status":
        return run(INTAKE, "--status")
    args = []
    if a.course:
        args += ["--course", a.course]
    if a.dry_run:
        args.append("--dry-run")
    if a.keep:
        args.append("--keep")
    if a.max:
        args += ["--max", a.max]
    if getattr(a, "cont", False):
        args.append("--continue")
    # needs_sdk: unlike the other verbs this one calls a model, so it has to run
    # on the interpreter that has the agent SDK.
    return run(INTAKE, *args, needs_sdk=True)


def cmd_guide(a):
    if a.guide_cmd == "status" or not getattr(a, "course", None):
        return run(GUIDE, "--status")
    # guide.py's model surface is `claude -p` via sigma.call_model — the
    # reflect/retro path, so the SDK interpreter is not demanded.
    redo = getattr(a, "redo", None)
    return run(GUIDE, a.course, *(["--redo", redo] if redo else []))


def cmd_recall(a):
    """Cards and the measured pace. `sweep` writes (it retires expired cards);
    bare `sigma recall` only reports — the same free-to-report rule the spending
    verbs follow, even though this one spends no window."""
    if a.recall_cmd == "sweep":
        return run(RECALL, "sweep", *(["--course", a.course] if a.course else []))
    return run(RECALL, "status")


def cmd_devlog(a):
    if a.devlog_cmd == "status":
        return run(DEVLOG, "--status", *(["--project", a.project] if a.project else []))
    args = []
    if a.project:
        args += ["--project", a.project]
    if a.dry_run:
        args.append("--dry-run")
    if a.max:
        args += ["--max", a.max]
    # Calls a model, so it needs the interpreter that has the agent SDK — except
    # for a dry run, which only reads git.
    return run(DEVLOG, *args, needs_sdk=not a.dry_run)


def cmd_map(a):
    if a.map_cmd == "status":
        return run(MAPPER, "--status", *(["--project", a.project] if a.project else []))
    args = []
    if a.project:
        args += ["--project", a.project]
    if a.dry_run:
        args.append("--dry-run")
    if a.max:
        args += ["--max", a.max]
    return run(MAPPER, *args, needs_sdk=not a.dry_run)


def cmd_new(a):
    args = [" ".join(a.description)]
    if a.dry_run:
        args.append("--dry-run")
    # Uses `claude -p` rather than the SDK, and that reads its prompt from
    # stdin — so the description never reaches a command line either.
    return run(SCAFFOLD, *args)


def cmd_todo(a):
    """The four queues, exactly as the dashboard computes them.

    Reads only, but it does persist the sidecar index — which is how a task's
    age and its completion date get recorded at all. `--no-persist` makes it a
    pure read for when you only want to look.
    """
    args = []
    if a.all:
        args.append("--all")
    if a.json:
        args.append("--json")
    if a.date:
        args += ["--date", a.date]
    if a.no_persist:
        args.append("--no-persist")
    return run(TODO, *args)


def cmd_leetcode(a):
    """Log the day's problem, or report the streak when given no number.

    Bare `sigma leetcode` reports and `sigma leetcode 217` writes — the same
    read-is-free/verb-writes split intake, devlog and map already use.
    """
    args = []
    if a.number:
        args.append(a.number)
        if a.title:
            args.append(a.title)
    if a.difficulty:
        args += ["--difficulty", a.difficulty]
    if a.topics:
        args += ["--topics", a.topics]
    if a.date:
        args += ["--date", a.date]
    if a.again:
        args.append("--again")
    if a.offline:
        args.append("--offline")
    if a.recent:
        args += ["--recent", a.recent]
    # `--history` takes an optional N, so "given with no value" (None) still has
    # to be forwarded — testing truthiness here would silently drop bare
    # `--history` and quietly show the status screen instead of the record.
    if a.history is not False:
        args.append("--history")
        if a.history is not None:
            args.append(a.history)
    if a.find:
        args += ["--find", a.find]
    if a.since:
        args += ["--since", a.since]
    return run(LEETCODE, *args)


def cmd_review(a):
    """Score yesterday and write it down. Calls a model for the narrative only —
    the number is arithmetic, so --dry-run needs no window at all."""
    if a.status:
        return run(RETRO, "--status")
    if a.install_schedule:
        return run(RETRO, "--install-schedule", "--at", a.at)
    args = []
    if a.date:
        args += ["--date", a.date]
    if a.dry_run:
        args.append("--dry-run")
    return run(RETRO, *args, needs_sdk=False)


def cmd_install(a):
    what = a.what or "all"
    rc = 0
    if what in ("all", "hooks"):
        print("-- git hooks")
        rc |= run(HOOKS)
    if what in ("all", "schedules"):
        print("-- scheduled tasks")
        rc |= run(REFLECT, "--install-schedule")
        rc |= run(FLEET, "--install-schedule")
        rc |= run(RETRO, "--install-schedule")
    return rc


def cmd_ui(a):
    """Start the Phase 3 interface: one process serving API and built UI."""
    py = interpreter(needs_sdk=True)
    backend = REPO / "interface" / "backend"
    dist = REPO / "interface" / "frontend" / "dist"
    if not dist.is_dir():
        print("sigma: the frontend is not built yet — the API will run but the "
              "page will not.\n       cd interface/frontend && npm install && "
              "npm run build", file=sys.stderr)
    url = f"http://127.0.0.1:{a.port}"
    print(f"sigma: interface on {url}   (ctrl-c to stop)")
    try:
        return subprocess.run(
            [py, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(a.port)], cwd=str(backend)).returncode
    except KeyboardInterrupt:
        return 0


def shared_flags(parent, verbs, specs):
    """Give a group's parent parser and each of its verbs the same flags.

    **The verbs get `argparse.SUPPRESS` as their default, never a concrete one,
    and that is the entire point of this helper.** argparse parses a subcommand
    into a *fresh* namespace and then copies every key of it onto the parent's,
    so a concrete default on a verb silently overwrites a flag the user typed
    *before* the verb. `sigma intake --dry-run run` set `dry_run=True` while
    parsing `intake`, and then `run`'s own `store_true` default clobbered it
    back to `False` — performing a real intake, spending model calls and
    clearing the drop folder, while printing nothing to say it had ignored the
    flag. The same trap sat on `sigma devlog --dry-run run` and `sigma map
    --dry-run run`, where the verb writes notes.

    SUPPRESS keeps the key out of the subnamespace entirely, so the parent's
    value survives unless the user really did type the flag after the verb.
    Both orders now mean the same thing, which is what the flag being on both
    parsers was always meant to promise.
    """
    for name, kw in specs:
        parent.add_argument(name, **kw)
        for v in verbs:
            v.add_argument(name, **{**kw, "default": argparse.SUPPRESS})


def build_parser():
    ap = argparse.ArgumentParser(
        prog="sigma", description="Sigma — a personal agentic OS.",
        epilog="Run `sigma` with no arguments for what needs your attention.")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="everything that needs your attention")

    d = sub.add_parser("doctor", help="health check (what SessionStart runs)")
    d.add_argument("--json", action="store_true")
    d.add_argument("--quiet", action="store_true", help="print only problems")

    f = sub.add_parser("fleet", help="the Phase 4 specialists")
    fs = f.add_subparsers(dest="fleet_cmd")
    fs.add_parser("status", help="what ran, when, and what it raised")
    fr = fs.add_parser("run", help="run the specialists that are due")
    fr.add_argument("--only", action="append", metavar="KEY",
                    help="coach | auditor | tracker (repeatable)")
    fr.add_argument("--all", action="store_true", help="ignore cadence")
    fr.add_argument("--dry-run", action="store_true", help="print the order, call no model")

    r = sub.add_parser("reflect", help="the Phase 2 learning loop")
    rs = r.add_subparsers(dest="reflect_cmd")
    rs.add_parser("status", help="self-check")
    rr = rs.add_parser("run", help="distil insights and proposals from recent sessions")
    rr.add_argument("--all", action="store_true")
    rr.add_argument("--since", metavar="YYYY-MM-DD")
    rr.add_argument("--dry-run", action="store_true")
    rs.add_parser("apply", help="execute approved proposals")
    rd = rs.add_parser("diff", help="review changes staged against existing notes")
    rd.add_argument("name", nargs="?")
    rm = rs.add_parser("merge", help="apply one staged change to its target")
    rm.add_argument("name")

    c = sub.add_parser("capture", help="Phase 1 session logging")
    cs = c.add_subparsers(dest="capture_cmd")
    cs.add_parser("status", help="self-check + backlog")
    csw = cs.add_parser("sweep", help="log any session the hook missed")
    csw.add_argument("--max", metavar="N")

    n = sub.add_parser("intake", help="turn dropped course material into notes")
    ns = n.add_subparsers(dest="intake_cmd")
    ns.add_parser("status", help="what is waiting in the drop folder")
    nr = ns.add_parser("run", help="read the drop folder and write the notes")
    nc = ns.add_parser("continue",
                       help="finish sources that ran past one pass's budget")
    # every intake verb takes the same flags, in either order — see shared_flags
    shared_flags(n, (nr, nc), [
        ("--course", {"default": "", "help": "only this course code"}),
        ("--dry-run", {"action": "store_true",
                       "help": "name the files; call no model"}),
        ("--keep", {"action": "store_true",
                    "help": "leave sources in the drop folder"}),
        ("--max", {"metavar": "N"}),
    ])
    nc.set_defaults(cont=True)

    g = sub.add_parser("devlog", help="write recent commits into a project hub's dev log")
    gs = g.add_subparsers(dest="devlog_cmd")
    gss = gs.add_parser("status", help="which projects have unlogged work")
    gss.add_argument("--project", default="", help="only this hub note's name")
    gr = gs.add_parser("run", help="write up every project with unlogged work")
    # `sigma devlog` and `sigma devlog run` take the same flags, in either order
    shared_flags(g, (gr,), [
        ("--project", {"default": "", "help": "only this hub note's name"}),
        ("--dry-run", {"action": "store_true",
                       "help": "name the commits; call no model"}),
        ("--max", {"metavar": "N"}),
    ])

    gd = sub.add_parser("guide", help="the S7 study-guide generation pipeline")
    gds = gd.add_subparsers(dest="guide_cmd")
    gds.add_parser("status", help="blueprint + coverage per course")
    gdr = gds.add_parser("run", help="blueprint pass, or author what the "
                                     "approved blueprint still misses")
    gdr.add_argument("course", help="course code, e.g. AA-210")
    gdr.add_argument("--redo", metavar="ROWS",
                     help="re-author notes that ALREADY exist, e.g. 'M02,CP1' "
                          "— for when the prompt improved after they were "
                          "written. Overwrites each in one revertible commit, "
                          "refuses to create anything, and leaves the chain "
                          "alone, so it does not need the blueprint approved.")

    rc_ = sub.add_parser("recall", help="the S8 recall cards and the measured pace")
    rcs = rc_.add_subparsers(dest="recall_cmd")
    rcs.add_parser("status", help="open cards and the pace multiplier, per course")
    rcw = rcs.add_parser("sweep", help="retire cards that have sat open too long")
    rcw.add_argument("--course", default="", help="limit to one course")

    mp = sub.add_parser("map", help="turn a project's codebase into architecture notes")
    ms = mp.add_subparsers(dest="map_cmd")
    mss = ms.add_parser("status", help="which projects have no architecture notes")
    mss.add_argument("--project", default="", help="only this hub note's name")
    mr = ms.add_parser("run", help="survey the code and write the notes")
    shared_flags(mp, (mr,), [
        ("--project", {"default": "", "help": "only this hub note's name"}),
        ("--dry-run", {"action": "store_true",
                       "help": "build the survey and report its size; "
                               "call no model"}),
        ("--max", {"metavar": "N"}),
    ])

    nw = sub.add_parser("new", help="scaffold a project from a one-line description")
    nw.add_argument("description", nargs="+", help="what the project is, in one line")
    nw.add_argument("--dry-run", action="store_true",
                    help="plan it and print what would be made; create nothing")

    t = sub.add_parser("todo", help="the four priority queues")
    t.add_argument("--all", action="store_true",
                   help="the full queues, not just the visible windows")
    t.add_argument("--json", action="store_true", help="the raw payload")
    t.add_argument("--date", metavar="YYYY-MM-DD",
                   help="score as of this date instead of today")
    t.add_argument("--no-persist", action="store_true",
                   help="do not update the index (a pure read)")

    lc = sub.add_parser("leetcode", help="log the day's problem by number")
    lc.add_argument("number", nargs="?", help="the problem number you solved")
    lc.add_argument("title", nargs="?", default="",
                    help="only needed if the lookup cannot reach LeetCode")
    lc.add_argument("--difficulty", choices=["easy", "medium", "hard"], default="")
    lc.add_argument("--topics", default="", help="comma-separated, overrides the lookup")
    lc.add_argument("--date", metavar="YYYY-MM-DD", help="log it against another day")
    lc.add_argument("--again", action="store_true",
                    help="log a problem you have already solved, as a revisit")
    lc.add_argument("--offline", action="store_true", help="never touch the network")
    lc.add_argument("--recent", metavar="N", help="with no number: list the last N solves")
    # `default=False` is the "absent" sentinel here, because None already means
    # "given with no cap" — see cmd_leetcode, which forwards the difference.
    lc.add_argument("--history", nargs="?", const=None, default=False, metavar="N",
                    help="browse solved problems, newest first (N caps it)")
    lc.add_argument("--find", default="", metavar="TEXT",
                    help="with no number: match a number, title or topic")
    lc.add_argument("--since", default="", metavar="YYYY-MM-DD",
                    help="with no number: only solves on or after this day")

    rv = sub.add_parser("review", help="score yesterday and write it down")
    rv.add_argument("--date", metavar="YYYY-MM-DD", help="review this day instead")
    rv.add_argument("--dry-run", action="store_true",
                    help="print the note; call no model and write nothing")
    rv.add_argument("--status", action="store_true")
    rv.add_argument("--install-schedule", action="store_true")
    rv.add_argument("--at", default="06:00", help="time for --install-schedule")

    i = sub.add_parser("install", help="git hooks and scheduled tasks")
    i.add_argument("what", nargs="?", choices=["all", "hooks", "schedules"])

    u = sub.add_parser("ui", help="start the Phase 3 interface")
    u.add_argument("--port", type=int, default=8787)

    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)

    # Bare `sigma` is the question you actually have most often.
    if not a.cmd:
        return cmd_status(a)

    # A group with no verb should show that group's help, not silently do
    # something plausible — guessing is how a CLI teaches you the wrong model.
    for group, dest in (("fleet", "fleet_cmd"), ("reflect", "reflect_cmd"),
                        ("capture", "capture_cmd"), ("intake", "intake_cmd"),
                        ("devlog", "devlog_cmd"), ("map", "map_cmd"),
                        ("guide", "guide_cmd")):
        if a.cmd == group and not getattr(a, dest, None):
            if group == "fleet":
                return run(FLEET, "--status")
            if group == "capture":
                return run(CAPTURE, "--status")
            # Bare `sigma intake` reports; `sigma intake run` spends the window.
            if group == "intake":
                return run(INTAKE, "--status")
            # Same for devlog, map and guide: reporting is free, the verb spends.
            if group == "guide":
                return run(GUIDE, "--status")
            if group in ("devlog", "map"):
                return run(DEVLOG if group == "devlog" else MAPPER, "--status",
                           *(["--project", a.project] if a.project else []))
            return run(REFLECT, "--status")

    return {
        "status": cmd_status, "doctor": cmd_doctor, "fleet": cmd_fleet,
        "reflect": cmd_reflect, "capture": cmd_capture, "intake": cmd_intake,
        "devlog": cmd_devlog, "map": cmd_map, "guide": cmd_guide, "new": cmd_new,
        "recall": cmd_recall,
        "todo": cmd_todo, "review": cmd_review, "leetcode": cmd_leetcode,
        "install": cmd_install, "ui": cmd_ui,
    }[a.cmd](a)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
