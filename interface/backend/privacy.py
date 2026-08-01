#!/usr/bin/env python3
"""
privacy.py — keep the interface's agent out of anything the vault keeps local.

The vault already declares what must never leave this machine: `.gitignore`. The
carve-out that keeps internship notes off GitHub is written there, and the
pre-push hook enforces it at the *push* boundary. This module enforces the same
declaration at the *model* boundary.

The original rule, stated once, held in both directions:

    if git will not sync it, the model does not see it.

**Amended 2026-07-30 (Option B):** the two boundaries are now deliberately
decoupled. Zach's internship agreement permits AI tools, so the ProCertus
material may reach the model — but it still must never reach his personal
GitHub, so it stays gitignored and the push boundary is untouched. The
exception is an explicit `model_allow` prefix list in
`runtime/privacy.config.json` (gitignored, like every config that names the
client). Everything gitignored and *not* listed stays refused, fail-closed.

That list is exactly the "second list that can drift" the original design
refused, so the drift is made visible instead of trusted: the watchdog reports
the active exemptions in every session's context, and an unreadable config
means no exemptions at all — the failure direction is over-blocking, which is
loud, never under-blocking, which is silent.
"""
import json
import subprocess
from functools import lru_cache
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"


@lru_cache(maxsize=1)
def model_allow_raw() -> tuple:
    """The exemptions exactly as the operator wrote them — for *display*, never
    for matching. A missing or broken config yields no exemptions, never a
    wider opening."""
    try:
        cfg = json.loads((_RUNTIME / "privacy.config.json").read_text(encoding="utf-8"))
        return tuple(str(p).strip() for p in cfg.get("model_allow", []) if str(p).strip())
    except Exception:
        return ()


@lru_cache(maxsize=1)
def model_allow_prefixes() -> tuple:
    """The same list normalised for matching: forward slashes, no surrounding
    separators, lower-cased. Kept separate from the raw form because showing a
    path back to the operator in a shape they did not write reads like a bug —
    the audit view lists `02-Areas/ProCertus/`, not `02-areas/procertus`."""
    return tuple(p.replace("\\", "/").strip("/").lower() for p in model_allow_raw())


def is_model_allowed(rel_posix: str) -> bool:
    """May this vault-relative (gitignored) path reach the model / the panels?
    Exact file match, or anything under a listed directory."""
    r = rel_posix.replace("\\", "/").strip("/").lower()
    return any(r == p or r.startswith(p + "/") for p in model_allow_prefixes())


def gitignore_scan(vault: Path, rels: list) -> tuple:
    """One `git check-ignore` pass, split into the two things the dashboard
    needs — plus whether git actually answered.

        sealed   gitignored and NOT model-exempt. Hidden from every panel.
        no_sync  gitignored and model-exempt. Shown, and marked *never leaves
                 this machine* (dashboard-plan §6, Phase 5).
        answered False if git could not be asked at all.

    The single implementation of the boundary — panels must not grow their own
    copy with different failure semantics (they did once, and the two disagreed
    about git exit 128).

    utf-8 is explicit because the default is cp1252 on Windows, where one
    emoji in a *filename* would raise on encode and silently seal everything.
    NUL separation because text-mode newline translation broke this once.

    **The two failure directions are not symmetric, so they fail differently.**
    Hiding is a safety measure, so it fails *on*: if git cannot answer,
    everything non-exempt is sealed. Marking is a *claim about confidentiality*
    — "you may put client material here, it cannot leave" — so it fails *off*:
    an unanswered git yields no marks at all. Marking something no-sync that in
    fact syncs is the one error in this module that could cause a breach rather
    than an inconvenience, so it is never made on a guess.

    That also means a mark requires two independent yeses: git must refuse the
    path *and* the operator must have listed it. A path wrongly listed in
    `model_allow` but actually tracked by git is never marked.
    """
    if not rels:
        return set(), set(), True
    ignored, answered = set(rels), False
    try:
        r = subprocess.run(["git", "-C", str(vault), "check-ignore", "--stdin", "-z"],
                           input="\0".join(rels), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=15)
        if r.returncode in (0, 1):        # 0 = some ignored, 1 = none
            ignored, answered = {s for s in r.stdout.split("\0") if s}, True
    except Exception:
        pass
    sealed = {s for s in ignored if not is_model_allowed(s)}
    no_sync = {s for s in ignored if is_model_allowed(s)} if answered else set()
    return sealed, no_sync, answered


def sealed_paths(vault: Path, rels: list) -> set:
    """Just the hidden half, for callers that only filter."""
    return gitignore_scan(vault, rels)[0]

# Tools whose arguments name a path we must vet before the model sees the result.
PATH_ARGS = {
    "Read": ("file_path",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "Glob": ("path",),
    "Grep": ("path",),
}

# Always refused. Writing arrived in 2026-07-28, and deliberately not through
# these: the agent proposes via a structured `propose_change` tool and the
# backend writes the proposal note (see propose.py). So there is still no path
# by which the model edits a note directly, and `allow_writes` stays False —
# it exists for a future caller that has earned it, not for the interface.
WRITE_TOOLS = {"Write", "Edit", "NotebookEdit", "Bash", "KillShell", "BashOutput"}

# The proposal tool's canonical name lives in propose.py. This is a deliberate
# literal copy: privacy.py imports nothing from the backend it guards, so that
# the guard cannot be broken by a change to the thing being guarded.
# `test_tool_gate` asserts the two agree — a second declaration is surfaced,
# never trusted, exactly as `model_allow` is.
PROPOSE_TOOL_NAME = "mcp__sigma__propose_change"

# Every tool this module knows how to reason about. Anything else is REFUSED.
#
# **Inverted 2026-08-01, and this is the load-bearing line in the file.** Until
# then `refusal()` returned None — allowed — for any tool that was neither a
# write tool nor a key in PATH_ARGS. That made the guard an allowlist of things
# to *check* rather than a denylist of things to *permit*, and it was survivable
# only because `tools=` limited what existed at all.
#
# It stops being survivable the moment a tool arrives whose argument is a URL
# rather than a path: `browser_navigate` has no `file_path`, so every loop in
# `_classify` would skip it and the call would sail through unvetted, with the
# run reporting zero denials. "Zero denials" is exactly what this guard reported
# the two times it was already found not to be running (SYSTEM.md §12, #3). The
# lesson was that a guard which cannot see a tool must not wave it through.
#
# `granted_tools` narrows this further per caller; this set is the floor for a
# VaultPrivacy built without one, so the fail-closed property never depends on
# a caller having remembered.
VETTED_TOOLS = frozenset(PATH_ARGS) | WRITE_TOOLS | {PROPOSE_TOOL_NAME}

# Best-effort: refusals are recorded so a *false* refusal is visible rather than
# silent (see sigma/audit.py). Guarded because privacy.py is imported by
# runtime scripts, by the backend, and by tests, and a missing audit log must
# cost a log line rather than the guard itself.
try:                                                # pragma: no cover - wiring
    import sys as _sys
    if str(_RUNTIME) not in _sys.path:
        # The same insert app.py and panels.py already do. Module names under
        # runtime/ are chosen not to shadow the stdlib (todo.py, not queue.py),
        # so this adds no new hazard.
        _sys.path.insert(0, str(_RUNTIME))
    from sigma import audit as _audit
except Exception:
    _audit = None


class VaultPrivacy:
    """Decides whether the agent may use a given tool, on a given path."""

    def __init__(self, vault: Path, allow_writes: bool = False,
                 granted_tools=None, actor: str = "interface"):
        """`granted_tools` should be the exact list handed to
        `ClaudeAgentOptions(tools=...)`. Passing it makes the gate and the grant
        the same statement rather than two that can drift — build_options builds
        one list and uses it twice. None falls back to VETTED_TOOLS.

        `actor` only labels audit lines: which run refused what.
        """
        self.vault = Path(vault).resolve()
        self.allow_writes = allow_writes
        self.granted = frozenset(granted_tools) if granted_tools is not None else None
        self.actor = actor

    # -- the underlying question, cached because git check-ignore is a subprocess
    @staticmethod
    @lru_cache(maxsize=2048)
    def _git_ignored(vault_str: str, rel: str) -> bool:
        try:
            r = subprocess.run(
                ["git", "-C", vault_str, "check-ignore", "-q", "--", rel],
                capture_output=True, timeout=15)
            if r.returncode == 0:
                return True                   # ignored
            if r.returncode == 1:
                return False                  # a normal, syncable path
            # 128 etc: git could not answer — and obsidian-git touches this
            # repo every 15 minutes, so "could not answer" is routine, not
            # exotic. Fail closed, matching the promise below.
            return True
        except Exception:
            # Fail closed. A privacy control that opens up when git hiccups is
            # not a privacy control.
            return True

    def verdict(self, raw_path: str) -> str | None:
        """None if allowed, else a human-readable reason for refusing."""
        if not raw_path:
            return None
        try:
            p = Path(raw_path)
            p = (p if p.is_absolute() else self.vault / p).resolve()
        except (OSError, ValueError):
            return "that path could not be resolved"

        # Scoped reads: the agent's world is the vault and nothing above it.
        try:
            rel = p.relative_to(self.vault)
        except ValueError:
            return "that path is outside the vault"

        # NTFS alternate data streams: "note.md::$DATA" resolves and opens
        # exactly like the note, but git check-ignore does not match the
        # suffixed name — a verified bypass of the sealed boundary. No
        # legitimate vault-relative path contains a colon, so refuse them all.
        if ":" in rel.as_posix():
            return "that path carries an NTFS stream or drive qualifier"

        if self._git_ignored(str(self.vault), rel.as_posix()):
            if is_model_allowed(rel.as_posix()):
                # Option B (2026-07-30): explicitly exempted at the model
                # boundary while staying gitignored — it may be read, and it
                # still never syncs.
                return None
            return ("that path is excluded from the vault's git repo, which marks "
                    "it as local-only material that must not be sent to a model")
        return None

    def _is_vetted(self, tool: str) -> bool:
        """Was this tool actually granted to this run? See VETTED_TOOLS."""
        return tool in (self.granted if self.granted is not None else VETTED_TOOLS)

    def _classify(self, tool: str, args: dict) -> tuple:
        """(rule, message) for a refusal, or (None, None) to allow.

        **Pure**, deliberately: the enforcement paths below record to the audit
        log, this only decides. A decision function with a side effect is one
        tests cannot call freely, and this is the function that most needs
        calling freely.
        """
        if tool in WRITE_TOOLS and not self.allow_writes:
            return "write-tool", (
                f"{tool} is disabled. This interface is read-only: it answers "
                f"questions about the vault and never edits it.")

        if not self._is_vetted(tool):
            # Fail closed on the unknown. The message tells the model the truth
            # — this is a configuration boundary, not a judgement about the
            # request — so it reports the wall instead of trying to climb it.
            return "unvetted-tool", (
                f"{tool} is not available in this run. It is not one of the "
                f"tools this agent was granted, so it is refused before it "
                f"runs. Say plainly that you cannot do that here and answer "
                f"with the tools you do have.")

        for key in PATH_ARGS.get(tool, ()):
            why = self.verdict(str(args.get(key) or ""))
            if why:
                return "path", (
                    f"Refused {tool} on {args.get(key)!r}: {why}. Tell the user "
                    f"this material is deliberately private, and answer from "
                    f"what you can legitimately see. Do not try to reach the "
                    f"same content another way.")
        return None, None

    def refusal(self, tool: str, args: dict) -> str | None:
        """The single decision both enforcement paths share. None = allowed."""
        return self._classify(tool, args)[1]

    def _record(self, rule: str, tool: str, args: dict) -> None:
        """Write one refusal to the audit log. Never raises."""
        if _audit is None:
            return
        detail = ""
        for key in PATH_ARGS.get(tool, ()):
            if args.get(key):
                detail = str(args.get(key))
                break
        try:
            _audit.record("refused", self.actor, tool, detail, rule=rule)
        except Exception:
            pass

    async def pre_tool_hook(self, input_data: dict, tool_use_id, context) -> dict:
        """PreToolUse hook — the enforcement that actually holds.

        `can_use_tool` alone is not enough, and the way it fails is the dangerous
        kind. It is only consulted for calls that would otherwise *prompt*, and in
        `permission_mode="default"` Claude Code treats Read/Grep/Glob as safe and
        auto-approves them — so the callback is never reached, no error is raised,
        and the run reports zero denials. Twice during this build that looked
        exactly like a guard working.

        Hooks have no such conditionality: PreToolUse fires for every tool call,
        every time, whatever the permission mode. That is why the guarantee lives
        here and `can_use_tool` is kept only as a second layer.
        """
        tool = str(input_data.get("tool_name") or "")
        args = input_data.get("tool_input") or {}
        rule, why = self._classify(tool, args)
        if why is None:
            return {}
        self._record(rule, tool, args)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": why}}

    async def can_use_tool(self, tool: str, args: dict, ctx) -> object:
        """Second layer, for tools that do route through the permission prompt.

        Calls `_classify` rather than re-deriving the decision. It used to
        carry its own copy of the write-tool and path checks, which meant two
        implementations of one boundary that could disagree — and the docstring
        on `refusal` already claimed they were one. They are now.

        This layer is only reached when PreToolUse allowed the call, so a
        refusal here is recorded exactly once, not twice.
        """
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        rule, why = self._classify(tool, args)
        if why is not None:
            self._record(rule, tool, args)
            return PermissionResultDeny(behavior="deny", interrupt=False, message=why)

        return PermissionResultAllow(behavior="allow", updated_input=None,
                                     updated_permissions=None)
