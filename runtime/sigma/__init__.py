#!/usr/bin/env python3
"""
sigma — the shared core of Sigma, a personal agentic OS.

Phase 1 (`session_logger.py`) and Phase 2 (`reflect.py`) each grew their own copy
of the same handful of helpers: settings precedence, kebab-casing, frontmatter
parsing, the `claude -p` call, the append-only failure log. Phase 3's Agent SDK
backend is the third consumer, so they live here instead.

Nothing in this module touches the vault except to read it. Writing is the
caller's job — see [[agent-guardrails]].
"""
import os, re, json, subprocess, datetime, sys
from pathlib import Path

__all__ = [
    "load_config", "setting_reader", "kebab", "frontmatter", "parse_model_json",
    "call_model", "make_logger", "spawn_detached", "project_hubs",
    "resolve_project", "read_state", "write_state", "write_note", "DEFAULT_VAULT",
]


def _enable_utf8_output():
    """Stop a stray arrow in model output from killing a scheduled run.

    On Windows, stdout is a cp1252 pipe whenever it is redirected — which is
    exactly the case for a scheduled task and for a detached hook worker. cp1252
    happens to contain the em-dash, so this hid for a long time; it does not
    contain `→`, `✅`, or any emoji, and every one of those routinely appears in
    text a model wrote. Printing one raises UnicodeEncodeError, which in a
    scheduled context means the job dies with its output going nowhere: the exact
    silent failure this OS has now been bitten by three times.

    Both halves matter. Reconfiguring gets real UTF-8 out where the consumer can
    take it; errors="replace" means that even if the console cannot, a character
    is mangled rather than a process lost.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass                       # Python < 3.7, or a stream that is not a TextIO


_enable_utf8_output()

DEFAULT_VAULT = Path(r"C:\Users\tusha\documents\obsidian vault")

# Project hubs live in these two places (see CLAUDE.md "Folder structure").
PROJECT_HUB_GLOBS = ("03-Projects/*.md", "05-Archive/Projects/*.md")


# --------------------------------------------------------------------------
# config: a durable JSON file next to the calling script, since hooks and
# scheduled tasks run with no environment. Precedence: env var > file > default.
# --------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def setting_reader(cfg: dict):
    """Return a `setting(env, key, default)` closure bound to one config dict."""
    def setting(env, key, default):
        v = os.environ.get(env)
        if v not in (None, ""):
            return v
        if cfg.get(key) not in (None, ""):
            return cfg[key]
        return default
    return setting


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------

def kebab(s: str, fallback: str = "note") -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(s)).strip("-").lower()
    return re.sub(r"-{2,}", "-", s)[:60] or fallback


def frontmatter(text: str) -> dict:
    """Flat YAML frontmatter → dict. Good enough for our own single-line fields;
    block lists (which Obsidian rewrites `tags: [x]` into) are simply skipped."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    fm = {}
    for line in text[3:end if end != -1 else len(text)].splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm


def parse_model_json(out: str):
    """The model was asked for bare JSON; tolerate a code fence or stray prose."""
    s = (out or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\n?|\n?```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1:
        try:
            return json.loads(s[i:j + 1])
        except json.JSONDecodeError:
            pass
    return None


# --------------------------------------------------------------------------
# model + process
# --------------------------------------------------------------------------

def call_model(prompt: str, model: str, timeout: int = 180,
               extra_env: dict | None = None, actor: str = "cli") -> str:
    """Run `claude -p` headlessly.

    SESSION_LOGGER_ACTIVE=1 is always set: it is the recursion guard that stops
    Phase 1 from logging the OS's own model calls as if they were work sessions.

    Every call is also recorded in the spend log (Phase 4's window proxies) —
    this is the chokepoint for the two `claude -p` consumers, so neither needs
    its own metering. `claude -p` reports no cost, so cost stays None; the
    rate-limited flag keys on a FAILED call whose error names the limit, never
    on the model merely mentioning rate limits in its answer (the false-halt
    bug the fleet already fixed once).
    """
    env = {**os.environ, "SESSION_LOGGER_ACTIVE": "1", **(extra_env or {})}
    r = subprocess.run(["claude", "-p", "--model", model], input=prompt,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=timeout)
    out = (r.stdout or "").strip()
    try:
        from .spend import record_spend
        blob = (out + " " + (r.stderr or "")).lower()
        record_spend(actor=actor, model=model,
                     rate_limited=r.returncode != 0 and any(
                         s in blob for s in ("rate limit", "rate_limit", "429",
                                             "usage limit", "quota", "overloaded")))
    except Exception:
        pass
    return out


def read_state(path, default: dict | None = None) -> dict:
    """A JSON state file, or `default` if it is missing or unreadable.

    Three scripts had grown their own copy of this and its writer, and they had
    drifted: two guarded the write against OSError and one did not, so a full
    disk would crash the weekly reflection *after* it had already written its
    insights — losing the run while its work sat on disk. One copy, guarded.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default or {})
    except (OSError, ValueError):
        return dict(default or {})


def write_state(path, state: dict, on_error=None) -> bool:
    """Persist a JSON state file. Returns False rather than raising.

    State is a cache of what already happened, not the record of it — the notes
    and the logs are that. Losing it costs a repeated run; raising here would
    cost the run that just succeeded.
    """
    try:
        Path(path).write_text(json.dumps(state, indent=2), encoding="utf-8")
        return True
    except (OSError, TypeError) as e:
        if on_error:
            on_error(e)
        return False


def write_note(path, text: str, default: str = "\n") -> None:
    """Write a note without silently changing its line endings.

    `Path.write_text` opens in text mode with `newline=None`, which on Windows
    translates every `\\n` to `\\r\\n`. Every script write of a vault note went
    through it, so a one-line addition to an LF note came back as a whole-file
    rewrite: the first dev log entry showed up in git as **35 insertions and 35
    deletions** on a 35-line note. Nothing was lost, but everything downstream
    that depends on a legible diff was — `reflect --diff` review, the dashboard's
    "what would this change" panel, and the ledger's promise that one click
    undoes one change you can actually see.

    So: match whatever the file already uses, and fall back to LF for a file that
    does not exist yet. This vault is genuinely mixed (103 LF, 81 CRLF), which is
    Zach's business and not something a write path should quietly settle on his
    behalf — the rule here is only that editing a note must not *change* it.
    """
    p = Path(path)
    nl = default
    try:
        raw = p.read_bytes()
    except OSError:
        raw = b""
    if raw:
        crlf = raw.count(b"\r\n")
        nl = "\r\n" if crlf > (raw.count(b"\n") - crlf) else "\n"
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if nl != "\n":
        body = body.replace("\n", nl)
    # newline="" writes exactly these bytes: the normalisation above is the only
    # thing deciding line endings, which is the point.
    p.write_text(body, encoding="utf-8", newline="")


def make_logger(log_path: Path, prefix: str, stream=None):
    """An append-only failure log. A detached worker's stdout goes to DEVNULL and
    a scheduled task has nowhere to complain, so without this a failure is silent."""
    def log(msg: str):
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            with Path(log_path).open("a", encoding="utf-8") as f:
                f.write(f"{stamp}  {msg}\n")
        except OSError:
            pass
        # The file above is the record and is always UTF-8. The console is best
        # effort: _enable_utf8_output() usually makes this moot, but a caller can
        # hand in a stream it did not configure, and losing a scheduled run to a
        # character in a log line would be an absurd way to fail.
        try:
            print(f"{prefix}: {msg}", file=stream)
        except UnicodeEncodeError:
            enc = getattr(stream or sys.stdout, "encoding", "ascii") or "ascii"
            print(f"{prefix}: {msg.encode(enc, 'replace').decode(enc, 'replace')}",
                  file=stream)
    return log


def spawn_detached(args):
    """Launch a fully detached background process (so a hook returns instantly)."""
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | NEW_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)


# --------------------------------------------------------------------------
# project resolution
# --------------------------------------------------------------------------

def _norm(p) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def project_hubs(vault: Path) -> dict:
    """{hub basename: normalised `repo:` path or None} for every project hub note.

    Only `type: project` notes count, which is what keeps the MOC (`projects.md`)
    and the design doc (`sigma-os-plan.md`) out of the map.
    """
    hubs = {}
    for pattern in PROJECT_HUB_GLOBS:
        for p in sorted(Path(vault).glob(pattern)):
            try:
                fm = frontmatter(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if fm.get("type") != "project":
                continue
            hubs[p.stem] = _norm(fm["repo"]) if fm.get("repo") else None
    return hubs


def resolve_project(cwd: str, vault: Path) -> str:
    """The `project:` field for a session log: the project hub a session belongs to.

    Naming the session's working directory is not good enough — a session run in
    `Focus Log\\backend` is still Focus Log work, and `backend` matches no hub, so
    it drops out of the per-project rollup on 🧠 System. Resolution order:

      1. a hub whose declared `repo:` path contains the cwd (longest match wins,
         so a subdirectory resolves to its parent project);
      2. any cwd path component that kebabs to an existing hub's name;
      3. the working directory's own name — still useful for a project that has
         no hub note yet, and the value a hub will later be named after.

    Only a session with no recorded cwd at all is "unknown".
    """
    if not cwd:
        return "unknown"
    hubs = project_hubs(vault)
    c = _norm(cwd)

    best = None
    for name, repo in hubs.items():
        if repo and (c == repo or c.startswith(repo + "/")):
            if best is None or len(repo) > len(hubs[best]):
                best = name
    if best:
        return best

    parts = [q for q in c.split("/") if q]
    for part in reversed(parts):
        if kebab(part) in hubs:
            return kebab(part)

    return kebab(parts[-1], "unknown") if parts else "unknown"
