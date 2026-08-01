# Sigma — system state

*What exists, what is actually running, and what it does not do yet. Rewritten 2026-08-01.*

Three documents, and the split is deliberate:

| | |
|---|---|
| [`README.md`](README.md) | the introduction — what Sigma is, in a page |
| **`SYSTEM.md`** (this) | **the state of the system** — what is built, what executes, what is healthy, what is left. The thing to read when you come back after a gap and want to know where the machine stands. |
| [`CONTEXT.md`](CONTEXT.md) | the reference — every module's functions, all 27 endpoints, the data formats, the algorithms written out. The thing to read when you are about to change something. |

Where this file and `CONTEXT.md` cover the same ground, this one stays at the level of *what exists
and whether it runs*, and defers the function-by-function detail. Both were brought current on
2026-08-01; if they ever disagree, check the dates at the top of each and trust the newer.

**The vault is the more complete design record.** This file summarises; `03-Projects/sigma-os.md` in
the vault explains *why* each decision went the way it did, and its dev log is the best account of how
this got here.

---

## 1. What Sigma is

An agentic OS with an **Obsidian vault as its memory substrate**. Agents perceive state (frontmatter,
checkboxes, dates), reason, act (draft notes, plans, reports, dev logs), and remember (git history) —
with the vault co-writable by human and agents alike.

Three systems, deliberately separate:

| | What | Where |
|---|---|---|
| **Knowledge Vault** | Zach's facts — coursework, projects, internship, applications | `C:\Users\tusha\Documents\Obsidian Vault` |
| **Agent Memory** | Sigma's record of *its own* work | `06-System/` inside that vault |
| **OS Application** | The engine | this repo |

The vault works as agent memory because it is human/machine co-writable, its frontmatter contract is a
queryable API, git is temporal memory, and wikilinks are a retrieval graph.

**One credential.** Sigma has no `ANTHROPIC_API_KEY` and no `.env`. Both paths to a model end at the
same Claude Code CLI riding this machine's subscription login: `claude -p` for capture, reflection, the
watchdog's auth probe and the retrospective's narration, and the Claude Agent SDK (which *is* Claude
Code packaged as a library, spawning that same binary) for the interface, the fleet, intake, devlog,
the mapper and the scaffolder. Verified on both paths with the key explicitly unset.

The consequence shapes everything downstream: **the budget is a rate-limit window, not an invoice.**
Nothing is billed per token, so the question is not *what does this cost* but *how many agents can run
before the window is spent*. That is why specialists are sequenced rather than fanned out, why there is
no concurrency option, and why model tiering (Haiku vs Sonnet) exists for headroom rather than money.

---

## 2. Memory model

Five layers, raw → distilled:

| Layer | What | Where | Written by |
|---|---|---|---|
| **L0** | Raw transcripts | `~/.claude/projects/**/*.jsonl` | Claude Code |
| **L1** | Session logs — one per session | `06-System/sessions/` | `session_logger.py` |
| **L2** | Knowledge notes | the vault at large | Zach, and applied proposals |
| **L3** | Procedural memory — skills + the contract | `~/.claude/skills/`, `<vault>/.claude/skills/`, `CLAUDE.md` | approved proposals |
| **L4** | Insights — durable lessons | `06-System/insights/` | `reflect.py` |

L0 is treated as opaque: the JSONL schema is internal to Claude Code and shifts between versions, so
Sigma **summarises it, never parses it** as a contract.

Two records sit alongside the ladder rather than inside it, both added with the autonomy flip:
**reviews** (`06-System/reviews/`, one note per day, written by `retro.py`) are about the human's
throughput, and the **activity ledger** (`runtime/ledger.jsonl`) is about the machine's actions — every
write, with the commit that carries it.

---

## 3. Phases — where each track stands

There are two numbering schemes and they collide, so both are laid out here. **The OS's own phases run
0–5. The dashboard has its own D0–D6 track**, which the vault's project hub confusingly calls "Phase
5". They are different things; anything new should pick one or name itself something else.

### The OS track

| Phase | What | Status |
|---|---|---|
| **0 — Foundation** | Structured vault, `CLAUDE.md` contract, course study systems | ✅ done |
| **1 — Session logging** | `SessionEnd` hook + `SessionStart` sweep + Haiku summariser → L1 notes | ✅ 2026-07-23, capture fixed 07-24 |
| **2 — Reflection & skills** | Weekly reflection → insights + proposals, propose-and-approve | ✅ 2026-07-24 |
| **2.5 — Watchdog** | `doctor.py` on `SessionStart` — reports Sigma's health into every session | ✅ 2026-07-27 |
| **3 — Interface** | Local web app: Agent SDK backend + React frontend | ✅ 2026-07-27, closed 07-28 |
| **4 — Specialist fleet** | coach / auditor / tracker, sequenced under one window | ✅ 2026-07-28 |
| **CLI** | `sigma` — one front door over all of it | ✅ 2026-07-29 |
| **5 — Priority queues** | Four self-maintaining queues + a 06:00 retrospective; the 09:00 planner retired | ✅ 2026-08-01 |

Phase 2.5 was in neither plan. It exists because both shipped phases were found **dead** on 2026-07-27
— Phase 1 had captured nothing for three days, Phase 2's first scheduled run had failed on `ENOTFOUND`
with the next attempt a week out — while both `--status` self-checks reported green and were *correct*.
Nothing had run them.

### The dashboard track

| | What | Status |
|---|---|---|
| **D0** | The shell and the read API — five endpoints, the §3 layout, chat into a drawer | ✅ 2026-07-29 |
| **D1** | The reactor and the live SSE progress feed | ✅ 2026-07-29 |
| **D2** | The wikilink resolver, `GET /api/graph`, the live-firing brain | ✅ 2026-07-29 |
| **D3** | The Ctrl+K command palette over a server-side whitelist | ✅ 2026-07-30 |
| **D4** | **The autonomy flip** — gitops, the ledger, the spend log, `applier.py`, real checkboxes, one-click undo, degrade→pause | ✅ 2026-07-30 |
| **D5** | The no-sync boundary: one scan, two opposite failure directions; the mark, the ring, the Ctrl+. lens | ✅ 2026-07-31 |
| **D6** | Domain panels — study intake, exam mode, repo awareness, calendar strip, quick capture, brain filters, approve-from-dashboard, dev log, codebase mapping, project scaffolding | ✅ 2026-07-31 → 08-01 |

D6 was always "a list to pull from, not a commitment to build all of it". What was pulled is above;
what was checked and deliberately **not** pulled is in §11.

---

## 4. Components

One line per file. `CONTEXT.md` §7 is the same map with the functions in it.

```
sigma.cmd / sigma       front door → runtime/cli.py   (sigma.cmd must stay CRLF)
runtime/
  sigma/__init__.py     shared core: settings precedence, frontmatter, kebab, `claude -p`,
                        project resolution, JSON state files, UTF-8 output, detached spawn
  sigma/gitops.py       the ONE place anything commits: mutex, pull, path-scoped commit, revert
  sigma/ledger.py       the append-only activity ledger (ledger.jsonl) — what CHANGED
  sigma/audit.py        the append-only attempt log (audit.jsonl) — what was TRIED, incl. refusals
  sigma/spend.py        the rate-limit window proxy (spend.jsonl)
  cli.py                the `sigma` command — dispatcher, picks the interpreter
  session_logger.py     Phase 1 — transcript → session log
  reflect.py            Phase 2 — logs → insights + proposals; apply / diff / merge
  doctor.py             Phase 2.5 — eight health checks, exit code always 0
  fleet.py              Phase 4 — runs specialists one at a time under a lock
  specialists.py        who the three specialists are and what each is briefed to do
  inventory.py          the vault's frontmatter, precomputed for the auditor
  applier.py            deterministic auto-apply — the script half of the autonomy flip
  todo.py               the four priority queues and their scoring function
  retro.py              the 06:00 retrospective — arithmetic score, model writes only the sentence
  intake.py             study intake — dropped course material → contract-shaped notes
  devlog.py             commits + session logs → a project hub's dev log entry
  mapper.py             a codebase → linked architecture notes
  scaffold.py           `sigma new` — repo, gitignore, README, hub note from one line
  install_hooks.py      copies the pre-push guard into each repo
  hooks/pre-push        refuses a push where a tracked file is also gitignored
interface/
  backend/app.py        FastAPI: /api/ask (SSE), /api/health, serves the built frontend
  backend/agent.py      Agent SDK options — the single guarded construction every consumer uses
  backend/privacy.py    the model boundary
  backend/propose.py    the only way an agent affects disk — writes a *pending* proposal
  backend/panels.py     every read endpoint, plus the wikilink resolver behind the brain
  backend/commands.py   the palette's whitelisted command runner
  backend/writes.py     every mutation: toggle, queue add/edit/reword/meta, capture, revert
  backend/review.py     read one proposal with its diff, and decide
  frontend/             React 19 + Vite 8 — 18 modules and one hand-written stylesheet
tests/                  18 stdlib-unittest suites
tools/convert_pdfs.py   a vault utility, not part of Sigma (imported by intake)
```

**Two interpreters, resolved for you.** `fleet.py`, `intake.py`, `devlog.py`, `mapper.py`,
`scaffold.py` and the interface need the Agent SDK, which lives in `interface/backend/.venv`.
Everything else is pure stdlib. That venv is a superset, so `cli.py` prefers it for every subcommand
and falls back to system Python. The SDK is imported *lazily*, so `--status` and `--dry-run` work
without it.

---

## 5. How the pieces run

### Automatically

| Trigger | Runs | Purpose |
|---|---|---|
| `SessionEnd` hook | `session_logger.py` | log the session that just ended |
| `SessionStart` hook | `session_logger.py --sweep --detach` | catch sessions the above missed |
| `SessionStart` hook | `doctor.py --quiet` | put Sigma's health in the session's context |
| `SigmaOS-DailyReview`, 06:00 | `retro.py` | score yesterday, write the review note |
| `SigmaOS-DailyFleet`, 09:00 | `fleet.py` | run the specialists that are due |
| `SigmaOS-WeeklyReflection`, Sun 09:00 | `reflect.py` | distil the week into insights + proposals |

`SessionEnd` only fires on a *clean* exit — closing the terminal skips it. That cost three days of
capture once. The `SessionStart` sweep is the safety net, so capture is **eventually consistent**: a
session missed today is picked up on the next launch.

All three tasks carry `RestartCount 3`, added after one network blip cost a week of learning, and the
fleet carries `-StartWhenAvailable`, so a machine asleep at 09:00 runs late instead of skipping the day.

### The daily fleet, in order

`fleet.py` takes an exclusive-create lock (check-then-write could race a palette click at 09:00:00 into
two concurrent fleets), then runs each due specialist **to completion before starting the next**:

| Order | Specialist | Cadence | Model | Turns | Brief |
|---|---|---|---|---|---|
| 30 | **coach** | weekly | Sonnet | 24 | plan-vs-date drift in course timelines |
| 40 | **auditor** | weekly | Haiku | 40 | frontmatter against the contract |
| 60 | **tracker** | weekly | Haiku | 24 | stale entries in the job pipeline |

**Order 10 is vacant.** It belonged to the *planner*, a daily Sonnet run that rewrote the day's note in
`01-Daily/`. It was retired on 2026-08-01: a daily rebuild only earns its cost if the list cannot
maintain itself, and the priority queues promote the next task the moment one pops. What replaced it is
not another planner but `retro.py` — 06:00, about *yesterday* rather than today, and its number is
arithmetic rather than judgement. Planning forward stopped needing a model; noticing what did not move
still does.

**There is no concurrency option**, deliberately — an option is a constraint you have already decided
to break. A stale lock (>2h) is taken over rather than obeyed. Each specialist has a 420s timeout
applied via `asyncio.wait_for`. `is_due()` compares **calendar days**, not rolling hours, so an
afternoon hand-run no longer suppresses the next morning's scheduled run.

A rate limit **degrades** Sonnet → Haiku and continues; a second one while already degraded **pauses**
cleanly with a resume point persisted, and the remaining specialists stay due for the next invocation
rather than burning the rest of the window on retries.

### The write path

```
model ──propose_change──▶ 06-System/proposals/<name>.md  (always status: pending)
             │
   kind: note ├──▶ applier.py: nine mechanical holds, then
             │      gitops (mutex → pull → write → commit → SHA) → ledger row → [undo] in the UI
             │
   everything else ──▶ waits for Zach: status: approved, then `sigma reflect apply`
                       (target exists? staged to 06-System/proposed/ for diff + merge)
```

---

## 6. The guarantees, and how each is enforced

Mechanical, not promised. Every one exists because something failed.

### No agent writes to the vault

The interface and every specialist get **`propose_change`**, an in-process MCP tool taking structured
fields — the *backend* writes the proposal note. The agent holds no filesystem write primitive at all,
so "cannot apply its own changes" is a fact about which tools exist rather than a rule it might route
around. The alternative — a scoped `Write` fenced by a path check — is the bet that already lost twice
here.

Proposals land in `06-System/proposals/` as `status: pending`, in **one shape**, whether raised by the
weekly reflection, a chat question, a 09:00 specialist, study intake, the dev logger or the mapper.

### Applying is deterministic, and reversible

*(Changed 2026-07-30 by dashboard D4. Before that, everything waited for a human.)*

Script code — never the model — applies each fresh `kind: note` proposal as **its own git commit**,
recorded in an append-only ledger with one-click revert in the UI. `applier.py` **holds** a proposal if
it is not `kind: note`, targets `CLAUDE.md`, targets the proposal machinery itself, resolves outside the
vault, is gitignored, ticks a checkbox, would shrink the note below 40% of its size, has an empty
content block, or arrives while the git mutex is busy. Each hold is adversarially tested.

**`CLAUDE.md` is the single held category.** It governs the schema every other note obeys, and an agent
has already produced a malformed contract edit once.

Undo is `git revert` of exactly that one commit, surfaced as a button — not a bespoke rollback with its
own bugs.

### Corrections are staged, never overwritten

`reflect --apply` creates files that do not exist and appends to the contract. It **never overwrites** —
the only reason a Haiku-drafted `MAP.md` did not once flatten a hand-built one. But correcting existing
notes is the entire job of two specialists, so a proposal whose target exists is **staged** to
`06-System/proposed/<the target's path>` and left `approved`:

```
sigma reflect diff            git diff of each staged change vs its target
sigma reflect merge NAME      copy one over its target, mark it applied
```

`merge` is **the one place Sigma overwrites a note**, and it is human-only by construction: one explicit
name, refuses anything not already approved *and* staged, no bulk mode, no scheduled job calls it. Since
D6 the dashboard's proposal view does the same job with a diff on screen. The mirror path matters: a
`timeline.proposed.md` beside the real one would carry its `type:` frontmatter and appear in Dataview as
a phantom second note.

### Privacy — one declaration, three boundaries

> **If git will not sync it, the model does not see it.**

The vault's `.gitignore` is the single declaration. **24 Markdown files were rewritten out of git
history on 2026-07-25** (including the root commit), gitignored, and restored to disk: readable in
Obsidian, never synced. Today that is 15 ignore entries covering 28 files present on disk and absent
from the repo — 24 of them notes.

| Boundary | Enforced by |
|---|---|
| **push** | `runtime/hooks/pre-push`, installed in both repos — checks the working tree *and* the outgoing commit range |
| **model** | `privacy.py` as a `PreToolUse` hook on every agent run |
| **display** | `panels.py` + `privacy.gitignore_scan` — sealed paths dropped, exempt paths *marked* |

The precise invariant the push guard checks is **a file may not be both tracked and gitignored** — one
rule covering every carved-out path, self-maintaining, zero false positives. Grepping a diff for the
client's name was the first attempt and flagged two files that legitimately mention it: Sigma's own
insight about the carve-out, and the proposal asking for the grep.

**Option B (2026-07-30) opened the model boundary and left the sync boundary shut.** Zach's internship
agreement permits AI tools, so `privacy.config.json` carries a `model_allow` prefix list — currently
three prefixes covering **17 notes** — that the guard, the panels and the graph all honour while the
files stay gitignored and untracked. GitHub never gets them. Because an exemption list is exactly the
second-declaration-that-drifts the original design refused, the drift is surfaced rather than trusted:
the doctor names the active exemptions in **every** session, at `info` level, which is shown even in
`--quiet` mode and never costs the all-clear.

The **capture deny list deliberately did not move.** New session logs land in *tracked* `06-System/`, so
summarising internship sessions would leak them through the side door. That asymmetry is visible as a
number: 24 carved-out notes = 17 model-exempt + **7 sealed session logs** the model still cannot read.

Marking and hiding **fail in opposite directions on purpose**: hiding is a safety measure, so it fails
*on*; marking is a confidentiality *claim*, so it fails *off*. An unanswered `git check-ignore` yields
no marks at all, and a mark needs two independent yeses — git refuses it *and* the operator listed it.

Enforcement is a **`PreToolUse` hook, not the permission callback**: `can_use_tool` is only consulted
for calls that would otherwise prompt, and in `permission_mode="default"` Claude Code treats
Read/Grep/Glob as safe and never prompts — so the callback was never reached, no error was raised, and
the run reported *zero denials*. Twice, that looked exactly like a guard working. `VaultPrivacy` is
constructed `allow_writes=False` **unconditionally**; the proposals flag does not reach it, because "may
propose" and "may edit a note" must never be one switch. It fails closed: a `git check-ignore` error
refuses the path, and NTFS `::$DATA` stream suffixes — a verified bypass — are refused outright.

### An agent may only call tools it was granted

*(Added 2026-08-01, ahead of the browser lane — see the vault's `browser-plan`.)*

`build_options` builds **one list and uses it twice**: as the grant (`tools=`) and as the gate
(`granted_tools=`). `privacy.py` refuses anything outside it, so adding a tool to a run necessarily
adds it to what the guard vets, and *forgetting* fails closed rather than open.

Before this the guard returned "allowed" for any tool it had no rule for — an allowlist of things to
*check* rather than a denylist of things to *permit*. That was survivable only while every tool's
argument was a path. A tool whose argument is a URL would have sailed through unvetted with the run
reporting **zero denials**, which is exactly what this guard reported the two times it was already
found not to be running.

Refusals are written to `runtime/audit.jsonl`, because the danger of a fail-closed default is not that
it blocks an attacker — it is that it blocks something legitimate *silently*.

### Never deletes, never checks your boxes

Agents observe freely, write additively, and never tick a checkbox on Zach's behalf. `retro.py` reports
and completes nothing; `applier.py` refuses any proposal that ticks a box.

---

## 7. The watchdog

`doctor.py` runs on every `SessionStart` and speaks only when something is wrong. Its exit code is
**always 0** — a broken watchdog must not block a session from starting.

**Nine checks** (six at first; `review` and `backup` added 2026-07-31, `toolgate` 2026-08-01):

1. **capture** — is any finished session still unlogged?
2. **reflection** — did the weekly loop run, and is anything waiting on Zach?
3. **schedule** — did each scheduled task fire *and succeed*? (It can fire and fail.)
4. **review** — has the 06:00 retrospective run, and is it current?
5. **auth** — is the login still live?
6. **privacy** — is anything gitignored also tracked, is the pre-push guard installed, and which model
   exemptions are active?
7. **toolgate** — did the gate refuse a call to a tool it was not granted? Reports `unvetted-tool`
   hits only: the older rules have a track record, that one does not, so its hits are surfaced until
   it earns one. A false refusal is otherwise invisible.
8. **backup** — has the vault pushed recently? (obsidian-git pushes every 30 min; stale after 2h)
9. **fleet** — per *specialist*: any failing, overdue by >2 cycles, or a run that stopped on a limit?

Two design rules it follows:

- **It is not a fourth source of truth.** Facts come from the tools that own them —
  `session_logger.capture_candidates()`, `reflect.load_state()`, `fleet.load_state()`. A watchdog with
  its own private copy of "is capture healthy?" is just another thing that can disagree.
- **It does not cry wolf.** A backlog the concurrent sweep is already clearing is not an alert. A failed
  scheduled run stops nagging once re-run by hand. A network blip is never reported as an expired login.

The auth check is the subtle one: the only honest test is a real model call, but the budget *is* the
rate-limit window — so a good result is cached for 12h and only failures re-probe. A failing probe is
free, since it errors before a call is spent.

---

## 8. The interface

`sigma ui` → one process on `127.0.0.1:8787` serving the API and the built frontend. Local-first and
unauthenticated on purpose: it binds to loopback, reads a vault on this disk, and inherits a *machine*
login — so the machine is the natural boundary. **Do not expose it.**

**As an agent** it reads the vault live (no index to rebuild), cites by `[[wikilink]]` rendered as
`obsidian://` links, streams tokens over SSE with a collapsible trail of every lookup, surfaces the
watchdog at `/api/health`, and proposes rather than edits.

**As a dashboard** it is a dark instrument, not a dark-mode web app: top strip (health · window meter ·
study block · clock), left rail, a centre stage where the **brain** (the vault's link graph as a nebula
that lights up along the path an agent actually reads) carries the **reactor** (arcs per specialist,
five states, the status bar and the agent visual in one object) at its heart, a right column of
waiting-on-you and the queue digest, a lower row of calendar strip and projects, and a foot with the
live activity dock.

One keystroke each: `Ctrl+K` palette · `Ctrl+/` chat · `Ctrl+G` expand the brain · `Ctrl+J` the activity
ledger with per-row undo · `Ctrl+.` the no-sync lens · `Ctrl+N` quick capture · `Ctrl+;` the four
queues. Every binding has a clickable twin in the foot, because Chrome intermittently eats Ctrl+K and
Ctrl+G at the browser level. Rail slots `AG`, `CR` and `SY` are rendered **disabled rather than hidden**
— disabled beats hidden, the same doctrine the palette applies to its four disabled verbs.

27 endpoints, 21 palette verbs. Both are enumerated in `CONTEXT.md` §8 and §9.

---

## 9. The command line

```
sigma                       what needs your attention right now
sigma doctor                health check          --json --quiet
sigma fleet status|run      what ran / run what is due   --only KEY --all --dry-run
sigma reflect status|run    distil insights + proposals  --all --since --dry-run
sigma reflect apply         execute approved proposals
sigma reflect diff|merge    review, then land, a staged change
sigma capture status|sweep  session-logging self-check / log what the hook missed   --max N
sigma intake [run]          course material → notes      --course --dry-run --keep --max N
sigma devlog [run]          commits → the project hub's dev log   --project --dry-run --max N
sigma map [run]             a codebase → architecture notes       --project --dry-run --max N
sigma new "<description>"   scaffold a project           --dry-run
sigma todo                  the four priority queues     --all --json --date --no-persist
sigma review                score yesterday              --date --dry-run --status
                                                         --install-schedule --at
sigma install [what]        git hooks and scheduled tasks (all | hooks | schedules)
sigma ui                    start the interface          --port
```

A dispatcher, not a rewrite — each subcommand shells out to the script that already owns the job, so
there is one implementation of each behaviour. A group with no verb prints that group's *state* rather
than guessing at intent: `sigma fleet` shows status, it does not run anything, and bare `sigma intake`
reports while `sigma intake run` spends the window.

---

## 10. Current state

*Measured 2026-08-01, not recalled.*

| | |
|---|---|
| Code repo | `sigma-os`, private, **71 commits** |
| Vault repo | `sigma-vault`, private, **183 commits** |
| Notes | **174 on disk, 150 tracked** — the 24-note carve-out is the difference |
| Session logs | **31**, backlog 0 |
| Insights | **8** |
| Proposals | **39 raised** — 37 applied, 2 rejected, 0 pending, 0 staged. The folder was collapsed on 2026-08-01 into `06-System/proposals/ledger.md`, one row per proposal; full text stays recoverable from git |
| Skills | **4** — 3 user-scoped, 1 vault-scoped (`reflect`) |
| Reviews | **0** — the schedule is installed and `Ready` but has never fired |
| Code | 20 Python modules in `runtime/`, 8 in `interface/backend/`, 18 frontend modules |
| Tests | **358 across 19 suites**, green 2026-08-01. Stdlib `unittest`: `interface\backend\.venv\Scripts\python -m unittest discover -s tests -t tests` — the backend venv supplies `fastapi`/`httpx`, and `-t tests` is required because `tests/` is not a package. Budget 3–8 minutes; several suites shell out to real git. |
| Doctor | **8 of 9 checks OK**, one item waiting: the daily review has never run |

**Scheduled tasks — all installed, all `Ready`:**

| Task | Last run | Result |
|---|---|---|
| `SigmaOS-DailyFleet` | 2026-08-01 09:00:01 | `0` — *nothing due*, correctly |
| `SigmaOS-DailyReview` | never | installed 2026-08-01; first fire 06:00 tomorrow |
| `SigmaOS-WeeklyReflection` | 2026-07-26 09:00 | `1` — the `ENOTFOUND` failure, re-run by hand 07-27; next due 08-02 |

**The fleet runs on a schedule — verified.** `SigmaOS-DailyFleet` has fired at 09:00:0x with result `0`
on 07-29, 07-30, 07-31 and 08-01. What has **still** not happened is a specialist actually working
unattended: all four scheduled runs logged *nothing due*, and every real specialist run to date was
invoked by hand. Since the cadence fix that is now the *correct* answer rather than the bug it was —
the three specialists are weekly and each had been hand-run inside its window — but it means the
scheduled path has never been exercised end to end with real work in it. See gap 1.

---

## 11. Known gaps

**Open, in rough priority order:**

1. **No specialist has ever done real work unattended.** Every scheduled run so far found nothing due.
   The next natural test is 2026-08-04, when the coach comes due and nobody has hand-run it. Watch that
   one rather than assuming it.
2. **Scheduled tasks still hardcode interpreter paths.** Repointing them at `sigma` would leave one path
   per task instead of two, surviving a venv rebuild or a Python upgrade. Both shims are verified working
   when called by absolute path from an unrelated cwd. Deliberately deferred so a rewire never confuses a
   run being observed.
3. **`fleet.state.json` still carries the retired planner**, so the doctor reports *"4/3 specialists
   reporting"*. Cosmetic today, and exactly the kind of stale-state-inflates-a-count wart this project
   has been bitten by before.
4. **No doctor check for the paused fleet state** introduced in D4.
5. **Multi-turn chat continuity is wired but untested on long threads** (`session_id` round-trips).
6. **`06-System/proposed/` has no expiry.** A staged change ignored for months just sits there.
7. **The auditor and coach can propose contradictory fixes** to the same drift — as they did on
   2026-07-28, one proposing to amend the contract and two to amend the notes. That is a real decision
   for a human, but nothing flags that two proposals conflict.
8. **The interface is not packaged.** `sigma ui` runs uvicorn; Tauri would make it an app.
9. **The window meter is a proxy** — spend rows, call counts, and the last rate-limit event. No
   documented API exposes subscription headroom, so it never claims a percentage. Reservation is a fixed
   08:40–09:00 hold rather than a real budget.
10. **Nothing has been profiled past ~175 notes.** The auditor's inventory, the graph, the queue scan
    and the no-sync scan each walk every note; they are cached with TTLs and nothing more.
11. **The auditor's turn budget scales with the vault, not with the number of checks.** 24 stopped being
    enough somewhere between 157 and 175 notes; `inventory.py` fixed the cause, but 40 is a number that
    will need revisiting.

**Checked and deliberately not built** *(both were on D6's menu; each is a panel with nothing to show)*:
the **application pipeline**, because `02-Areas/Career/Applications/` is empty and the tracker has
reported "pipeline is clear" every week since it started; and **quick reference**, because
`04-Resources/` holds one file. Exam mode's third question — *what did you get wrong last time* — is
absent for a stronger reason: nothing in the vault records a wrong answer, so the panel would be a
confident guess wearing the costume of data, and the view says so out loud rather than quietly omitting
it. The larger unbuilt list (agent control, browser and Google Suite, deep research, résumé tailoring,
phone access) lives in `CONTEXT.md` §16.

**Accepted, not bugs:**

- `CLAUDE.md` has no frontmatter, deliberately — it is the contract, read as instructions.
- The carved-out files live on one disk only, by design: excluded from the vault repo, so no off-machine
  backup. The Ctrl+. lens now gives the exact list; the backup itself is unbuilt.
- The employer's name and task titles remain in daily notes and MOCs; only those 24 files were carved out.
- Reverting a toggle on a note younger than one obsidian-git backup cycle deletes the file — the commit
  being reverted is the file's *creation*. Git-exact, recoverable, self-limiting.
- `SYSTEM.md` and `CONTEXT.md` both describe this system and nothing mechanically keeps them in step.
  Same for the vault twin of `CONTEXT.md`. This is the parallel-list failure the privacy model refuses
  to make; here it is accepted because the audience differs, and the mitigation is the date at the top
  of each file.

---

## 12. The recurring failure mode

Worth stating plainly, because it has now happened eight times and will happen again:

> **"Configured" and "executes" are different claims, and only the second one matters.**

1. `SessionEnd` was wired and never fired — it only runs on a clean exit.
2. The hook's interpreter path was unquoted (`C:\Python314\python.exe`), and Claude Code runs hooks
   through a POSIX shell where `\P` collapses to `P`. The hook died before Python started.
3. The privacy callback was shadowed by `allowed_tools`, then skipped by `permission_mode`. Both runs
   reported *zero denials* — zero because nothing had been denied, not because nothing needed denying.
4. The fleet's per-specialist timeout was declared and caught but never *applied* — `wait_for` was never
   called, so the `except` was unreachable and a hung specialist would have blocked the daily run
   indefinitely while holding the lock.
5. A logged-out CLI fails silently. Nothing checked it until the auth check was added.
6. On Windows, stdout is cp1252 whenever redirected — which is every scheduled task. cp1252 *contains*
   the em-dash, which is why this hid for weeks while the fleet logged model summaries full of them. It
   does not contain `→` or emoji. One arrow in a specialist's summary would have killed the daily run
   with its output going nowhere.
7. **`is_due()` compared rolling hours, not calendar days.** Three consecutive 09:00 runs did nothing and
   each reported success. No self-check would ever have caught it; only the calendar could tell a green
   run from a correct one.
8. **A counter counted the wrong thing.** The fleet reported "2 proposals" when one file existed — it was
   counting tool *calls*, including refused ones. The same defect this project had already fixed once in
   `--dry-run`, which wrote *"logged 3"* into the failure log while writing nothing.

Every one looked like success from the outside. The countermeasures are the same each time: run the
thing rather than reading it, test the adversarial case rather than the happy path, make the watchdog
report what a resolver can *see* rather than that it ran, and count what appeared on disk rather than
what was attempted.

Three corollaries earned the same way:

- **A fix that lives in a module only protects the files that import it.** `cli.py` did not import
  `sigma`, so it missed the UTF-8 reconfiguration and mangled the em-dash in its own `--help` text.
- **A sealed test has to try to leak.** The Windows-newline bug in the batch `check-ignore` call was
  caught by an adversarial check (*procertus-interface must not appear*), not by anything passing.
- **The first real run finds what reasoning did not.** Intake's first live run exposed two defects in the
  write layer and one environment failure. The no-sync marker moved from ▦ to ⊘ only because live
  rendering showed `🏁` falling back to a near-identical hatched box — and a confidentiality mark that
  tofu can imitate is worse than none.

---

## 13. Where things live

| | |
|---|---|
| Vault | `C:\Users\tusha\Documents\Obsidian Vault` |
| This repo | `C:\Users\tusha\Documents\CS Projects\sigma-os` |
| Other repos | `C:\Users\tusha\Documents\CS Projects\` — never inside the vault |
| Transcripts (L0) | `C:\Users\tusha\.claude\projects\` |
| Hooks config | `C:\Users\tusha\.claude\settings.json` |
| User skills | `C:\Users\tusha\.claude\skills\` |
| Machine-local state | `runtime/*.config.json`, `*.state.json`, `*.progress.json`, `*.log`, `*.lock`, `*.jsonl` — all gitignored |
| GitHub | `zacharyli293680-bot/sigma-os`, `zacharyli293680-bot/sigma-vault` — both private |

Config, state and logs stay behind `.gitignore`: they carry the vault path, the privacy denylist (which
names the very client it exists to hide), the model-exemption list, and private working directories. The
repo ships `.example` files instead.

**The vault is the design record**, and it is the more complete one — this file summarises; the vault
explains *why* each decision went the way it did.

- `03-Projects/sigma-os.md` — the hub: live phase table, architecture map, and the full dev log, which
  is the single best account of how this got here.
- `03-Projects/sigma-os/` — 17 notes, one per concept: `three-systems`, `memory-layers`,
  `vault-as-memory-substrate`, `session-logger`, `reflection-loop`, `watchdog`, `interface`, `fleet`,
  `command-line`, `agent-guardrails`, `repo-and-privacy-model`, `subscription-only`, `open-decisions`,
  `dashboard-vision`, `dashboard-plan`, `sigma-os-plan` (the original blueprint, kept as a record), and
  `sigma-os-reference` (the vault twin of `CONTEXT.md`).
- `06-System/system.md` — the live Dataview dashboards over sessions, insights and proposals.
