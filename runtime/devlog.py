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

**The model writes the entry; script code writes the note.** This is the first
feature that routinely **updates** a note Zach wrote by hand, and the obvious
design — have the model return the whole updated file — is wrong twice over. A
dropped section would be a silent loss of his writing rather than a bad new note
he can delete; and the whole note has to go *in* to come back out, which put
`sigma-os` (42,000 characters of hub note) past Windows' 32,767-character command
line and made the vault's most important project the one this could not log. So
the model returns one paragraph, `splice()` inserts it between two known offsets,
and the composed file goes through `rf.write_proposal` — the same function the
agent's own tool calls. A splice cannot retitle a heading or tick a checkbox,
because it only ever inserts.

`_vet` still runs on the composed note, and now guards *this module's* splice
rather than the model's carelessness: every H2 heading, every existing entry and
the `repo:` line must survive, the note must not shrink, and the entry must carry
its sha. A refused proposal stays pending, so nothing is lost either way.

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

# Windows caps a command line at 32,767 characters, and the SDK passes the system
# prompt — which is where the brief goes — as an argument. Overshooting does not
# fail cleanly: the SDK reports `CLINotFoundError: Claude Code not found`, naming
# a binary that is sitting right there. `sigma-os` hit it first, at 48,576
# characters, because a hub note grows as it is dev-logged: the feature makes its
# own failure more likely the longer it works. So the brief is assembled to a
# budget, and what gets dropped is named rather than silently cut.
BRIEF_BUDGET = 22_000
HUB_BUDGET = 9_000
LOG_TAIL = 5            # existing dev log entries shown, for voice and overlap

# The model writes prose; script code stamps the sha. Asking it to copy a literal
# was the earlier design, and a stamp the model can forget is a duplicate entry
# next run — this cannot be forgotten.
NOTHING = "NOTHING"

# Where the entry starts and stops. "Reply with the entry alone" is not enough on
# its own: the first real run opened with "I can't read the repo itself — only the
# commit subjects are available to me. I'll write the entry from those", and that
# sentence went into the note. Instruction-following is the wrong tool for a
# boundary a parser can enforce, so the entry is delimited and everything outside
# the tags is thrown away.
ENTRY_TAG = re.compile(r"<entry>(.*?)</entry>", re.S | re.I)

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
            # An archived project with commits *past its last entry* is a real
            # signal: the work restarted after someone decided it was finished,
            # and only Zach can say which of those two facts is wrong. An
            # archived project that was simply never dev-logged is not — its repo
            # has always had that history, and reporting it as a problem on every
            # single run is how a warning list teaches you to stop reading it.
            if base or since:
                problems.append((name, f"archived, but {_span(job)} — unarchive it "
                                       f"if the work restarted"))
            else:
                quiet.append((name, f"archived, never dev-logged "
                                    f"({got['n']} commit(s) in its history)"))
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

RULES = f"""
You are Sigma's dev-logger. Zach triggered this run himself and is watching the
output land.

Three rules bound everything you do:

1. **You are writing one paragraph, not a file.** Wrap it in `<entry>` and
   `</entry>` tags; everything outside them is discarded, so put the entry and
   only the entry between them. The runner adds the date, the bullet and the
   commit stamp, and puts it in the note. Do not call any tool.
2. **Everything you know is in the brief.** You cannot open the repository — it
   lives outside the vault. The commit subjects, the file churn and the session
   logs printed below are the whole of the evidence, and they are enough. Do not
   say you were unable to read something; write the entry from what you have.
3. **Never invent work.** Every claim must be traceable to a commit subject, a
   changed file, or a session log printed below. If you cannot tell what
   something was for, say what changed and leave the why out.
4. **If there is nothing worth recording, reply with exactly `{NOTHING}`** and
   no tags. A run that finds a version bump and a typo fix should say so this way
   rather than inflating them into an entry.
""".strip()

BRIEF = """
## The project

- **Hub note:** `{rel}`
- **Repo:** `{repo}`
- **Covering:** {span}
{morenote}
## The hub note

{hub}

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

Write the body of **one** dev log entry describing this work.

1. **Say what the work was, not what the commits were called.** "Add auth stack
   and public read allowlist" is a commit subject; the entry should say what the
   project can now do that it could not before, and what decision it reflects.
   Group related commits into one thought rather than listing them one by one.
2. **A few sentences**, in the voice of the existing entries above: what changed,
   what it was for, and what a reader six months from now would need to know.
   Bold a phrase only where it earns it. Prose, not bullets — this becomes a
   single bullet in a list.
3. **Do not write the date, the leading `-`, or the commit stamp.** The runner
   adds all three, so an entry that includes them ends up with two of each.
   Start directly with the first word.
4. Anything you were not shown, you do not know. The hub note above is trimmed to
   its opening and its most recent entries; do not refer to parts of it you
   cannot see, and do not assume a task or a decision is or is not recorded there.

Reply with exactly this and nothing else:

```
<entry>the entry text</entry>
```

or `{nothing}` on its own if this work does not deserve an entry.
""".strip()


def hub_context(job: dict) -> str:
    """What the model needs of the hub note: its shape, its opening, its voice.

    Deliberately *not* the whole note. The earlier design asked the model to
    return the complete updated file, which meant the complete file had to go in
    — and `sigma-os.md` is 42,000 characters of dev log, which put the brief past
    the command-line limit and made the most important project in the vault the
    one project this could not log. Sending less is not a workaround for that: the
    model is writing one paragraph, and the only parts of the note that bear on
    the paragraph are what the project is, what the recent entries sound like,
    and what is already covered.
    """
    text = job["text"]
    parts = []
    fm = job["fm"]
    parts.append("**Frontmatter:** " + ", ".join(
        f"`{k}: {v}`" for k, v in fm.items() if v and k != "tags"))
    heads = ", ".join(f"`## {h}`" for h in H2.findall(text))
    parts.append("**Sections:** " + (heads or "_(none)_"))

    # The opening: everything before the first H2 is what the project *is*.
    body = text.split("---", 2)[-1]
    first = H2.search(body)
    intro = (body[:first.start()] if first else body).strip()
    if intro:
        parts.append("### How the note opens\n\n" + intro[:2500])

    section = dev_log_section(text)
    if section.strip() in ("", "-", "*"):
        parts.append("### The dev log\n\n_Empty — this would be its first entry._")
    else:
        # Split on entry bullets so a truncated tail never cuts mid-entry.
        starts = [m.start() for m in ENTRY_DATE.finditer(section)]
        entries = [section[a:b].rstrip() for a, b in
                   zip(starts, starts[1:] + [len(section)])] or [section]
        shown = entries[-LOG_TAIL:]
        head = (f"_Showing the last {len(shown)} of {len(entries)} entries._\n\n"
                if len(entries) > len(shown) else "")
        parts.append("### The dev log, most recent last\n\n" + head
                     + "\n".join(shown))

    out = "\n\n".join(parts)
    return out if len(out) <= HUB_BUDGET else out[:HUB_BUDGET] + "\n\n… (trimmed)"


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
                 f"entry. Read them and cover only what they do not.\n")

    fields = dict(rel=rel, repo=job["repo"], span=_span(job), morenote=more,
                  hub=hub_context(job), commits="\n".join(job["commits"]),
                  churn=job["churn"], sessions=session_context(job["name"], guard),
                  nothing=NOTHING)

    # Trim to the command-line budget, cheapest material first: file churn is a
    # summary of the commits, the session logs restate them in prose, and the
    # commit subjects themselves are the irreplaceable part. Each cut is stated
    # in the brief so the model knows it is working from less.
    for key, why in (("churn", "file-level churn"), ("sessions", "session logs")):
        if len(BRIEF.format(**fields)) <= BRIEF_BUDGET:
            break
        log(f"   brief is over budget — dropping {why} for {job['name']}")
        fields[key] = f"_(omitted: too large to fit alongside the commits)_"
    brief = BRIEF.format(**fields)
    if len(brief) > BRIEF_BUDGET:
        log(f"   brief still over budget at {len(brief):,} — truncating the commit list")
        brief = brief[:BRIEF_BUDGET] + "\n\n_(the brief was truncated here.)_"
    return brief


# --------------------------------------------------------------------------
# composing the note — script code, not the model
# --------------------------------------------------------------------------

def entry_from(said: str, head: str, today: str) -> str | None:
    """The model's prose → one dev log bullet, or None if it declined.

    The model is asked for the body alone and told the runner adds the rest, but
    a model that has just been shown five entries in `- **date** — …` form will
    sometimes hand one back in that form too. Stripping what it should not have
    written is cheaper than a refusal, and it makes the stamp unforgettable
    rather than merely instructed: a missing stamp would silently duplicate this
    work on the next run.
    """
    s = (said or "").strip()
    tagged = ENTRY_TAG.search(s)
    if tagged:
        # The delimited case: everything outside the tags was preamble, and
        # everything inside is the entry — including a "NOTHING" that happens to
        # be the first word of a real sentence.
        s = tagged.group(1).strip()
    else:
        # Undelimited, so the refusal has to be recognised by reading. Loose on
        # purpose: a false refusal here costs a re-run and reports itself, while
        # a missed one puts "there is nothing to report" in the note.
        if NOTHING in s.upper()[:80]:
            return None
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\n?|\n?```$", "", s).strip()
    if not s:
        return None
    s = re.sub(r"^\s*[-*]\s+", "", s)                        # its own bullet
    s = re.sub(r"^\*\*\d{4}-\d{2}-\d{2}\*\*\s*[—–-]\s*", "", s)   # its own date
    s = THROUGH.sub("", s)                                   # its own stamp
    # One logical line: the vault's existing entries wrap by hand, but rewrapping
    # here would break wikilinks and code spans across lines for no gain.
    s = " ".join(s.split())
    if len(s) < 40:
        return None
    return f"- **{today}** — {s} _(through `{head}`)_"


def splice(hub: str, entry: str) -> str | None:
    """Put one entry at the end of the `## Dev log` list. None if it cannot be
    placed — better to refuse than to guess at where a dev log belongs.

    This is the whole reason the model no longer returns the file. A splice is a
    rule: it cannot drop a section, retitle a heading, tick a checkbox or reword
    a decision, because it only ever inserts between two known offsets.
    """
    m = re.search(r"^##\s+Dev log\s*$", hub, re.M | re.I)
    if m:
        start = m.end()
        nxt = re.search(r"^##\s+", hub[start:], re.M)
        end = start + nxt.start() if nxt else len(hub)
        body, tail = hub[start:end], hub[end:]
        # The scaffold templates leave a bare `-`, which is a placeholder rather
        # than an entry; keeping it would leave an empty bullet above the log.
        kept = "" if body.strip() in ("-", "*", "") else body.strip() + "\n"
        return hub[:start] + "\n" + kept + entry + "\n" + ("\n" + tail.lstrip("\n")
                                                           if tail.strip() else "\n")
    block = f"## Dev log\n{entry}\n"
    d = re.search(r"^##\s+Decisions\s*$", hub, re.M | re.I)
    if d:
        return hub[:d.start()] + block + "\n" + hub[d.start():]
    if H2.search(hub):
        return hub.rstrip() + "\n\n" + block
    return None            # no headings at all: not a hub note shape we know


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
        # Reading the repo when the commit subjects do not explain themselves is
        # the only thing it spends turns on — the answer is one message.
        max_turns=20)
    return await fl.run_one(spec, timeout_s=TIMEOUT_S, rules=RULES)


def propose_entry(job: dict, entry: str) -> Path:
    """Write the proposal, with the composed note as its content.

    Still `propose_change`'s file, still `applier.py`, still one revertible
    commit in the ledger — `rf.write_proposal` is the same function the agent's
    tool calls. What is different is who composed the content: script code, from
    a splice it cannot get wrong in the ways a re-transcription can.
    """
    rel = job["path"].relative_to(VAULT).as_posix()
    return rf.write_proposal({
        "title": f"Dev log: {job['name']} through {job['head']}",
        "kind": "note", "target": rel, "content": splice(job["text"], entry),
        "rationale": (f"{_span(job)}, written up from the commit log and this "
                      f"project's session logs. The entry is the model's; the "
                      f"rest of the note is the existing file, spliced by "
                      f"`devlog.py` rather than re-transcribed."),
        "risk": "low", "scope": "vault", "insight": "",
    }, datetime.date.today().isoformat())


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
    today = datetime.date.today().isoformat()
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

        # The brief says to call no tool, but `run_one` still hands it
        # `propose_change`, and a proposal this module will not apply would
        # otherwise sit pending forever with nothing to say where it came from.
        for stray in r.get("files") or []:
            log(f"   note: the model raised {stray} despite being asked for prose "
                f"— left pending for you, not applied by this run")

        entry = entry_from(r.get("text", ""), j["head"], today)
        if entry is None:
            declined += 1
            log(f"   nothing worth recording"
                + (f" ({r.get('summary', '')[:160]})" if r.get("summary") else ""))
            continue
        if splice(j["text"], entry) is None:
            failed += 1
            log(f"   cannot place an entry in {j['path'].name} — it has no "
                f"`## Dev log` section and no headings to add one before")
            continue

        try:
            prop = propose_entry(j, entry)
        except Exception as e:
            failed += 1
            log(f"   could not write the proposal: {type(e).__name__}: {e}")
            continue

        # Vet the composed note before applying. The splice cannot lose a section
        # the way a re-transcription could, so this now guards *this module's own
        # bug* rather than the model's carelessness — cheap, and the one check
        # that would catch a broken splice before it reached Zach's note.
        why = _vet(prop, j)
        if why:
            failed += 1
            log(f"   REFUSED {prop.name} — {why}")
            log(f"           left pending in 06-System/proposals/ — this is a "
                f"devlog.py bug, not a model one; the entry text is in the file")
            continue

        try:
            import applier
            applied = applier.apply_run([prop.name], actor="devlog")
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
