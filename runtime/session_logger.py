#!/usr/bin/env python3
"""
session_logger.py  —  Phase 1 (Part B) of Sigma, the agentic OS.

Distills a finished Claude Code session into a Markdown "session log" note in the
vault's 06-System/sessions/ folder. Intended to be run by a SessionEnd hook
(Part C), which passes hook context as JSON on stdin. Also runnable by hand for
testing via --transcript / --session-id / --cwd.

Reads the transcript conservatively (the JSONL schema is internal to Claude Code
and shifts between versions), condenses it, and asks a cheap model (Haiku, via
`claude -p`) for a summary. Frontmatter facts (tools, files, project) are computed
deterministically from the transcript; only the prose + task + outcome come from
the model.

Docs: see [[sigma-os-plan]] in the vault.
"""
import os, sys, json, re, subprocess, argparse, datetime, tempfile
from pathlib import Path

from sigma import (DEFAULT_VAULT as _VAULT_FALLBACK, call_model, kebab,
                   load_config, make_logger, parse_model_json, project_hubs,
                   resolve_project, setting_reader, spawn_detached)

# --- recursion guard: our own `claude -p` call would otherwise re-trigger the hook
if os.environ.get("SESSION_LOGGER_ACTIVE") == "1":
    sys.exit(0)

# --- config: a durable JSON file next to this script, since the SessionEnd hook
#     runs with no environment set (env vars alone would never reach it).
#     Precedence for every setting: env var > config file > built-in default.
CONFIG_PATH = Path(os.environ.get(
    "SESSION_LOGGER_CONFIG",
    str(Path(__file__).with_name("session_logger.config.json"))))
_CFG = load_config(CONFIG_PATH)
_setting = setting_reader(_CFG)

DEFAULT_VAULT = Path(_setting("OBSIDIAN_VAULT_PATH", "vault_path",
                              str(_VAULT_FALLBACK)))
MODEL = _setting("SESSION_LOGGER_MODEL", "model", "haiku")
MIN_MESSAGES = int(_setting("SESSION_LOGGER_MIN_MESSAGES", "min_messages", 4))
EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
# denylist: cwd substrings whose sessions are NEVER summarized (privacy control).
_env_deny = [s for s in os.environ.get("SESSION_LOGGER_DENY", "").split(",") if s]
DENY = _env_deny or [s for s in _CFG.get("deny", []) if s]


LOG_PATH = Path(__file__).with_name("session_logger.log")
SWEEP_MAX = int(_setting("SESSION_LOGGER_SWEEP_MAX", "sweep_max", 3))
# a transcript touched more recently than this is assumed to be a live session
MIN_AGE_MIN = int(_setting("SESSION_LOGGER_MIN_AGE_MIN", "min_age_minutes", 15))


log = make_logger(LOG_PATH, "session-logger", stream=sys.stderr)


def load_hook_input():
    """SessionEnd hook sends JSON on stdin; fall back to CLI args for testing."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript"); ap.add_argument("--session-id")
    ap.add_argument("--cwd"); ap.add_argument("--vault")
    ap.add_argument("--out-dir"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--worker", action="store_true")     # internal: the detached worker
    ap.add_argument("--payload")                          # internal: temp JSON path
    ap.add_argument("--no-detach", action="store_true")  # run synchronously (testing)
    ap.add_argument("--status", action="store_true")     # print a health/self-check report
    # sweep: log any transcript the SessionEnd hook missed (it only fires on a
    # clean exit, so closing the terminal loses the session otherwise)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--max", type=int)                   # cap sessions per sweep
    ap.add_argument("--detach", action="store_true")     # re-spawn detached, return now
    a = ap.parse_args()
    data, from_stdin = {}, False
    if not a.transcript and not a.worker and not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            try: data = json.loads(raw); from_stdin = True
            except json.JSONDecodeError: pass
    hook = {
        "transcript_path": a.transcript or data.get("transcript_path"),
        "session_id": a.session_id or data.get("session_id", ""),
        "cwd": a.cwd or data.get("cwd", ""),
    }
    return hook, a, from_stdin


def parse_transcript(path: Path):
    """Lenient extraction: condensed dialogue, tools used, files edited, title."""
    convo, tools, files, first_user, title, last_ts = [], [], [], None, None, None
    n_msg = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try: o = json.loads(line)
        except json.JSONDecodeError: continue
        t = o.get("type")
        if t == "ai-title" and o.get("aiTitle"):
            title = o["aiTitle"]
        if o.get("timestamp"): last_ts = o["timestamp"]
        if t not in ("user", "assistant"): continue
        m = o.get("message") or {}
        content = m.get("content")
        if content is None: continue
        n_msg += 1
        if isinstance(content, str):                         # user text
            txt = content.strip()
            if txt:
                if first_user is None and not txt.startswith("<"):
                    first_user = txt[:600]
                convo.append(("USER", txt[:1500]))
            continue
        if isinstance(content, list):                        # assistant blocks / tool results
            texts, toolbits = [], []
            for b in content:
                if not isinstance(b, dict): continue
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    texts.append(b["text"].strip())
                elif bt == "tool_use":
                    name = b.get("name", "?"); tools.append(name)
                    inp = b.get("input") or {}
                    fp = inp.get("file_path")
                    if name in EDIT_TOOLS and fp: files.append(fp)
                    hint = fp or inp.get("command") or inp.get("pattern") or ""
                    toolbits.append(f"{name}({str(hint)[:80]})")
            role = "ASSISTANT" if m.get("role") == "assistant" else "USER"
            joined = " ".join(texts)[:1500]
            if joined: convo.append((role, joined))
            if toolbits: convo.append(("TOOLS", ", ".join(toolbits[:12])))
    return {
        "convo": convo, "tools": sorted(set(tools)), "files": files,
        "first_user": first_user, "title": title, "last_ts": last_ts, "n_msg": n_msg,
    }


def condense(convo, cap=20000):
    lines = [f"{r}: {x}" for r, x in convo]
    blob = "\n".join(lines)
    if len(blob) <= cap: return blob
    head, tail = blob[:6000], blob[-14000:]
    return head + "\n\n…[middle elided]…\n\n" + tail


def rel_files(files, vault: Path):
    """Vault notes as vault-relative paths; everything else by basename (a session
    log records what changed, not where another repo lives on disk)."""
    out = []
    for f in files:
        p = Path(f)
        try: out.append(p.relative_to(vault).as_posix())
        except ValueError: out.append(p.name)
    seen, uniq = set(), []
    for f in out:
        if f not in seen: seen.add(f); uniq.append(f)
    return uniq[:40]


def already_logged(sessions_dir: Path, session_id: str) -> bool:
    if not session_id or not sessions_dir.exists(): return False
    for md in sessions_dir.glob("*.md"):
        head = md.read_text(encoding="utf-8", errors="replace")[:500]
        if f"session_id: {session_id}" in head: return True
    return False


def logged_session_ids(sessions_dir: Path) -> set:
    """Every session_id already present in the vault's logs (one pass, for --sweep)."""
    out = set()
    if not sessions_dir.exists(): return out
    for md in sessions_dir.glob("*.md"):
        head = md.read_text(encoding="utf-8", errors="replace")[:500]
        m = re.search(r"^session_id:\s*(\S+)", head, re.M)
        if m: out.add(m.group(1))
    return out


def transcript_meta(path: Path):
    """(session_id, cwd, entrypoint) read from the transcript itself — a sweep has
    no hook payload to take them from."""
    sid = cwd = entry = ""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try: o = json.loads(line)
            except json.JSONDecodeError: continue
            sid = sid or o.get("sessionId") or ""
            cwd = cwd or o.get("cwd") or ""
            entry = entry or o.get("entrypoint") or ""
            if sid and cwd and entry: break
    except OSError:
        pass
    return sid, cwd, entry


def is_machine_session(entrypoint: str) -> bool:
    """True for the OS's own headless model calls (`claude -p` → entrypoint
    'sdk-cli'), which are machinery, not work worth remembering.

    The SessionEnd path excludes these via the SESSION_LOGGER_ACTIVE env guard, but
    a sweep reads transcripts off disk where that env var is long gone — so the
    same exclusion has to be re-derived from the transcript itself. Matching on
    'sdk' rather than the exact value also covers future SDK entrypoints (Phase 3's
    Agent SDK backend will show up here too)."""
    return "sdk" in (entrypoint or "").lower()


PROJECTS_ROOT = Path(os.path.expanduser("~/.claude/projects"))


def capture_candidates(sessions_dir: Path):
    """(transcripts awaiting capture, newest first), {what was skipped and why}.

    The single place that answers "is this session still unlogged?". The sweep
    logs these, --status counts them, and doctor.py alerts on them — when that
    filter existed in three copies, the three could disagree about whether
    capture was healthy, which is the failure this whole path guards against.
    """
    if not PROJECTS_ROOT.exists():
        return [], {"trivial": 0, "machine": 0}
    done = logged_session_ids(sessions_dir)
    cutoff = datetime.datetime.now().timestamp() - MIN_AGE_MIN * 60
    out, trivial, machine = [], 0, 0
    for tp in sorted(PROJECTS_ROOT.glob("*/*.jsonl"),
                     key=lambda p: p.stat().st_mtime, reverse=True):
        if tp.stat().st_mtime > cutoff:          # still live, or only just ended
            continue
        sid, cwd, entry = transcript_meta(tp)
        if not sid or sid in done:
            continue
        if is_machine_session(entry):            # our own claude -p calls
            machine += 1
            continue
        if any(d and d in cwd for d in DENY):    # privacy denylist
            continue
        # Cheap triviality gate: a session with >= MIN_MESSAGES turns is never this
        # small, so only tiny transcripts get parsed. Without it, one-message
        # transcripts would be re-examined on every sweep forever.
        if tp.stat().st_size < 20_000 and parse_transcript(tp)["n_msg"] < MIN_MESSAGES:
            trivial += 1
            continue
        out.append((tp, sid, cwd))
    return out, {"trivial": trivial, "machine": machine}


def sweep(a):
    """Log every transcript that has no session log yet.

    SessionEnd only fires on a clean exit (/exit, /clear, logout) — closing the
    terminal window loses the session. Wiring this to SessionStart makes capture
    self-healing: whatever the hook missed is picked up on the next launch.
    """
    vault = Path(a.vault) if a.vault else DEFAULT_VAULT
    sessions_dir = Path(a.out_dir) if a.out_dir else (vault / "06-System" / "sessions")
    if not PROJECTS_ROOT.exists():
        log("sweep: no ~/.claude/projects directory; nothing to do."); return 0

    cap = a.max or SWEEP_MAX
    candidates, skip = capture_candidates(sessions_dir)
    trivial, machine = skip["trivial"], skip["machine"]

    skipped = f"{trivial} trivial, {machine} machine-session(s) ignored"
    if not candidates:
        log(f"sweep: nothing unlogged ({skipped}).")
        return 0
    # A dry run must not leave "logged 3" in the failure log — that file is the
    # only evidence available after a silent failure, so it may never overstate
    # what happened. Say what a real run *would* do, and say that it was a rehearsal.
    verb = "would log" if a.dry_run else "logging"
    log(f"sweep: {len(candidates)} unlogged transcript(s); {verb} up to {cap} "
        f"(newest first; {skipped})" + (" [dry-run]" if a.dry_run else ""))
    n = 0
    for tp, sid, cwd in candidates[:cap]:
        try:
            do_log({"transcript_path": str(tp), "session_id": sid, "cwd": cwd}, a)
            n += 1
        except Exception as e:                   # one bad transcript must not stop the rest
            log(f"sweep: {tp.name} failed: {type(e).__name__}: {e}")
    left = len(candidates) - n
    log(f"sweep: {'would have logged' if a.dry_run else 'logged'} {n}" +
        (f"; {left} still queued for the next sweep" if left > 0 else "") +
        (" [dry-run — nothing written]" if a.dry_run else ""))
    return 0


PROMPT = """You are the session-logger for Sigma, a personal agentic OS. Below is a condensed transcript of a finished Claude Code session. Write a concise, factual log entry.

Return ONLY a JSON object (no prose, no code fence) with exactly these keys:
- "task": one plain-text line naming what the session set out to do (<= 90 chars).
- "outcome": exactly one of "success", "partial", or "abandoned".
- "body": Markdown with these five H2 sections, terse and specific, no filler:
    ## What was asked
    ## What Claude did
    ## Key decisions
    ## Reusable next time
    ## Touched
  In "Touched", list the notes/files changed; for vault notes use [[wikilinks]] by basename (drop the folder and .md). Keep the whole body under ~250 words.

Files this session edited (ground truth — use for the Touched section): {files}

CONDENSED TRANSCRIPT:
{convo}
"""


def yaml_list(items):
    return "[" + ", ".join(items) + "]" if items else "[]"


def build_note(fields, body):
    fm = (
        "---\n"
        "type: session-log\n"
        f"date: {fields['date']}\n"
        f"session_id: {fields['session_id']}\n"
        f"project: {fields['project']}\n"
        f"task: \"{fields['task']}\"\n"
        f"tools: {yaml_list(fields['tools'])}\n"
        f"files_touched: {yaml_list(fields['files'])}\n"
        f"outcome: {fields['outcome']}\n"
        "tags: [session-log]\n"
        "---\n\n"
    )
    heading = fields.get("title") or fields["task"]
    return fm + f"# {fields['date']} — {heading}\n\n" + body.strip() + "\n"


def status_report():
    """Human-readable self-check: config, vault, hook wiring, log count, deps."""
    ok = lambda b: "OK " if b else "!! "
    print("session-logger - status\n" + "-" * 34)
    print(f"config file : {CONFIG_PATH}")
    print(f"  {ok(CONFIG_PATH.exists())}exists={CONFIG_PATH.exists()}  model={MODEL}  "
          f"min_messages={MIN_MESSAGES}")
    print(f"  deny (privacy): {DENY or '(none - every session is logged)'}")
    sess = DEFAULT_VAULT / "06-System" / "sessions"
    n = len(list(sess.glob('*.md'))) if sess.exists() else 0
    print(f"vault       : {DEFAULT_VAULT}")
    print(f"  {ok(sess.exists())}sessions/ exists={sess.exists()}  logs={n}")
    # claude CLI reachable?
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True,
                           timeout=20)
        print(f"  {ok(r.returncode == 0)}claude CLI: {(r.stdout or '').strip() or 'not found'}")
    except Exception:
        print("  !! claude CLI: NOT reachable on PATH")
    # project hubs the resolver can map a session's cwd onto
    hubs = project_hubs(DEFAULT_VAULT)
    n_repo = sum(1 for r in hubs.values() if r)
    print(f"  {ok(bool(hubs))}project hubs: {len(hubs)} ({n_repo} with a repo: path) "
          f"-> {', '.join(sorted(hubs)) or '(none)'}")
    # SessionEnd hook wired?
    settings = Path(os.path.expanduser("~/.claude/settings.json"))
    wired = swept = False
    try:
        s = json.loads(settings.read_text(encoding="utf-8"))
        hooks = s.get("hooks", {})
        wired = any("session_logger" in json.dumps(h) for h in hooks.get("SessionEnd", []))
        swept = any("--sweep" in json.dumps(h) for h in hooks.get("SessionStart", []))
    except (OSError, json.JSONDecodeError):
        pass
    print(f"hook        : {settings}")
    print(f"  {ok(wired)}SessionEnd  -> session_logger: {'wired' if wired else 'NOT wired'}"
          f"   (fires only on a clean exit)")
    print(f"  {ok(swept)}SessionStart-> --sweep: {'wired' if swept else 'NOT wired'}"
          f"   (catches sessions the above missed)")
    # how far behind is capture? (same filter the sweep uses — see capture_candidates)
    if PROJECTS_ROOT.exists():
        pending = len(capture_candidates(sess)[0])
        print(f"backlog     : {ok(pending == 0)}{pending} session(s) awaiting capture "
              f"(sweep_max={SWEEP_MAX}/run, min_age={MIN_AGE_MIN}min)")
    print(f"failure log : {LOG_PATH}  "
          f"({'present' if LOG_PATH.exists() else 'nothing logged yet'})")
    return 0


def main():
    hook, a, from_stdin = load_hook_input()

    if a.status:
        return status_report()

    # sweep mode: catch whatever SessionEnd missed. --detach returns instantly so
    # a SessionStart hook never delays the session coming up.
    if a.sweep:
        if a.detach and not (a.dry_run or a.stdout or a.no_detach):
            spawn_detached([sys.executable, os.path.abspath(__file__), "--sweep"]
                           + (["--max", str(a.max)] if a.max else []))
            return 0
        return sweep(a)

    # worker mode: the detached child reads the hook payload from a temp file
    if a.worker:
        if not a.payload or not os.path.exists(a.payload):
            return 0
        try:
            hook = json.loads(Path(a.payload).read_text(encoding="utf-8"))
        finally:
            try: os.remove(a.payload)
            except OSError: pass
        return do_log(hook, a)

    # hook mode: detach so the model call never blocks the session's exit
    if from_stdin and hook.get("transcript_path") and not (
            a.dry_run or a.stdout or a.out_dir or a.no_detach):
        tf = tempfile.NamedTemporaryFile("w", delete=False, suffix=".json",
                                         encoding="utf-8")
        json.dump(hook, tf); tf.close()
        spawn_detached([sys.executable, os.path.abspath(__file__),
                        "--worker", "--payload", tf.name])
        return 0

    return do_log(hook, a)


def do_log(hook, a):
    tp = hook["transcript_path"]
    if not tp or not Path(tp).exists():
        log("no transcript path; nothing to do.")
        return 0
    cwd = hook["cwd"] or ""
    if any(d and d in cwd for d in DENY):
        log(f"cwd on denylist, skipping: {cwd}")
        return 0

    vault = Path(a.vault) if a.vault else DEFAULT_VAULT
    sessions_dir = Path(a.out_dir) if a.out_dir else (vault / "06-System" / "sessions")

    data = parse_transcript(Path(tp))
    if data["n_msg"] < MIN_MESSAGES:
        log(f"trivial session ({data['n_msg']} msgs), skipping.")
        return 0
    if already_logged(sessions_dir, hook["session_id"]):
        log("already logged this session, skipping.")
        return 0

    date = datetime.date.today().isoformat()
    hhmm = datetime.datetime.now().strftime("%H%M")
    if data["last_ts"]:
        try:
            dt = datetime.datetime.fromisoformat(data["last_ts"].replace("Z", "+00:00"))
            date, hhmm = dt.date().isoformat(), dt.strftime("%H%M")
        except (ValueError, AttributeError):
            pass

    files = rel_files(data["files"], vault)
    project = resolve_project(cwd, vault)
    prompt = PROMPT.format(files="\n".join(files) or "(none)",
                           convo=condense(data["convo"]))

    if a.dry_run:
        print(f"[dry-run] project={project} tools={data['tools']} files={len(files)} "
              f"msgs={data['n_msg']} title={data['title']!r}")
        print(f"[dry-run] would write: {sessions_dir}/{date}-{hhmm}-<slug>.md")
        print("\n----- PROMPT -----\n" + prompt[:1600] + "\n…")
        return 0

    model_out = call_model(prompt, MODEL, timeout=180, actor="capture")
    parsed = parse_model_json(model_out)
    if parsed:
        task = str(parsed.get("task", data["title"] or "session"))[:90]
        outcome = parsed.get("outcome", "success")
        body = parsed.get("body", model_out)
    else:                                   # model didn't return JSON — degrade gracefully
        log("model did not return JSON; falling back to raw output")
        task = (data["title"] or (data["first_user"] or "session")[:90])
        outcome, body = "success", model_out or "*(summary unavailable)*"
    if outcome not in ("success", "partial", "abandoned"): outcome = "success"

    fields = {"date": date, "session_id": hook["session_id"] or "unknown",
              "project": project, "task": task.replace('"', "'"),
              "tools": data["tools"], "files": files, "outcome": outcome,
              "title": data["title"]}
    note = build_note(fields, body)

    if a.stdout:
        print(note); return 0

    sessions_dir.mkdir(parents=True, exist_ok=True)
    out = sessions_dir / f"{date}-{hhmm}-{kebab(task, 'session')}.md"
    out.write_text(note, encoding="utf-8")
    log(f"wrote {out.name}  (project={project}, outcome={outcome})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                  # never let a logging error break the session
        log(f"error: {type(e).__name__}: {e}")
        sys.exit(0)
