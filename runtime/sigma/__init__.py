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
import os, re, json, subprocess, datetime
from pathlib import Path

__all__ = [
    "load_config", "setting_reader", "kebab", "frontmatter", "parse_model_json",
    "call_model", "make_logger", "spawn_detached", "project_hubs",
    "resolve_project", "DEFAULT_VAULT",
]

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

def call_model(prompt: str, model: str, timeout: int = 180, extra_env: dict | None = None) -> str:
    """Run `claude -p` headlessly.

    SESSION_LOGGER_ACTIVE=1 is always set: it is the recursion guard that stops
    Phase 1 from logging the OS's own model calls as if they were work sessions.
    """
    env = {**os.environ, "SESSION_LOGGER_ACTIVE": "1", **(extra_env or {})}
    r = subprocess.run(["claude", "-p", "--model", model], input=prompt,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=timeout)
    return (r.stdout or "").strip()


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
        print(f"{prefix}: {msg}", file=stream)
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
