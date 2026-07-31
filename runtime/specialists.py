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
    # Runs in this order. The planner is first because it is the one with a time
    # of day attached to it: a plan that lands at 09:20 because three other
    # agents went first is a plan for a morning that already started.
    order: int = 50
    tags: tuple = field(default_factory=tuple)


PLANNER = Specialist(
    key="planner", title="Daily planner", cadence="daily",
    model="sonnet", effort="medium", order=10,
    brief="""
Build today's plan.

Read `Home.md` for the live queries, then the notes behind whatever they surface:
open assignments, upcoming exams, active project hubs, and today's daily note in
`01-Daily/` if it exists.

A good plan from you is **short and ordered, and it says why**. Lead with what is
genuinely due or at risk today, not everything outstanding. Three well-chosen
items beat a list of twelve. If something is due today and nothing has been
started, say so plainly.

Propose a `note` targeting today's daily note path (`01-Daily/YYYY-MM-DD.md`,
using the date from the vault's most recent daily note or today's date). Content
must be the **complete note**, following the `daily` frontmatter schema in
`CLAUDE.md`. If today's daily note already exists, your proposal should be the
updated whole file — the apply step writes files, it does not merge.

If the day genuinely has nothing pressing, propose nothing and say so. A plan
invented to justify running is worse than no plan.
""".strip())

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
    brief="""
Police drift from the vault contract. `CLAUDE.md` is the contract; you enforce it.

Check, by Grep over frontmatter rather than by reading every note:
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


FLEET = (PLANNER, COACH, AUDITOR, TRACKER)
BY_KEY = {s.key: s for s in FLEET}


def in_run_order(keys=None):
    """The specialists to run, in the order they must run in."""
    chosen = FLEET if not keys else tuple(BY_KEY[k] for k in keys if k in BY_KEY)
    return tuple(sorted(chosen, key=lambda s: (s.order, s.key)))
