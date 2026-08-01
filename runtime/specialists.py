#!/usr/bin/env python3
"""
specialists.py — who is in Sigma's fleet, and what each one is for.

Kept apart from `fleet.py` (which decides *when* they run) because these are the
part worth editing. A specialist is a name, a cadence, a model tier, and a brief.
Adding one should not mean touching the runner.

Every brief inherits the same three rules from the runner, so none of them
restate it: read the vault, propose changes through `propose_change`, never
claim a change was made. What each brief adds is *what to look at and what a
good proposal from this specialist looks like*.

**Model tiering is for window headroom, not money.** Nothing here is billed per
token — the ceiling is a rate-limit window (see the vault's `subscription-only`
note). Haiku for the two that mostly pattern-match over frontmatter, Sonnet for
the two that have to reason about a plan.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Specialist:
    key: str
    title: str
    cadence: str            # daily | weekly — advisory; the schedule decides
    model: str
    effort: str
    brief: str
    max_turns: int = 24
    # Runs in this order. Order 10 is deliberately vacant: it belonged to the
    # planner, which was first because it was the only specialist with a time of
    # day attached to it. Nothing here is time-critical any more.
    order: int = 50
    tags: tuple = field(default_factory=tuple)
    # Optional zero-argument callable returning extra brief text, computed at
    # run time and appended to `brief`. For work whose expensive part is
    # *gathering* rather than *judging*: a deterministic scan costs one function
    # call, where making the model rebuild the same picture by Grep cost the
    # auditor its entire turn budget three runs running. Must never raise — the
    # caller degrades to the plain brief.
    context: object = None


# The planner was here: a daily Sonnet run that rewrote `01-Daily/YYYY-MM-DD.md`
# whole with a short ordered plan built from Home.md's live queries.
#
# It was retired on 2026-08-01 when the todo list became four self-maintaining
# priority queues. A daily rebuild only earns its cost if the list cannot
# maintain itself, and the queue promotes the next task the moment one pops —
# so the plan the planner wrote each morning had become a restatement of what
# the WORK view already showed, in a note nobody read twice.
#
# Its output outlived it in an unwelcome way: seven daily notes full of
# generated checkboxes that made Misc a seven-times-over copy of the other
# three queues. That is why `todo.py` excludes `01-Daily/` outright.
#
# What replaced it is not another planner but `retro.py` — 06:00, about
# yesterday rather than today, and its number is arithmetic rather than
# judgement. Planning forward was the thing that stopped needing a model;
# noticing what did not move still does.

COACH = Specialist(
    key="coach", title="Study coach", cadence="weekly",
    model="sonnet", effort="medium", order=30,
    brief="""
Check whether the course timelines still describe reality.

Look at the `timeline` notes under `02-Areas/Academics/*/` and the course-index
notes beside them. You are looking for **drift between the plan and the date**:
blocks whose dates have passed with work unstarted, milestones scheduled after
the exam they prepare for, a course marked `active` with a timeline that ended.

Prefer one specific, well-evidenced rebalance over a general observation. Cite
the exact block or line you are reacting to.

Propose a `note` targeting the timeline you would rewrite, with the complete
updated file as content. If the problem is a single wrong date, say that in the
rationale and keep the change minimal — a rewrite that also reflows everything
else is harder to approve than a one-line fix.

Nothing drifting? Say so and propose nothing.
""".strip())

AUDITOR = Specialist(
    key="auditor", title="Contract auditor", cadence="weekly",
    model="haiku", effort="medium", order=40,
    # 24 (the default) stopped being enough somewhere between 157 and 175 notes.
    # On 2026-07-31 the auditor found real drift, wrote a correct proposal for
    # it, and then died on error_max_turns before finishing — and because the
    # fleet applies proposals only from a *successful* run, that finding sat
    # unapplied while the specialist reported FAILED. The work was good; it ran
    # out of room to finish saying so. This budget has to scale with the vault,
    # which grows, not with the number of checks, which does not.
    max_turns=40,
    context=lambda: __import__("inventory").for_auditor(),
    brief="""
Police drift from the vault contract. `CLAUDE.md` is the contract; you enforce it.

**Read `CLAUDE.md`, then work from the inventory printed below it.** The
inventory is a complete, current, deterministic scan of every note's
frontmatter, plus which notes each course index does and does not link. You do
not need to find any of that — it is already found. Reading the contract and
then reasoning over the inventory should take a handful of turns, and going
looking for what you have already been given is what made three earlier runs of
this brief die before they could report anything.

Read individual notes only to confirm a specific suspicion the inventory raises,
never to survey.

What counts as drift:
- notes whose `type` does not match the schema for their folder, or that are
  missing required fields for their type
- a `type` value that is not in the contract at all
- assignments/exams whose `status` contradicts their dates (e.g. `status: todo`
  with a `due` well in the past)
- index/manifest notes that no longer list files that exist beside them

Report the **most consequential** drift you find, not an exhaustive list. One
proposal fixing a real inconsistency is worth more than twenty cosmetic ones.

Propose a `note` targeting the file to correct, with the complete corrected file
as content. If the drift is in `CLAUDE.md` itself — the contract describing
something the vault no longer does — propose kind `contract` instead.

**A `contract` proposal is appended to `CLAUDE.md`, not merged into it.** So its
content must be *new, self-contained material under a heading the contract does
not already have*. Do not restate or re-open an existing section: that gives the
contract two copies of it. If what you want is an edit to an existing section,
say so in the rationale and keep the content to just the new lines.

A clean audit is a real result. Say "no drift found" and propose nothing.
""".strip())

TRACKER = Specialist(
    key="tracker", title="Application tracker", cadence="weekly",
    model="haiku", effort="low", order=60,
    brief="""
Keep the job pipeline honest.

Read `02-Areas/Career/Applications/` and the `job-search` MOC. You are looking
for applications that have gone stale: `status: applied` with an `applied` date
weeks old and no movement, an `oa` or `interview` status with a date that has
passed, or a note whose frontmatter no longer matches what its body says.

If the Applications folder is empty, that is the answer — say the pipeline is
empty and propose nothing. Do not invent applications, and do not propose
creating placeholder notes for jobs that were never applied to.

Otherwise propose a `note` targeting the application to update, with the complete
corrected file as content, and say in the rationale what specifically went stale.
""".strip())


FLEET = (COACH, AUDITOR, TRACKER)
BY_KEY = {s.key: s for s in FLEET}


def in_run_order(keys=None):
    """The specialists to run, in the order they must run in."""
    chosen = FLEET if not keys else tuple(BY_KEY[k] for k in keys if k in BY_KEY)
    return tuple(sorted(chosen, key=lambda s: (s.order, s.key)))
