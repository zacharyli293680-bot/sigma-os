# Sigma — system state

*What exists, how it works, and what it does not do yet. Written 2026-07-29.*

[`README.md`](README.md) is the introduction. This is the full picture: every component, how the
pieces reach each other, the guarantees and how they are enforced, and the gaps. If you are picking
this up after a gap, read this first.

---

## 1. What Sigma is

An agentic OS with an **Obsidian vault as its memory substrate**. Agents perceive state (frontmatter,
checkboxes, dates), reason, act (draft notes, plans, reports), and remember (git history) — with the
vault co-writable by human and agents alike.

Three systems, deliberately separate:

| | What | Where |
|---|---|---|
| **Knowledge Vault** | Zach's facts — coursework, projects, internship, applications | `C:\Users\tusha\Documents\Obsidian Vault` |
| **Agent Memory** | Sigma's record of *its own* work | `06-System/` inside that vault |
| **OS Application** | The engine | this repo |

The vault works as agent memory because it is human/machine co-writable, its frontmatter contract is
a queryable API, git is temporal memory, and wikilinks are a retrieval graph.

**One credential.** Sigma has no `ANTHROPIC_API_KEY` and no `.env`. Both paths to a model end at the
same Claude Code CLI riding this machine's subscription login: `claude -p` for Phases 1–2 and the
watchdog, and the Claude Agent SDK (which *is* Claude Code packaged as a library, spawning that same
binary) for Phases 3–4. Verified on both paths with the key explicitly unset.

The consequence shapes everything downstream: **the budget is a rate-limit window, not an invoice.**
Nothing is billed per token, so the question for the fleet is not *what does this cost* but *how many
agents can run before the window is spent*. That is why specialists are sequenced rather than fanned
out, and why model tiering (Haiku vs Sonnet) exists for headroom rather than for money.

---

## 2. Memory model

Five layers, raw → distilled:

| Layer | What | Where | Written by |
|---|---|---|---|
| **L0** | Raw transcripts | `~/.claude/projects/**/*.jsonl` | Claude Code |
| **L1** | Session logs — one per session | `06-System/sessions/` | `session_logger.py` (Phase 1) |
| **L2** | Knowledge notes | the vault at large | Zach, and proposals |
| **L3** | Procedural memory — skills | `~/.claude/skills/`, `<vault>/.claude/skills/` | approved proposals |
| **L4** | Insights — durable lessons | `06-System/insights/` | `reflect.py` (Phase 2) |

L0 is treated as opaque: the JSONL schema is internal to Claude Code and shifts between versions, so
Sigma **summarises it, never parses it** as a contract.

---

## 3. Phases — all built

| Phase | What | Status |
|---|---|---|
| **0 — Foundation** | Structured vault, `CLAUDE.md` contract, course study systems | ✅ done |
| **1 — Session logging** | `SessionEnd` hook + `SessionStart` sweep + Haiku summariser → L1 notes | ✅ built 2026-07-23, capture fixed 2026-07-24 |
| **2 — Reflection & skills** | Weekly reflection → insights + proposals, propose-and-approve | ✅ built 2026-07-24 |
| **2.5 — Watchdog** | `doctor.py` on `SessionStart` — reports Sigma's health into every session | ✅ built 2026-07-27 |
| **3 — Interface** | Local web app: Agent SDK backend + React frontend | ✅ built 2026-07-27, completed 2026-07-28 |
| **4 — Specialist fleet** | planner / coach / auditor / tracker, sequenced under one window | ✅ built 2026-07-28 |
| **CLI** | `sigma` — one front door over all of it | ✅ built 2026-07-29 |

Phase 2.5 was not in either plan. It exists because both shipped phases were found **dead** on
2026-07-27 — Phase 1 had captured nothing for three days, Phase 2's first scheduled run had failed on
`ENOTFOUND` with the next attempt a week out — while both `--status` self-checks reported green and
were *correct*. Nothing had run them.

---

## 4. Components

```
sigma.cmd / sigma       front door → runtime/cli.py
runtime/
  sigma/__init__.py     shared core: settings precedence, frontmatter, kebab, `claude -p`,
                        project resolution, JSON state files, UTF-8 output
  cli.py                the `sigma` command — dispatcher, picks the interpreter
  session_logger.py     Phase 1 — transcript → session log
  reflect.py            Phase 2 — logs → insights + proposals; --apply / --diff / --merge
  doctor.py             Phase 2.5 — seven health checks, exit code always 0
  inventory.py          the vault's frontmatter, precomputed for the auditor
  fleet.py              Phase 4 — runs specialists one at a time under a lock
  specialists.py        who the four specialists are and what each is briefed to do
  install_hooks.py      copies the pre-push guard into each repo
  hooks/pre-push        refuses a push where a tracked file is also gitignored
interface/
  backend/app.py        FastAPI: /api/ask (SSE), /api/health, serves the built frontend
  backend/agent.py      Agent SDK options — the single guarded construction both consumers use
  backend/privacy.py    the model boundary
  backend/propose.py    the only way an agent affects disk
  frontend/             React + Vite
tools/convert_pdfs.py   vault utility, not part of Sigma
```

**Two interpreters.** `fleet.py` and the interface need the Agent SDK, which lives in
`interface/backend/.venv`. Everything else is pure stdlib. That venv is a superset, so `cli.py`
prefers it for every subcommand and falls back to system Python. `fleet` imports the SDK *lazily*,
so `fleet status` and `fleet run --dry-run` work without it.

---

## 5. How the pieces run

### Automatically

| Trigger | Runs | Purpose |
|---|---|---|
| `SessionEnd` hook | `session_logger.py` | Log the session that just ended |
| `SessionStart` hook | `session_logger.py --sweep --detach` | Catch sessions the above missed |
| `SessionStart` hook | `doctor.py --quiet` | Put Sigma's health in the session's context |
| Scheduled, Sundays 09:00 | `reflect.py` | Distil the week into insights + proposals |
| Scheduled, daily 09:00 | `fleet.py` | Run the specialists that are due |

`SessionEnd` only fires on a *clean* exit — closing the terminal skips it. That cost three days of
capture once. The `SessionStart` sweep is the safety net, so capture is **eventually consistent**: a
session missed today is picked up on the next launch.

Both scheduled tasks carry `RestartCount 3`, added after one network blip cost a week of learning.

### The daily fleet, in order

`fleet.py` takes a lock, then runs each due specialist **to completion before starting the next**:

| Order | Specialist | Cadence | Model | Brief |
|---|---|---|---|---|
| 10 | **planner** | daily | Sonnet | Today's plan from `Home.md`'s live queries |
| 30 | **coach** | weekly | Sonnet | Plan-vs-date drift in course timelines |
| 40 | **auditor** | weekly | Haiku | Frontmatter against the contract |
| 60 | **tracker** | weekly | Haiku | Stale entries in the job pipeline |

The planner is first because it is the one with a time of day attached: a plan that lands at 09:20
because three other agents went first is a plan for a morning that already started.

**There is no concurrency option**, deliberately — an option is a constraint you have already decided
to break. A stale lock (>2h) is taken over rather than obeyed, so a crashed run cannot wedge the
fleet forever. Hitting a rate limit **stops** the run; the remaining specialists stay due and go on
the next invocation rather than burning the rest of the window on retries.

Each specialist has a 420s timeout, applied via `asyncio.wait_for`.

---

## 6. The guarantees, and how each is enforced

These are mechanical, not promised. Every one of them exists because something failed.

### Propose, never apply

No agent writes to the vault. The interface and every specialist get **`propose_change`**, an
in-process MCP tool taking structured fields — the *backend* writes the proposal note. The agent holds
no filesystem write primitive at all, so "cannot apply its own changes" is a fact about which tools
exist rather than a rule it might route around.

The alternative — a scoped `Write` fenced by a path check — is the bet that already lost twice here.

Proposals land in `06-System/proposals/` as `status: pending`. Zach edits the content block if he
wants it different, sets `status: approved`, and `sigma reflect apply` executes it. **One approval
path in the whole OS**, whether the proposal came from the weekly reflection, a chat, or a specialist.

### Apply is additive; corrections are staged

`--apply` creates files that do not exist and appends to the contract. It **never overwrites**. That
guarantee earned its keep: it is the only reason a Haiku-drafted `MAP.md` did not flatten a
hand-built one.

But correcting existing notes is the entire job of two specialists, so a proposal whose target exists
is **staged** rather than skipped: the proposed version is written to
`06-System/proposed/<target's path>`, recorded in `staged:`, and left `approved`.

```
sigma reflect diff            git diff of each staged change vs its target
sigma reflect merge NAME      copy one over its target, mark it applied
```

`merge` is **the one place Sigma overwrites a note**, and it is human-only by construction: one
explicit name, refuses anything not already approved *and* staged, no bulk mode, and no scheduled job
calls it. Editing the staged file before merging is how a change is accepted *partly*. Git holds the
previous version.

The mirror path matters: a `timeline.proposed.md` beside the real one would carry its `type:`
frontmatter and appear in Dataview as a phantom second note.

### Privacy — one rule, two boundaries

> **If git will not sync it, the model does not see it.**

`.gitignore` already declares the carve-out: the ProCertus area, the Interface project folder and its
hub, and seven named session logs — **24 files rewritten out of git history** on 2026-07-25
(including the root commit), gitignored, and restored to disk. Today that is 13 ignore entries
covering **18 files** present on disk and absent from the repo.

The `pre-push` hook enforces it at the *push* boundary; `privacy.py` enforces the same declaration at
the *model* boundary. One statement, no parallel list to drift.

The precise invariant the guard checks is **a file may not be both tracked and gitignored** — one
rule covering every carved-out path, self-maintaining, and zero false positives. Grepping a diff for
the client's name was the first attempt and flagged two files that legitimately mention it: Sigma's
own insight about the carve-out, and the proposal that asked for the grep.

Enforcement is a **`PreToolUse` hook**, not the permission callback. That distinction was learned
expensively: `can_use_tool` is only consulted for calls that would otherwise prompt, and in
`permission_mode="default"` Claude Code treats Read/Grep/Glob as safe and never prompts — so the
callback was never reached, no error was raised, and the run reported *zero denials*. Twice that
looked exactly like a guard working. Hooks fire for every tool call regardless of mode.

`VaultPrivacy` is constructed `allow_writes=False` **unconditionally**; the proposals flag does not
reach it. Two switches, because "may propose" and "may edit a note" must never be the same one. Fails
closed: if `git check-ignore` errors, the path is refused.

### Never deletes, never checks your boxes

Agents observe freely, write additively, and never tick a checkbox on Zach's behalf.

---

## 7. The watchdog

`doctor.py` runs on every `SessionStart` and speaks only when something is wrong. Its exit code is
**always 0** — a broken watchdog must not block a session from starting.

Six checks:

1. **capture** — is any finished session still unlogged?
2. **reflection** — did the weekly loop run, and is anything waiting on Zach?
3. **schedule** — did the scheduled task fire *and succeed*? (It can fire and fail.)
4. **auth** — is the login still live?
5. **privacy** — is anything gitignored also tracked, and is the pre-push guard installed?
6. **fleet** — per *specialist*: any failing, overdue by >2 cycles, or a run that stopped on a limit?

Two design rules it follows:

- **It is not a fourth source of truth.** Facts come from the tools that own them —
  `session_logger.capture_candidates()`, `reflect.load_state()`, `fleet.load_state()`. A watchdog
  with its own private copy of "is capture healthy?" is just another thing that can disagree.
- **It does not cry wolf.** A backlog the concurrent sweep is already clearing is not an alert. A
  failed scheduled run stops nagging once re-run by hand. A network blip is never reported as an
  expired login.

The auth check is the subtle one: the only honest test is a real model call, but the budget *is* the
rate-limit window — so a good result is cached for 12h and only failures re-probe. A failing probe is
free, since it errors before a call is spent.

---

## 8. The interface (Phase 3)

`sigma ui` → one process on `127.0.0.1:8787` serving both the API and the built frontend.

- **Reads the vault live** — no index to rebuild, so answers reflect what is on disk now.
- **Cites by `[[wikilink]]`**, rendered as `obsidian://` links. That is the difference between an
  answer and a copy of one.
- **Streams tokens** over SSE, with a collapsible trail of every lookup.
- **Surfaces the watchdog** — `/api/health` runs `doctor.collect()`.
- **Proposes, never edits.**

Local-first and unauthenticated on purpose: it binds to loopback, reads a vault on this disk, and
inherits a *machine* login — so the machine is the natural boundary. Do not expose it.

---

## 9. The command line

```
sigma                     what needs your attention right now
sigma doctor              health check          --json --quiet
sigma fleet status        what ran, when, what it raised
sigma fleet run           specialists that are due   --only KEY --all --dry-run
sigma reflect run         distil insights + proposals   --all --since --dry-run
sigma reflect apply       execute approved proposals
sigma reflect diff        review staged changes
sigma reflect merge NAME  apply one staged change
sigma capture status      session-logging self-check + backlog
sigma capture sweep       log any session the hook missed   --max N
sigma install [what]      git hooks and scheduled tasks (all | hooks | schedules)
sigma ui                  start the interface   --port
```

A dispatcher, not a rewrite — each subcommand shells out to the script that owns the job, so there is
one implementation of each behaviour. A group with no verb prints that group's *state* rather than
guessing at intent: `sigma fleet` shows status, it does not run anything.

---

## 10. Current state

| | |
|---|---|
*(Refreshed 2026-07-31 during the Phase 5 readiness check.)*

| Code repo | `sigma-os`, private, 34 commits |
| Vault repo | `sigma-vault`, private, 105 commits, 140 tracked notes (165 on disk — the carve-out) |
| Session logs | 27, backlog 0 |
| Insights | 8 |
| Proposals | 13 — 11 applied, 2 rejected, 0 pending, 0 staged |
| Skills | 3 user-scoped, 1 vault-scoped (`reflect`) |
| Doctor | `all clear` on all **seven** checks (backup added 2026-07-31); fleet 4/4 |
| Tests | **98 across eleven committed suites in `tests/`, green 2026-07-31** (41 + 11 Phase 5 boundary + 20 study intake incl. slides + 6 backup check + 6 fleet cadence + 12 inventory). Stdlib `unittest`, no pytest: `interface\backend\.venv\Scripts\python -m unittest discover -s tests -t tests`. Both halves matter — the backend venv supplies `fastapi`/`httpx`, and `-t tests` is required because `tests/` is not a package (a bare `discover` dies on *"Start directory is not importable"*). |

**Both scheduled tasks are installed and `Ready`.** The weekly reflection's 2026-07-26 run failed on
`ENOTFOUND` and was re-run by hand; it has not been due since, and `LastTaskResult` is still `1` from
that failure. Next 2026-08-02 — worth watching rather than assuming.

**The fleet runs on a schedule — verified.** `SigmaOS-DailyFleet` fired 2026-07-29, 07-30 and 07-31,
each at 09:00:0x with result `0`. What has *not* happened unattended is a specialist actually
working: all three scheduled runs logged `nothing due`, and every real specialist run to date was
invoked by hand. See gap 1 for why, which is a live design question rather than a bug.

---

## 11. Known gaps

**Not yet done, in rough priority order:**

1. ~~**A hand-run silently suppresses the next morning's scheduled run.**~~ **Fixed 2026-07-31.**
   `is_due()` now compares calendar days rather than a rolling `cadence_days × 24 − 1` hours, so an
   afternoon hand-run no longer costs the next morning's plan. Six tests pin it.
   *(Kept for the lesson, twice over. This entry first read "the fleet's first scheduled run has not
   happened — watch it." Watching it is what surfaced the cadence bug, which no self-check would ever
   have reported: three consecutive 09:00 runs did nothing and each reported success. A green run and
   a correct run are not the same thing, and only the calendar could tell them apart.)*
2. **Scheduled tasks still hardcode interpreter paths.** Repointing them at `sigma` would leave one
   path per task instead of two, surviving a venv rebuild or Python upgrade. Both shims are verified
   working when called by absolute path from an unrelated cwd. Deliberately deferred until after (1),
   so a rewire does not confuse the run being observed.
3. **Multi-turn continuity is wired but untested on long threads** (`session_id` round-trips).
4. **The interface is not packaged.** `sigma ui` runs uvicorn; Tauri would make it an app.
5. **`06-System/proposed/` has no expiry.** A staged change ignored for months just sits there.
6. **The auditor and coach can propose contradictory fixes** to the same drift — as they did on
   2026-07-28, one proposing to amend the contract and two to amend the notes. That is a real
   decision for a human, but nothing flags that two proposals conflict.
7. ~~**The test suites exist only as history.**~~ **Closed.** `tests/` is committed — five suites,
   41 tests, green on 2026-07-31 (see §10 for the exact command, which is the part that was
   genuinely missing: the suite was runnable all along and nothing recorded how).

8. ~~**The auditor cannot finish a run.**~~ **Fixed 2026-07-31** — `runtime/inventory.py`
   precomputes the scan and hands it over in the brief. Measured against the three failures below:
   **`ok in 123.3s`**, first clean completion. `Specialist.context` is the general hook (any
   specialist whose expensive part is gathering rather than judging can use it); the inventory
   reports facts only and never judges them, because which `type` is legal lives in `CLAUDE.md` and
   a second copy in Python is the drift this vault has already paid for once. The diagnosis, kept
   because two earlier fixes were wrong:
   Three runs on 2026-07-31, all `error_max_turns`: 24 turns/1 proposal/138.7s, then 40 turns/3
   proposals/139.6s, then 40 turns/**0 proposals**/129.2s. The third is the diagnostic one — with no
   drift left to find it still exhausted 40 turns, so the *search* alone does not fit. That also
   rules out "tell it to stop after the first finding": there was nothing to stop at. Per-turn cost
   differs (5.8s when drafting a proposal, 3.2s when only grepping), which is why the wall-clock
   times look suspiciously alike and are not evidence of a hidden time limit — `max_turns` does
   reach the SDK (`fleet.py` → `build_options` → `ClaudeAgentOptions`).
   The brief asks Haiku to sweep 175 notes across five classes of check by Grep. The fix is probably
   not a bigger number but a cheaper search: **precompute the frontmatter inventory in script code
   and hand it to the model**, the way intake hands over extracted text instead of making the model
   read PDFs. That turns ~40 search turns into zero and leaves the model doing the part it is
   actually for — judging a schema against its notes. Not attempted yet; two guesses were already
   wrong here, and this one deserves measuring rather than assuming.
   *Consequence while it stands:* the auditor still finds real drift (it caught a contract rule
   twenty minutes old), but the fleet applies proposals only from successful runs, so its findings
   never land unattended.

**Accepted, not bugs:**

- `CLAUDE.md` has no frontmatter, deliberately — it is the contract, read as instructions.
- The carved-out ProCertus files live on one disk only, by design. They are excluded from the vault
  repo, so they have no off-machine backup. *(Open task, dated 2026-08-01.)*
- The employer's name and task titles remain in daily notes and MOCs; only the 24 files were carved
  out.

---

## 12. The recurring failure mode

Worth stating plainly, because it has now happened five times and will happen again:

> **"Configured" and "executes" are different claims, and only the second one matters.**

1. `SessionEnd` was wired and never fired — it only runs on a clean exit.
2. The hook's interpreter path was unquoted (`C:\Python314\python.exe`), and Claude Code runs hooks
   through a POSIX shell where `\P` collapses to `P`. The hook died before Python started.
3. The privacy callback was shadowed by `allowed_tools`, then skipped by `permission_mode`. Both runs
   reported *zero denials* — zero because nothing had been denied, not because nothing needed denying.
4. The fleet's per-specialist timeout was declared and caught but never *applied* — `wait_for` was
   never called, so the `except` was unreachable and a hung specialist would have blocked the daily
   run indefinitely while holding the lock.
5. A logged-out CLI fails silently. Nothing checked it until the auth check was added.

Every one looked like success from the outside. The countermeasures are the same each time: run the
thing rather than reading it, test the adversarial case rather than the happy path, and make the
watchdog report what a resolver can *see* rather than that it ran.

A sixth, of a different shape, is worth adding: on Windows, stdout is cp1252 whenever redirected —
which is every scheduled task. cp1252 *contains* the em-dash, which is why this hid for weeks while
the fleet logged model summaries full of them. It does not contain `→` or emoji. One arrow in a
specialist's summary would have killed the daily run with its output going nowhere.

---

## 13. Where things live

| | |
|---|---|
| Vault | `C:\Users\tusha\Documents\Obsidian Vault` |
| This repo | `C:\Users\tusha\Documents\CS Projects\sigma-os` |
| Transcripts (L0) | `C:\Users\tusha\.claude\projects\` |
| Hooks config | `C:\Users\tusha\.claude\settings.json` |
| User skills | `C:\Users\tusha\.claude\skills\` |
| Machine-local state | `runtime/*.state.json`, `*.log`, `*.lock` — all gitignored |
| GitHub | `zacharyli293680-bot/sigma-os`, `zacharyli293680-bot/sigma-vault` — both private |

Config, state and logs stay behind `.gitignore`: they carry the vault path, the privacy denylist
(which names the very client it exists to hide), and private working directories. The repo ships
`.example` files instead.

**The vault is the design record**, and it is the more complete one — this file summarises; the vault
explains *why* each decision went the way it did.

- `03-Projects/sigma-os.md` — the hub: live phase table, architecture map, and the full dev log,
  which is the single best account of how this got here.
- `03-Projects/sigma-os-plan.md` — the original design doc, kept as a record rather than a status
  board. Two of its five predicted problems were answered *by construction* (there is no API key to
  leak, and nothing is billed per token).
- `03-Projects/sigma-os/` — 13 atomic notes, one per concept: `three-systems`, `memory-layers`,
  `vault-as-memory-substrate`, `session-logger`, `reflection-loop`, `watchdog`, `interface`,
  `fleet`, `command-line`, `agent-guardrails`, `repo-and-privacy-model`, `subscription-only`,
  `open-decisions`.
- `06-System/system.md` — the live dashboards over sessions, insights and proposals.
