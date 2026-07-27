# Sigma

A personal agentic OS whose memory substrate is an Obsidian vault.

Agents perceive state (frontmatter, checkboxes, dates), reason, act (write notes, plans,
reports), and remember (git history) — with the vault co-writable by human and agents alike.

> **This repo is the engine, and it was deliberately behind the memory.**
> Memory before engine was the plan, not an accident — so this repo held only a README until
> Phases 0–2 worked. As of 2026-07-27 the running code lives here too (`runtime/`), which also
> means it is finally backed up: it had spent its whole life on one disk, untracked.

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
- **2.5 — Watchdog** ✅ `doctor.py` on `SessionStart` — the phase two silent failures argued for
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

## Layout

```
runtime/            Phases 1–2 — driven by Claude Code hooks + a Windows scheduled task
  sigma/            shared core — settings precedence, frontmatter, `claude -p`, project resolution
  session_logger.py Phase 1 — transcript → session log.   --status --sweep --dry-run
  reflect.py        Phase 2 — logs → insights + proposals. --status --apply --install-schedule
  doctor.py         watchdog — reports Sigma's health into every session.  --quiet --json
tools/              vault utilities that aren't Sigma (PDF → Markdown converter)
```

All three are pure stdlib and self-checking: `--status` reports config, wiring, backlog, and
skill roots. `doctor.py` exists because those self-checks were green while the system was dead —
it runs itself, on `SessionStart`, and speaks only when something is wrong.

**Config, state, and logs are gitignored** (`*.config.json`, `*.state.json`, `*.log`) — not
because they hold secrets, but because they hold *this machine's* operating state, including the
privacy denylist, which names the very client it exists to hide. Copy the `.example` files to set
up a fresh machine.

**Wiring lives outside the repo** and points back into it: two hooks in `~/.claude/settings.json`
(`SessionEnd` → capture, `SessionStart` → sweep + doctor) and the `SigmaOS-WeeklyReflection`
scheduled task. **Quote the interpreter path in every hook** — Claude Code runs hooks through a
POSIX shell, where an unquoted `C:\Python314\python.exe` silently becomes `C:Python314python.exe`
and the hook dies before Python starts. That cost three days of capture once.

## Phase 3 sketch

Local-first web app, Agent SDK backend + React frontend; Tauri as a later native upgrade.
First target is interactive Q&A and synthesis over the vault's own materials. The shared
`sigma` package is the seam this repo builds on rather than reimplementing.

**No API key required.** The Claude Agent SDK is Claude Code packaged as a library — it spawns the
same CLI and inherits the same login, so Phase 3 rides the existing subscription exactly as
`claude -p` does today. Verified with `ANTHROPIC_API_KEY` explicitly unset.

## Privacy

Both this repo and the vault repo are **private**. Session capture honours a path denylist, so
named projects are never summarized to a model at all. Secrets live in a gitignored `.env` and
never in the vault.
