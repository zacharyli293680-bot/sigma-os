#!/usr/bin/env python3
"""
agent.py — the Agent SDK configuration behind Sigma's interface.

Phase 3's first slice: ask the vault a question, get a grounded answer. The
agent reads the vault directly rather than a pre-built index, so answers reflect
what is on disk right now and cite real notes.

Why the SDK and not `claude -p` (which Phases 1–2 use): the SDK is Claude Code
packaged as a library, so it inherits the same subscription login — no API key,
no second bill — while giving us a real tool loop, token streaming, and a
permission callback we can enforce privacy through. Shelling out gives one blob
of text at the end and no way to veto a file read.
"""
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

from privacy import VaultPrivacy

# The shared core is the seam this repo builds on rather than reimplementing —
# it already knows where the vault is and how to read its frontmatter.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))
from sigma import DEFAULT_VAULT  # noqa: E402

VAULT = Path(DEFAULT_VAULT)

# Read-only. Grep and Glob are ripgrep-backed and skip gitignored files on their
# own, but that is a convenience, not the guarantee — VaultPrivacy vets every
# path argument, and Read is vetted because a direct read is the one that would
# otherwise walk straight into the carve-out.
READ_ONLY_TOOLS = ["Read", "Glob", "Grep"]

ORIENTATION = """
You are Sigma, answering questions about Zach's Obsidian vault — his single
source of truth for coursework, internship work, projects, and job applications.
The working directory IS the vault.

How to answer well here:

- **Ground every claim in a note you actually read.** This vault is a knowledge
  base, not your memory. If you did not read it this turn, do not assert it.
- **Cite by wikilink** — `[[note-name]]`, basename only, no folder and no `.md`.
  Zach reads answers inside Obsidian, so those render as working links.
- **Start from the structure.** `CLAUDE.md` is the vault's contract: it defines
  the folder layout and the frontmatter schema every note follows (`type`,
  `status`, `due`, `course`, ...). Those fields are queryable — prefer a targeted
  Grep on frontmatter over reading many notes. `MAP.md` and `Home.md` are the
  navigation entry points; a course's `<code>.md` is that course's manifest.
- **Say when the vault is silent.** "There is no note on that" is a real and
  useful answer. Do not fill gaps with plausible inference — a wrong answer that
  sounds like a note is worse than no answer, because it will get trusted.
- **Answer in prose, briefly.** Lead with the answer, then the supporting
  detail. Skip preamble.

Some paths are deliberately private and reading them is refused. If that happens,
say so plainly and answer from what you can legitimately see — do not try to
reach the same content another way.

You are read-only. You cannot edit the vault, and should not offer to.
""".strip()


def build_options(allow_writes: bool = False) -> ClaudeAgentOptions:
    privacy = VaultPrivacy(VAULT, allow_writes=allow_writes)
    return ClaudeAgentOptions(
        cwd=str(VAULT),
        # `tools` limits which tools EXIST. `allowed_tools` would be a different
        # thing entirely: "may be called *without being prompted*" — and since
        # can_use_tool IS the prompt, listing a tool there auto-approves it and
        # the guard never runs. Verified the hard way: with Read in
        # allowed_tools the agent read two carved-out notes and reported zero
        # denials. The SDK warns about this (CanUseToolShadowedWarning); the
        # warning is easy to miss, the silence is not obvious, and the failure
        # looks exactly like success. Leave allowed_tools empty.
        tools=READ_ONLY_TOOLS,
        allowed_tools=[],
        # Keep Claude Code's tool-use competence, add our framing on top.
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": ORIENTATION},
        # PreToolUse fires for EVERY tool call regardless of permission mode.
        # This is the guarantee; can_use_tool below is a second layer that only
        # covers calls which would otherwise prompt.
        hooks={"PreToolUse": [HookMatcher(matcher=None,
                                          hooks=[privacy.pre_tool_hook])]},
        can_use_tool=privacy.can_use_tool,
        permission_mode="default",
        # Load NO filesystem settings. The default (None) loads all of them,
        # which would drag in ~/.claude/settings.json — and with it Sigma's own
        # SessionStart hooks, firing a capture sweep and the watchdog on every
        # question the interface is asked.
        setting_sources=[],
        include_partial_messages=True,  # token-level streaming to the browser
        max_turns=30,
        effort="medium",
    )
