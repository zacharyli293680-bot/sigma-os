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
def model_allow_prefixes() -> tuple:
    """The model-boundary exemptions, loaded once per process. A missing or
    broken config yields no exemptions — never a wider opening."""
    try:
        cfg = json.loads((_RUNTIME / "privacy.config.json").read_text(encoding="utf-8"))
        return tuple(str(p).replace("\\", "/").strip("/").lower()
                     for p in cfg.get("model_allow", []) if str(p).strip())
    except Exception:
        return ()


def is_model_allowed(rel_posix: str) -> bool:
    """May this vault-relative (gitignored) path reach the model / the panels?
    Exact file match, or anything under a listed directory."""
    r = rel_posix.replace("\\", "/").strip("/").lower()
    return any(r == p or r.startswith(p + "/") for p in model_allow_prefixes())

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
            return r.returncode == 0          # 0 = ignored, 1 = not, 128 = error
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
