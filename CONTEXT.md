# Sigma — complete project context

*One document that explains the whole system: what it is, what it is made of, every feature that
exists, how the pieces reach each other, and what is deliberately not built yet. Written 2026-08-01.*

**Who this is for.** A human picking the project up cold, and — equally — an AI agent (Claude Code,
another Claude instance, any model with file access) that needs enough context to reason about this
system and extend it without re-deriving it. Read this end to end before changing anything; §3 and
§7 are the parts that will bite you if you skip them.

**Where it lives.** This file exists twice, kept identical: `CONTEXT.md` at the root of the `sigma-os`
code repo, and `03-Projects/sigma-os/sigma-os-reference.md` in the Obsidian vault. The vault copy
adds YAML frontmatter and a Related section; the body is the same. If you change one, change both.

**Its siblings.** `README.md` is the one-page introduction. `SYSTEM.md` is the **state** document —
what is built, what actually executes, what is healthy, what is left — and it was brought current on
the same day as this file; where this one goes function-by-function, that one stays at the level of
*does it run*. The vault's `03-Projects/sigma-os.md` hub holds the dev log, still the best narrative
account of *how* this got here, and `03-Projects/sigma-os/` holds the atomic notes arguing individual
decisions in depth.

---

## Table of contents

1. [What Sigma is](#1-what-sigma-is)
2. [Orientation for an agent](#2-orientation-for-an-agent)
3. [The invariants](#3-the-invariants)
4. [The memory model](#4-the-memory-model)
5. [Repository layout](#5-repository-layout)
6. [The vault — the substrate](#6-the-vault--the-substrate)
7. [Component reference](#7-component-reference)
8. [The HTTP API](#8-the-http-api)
9. [The dashboard UI](#9-the-dashboard-ui)
10. [Runtime wiring](#10-runtime-wiring)
11. [Data formats](#11-data-formats)
12. [The algorithms](#12-the-algorithms)
13. [Feature inventory — everything built](#13-feature-inventory--everything-built)
14. [Tech stack and tooling](#14-tech-stack-and-tooling)
15. [Current state](#15-current-state)
16. [Known gaps and future work](#16-known-gaps-and-future-work)
17. [Failure modes this project has already paid for](#17-failure-modes-this-project-has-already-paid-for)
18. [Glossary](#18-glossary)
19. [Working on this](#19-working-on-this)

---

## 1. What Sigma is

Sigma is a personal agentic operating system whose **memory substrate is an Obsidian vault**. It is
built for one person — Zach, a CS student with an internship, several side projects, coursework, a
club, and a job search — and it runs entirely on one Windows machine.

Agents **perceive** state (frontmatter, checkboxes, dates), **reason**, **act** (draft notes, plans,
reports, dev logs), and **remember** (git history). The vault is co-writable by human and agents
alike, which is the whole trick: there is no database, no vector index, no separate agent memory
store. Markdown files in a git repo are the state.

### The three systems

Kept deliberately separate — it is what makes the storage, repo, and privacy questions answerable.

| | What it is | Where it lives |
|---|---|---|
| **Knowledge Vault** | Zach's facts — coursework, projects, internship, applications. The OS's model of *you*. | `C:\Users\tusha\Documents\Obsidian Vault` (its own private repo, `sigma-vault`) |
| **Agent Memory** | The OS's record of *its own* work — session logs, insights, proposals, reviews. Model of *itself*. | `06-System/` **inside** that vault |
| **OS Application** | The engine: hooks, scheduled jobs, the CLI, the agent code, the web interface. | this repo (`sigma-os`) |

**Two repos, never nested.** A vault inside the code repo is one `git add .` away from publishing
personal notes. The separation is a safety property, not a preference.

### Why a vault works as agent memory

- **Human/machine co-writable.** Both sides read and write the same files, in the same format.
- **The frontmatter contract is a queryable API.** `CLAUDE.md` in the vault defines a YAML schema per
  note type; Dataview dashboards and agents both query against it. Contract drift is therefore the
  system's named failure mode, and a specialist exists to police it.
- **Git is temporal memory and the undo button.** Every change is a commit; every autonomous change
  is *its own* commit, revertible in one click.
- **Wikilinks are a retrieval graph.** The link structure is used for navigation, for the dashboard's
  brain visualisation, and by agents deciding what to read next.

### Memory mapping

Episodic = `01-Daily/` + git log · semantic = notes, study guides, resources · procedural = `CLAUDE.md`
+ skills + templates · working = inbox, unchecked boxes, the priority queues · prospective = 📅 dates
and 🏁 milestones.

### One credential, and what follows from it

**Sigma has no `ANTHROPIC_API_KEY` and no `.env`.** Both paths to a model end at the same Claude Code
CLI riding this machine's subscription login:

- `claude -p` (subprocess) — session logging, reflection, the watchdog's auth probe, retro narration.
- The **Claude Agent SDK**, which *is* Claude Code packaged as a library and spawns that same binary —
  the interface, the fleet, intake, devlog, mapper, scaffold.

Verified on both paths with the key explicitly unset. The consequence shapes everything downstream:

> **The budget is a rate-limit window, not an invoice.**

Nothing is billed per token, so the question is never *what does this cost* but *how many agents can
run before the window is spent*. That is why specialists are **sequenced rather than fanned out**, why
there is no concurrency option to enable later, why model tiering (Haiku vs Sonnet) exists for headroom
rather than for money, and why the SDK's `cost_usd` is treated as notional — useful for ranking which
agent is expensive, billed to no one. It also means anything that cannot reach this machine's login —
the raw `anthropic` SDK, a hosted backend, a cloud runner — is out of scope by construction, which is
why the interface is local-first.

---

## 2. Orientation for an agent

If you are an AI agent about to work on this system, this is the shortest path to being useful.

**Read first, in this order:**

1. The vault's `CLAUDE.md` — the frontmatter contract and standing permissions. It governs every note
   you might write.
2. This file.
3. `03-Projects/sigma-os.md` in the vault — the hub, whose dev log explains why things are the way
   they are.
4. The specific module you are changing. Every module in `runtime/` and `interface/backend/` opens
   with a docstring that explains its design decisions, including the ones that were wrong first.

**The five rules you must not break** (expanded in §3):

1. No model ever gets a filesystem write primitive. Writes happen in script code.
2. Never delete a note. Never tick a checkbox on the human's behalf.
3. Never write outside the vault, except `scaffold.py`, which has its own guards.
4. If git will not sync a path, the model does not see it — unless it is explicitly exempted in
   `privacy.config.json`, and even then git still refuses it.
5. "Configured" and "executes" are different claims. Verify by running the thing.

**Conventions in this codebase:**

- Python is stdlib-only in `runtime/` except `fleet.py`, `intake.py`, `devlog.py`, `mapper.py` and
  `scaffold.py`, which need the Agent SDK. Those import it lazily where possible so `--status` and
  `--dry-run` work without the venv.
- Comments explain *why*, especially where a design looks odd. Several of the oddest lines exist
  because something failed. Do not "clean them up" without reading the comment.
- Prose in this project is plain, specific, and honest about failure. Match it.
- Nothing new gets its own privacy check, its own write path, its own options builder, or its own
  approval gate. There is exactly one of each and everything routes through it.

**How to verify a change:** run it. `sigma doctor` for health, the test suite for regressions, and for
anything user-facing, drive it in the browser. Several bugs in this project's history were invisible
to every self-check and obvious in one live run.

---

## 3. The invariants

These are mechanical, not promised. Each one exists because something failed.

### 3.1 Propose, never apply (the model half)

No agent writes to the vault. The interface and every specialist get **`propose_change`**, an
in-process MCP tool taking structured fields — the *backend* writes the proposal note. The agent holds
no filesystem write primitive at all, so "cannot apply its own changes" is a fact about which tools
exist rather than a rule it might route around.

The alternative — a scoped `Write` fenced by a path check — is the bet that already lost twice here.

Proposals land in `06-System/proposals/` as `status: pending`, in one shape, whether they came from
the weekly reflection, a chat question, a 09:00 specialist, study intake, the dev logger, or the
mapper. One approval path in the whole OS.

### 3.2 Apply is deterministic (the script half)

Since 2026-07-30 the *runner* applies proposals — script code in `applier.py`, never the model. Each
fresh `kind: note` proposal becomes **its own git commit**, recorded in an append-only ledger, with a
one-click revert in the UI. Everything else stays behind the human gate.

`applier.apply_one()` **holds** (refuses, marks, and explains) a proposal when any of these is true:

| Hold | Why |
|---|---|
| `kind` is not `note` | skills, contracts and routines keep the approval gate |
| target is `CLAUDE.md` | the contract is the single held category |
| target is under `06-System/proposals/` or `proposed/` | targets the proposal machinery itself |
| target does not resolve inside the vault | scope escape |
| target is gitignored | sealed or machine-local; never auto-written |
| `git check-ignore` errors | fails closed rather than open |
| content ticks more checkboxes than the existing note | completion is a human signal |
| content is under 40% the size of the note it replaces | a hollow rewrite |
| empty content block | nothing to write |
| the git mutex is busy | left pending, nothing written |

Every one of those is adversarially tested in `tests/test_applier.py`.

### 3.3 Corrections are staged, not overwritten

`reflect.py --apply` creates files that do not exist and appends to the contract. It **never
overwrites** — that guarantee is the only reason a Haiku-drafted `MAP.md` did not once flatten a
hand-built one.

But correcting existing notes is the entire job of two specialists, so a proposal whose target exists
is **staged**: the proposed version is written to `06-System/proposed/<the target's path>`, recorded
in the proposal's `staged:` field, and left `approved`.

```
sigma reflect diff            git diff of each staged change against its target
sigma reflect merge NAME      copy one over its target, mark it applied
```

`merge` is **the one place Sigma overwrites a note**, and it is human-only by construction: one
explicit name, refuses anything not already approved *and* staged, no bulk mode, and no scheduled job
calls it. Editing the staged file before merging is how a change is accepted *partly*. Since Phase 6
the dashboard's Ctrl-click-through diff view (`review.tsx`, `GET /api/proposals/{name}`) does this
without a terminal.

The mirror path matters: a `timeline.proposed.md` sitting beside the real one would carry its `type:`
frontmatter and appear in Dataview as a phantom second note.

### 3.4 Privacy — one declaration, three boundaries

> **If git will not sync it, the model does not see it.**

`.gitignore` in the vault is the single declaration. It covers the ProCertus internship area, the
Interface project folder and its hub, and seven named session logs — **24 files rewritten out of git
history on 2026-07-25** (including the root commit), gitignored, and restored to disk: readable in
Obsidian, never synced.

Three enforcement points read that one declaration:

| Boundary | Enforced by | Behaviour |
|---|---|---|
| **Push** | `runtime/hooks/pre-push`, installed in both repos | refuses a push where any file is both tracked and gitignored, checking the working tree *and* the outgoing commit range |
| **Model** | `interface/backend/privacy.py`, a `PreToolUse` hook on every agent | refuses Read/Grep/Glob on sealed paths, refuses every write tool unconditionally, refuses anything outside the vault |
| **Display** | `panels.py` + `privacy.gitignore_scan` | sealed paths are dropped from panels; exempt paths are *marked* bronze rather than hidden |

The precise invariant the push guard checks is **a file may not be both tracked and gitignored** — one
rule covering every carved-out path, self-maintaining, zero false positives. Grepping a diff for the
client's name was the first attempt and flagged two files that legitimately mention it: Sigma's own
insight about the carve-out, and the proposal that asked for the grep.

**The Option B exemption (2026-07-30).** Zach's internship agreement permits AI tools, so
`privacy.config.json` carries a `model_allow` prefix list: those paths are readable by the model, the
fleet and the panels while staying gitignored and untracked. GitHub never gets them. Because an
exemption list is exactly the second-declaration-that-drifts the original design refused, the drift is
surfaced rather than trusted — the watchdog names the active exemptions in **every** session's context.
The *capture* deny list deliberately did not move: new session logs land in tracked `06-System/`, so
summarising internship sessions would leak them through the side door.

Marking and hiding **fail in opposite directions on purpose**: hiding is a safety measure, so it fails
*on*; marking is a confidentiality *claim* ("you may keep client material here, it cannot leave"), so
it fails *off*. An unanswered `git check-ignore` yields no marks at all. A mark needs two independent
yeses — git refuses it *and* the operator listed it — so a stale `model_allow` entry pointing at a
tracked file cannot invent one.

Enforcement is a **`PreToolUse` hook, not the permission callback**. That distinction was learned
expensively — see §17.

### 3.5 An agent may only call tools it was granted

*(Added 2026-08-01, ahead of the browser lane — the plan is the vault's `browser-plan` note.)*

`agent.build_options()` builds **one list and uses it twice**: as the grant (`tools=`) and as the gate
(`VaultPrivacy(granted_tools=...)`). `privacy._classify()` refuses anything outside it with rule
`unvetted-tool`, so granting a tool necessarily adds it to what the guard vets, and forgetting fails
closed rather than open.

Before this the guard returned "allowed" for any tool it had no rule for — an allowlist of things to
*check* rather than a denylist of things to *permit*. It held only while every tool's argument was a
path. A tool whose argument is a URL (`browser_navigate`) has no `file_path`, so every loop in the
guard would skip it and the call would pass unvetted with the run reporting **zero denials** — which
is exactly what this guard reported the two times it was already found not to be running (§17).

Every refusal is appended to `runtime/audit.jsonl`, and `doctor` reports `unvetted-tool` hits, because
the danger of a fail-closed default is not that it blocks an attacker: it is that it blocks something
legitimate *silently*, leaving a run that looks successful and quietly answered worse.

### 3.6 Observe freely, write additively, never delete

Agents may read anything they are allowed to see. They append and create; they do not rewrite or
remove Zach's words. They never tick his checkboxes — completion is a human signal. They pull before
writing, because obsidian-git syncs the vault underneath them every 15–30 minutes. They fail closed
and quiet: an agent that breaks degrades to doing nothing rather than breaking the session it ran in.

---

## 4. The memory model

Five layers, raw → distilled.

| Layer | What | Where | Written by |
|---|---|---|---|
| **L0** | Raw Claude Code transcripts | `~/.claude/projects/**/*.jsonl` | Claude Code |
| **L1** | Session logs — one Markdown note per session | `06-System/sessions/` | `session_logger.py` |
| **L2** | Knowledge notes — the vault at large | everywhere | Zach, and applied proposals |
| **L3** | Procedural memory — skills + the contract | `~/.claude/skills/`, `<vault>/.claude/skills/`, `CLAUDE.md` | approved proposals |
| **L4** | Insights — durable lessons | `06-System/insights/` | `reflect.py` |

**L0 is treated as opaque.** The JSONL schema is internal to Claude Code and shifts between versions,
so Sigma **summarises it, never parses it** as a contract.

Two later additions sit alongside rather than inside this ladder: **reviews** (`06-System/reviews/`,
one per day, written by `retro.py`) and the **activity ledger** (`runtime/ledger.jsonl`, every write
Sigma or the dashboard made). Reviews are about the human's throughput; the ledger is about the
machine's actions.

**Skill scope is load-bearing.** Claude Code loads a skill only from the root it lives in:
`~/.claude/skills/` fires in every repo everywhere; `<vault>/.claude/skills/` fires only in vault
sessions. A skill about writing code installed as `vault` scope is inert — which is exactly how the
loop's first two skills silently failed. `user` is the default; `vault` is only for procedures that
operate on the vault itself.

---

## 5. Repository layout

```
sigma-os/
├── README.md                     one-page introduction
├── SYSTEM.md                     previous full picture (accurate through 2026-07-31, Phases 0–4)
├── CONTEXT.md                    this file — the current comprehensive reference
├── .gitignore                    secrets, machine-local state, and a refusal to nest the vault
├── .gitattributes
├── sigma                         POSIX front door  → runtime/cli.py
├── sigma.cmd                     Windows front door (must stay CRLF, or cmd.exe eats half a REM)
│
├── runtime/                      hooks, scheduled jobs, and the CLI. Stdlib except where noted.
│   ├── sigma/                    the shared core every tool imports
│   │   ├── __init__.py           settings precedence, frontmatter, kebab, `claude -p`, state files,
│   │   │                         project resolution, UTF-8 output, detached spawn
│   │   ├── gitops.py             the ONE place anything commits to the vault
│   │   ├── ledger.py             the append-only activity ledger — what CHANGED
│   │   ├── audit.py              the append-only attempt log — what was TRIED, incl. refusals
│   │   └── spend.py              the rate-limit window proxy meter
│   ├── cli.py                    the `sigma` command — a dispatcher, not a rewrite
│   ├── session_logger.py         Phase 1 — transcript → session log
│   ├── reflect.py                Phase 2 — logs → insights + proposals; apply / diff / merge
│   ├── doctor.py                 Phase 2.5 — nine health checks, exit code always 0
│   ├── fleet.py                  Phase 4 — runs specialists one at a time under a lock
│   ├── specialists.py            who the specialists are and what each is briefed to do
│   ├── inventory.py              deterministic frontmatter scan, precomputed for the auditor
│   ├── applier.py                deterministic auto-apply — the script half of the autonomy flip
│   ├── todo.py                   the four priority queues and their scoring function
│   ├── retro.py                  the 06:00 retrospective — arithmetic score, narrated by a model
│   ├── intake.py                 study intake — dropped course material → contract-shaped notes
│   ├── devlog.py                 project hub dev-log entries from commits + session logs
│   ├── mapper.py                 a codebase → linked architecture notes
│   ├── scaffold.py               `sigma new` — a project from a one-line description
│   ├── install_hooks.py          copies the pre-push guard into each repo
│   ├── hooks/pre-push            refuses a push where a tracked file is also gitignored
│   ├── *.config.example.json     shipped templates; the real configs are gitignored
│   └── (gitignored at runtime)   *.config.json  *.state.json  *.progress.json
│                                 *.log  *.lock  ledger.jsonl  spend.jsonl  reviews.jsonl
│
├── interface/                    Phase 3 + the dashboard
│   ├── README.md
│   ├── backend/                  FastAPI + Claude Agent SDK; also serves the built frontend
│   │   ├── app.py                the app, /api/ask (SSE), /api/health, static mount
│   │   ├── agent.py              the single guarded Agent SDK options builder
│   │   ├── privacy.py            the model boundary
│   │   ├── propose.py            the in-process MCP `propose_change` tool
│   │   ├── panels.py             every read endpoint the dashboard renders
│   │   ├── commands.py           the whitelisted command runner behind the palette
│   │   ├── writes.py             every mutation endpoint (toggle, queue, capture, revert)
│   │   ├── review.py             proposal read + decide (approve / reject / apply / merge)
│   │   └── requirements.txt
│   └── frontend/                 React 19 + Vite 8 + TypeScript
│       ├── src/App.tsx           the shell: layout, keybindings, polling, SSE wiring
│       ├── src/api.ts            typed fetch helpers + every response type
│       ├── src/panels.tsx        top strip, rail, waiting-on-you, queue digest, projects, foot
│       ├── src/reactor.tsx       the four-arc fleet instrument at the centre
│       ├── src/brain.tsx         the live-firing vault nebula (canvas 2D)
│       ├── src/palette.tsx       Ctrl+K command palette
│       ├── src/chat.tsx          Ctrl+/ chat drawer
│       ├── src/ledger.tsx        Ctrl+J activity ledger with per-row undo
│       ├── src/nosync.tsx        Ctrl+. the no-sync lens
│       ├── src/work.tsx          WK — the four priority queues
│       ├── src/queue-bits.ts     shared chips/score-breakdown/completion for both queue views
│       ├── src/study.tsx         ST — exam mode
│       ├── src/build.tsx         BD — repo awareness
│       ├── src/review.tsx        the proposal diff + decide view
│       ├── src/calendar.tsx      the 14-day calendar strip
│       ├── src/capture.tsx       Ctrl+N quick capture
│       ├── src/theme.ts          the six palettes + the store that writes one onto :root
│       ├── src/theme-view.tsx    Ctrl+, the palette picker (previews live, Esc reverts)
│       ├── src/App.css           the entire visual language, hand-written (~1,900 lines)
│       └── (gitignored)          node_modules/, dist/
│
├── tests/                        stdlib unittest, 19 suites (see §19 for the exact command)
├── tools/                        vault utilities that are NOT Sigma
│   ├── convert_pdfs.py           batch PDF → Markdown (imported by intake.py, not rewritten)
│   └── convert.cmd
└── reference-photos/             the HUD/dashboard visual references the UI was designed from
```

**Two interpreters, resolved for you.** `fleet.py`, `intake.py`, `devlog.py`, `mapper.py`,
`scaffold.py` and the whole interface need the Agent SDK, which lives in `interface/backend/.venv`.
Everything else is pure stdlib. That venv is a superset, so `cli.py` prefers it for every subcommand
and falls back to system Python. `fleet` imports the SDK lazily, so `fleet status` and
`fleet run --dry-run` work without it.

---

## 6. The vault — the substrate

The vault is not "data this program reads". It is half the system, and its structure is a contract the
code depends on. Full detail lives in the vault's own `CLAUDE.md`; this is the shape.

```
00-Inbox/               fast capture, unsorted
  intake/<COURSE>/      study intake's drop folder — the one machine-read part of the inbox
01-Daily/               daily notes, YYYY-MM-DD.md
02-Areas/               ongoing responsibilities, no end date
  Academics/<COURSE>/   lectures/ assignments/ exams/ + a <code>.md course index
  ProCertus/            the internship area — gitignored, model-exempt, never synced
  Clubs/  Career/       Career/Applications/ holds one note per job application
  Personal/
03-Projects/            anything with a deadline; one hub note per project, plus optional
                        <project>/ folders of atomic architecture notes
04-Resources/           reference material, cheat sheets
05-Archive/             finished courses, shipped projects, closed applications
06-System/              the OS's memory of its own work
  sessions/             L1 session logs        insights/   L4 durable lessons
  proposals/            the approval queue     proposed/   staged corrections
  reviews/              daily retrospectives   system.md   the Dataview MOC over all of it
99-Meta/                Templates/, Attachments/
.claude/skills/         vault-scoped procedural memory
CLAUDE.md               the contract
Home.md  MAP.md         the dashboard and the navigation index
```

**The frontmatter contract.** Every note carries YAML frontmatter matching its `type`. The types are:
`daily`, `lecture`, `assignment`, `exam-prep`, `project`, `application`, `club`, `area`, `moc`,
`dashboard`, `resource`, `course-index`, `session-log`, `insight`, `proposal`, `review`. `type` is the
machine key; tags mirror it for humans. Every Dataview dashboard and every agent read depends on this,
which is why **contract drift is the named failure mode** and why a specialist polices it weekly.

**Tasks** are Tasks-plugin checkboxes that can live in any note:
`- [ ] Finish PSet 3 📅 2026-01-20 🔺`. They roll up by query, and since the priority-queue engine they
roll up by score as well. Markdown remains the truth: ticking a task in the dashboard edits the
checkbox line in the note that owns it.

**Code never lives in the vault.** Repositories sit in `C:\Users\tusha\Documents\CS Projects\`; a
project hub note carries a `repo:` field and a `file:///` link. This is what makes `devlog.py`,
`mapper.py`, `scaffold.py` and the Build view possible — the hub is the join between a repo and
everything the vault knows about it.

---

## 7. Component reference

Every module, what it is for, and the functions worth knowing about. Line counts are as of
2026-08-01 and give a sense of weight.

### 7.1 `runtime/sigma/` — the shared core

Extracted 2026-07-25, before the interface became its third consumer, because both existing tools had
grown their own copy of the same helpers.

**`__init__.py`** (308 lines) — imported by everything.

| Function | Purpose |
|---|---|
| `_enable_utf8_output()` | reconfigures stdout/stderr to UTF-8 at import. On Windows, redirected stdout is cp1252 — which is *every* scheduled task — and cp1252 has no `→` or emoji. One arrow in a model summary would kill a scheduled run. A fix in a module only protects files that import it. |
| `load_config(path)` / `setting_reader(cfg)` | settings precedence: environment variable → config file → default |
| `kebab(s, fallback)` | the vault's filename convention, in one place |
| `frontmatter(text)` | parses YAML frontmatter without a YAML dependency; note it skips block lists, which Obsidian rewrites `tags:` into |
| `parse_model_json(out)` | pulls JSON out of a model's answer, tolerating fences and preamble |
| `call_model(prompt, model, timeout)` | the `claude -p` chokepoint. Every non-SDK model call goes through here, which is also where spend is metered. |
| `read_state` / `write_state` | JSON state files with a failure callback rather than an exception |
| `write_note(path, text)` | writes a note preserving LF, so a one-line change is not a whole-file CRLF diff |
| `make_logger(path, prefix)` | the per-tool failure log |
| `spawn_detached(args)` | fire-and-forget worker (used by the SessionStart sweep) |
| `project_hubs(vault)` / `resolve_project(cwd, vault)` | maps a working directory to a project hub by matching `repo:` fields, so a session log says `sigma-os` rather than the folder name |

**`gitops.py`** (258 lines) — **the one place anything commits to the vault.**

- `_Mutex` — a short-held file lock at `runtime/git.lock`, stale after 10 minutes so a crashed writer
  cannot wedge every writer after it.
- `_pull(vault)` — best-effort pull with a 20s timeout, because obsidian-git pulls every 15 minutes
  underneath and a merge commit can otherwise swallow an uncommitted write. It reports failure rather
  than proceeding silently; hanging counts as failure.
- `Writer.commit(rel, message)` — path-scoped staging, then reads the SHA back. If obsidian-git's
  interleaved backup commit absorbed the change, it reports **that** commit rather than pretending.
- `revert(vault, sha)` — `git revert` of exactly one commit; surfaces conflicts and aborts to a clean
  tree rather than forcing.
- `GitBusy` — raised when the mutex is held; callers leave their work pending rather than queueing.

**`ledger.py`** (70 lines) — `record(actor, action, target, sha, title, extra)` appends one JSON line to
`runtime/ledger.jsonl`; `entries(limit)` reads them back. Actions: `create`, `update`, `toggle`,
`revert`, `append`. Append-only by design — a reverted row stays visible, struck through.

**`audit.py`** (new 2026-08-01) — the append-only record of what an agent *tried*, as distinct from
the ledger's record of what changed. `record(kind, actor, tool, detail, rule)` appends to
`runtime/audit.jsonl`; `entries(limit)` and `recent(hours, kind, rule)` read it back — the latter is
what `doctor.check_toolgate` asks. Kinds: `refused` (live), `navigated` and `fetched` (the browser
lane's, declared early so two modules do not guess at the shape). An entry with an unparseable
timestamp is *included* by `recent`, never dropped: this log's failure direction should be one row too
many, never one hidden.

**`spend.py`** (107 lines) — the window proxy. `record_spend()` appends to `spend.jsonl` at every model
call site; `window(hours=5)` summarises the rolling window; `rate_limited_within(minutes)` and
`resume_estimate()` drive the fleet's degrade-then-pause policy and the UI's meter. Deliberately
labelled a proxy: no documented API exposes subscription headroom, so the meter never claims a
percentage it cannot know.

### 7.2 `runtime/cli.py` — the front door (480 lines)

Five phases had produced five scripts with five flag vocabularies (`--status`, `--apply`, `--diff`,
`--all`, `--only` each meaning something different depending on which script you were in) and two of
them needed different Python interpreters, with nothing to say which.

`cli.py` is a **dispatcher, not a rewrite**: every subcommand shells out to the script that already
owns the job, so there is one implementation of each behaviour. It picks the interpreter
(`interpreter(needs_sdk)`), and a group invoked with no verb prints that group's *state* rather than
guessing at intent — `sigma fleet` shows status, it does not run anything.

```
sigma                       what needs your attention right now
sigma doctor                health check (what SessionStart runs)   --json --quiet
sigma fleet status          who ran, when, and what they raised
sigma fleet run             the specialists that are due            --only KEY --all --dry-run
sigma reflect status|run    distil insights + proposals             --all --since --dry-run
sigma reflect apply         execute approved proposals
sigma reflect diff [NAME]   review changes staged against existing notes
sigma reflect merge NAME    apply one staged change to its target
sigma capture status|sweep  session-logging self-check / log what the hook missed   --max N
sigma intake [run]          course material → notes        --course --dry-run --keep --max N
sigma devlog [run]          commits → the project hub's dev log     --project --dry-run --max N
sigma map [run]             a codebase → architecture notes         --project --dry-run --max N
sigma new "<description>"   scaffold a project                      --dry-run
sigma todo                  the four priority queues     --all --json --date --no-persist
sigma review                score yesterday and write it down   --date --dry-run --status
                                                                --install-schedule --at
sigma install [what]        git hooks and scheduled tasks (all | hooks | schedules)
sigma ui                    start the interface                     --port
```

Neither `sigma` nor `sigma.cmd` needs to be on `PATH`; an absolute path works, which is what a
scheduled task uses.

### 7.3 `runtime/session_logger.py` — Phase 1, capture (488 lines)

Turns a finished Claude Code session into one L1 note.

- `load_hook_input()` reads the hook's JSON on stdin; `parse_transcript(path)` walks the L0 JSONL for
  messages, tools used and files touched; `condense(convo, cap=20000)` trims it for the summariser.
- `capture_candidates(sessions_dir)` is the **single source of truth** for "what is unlogged" —
  the sweep, `--status` and the watchdog all call it rather than each computing their own answer.
- `is_machine_session(entrypoint)` filters out Sigma's own `claude -p` calls, which the sweep's first
  run cheerfully logged as if they were work.
- `sweep(a)` logs up to `sweep_max` (default 3) missed sessions; `--detach` spawns it so a session
  start is never blocked.
- The summariser is Haiku, prompted for a factual log; `build_note(fields, body)` renders the
  `session-log` frontmatter and the body sections.
- **Capture is eventually consistent.** `SessionEnd` only fires on a *clean* exit — closing the
  terminal skips it, which cost three days of capture once — so `SessionStart --sweep` is the safety
  net. A session missed today is picked up at the next launch.
- A `deny` list in `session_logger.config.json` means named paths are never summarised to a model at
  all. It stayed empty for a week while seven internship sessions were summarised; it is armed now,
  and deliberately did **not** move when Option B opened the model boundary.

### 7.4 `runtime/reflect.py` — Phase 2, learning (907 lines)

The weekly loop: read the session logs since last time, distil **insights** (L4), and draft
**proposals** for changes to the system.

- `heal_capture_first()` asks `session_logger` whether anything is unlogged and sweeps first, so a week
  is never distilled from a hole in the evidence.
- `call_with_retry()` — transport-only retry (`60s`, `300s`) for the transient class
  (`ENOTFOUND`, `ECONNRESET`, …). One network blip once cost a week of learning.
- `new_sessions()`, `existing_skills()` (scans **both** skill roots so the agent does not re-propose
  what it can no longer see), `existing_insights()`, `git_log(since)` build the prompt.
- `write_insight()` / `write_proposal()` — one note per lesson, atomic: one insight = one claim.
  Proposals are always written `status: pending`; there is no code path that writes `approved`.
- `do_apply()` — executes approved proposals. `safe_target(target, scope)` confines a `user`-scoped
  skill to `~/.claude/skills/` and a `vault`-scoped one to the vault, refusing anything resolving
  outside its root. `append_to_contract()` appends to `CLAUDE.md`'s *Learned conventions* section.
  `stage_change()` handles the existing-target case (§3.3).
- `do_diff()` / `do_merge()` — review and land staged changes.
- `proposal_content(text)` extracts the `<!-- proposal:content -->` block. Its first version used a
  lazy regex and would have applied a daily note **truncated mid-section** the moment a proposal
  embedded a ```dataview fence; it is greedy and bounded now, with a regression test.

### 7.5 `runtime/doctor.py` — Phase 2.5, the watchdog (595 lines)

Runs on **every** `SessionStart` and speaks only when something is wrong. **Exit code is always 0** —
a broken watchdog must not block a session from starting.

Nine checks, in order: `capture`, `reflection`, `schedule`, `review`, `auth`, `privacy`, `toolgate`,
`backup`, `fleet`.

| Check | What it actually verifies |
|---|---|
| capture | is any finished session still unlogged? (a backlog the concurrent sweep is already clearing is not an alert) |
| reflection | did the weekly loop run, and is anything waiting on Zach? |
| schedule | did each scheduled task fire *and succeed*? A task can fire and fail. |
| review | has the 06:00 retrospective run, and is it current? |
| auth | is the login still live? The only honest test is a real model call, but the budget *is* the window — so a good result is cached 12h and only failures re-probe. A failing probe is free, because it errors before a call is spent. A dead network is never reported as an expired login. |
| privacy | is anything gitignored also tracked, is the pre-push guard installed, and which `model_allow` exemptions are active (reported at `info` level, shown every session, never costing the all-clear) |
| backup | has the vault pushed recently? (obsidian-git pushes every 30 minutes; stale after 2h) |
| fleet | per **specialist**: any failing, overdue by more than two cycles, or a run that stopped on a limit |

Two rules it follows: **it is not a fourth source of truth** (facts come from
`session_logger.capture_candidates()`, `reflect.load_state()`, `fleet.load_state()`), and **it does not
cry wolf** (a failed scheduled run stops nagging once re-run by hand). Levels are `ALERT`, `TODO`,
`INFO`, `OK`; `--quiet` (hook mode) prints only what needs attention plus `info`.

### 7.6 `runtime/fleet.py` + `specialists.py` — Phase 4, the specialists (704 + 162 lines)

`fleet.py` takes an **exclusive-create** lock (a check-then-write could race a palette click at
09:00:00 into two concurrent fleets), then runs each due specialist **to completion before starting the
next**. A stale lock (>2h) is taken over rather than obeyed. Each specialist gets a 420s timeout via
`asyncio.wait_for` — declared and caught but never actually *applied* in the first version, which is
its own lesson.

There is deliberately **no concurrency option**: an option is a constraint you have already decided to
break.

| Order | Specialist | Cadence | Model | Effort | Turns | Brief |
|---|---|---|---|---|---|---|
| 10 | *(vacant)* | — | — | — | — | belonged to the **planner**, retired 2026-08-01 |
| 30 | **coach** | weekly | Sonnet | medium | 24 | plan-vs-date drift in course timelines |
| 40 | **auditor** | weekly | Haiku | medium | 40 | frontmatter against the contract |
| 60 | **tracker** | weekly | Haiku | low | 24 | stale entries in the job pipeline |

`Specialist` is a frozen dataclass with an optional `context` callable — a zero-argument function
returning extra brief text computed at run time. It exists for work whose expensive part is *gathering*
rather than *judging*: the auditor's `context` is `inventory.for_auditor()`, which turned ~40 search
turns into zero (see §16).

`is_due()` compares **calendar days**, not a rolling `cadence_days × 24 − 1` hours — the earlier version
meant an afternoon hand-run silently suppressed the next morning's scheduled run, and three consecutive
09:00 runs did nothing while each reported success.

**Window policy.** `_is_rate_limited(result)` keys on the *error channel*, not the model's prose — the
vault contains a whole note about rate limits, and a specialist merely mentioning them would otherwise
have halted the fleet. An SDK error with no text falls back to consulting the model's own words, so an
opaque error cannot sail past the halt. On the first rate limit the sequencer **degrades** Sonnet →
Haiku and continues; if it hits one again while already degraded it **pauses** cleanly, persists a
resume point in `fleet.state.json`, and the remaining specialists stay due for the next invocation.

`write_progress()` writes `fleet.progress.json` at every transition — **including the nothing-due pass**,
which otherwise looks identical to a run that never fired. That file is what the dashboard's reactor
streams.

Specialists have no write path of their own: they propose through `propose_change`, and `applier.py`
lands each fresh note proposal as its own commit.

### 7.7 `runtime/inventory.py` — the precomputed scan (165 lines)

`scan_notes(vault, sealed)` walks every note and reports its frontmatter fields of interest;
`manifest_coverage(vault)` reports which notes each course index does and does not link; `render()`
formats both; `for_auditor()` is the entry point the auditor's `context` calls.

It **reports facts and never judges them** — which `type` is legal lives in `CLAUDE.md`, and a second
copy of that rule in Python is exactly the drift this vault has already paid for once.

### 7.8 `runtime/applier.py` — deterministic auto-apply (178 lines)

`apply_one(prop_path, actor)` and `apply_run(files, actor)`. The hold list is §3.2. On success it
writes the file inside the git mutex (so the guards judge the file as it is *now*, after the pull),
commits it as `sigma(<actor>): <verb> <path> - <title>`, records a ledger row, and stamps the proposal
note as auto-applied with its SHA. It never raises and never deletes.

### 7.9 `runtime/todo.py` — the priority queues (1,053 lines)

Replaced the day-bucketed Today panel on 2026-08-01. Days stopped being the organising unit; there are
four queues — **Courses, ProCertus, Projects, Misc** — each showing a small window of its
highest-scoring *eligible* tasks. Completing one pops it and promotes the next. Deadlines are optional,
where the old panel discarded every task without a 📅 — which is most of the vault's real work.

**Markdown stays the truth.** A task is a checkbox line in a note. The sidecar index
(`runtime/todo.state.json`) holds only what a checkbox line *cannot* say: when a task first appeared,
whether it is snoozed, pinned or archived, and a section override. Everything Obsidian can express —
title, due date, priority — is re-derived on every scan and never cached, so editing a note in
Obsidian can never disagree with the dashboard.

Key functions: `scan(vault, split)`, `reconcile(index, found, today)`, `parts_of(task, today)` (the
score, broken into its named terms so the UI can answer "why is this here?"), `score()`, `sort_key()`,
`section_of(rel)`, `infer_section(text, vault)`, `compose()`, `splice()`, `replace_line()`,
`unsplice()`, `set_meta()`, `build()`, `reword_prompt()` / `parse_reword()` (the AI reword path),
`_chain()` (chain files — `timeline.md` — where task *n+1* is genuinely blocked by task *n*).

Scoring is §12.1. The file is not called `queue.py` because `runtime/` is inserted at the *front* of
`sys.path` by the backend, so a module named `queue` would shadow the stdlib `queue` for uvicorn's own
dependencies.

### 7.10 `runtime/retro.py` — the 06:00 retrospective (482 lines)

Replaced the 09:00 planner. The queues maintain themselves, so there is nothing left to rebuild; what
is worth doing each morning is looking at *yesterday* and saying what actually moved.

**The score is arithmetic and no model ever assigns it.** `day_facts(date)` gathers everything
deterministic; `components(facts, past)` computes throughput, adherence and momentum; `score_of(parts)`
weights and renormalises them; `narrative(facts)` asks Haiku for one or two sentences *given the
finished numbers*, and the code never reads a number back out of the answer. Details in §12.2.

Two outputs, answering different questions: one note per day in `06-System/reviews/` that a human
reads, and one row in `runtime/reviews.jsonl` that the next review's median and the dashboard strip
read. It reports; it never completes anything.

Not named `review.py` — `interface/backend/review.py` already owns that name and `runtime/` is at the
front of `sys.path`, so `app.py`'s `import review` would have silently resolved to this instead of the
proposal API.

### 7.11 `runtime/intake.py` — study intake (572 lines)

Drop a `.pdf`, `.pptx`, `.md` or `.txt` into `00-Inbox/intake/<COURSE>/`, run `sigma intake run`, and
get back notes filed to that course, shaped by the contract.

- **A drop folder rather than a path argument**, because the palette's security property is that a verb
  is a dictionary key with nothing user-supplied in it. A `--source` argument would have been its first
  exception.
- **The course comes from the folder name** and is validated against real folders under
  `02-Areas/Academics/`. An unmatched subfolder is reported and skipped, never guessed at — the
  contract says never invent a location.
- `extract(path)` dispatches by suffix. PDFs go through `tools/convert_pdfs.py`, imported rather than
  reimplemented. `extract_pptx(path)` uses **python-pptx** — deliberately not `markitdown[pptx]`, which
  drags in an ML content-type sniffer (18 packages against 4) to identify a file type already known
  from its extension. Slides keep their structure: slide number, title, bullets, and speaker notes,
  which are often the only place a deck explains itself. Master-slide furniture (the repeated copyright
  line and page number) is stripped — it was ~30% of the text in a sample deck.
- `course_context(course)` gives the model the course's existing notes so it links rather than
  duplicates. `build_brief()` assembles the conversation; a 60,000-character text budget bounds it.
- **A declined document is not a failure.** The brief tells the model that material too thin to work
  with should produce nothing, and the code used to exit 1 for exactly that — which the palette rendered
  as *✗ failed*.
- Sources are cleared only once a note actually **lands**, not when the model finishes.
- Writes go through `propose_change` → `applier.py`, one revertible commit per note.

### 7.12 `runtime/devlog.py` — dev-log entries (873 lines)

Writes a project's recent work into its hub note's `## Dev log` section.

- **No arguments.** The input is discovered: hub notes that declare a `repo:`, and the commits in those
  repos that no dev-log entry covers yet.
- **The hub note is the state.** There is no `devlog.state.json` — each entry ends with
  `_(through `<sha>`)_`, so the note itself says where the next run should start. That is the thesis of
  this OS restated: the vault is the memory substrate, so the bookkeeping survives a wiped machine and
  is legible to a human.
- **The model writes the entry; script code writes the note.** The obvious design — have the model
  return the whole updated file — is wrong twice: a dropped section would silently destroy Zach's
  writing, and the whole note has to go *in* to come back out, which put the 42,000-character `sigma-os`
  hub past Windows' 32,767-character command line. So the model returns one paragraph, `splice()`
  inserts it between two known offsets, and `_vet()` checks the composed note: every H2 heading, every
  existing entry and the `repo:` line must survive, the note must not shrink, and the entry must carry
  its SHA. A splice cannot retitle a heading or tick a checkbox, because it only ever inserts.
- `session_context(project, guard)` pulls the relevant session logs so the entry reflects *why*, not
  just what changed.

### 7.13 `runtime/mapper.py` — codebase → architecture notes (598 lines)

Fills in a format the contract was already waiting for: a `03-Projects/<project>/` folder of atomic
context notes, one per concept, cross-linked and indexed from an *Architecture map* section in the hub.

- **Script code reads the repo, not the model.** `VaultPrivacy.verdict` refuses every path outside the
  vault, and that boundary is not one a convenience feature gets to widen. `survey(repo)` builds the
  material — file tree (capped at 400), README, manifests, function/class signatures from up to 24
  files, and a `graphify-out/` knowledge-graph report if one exists (its communities and god nodes are
  a better answer to "what deserves a note" than file-tree guessing) — and hands it over as *material*.
- **The survey rides the conversation, not the command line**, because Windows caps a command line at
  32,767 characters and a codebase survey does not fit.
- **`link_hub()` writes the Architecture map from the notes that actually landed**, not from the model,
  which would be claiming links it cannot verify.

### 7.14 `runtime/scaffold.py` — `sigma new` (494 lines)

A new project from one line: a git-initialised folder in the code root, a stack-appropriate
`.gitignore`, a README, and a hub note in `03-Projects/` written with the `## Dev log` section
`devlog.py` splices into — so `sigma devlog` works on it from the first commit.

- **Not a framework generator.** It does not run `npm create vite`; those tools are better at their own
  job and change under you.
- **The model chooses from a list; script code writes the files.** The model picks a stack *name*, that
  name is a key into a `STACKS` table, and the contents come from this file. A hallucinated `.gitignore`
  that omits `.env` is a credential leak with a plausible explanation.
- It is the only thing in Sigma that writes outside the vault, so the guards are about that: the name
  must be a plain kebab slug matching `^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$`, the target must resolve
  inside the code root, and the directory must not already exist with anything in it. No remote is
  created and nothing is pushed.

### 7.15 `runtime/agenda.py` — the calendar resolver (P1–P2 of the agenda subsystem)

**One resolver, and disagreement is visible rather than silent.** Four kinds of thing land on the
calendar — a dated task, a dated note, an event row, a recurrence rule — and they are merged here,
once, so nothing else has to hold an opinion about what is due. Every occurrence carries where it came
from: a path, and a line, block ID or rule ID. When two sources disagree about the same subject (a
task's 📅 against its own note's `date:`) **both** are emitted and both are flagged; a resolver that
silently picks a winner is one you cannot audit.

Key functions: `parse_event(line)` / `compose_event(...)` (the canonical serialiser, so the round-trip
property is testable before a writer exists) · `parse_rule(line)` / `expand(rule, frm, to)` — one
swappable pair, a deliberately small grammar (weekly by weekday, a start, an until, named exceptions),
never RRULE · `collect(vault, split)` — the range-independent scan, which is what the TTL caches ·
`collect_cached()` / `invalidate()` · `resolve(vault, frm, to)` — the merge · `due_tasks()` — every
open dated task, unbounded by range, which is what `/api/tasks` serves · `committed_hours()`.

Three decisions worth knowing:

- **The block ID is optional on read.** A hand-typed line will not have one, and a line that does not
  parse is a line that has silently left your calendar. Absent, identity falls back to a hash of path +
  date + title — what `todo.task_id` already does for a checkbox, which has no ID of its own either.
  The ID is stripped off the end *before* the body is matched; as a trailing optional group it is
  ambiguous against a lazy title, and the engine happily reads `Dentist ^sg-evt-…` as a longer title.
- **`collect()` returns `problems`.** A line that was meant to be an event and is not is reported with
  its path and line rather than dropped. This subsystem's worst failure has no symptom — the calendar
  looks fine, it just quietly has less in it.
- **A multi-day event emits one occurrence per covered day**, sharing a `span`, so a range query still
  sees an event that began before the window opened.

`todo.scan(vault, split, tops)` grew its third argument for this: the queue and the calendar genuinely
disagree about `01-Daily/`. A daily note's `date:` is journal — seven of them would bury every real
event — but a dated *checkbox* inside one is work, and `/api/tasks` has always shown it. One scanner,
two callers, one argument, rather than a second copy of the checkbox grammar.

**The write path (P5)** lives in `interface/backend/writes.py`, not here — this module stays the one
that reads and the one that owns the grammar. What it contributes is `compose_event`, the single
serialiser, and `MONTH_NOTE`, the note a write creates when a month has none. The endpoint composes
through `compose_event` and then **parses its own output back** before committing, so "never write
something the scanner cannot read" is enforced by construction rather than by discipline: the only
definition of a well-formed line that matters is this module's parser, so the parser is what is asked.

Not named `calendar.py`: `runtime/` is at the front of `sys.path`, so it would shadow the stdlib
`calendar` for uvicorn's dependency tree. Third time — after `todo.py`/`queue.py` and
`retro.py`/`review.py` — and the first one that cost nothing.

### 7.16 `interface/backend/` — the API

**`app.py`** (269 lines) — the FastAPI app. `POST /api/ask` streams an answer over SSE with a
collapsible trail of every tool call; `GET /api/health` runs `doctor.collect()`; the built frontend is
mounted at `/`, so one process on `127.0.0.1:8787` serves both. CORS matches any loopback origin
(Vite's dev port walks upward from 5173, so a hardcoded allowlist breaks). `warnings.simplefilter
("always")` is set so the SDK's `CanUseToolShadowedWarning` — the difference between a privacy guard
and the appearance of one — cannot pass unnoticed.

**`agent.py`** (142 lines) — `build_options()`, **the single guarded Agent SDK construction both the
chat and every specialist use.** A specialist that built its own options would be one edit away from a
model path with no privacy guard on it. It sets:

- `tools = ["Read", "Glob", "Grep"] + (["mcp__sigma__propose_change"] if allow_proposals else [])`
- `allowed_tools = []` — deliberately empty. `allowed_tools` means "callable *without being prompted*",
  and since `can_use_tool` **is** the prompt, listing a tool there auto-approves it.
- `hooks = {"PreToolUse": [privacy.pre_tool_hook]}` — the actual guarantee; `can_use_tool` is a second
  layer that only covers calls which would otherwise prompt.
- `setting_sources = []` — loads no filesystem settings, or it would drag in `~/.claude/settings.json`
  and fire Sigma's own SessionStart hooks on every question the interface is asked.
- `system_prompt` = the `claude_code` preset plus an ORIENTATION that tells the agent to ground every
  claim in a note it read, cite by wikilink, start from `CLAUDE.md`, say when the vault is silent, and
  **never claim it made a change** — it cannot.
- `allow_proposals` toggles the proposal tool **only**. `VaultPrivacy` is constructed
  `allow_writes=False` unconditionally: "may propose" and "may edit a note" must never be one switch.

**`privacy.py`** (249 lines) — the model boundary. `VaultPrivacy.pre_tool_hook` vets every tool call's
path arguments; `gitignore_scan(vault, rels)` returns both halves of the sealed/exempt split in one
`git check-ignore -z` pass (NUL-separated, because text-mode stdin once turned `\n` into `\r\n` and the
batch call silently matched nothing); `is_model_allowed(rel)` is the single source for the Option B
exemption. It refuses NTFS `::$DATA` stream suffixes — a verified bypass that dodged `git check-ignore`
while Windows happily opened the file — and treats a `git` exit code of 128 as *refused*, not *allowed*.

**`propose.py`** (165 lines) — the in-process MCP server exposing `propose_change`. No subprocess, no
port. It validates `kind`/`scope` against the contract's enums, checks proposed contract headings
against the headings that already exist, and calls `reflect.write_proposal`, so a proposal raised in
chat is byte-identical in shape to one raised by the weekly loop.

**`panels.py`** (891 lines) — every read endpoint (§8). Also holds the **wikilink resolver** built for
the brain: case-insensitive, full-path and same-folder-first basename resolution, escaped-pipe aliases,
and fenced code blocks and code spans skipped entirely — because a backticked `` `[[wikilink]]` `` is
the contract's own notation for a *non*-link. Results are cached per endpoint with a TTL (the graph for
300s).

**`commands.py`** (340 lines) — the palette's runner. `VERBS` is a **fixed dictionary**, each value a
pre-built argv; the verb is a dictionary key and nothing user-supplied ever reaches a command line.
One job at a time (409 with the name of what is running), per-verb timeouts, output streamed over SSE,
process-tree kill on cancel, and a shutdown hook. `_window_hold()` greys model verbs after a rate limit
and during the 08:40–09:00 fleet reservation — at POST, not just in the listing.

**`writes.py`** (553 lines) — every mutation. Toggling a task is line-verified *after* the pull, CRLF
preserving, 409 if the note moved, and recorded in the ledger with actor `zach` — the human clicked.
Queue add/edit/reword/meta, quick capture, activity read, and revert live here too.

**`review.py`** (230 lines) — read one proposal with its diff (`GET /api/proposals/{name}`) and decide
(`POST /api/proposals/{name}/decide` with `approve` | `reject` | `apply` | `merge`). The buttons do not
hide what the system will actually do: a change to an existing note *stages* rather than applies, and
the header says so before you press anything.

### 7.17 `tools/` — not Sigma

`convert_pdfs.py` (147 lines) is a batch PDF→Markdown converter that predates the OS and lives here so
it is versioned and backed up. `intake.py` imports it. It is explicitly not part of the system's
architecture.

---

## 8. The HTTP API

One process, `127.0.0.1:8787`, unauthenticated **on purpose**: it binds to loopback, reads a vault on
this disk, and inherits a *machine* login — so the machine is the natural boundary. Do not expose it.

### Reads

| Method | Route | Returns |
|---|---|---|
| `GET` | `/api/health` | the watchdog's findings + the vault path (runs `doctor.collect()`) |
| `GET` | `/api/fleet` | per specialist: cadence, model, last ok/run/result, proposals raised, due |
| `GET` | `/api/fleet/progress` | **SSE** — the live fleet state, replayed on connect |
| `GET` | `/api/tasks` | every dated task in the vault + the current study-block position |
| `GET` | `/api/agenda?from=&to=` | the calendar: every occurrence in a window, with provenance |
| `GET` | `/api/queue` | the four priority queues, windowed, with score breakdowns |
| `GET` | `/api/review` | the latest retrospective row |
| `GET` | `/api/proposals` | pending / approved / staged proposals |
| `GET` | `/api/proposals/{name}` | one proposal, its content block, and a diff against its target |
| `GET` | `/api/projects` | project hubs + live repo state (branch, dirty count, last commit) |
| `GET` | `/api/graph` | the vault link graph — nodes with bucket colours, edges, sealed excluded |
| `GET` | `/api/nosync` | every file that never leaves this machine, and the total |
| `GET` | `/api/study` | exam mode: what an exam covers, and which sources no note embeds |
| `GET` | `/api/repos` | repo awareness: repos with no hub, hubs stale against their code, idle repos |
| `GET` | `/api/window` | the rate-limit window proxy — spend rows, last limit, resume estimate |
| `GET` | `/api/activity` | the ledger, most recent first |
| `GET` | `/api/commands` | the server's own verb whitelist, with `model`/`writes` badges |
| `GET` | `/api/commands/events` | **SSE** — the running job's output lines |

### Writes

| Method | Route | Does |
|---|---|---|
| `POST` | `/api/ask` | **SSE** — ask the vault a question; streams tokens and tool events |
| `POST` | `/api/tasks/toggle` | tick/untick a checkbox in the note that owns it → one commit + ledger row |
| `POST` | `/api/queue/add` | quick-add a task into the right note under the right heading |
| `POST` | `/api/queue/edit` | edit a task's text in place |
| `POST` | `/api/queue/reword` | ask a model to reword/enrich a task, after it is already filed |
| `POST` | `/api/queue/meta` | snooze / pin / archive / move — index-only, never destructive |
| `POST` | `/api/agenda/add` | one typed event becomes a line in a month note |
| `POST` | `/api/agenda/edit` | retitle, retime, reschedule or cancel one event line |
| `POST` | `/api/agenda/except` | skip or move ONE occurrence of a recurrence rule (P6) |
| `POST` | `/api/capture` | quick capture → one note in `00-Inbox`, as its own revertible commit |
| `POST` | `/api/activity/revert` | `git revert` of exactly one ledger commit |
| `POST` | `/api/proposals/{name}/decide` | approve · reject · apply · merge |
| `POST` | `/api/commands/{verb}` | run one whitelisted verb (409 if busy, 404 if not in the list) |

**Streaming is SSE, not websockets**: the traffic is one-way, SSE reconnects on its own, and it is
plain HTTP you can debug with `curl`.

---

## 9. The dashboard UI

A dark instrument, not a dark-mode web app. Designed from HUD references in `reference-photos/`.

**Visual language.** Void `#05070A` with a 4% circuit texture · primary cyan `#22D3EE` · glow
`#00E5FF` at 40% · held/warning amber `#FFB020` · fault `#FF4D4D` · **no-sync bronze `#C77D2E`** ·
muted `#3A4A55`. Monospace for every number and path; condensed letter-spaced uppercase for labels;
numbers are the hero. Panels are **bordered, not filled** — 1px hairlines, notched corners, depth from
glow rather than drop shadows. Motion is instrument-like: a 4s breathing pulse at idle, linear sweeps
when working, 200ms cross-fades. **If it moves, something happened.** Status is never colour alone —
every state pairs a glyph or word with its colour.

**Layout.** Top strip (health glyph · window meter · study block · clock) · left rail · centre stage ·
right column (waiting-on-you, queue digest) · lower row (**today rail**, projects) · foot (clickable
keybind hints + the live activity dock).

**The today rail** (`agenda-rail.tsx`, agenda P3) replaced the 14-day calendar strip on 2026-08-03;
`calendar.tsx` is gone rather than orphaned. It is not a calendar — an instrument strip: `NOW` with the
clock, at most the next three commitments with a countdown on each, and on the right one number and a
seven-cell bar whose height is committed hours, so a heavy week *looks* heavy. Kinds are separated by
glyph as well as hue (`◆` event · `▣` rule · `▦` dated note · `☐` task), because status is never colour
alone. A sealed-but-exempt occurrence is marked bronze `⊘`, never hidden; a disagreement between two
sources is marked amber `⚠` on **both** sides with the reason in its title. A row that failed to parse
is reported in the header (`⚠ n unreadable`) with its file, line and text — an event that silently
stopped existing is this subsystem's worst failure, because nothing about the calendar looks wrong.

**The number is free hours *left*** — the waking window (08:00–22:00, one constant in that file) from
now to its end, minus what is still ahead in it. A number that counts down through the day is true when
you read it, where a whole-day figure still claims nine free hours at 21:00. **It feeds nothing until
P7**, deliberately: three phases of checking it against a real day before the queue may believe it.

Clicking anything opens its note in Obsidian.

**The full view** (`agenda.tsx`, agenda P4) opens on `Ctrl+'` or rail slot **CA**, and peels *after*
work — it is normally entered from the work view or the rail, so Esc unwinds in the order you arrived.
Read-only; writing is P5. Three modes, because they answer different questions: **Week** is the
workhorse (an hour grid, with an all-day gutter for everything dated but untimed, which is every task),
**Month** is density and horizon, **Agenda** is the 14-day list the old strip was reaching for.

The hour grid **widens rather than clips** — a 06:30 event is not something a calendar may hide because
its default window starts at 08:00. Month cells draw three items and count the rest; what is not drawn
is never silently dropped. Filtering by section reuses `todo.section_of` through the resolver rather
than growing a second answer to whose work something is.

**`[?]` on any occurrence answers *why is this here***, opening a provenance strip at the foot of the
view: the file, the line, and which key or rule put it on that day (`frontmatter due:`,
`rule sg-rule-cse311`, a block ID). It is the queue's score breakdown applied to dates, and it sits in
one fixed place rather than in a popover so there is nothing to position and one place to look.

**Writing (agenda P5).** Quick-add is a form row under the header; a drag between days reschedules;
cancel lives on the provenance strip, because that is where you can see exactly which line you are
about to change. A move renders **instantly and unsettled** — dimmed, with a `⟳` — and snaps back
carrying the server's own word if a hold fires. Repeated moves of one event **coalesce** into a single
commit after a settle delay: a replanning session is a handful of ledger rows, not thirty.

**Only event rows move from here.** A task's date belongs to `/api/queue/edit`, which already rewrites
task lines with its own holds and ledger row — a second way to move a task's date would be the second
write path this subsystem is not allowed to grow. Dated notes (their date is frontmatter), rule
occurrences (P6) and multi-day spans (dragging one of its days would rewrite it as single-day and lose
the other end) are refused **with a named reason** rather than being quietly undraggable.

**The centre stage** is one scene rather than a panel with a picture in it: the brain fills the cell,
the **reactor** sits at its heart in a pool of darkened sky, and the vault stat chips ride the bottom
edge.

**The reactor** is both the agent visual and the status bar, which is why there is no separate bar.
Arcs around a machined core, one per specialist, with five states: *idle* (dim, 4s breathing),
*running* (that arc sweeps, elapsed counts up), *held* (amber — something needs you), *fault* (red,
and the health glyph turns with it), *degraded* (hollow/dashed while running on Haiku, so a quality
drop is visible rather than buried in a log). Fed by SSE from a file poll **deliberately uncoupled from
the fleet process** — the case that matters is a run the server did not launch.

**The brain** is a live-firing nebula on canvas. Every note is a point of light; position comes from a
seeded 3D force layout run **once** and frozen — drift and parallax are camera motion, never
re-simulation. Colour is the note's bucket; in **VOID** those are the exact colours from
`.obsidian/graph.json`, so the default palette and Obsidian's graph are one picture of one vault, and
the other five re-tune the same nine groups for their own ground. Brightness is inbound links, so hubs read
as bright stars. **The firing is the part that has to be true**: each `Read` an agent makes lights its
node and pulses the edges it traversed, so you watch where an answer came from. Layout is aesthetic;
firing is factual. It degrades to a static sky if the frame budget slips, and starts there under
`prefers-reduced-motion`.

**Keybindings** (Windows, so Ctrl rather than ⌘). Every one has a clickable twin in the foot, because
Chrome intermittently eats Ctrl+K and Ctrl+G at the browser level.

| Key | Opens |
|---|---|
| `Ctrl+K` | command palette — fuzzy search over the server's verb whitelist |
| `Ctrl+/` | chat drawer |
| `Ctrl+G` | expand the brain to the whole shell ("dive") |
| `Ctrl+J` | the activity ledger, with per-row undo |
| `Ctrl+.` | the no-sync lens |
| `Ctrl+N` | quick capture (Ctrl+Enter submits) |
| `Ctrl+;` | the work view — the four queues, and quick-add |
| `Ctrl+'` | the full agenda — week, month, and the 14-day list |
| `Ctrl+,` | the palette picker — six colour schemes, previewed live |
| `Esc` | peels one layer: capture → review → work → **agenda** → study → build → palette → no-sync → ledger → chat → dive → active brain filter |

The picker is the one overlay **absent from that Esc ladder**, and deliberately: Esc there has to
*revert* the live preview before it closes, so `theme-view.tsx` handles its own in the capture phase
and stops the event before the ladder sees it.

**The palettes** (`theme.ts`, `Ctrl+,`). §2 specified one deliberate mode. What that bought was
coherence and what it cost was that every hue lived inline in `App.css` — 64 literals, 35 of them the
same cyan at a different alpha, which is *why* a second palette was impossible rather than merely
undesirable. The rule that replaced it is stricter, not looser: **no rule may name a colour, and a
theme is a value swap rather than a stylesheet fork.** There is no `.theme-ember .panel` anywhere and
there must never be one — the moment a rule knows which theme is active, the other five stop being
maintained. Composited colours take channel triples (`rgb(var(--accent-rgb) / 0.28)`); `theme.ts`
derives every `-rgb` token from its hex, so a colour and its channels cannot drift apart.

| | |
|---|---|
| **VOID** | the original — cyan structure on near-black |
| **EMBER** | tungsten — red-orange on charred black |
| **VERDANT** | phosphor — the green terminal |
| **SYNAPSE** | violet — the brain's nebula colours, brought out to the shell |
| **MERIDIAN** | daylight — a paper dashboard around a dark instrument window |
| **GRAPHITE** | neutral — grey structure, so only status and the graph carry hue |

Three constraints survive the swap, and every palette is held to them. **Bronze means one thing**
("never leaves this machine"), so each theme keeps it in the copper family *and* clear of its own
accent — EMBER is the hard case, which is why its accent is red-orange and its bronze olive.
**Status is never colour alone**, so amber/red/green may move for contrast without changing meaning.
**The graph's nine bucket colours are data**, so they stay a full categorical wheel even in GRAPHITE;
a monochrome brain would look consistent and say nothing.

MERIDIAN is the one that proves the tokens are real, because it cannot simply invert: the brain's
canvas composites with `lighter` and needs a dark ground to read at all. So the **sky is its own
palette** — `.center` remaps the base tokens to `--sky-*` in one block, and every rule inside the
stage re-tokenises for free. In the five dark themes those values are identical to the base set, so
the remap is a no-op that costs nothing.

**Rail slots.** `OV` overview · `BR` brain · `AG` agents *(not built)* · `WK` work · `ST` study ·
`BD` build · `CR` career *(not built)* · `SY` system *(not built)* · `⊘ SEAL` the no-sync lens with a
file count. Dead slots are rendered disabled rather than hidden — **disabled beats hidden**, the same
doctrine the palette applies to its four disabled verbs.

**The command palette** renders from `GET /api/commands`, the server's own whitelist, so the UI cannot
drift ahead of what is invokable. Verbs that spend window are badged *model*; verbs that change the
vault are badged *writes*. **21 verbs enabled**, **4 deliberately disabled with reasons shown**:
`new` (its input is a sentence, and a palette verb is a fixed argv), `reflect-merge` (moved, not
missing — merge from a proposal's diff view, which can name one file), `install` (system configuration
stays in the terminal), and `ui` (you are already in it).

**The ledger** (`Ctrl+J`) is the morning view: one row per commit — glyph, time, actor, target,
`[diff]`, `[undo]`. Undo is `git revert` of exactly that commit, surfaced as a button rather than a
bespoke rollback with its own bugs. A reverted row stays visible, struck through, undo disabled: the
ledger is a record, not a todo list.

**The no-sync lens** (`Ctrl+.`) lists every file that exists on one disk only, with the total. Nothing
exclusive lives there — every file it names also appears in Today, Projects or the brain wearing the
same bronze `⊘` mark. What the view adds is **the number**, which no inline marker can show and which
is exactly the list the one-disk backup problem needs.

**Expanding a queue** (the chevron on a card) opens the rest of it *inside the card* — what is behind
the window is the same queue, and moving it to a drawer would make it a different list. One card is
open at a time, and the open one **spans the grid** and becomes a fixed-height pane whose list scrolls
inside it. Before that it grew its own grid row without bound: an expanded COURSES pushed the other
three queues a screen and a half down, and two cards open at once compounded it. Course codes and
block titles are sticky on two levels, each block in its own stick region.

Three things there are load-bearing and easy to undo by accident. The open card's height must be
**definite**, not a `max-height` — `.work-grid` sizes an auto row from its item's content
contribution and a scroll container contributes almost nothing, so a capped card had its list render
~200px past its own box and paint over the cards below. `.q-card` must re-declare `min-height: auto`
against `.panel`'s `min-height: 0`, or the collapsed cards squash to 16px beside a tall one. And
`.q-more` must never gain `container-type`/`contain`/`transform`: each makes it a containing block for
`position: fixed`, and the row menu positions itself in viewport coordinates.

**Every row in the expanded list can be ticked**, including mid-chain ones, along with the note link
and the ⋯ menu. `POST /api/tasks/toggle` carries `(file, line, raw)` and never cared whether a task
was at the front of anything, so this needed no backend change. Out-of-sequence rows are *marked, not
blocked* — the row already reads "waiting on the one above" and the tick flashes amber rather than
accent — because markdown is the truth and `_chain` re-derives the frontier as the earliest still-open
task on every build, so completing Day 9 early moves nothing and opens no second frontier. A chain's
blocks ahead were previously a one-line summary; `show all` opens them into real rows, because a task
with no DOM node cannot be ticked and that was ~95 of this vault's 100 blocked course tasks.

**Other views.** `work.tsx` (the four queues, with score breakdowns, snooze/pin/archive/move, quick-add
and AI reword) · `study.tsx` (exam mode: what an exam covers and which sources no note embeds — the
"what did you get wrong last time" third is **deliberately absent and says so**, because nothing in the
vault records a wrong answer) · `build.tsx` (repos with no hub, hubs stale against their code, idle
repos — `team20` sat untouched for 56 days and appeared nowhere in the vault) · `calendar.tsx` (14 days,
from `/api/tasks`, because adding a second source would mean two things that could disagree about what
is due) · `capture.tsx` · `review.tsx` · `chat.tsx` (a React-nodes-only Markdown subset — no
`dangerouslySetInnerHTML` anywhere, so a vault note containing a stray `<script>` renders as text).

---

## 10. Runtime wiring

**Wiring lives outside the repo and points back into it.**

### Claude Code hooks — `~/.claude/settings.json`

| Event | Runs | Purpose |
|---|---|---|
| `SessionEnd` | `session_logger.py` | log the session that just ended |
| `SessionStart` | `session_logger.py --sweep --detach` | catch sessions the above missed |
| `SessionStart` | `doctor.py --quiet` | put Sigma's health into the session's context |

> **Quote the interpreter path in every hook.** Claude Code runs hooks through a POSIX shell, where an
> unquoted `C:\Python314\python.exe` silently becomes `C:Python314python.exe` (`\P` collapses to `P`)
> and the hook dies before Python starts. That cost three days of capture once.

### Windows scheduled tasks

| Task | When | Runs |
|---|---|---|
| `SigmaOS-WeeklyReflection` | Sundays 09:00 | `reflect.py` |
| `SigmaOS-DailyFleet` | daily 09:00 | `fleet.py` |
| `SigmaOS-DailyReview` | daily 06:00 | `retro.py` |

All three carry `RestartCount 3` (one network blip once cost a week of learning) and the fleet carries
`-StartWhenAvailable`, so a machine asleep at 09:00 runs late rather than skipping the day.
`sigma install schedules` creates them.

### Git hooks

`pre-push` in **both** repos, installed by `sigma install hooks`. The watchdog also re-checks the same
invariant every session, because obsidian-git auto-pushes unattended and may not run hooks at all.

### Config, state, and logs — all gitignored

Not because they hold secrets, but because they hold *this machine's* operating state — including the
privacy denylist, which names the very client it exists to hide. The repo ships `.example` files.

| File | Holds |
|---|---|
| `session_logger.config.json` | vault path, model, thresholds, the capture **deny** list |
| `reflect.config.json` | vault path, model, caps |
| `privacy.config.json` | the **`model_allow`** exemption prefixes |
| `*.state.json` | doctor auth cache · reflect's covered logs · fleet's last runs and pause · todo's index |
| `fleet.progress.json` | the live reactor feed |
| `ledger.jsonl` | every write, append-only |
| `spend.jsonl` | every model call, for the window proxy |
| `reviews.jsonl` | one row per scored day |
| `*.log` | per-tool failure logs |
| `git.lock`, `fleet.lock` | mutexes |

---

## 11. Data formats

### Proposal (`06-System/proposals/YYYY-MM-DD-<kebab-title>.md`)

```yaml
type: proposal
date: 2026-08-11
status: pending          # pending | approved | rejected | applied
kind: skill              # skill | contract | note | routine
target: .claude/skills/exam-cram/SKILL.md    # path relative to the scope root
scope: user              # skill only — user | vault
risk: low
source_insight: "..."
staged:                  # set when the target already existed
applied:
tags: [proposal]
```

The body's `<!-- proposal:content -->` fenced block is **literally** what gets written, so editing that
block edits the change.

### Session log (`06-System/sessions/YYYY-MM-DD-HHMM-<slug>.md`)

```yaml
type: session-log
date: 2026-08-04
session_id:                    # Claude Code session UUID
project: obsidian-vault        # resolved against project hubs' repo: fields
task:                          # one-line goal
tools: [Edit, Bash, Write]
files_touched: []
outcome: success               # success | partial | abandoned
tags: [session-log]
```

Body: *What I asked · What Claude did · Key decisions · Reusable next time · Links to touched notes.*

### Insight (`06-System/insights/<kebab-lesson>.md`)

```yaml
type: insight
date: 2026-08-11
covers_from: 2026-08-04
covers_to: 2026-08-11
sessions: 6
confidence: medium       # low | medium | high
status: active           # active | superseded
tags: [insight]
```

Atomic: one insight = one claim. Superseded rather than rewritten.

### Review (`06-System/reviews/YYYY-MM-DD.md`)

```yaml
type: review
date: 2026-08-01   # the day being reviewed, not the day it ran
score: 4           # 0–5, deterministic. BLANK when nothing could be measured.
weighted: 4.4      # weighted completions — the raw input to the next median
tags: [review]
```

A day with no history, no deadlines due and no active timeline **did not score zero — it went
unscored.**

### Ledger row (`runtime/ledger.jsonl`)

```json
{"ts": "...", "actor": "zach|planner|coach|intake|devlog|...",
 "action": "create|update|toggle|revert|append",
 "target": "02-Areas/…/note.md", "sha": "c2baa90…", "title": "…",
 "extra": {"proposal": "…", "absorbed": true}}
```

`sha` is `null` for a write to an exempt-but-gitignored path — there is nothing to commit, and the row
says "no commit" honestly rather than making a promise the UI cannot keep.

### Calendar event row (`02-Areas/Personal/Calendar/YYYY-MM.md`)

```
- 2026-08-04 14:30–15:30 Dentist ^sg-evt-8f2a1c04
- 2026-08-06 Flight to SFO ^sg-evt-91c3ade7
- 2026-08-12→2026-08-15 Family visit ^sg-evt-a77d2b19
- 2026-08-19 09:00– Career fair ^sg-evt-c04b6e83
```

`- <date>[→<date>] [HH:MM[–HH:MM]] <title> ^sg-evt-<8 hex>`. No time = all-day · a date range =
multi-day · a trailing dash = open-ended. The full date is on every line even though the filename
repeats it, so a line parses without context — which is what the write path's *must re-parse as the
same kind of occurrence* hold needs, and it keeps a reschedule a one-line diff. **Liberal on input,
canonical on output**: `-`, `–`, `—`, `->` and `→` all parse, and `compose_event` only ever writes the
canonical form.

The ID is **eight** hex. It said eight in the contract and four in the contract's own examples for one
day; P1's parser followed the rule and P1's fixtures followed the examples, which is how it was found.

A cancelled event keeps its line and gains `cancelled::YYYY-MM-DD` before its block ID — the date it
was *cancelled*, not the date it was going to happen. It is still emitted as an occurrence, so it
renders struck through rather than vanishing, and it stops counting toward committed hours. Same
`key::value` shape the rule row already uses, so it is one grammar rather than two.

### Recurrence rule row (`02-Areas/Personal/Calendar/schedule.md`)

```
- MWF 10:30–11:20 [[CSE-311]] lecture until::2026-12-12 except::2026-09-14 ^sg-rule-cse311
```

`- <weekdays> [HH:MM[–HH:MM]] <title> [from::<date>] [until::<date>] [except::<date>,…] ^sg-rule-<slug>`.
Weekdays are `M T W R F S U`. Expanded when read and **never materialised** into a month note — the row
is the only record. `schedule.md`'s frontmatter carries `timezone:`, the vault's single timezone
declaration; times are local wall time with no offset.

### Occurrence (`GET /api/agenda`, in memory only)

```json
{"kind": "task|note|event|rule", "id": "…", "date": "2026-08-04",
 "start": "14:30", "end": "15:30", "all_day": false,
 "title": "…", "owner": "sigma", "raw": "- …", "priority": null,
 "source": {"path": "…", "line": 12, "block_id": "…", "rule_id": null, "field": null},
 "span": null, "section": "courses", "parent": "CSE-311",
 "no_sync": false, "conflict": null}
```

`source.path` is never null — provenance is an invariant, asserted in the tests rather than trusted to
four call sites. `raw` is the staleness token every write in this system already runs on.

---

## 12. The algorithms

### 12.1 Queue scoring (`todo.py`)

```
score = deadline + urgency + aging

deadline = 100 / (days_until_due + 1)     0 when there is no date;
                                          overdue clamps to days = 0, so
                                          overdue tasks tie at 100 and break by age
urgency  = 30 high (🔺 ⏫) | 15 medium (🔼 or unmarked) | 5 low (🔽 ⏬)
aging    = 0.5 per day since the task first appeared, capped at 15
```

Overdue is treated as due-today rather than escalating past 100, so one forgotten task can never
monopolise its window. An unmarked task defaults to **medium**, not low — it should not rank below one
someone bothered to mark 🔽.

**Windows are maxima, never quotas** — a section with fewer eligible tasks shows fewer rows and no
filler: `courses` 1 **per course**, `procertus` 3, `projects` 1 **per project**, `misc` 2. Courses and
Projects are per-parent because their queues are chains and the interesting task is the head of each
chain.

**A chain is a document that is a sequence**, not a section: `timeline.md` says so in its own title,
its blocks are dated in order, and you genuinely cannot do Day 5 before Day 4. A project hub's task list
is a todo list, and calling its second entry "blocked by" the first would be a dependency claim nothing
in the note supports.

**Suppression is a decision, not a timer.** A task never disappears on its own; `archived` is set by an
explicit action or an approved hygiene proposal. `01-Daily/` is excluded from the queues outright — the
retired planner left seven daily notes full of generated checkboxes that made Misc a seven-times-over
copy of the other three queues.

Every task exposes its score **broken into named terms**, so the UI can answer "why is this here?" — a
priority order nobody can interrogate is one nobody trusts.

### 12.2 The retrospective score (`retro.py`)

```
T  throughput   yesterday's weighted completions vs your own trailing 14-day median
A  adherence    deadlines met / deadlines that came due
M  momentum     active courses with a task completed, over all active courses

score = round(0.40·T + 0.35·A + 0.25·M)

completion weights: procertus 1.5 · courses 1.2 · projects 1.0 · misc 0.5
```

**Momentum is a straight touched/active fraction as of 2026-08-04**, encoding Zach's standing goal of
one task from every active course every day. It previously scored the *frontier* — a completion in a
course's `timeline.md` — over only those courses that had one. That is the better idea and was the
wrong measure: three of the five active courses have no `timeline.md`, so no amount of work on
CSE-311, CSE-351 or CSE-391 could move the score at all, and a component two thirds of your courses
cannot reach measures which folders hold a timeline rather than momentum. The frontier distinction
survives in `facts["frontier"]`, which the narrative prompt still receives, so a review can say
whether the timeline moved or the work was ad-hoc — it just no longer gates the arithmetic. The
practical effect is a harsher and more honest number: 2026-08-03 scored M 1.0 (1 of 5) where the old
rule gave 2.5 (1 of 2).

**A component with nothing to measure is omitted and the weights renormalise**, rather than counted as
zero — a day with no deadlines due did not fail to meet any, and scoring that as 0/5 adherence would
punish a Tuesday for being a Tuesday. Below 3 recorded days there is no meaningful median and the score
is blank. A task eligible and ignored for 14 days earns a mention in the narrative — a nudge, never an
action, because hiding something because it was ignored is how a queue quietly loses work.

The model is handed the finished numbers and writes one or two sentences. **The code never reads a
number back out of the model's answer.** A number a model assigns drifts with its mood, and a
productivity score you cannot audit is one you stop believing by the second week.

### 12.3 The write path, end to end

```
model  ──propose_change──▶  06-System/proposals/<name>.md   (status: pending)
                                        │
                    ┌───────────────────┴────────────────────┐
              kind: note                              kind: contract|skill|routine
                    │                                        │
            applier.apply_one()                    human sets status: approved
            (holds in §3.2 apply)                            │
                    │                              sigma reflect apply
        gitops.vault_write():                                │
          mutex → pull → write → stage → commit → read SHA back
                    │                              target exists? ─▶ staged to
            ledger.record(...)                     06-System/proposed/<path>
                    │                                        │
        dashboard ledger row + [undo]              sigma reflect diff / merge
                                                   (or the dashboard's diff view)
```

---

## 13. Feature inventory — everything built

Chronological, because the two numbering schemes overlap. **The OS's own phases are 0–4 plus a CLI;
the dashboard has its own Phase 0–6 track, which the project hub calls "Phase 5"; and the
priority-queue work is what `SYSTEM.md` calls "Phase 5".** They are different things — this list is the
unambiguous version.

| Date | Shipped |
|---|---|
| 2026-07-23 | **Phase 0** closed later: structured vault, `CLAUDE.md` contract, course study systems. **Phase 1**: `06-System/` + the `session-log` schema, `session_logger.py`, the `SessionEnd` hook, 13 sessions backfilled |
| 2026-07-24 | Capture fixed — the hook never fired (`SessionEnd` only runs on a clean exit); `SessionStart --sweep` safety net; machine-session filtering. **Phase 2**: `reflect.py`, the `insight`/`proposal` schemas, the `/reflect` skill, the weekly schedule |
| 2026-07-25 | Shared core extracted to `sigma/`; skill `scope` field; project resolution against hub `repo:` fields; **named Sigma**; both GitHub repos created private; **24 ProCertus files purged from git history** and gitignored |
| 2026-07-27 | **Phase 2.5**: `doctor.py` on `SessionStart`. Root cause of the capture gap found (unquoted interpreter path). The engine moved into the repo (`runtime/`) — until then 1,522 lines lived on one untracked disk. `pre-push` privacy guard. `reflect` heals its own input first. **Subscription-only** promoted from discovery to standing constraint |
| 2026-07-28 | **Phase 3** closed: `propose_change`, the backend serving its own built UI, the auth-liveness check. **Phase 4**: the specialist fleet, sequenced under one lock, on a daily schedule |
| 2026-07-29 | **The `sigma` CLI.** **Dashboard D0** — five read endpoints + the shell rewrite. **D1** — the reactor and the live SSE progress feed. **D2** — the wikilink resolver, `GET /api/graph`, the live-firing nebula |
| 2026-07-30 | **D3** — the whitelisted command palette. **Option B** — the model boundary opened for ProCertus while the sync boundary stayed shut. Two deep audits (~60 findings) and a three-commit fix sprint, including a **verified NTFS stream bypass** of the sealed boundary. **D4** — the autonomy flip: `gitops.py`, the ledger, the spend log, `applier.py`, `POST /api/tasks/toggle`, the Ctrl+J ledger with one-click revert, degrade→pause, and the contract + six constraint notes amended in the same unit of work |
| 2026-07-31 | **D5** — the no-sync boundary as one scan with two opposite failure directions; the mark, the ring, and the Ctrl+. lens. **D6** — study intake (incl. `.pptx` with slide structure), exam mode, repo awareness, the calendar strip, quick capture, brain filters, approve-a-proposal-from-the-dashboard. The auditor fixed by **precomputing the scan** instead of buying more turns. `devlog.py`. The cadence bug (`is_due` by calendar day) and the backup check |
| 2026-08-01 | `mapper.py` and `scaffold.py`. **The priority-queue engine** — four self-maintaining queues replacing the day-bucketed list, with quick-add, AI reword, completion/promotion, expansion views, and snooze/pin/archive/move. **`retro.py`** — the 06:00 retrospective, and the planner it replaces, retired |
| 2026-08-02 | **Agenda P0** — the calendar's contract amendment: `02-Areas/Personal/Calendar/`, the `#calendar` tag, tasks are date-only, a `Calendar events` section holding the line grammar, and the `calendar-month` and `schedule` schemas. Approved and placed by hand, because `reflect --apply` appends contract blocks to one section and this one belongs in five |
| 2026-08-03 | **Agenda P1** — `runtime/agenda.py`: the resolver, read-only. Event and rule parsing, read-time expansion, the merge, provenance on every occurrence, conflict detection, a TTL over the scan. **Agenda P2** — `GET /api/agenda`, and `/api/tasks` folded in behind the same resolver rather than left as a second answer to "what is due". **Agenda P3** — the today rail, replacing the 14-day strip and retiring `calendar.tsx`; free-hours-left displayed and feeding nothing. **Agenda P4** — the full week/month/agenda view on `Ctrl+'` and rail slot CA, read-only, with `[?]` provenance on every occurrence. **Agenda P5** — the write path: quick-add, drag-to-reschedule, cancel, an eight-hold table with one adversarial test each, and `cancelled::` added to the contract |

### The feature list, by area

**Memory and learning** — automatic per-session logging with a sweep safety net · weekly distillation
into atomic insights · a propose-and-approve loop that can write skills, contract additions, notes and
routines · staged corrections with diff and merge · a proposal ledger note recording all 39 raised
to date.

**Health and honesty** — eight watchdog checks in every session's context · auth liveness with a 12h
cache · privacy invariant checked at three boundaries · a backup check · per-specialist fleet health ·
`info`-level reporting that shows the Option B exemptions every session without costing the all-clear.

**Autonomy** — four sequenced specialists on a daily schedule · deterministic auto-apply, one revertible
commit per change · an append-only ledger with one-click undo · nine mechanical holds · degrade→pause
under rate limits · a fixed pre-09:00 window reservation.

**Study** — drop-folder intake for PDFs, PowerPoint, Markdown and text, filed to a validated course,
split atomically or kept whole as the material warrants · exam mode showing coverage and un-worked
sources · timeline drift detection by the coach · the calendar strip.

**Work** — four priority queues with an inspectable scoring function · quick-add into the right note
under the right heading · AI reword after filing · completion that pops and promotes · snooze, pin,
archive and move, none of them one-way doors · the 06:00 retrospective with an arithmetic score.

**Build** — repo awareness across the code folder (no hub / stale hub / idle) · dev-log entries spliced
into project hubs from commits and session logs · codebase mapping into linked architecture notes ·
project scaffolding from one line.

**Interface** — cited streaming Q&A over the vault with an `obsidian://` link per citation · a
21-verb command palette that cannot invoke anything off its whitelist · the live-firing brain · the
reactor · the activity ledger · the no-sync lens · quick capture · proposal diff and decide.

---

## 14. Tech stack and tooling

| Layer | Choice | Why |
|---|---|---|
| Language (engine) | **Python 3.14**, stdlib-only where possible | hooks and scheduled tasks must run with no environment to set up |
| Model access | **Claude Code CLI** (`claude -p`) and **`claude-agent-sdk` 0.2.128** | the SDK *is* Claude Code as a library — same binary, same subscription login, no API key |
| Models | **Haiku** for summarising and cheap scans, **Sonnet** for judgement | tiering is for rate-limit headroom, not money |
| API | **FastAPI 0.140** + **uvicorn 0.51** + **pydantic 2.13.4** | small, typed, and it can serve the built frontend from the same process |
| Streaming | **Server-Sent Events** | one-way traffic, reconnects itself, debuggable with `curl` |
| Frontend | **React 19.2** + **TypeScript ~6.0** + **Vite 8.1** | no router, no state manager — one view, plain hooks |
| Linting | **oxlint 1.71** | |
| Styling | hand-written CSS (~1,630 lines) | the visual language is specific enough that a framework would fight it |
| Graphics | **canvas 2D** with a seeded one-shot 3D force layout | no WebGL dependency; the layout is frozen, so drift is camera motion |
| Slides | **python-pptx 1.0.2** | 4 packages against markitdown's 18, and it exposes slide structure a flat conversion throws away |
| PDFs | markitdown / pdftotext via `tools/convert_pdfs.py` | already existed and already worked |
| Tests | **stdlib `unittest`** | no pytest dependency; the venv already has what the endpoint suites need |
| Storage | **Markdown + git**, plus JSON/JSONL sidecars for machine-only state | the substrate thesis |
| Sync | **obsidian-git** auto-commit/push every ~30 min, pull every ~15 | which is why every writer pulls first and holds a mutex |
| Scheduling | **Windows Task Scheduler** | the machine is Windows; no daemon to keep alive |
| Host | **one Windows 11 machine, loopback only** | one credential, local-first by construction |

---

## 15. Current state

*As of 2026-08-03. The table below still reads 2026-08-01 for every count that has not been
re-measured since; what changed is listed under it rather than by editing numbers nobody recounted.*

| | |
|---|---|
| Code repo | `zacharyli293680-bot/sigma-os`, private, **70 commits** |
| Vault repo | `zacharyli293680-bot/sigma-vault`, private, **182 commits** |
| Notes | **174 on disk, 150 tracked** — the 24-file carve-out is the difference |
| Session logs | **31**, backlog 0 |
| Insights | **8** |
| Proposals | **39 raised** — 37 applied, 2 rejected, 0 pending. The folder was collapsed on 2026-08-01 into `06-System/proposals/ledger.md`, one table row per proposal; full text stays recoverable from git |
| Skills | **4** — 3 user-scoped (`browser-verify-before-merge`, `env-secrets-audit`, `graphify-to-atomic-notes`), 1 vault-scoped (`reflect`) |
| Reviews | **0** — `SigmaOS-DailyReview` is installed and `Ready` but has never fired yet |
| Scheduled tasks | all three installed and `Ready` |
| Python modules | 20 in `runtime/` (incl. 5 in `sigma/`), 8 in `interface/backend/` |
| Frontend modules | 18 TS/TSX + one 1,630-line stylesheet |
| Tests | **19 suites, 358 test functions** in `tests/` — all green 2026-08-01 |
| Doctor | 0 alerts; one item waiting (the review has never run) |

**Since then (2026-08-02 → 03), the agenda subsystem's first four phases.** P0 amended `CLAUDE.md` in
five places; P1 added `runtime/agenda.py`; P2 added `GET /api/agenda` and moved `/api/tasks` onto the
resolver; P3 replaced the 14-day strip with the today rail and deleted `calendar.tsx`; P4 added the
full view on `Ctrl+'`; P5 added the write path and its holds table. Tests are now **22 suites,
438 test functions**. Tests are now
**21 suites, 413 test functions**, green under `interface/backend/.venv`. Doctor reports one item
waiting — a pending proposal unrelated to this work.

**The vault has no `02-Areas/Personal/Calendar/` yet**, so the rail currently shows dated tasks and a
flat week bar, and `timezone` reads `null`. The resolver's event, rule, span, seal and conflict paths
are exercised by tests and were driven in a browser against a scratch vault; they have never seen real
data, because there is none to see.

**A trap worth recording: the suite must be run with `interface/backend/.venv/Scripts/python.exe`.**
Under a bare system Python, five API modules and the tool gate cannot import at all (`fastapi`,
`claude_agent_sdk`), and the run reports 11 errors that are not real. `cli.py` already picks that
interpreter for the same reason; a human running `python -m unittest` by hand does not.

---

## 16. Known gaps and future work

Honest list. Nothing here is hidden behind a "coming soon".

### Open engineering gaps

1. **Scheduled tasks still hardcode interpreter paths.** Repointing them at `sigma` would leave one
   path per task instead of two and survive a venv rebuild or a Python upgrade. Both shims are verified
   working when called by absolute path from an unrelated directory. Deferred deliberately so a rewire
   never confuses a run being observed.
2. **Multi-turn chat continuity is wired but untested on long threads** (`session_id` round-trips).
3. **The interface is not packaged.** `sigma ui` runs uvicorn; Tauri would make it an app.
4. **`06-System/proposed/` has no expiry.** A staged change ignored for months just sits there.
5. **The auditor and coach can propose contradictory fixes** to the same drift, as they did on
   2026-07-28 — one proposing to amend the contract and two to amend the notes. That is a real decision
   for a human, but nothing flags that two proposals conflict.
6. **The window meter is a proxy** — spend rows, call counts, and the last rate-limit event. No
   documented API exposes subscription headroom, so it never claims a percentage. Reservation is a
   fixed 08:40–09:00 hold rather than a real budget.
7. **No doctor check for the paused fleet state** introduced in D4.
8. **The ProCertus carve-out lives on one disk.** By design it is excluded from the vault repo, so it
   has no off-machine backup. The no-sync lens now gives the exact list; the backup itself is unbuilt.
9. **Reverting a toggle on a note younger than one obsidian-git backup cycle deletes the file** — the
   commit being reverted is the file's *creation*. Git-exact, recoverable, self-limiting; recorded as a
   known edge rather than blocked.
10. **The auditor's turn budget scales with the vault, not the checks.** 24 stopped being enough
    somewhere between 157 and 175 notes; `inventory.py` fixed the cause, but 40 is still a number that
    will need revisiting as the vault grows.

### Designed and not built

From the vision note, in rough order of how ready each is. The first entry is not from the vision note,
and is further along than anything below it:

- **The agenda subsystem, P7 onward.** P0–P6 have shipped (§7.15, §8, §9, §11): the contract, the
  resolver, the read endpoint, the today rail, the full view, the write path with its holds table, and
  rule exceptions. What is left is **P7** committed hours feeding queue windowing and the 06:00
  retrospective — the actual reason the subsystem exists, since the queue still has no idea what a day
  already costs; **P8** Google Calendar. The brief is
  `03-Projects/sigma-os/sigma-os-calendar-plan.md` in the vault, and its nine open questions were
  answered on 2026-08-02.

  **P6's browser drive is outstanding**, and the phase is not done until it happens (§11's third
  done-when). Everything else passed: 469 tests including 30 for P6 and two mutation tests, and a
  real skip driven over HTTP against the real vault — commit, ledger row, undo, file restored
  exactly. What was not exercised is a human clicking `SKIP THIS ONE` in the provenance strip or
  dragging a rule occurrence between days. The Chrome automation harness wedged partway through on
  2026-08-04 (JS evaluation timing out, the page reporting the backend unreachable while `curl` got
  200 from the same origin, the extension hinting at a pending permission prompt), which is a worse
  instance of the flakiness already recorded against P4's keybind testing. Two UI paths therefore
  rest on unit tests alone: the skip button and the rule drag.

  **P6 also left `this and all future` unbuilt, deliberately and by name.** Moving every remaining
  occurrence means splitting the rule into two rows — the old one gains `until::`, a new one gains
  `from::` — which is a different operation on a different number of lines, and the plan scopes P6
  to single occurrences. The endpoint refuses it with a reason rather than approximating it.

  Two of those answers were settled by this repo rather than by preference, and both still bind: tasks
  stay **date-only**, because the Tasks plugin has no concept of a time and the calendar would become
  the sole source of truth for a field the note is supposed to own; and a dashboard write into a
  gitignored path is **refused rather than permitted**, because `gitops.commit()` returns no SHA for an
  ignored path and that write would be the only one in Sigma with no ledger row and no undo. P5 enforces
  the second as a hold, checked inside the mutex after the pull, the way `applier.py` does it — not
  before the mutex, the way the toggle does.

  Three things P8 needs that already exist: `owner:` on every occurrence, local wall time plus the
  `timezone:` declaration on `schedule.md`, and the fact that outbound sync is the first place where
  **visible ≠ syncable**. The ID map (`runtime/agenda.state.json`) does not exist yet and is not needed
  until there is something to map.
- **Application pipeline** — paste a posting, get a tracked application note, deadlines on the calendar,
  a nudge before each goes stale. Not built because `02-Areas/Career/Applications/` is *empty*; the
  tracker has reported "pipeline is clear" every week since it started. A panel with nothing to show.
- **Quick reference** — equation sheets, cheat sheets, git/unix crib notes, one keystroke. Same reason:
  `04-Resources/` holds one file.
- **Exam mode's third question** — "what did you get wrong last time". Deliberately absent and said out
  loud, because nothing in the vault records a wrong answer, and a weak-areas panel would be a confident
  guess wearing the costume of data.
- **Rail slots `AG`, `CR`, `SY`** — agents, career, system. Rendered disabled.
- **Agent control** — see every agent and assign work to one directly; break a goal down into
  specialist work and get one answer back; **watch, steer, stop** a run. A run you can interrupt is a
  run you can trust to start.
- **Explain this** — highlight a proof, a slide, a stack trace, a chunk of C, get it opened up at the
  level you are actually at.
- **Rubber-duck with context** — the code *and* the notes about the code, together.
- **Tailor a résumé or cover letter** against a posting, grounded in what was actually built; interview
  prep driven by the gap between a posting and the vault.
- **Browse the web and act in a browser** — read docs, pull a spec, fill an approved form.
- **Google Suite as first-class sources** — Docs, Sheets, Drive, Calendar in; a schedule out; an email
  thread into a task.
- **Deep research** — a real question, several sources, one synthesised cited note, filed where it
  belongs.
- **Phone access.** The only vision-note open question never answered. None of this is reachable from a
  phone, and half of capture happens away from the desk.

### Structural questions still open

- **How much should Sigma do unattended?** The answer moved once already (propose-only → auto-apply
  notes with an undo ledger). The contract remains the single held category. Where the line goes next
  is not settled.
- **What happens when the vault gets big?** Several components scan every note on every call: the
  auditor's inventory, the graph, the queue scan, the no-sync scan. They are cached with TTLs, but
  nothing has been profiled past ~175 notes.
- **Two numbering schemes** (OS phases and dashboard phases) already collide in the docs. Any new
  phase-shaped work should pick one or name itself something else.

---

## 17. Failure modes this project has already paid for

Worth stating plainly, because the same shape has now appeared six times and will appear again.

> **"Configured" and "executes" are different claims, and only the second one matters.**

1. `SessionEnd` was wired and **never fired** — it only runs on a clean exit, and sessions were ending
   by closing the terminal. Three days of capture lost.
2. The hook's interpreter path was unquoted, and Claude Code runs hooks through a POSIX shell where
   `\P` collapses to `P`. The hook died before Python started. `session_logger.log` was empty because
   the script that writes it never ran.
3. The privacy callback was shadowed by `allowed_tools`, then skipped by `permission_mode`. **Both runs
   reported zero denials** — zero because nothing had been denied, not because nothing needed denying.
   Twice, that looked exactly like a guard working. The fix was a `PreToolUse` hook, which fires for
   every tool call regardless of mode.
4. The fleet's per-specialist timeout was declared and caught but **never applied** — `wait_for` was
   never called, so the `except` was unreachable and a hung specialist would have blocked the daily run
   indefinitely while holding the lock.
5. A logged-out CLI **fails silently**. Nothing checked it until the auth check existed.
6. On Windows, redirected stdout is cp1252 — which is every scheduled task. cp1252 *contains* the
   em-dash, which is why this hid for weeks while the fleet logged model summaries full of them. It does
   not contain `→` or emoji. One arrow in a specialist's summary would have killed the daily run with
   its output going nowhere.

And two more of a different shape:

7. **`is_due()` compared hours, not calendar days.** Three consecutive 09:00 runs did nothing and each
   reported success. No self-check would ever have caught it; only the calendar could tell a green run
   from a correct one.
8. **A counter counted the wrong thing.** The fleet reported "2 proposals" when one file existed — it
   was counting tool *calls*, including refused ones. The same defect this project had already fixed
   once in `--dry-run`, which wrote *"logged 3"* into the failure log while writing nothing.

**The countermeasures are the same every time:** run the thing rather than reading it; test the
adversarial case rather than the happy path; make health checks report what a resolver can actually
*see* rather than that it ran; and count what appeared on disk, never what was attempted.

Three more lessons worth carrying:

- **A fix that lives in a module only protects the files that import it.** `cli.py` did not import
  `sigma`, so it missed the UTF-8 reconfiguration and mangled the em-dash in its own `--help` text.
- **A sealed test has to try to leak.** The Windows-newline bug in the batch `check-ignore` call was
  caught by an adversarial check (*procertus-interface must not appear*), not by anything passing.
- **The first real run of anything finds what reasoning did not.** Intake's first live run exposed two
  defects in the write layer and one environment failure. Phase 5's live check moved the no-sync marker
  from ▦ to ⊘ because `🏁` renders as a near-identical hatched box in this font stack, and a
  confidentiality mark that tofu can imitate is worse than none.

---

## 18. Glossary

| Term | Meaning |
|---|---|
| **Sigma** | the OS. Project name `sigma-os`; the vault repo is `sigma-vault`. |
| **The contract** | `CLAUDE.md` in the vault — the frontmatter schema and standing permissions. |
| **Carve-out** | the 24 ProCertus files rewritten out of git history, gitignored, present on disk. |
| **Sealed** | gitignored and *not* exempted — refused at the model boundary, invisible in panels. |
| **Exempt / Option B** | gitignored but listed in `model_allow` — readable by the model, marked bronze, still never synced. |
| **No-sync** | the display-layer name for exempt material: "this never leaves the machine". |
| **Held** | a proposal (or a reactor arc) stalled waiting on a human decision. |
| **Staged** | a proposal whose target already exists, written to `06-System/proposed/<path>` instead of applied. |
| **Absorbed** | a change that obsidian-git's interleaved backup commit picked up; the ledger reports that commit rather than pretending. |
| **The window** | the rolling rate-limit window that *is* the budget. |
| **Degrade** | dropping Sonnet → Haiku after a rate limit; the reactor's arcs render hollow so it is visible. |
| **Chain** | a note that is a sequence (`timeline.md`), where task *n+1* is genuinely blocked by task *n*. |
| **The fleet** | the sequenced specialists: coach, auditor, tracker. |
| **The reactor** | the dashboard's centre instrument — the fleet's state, and the status bar. |
| **The brain** | the vault link graph rendered as a nebula that lights up where an agent reads. |
| **L0–L4** | the memory layers (§4). |

---

## 19. Working on this

### Run it

```powershell
sigma                       # what needs your attention right now
sigma doctor                # the nine health checks
sigma ui                    # backend + built frontend on http://127.0.0.1:8787
```

First time only: `python -m venv .venv` then `.\.venv\Scripts\pip install -r requirements.txt` in
`interface/backend/`, and `npm install && npm run build` in `interface/frontend/`. For frontend work,
`npm run dev` against the running backend (any loopback port is allowed by CORS).

### Test it

```powershell
interface\backend\.venv\Scripts\python -m unittest discover -s tests -t tests
```

**Both halves are load-bearing.** The backend venv supplies `fastapi`/`httpx`, which the endpoint
suites import; `-t tests` is required because `tests/` deliberately has no `__init__.py`, and a bare
`discover` dies with *"Start directory is not importable"* rather than anything that names the cause.
There is no `sigma` verb for the tests, on purpose.

**Budget three to eight minutes** (measured 148s and 434s on the same machine, same day) — several suites shell out
to git against throwaway repos, which is the point: the write path is tested against real git rather
than a mock of it. Do not add a timeout that assumes it is fast.

**A test run must not write into `runtime/`.** It did for months, and the cost was not disk: the
review suite's fixture raises `RuntimeError("claude is not on PATH")` to prove the 06:00 score
survives a dead model, every run appended that line to the production `review.log`, and it was
eventually read there as a live failure of a job that had never failed. `test_intake` appended to
`intake.log` the same way, and `test_fleet_window` **truncated** `fleet.fire.jsonl` on every pass —
erasing the last real run's feed, which is destruction rather than noise.

`tests/isolation.py` is the fix, in two halves matching the two kinds of path. **Logs** resolve
`SIGMA_STATE_DIR` inside `make_logger` at *write* time (they are closures bound at import, so a
per-test patch of the constant would not have moved them); importing `isolation` sets that variable,
and because discovery imports every test module before running any of them, **one import anywhere
redirects the whole run's logs** — including suites that never opt in. **State files** are module
constants read at use time, so `isolation.sandbox(self)` patches them per case and restores them on
cleanup. Its `_STATE` list is the only such list in the repo, on purpose: the hand-maintained
per-suite tuple it replaces is what drifted, and `FIRE_PATH` is the entry it was missing.
`test_isolation` checks the real files' bytes rather than the redirect's presence, and carries the
control case — with the variable cleared, the same logger writes to the path it was bound to.

The suites: `test_agenda`, `test_agenda_api`, `test_agenda_except`, `test_agenda_writes`,
`test_applier`, `test_devlog`, `test_doctor_backup`, `test_fleet_due`, `test_fleet_window`,
`test_intake`, `test_inventory`, `test_isolation`, `test_mapper`, `test_nosync`,
`test_proposal_content`, `test_queue`, `test_queue_api`, `test_review_api`, `test_review_score`,
`test_scaffold`, `test_sigma_core`, `test_study_api`, `test_tool_gate`, `test_writes_api`.

### Add something

**A new specialist** → add a `Specialist` to `specialists.py` with a key, cadence, model, effort, order
and brief. It gets privacy, proposals, sequencing, timeout, rate-limit handling and per-specialist
health for free. If its expensive part is *gathering* rather than *judging*, give it a `context`
callable and precompute the gathering in script code.

**A new palette verb** → add an entry to `VERBS` in `commands.py`. It must be a fixed argv; if it needs
an argument the human types, it does not belong in the palette (see the four disabled entries for how
to say so).

**A new panel** → a read function plus a `@router.get` in `panels.py`, a type in `api.ts`, and a
component. Wrap the scan in `_cached(key, ttl, compute)`.

**A new writer** → route it through `gitops.vault_write()` and `ledger.record()`. Do not add a second
place that commits.

**A new model-calling tool** → build its options with `agent.build_options()`. Do not construct
`ClaudeAgentOptions` anywhere else.

### Things not to do

- Do not give a model a filesystem write tool, however scoped. That bet has lost twice.
- Do not add a second privacy list. `.gitignore` is the declaration; `model_allow` is the one exemption
  and it is surfaced in every session precisely because it is a second declaration.
- Do not add a concurrency option to the fleet.
- Do not nest the vault inside this repo, or copy code into the vault.
- Do not commit `runtime/*.config.json`, `*.state.json`, `*.jsonl` or `*.log`.
- Do not report a run as verified because a self-check was green. Run it, watch it, and prefer the
  adversarial case.
