#!/usr/bin/env python3
"""
reflect.py  —  Phase 2 of Sigma, the agentic OS.

The weekly reflection + propose-and-approve loop. Reads the L1 session logs
written since the last reflection (06-System/sessions/), asks a reasoning model
(Sonnet, via `claude -p`) for durable lessons, and writes them into the vault as:

  06-System/insights/    L4 — one atomic note per lesson
  06-System/proposals/   pending changes to the system, status: pending

The agent NEVER changes the system itself. A proposal is applied only after a
human flips its frontmatter to `status: approved` and `reflect.py --apply` runs.
Applying is additive-only: it creates files that do not exist and appends to
CLAUDE.md; it never overwrites or deletes.

Companion to session_logger.py (Phase 1), which produces this script's input.
Docs: see [[sigma-os-plan]] and [[reflection-loop]] in the vault.
"""
import os, sys, json, re, time, subprocess, argparse, datetime
from pathlib import Path

from sigma import (DEFAULT_VAULT as _VAULT_FALLBACK, call_model, frontmatter,
                   kebab, load_config, make_logger, parse_model_json,
                   read_state, setting_reader, write_state)

# --- config: a durable JSON file next to this script, so a scheduled run (which
#     carries no environment) sees the same settings as an interactive one.
#     Precedence for every setting: env var > config file > built-in default.
CONFIG_PATH = Path(os.environ.get(
    "REFLECT_CONFIG", str(Path(__file__).with_name("reflect.config.json"))))
_CFG = load_config(CONFIG_PATH)
_setting = setting_reader(_CFG)

VAULT = Path(_setting("OBSIDIAN_VAULT_PATH", "vault_path", str(_VAULT_FALLBACK)))
MODEL = _setting("REFLECT_MODEL", "model", "sonnet")
MAX_INSIGHTS = int(_setting("REFLECT_MAX_INSIGHTS", "max_insights", 4))
MAX_PROPOSALS = int(_setting("REFLECT_MAX_PROPOSALS", "max_proposals", 3))
MIN_SESSIONS = int(_setting("REFLECT_MIN_SESSIONS", "min_sessions", 2))
STATE_PATH = Path(_setting("REFLECT_STATE", "state_path",
                           str(Path(__file__).with_name("reflect.state.json"))))
LOG_PATH = Path(__file__).with_name("reflect.log")
TASK_NAME = "SigmaOS-WeeklyReflection"

SESSIONS = VAULT / "06-System" / "sessions"
INSIGHTS = VAULT / "06-System" / "insights"
PROPOSALS = VAULT / "06-System" / "proposals"
# Where a change to an EXISTING file waits. `--apply` never overwrites, so a
# proposal that corrects a note used to skip forever — which meant the two
# specialists whose whole job is correcting existing notes could never land
# anything. It now writes the intended file here, mirroring the target's path,
# for `--diff` and then `--merge`. See stage_change().
STAGED = VAULT / "06-System" / "proposed"
CONTRACT = VAULT / "CLAUDE.md"
CONTENT_MARKER = "<!-- proposal:content -->"
CONTRACT_SECTION = "## Learned conventions"

# Skills install into one of two roots. A vault-scoped skill loads only for sessions
# run in the vault; a user-scoped one loads in *every* repo. Which one a skill needs
# depends on where the work it describes happens — a procedure about another codebase
# is inert in the vault, which is exactly how the first two applied skills failed.
USER_CLAUDE = Path(os.path.expanduser("~/.claude"))
SKILL_ROOTS = {"vault": VAULT / ".claude" / "skills", "user": USER_CLAUDE / "skills"}
SKILLS = SKILL_ROOTS["vault"]

KINDS = ("skill", "contract", "note", "routine")
SCOPES = ("vault", "user")
DEFAULT_SCOPE = "user"


log = make_logger(LOG_PATH, "reflect")


def load_state():
    return read_state(STATE_PATH, {"last_run": None, "covered": []})


def save_state(state):
    # Guarded, unlike the copy this replaced: by the time state is written the
    # insights and proposals are already on disk, so raising here would report a
    # failed run whose actual work succeeded.
    write_state(STATE_PATH, state, on_error=lambda e: log(f"could not write state: {e}"))


# A weekly job gets one shot a week, so a blip costs seven days of learning.
# 2026-07-26 lost a run to `Unable to connect to API (ENOTFOUND)` — the network,
# not the model. These are worth another attempt; a malformed answer is not.
TRANSIENT = ("unable to connect", "enotfound", "econnreset", "etimedout",
             "socket hang up", "network", "overloaded", "rate limit",
             "502", "503", "504", "429")
RETRY_WAITS = (60, 300)          # seconds; total added delay <= 6 min


def looks_transient(out: str) -> bool:
    return any(t in (out or "").lower() for t in TRANSIENT)


def call_with_retry(prompt: str):
    """(parsed, raw). Retries only a transient *transport* failure.

    A model that answers with prose instead of JSON will answer with prose again,
    so retrying that just burns tokens — but a run lost to a dropped connection is
    recoverable, and the alternative is waiting until next Sunday.
    """
    out = ""
    for i, wait in enumerate((*RETRY_WAITS, None)):
        out = call_model(prompt, MODEL, timeout=900,
                         extra_env={"REFLECT_ACTIVE": "1"}, actor="reflect")
        parsed = parse_model_json(out)
        if parsed:
            if i:
                log(f"recovered on attempt {i + 1}")
            return parsed, out
        if wait is None or not looks_transient(out):
            return None, out
        log(f"transient failure (attempt {i + 1}): {out[:120].replace(chr(10), ' ')!r}"
            f" - retrying in {wait}s")
        time.sleep(wait)
    return None, out


def new_sessions(state, use_all=False, since=None):
    """Session logs not yet reflected on (or all / since a date, when asked)."""
    if not SESSIONS.exists():
        return []
    logs = sorted(SESSIONS.glob("*.md"))
    if use_all:
        return logs
    covered = set(state.get("covered", []))
    out = []
    for p in logs:
        if p.name in covered:
            continue
        if since and frontmatter(p.read_text(encoding="utf-8", errors="replace")
                                 ).get("date", "") < since:
            continue
        out.append(p)
    return out


def existing_skills():
    """Every skill the OS already has, across BOTH roots.

    This has to scan the user root too, not just the vault's: a skill installed
    user-globally is invisible here otherwise, and the reflection agent would
    cheerfully propose it again every week.
    """
    out = []
    for scope, root in SKILL_ROOTS.items():
        if not root.exists():
            continue
        for sk in sorted(root.glob("*/SKILL.md")):
            fm = frontmatter(sk.read_text(encoding="utf-8", errors="replace"))
            out.append(f"{sk.parent.name} [{scope}] — {fm.get('description', '')[:120]}")
    return out


def existing_insights():
    if not INSIGHTS.exists():
        return []
    return [p.stem for p in sorted(INSIGHTS.glob("*.md"))]


def git_log(since: str | None) -> str:
    cmd = ["git", "-C", str(VAULT), "log", "--oneline", "-30"]
    if since:
        cmd.insert(4, f"--since={since}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def cap(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "\n…[elided]…"


PROMPT = """You are the weekly reflection agent for Sigma, a personal agentic OS whose memory is an Obsidian vault. You read what the system did recently and distill what it should LEARN from it.

You never change the system yourself. You write insights (durable lessons) and proposals (concrete changes) that a human reviews and approves.

Return ONLY a JSON object (no prose, no code fence), with exactly these keys:
{{"insights": [...at most {max_insights}...], "proposals": [...at most {max_proposals}...]}}

An insight object:
- "title": short noun phrase naming the lesson, <= 70 chars (becomes a note title)
- "lesson": ONE sentence stating the lesson as a claim
- "evidence": 2-4 sentences citing what in the logs supports it, naming specific session logs, tools, files or counts
- "action": what should change because of it (may be "nothing yet - watch")
- "confidence": "low" | "medium" | "high"
- "sessions": list of session-log filenames that support it, basename WITHOUT .md

A proposal object:
- "title": imperative and short, <= 70 chars
- "kind": "skill" | "contract" | "note" | "routine"
- "target": vault-relative path it creates or changes. skill -> .claude/skills/<name>/SKILL.md ; contract -> CLAUDE.md ; note -> e.g. 04-Resources/<name>.md ; routine -> a one-line description instead of a path
- "scope": for kind "skill" ONLY, where it installs. "user" = ~/.claude/skills/, loads in EVERY repo - use this whenever the procedure is about writing code, another codebase, or a tool outside the vault. "vault" = <vault>/.claude/skills/, loads ONLY for sessions run in the Obsidian vault - use this only for a procedure that operates on the vault itself. A skill about another repo installed with scope "vault" can never fire. Omit for other kinds.
- "rationale": 2-3 sentences: the problem it solves, tied to the evidence
- "risk": "low" | "medium" | "high"
- "content": the EXACT text to write, complete and self-contained. For kind "skill" it MUST begin with YAML frontmatter containing `name:` and `description:` then the procedure in Markdown. For kind "contract" give ONLY the Markdown bullet lines to APPEND (never a rewrite). For kind "routine" leave it empty.
- "insight": the title of the insight it follows from, or ""

Rules:
- Few, high-value items beat many. An empty list is a valid answer for either key.
- DURABLE only: a lesson that will still be true in a month. Not a summary of what happened, not praise, not filler.
- Additive only: never propose deleting, rewriting or reorganising existing content.
- A "skill" must be a genuinely repeatable procedure that recurred in the logs - not a one-off task.
- A "contract" addition must match CLAUDE.md's existing structure and voice.
- Cite specifics. If the evidence is thin, lower the confidence rather than inflating the claim.

=== THE VAULT CONTRACT (CLAUDE.md, trimmed) ===
{contract}

=== SKILLS THAT ALREADY EXIST ===
{skills}

=== INSIGHTS ALREADY RECORDED (do not repeat these) ===
{insights}

=== RECENT GIT HISTORY (vault) ===
{git}

=== SESSION LOGS SINCE THE LAST REFLECTION ({n} logs) ===
{logs}
"""


def build_prompt(logs, since):
    blob = "\n\n".join(
        f"--- {p.name} ---\n" + cap(p.read_text(encoding="utf-8", errors="replace"), 3000)
        for p in logs)
    return PROMPT.format(
        max_insights=MAX_INSIGHTS, max_proposals=MAX_PROPOSALS,
        contract=cap(CONTRACT.read_text(encoding="utf-8", errors="replace")
                     if CONTRACT.exists() else "(missing)", 7000),
        skills="\n".join(existing_skills()) or "(none yet)",
        insights="\n".join(existing_insights()) or "(none yet)",
        git=git_log(since) or "(unavailable)",
        n=len(logs), logs=cap(blob, 60000))


def unique_path(d: Path, stem: str) -> Path:
    """Never overwrite: bump a suffix instead."""
    p = d / f"{stem}.md"
    n = 2
    while p.exists():
        p = d / f"{stem}-{n}.md"
        n += 1
    return p


def write_insight(ins, window, today) -> Path:
    sessions = [s for s in ins.get("sessions", []) if isinstance(s, str)][:8]
    conf = ins.get("confidence", "medium")
    conf = conf if conf in ("low", "medium", "high") else "medium"
    fm = ("---\n"
          "type: insight\n"
          f"date: {today}\n"
          f"covers_from: {window[0]}\n"
          f"covers_to: {window[1]}\n"
          f"sessions: {len(sessions)}\n"
          f"confidence: {conf}\n"
          "status: active\n"
          "tags: [insight]\n"
          "---\n\n")
    ev_links = " · ".join(f"[[{s}]]" for s in sessions) or "*(no session cited)*"
    body = (f"# {ins.get('title', 'Insight')}\n\n"
            f"> {ins.get('lesson', '').strip()}\n\n"
            "## Evidence\n"
            f"{ins.get('evidence', '').strip()}\n\n"
            f"**Sessions:** {ev_links}\n\n"
            "## What to do about it\n"
            f"{ins.get('action', '').strip() or 'Nothing yet — watch.'}\n\n"
            "## Related\n"
            "- [[system|🧠 System]] · [[sigma-os]] · [[reflection-loop]]\n")
    p = unique_path(INSIGHTS, kebab(ins.get("title", "insight"), "insight"))
    p.write_text(fm + body, encoding="utf-8")
    return p


def write_proposal(pr, today) -> Path:
    kind = pr.get("kind", "note")
    kind = kind if kind in KINDS else "note"
    risk = pr.get("risk", "medium")
    risk = risk if risk in ("low", "medium", "high") else "medium"
    target = str(pr.get("target", "")).replace("\\", "/").replace('"', "'")
    content = pr.get("content", "") or ""
    lang = "yaml" if kind == "skill" else "markdown"
    insight = str(pr.get("insight", "")).replace('"', "'")
    # a routine is a human instruction, not a path: keep it out of the target field,
    # which must stay a short, quotable, YAML-safe string.
    action = ""
    if kind == "routine":
        action, target = target, "(manual)"
    scope = pr.get("scope", DEFAULT_SCOPE)
    scope = scope if scope in SCOPES else DEFAULT_SCOPE
    fm = ("---\n"
          "type: proposal\n"
          f"date: {today}\n"
          "status: pending\n"
          f"kind: {kind}\n"
          f"target: \"{target[:120]}\"\n"
          + (f"scope: {scope}\n" if kind == "skill" else "")
          + f"risk: {risk}\n"
          f"source_insight: \"{insight}\"\n"
          "applied:\n"
          "tags: [proposal]\n"
          "---\n\n")
    approve = ("## How to approve\n"
               "1. Read **The change** above and edit it here if you want it different — "
               "what is in this note is exactly what gets written.\n"
               "2. Set `status: approved` (or `rejected`) in the frontmatter.\n"
               + ("3. Do it by hand — no script applies this kind — then set "
                  "`status: applied` and fill in `applied:`.\n" if kind == "routine" else
                  # Derived, not hardcoded: this script has moved once already
                  # (~/.obsidian-tools → the sigma-os repo), and a proposal that
                  # tells you to run a path that no longer exists is worse than
                  # one that tells you nothing.
                  f"3. Run `python {os.path.abspath(__file__)} --apply`, "
                  "or `/reflect apply` in Claude Code.\n"))
    change = (f"*Not auto-appliable — this one is done by hand.*\n\n{action.strip()}\n\n"
              if kind == "routine" else
              f"{CONTENT_MARKER}\n```{lang}\n{content.strip()}\n```\n\n")
    where = (f" · installs **{scope}-wide**"
             f"{' (loads in every repo)' if scope == 'user' else ' (loads only in the vault)'}"
             if kind == "skill" else "")
    body = (f"# {pr.get('title', 'Proposal')}\n\n"
            f"> **{kind}** → `{target}`{where} · risk **{risk}** · status **pending**\n\n"
            "## Why\n"
            f"{pr.get('rationale', '').strip()}\n\n"
            "## The change\n"
            + change
            + approve +
            "\n## Related\n"
            "- [[system|🧠 System]] · [[reflection-loop]]"
            + (f" · from [[{kebab(pr['insight'])}]]" if pr.get("insight") else "") + "\n")
    p = unique_path(PROPOSALS, f"{today}-{kebab(pr.get('title', 'proposal'), 'proposal')}")
    p.write_text(fm + body, encoding="utf-8")
    return p


def heal_capture_first():
    """Recover unlogged sessions before reflecting on the week.

    The reflection's conclusions are only as complete as its input, and on
    2026-07-25 three days of sessions were missing without anyone noticing —
    a reflection run in that window would have drawn confident lessons from a
    hole in the evidence. Rather than merely flagging the gap, close it: the
    sweep is the same code the SessionStart hook runs, so this is a retry, not
    a new mechanism. A gap that survives the sweep is reported and does not
    block — a partial reflection beats skipping the week entirely.
    """
    try:
        import session_logger as sl
    except Exception as e:
        log(f"capture pre-check unavailable ({type(e).__name__}: {e}); reflecting anyway.")
        return
    sessions = VAULT / "06-System" / "sessions"
    try:
        pending = len(sl.capture_candidates(sessions)[0])
        if not pending:
            return
        log(f"capture pre-check: {pending} unlogged session(s) - sweeping before reflecting")
        sl.sweep(argparse.Namespace(vault=str(VAULT), out_dir=None,
                                    max=pending, dry_run=False))
        left = len(sl.capture_candidates(sessions)[0])
        log(f"capture pre-check: {'all recovered' if not left else f'{left} still unlogged - reflecting over an incomplete window'}")
    except Exception as e:
        log(f"capture pre-check failed ({type(e).__name__}: {e}); reflecting anyway.")


def do_reflect(a):
    state = load_state()
    heal_capture_first()
    logs = new_sessions(state, a.all, a.since)
    if len(logs) < MIN_SESSIONS and not a.force:
        log(f"only {len(logs)} new session log(s) (min {MIN_SESSIONS}) - nothing to reflect on.")
        return 0

    dates = sorted(frontmatter(p.read_text(encoding="utf-8", errors="replace")).get("date", "")
                   for p in logs)
    window = (dates[0] or "?", dates[-1] or "?")
    prompt = build_prompt(logs, window[0] if window[0] != "?" else None)

    if a.dry_run:
        print(f"[dry-run] {len(logs)} new session log(s), window {window[0]} -> {window[1]}")
        print(f"[dry-run] would write to {INSIGHTS} and {PROPOSALS}")
        print(f"[dry-run] prompt is {len(prompt)} chars, model={MODEL}\n")
        print("----- PROMPT (head) -----\n" + prompt[:2000] + "\n…")
        return 0

    log(f"reflecting over {len(logs)} session log(s), {window[0]} -> {window[1]}, model={MODEL}")
    parsed, out = call_with_retry(prompt)
    if not parsed:
        log("model did not return JSON - no notes written. Raw head: " + out[:300].replace("\n", " "))
        return 1

    today = datetime.date.today().isoformat()
    if a.stdout:
        print(json.dumps(parsed, indent=2)[:8000])
        return 0

    INSIGHTS.mkdir(parents=True, exist_ok=True)
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    written = []
    for ins in (parsed.get("insights") or [])[:MAX_INSIGHTS]:
        if isinstance(ins, dict) and ins.get("title"):
            written.append(write_insight(ins, window, today))
    props = []
    for pr in (parsed.get("proposals") or [])[:MAX_PROPOSALS]:
        if isinstance(pr, dict) and pr.get("title"):
            props.append(write_proposal(pr, today))

    state["last_run"] = datetime.datetime.now().isoformat(timespec="seconds")
    state["covered"] = sorted(set(state.get("covered", [])) | {p.name for p in logs})
    save_state(state)

    log(f"wrote {len(written)} insight(s), {len(props)} proposal(s)")
    for p in written + props:
        print(f"  + {p.relative_to(VAULT).as_posix()}")
    if props:
        print("\nReview them in Obsidian, set `status: approved`, then run --apply.")
    return 0


# --------------------------------------------------------------------------
# apply: execute approved proposals. Additive only, never overwrites.
# --------------------------------------------------------------------------

def _ledger_commit(rel: str, message: str, action: str, title: str, proposal: str):
    """Phase 4: --apply is a ledger consumer. Each change it lands becomes its
    own path-scoped commit with its SHA in the activity ledger, revertible from
    the dashboard like every autonomous change. The write itself happened just
    above; the interleaved-backup-commit case is gitops's absorbed handling."""
    try:
        from sigma import gitops, ledger
        with gitops.vault_write(VAULT) as w:
            res = w.commit(rel, message)
        ledger.record("reflect", action, rel, res["sha"], title,
                      extra={"proposal": proposal})
        return res["sha"]
    except Exception as e:
        log(f"ledger commit failed for {rel}: {e}")
        return None

def proposal_content(text: str) -> str:
    """The exact payload to write: the fenced block after the marker.

    Greedy to the LAST fence close before "## How to approve", not lazy to the
    first: proposed notes legitimately contain fences of their own (the first
    real daily-note proposal embedded a ```dataview block), and the lazy match
    silently truncated the payload at the nested fence — an auto-applied note
    would have been gutted mid-section. Bounded at the approve heading so the
    stamps appended below a proposal never leak into the payload."""
    i = text.find(CONTENT_MARKER)
    if i == -1:
        return ""
    seg = text[i:]
    j = seg.find("## How to approve")
    if j != -1:
        seg = seg[:j]
    m = re.search(r"```[a-z]*\n(.*)\n```", seg, re.S)
    return m.group(1) if m else ""


def safe_target(target: str, scope: str = "vault") -> Path | None:
    """Resolve a proposal's target, refusing anything that escapes its scope root.

    vault → anywhere inside the vault.
    user  → only inside ~/.claude/skills/. Widening the blast radius from "the
            vault" to "the home directory" would be the wrong trade for letting a
            skill load everywhere, so a user-scoped target must still name a path
            under .claude/skills/ and resolve inside it.
    """
    if not target or target.startswith(("/", "\\")) or ":" in target:
        return None
    rel = target.replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    if scope == "user":
        if not rel.startswith(".claude/skills/"):
            return None
        root, p = SKILL_ROOTS["user"], (USER_CLAUDE.parent / rel).resolve()
    else:
        root, p = VAULT, (VAULT / target).resolve()
    try:
        p.relative_to(Path(root).resolve())
    except ValueError:
        return None
    return p


def append_to_contract(content: str, title: str, today: str) -> str:
    """Append one learned convention to CLAUDE.md's "Learned conventions" section,
    creating the section at the end of the file the first time.

    The block goes at the *end of that section*, not the end of the file — once
    anything else is appended below it, those are not the same place.
    """
    text = CONTRACT.read_text(encoding="utf-8")
    block = f"\n<!-- from proposal: {title} ({today}) -->\n{content.strip()}\n"
    i = text.find(CONTRACT_SECTION)
    if i == -1:
        text = text.rstrip() + (
            f"\n\n---\n\n{CONTRACT_SECTION}\n\n"
            "> Added by the reflection loop from approved proposals in "
            "`06-System/proposals/`. Each block names the proposal it came from.\n"
            + block)
    else:
        # the section ends at the next H2 (or the end of the file)
        after = i + len(CONTRACT_SECTION)
        m = re.search(r"^## ", text[after:], re.M)
        end = after + m.start() if m else len(text)
        rest = text[end:]
        text = text[:end].rstrip() + "\n" + block + ("\n" + rest.lstrip("\n") if rest.strip() else "")
    CONTRACT.write_text(text, encoding="utf-8")
    return "CLAUDE.md"


def staged_path(target: str, scope: str = "vault") -> Path:
    """Where the proposed version of an existing file waits for review.

    Mirrors the target's path under `06-System/proposed/` rather than sitting
    beside it as `timeline.proposed.md`. A sibling would carry the target's own
    frontmatter — `type: resource`, `type: assignment` — and so would show up in
    the Dataview tables and the graph as a second, phantom copy of the note it
    is proposing to replace. Under `06-System/` it is out of every area query's
    `FROM` clause, which is the difference between a staging area and a mess.
    """
    # NOT lstrip("./") — that strips *characters*, so a target like
    # `.claude/skills/x/SKILL.md` loses its leading dot and stages under
    # `claude/...`, quietly mislabelling where the change belongs.
    rel = target.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    return STAGED / ("_user" / Path(rel) if scope == "user" else Path(rel))


def stage_change(target: str, scope: str, content: str) -> Path:
    dest = staged_path(target, scope)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content.rstrip() + "\n", encoding="utf-8")
    return dest


def mark_staged(path: Path, today: str, target: str, staged_rel: str):
    """Record that a proposal is staged, WITHOUT calling it applied.

    Status stays `approved`, because nothing has changed at the target yet. The
    distinction matters for the watchdog: "waiting for you to merge" is an alert
    that clears when you act, where the old "approved but skipped" could never
    clear at all.
    """
    text = path.read_text(encoding="utf-8")
    if re.search(r"^staged:", text, re.M):
        text = re.sub(r"^staged:.*$", f"staged: {staged_rel}", text, count=1, flags=re.M)
    else:
        text = re.sub(r"^(applied:.*)$", f"staged: {staged_rel}\n\\1", text,
                      count=1, flags=re.M)
    if "**Staged " not in text:
        text = text.rstrip() + (
            f"\n\n---\n**Staged {today}** — `{target}` already exists, so the proposed "
            f"version was written to `{staged_rel}` instead of overwriting it.\n\n"
            f"Review it:\n\n"
            f"```\npython reflect.py --diff {path.stem}\n```\n\n"
            f"Then either merge it (`python reflect.py --merge {path.stem}`), edit the "
            f"staged file first if you want it different, or set `status: rejected` "
            f"here and delete the staged file.\n")
    path.write_text(text, encoding="utf-8")


def mark_applied(path: Path, today: str, where: str):
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^status: approved\s*$", "status: applied", text, count=1, flags=re.M)
    text = re.sub(r"^applied:\s*$", f"applied: {today}", text, count=1, flags=re.M)
    text = text.replace("status **pending**", "status **applied**", 1)
    text = text.rstrip() + f"\n\n---\n**Applied {today}** → `{where}`\n"
    path.write_text(text, encoding="utf-8")


def staged_proposals(name_filter: str = ""):
    """(proposal path, frontmatter, target path, staged path) for everything staged."""
    out = []
    if not PROPOSALS.exists():
        return out
    for p in sorted(PROPOSALS.glob("*.md")):
        fm = frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        rel = fm.get("staged", "")
        if fm.get("type") != "proposal" or not rel:
            continue
        if name_filter and name_filter.lower() not in p.stem.lower():
            continue
        scope = fm.get("scope", DEFAULT_SCOPE if fm.get("kind") == "skill" else "vault")
        dest = safe_target(fm.get("target", ""), scope if scope in SCOPES else "vault")
        out.append((p, fm, dest, VAULT / rel))
    return out


def do_diff(a):
    """Show what each staged change would do to its target."""
    items = staged_proposals(a.diff if isinstance(a.diff, str) else "")
    if not items:
        print("nothing staged.")
        return 0
    for p, fm, dest, staged in items:
        print("=" * 70)
        print(f"{p.stem}\n  target: {fm.get('target')}\n  staged: "
              f"{staged.relative_to(VAULT).as_posix()}")
        print("=" * 70)
        if not staged.exists():
            print("  ! the staged file is missing - re-run --apply or reject the proposal")
            continue
        if dest is None or not dest.exists():
            print("  (target does not exist any more - --merge would simply create it)")
            continue
        # git diff --no-index works on files outside a repo and gives colour and
        # context for free; there is no reason to hand-roll a differ here.
        r = subprocess.run(["git", "diff", "--no-index", "--", str(dest), str(staged)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        body = (r.stdout or "").strip()
        print(body if body else "  (no difference - the staged file matches the target)")
    print("=" * 70)
    print(f"{len(items)} staged. Merge one with: reflect.py --merge <name>")
    return 0


def do_merge(a):
    """Copy a staged change over its target. The one place Sigma overwrites.

    Deliberately human-only and one-at-a-time: no scheduled job calls this, it
    takes an explicit name rather than acting on everything, and it refuses
    anything that is not already `approved` and staged. The never-overwrite
    guarantee is about what the loop does unattended — this is you, naming a
    file, after reading its diff.
    """
    items = staged_proposals(a.merge)
    if not items:
        print(f"no staged proposal matches '{a.merge}'. `--diff` lists them.")
        return 1
    if len(items) > 1:
        print(f"'{a.merge}' matches {len(items)} proposals - name one exactly:")
        for p, *_ in items:
            print(f"  {p.stem}")
        return 1

    p, fm, dest, staged = items[0]
    if fm.get("status") != "approved":
        print(f"refusing: {p.stem} is status '{fm.get('status')}', not 'approved'.")
        return 1
    if not staged.exists():
        print(f"refusing: staged file {staged} is missing.")
        return 1
    if dest is None:
        print(f"refusing: target '{fm.get('target')}' does not resolve inside its scope root.")
        return 1

    today = datetime.date.today().isoformat()
    content = staged.read_text(encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    existed = dest.exists()
    dest.write_text(content, encoding="utf-8")
    staged.unlink()
    for parent in (staged.parent, *staged.parent.parents):   # tidy empty dirs
        if parent == STAGED or STAGED not in parent.parents:
            break
        try:
            parent.rmdir()
        except OSError:
            break

    try:
        rel = dest.resolve().relative_to(Path(VAULT).resolve()).as_posix()
    except ValueError:
        rel = None                       # a user-scoped skill: outside the repo
    if rel:
        _ledger_commit(rel, f"sigma(reflect): merge staged change into {rel}",
                       "update", p.stem, p.stem)

    text = p.read_text(encoding="utf-8")
    text = re.sub(r"^staged:.*\n", "", text, count=1, flags=re.M)
    p.write_text(text, encoding="utf-8")
    mark_applied(p, today, fm.get("target", ""))
    log(f"merged {p.stem} -> {fm.get('target')} "
        f"({'overwrote' if existed else 'created'}; git has the previous version)")
    return 0


def do_apply(a):
    if not PROPOSALS.exists():
        log("no proposals folder - nothing to apply.")
        return 0
    today = datetime.date.today().isoformat()
    n_ok = n_skip = n_stage = 0
    for p in sorted(PROPOSALS.glob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = frontmatter(text)
        if fm.get("type") != "proposal" or fm.get("status") != "approved":
            continue
        kind, target = fm.get("kind", ""), fm.get("target", "")
        scope = fm.get("scope", DEFAULT_SCOPE if kind == "skill" else "vault")
        scope = scope if scope in SCOPES else "vault"
        content = proposal_content(text)
        h1 = re.search(r"^# (.+)$", text, re.M)
        title = h1.group(1).strip() if h1 else p.stem

        if kind == "routine" or not content:
            log(f"SKIP {p.name}: kind '{kind}' is not auto-appliable - apply it by hand.")
            n_skip += 1
            continue
        if kind == "contract":
            if a.dry_run:
                print(f"[dry-run] would append {len(content)} chars to CLAUDE.md from {p.name}")
                continue
            where = append_to_contract(content, title, today)
            _ledger_commit("CLAUDE.md",
                           f"sigma(reflect): append to CLAUDE.md - {title[:60]}",
                           "append", title, p.stem)
        else:                                     # skill | note → create a new file
            dest = safe_target(target, scope)
            if dest is None:
                log(f"SKIP {p.name}: target '{target}' is not inside the "
                    f"{scope} root ({SKILL_ROOTS.get(scope, VAULT)}).")
                n_skip += 1
                continue
            # a skill must not exist in EITHER root: the same name in the other
            # scope would shadow or duplicate this one rather than add to it
            clash = next((r / dest.parent.name / "SKILL.md" for r in SKILL_ROOTS.values()
                          if kind == "skill" and (r / dest.parent.name / "SKILL.md").exists()),
                         None)
            if dest.exists() or clash:
                # Apply stays additive — it still will not overwrite. But a
                # proposal that corrects an existing note is the normal case for
                # the auditor and the coach, so dropping it on the floor made
                # both of them structurally unable to land anything. Stage it.
                if a.dry_run:
                    print(f"[dry-run] would stage {dest} -> "
                          f"{staged_path(target, scope)} from {p.name}")
                    continue
                st = stage_change(target, scope, content)
                rel = st.relative_to(VAULT).as_posix()
                mark_staged(p, today, target, rel)
                log(f"STAGED {p.name} -> {rel} ({(clash or dest)} exists; "
                    f"review with --diff {p.stem}, then --merge)")
                n_stage += 1
                continue
            if a.dry_run:
                print(f"[dry-run] would create {dest} ({len(content)} chars, scope={scope}) "
                      f"from {p.name}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content.rstrip() + "\n", encoding="utf-8")
            where = f"{target} ({scope})" if kind == "skill" else target
            if scope == "vault":
                rel = dest.resolve().relative_to(Path(VAULT).resolve()).as_posix()
                _ledger_commit(rel, f"sigma(reflect): create {rel} - {title[:60]}",
                               "create", title, p.stem)
            else:
                # a user-scoped skill lands outside the vault repo: no commit
                # exists to revert, but the ledger still records that it happened
                from sigma import ledger
                ledger.record("reflect", "create", target, None, title,
                              extra={"proposal": p.stem, "scope": scope})
        if not a.dry_run:
            mark_applied(p, today, where)
            log(f"applied {p.name} -> {where}")
            n_ok += 1
    if not a.dry_run:
        log(f"apply finished: {n_ok} applied, {n_stage} staged, {n_skip} skipped")
        if n_stage:
            log(f"  {n_stage} change(s) to existing files are staged in "
                f"06-System/proposed/ - `reflect.py --diff` to review")
    return 0


# --------------------------------------------------------------------------

SCHEDULE_PS = (
    "$a = New-ScheduledTaskAction -Execute '{py}' -Argument '\"{script}\"'; "
    "$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am; "
    # StartWhenAvailable covers a *missed* run (machine off at 9am); RestartCount
    # covers a run that fired and *failed* — which is what happened on 2026-07-26.
    "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable "
    "-DontStopIfGoingOnBatteries -AllowStartIfOnBatteries "
    "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 30); "
    "Register-ScheduledTask -TaskName '{task}' -Action $a -Trigger $t -Settings $s "
    "-Description 'Sigma: weekly reflection over session logs' -Force"
)


def schedule_cmd() -> str:
    return SCHEDULE_PS.format(py=sys.executable, script=os.path.abspath(__file__),
                              task=TASK_NAME)


def task_installed() -> bool:
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def status_report():
    ok = lambda b: "OK " if b else "!! "
    state = load_state()
    print("reflect (Phase 2) - status\n" + "-" * 34)
    print(f"config file : {CONFIG_PATH}")
    print(f"  {ok(CONFIG_PATH.exists())}exists={CONFIG_PATH.exists()}  model={MODEL}  "
          f"max_insights={MAX_INSIGHTS} max_proposals={MAX_PROPOSALS} min_sessions={MIN_SESSIONS}")
    print(f"vault       : {VAULT}")
    n_logs = len(list(SESSIONS.glob("*.md"))) if SESSIONS.exists() else 0
    fresh = len(new_sessions(state))
    print(f"  {ok(SESSIONS.exists())}sessions/  {n_logs} log(s), {fresh} not yet reflected on")
    n_ins = len(list(INSIGHTS.glob("*.md"))) if INSIGHTS.exists() else 0
    print(f"  {ok(INSIGHTS.exists())}insights/  {n_ins} note(s)")
    pend = appr = 0
    if PROPOSALS.exists():
        for p in PROPOSALS.glob("*.md"):
            st = frontmatter(p.read_text(encoding='utf-8', errors='replace')).get("status")
            pend += st == "pending"
            appr += st == "approved"
    n_pr = len(list(PROPOSALS.glob("*.md"))) if PROPOSALS.exists() else 0
    print(f"  {ok(PROPOSALS.exists())}proposals/ {n_pr} note(s): {pend} pending, "
          f"{appr} approved & waiting for --apply")
    for scope, root in SKILL_ROOTS.items():
        n = len(list(root.glob("*/SKILL.md"))) if root.exists() else 0
        fires = "every repo" if scope == "user" else "vault sessions only"
        print(f"  {ok(root.exists())}skills/{scope:<6} {n} skill(s) at {root}  -> fires in {fires}")
    print(f"last run    : {state.get('last_run') or '(never)'}  "
          f"({len(state.get('covered', []))} log(s) covered)")
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20)
        print(f"  {ok(r.returncode == 0)}claude CLI: {(r.stdout or '').strip() or 'not found'}")
    except Exception:
        print("  !! claude CLI: NOT reachable on PATH")
    inst = task_installed()
    print(f"schedule    : {ok(inst)}task '{TASK_NAME}': "
          f"{'installed (Sundays 9am)' if inst else 'NOT installed - run --install-schedule'}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Agentic OS Phase 2: weekly reflection loop.")
    ap.add_argument("--apply", action="store_true", help="execute approved proposals")
    ap.add_argument("--diff", nargs="?", const="", metavar="NAME",
                    help="show staged changes to existing files (optionally filtered)")
    ap.add_argument("--merge", metavar="NAME",
                    help="copy one staged change over its target and mark it applied")
    ap.add_argument("--status", action="store_true", help="health / self-check report")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")
    ap.add_argument("--stdout", action="store_true", help="print the model's JSON, write nothing")
    ap.add_argument("--all", action="store_true", help="reflect over every session log")
    ap.add_argument("--since", help="only session logs dated on/after YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="run even below min_sessions")
    ap.add_argument("--install-schedule", action="store_true")
    ap.add_argument("--uninstall-schedule", action="store_true")
    ap.add_argument("--print-schedule", action="store_true",
                    help="print the PowerShell command that installs the weekly task")
    a = ap.parse_args()

    if a.status:
        return status_report()
    if a.diff is not None:
        return do_diff(a)
    if a.merge:
        return do_merge(a)
    if a.print_schedule:
        print(schedule_cmd()); return 0
    if a.install_schedule:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", schedule_cmd()],
                           capture_output=True, text=True, timeout=60)
        print((r.stdout or "").strip() or (r.stderr or "").strip())
        log(f"install-schedule rc={r.returncode}")
        return r.returncode
    if a.uninstall_schedule:
        r = subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
                           capture_output=True, text=True, timeout=30)
        print((r.stdout or "").strip() or (r.stderr or "").strip())
        return r.returncode
    if a.apply:
        return do_apply(a)
    return do_reflect(a)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"error: {type(e).__name__}: {e}")
        sys.exit(1)
