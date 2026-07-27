#!/usr/bin/env python3
"""
doctor.py  —  Sigma's watchdog.

Phases 1 and 2 both shipped with a `--status` self-check, and both still failed
silently — Phase 1 lost three days of capture (2026-07-25 → 27) and Phase 2 lost
its first scheduled run to a network blip. Neither self-check was wrong. Nothing
ran them.

So this script exists to be *run automatically and to speak up only when
something is wrong*. It is wired into SessionStart, which means its output lands
in the context of every Claude Code session: the agent working in the vault is
told that its own memory is broken, and can say so. That is the surface a human
actually reads.

  doctor.py            full report (green lines included)
  doctor.py --quiet    print nothing unless something needs attention  <- the hook
  doctor.py --json     machine-readable findings

Facts come from the tools that own them (`session_logger.capture_candidates`,
`reflect.load_state`) rather than being re-derived here — a watchdog with its own
private copy of "is capture healthy?" is just a fourth thing that can disagree.

Never fails loudly: a broken watchdog must not block a session from starting, so
every check is guarded and the exit code is always 0.
"""
import os, sys, json, argparse, datetime, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# How stale each thing is allowed to get before it is worth interrupting for.
REFLECT_OVERDUE_DAYS = 8      # weekly job + a day of slack
CAPTURE_BACKLOG_WARN = 1      # the sweep clears up to sweep_max per session

ALERT, TODO, OK = "alert", "todo", "ok"
_ICON = {ALERT: "!!", TODO: "->", OK: "OK"}


def _days_since(stamp: str):
    """Whole days since an ISO date/datetime, or None if unparseable."""
    if not stamp:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    return (datetime.datetime.now() - dt).days


def check_capture(out):
    """Phase 1: is any finished session still unlogged?"""
    import session_logger as sl
    sessions = sl.DEFAULT_VAULT / "06-System" / "sessions"
    pending = len(sl.capture_candidates(sessions)[0])
    n_logs = len(list(sessions.glob("*.md"))) if sessions.exists() else 0

    # The sweep is firing detached *right now*, from the same SessionStart hook,
    # and clears up to sweep_max. So a small backlog is not a fault — it is the
    # system working. Only two things are: a backlog the sweep cannot catch up
    # on, and a sweep that is not running at all. Alerting on the healthy case
    # would put a warning on every session, which is how a watchdog gets muted.
    hours_quiet = None
    if sl.LOG_PATH.exists():
        hours_quiet = (datetime.datetime.now().timestamp()
                       - sl.LOG_PATH.stat().st_mtime) / 3600

    if pending > sl.SWEEP_MAX:
        out.append((ALERT, f"{pending} finished session(s) never logged - more than "
                           f"one sweep ({sl.SWEEP_MAX}) can clear",
                    "python session_logger.py --sweep"))
    elif pending >= CAPTURE_BACKLOG_WARN and (hours_quiet is None or hours_quiet > 24):
        out.append((ALERT, f"{pending} session(s) unlogged and the sweep has not "
                           f"run in {int(hours_quiet or 0)}h - capture has stalled",
                    "python session_logger.py --sweep; check the SessionStart hook"))
    elif pending:
        out.append((OK, f"capture running ({n_logs} logs, {pending} in flight)", None))
    else:
        out.append((OK, f"capture current ({n_logs} logs, backlog 0)", None))


def check_reflection(out):
    """Phase 2: did the weekly loop actually run, and is anything waiting on Zach?"""
    import reflect as rf
    state = rf.load_state()
    age = _days_since(state.get("last_run"))
    fresh = len(rf.new_sessions(state))

    if age is None:
        out.append((TODO, "reflection has never run", "python reflect.py"))
    elif age >= REFLECT_OVERDUE_DAYS:
        out.append((ALERT, f"reflection last ran {age} days ago "
                           f"({fresh} log(s) unreflected)", "python reflect.py"))
    else:
        out.append((OK, f"reflection ran {age}d ago ({fresh} log(s) queued)", None))

    pend = appr = 0
    if rf.PROPOSALS.exists():
        for p in rf.PROPOSALS.glob("*.md"):
            try:
                st = rf.frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            pend += st.get("status") == "pending"
            appr += st.get("status") == "approved"
    if pend:
        out.append((TODO, f"{pend} proposal(s) awaiting your review",
                    "open 06-System/proposals/ and set status: approved | rejected"))
    if appr:
        out.append((TODO, f"{appr} approved proposal(s) not yet applied",
                    "python reflect.py --apply"))


def check_schedule(out):
    """The scheduled task can fire and *fail*; 2026-07-26 did exactly that."""
    import reflect as rf
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", rf.TASK_NAME, "/fo", "LIST", "/v"],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return
    if r.returncode != 0:
        out.append((ALERT, f"scheduled task '{rf.TASK_NAME}' is not installed",
                    "python reflect.py --install-schedule"))
        return
    fields = {}
    for line in (r.stdout or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip()
    result = fields.get("last result")

    if result in (None, "0"):
        out.append((OK, "weekly reflection scheduled, last run clean", None))
        return

    # A failed run only matters if the reflection *still* has not happened. Once
    # it has been re-run by hand, keeping the alert up for the rest of the week
    # would be nagging about something already fixed — and a watchdog that cries
    # wolf is one you learn to ignore, which is how this failed the first time.
    last_run, recovered = fields.get("last run time"), False
    try:
        state_run = __import__("reflect").load_state().get("last_run")
        if last_run and state_run:
            recovered = (datetime.datetime.fromisoformat(state_run)
                         > datetime.datetime.strptime(last_run, "%Y-%m-%d %I:%M:%S %p"))
    except Exception:
        pass

    if recovered:
        out.append((OK, f"scheduled reflection failed {last_run} but has since "
                        f"been re-run by hand", None))
    else:
        out.append((ALERT, f"last scheduled reflection failed (exit {result}, "
                           f"{last_run}) - nothing has reflected since",
                    "tail reflect.log; python reflect.py"))


def check_privacy(out):
    """Is anything the ignore rules protect actually tracked — and is the guard armed?

    The pre-push hook is the real enforcement, but obsidian-git auto-pushes the
    vault every 30 minutes and may not run hooks at all. So the same invariant is
    re-checked here, where it surfaces in a session's context either way.
    """
    import session_logger as sl
    vault = sl.DEFAULT_VAULT

    r = subprocess.run(["git", "-C", str(vault), "ls-files", "-i", "-c",
                        "--exclude-standard"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return                                   # not a repo / git unavailable

    files = [f for f in r.stdout.splitlines() if f.strip()]
    if files:
        out.append((ALERT, f"{len(files)} gitignored file(s) are tracked in the vault "
                           f"- a push would publish them: {files[0]}"
                           + (f" (+{len(files) - 1} more)" if len(files) > 1 else ""),
                    "git rm --cached <path> && git commit"))
    else:
        out.append((OK, "privacy invariant holds (nothing tracked is gitignored)", None))

    if not (Path(vault) / ".git" / "hooks" / "pre-push").exists():
        out.append((TODO, "vault pre-push privacy guard is not installed",
                    "python install_hooks.py"))


CHECKS = (check_capture, check_reflection, check_schedule, check_privacy)


def collect():
    out = []
    for fn in CHECKS:
        try:
            fn(out)
        except Exception as e:      # a broken check must not hide the others
            out.append((TODO, f"{fn.__name__} could not run: "
                              f"{type(e).__name__}: {e}", None))
    return out


def main():
    ap = argparse.ArgumentParser(description="Sigma health watchdog.")
    ap.add_argument("--quiet", action="store_true",
                    help="print only when something needs attention (hook mode)")
    ap.add_argument("--json", action="store_true", help="machine-readable findings")
    a = ap.parse_args()

    findings = collect()
    if a.json:
        print(json.dumps([{"level": l, "what": w, "fix": f} for l, w, f in findings],
                         indent=2))
        return 0

    actionable = [f for f in findings if f[0] != OK]
    if a.quiet:
        if not actionable:
            return 0
        n_alert = sum(1 for l, _, _ in actionable if l == ALERT)
        print(f"Sigma health: {n_alert} alert(s), "
              f"{len(actionable) - n_alert} item(s) waiting on you.")
        for level, what, fix in actionable:
            print(f"  {_ICON[level]} {what}" + (f"   -> {fix}" if fix else ""))
        return 0

    print("sigma doctor\n" + "-" * 34)
    for level, what, fix in findings:
        print(f"  {_ICON[level]} {what}" + (f"\n       -> {fix}" if fix else ""))
    print("-" * 34)
    print("all clear" if not actionable else
          f"{len(actionable)} item(s) need attention")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:          # never break a session over a watchdog
        print(f"sigma doctor: check failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)
