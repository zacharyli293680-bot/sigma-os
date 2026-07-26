# Sigma

A personal agentic OS whose memory substrate is an Obsidian vault.

Agents perceive state (frontmatter, checkboxes, dates), reason, act (write notes, plans,
reports), and remember (git history) — with the vault co-writable by human and agents alike.

> **This repo is the engine. It is deliberately behind the memory.**
> Phases 1 and 2 ship as two Python scripts plus Claude Code hooks; this repo starts at
> Phase 3, the application layer. Memory before engine was the plan, not an accident.

## The three systems

Kept separate on purpose — it's what makes the storage, repo, and privacy questions answerable.

| | What it is | Where it lives |
|---|---|---|
| **Knowledge Vault** | life's facts — the OS's model of *you* | private Obsidian vault (separate repo) |
| **Agent Memory** | the OS's record of its own work — model of *itself* | `06-System/` inside that vault |
| **OS Application** | interface + orchestration + agent code — the *engine* | **this repo** |

**Two repos, never nested.** A vault inside the code repo is one `git add .` away from publishing
personal notes. The separation is the safety property, not a preference.

## Memory model

Five layers, raw → distilled:

| Layer | What | Written by |
|---|---|---|
| **L0** | raw Claude Code transcripts (`~/.claude/projects/*.jsonl`) | Claude Code — *summarize, never parse*; the schema is version-internal |
| **L1** | session logs, one Markdown note per session | `session_logger.py` (Phase 1) |
| **L2** | semantic notes, cross-linked from the logs | human + agents |
| **L3** | procedural memory — skills + `CLAUDE.md` contract | approved proposals |
| **L4** | durable insights distilled from L1 | `reflect.py` (Phase 2) |

## Phases

- **0 — Foundation** ✅ structured vault, frontmatter contract, course study systems
- **1 — Session logging** ✅ `SessionEnd` hook + `SessionStart` sweep + Haiku summarizer → L1 notes
- **2 — Reflection & skills** ✅ weekly reflection → insights + proposals, propose-and-approve learning
- **3 — Interface** ⬜ *next* — local web app: Claude Agent SDK backend + React frontend
- **4 — Specialist fleet** ⬜ planner / coach / auditor / tracker; git commits as the coordination log

## Design rules

Non-negotiable, and mechanical rather than promised wherever possible:

- **Observe freely, write additively, never delete.** Agents never check the human's boxes.
- **Propose, don't self-apply.** The reflection loop can only write proposals; a human flips
  `status: approved` before anything executes. There is no auto-apply path to relax.
- **Pull before write**, and git is the undo.
- **Scoped writes.** An apply step is confined to its scope root and refuses anything resolving
  outside it.
- **Contract drift is the failure mode** — a field derived from one unvalidated input fails
  silently, so health checks report what the resolver can *see*, not just that it ran.

## Phase 1–2 runtime (outside this repo, for now)

Lives in `~/.obsidian-tools/`, driven by Claude Code hooks and a Windows scheduled task:

| | |
|---|---|
| `sigma/` | shared core — settings precedence, frontmatter, the `claude -p` call, project resolution |
| `session_logger.py` | Phase 1 — transcript → session log. `--status`, `--sweep`, `--dry-run` |
| `reflect.py` | Phase 2 — logs → insights + proposals. `--status`, `--apply`, `--install-schedule` |

Both are self-checking: `--status` reports config, wiring, backlog, and skill roots.

## Phase 3 sketch

Local-first web app, Agent SDK backend + React frontend; Tauri as a later native upgrade.
First target is interactive Q&A and synthesis over the vault's own materials. The shared
`sigma` package is the seam this repo builds on rather than reimplementing.

## Privacy

Both this repo and the vault repo are **private**. Session capture honours a path denylist, so
named projects are never summarized to a model at all. Secrets live in a gitignored `.env` and
never in the vault.
