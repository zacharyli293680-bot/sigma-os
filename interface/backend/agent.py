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
from propose import PROPOSE_TOOL, proposal_server

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

# The one way the agent may affect disk. It is not a filesystem tool: it takes
# structured fields and the backend writes a *pending proposal*, so the agent
# holds no primitive that could edit a note directly. See propose.py.
WRITE_TOOLS = [PROPOSE_TOOL]

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

## Changing things

You cannot edit the vault. You have no tool that writes a note. What you have is
`propose_change`, which drafts a **pending proposal** for Zach to review — the
same gate the weekly reflection goes through.

- When Zach asks you to change, add, fix, or record something, call
  `propose_change` and then **say a proposal is waiting**, citing it by wikilink.
- `content` must be the real file content, exactly as it should be written if
  approved — not a summary of it. Zach edits that block if he wants it different,
  so a vague draft makes more work, not less.
- **Never say you made the change.** You did not. Nothing reaches disk until Zach
  approves it and runs the apply step. Claiming otherwise would be a lie the
  vault then contradicts.
- If a request is better answered than actioned, just answer it. Not every
  question needs a proposal.
""".strip()


def build_options(allow_proposals: bool = True,
                  orientation: str = ORIENTATION,
                  model: str | None = None,
                  effort: str = "medium",
                  max_turns: int = 30,
                  actor: str = "interface") -> ClaudeAgentOptions:
    """Options for one agent run — a chat question, or one Phase 4 specialist.

    Parameterised rather than copied because the *guarantees* below must not be
    re-derived per caller. A specialist that built its own options would be one
    edit away from a model path with no privacy guard on it, which is exactly
    the carve-out this vault paid a history rewrite to make.

    `allow_proposals` toggles the *proposal* tool only. It deliberately does not
    reach `VaultPrivacy`, which is constructed with `allow_writes=False`
    unconditionally: `Write`, `Edit` and `Bash` are refused at the PreToolUse
    boundary no matter how this is called. Wiring one flag to both would mean
    "let the agent propose" and "let the agent edit notes" were the same switch,
    and the second is a thing nothing here may do.

    `actor` only labels audit lines, so a refusal can be traced to the run that
    caused it: "interface" for a chat question, the specialist's key otherwise.
    """
    # ONE list, used twice — as the grant and as the gate. The guard refuses any
    # tool outside it (privacy.VETTED_TOOLS), and building it here means the two
    # cannot drift: adding a tool to the run necessarily adds it to what the
    # guard will vet, and forgetting to do so fails closed rather than open.
    granted = READ_ONLY_TOOLS + (WRITE_TOOLS if allow_proposals else [])
    privacy = VaultPrivacy(VAULT, allow_writes=False,
                           granted_tools=granted, actor=actor)
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
        tools=granted,
        allowed_tools=[],
        # In-process MCP server: no subprocess, no port, and it inherits this
        # process's vault paths. Registered even when proposals are off so the
        # server list does not change shape between modes; the tool is withheld
        # via `tools` above, which is the switch that actually decides.
        mcp_servers={"sigma": proposal_server()},
        # Keep Claude Code's tool-use competence, add our framing on top.
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": orientation},
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
        max_turns=max_turns,
        effort=effort,
        **({"model": model} if model else {}),
    )
