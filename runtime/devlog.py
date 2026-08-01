#!/usr/bin/env python3
"""
devlog.py — write a project's recent work into its hub note (dashboard-plan Phase 6).

`dashboard-vision`: "Dev-log a session into the right project hub without writing
it up by hand."

    C:\\Users\\tusha\\Documents\\CS Projects\\Focus Log   (12 commits since the last entry)
        -> 03-Projects/focus-log.md  ## Dev log
           - **2026-07-31** — Added the graphify knowledge-graph tooling … _(through `93aa383`)_

**No path argument, and no session argument.** The palette's security property is
that a verb is a dictionary key and nothing user-supplied ever reaches a command
line (dashboard-plan Phase 3). Study intake solved that with a drop folder; here
the equivalent is already sitting there — the set of project hubs that declare a
`repo:`, and the commits in those repos that no dev log entry covers yet. The
input is discovered, never named.

**The hub note is the state.** There is no `devlog.state.json` recording what was
already written up: each entry ends with the sha it covered through, so the note
itself says where the next run should start. That is the whole thesis of this OS —
the vault is the memory substrate — and it means the bookkeeping survives a wiped
machine, syncs with the note, and is legible to a human reading the log.

**Why this one vets before applying.** Every previous auto-applied proposal
*created* a note. This is the first that routinely **updates** a note Zach wrote
by hand, and the model has to hand back the whole file to do it — so a dropped
section would be a silent loss of his writing, not a bad new note he can delete.
`_vet` is script code, not judgement: the proposal is refused unless every H2
heading, every existing dev log entry, and the `repo:` line come back intact and
the note did not get shorter. A refused proposal stays pending, so nothing is
lost either way.

**Writes.** None, directly — `propose_change` then `applier.py`, exactly as the
fleet and study intake do. One revertible commit per entry, in the ledger.
"""
import argparse
import asyncio
import datetime
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import reflect as rf                                       # noqa: E402
import specialists as sp                                   # noqa: E402
from sigma import PROJECT_HUB_GLOBS, frontmatter, make_logger   # noqa: E402
from sigma import gitops                                   # noqa: E402

VAULT = rf.VAULT
SESSIONS = VAULT / "06-System" / "sessions"

# The git well-known empty tree. Diffing against it gives the full contents of a
# repo whose entire history is being logged for the first time — `first..HEAD`
# would silently exclude everything the initial commit brought in, which for a
# one-commit repo is the entire project.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# A first-ever entry on a long-lived repo would otherwise try to summarise years
# of work in one bullet. Cap it, and say in the entry that it is a starting point.
MAX_COMMITS = 40
DIFF_BUDGET = 10_000
SESSION_BUDGET = 6

# One project, one entry — shorter work than an intake conversation.
TIMEOUT_S = 600

log = make_logger(_HERE / "devlog.log", "devlog", stream=sys.stdout)

# What an entry stamps itself with, and how the next run finds it. Visible on
# purpose rather than an HTML comment: "this entry covers up to 93aa383" is
# useful to a human reading the log, and invisible state in a note Zach edits by
# hand is state he can destroy without knowing he did.
THROUGH = re.compile(r"_\(through `([0-9a-f]{7,40})`\)_")
H2 = re.compile(r"^##\s+(.+?)\s*$", re.M)
ENTRY_DATE = re.compile(r"^\s*[-*]\s+\*\*(\d{4}-\d{2}-\d{2})\*\*", re.M)
REPO_LINE = re.compile(r"^repo:\s*(.*)$", re.M)


# --------------------------------------------------------------------------
# the boundary
# --------------------------------------------------------------------------

def _privacy():
    """The model-boundary guard, borrowed from the interface rather than copied.

    Same reasoning as `intake._privacy`: this is the second place where *script
    code* reads a vault file and puts its contents straight into a prompt, so
    `privacy.py`'s PreToolUse hook — which guards the agent's tool calls — never
    fires for it. A hub note or session log under a sealed path would otherwise
    reach a model through a side door.
    """
    backend = _HERE.parent / "interface" / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from privacy import VaultPrivacy
    return VaultPrivacy(VAULT)


# --------------------------------------------------------------------------
# what is unlogged
# --------------------------------------------------------------------------

def _git(repo: Path, *args, timeout: int = 30):
    """`gitops._git` reused rather than reimplemented — it is generic over the
    repo path, and it is the one place that knows the non-interactive env that
    stops a stray credential prompt from hanging a background run."""
    return gitops._git(repo, *args, timeout=timeout)


def dev_log_section(text: str) -> str:
    """The `## Dev log` section's body, or "" if the note has no such section."""
    m = re.search(r"^##\s+Dev log\s*$", text, re.M | re.I)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^##\s+", rest, re.M)
    return (rest[:nxt.start()] if nxt else rest).strip()


def last_logged(text: str) -> tuple:
    """(sha, date) — where the next run should start, and how it knows.

    The sha comes from the most recent `_(through …)_` stamp, in **document
    order**: the dev log is append-only and chronological, so the last marker in
    the file is the newest. Reading dates for this would put a hand-written
    back-fill in charge of where the next run starts.

    The date is the bootstrap. Every dev log written before this module existed
    is dated but unstamped — `sigma-os.md` has eight months of entries and no
    shas — and treating those as "never logged" would ask the model to write up
    a history it already documented. So an unstamped log falls back to its last
    entry's date, which is fuzzy at the edges (commits from that same day may
    already be covered) and therefore also tells the brief to say so. It happens
    at most once per project: the entry this run writes carries a sha.
    """
    section = dev_log_section(text) or text
    found = THROUGH.findall(section)
    if found:
        return found[-1], None
    dates = ENTRY_DATE.findall(section)
    return None, (max(dates) if dates else None)


def _rev_ok(repo: Path, sha: str) -> bool:
    """Is this sha still in this repo? A rebase, a re-clone or a hand-edited note
    can leave a marker pointing at nothing, and `git log <gone>..HEAD` fails in a
    way that reads like the repo is broken. Fall back to the whole history."""
    r = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    return r.returncode == 0


def gather(repo: Path, base: str | None, since: str | None = None) -> dict:
    """Commits and file churn since `base` (exclusive), `since` (a date), or all."""
    args = ["log", "--date=short", "--pretty=format:%h  %ad  %an  %s",
            f"-n{MAX_COMMITS + 1}"]
    args.append(f"{base}..HEAD" if base else "HEAD")
    if since and not base:
        args.append(f"--since={since}")
    r = _git(repo, *args)
    if r.returncode != 0:
        return {"error": (r.stderr or "git log failed").strip()[:200]}
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    more = len(lines) > MAX_COMMITS
    lines = lines[:MAX_COMMITS]

    head = _git(repo, "rev-parse", "--short", "HEAD")
    head_sha = (head.stdout or "").strip()

    # Oldest-first reads like a story; git hands them back newest-first.
    commits = list(reversed(lines))

    # The lower bound for the churn diff. With no sha to anchor on, use the
    # parent of the oldest commit in range — and the empty tree when that commit
    # is the repo's root, so a one-commit repo shows its contents rather than
    # nothing at all.
    lower = base
    if not lower and commits:
        oldest = commits[0].split()[0]
        par = _git(repo, "rev-parse", "--verify", "-q", f"{oldest}^")
        lower = (par.stdout or "").strip() if par.returncode == 0 else EMPTY_TREE
    stat = _git(repo, "diff", "--stat", f"{lower or EMPTY_TREE}..HEAD")
    churn = (stat.stdout or "").strip()
    if stat.returncode != 0 or not churn:
        # SHA-256 repos have a different empty tree, and a shallow clone can
        # refuse the range. Neither is worth failing the run over — the commit
        # subjects alone still make a usable entry.
        churn = "_(file-level churn unavailable for this range)_"
    if len(churn) > DIFF_BUDGET:
        churn = churn[:DIFF_BUDGET] + "\n… (truncated)"

    return {"commits": commits, "n": len(commits), "more": more,
            "head": head_sha, "churn": churn, "error": None}


def _span(job: dict) -> str:
    """How this range was chosen, in words. One phrasing, used by the brief, the
    status table and the skip messages — three copies had already disagreed once,
    reporting "since the last entry" for a project that had no entries."""
    n = f"{job['n']} commit(s)"
    if job.get("base"):
        return f"{n} since `{job['base']}`, the last entry's stamp"
    if job.get("since"):
        return f"{n} since {job['since']}, the date of the last dev log entry"
    return f"{n} — the whole history, as nothing has been logged yet"


def hub_records() -> list:
    """Every project hub note, with what it declares. Facts only — `candidates`
    decides what is skippable, so the two are separately testable."""
    out = []
    for pattern in PROJECT_HUB_GLOBS:
        for p in sorted(Path(VAULT).glob(pattern)):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm = frontmatter(text)
            if fm.get("type") != "project":
                continue
            out.append({"name": p.stem, "path": p, "text": text, "fm": fm,
                        "repo": (fm.get("repo") or "").strip(),
                        "archived": "05-Archive/" in p.as_posix()})
    return out


def candidates(only: str | None = None) -> tuple:
    """(jobs, problems, quiet).

    `quiet` is the projects that are simply up to date — reported by `--status`
    and otherwise silent, because "nothing to do" is not a problem. Everything in
    `problems` is something a human might want to act on.
    """
    jobs, problems, quiet = [], [], []
    try:
        guard = _privacy()
    except Exception as e:
        # Fail closed, like every other reading of this boundary.
        return [], [("devlog", f"privacy guard unavailable ({e}) — "
                               f"refusing to read anything")], []

    for rec in hub_records():
        name = rec["name"]
        if only and name.lower() != only.lower():
            continue
        rel = rec["path"].relative_to(VAULT).as_posix()

        if not rec["repo"]:
            # Not every project has code. Silent unless asked for by name.
            (problems if only else quiet).append(
                (name, "no `repo:` field — nothing to log from"))
            continue

        why = guard.verdict(str(rec["path"]))
        if why:
            problems.append((name, f"refused at the model boundary: {why}"))
            continue

        repo = Path(rec["repo"])
        if not repo.is_dir():
            problems.append((name, f"repo folder is missing: {rec['repo']}"))
            continue
        if not (repo / ".git").exists():
            problems.append((name, "repo folder is not a git repository"))
            continue

        # The applier refuses gitignored targets because it cannot make a
        # revertible commit for one. Better to say so here than to spend a model
        # call producing a proposal that is guaranteed to be held.
        ign = gitops._git(VAULT, "check-ignore", "-q", rel)
        if ign.returncode == 0:
            problems.append((name, "its hub note is gitignored — an entry could "
                                   "not be committed, so it could not be undone; "
                                   "this one stays hand-written"))
            continue
        if ign.returncode not in (0, 1):
            problems.append((name, "could not verify the privacy boundary — failing closed"))
            continue

        base, since = last_logged(rec["text"])
        if base and not _rev_ok(repo, base):
            log(f"  {name}: last-logged sha {base} is not in the repo any more "
                f"— covering the whole history instead")
            base = None

        got = gather(repo, base, since)
        if got.get("error"):
            problems.append((name, f"git: {got['error']}"))
            continue
        if not got["n"]:
            quiet.append((name, f"up to date (through {base or since})"
                                if (base or since) else "no commits yet"))
            continue

        job = {**rec, "base": base, "since": since, **got}
        if rec["archived"]:
            # Worth saying out loud rather than logging: an archived project with
            # new commits means either the archive was premature or the log was
            # never written, and which one it is decides what to do about it.
            problems.append((name, f"archived, and {_span(job)} — unarchive it if "
                                   f"the work restarted, or log it by hand"))
            continue

        jobs.append(job)
    return jobs, problems, quiet


# --------------------------------------------------------------------------
# the other half of a dev log: what Sigma watched happen
# --------------------------------------------------------------------------

def session_context(project: str, guard) -> str:
    """Session logs for this project, newest first.

    The vision line is "dev-log a **session**", and git alone does not know what a
    session was *for* — a log records the ask and the decisions, which is exactly
    the part a commit subject leaves out. Sessions with no commits behind them
    still belong in a dev log: a day spent diagnosing something and changing
    nothing is real work.
    """
    if not SESSIONS.is_dir():
        return "_(no session logs)_"
    picked = []
    for p in sorted(SESSIONS.glob("*.md"), reverse=True):
        if len(picked) >= SESSION_BUDGET:
            break
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = frontmatter(text)
        if (fm.get("project") or "").strip().lower() != project.lower():
            continue
        if guard.verdict(str(p)):
            continue                      # sealed session: not read, not mentioned
        picked.append((p, fm, text))
    if not picked:
        return "_(no session logs recorded for this project)_"
    out = []
    for p, fm, text in picked:
        body = text.split("---", 2)[-1].strip()
        out.append(f"### `{p.name}` — {fm.get('outcome', '?')}\n\n"
                   f"**Task:** {fm.get('task', '(not recorded)')}\n\n"
                   f"{body[:1200]}")
    return "\n\n".join(out)


# --------------------------------------------------------------------------
# the brief
# --------------------------------------------------------------------------

RULES = """
You are Sigma's dev-logger. Zach triggered this run himself and is watching the
output land, so say what you are doing plainly and briefly.

Three rules bound everything you do:

1. **You do not write files.** `propose_change` is the only tool you have that
   touches disk. It drafts a proposal; the runner applies it afterwards as its
   own revertible commit. Never claim you changed a file.
2. **Never invent work.** Every claim in the entry must be traceable to a commit
   subject, a changed file, or a session log printed below. If the material is
   too thin to say anything true and useful, propose nothing and say so — that
   is a correct outcome, and much better than a confident entry about work that
   did not happen.
3. **The hub note is Zach's writing.** You are adding one entry to it, not
   editing it. Everything that is already there comes back exactly as it was.
""".strip()

BRIEF = """
## The project

- **Hub note:** `{rel}`
- **Repo:** `{repo}`
- **Covering:** {span}
- **Stamp this entry with:** `_(through `{head}`)_` — copy that exactly,
  including the backticks. The next run reads it to know where to start, so an
  entry without it causes this same work to be written up twice.
{morenote}
## The hub note exactly as it stands right now

```markdown
{hub}
```

## The commits (oldest first)

```
{commits}
```

## What changed, by file

```
{churn}
```

## Sigma's own session logs for this project

{sessions}

## Your job

Write **one** dev log entry and propose the complete updated hub note.

1. **Where it goes.** At the end of the `## Dev log` list. If that section holds
   only an empty `-` placeholder, replace the placeholder. If the note has no
   `## Dev log` section at all, add one immediately before `## Decisions`, or at
   the end of the note if there is no such section.
2. **The shape**, matching the entries already in this vault:
   `- **{today}** — <what the work was and why it matters>. _(through `{head}`)_`
   Continuation lines are indented two spaces. One entry, a few sentences: what
   changed, what it was for, and anything a reader six months from now would need
   to know. Bold a phrase only where it earns it.
3. **Say what the work was, not what the commits were called.** "Add auth stack
   and public read allowlist" is a commit subject; the entry should say what the
   project can now do that it could not before, and what decision it reflects.
   Group related commits into one thought rather than listing them.
4. **Everything else comes back unchanged.** The proposal content is the whole
   file and it is written literally, so anything you drop is deleted from Zach's
   note. Reproduce the frontmatter, every heading, every task, and every existing
   dev log entry exactly. Do not tick a checkbox, do not change `status:`, do not
   touch the `repo:` line — a run that does any of those is refused before it
   lands.
5. If these commits genuinely do not amount to anything worth recording, call no
   tool and say so in one sentence.

Call `propose_change` **once**, with `kind: note` and `target: {rel}`.
""".strip()


def build_brief(job: dict, guard) -> str:
    rel = job["path"].relative_to(VAULT).as_posix()
    more = ""
    if job["more"]:
        more += (f"- **Note:** more than {MAX_COMMITS} commits are unlogged and you "
                 f"are seeing the {MAX_COMMITS} most recent. Say in the entry that "
                 f"it starts the log partway rather than implying it covers "
                 f"everything.\n")
    if job.get("since"):
        # The bootstrap case: a date is a fuzzier boundary than a sha, and the
        # model is the only thing here that can tell whether an existing entry
        # already describes a given commit.
        more += (f"- **Note:** this project's existing entries are dated but not "
                 f"stamped with a sha, so the range starts at {job['since']} and "
                 f"some of these commits may already be described by the last "
                 f"entry. Read it and cover only what it does not.\n")
    return BRIEF.format(
        rel=rel, repo=job["repo"], span=_span(job), head=job["head"],
        morenote=more, hub=job["text"].strip(),
        commits="\n".join(job["commits"]), churn=job["churn"],
        sessions=session_context(job["name"], guard),
        today=datetime.date.today().isoformat())


# --------------------------------------------------------------------------
# the vet — script code, not judgement
# --------------------------------------------------------------------------

def _vet(prop_path: Path, job: dict) -> str | None:
    """Why this proposal must not be applied, or None if it is safe.

    Every rule here is mechanical. The applier already refuses a hollowed-out
    note and a ticked checkbox; these are the losses specific to *rewriting a
    hub note*, which is the thing no earlier auto-apply path did.
    """
    try:
        text = prop_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"unreadable proposal ({e})"

    fm = frontmatter(text)
    if fm.get("kind") != "note":
        return f"kind is '{fm.get('kind') or '?'}', not note"

    dest = rf.safe_target(fm.get("target", ""), "vault")
    if dest is None or dest.resolve() != job["path"].resolve():
        return (f"targets `{fm.get('target') or '(nothing)'}`, but a dev log run "
                f"may only touch `{job['path'].relative_to(VAULT).as_posix()}`")

    new = rf.proposal_content(text)
    if not new.strip():
        return "empty content block"
    old = job["text"]

    if len(new.strip()) < len(old.strip()):
        return (f"the note would get shorter ({len(old.strip())} → "
                f"{len(new.strip())} chars); an entry is an addition")

    kept = set(H2.findall(new))
    missing = [h for h in H2.findall(old) if h not in kept]
    if missing:
        return f"drops the section(s): {', '.join('## ' + m for m in missing)}"

    gone = sorted(set(ENTRY_DATE.findall(dev_log_section(old)))
                  - set(ENTRY_DATE.findall(dev_log_section(new))))
    if gone:
        return f"drops existing dev log entries: {', '.join(gone)}"

    m_old, m_new = REPO_LINE.search(old), REPO_LINE.search(new)
    if m_old and not m_new:
        return "drops the `repo:` line"
    if m_old and m_new and m_old.group(1).strip() != m_new.group(1).strip():
        return "rewrites the `repo:` line"

    if job["head"] not in new:
        return (f"the entry is not stamped `_(through `{job['head']}`)_`, so the "
                f"next run would write this work up a second time")
    return None


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

async def devlog_one(job: dict, guard, model: str = "sonnet") -> dict:
    import fleet as fl

    spec = sp.Specialist(
        key="devlog", title="Dev log", cadence="manual",
        model=model, effort="medium",
        brief=build_brief(job, guard),
        # One proposal, plus room to read the repo or a linked note if the
        # commits do not explain themselves.
        max_turns=26)
    return await fl.run_one(spec, timeout_s=TIMEOUT_S, rules=RULES)


def run(only: str | None = None, dry_run: bool = False, limit: int = 0,
        model: str = "sonnet") -> int:
    jobs, problems, _quiet = candidates(only)

    for name, why in problems:
        log(f"  skipped {name} — {why}")

    if not jobs:
        log("nothing to write up — every project with a repo is logged through "
            "its latest commit")
        return 1 if problems else 0

    if limit and len(jobs) > limit:
        log(f"  {len(jobs)} project(s) with unlogged work; taking the first {limit}")
        jobs = jobs[:limit]

    log(f"{len(jobs)} project(s) to write up")
    if dry_run:
        for j in jobs:
            log(f"  would log {j['name']}: {_span(j)} → through {j['head']}")
            for c in j["commits"][:5]:
                log(f"      {c}")
            if j["n"] > 5:
                log(f"      … and {j['n'] - 5} more")
        return 0

    try:
        guard = _privacy()
    except Exception as e:
        log(f"privacy guard unavailable ({e}) — refusing to read anything")
        return 1

    made = failed = declined = 0
    for j in jobs:
        log(f"-> {j['name']} ({j['n']} commit(s) through {j['head']})")
        try:
            r = asyncio.run(devlog_one(j, guard, model=model))
        except Exception as e:
            r = {"ok": False, "proposals": 0, "files": [],
                 "error": f"{type(e).__name__}: {e}"}

        if not r.get("ok"):
            failed += 1
            log(f"   nothing written — {r.get('error') or 'the run failed'}")
            continue
        if not r.get("proposals"):
            declined += 1
            log(f"   nothing worth recording"
                + (f" ({r['summary'][:160]})" if r.get("summary") else ""))
            continue

        # Vet before applying. A refused proposal is left pending on purpose:
        # the writing is still there to read, edit and merge by hand.
        ok_files, refused = [], []
        for fname in r["files"]:
            p = Path(rf.PROPOSALS) / fname
            if not p.exists():
                refused.append((fname, "vanished before it could be checked"))
                continue
            why = _vet(p, j)
            (refused.append((fname, why)) if why else ok_files.append(fname))

        for fname, why in refused:
            log(f"   REFUSED {fname} — {why}")
            log(f"           left pending: read it in 06-System/proposals/ and "
                f"edit or discard it by hand")

        if not ok_files:
            failed += 1
            continue

        try:
            import applier
            applied = applier.apply_run(ok_files, actor="devlog")
        except Exception as e:
            failed += 1
            log(f"   proposal written but applying failed: {type(e).__name__}: {e}")
            continue

        landed = [a for a in applied if a.get("action") in ("create", "update")]
        held = [a for a in applied if a.get("action") == "held"]
        for a in landed:
            log(f"   logged → {a.get('target')} "
                f"({(a.get('sha') or '')[:10] or 'no commit'})")
        for a in held:
            log(f"   HELD {a.get('target')} — {a.get('reason')}")
        if r.get("summary"):
            log(f"   {r['summary'][:300]}")
        if landed:
            made += 1
        else:
            failed += 1

    tally = f"devlog finished: {made} logged"
    if declined:
        tally += f", {declined} with nothing worth recording"
    if failed:
        tally += f", {failed} failed"
    log(tally)
    return 1 if failed else 0


def status(only: str | None = None) -> int:
    jobs, problems, quiet = candidates(only)
    if jobs:
        log(f"{len(jobs)} project(s) with work not yet in their dev log:")
        for j in jobs:
            log(f"   {j['name']:<22} {_span(j)}")
    else:
        log("every project with a repo is logged through its latest commit")
    for name, why in quiet:
        log(f"   {name:<22} {why}")
    for name, why in problems:
        log(f"   ! {name} — {why}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Write a project's recent commits into its hub note's dev log.")
    ap.add_argument("--status", action="store_true",
                    help="which projects have unlogged work; write nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="name the commits that would be written up; call no model")
    ap.add_argument("--project", default="", help="only this hub note's name")
    ap.add_argument("--max", type=int, default=0, help="stop after N projects")
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    # Same reason fleet does this in main() and not at import: a run that prints
    # a Windows pipe-teardown traceback after succeeding is a run whose output
    # you learn to skim, and this one streams into the dashboard's dock.
    if not (a.status or a.dry_run):
        import fleet as fl
        fl._quiet_proactor_shutdown()
    if a.status:
        return status(a.project or None)
    return run(only=a.project or None, dry_run=a.dry_run,
               limit=a.max, model=a.model)


if __name__ == "__main__":
    raise SystemExit(main())
