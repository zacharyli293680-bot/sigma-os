#!/usr/bin/env python3
"""
propose.py — the interface's write path, which is not a write.

Phase 3's first slice was read-only, and the note said opening writes up would be
"a deliberate later step, not something to leave ajar now". This is that step.

The rule it has to honour is Sigma's oldest one: **agents observe freely, write
additively, and never apply their own changes.** The reflection loop already
implements it — `reflect.py` drafts a `proposal` note with `status: pending`,
Zach edits or approves it in Obsidian, and `reflect.py --apply` executes it. The
interface reuses that machinery rather than growing a second one, because two
approval gates is the same mistake as two privacy lists: they drift, and you
find out which one was stale after it matters.

**Why a custom tool instead of a scoped `Write`.** The agent could have been
given `Write` with `privacy.py` confining it to `06-System/proposals/`. That
makes safety depend on a path check being right on every call — and the shape of
that bet has already lost twice here (`allowed_tools` shadowing `can_use_tool`,
then `permission_mode` skipping it). So the agent is handed no filesystem write
primitive at all: `propose_change` takes structured fields, and *this module*
writes the file. There is no path argument to get wrong, and "the agent cannot
apply a change" stops being a rule it might route around and becomes a fact
about which tools exist.

The proposal body is written by `reflect.write_proposal`, so a proposal raised
from a chat is byte-identical in shape to one raised by the weekly reflection —
same frontmatter contract, same `<!-- proposal:content -->` block, same
`--apply`.
"""
import datetime
import re
import sys
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))
import reflect as rf  # noqa: E402

# Mirrors reflect.KINDS. `routine` is included on purpose: "change how you work"
# is a legitimate outcome of a conversation, and reflect.write_proposal already
# knows to mark it not-auto-appliable rather than pretending a script will do it.
KINDS = rf.KINDS
SCOPES = rf.SCOPES

# Every proposal THIS PROCESS wrote, in order — the fleet's attribution record.
# The fleet used to attribute proposals to a specialist by diffing the
# proposals directory before/after its conversation, which swept in anything
# that appeared in the window: a tutor- or chat-raised proposal from the
# dashboard process, even a file arriving via git pull — and apply_run would
# then auto-apply a kind:note nobody's run had raised, under the wrong actor.
# This list only ever gains a name when write_proposal actually returned, so
# slicing it around a run can neither overstate nor cross-attribute: the
# interface's writes happen in the uvicorn process and are invisible here.
WRITTEN: list = []


def _clean(value, allowed, default):
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def _headings_in(text: str) -> set:
    """Markdown H2/H3 headings, normalised — outside fenced code."""
    body = re.sub(r"```.*?```", "", text or "", flags=re.S)
    return {m.group(1).strip().lower()
            for m in re.finditer(r"^#{2,3}\s+(.+?)\s*$", body, re.M)}


def _existing_headings() -> set:
    try:
        return _headings_in(rf.CONTRACT.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return set()          # cannot read the contract: do not block on it


@tool(
    "propose_change",
    "Draft a change to the vault or to Sigma itself for Zach to review. This is "
    "the ONLY way you can affect anything on disk: it writes a pending proposal "
    "note, it does not make the change. Zach reads it in Obsidian, edits it if he "
    "wants it different, approves it, and a separate command applies it. Use this "
    "when the user asks you to change, add, fix, or record something.",
    {
        "title": str,        # short imperative name, becomes the filename
        "kind": str,         # skill | contract | note | routine
        "target": str,       # path it would write, relative to the scope root
        "content": str,      # EXACTLY what gets written if approved
        "rationale": str,    # why this is worth doing
        "risk": str,         # low | medium | high
        "scope": str,        # skill only: user | vault
    },
)
async def propose_change(args: dict) -> dict:
    """Write one pending proposal and tell the model what happened.

    Returns a normal (non-error) result even when it refuses, so the agent can
    explain the refusal to the user instead of the run dying on an exception.
    """
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "")
    if not title:
        return {"content": [{"type": "text",
                             "text": "Refused: a proposal needs a title."}],
                "is_error": True}
    if not content.strip() and _clean(args.get("kind"), KINDS, "note") != "routine":
        return {"content": [{"type": "text",
                             "text": "Refused: `content` is what actually gets written "
                                     "if Zach approves, so it cannot be empty. Draft the "
                                     "real file content, not a description of it."}],
                "is_error": True}

    kind = _clean(args.get("kind"), KINDS, "note")

    # A `contract` proposal is APPENDED to CLAUDE.md, so its content must be a
    # self-contained addition. On 2026-07-28 the auditor drafted a copy of the
    # contract's own "## Frontmatter schemas" section instead, and because the
    # content block is written literally, CLAUDE.md ended up with a second,
    # truncated copy of a section it already had — the drift-fixing proposal
    # introducing drift. Refuse the shape rather than trusting the drafter.
    if kind == "contract":
        dupes = _existing_headings() & _headings_in(content)
        if dupes:
            return {"content": [{"type": "text",
                                 "text": (f"Refused: a `contract` proposal is *appended* to "
                                          f"CLAUDE.md, so its content must be a new, "
                                          f"self-contained addition. This content re-opens "
                                          f"{', '.join(sorted(dupes))}, which already exists — "
                                          f"applying it would give the contract two copies of "
                                          f"that section. Draft only the new material, under a "
                                          f"heading that is not already in the contract.")}],
                    "is_error": True}

    pr = {
        "title": title,
        "kind": kind,
        "target": str(args.get("target") or "").strip(),
        "content": content,
        "rationale": (str(args.get("rationale") or "").strip()
                      or "Raised from a conversation in the Sigma interface."),
        "risk": _clean(args.get("risk"), ("low", "medium", "high"), "medium"),
        "scope": _clean(args.get("scope"), SCOPES, rf.DEFAULT_SCOPE),
        # Provenance: the weekly loop fills this with the insight that produced the
        # proposal. A chat-raised one has no insight behind it, and saying so is
        # better than inventing a citation — the audit trail should show which of
        # the two paths raised it.
        "insight": "",
    }

    try:
        today = datetime.date.today().isoformat()
        path = rf.write_proposal(pr, today)
    except Exception as e:                      # never take the conversation down
        return {"content": [{"type": "text",
                             "text": f"Could not write the proposal: "
                                     f"{type(e).__name__}: {e}"}],
                "is_error": True}

    WRITTEN.append(path.name)
    rel = path.name
    return {"content": [{"type": "text",
                         "text": (f"Wrote proposal [[{path.stem}]] "
                                  f"(06-System/proposals/{rel}) with status: pending. "
                                  f"Nothing has changed on disk yet. Tell the user it is "
                                  f"waiting for review, cite it as [[{path.stem}]], and "
                                  f"do not claim the change itself was made.")}]}


def proposal_server():
    """The in-process MCP server carrying the one tool the agent may write through."""
    return create_sdk_mcp_server(name="sigma", version="0.1.0",
                                 tools=[propose_change])


# The SDK namespaces in-process MCP tools as mcp__<server>__<tool>.
PROPOSE_TOOL = "mcp__sigma__propose_change"
