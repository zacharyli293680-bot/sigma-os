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

> **[SYSTEM.md](SYSTEM.md) is the full picture** — every component, how the pieces reach each other,
> the guarantees and how each is enforced, current state, and the known gaps. Read that if you are
> picking this up after a break. This file is the introduction.

## Phases

- **0 — Foundation** ✅ structured vault, frontmatter contract, course study systems
- **1 — Session logging** ✅ `SessionEnd` hook + `SessionStart` sweep + Haiku summarizer → L1 notes
- **2 — Reflection & skills** ✅ weekly reflection → insights + proposals, propose-and-approve learning
- **2.5 — Watchdog** ✅ `doctor.py` on `SessionStart` — the phase two silent failures argued for
- **3 — Interface** ✅ local web app: Claude Agent SDK backend + React frontend. Asks the vault a
  question and cites the notes it read; proposes changes rather than making them; one process
  serves the API and the built UI.
- **4 — Specialist fleet** ✅ planner / coach / auditor / tracker, on a daily schedule.
  **Sequenced, not fanned out** — one subscription means the ceiling is a rate-limit window, so
  concurrency is the budget and the runner has no parallel option.

## Commands

`sigma` is the front door. Five scripts had five flag vocabularies and needed two different
interpreters; this picks the interpreter and gives the verbs one shape.

```
sigma                     what needs your attention right now
sigma doctor              health check (what SessionStart runs)   --json --quiet
sigma fleet status        what ran, when, and what it raised
sigma fleet run           the specialists that are due   --only KEY --all --dry-run
sigma reflect run         distil insights + proposals    --all --since --dry-run
sigma reflect apply       execute approved proposals
sigma reflect diff        review changes staged against existing notes
sigma reflect merge NAME  apply one staged change to its target
sigma capture status      session-logging self-check + backlog
sigma capture sweep       log any session the hook missed   --max N
sigma intake              what is waiting in the drop folder
sigma intake run          course material -> notes   --course --dry-run --keep --max N
sigma install [what]      git hooks and scheduled tasks (all | hooks | schedules)
sigma ui                  start the interface   --port
```

On Windows use `sigma.cmd`; the `sigma` shell script is the POSIX equivalent. Neither needs to be
on `PATH` to work — an absolute path is fine, which is what a scheduled task uses.

**Study intake** reads a drop folder rather than a path argument, so the palette
verb stays a dictionary key with nothing user-supplied in it:

```
00-Inbox/intake/<COURSE>/<anything>.pdf|.pptx|.md|.txt
```

Slides keep their structure — slide number, title, bullets, and the speaker
notes, which are often the only place a deck explains itself rather than
listing. Master-slide furniture (the copyright line and page number repeated on
every slide) is stripped: it was ~30% of the text in a sample AA-210 deck.

The subfolder must match a real folder under `02-Areas/Academics/`; anything else
is reported and skipped rather than filed somewhere plausible. Sources are
cleared only once a note actually lands.

**Tests** are stdlib `unittest`, not pytest, and there is no `sigma` verb for them:

```
interface\backend\.venv\Scripts\python -m unittest discover -s tests -t tests
```

Both halves of that are load-bearing. The backend venv supplies `fastapi`/`httpx`, which the endpoint
suites import; `-t tests` is required because `tests/` deliberately has no `__init__.py`, and a bare
`discover` fails with *"Start directory is not importable"* rather than anything that names the cause.

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
sigma.cmd / sigma   the front door — dispatches to everything below
runtime/            hooks, scheduled tasks, and the CLI. Pure stdlib except fleet.
  sigma/            shared core — settings, frontmatter, `claude -p`, state files, UTF-8 output
  cli.py            the `sigma` command; picks the interpreter each subcommand needs
  session_logger.py Phase 1 — transcript → session log
  reflect.py        Phase 2 — logs → insights + proposals; also --diff / --merge for staged changes
  doctor.py         watchdog — reports Sigma's health into every session
  fleet.py          Phase 4 — runs the specialists, one at a time, under a lock
  specialists.py    who the specialists are and what each is briefed to do
  install_hooks.py  copies the pre-push privacy guard into each repo
  hooks/pre-push    refuses a push where a tracked file is also gitignored
interface/          Phase 3 — ask the vault, get a cited answer
  backend/          FastAPI + Claude Agent SDK; serves the built frontend too
    privacy.py      the model boundary: if git will not sync it, the model does not see it
    propose.py      the only way an agent affects disk — writes a *pending* proposal
  frontend/         React + Vite
tools/              vault utilities that aren't Sigma (PDF → Markdown converter)
```

Everything in `runtime/` is pure stdlib and self-checking except `fleet.py`, which needs the Agent
SDK — and only to *run* a specialist, so `fleet status` and `fleet run --dry-run` work without it.
`sigma` resolves that for you rather than making it a rule to remember.

`doctor.py` exists because the self-checks were green while the system was dead. It runs itself,
on `SessionStart`, and speaks only when something is wrong.

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
