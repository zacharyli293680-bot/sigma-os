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


class VaultPrivacy:
    """Decides whether the agent may touch a given path."""

    def __init__(self, vault: Path, allow_writes: bool = False):
        self.vault = Path(vault).resolve()
        self.allow_writes = allow_writes

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

    def refusal(self, tool: str, args: dict) -> str | None:
        """The single decision both enforcement paths share. None = allowed."""
        if tool in WRITE_TOOLS and not self.allow_writes:
            return (f"{tool} is disabled. This interface is read-only: it answers "
                    f"questions about the vault and never edits it.")
        for key in PATH_ARGS.get(tool, ()):
            why = self.verdict(str(args.get(key) or ""))
            if why:
                return (f"Refused {tool} on {args.get(key)!r}: {why}. Tell the user "
                        f"this material is deliberately private, and answer from "
                        f"what you can legitimately see. Do not try to reach the "
                        f"same content another way.")
        return None

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
        why = self.refusal(str(input_data.get("tool_name") or ""),
                           input_data.get("tool_input") or {})
        if why is None:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": why}}

    async def can_use_tool(self, tool: str, args: dict, ctx) -> object:
        """Second layer, for tools that do route through the permission prompt."""
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if tool in WRITE_TOOLS and not self.allow_writes:
            return PermissionResultDeny(
                behavior="deny", interrupt=False,
                message=(f"{tool} is disabled. This interface is read-only: it answers "
                         f"questions about the vault and never edits it."))

        for key in PATH_ARGS.get(tool, ()):
            why = self.verdict(str(args.get(key) or ""))
            if why:
                return PermissionResultDeny(
                    behavior="deny", interrupt=False,
                    message=(f"Refused {tool} on {args.get(key)!r}: {why}. "
                             f"Tell the user this material is deliberately private "
                             f"and answer from what you can legitimately see."))

        return PermissionResultAllow(behavior="allow", updated_input=None,
                                     updated_permissions=None)
