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

# Imported for its side effect as well as its helper: sigma reconfigures stdout
# to UTF-8, without which a non-cp1252 character in any finding kills this on a
# redirected stream. Relying on one of the checks to import it first would make
# that depend on check order, and this is the script that must not fail quietly.
from sigma import read_state, write_state  # noqa: E402

# How stale each thing is allowed to get before it is worth interrupting for.
REFLECT_OVERDUE_DAYS = 8      # weekly job + a day of slack
CAPTURE_BACKLOG_WARN = 1      # the sweep clears up to sweep_max per session
AUTH_TTL_HOURS = 12           # how long a good login is taken on trust (see check_auth)

STATE_PATH = Path(__file__).resolve().parent / "doctor.state.json"

# Substrings that say "the login is gone" rather than "the network hiccuped".
# Getting this wrong in either direction is the whole difficulty: calling a blip
# an expired login sends Zach to re-authenticate for nothing, and calling an
# expired login a blip is the silent failure this check exists to end.
_AUTH_SIGNS = ("login", "log in", "authenticat", "unauthorized", "401",
               "oauth", "credential", "invalid api key", "not signed in")
_NET_SIGNS = ("enotfound", "econnrefused", "etimedout", "econnreset",
              "getaddrinfo", "network", "socket hang up", "proxy")

ALERT, TODO, INFO, OK = "alert", "todo", "info", "ok"
_ICON = {ALERT: "!!", TODO: "->", INFO: "--", OK: "OK"}
# INFO: shown even in --quiet (a standing fact worth seeing every session)
# but not "actionable" — it must not make health read as needs-attention,
# or the one colour that means act gets trained away.


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

    pend = appr = staged = 0
    if rf.PROPOSALS.exists():
        for p in rf.PROPOSALS.glob("*.md"):
            try:
                st = rf.frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            pend += st.get("status") == "pending"
            if st.get("status") == "approved":
                # Staged and approved are different waits: staged means apply has
                # already done all it can and the change is sitting in
                # 06-System/proposed/ for a human to diff and merge. Reporting it
                # as "not yet applied" would point at --apply, which would just
                # re-stage it — an alert whose suggested fix does nothing is how a
                # watchdog gets ignored.
                staged += bool(st.get("staged"))
                appr += not st.get("staged")
    if pend:
        out.append((TODO, f"{pend} proposal(s) awaiting your review",
                    "open 06-System/proposals/ and set status: approved | rejected"))
    if appr:
        out.append((TODO, f"{appr} approved proposal(s) not yet applied",
                    "python reflect.py --apply"))
    if staged:
        out.append((TODO, f"{staged} change(s) to existing notes staged for review",
                    "python reflect.py --diff   then --merge <name>"))


def _task_fields(task_name: str) -> dict:
    """`schtasks /query /v` LIST output as {lowercased field: value}; {} on any failure."""
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", task_name, "/fo", "LIST", "/v"],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return {}
    if r.returncode != 0:
        return {}
    fields = {}
    for line in (r.stdout or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip()
    return fields


def check_schedule(out):
    """The scheduled task can fire and *fail*; 2026-07-26 did exactly that."""
    import reflect as rf
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", rf.TASK_NAME],
                           capture_output=True, timeout=20)
    except Exception:
        return
    if r.returncode != 0:
        out.append((ALERT, f"scheduled task '{rf.TASK_NAME}' is not installed",
                    "python reflect.py --install-schedule"))
        return
    fields = _task_fields(rf.TASK_NAME)
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


def check_review(out):
    """The 06:00 retrospective.

    Its absence is silent by nature: the queues keep working without it, so
    nothing else in the system notices that yesterday was never scored. That is
    exactly the shape of failure this watchdog exists for.
    """
    import retro
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", retro.TASK_NAME],
                           capture_output=True, timeout=20)
    except Exception:
        return
    if r.returncode != 0:
        out.append((TODO, f"scheduled task '{retro.TASK_NAME}' is not installed",
                    "sigma review --install-schedule"))
        return

    last = retro.latest()
    if not last:
        out.append((TODO, "the daily review is scheduled but has never run",
                    "sigma review"))
        return
    # Two days without a review means the schedule fired and did nothing, or did
    # not fire — either way the productivity journal has a hole in it.
    try:
        age = (datetime.date.today()
               - datetime.date.fromisoformat(last["date"])).days
    except (ValueError, KeyError, TypeError):
        return
    if age > 2:
        out.append((ALERT, f"last review covers {last['date']} ({age}d ago) - "
                           f"nothing has scored a day since",
                    "tail review.log; sigma review"))
    else:
        out.append((OK, f"daily review current ({last['date']})", None))


def _load_state() -> dict:
    return read_state(STATE_PATH)


def _save_state(state: dict):
    write_state(STATE_PATH, state)               # state is an optimisation, not a fact


def check_auth(out):
    """Is the one credential Sigma has still live?

    Sigma runs on exactly one credential — this machine's Claude Code login — so
    every path dies without it: `claude -p` in Phases 1-2, the Agent SDK in Phase
    3, and this watchdog's own summariser. And it dies *silently*, which is the
    third costume of the recurring bug here: the unquoted hook path, the shadowed
    permission callback, and now an expired login all report "configured" while
    executing nothing.

    Two things make this awkward to check, and both shape the design:

    1. The only honest test is to actually call the model, but under a
       subscription the budget *is* the rate-limit window. Probing every
       SessionStart would spend the resource this check protects. So a good
       result is trusted for AUTH_TTL_HOURS; only failures re-probe every
       session, and a failing probe is free — it errors before a call is spent.

    2. A dead network looks like a dead login from the outside. The 2026-07-26
       reflection died on ENOTFOUND, and reporting that as "log in again" would
       be exactly the crying-wolf that gets a watchdog muted. Transport errors
       are therefore deliberately silent here: check_schedule already notices a
       run that failed for any reason.
    """
    if os.environ.get("SESSION_LOGGER_ACTIVE") == "1":
        return                                   # inside the OS's own call; not a session

    state = _load_state()
    try:
        hours = (datetime.datetime.now() - datetime.datetime.fromisoformat(
            state.get("auth_ok_at"))).total_seconds() / 3600
    except Exception:
        hours = None

    if hours is not None and hours < AUTH_TTL_HOURS:
        out.append((OK, f"login verified {int(hours)}h ago", None))
        return

    env = {**os.environ, "SESSION_LOGGER_ACTIVE": "1"}
    try:
        r = subprocess.run(["claude", "-p", "--model", "haiku"], input="ok",
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=45)
    except FileNotFoundError:
        out.append((ALERT, "the `claude` CLI is not on PATH - every Sigma phase "
                           "routes through it", "install Claude Code / fix PATH"))
        return
    except Exception:
        return                                   # timeout or spawn failure: not a verdict

    if r.returncode == 0:
        state["auth_ok_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _save_state(state)
        out.append((OK, "login live (verified just now)", None))
        return

    blob = f"{r.stderr or ''}\n{r.stdout or ''}".lower()
    if any(s in blob for s in _NET_SIGNS) and not any(s in blob for s in _AUTH_SIGNS):
        return                                   # a blip is not a login problem
    if any(s in blob for s in _AUTH_SIGNS):
        out.append((ALERT, "Claude Code login has expired - every scheduled Sigma "
                           "job will fail silently until it is renewed",
                    "claude login"))
        return

    snippet = " ".join((r.stderr or r.stdout or "unknown error").split())[:120]
    out.append((TODO, f"login check inconclusive (exit {r.returncode}): {snippet}",
                "claude -p --model haiku <<< ok"))


def check_privacy(out):
    """Is anything the ignore rules protect actually tracked — and is the guard armed?

    The pre-push hook is the real enforcement, but obsidian-git auto-pushes the
    vault every 30 minutes and may not run hooks at all. So the same invariant is
    re-checked here, where it surfaces in a session's context either way.
    """
    import session_logger as sl
    vault = sl.DEFAULT_VAULT

    # Option B (2026-07-30): the model boundary can carry explicit exemptions
    # for gitignored paths. That list is exactly the kind of second declaration
    # that drifts, so it is surfaced in every session rather than trusted.
    # Deliberately TODO, not OK: --quiet (the SessionStart mode) drops OK
    # lines, and a visibility guarantee that is silent in the automatic path
    # is not a guarantee. First, before any git call that might early-return.
    cfg_path = Path(__file__).resolve().with_name("privacy.config.json")
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            allow = [str(p) for p in cfg.get("model_allow", []) if str(p).strip()]
            if allow:
                out.append((INFO, f"model boundary: {len(allow)} gitignored "
                                  f"path(s) exempted for the model "
                                  f"({', '.join(allow)}) - they never sync",
                            None))
        except Exception:
            out.append((ALERT, "privacy.config.json exists but cannot be parsed "
                               "- model exemptions are OFF (fail closed), which "
                               "may not be what you expect",
                        "fix or delete runtime/privacy.config.json"))

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


def check_fleet(out):
    """Phase 4: is each specialist still running, and did any of them stall?

    The fleet takes Sigma from one unattended scheduled process to five, and the
    two it already had each died silently once. So the watchdog watches per
    *specialist*, not just per task: a fleet run that completes while one
    specialist has failed every time for a fortnight is exactly the kind of
    healthy-looking failure this file exists to catch.
    """
    import fleet as fl
    import specialists as sp

    state = fl.load_state()

    # The task check comes first: a fleet that has never run *and* is not
    # scheduled is "configured but never executes" — the exact failure this
    # file exists for — and the early return below must not hide it.
    if not fl._task_installed():
        out.append((TODO, f"fleet is not scheduled (task '{fl.TASK_NAME}' missing)",
                    "python fleet.py --install-schedule"))

    if not state.get("last_run"):
        out.append((TODO, "the specialist fleet has never run",
                    f"python {fl.HERE / 'fleet.py'} --all"))
        return

    specs = state.get("specialists") or {}
    stale, broken = [], []
    for s in sp.in_run_order():
        rec = specs.get(s.key) or {}
        if rec.get("last_result") not in (None, "ok"):
            broken.append(s.key)
            continue
        days = _days_since(rec.get("last_ok"))
        allowed = fl.CADENCE_DAYS.get(s.cadence, 1) * 2 + 1   # two cycles + slack
        if days is None or days > allowed:
            stale.append(f"{s.key} ({'never' if days is None else str(days) + 'd'})")

    if broken:
        out.append((ALERT, f"specialist(s) failing: {', '.join(broken)}",
                    "tail fleet.log; python fleet.py --only " + broken[0]))
    if stale:
        out.append((TODO, f"specialist(s) overdue: {', '.join(stale)}",
                    "python fleet.py"))
    if state.get("stopped_early_at"):
        out.append((TODO, f"last fleet run stopped early on a rate limit "
                          f"({state['stopped_early_at']}) - some specialists "
                          f"did not run", "python fleet.py"))

    # The fleet task can fire and *fail* before fleet.py ever writes state —
    # the same blind spot check_schedule covers for the reflection. 0 is
    # success; 267009 is "currently running"; 267011 is "has not yet run".
    fields = _task_fields(fl.TASK_NAME)
    last_result = fields.get("last result")
    if last_result and last_result not in ("0", "267009", "267011"):
        fired = fields.get("last run time")
        recovered = False
        try:
            if fired and state.get("last_run"):
                recovered = (datetime.datetime.fromisoformat(state["last_run"])
                             > datetime.datetime.strptime(fired, "%Y-%m-%d %I:%M:%S %p"))
        except Exception:
            pass
        if recovered:
            out.append((OK, f"scheduled fleet run failed {fired} but the fleet "
                            f"has run since", None))
        else:
            out.append((ALERT, f"last scheduled fleet run failed (exit {last_result}, "
                               f"{fired}) - nothing ran", "tail fleet.log; python fleet.py"))

    if not (broken or stale):
        n = len(specs)
        out.append((OK, f"fleet healthy ({n}/{len(sp.FLEET)} specialist(s) "
                        f"reporting, last run {state.get('last_run')})", None))


BACKUP_STALE_HOURS = 2        # obsidian-git pushes every 30 minutes


def check_backup(out):
    """Is the vault actually *reaching* its remote?

    Added 2026-07-31, after the vault went 19 hours without a successful push
    and nothing said so. The GitHub credential had expired, and because git
    blocks on a username prompt rather than erroring, obsidian-git's auto-push
    hung silently every 30 minutes — eighteen orphaned git processes deep — with
    a day of work sitting on one disk. Every other check was green throughout,
    because none of them asked this question.

    **The cheap signal is local.** Unpushed commits and their age need no
    network at all, and that alone would have caught this: obsidian-git pushes
    every 30 minutes, so a backlog older than a couple of hours means pushing is
    failing, whatever the reason. Only when there *is* a stale backlog does this
    spend a bounded network call to tell "you have not pushed yet" apart from
    "you cannot push".

    Non-interactive env throughout, and deliberately so: this runs on every
    SessionStart, and a doctor that inherits the very credential prompt it is
    diagnosing would hang the thing it was meant to protect.
    """
    import session_logger as sl
    from sigma.gitops import _NONINTERACTIVE
    vault = str(sl.DEFAULT_VAULT)
    env = {**os.environ, **_NONINTERACTIVE}

    def git(*args, timeout=15):
        return subprocess.run(["git", "-C", vault, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=env, stdin=subprocess.DEVNULL)

    try:
        r = git("remote")
        if r.returncode != 0:
            return                                # not a repo; not this check's business
        if not (r.stdout or "").strip():
            out.append((INFO, "vault has no git remote - nothing is backed up off "
                              "this machine, by configuration", None))
            return

        ahead = git("rev-list", "--count", "@{u}..HEAD")
        if ahead.returncode != 0:
            out.append((TODO, "vault branch has no upstream - pushes will not "
                              "happen automatically",
                        "git -C <vault> push -u origin <branch>"))
            return
        n = int((ahead.stdout or "0").strip() or 0)
        if n == 0:
            out.append((OK, "vault is pushed (nothing waiting to back up)", None))
            return

        # Oldest unpushed commit: how long has the backlog actually been stuck?
        log = git("log", "--format=%cI", "@{u}..HEAD")
        stamps = [s for s in (log.stdout or "").splitlines() if s.strip()]
        hours = None
        if stamps:
            try:
                oldest = datetime.datetime.fromisoformat(stamps[-1].strip())
                if oldest.tzinfo:
                    oldest = oldest.replace(tzinfo=None)
                hours = (datetime.datetime.now() - oldest).total_seconds() / 3600
            except ValueError:
                pass

        if hours is not None and hours < BACKUP_STALE_HOURS:
            out.append((OK, f"{n} commit(s) not yet pushed, oldest {hours:.1f}h "
                            f"- obsidian-git pushes every 30 min", None))
            return

        age = f"{hours:.0f}h" if hours is not None else "unknown age"
        probe = git("ls-remote", "origin", "HEAD", timeout=20)
        if probe.returncode == 0:
            out.append((TODO, f"{n} vault commit(s) unpushed for {age}, but the "
                              f"remote is reachable - the push is simply not happening",
                        "git -C <vault> push (check obsidian-git's settings)"))
        else:
            why = " ".join(((probe.stderr or probe.stdout) or "").split())[:160]
            out.append((ALERT, f"{n} vault commit(s) unpushed for {age} and the "
                               f"remote cannot be reached - nothing is backed up: {why}",
                        "gh auth login (or refresh the git credential), then push"))
    except subprocess.TimeoutExpired:
        out.append((ALERT, "git did not answer while checking the vault backup - "
                           "it is most likely blocked on a credential prompt",
                    "gh auth login, then push"))
    except Exception as e:
        out.append((TODO, f"backup check could not run: {type(e).__name__}: {e}", None))


CHECKS = (check_capture, check_reflection, check_schedule, check_review,
          check_auth, check_privacy, check_backup, check_fleet)


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

    actionable = [f for f in findings if f[0] not in (OK, INFO)]
    notices = [f for f in findings if f[0] == INFO]
    if a.quiet:
        if not actionable and not notices:
            return 0
        if actionable:
            n_alert = sum(1 for l, _, _ in actionable if l == ALERT)
            print(f"Sigma health: {n_alert} alert(s), "
                  f"{len(actionable) - n_alert} item(s) waiting on you.")
        for level, what, fix in actionable + notices:
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
