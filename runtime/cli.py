#!/usr/bin/env python3
"""
cli.py  —  `sigma`, one front door for the whole OS.

    sigma                      what needs your attention right now
    sigma status               the same, in full
    sigma doctor               health check (what SessionStart runs)
    sigma fleet run            run the specialists that are due
    sigma reflect diff         review staged changes
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
HOOKS = HERE / "install_hooks.py"


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
                    help="planner | coach | auditor | tracker (repeatable)")
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
                        ("capture", "capture_cmd")):
        if a.cmd == group and not getattr(a, dest, None):
            if group == "fleet":
                return run(FLEET, "--status")
            if group == "capture":
                return run(CAPTURE, "--status")
            return run(REFLECT, "--status")

    return {
        "status": cmd_status, "doctor": cmd_doctor, "fleet": cmd_fleet,
        "reflect": cmd_reflect, "capture": cmd_capture, "install": cmd_install,
        "ui": cmd_ui,
    }[a.cmd](a)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
