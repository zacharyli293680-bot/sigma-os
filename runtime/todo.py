#!/usr/bin/env python3
"""
todo.py — the priority-queue engine behind the dashboard's four task queues.

This replaces the day-bucketed Today panel. Days stop being the organising unit;
four queues are: **Courses, ProCertus, Projects, Misc**. Each shows a small
window of the highest-scoring *eligible* tasks, and completing one promotes the
next. Deadlines are optional — the old panel discarded every task without a 📅,
which is most of the vault's real work.

**Markdown stays the truth.** A task is a checkbox line in a vault note, exactly
as CLAUDE.md §Tasks specifies, and ticking one is still a git commit through
writes.py with a ledger row behind it. This module adds a sidecar index
(`todo.json`) holding only what a checkbox line *cannot* say: when a task first
appeared, whether it is snoozed or pinned, and a section override. Everything
Obsidian can express — title, due date, priority — is re-derived on every scan
and never cached, so editing a note in Obsidian can never disagree with the
dashboard. A sidecar that stored the title would be a second source of truth for
it, and the vault's whole memory model exists to avoid that.

**Why this file is not called queue.py.** `runtime/` is inserted at the *front*
of sys.path by the backend (panels.py imports fleet/reflect/specialists that
way), so a module named `queue` here would shadow the stdlib `queue` for every
consumer in the process, uvicorn's own dependencies included. The HTTP endpoint
is still /api/queue; only the file is renamed.

Nothing here writes to the vault — reading is all it does. The index is its own
file under runtime/.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from sigma import DEFAULT_VAULT, frontmatter  # noqa: E402

# `.state.json`, not `.json`, and the suffix is load-bearing: .gitignore already
# excludes `runtime/*.state.json` as machine-local operating state. The index
# holds the text of every task in the vault, ProCertus's included, so a name
# that fell outside that rule would quietly push internship material to GitHub.
# Covered by an existing documented rule beats a new special case for it.
INDEX_PATH = HERE / "todo.state.json"
INDEX_VERSION = 1


# --------------------------------------------------------------------------
# the Tasks-plugin grammar — the canonical copy
# --------------------------------------------------------------------------
# panels._scan_tasks grew these first; they live here now because two modules
# parsing the same grammar with two regexes is precisely the drift the vault's
# one-implementation rule exists to stop. panels.py imports them from here.

TASK_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*\S)\s*$")
DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
PRIORITY = {"🔺": 4, "⏫": 3, "🔼": 2, "🔽": 1, "⏬": 0}
META_RE = re.compile(          # strip task-plugin metadata out of display text
    r"\s*(?:📅|✅|⏳|🛫|➕|🔁)\s*\d{4}-\d{2}-\d{2}|\s*[🔺⏫🔼🔽⏬]")
EXCLUDED_TOPS = {"05-Archive", "06-System", "99-Meta", ".obsidian", ".claude",
                 ".git", "Excalidraw"}

# Daily notes are journal, not queue. They were the *old* system's output: the
# template mints a ritual checklist every morning ("Triage Inbox to zero", "Log
# the day below") and the retired planner added a daily restatement of work that
# already lives in the course timelines and the ProCertus todo. Ingesting them
# made Misc a seven-times-over copy of the other three queues plus a ritual that
# was never meant to outlive its day. /api/tasks still reads them — the calendar
# strip is about dated work wherever it lives — so this exclusion is the queue's
# alone and deliberately not folded into EXCLUDED_TOPS.
QUEUE_EXCLUDED_TOPS = EXCLUDED_TOPS | {"01-Daily"}

_H2_RE = re.compile(r"^##\s+(.*\S)\s*$")
# Obsidian link syntax, reduced to its display text so a task reads as a
# sentence rather than as a path. `[[a/b/c|Day 3]]` -> `Day 3`, `[[foo]]` -> `foo`.
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


# --------------------------------------------------------------------------
# scoring — the whole ordering, in one pure function
# --------------------------------------------------------------------------

URGENCY_WEIGHT = {"high": 30.0, "medium": 15.0, "low": 5.0}
AGING_PER_DAY = 0.5
AGING_CAP = 15.0
DEADLINE_NUMERATOR = 100.0

# Windows are maxima, never quotas: a section with fewer eligible tasks shows
# fewer rows and no filler. Courses and Projects are one per *parent* rather
# than one per section, because their queues are chains and the interesting
# task is the head of each chain.
WINDOW = {"courses": 1, "procertus": 3, "projects": 1, "misc": 2}
PER_PARENT = ("courses", "projects")

# Suppression is a decision, not a timer. An earlier draft aged daily-note tasks
# out automatically, but daily notes no longer feed the queue at all, which left
# nothing for a staleness rule to act on. What remains is explicit: `archived` in
# the index, set by the archive action or by an approved hygiene proposal from
# the 06:00 review. A task never disappears from a queue on its own.
#
# How long a vanished task stays in the index. Long enough that un-ticking one
# next week still restores its original age; short enough that the file does not
# grow forever.
PRUNE_DAYS = 30

# A chain is a *document that is a sequence*, not a section.
#
# A timeline says so in its own title ("AA 210 — 60-Day Timeline"), its blocks
# are dated in order, and you genuinely cannot do Day 5 before Day 4. A study
# guide's chain (`<code>-guide.md`, study plan §5.2) is the same claim made
# structurally: module n+1 is blocked by module n. A project hub's task list and
# a course's ad-hoc list are todo lists: marking their second entry "blocked by"
# the first would be a claim about dependency that nothing in the note supports.
# This started as a per-section rule and quick-add exposed it — every task added
# to a course would have been buried behind a 51-item timeline.
#
# Recognition is the basename matching its own folder: `AA-210/aa-210-timeline.md`,
# `AA-210/aa-210-guide.md`. It was an exact match on `timeline.md` until commit
# a84695b (2026-08-09) renamed every timeline to `<code>-timeline.md` for the
# unique-basename invariant — after which recognition matched nothing, vault-wide,
# and every timeline silently went flat. A bare suffix match is not the repair:
# the vault holds `exam-1-study-guide.md` and `workbook-guide.md`, real notes
# whose checkboxes must never be sequenced behind a head. The folder has to vouch
# for the name, which is the contract's own naming rule — `<code>-guide.md` sits
# at the root of the `<COURSE>` folder it is named for.
CHAIN_SUFFIXES = ("-timeline.md", "-guide.md")

SECTIONS = ("courses", "procertus", "projects", "misc")
SECTION_TITLE = {"courses": "Courses", "procertus": "ProCertus",
                 "projects": "Projects", "misc": "Misc"}
# What a per-parent window is counting, so the card header can read "2 of 5
# courses" rather than "top 5" — which sounds like five tasks and is not what
# the number means. It also puts the silent parents on screen: three active
# courses have no timeline and contribute nothing, and a header that says
# "one per course" would hide that.
PARENT_NOUN = {"courses": "course", "projects": "project"}


def urgency_of(prio: int | None) -> str:
    """Tasks-plugin priority emoji -> the three urgency bands.

    🔺/⏫ are the two "this matters" markers and collapse to high; 🔼 is the
    explicit middle; 🔽/⏬ are both deprioritisations. No emoji means medium,
    which is also the default for a quick-added task — an unmarked task should
    not be quietly ranked below one someone bothered to mark 🔽.
    """
    if prio is None:
        return "medium"
    if prio >= 3:
        return "high"
    if prio == 2:
        return "medium"
    return "low"


def _days_between(a: str, b: str) -> int:
    """(b - a) in whole days, for two ISO dates."""
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def parts_of(task: dict, today: str) -> dict:
    """The score, broken into its three named terms.

    Every task exposes this so the UI can answer "why is this here?" — a
    priority order nobody can interrogate is one nobody trusts, and the
    breakdown also says which constant to turn when the order looks wrong.
    """
    deadline = 0.0
    days = None
    if task.get("deadline"):
        days = _days_between(today, task["deadline"])
        # Overdue is treated as due-today rather than escalating past 100: the
        # ceiling means one forgotten task can never monopolise its window, and
        # overdue tasks tie at 100 and break by age — oldest overdue first.
        deadline = DEADLINE_NUMERATOR / (max(0, days) + 1)
    urgency = URGENCY_WEIGHT[task.get("urgency") or "medium"]
    age_days = max(0, _days_between(task["created"], today))
    aging = min(AGING_CAP, AGING_PER_DAY * age_days)
    return {"deadline": round(deadline, 2), "urgency": urgency,
            "aging": round(aging, 2), "total": round(deadline + urgency + aging, 2),
            "days_until": days, "age_days": age_days}


def score(task: dict, today: str) -> float:
    return parts_of(task, today)["total"]


def sort_key(task: dict):
    """Pinned first, then score descending, then oldest first, then as written.

    `pinned` bypasses scoring entirely rather than adding a large weight —
    a manual override that can still be out-ranked is not an override.

    The last term is load-bearing on day one. Every adopted task shares a
    `created` date and most carry no deadline, so without it the entire visible
    window is decided by a SHA prefix: ProCertus listed "Update LinkedIn" above
    "Set up Gcloud cli" purely because its hash sorted lower. Document order is
    the order Zach wrote them in, which is a real signal and a stable one.
    """
    return (0 if task.get("pinned") else 1, -task["score"], task["created"],
            task["file"], task["order"])


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def task_id(rel: str, clean_text: str) -> str:
    """A stable handle for a checkbox line that has no id of its own.

    Keyed on the *cleaned* text, so adding a 📅 or a 🔺 to an existing task
    keeps its identity and its age — which is the common edit. Rewording a task
    mints a new identity and resets its age; that is honest, and the alternative
    (fuzzy matching) silently mis-attributes history, which is worse than losing
    it. The file path is in the key because moving a task between notes moves it
    between queues, and that is a different task in every way the UI cares about.
    """
    h = hashlib.sha1(f"{rel}|{normalise(clean_text)}".encode("utf-8"))
    return h.hexdigest()[:12]


# --------------------------------------------------------------------------
# where a task belongs
# --------------------------------------------------------------------------

def section_of(rel: str) -> tuple:
    """(section, parent) from the path alone.

    Deterministic on purpose: the folder a note lives in already encodes what
    kind of commitment it is (CLAUDE.md's PARA-hybrid layout is exactly this
    claim), so nothing has to be tagged and nothing can drift. The sidecar's
    `section` field overrides this when the guess is wrong.
    """
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] == "02-Areas" and parts[1] == "Academics":
        return "courses", parts[2]
    if len(parts) >= 2 and parts[0] == "02-Areas" and parts[1] == "ProCertus":
        return "procertus", None
    if len(parts) >= 2 and parts[0] == "03-Projects":
        stem = parts[1][:-3] if parts[1].endswith(".md") else parts[1]
        return "projects", stem
    return "misc", None


def active_courses(vault: Path) -> dict:
    """{folder name: display name} for every course that should contribute a frontier.

    A course folder with no course-index note still counts. Dropping it would
    hide real work behind a missing manifest, and the manifest is documentation,
    not the enrolment record.
    """
    out = {}
    base = Path(vault) / "02-Areas" / "Academics"
    if not base.is_dir():
        return out
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        status, name = None, None
        for note in sorted(d.glob("*.md")):
            try:
                fm = frontmatter(note.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if fm.get("type") == "course-index":
                status, name = fm.get("status"), fm.get("name")
                break
        if status in (None, "", "active"):
            out[d.name] = name or d.name
    return out


def active_projects(vault: Path) -> dict:
    """{hub stem: hub stem} for `type: project` hubs marked active.

    `type: project` is what keeps projects.md — the MOC — out of the list, the
    same guard sigma.project_hubs uses for the same reason.
    """
    out = {}
    base = Path(vault) / "03-Projects"
    if not base.is_dir():
        return out
    for p in sorted(base.glob("*.md")):
        try:
            fm = frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if fm.get("type") == "project" and fm.get("status") == "active":
            out[p.stem] = p.stem
    return out


# --------------------------------------------------------------------------
# quick-add: guessing where a typed line belongs, and where to write it
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[^a-z0-9]+")
_NUM_RE = re.compile(r"\b(\d{3})\b")

# Where a quick-added task lands, per section. Appended under the named heading
# if it exists, otherwise the heading is created at the end of the file — which
# is self-correcting: move the heading once and every later add follows it.
#
# Course tasks go to `tasks.md` rather than into `timeline.md`, because the
# timeline is a sequence and an ad-hoc task appended to it would be blocked
# behind every remaining day of the term.
DESTINATIONS = {
    "procertus": ("02-Areas/ProCertus/Todo.md", "## General"),
    "misc": ("02-Areas/Personal/misc.md", "## Tasks"),
}
# `resource` is the contract's catch-all and what 02-Areas/ProCertus/Todo.md
# already uses; there is no task-list type and inventing one would put the
# auditor and this file in disagreement about the schema.
NEW_NOTE = ("---\ntype: resource\ncourse: {course}\nsource: \ntags: [resource]\n"
            "---\n\n# {title}\n\n{heading}\n")


def destination(section: str, parent: str | None) -> tuple:
    """(vault-relative path, heading) for a new task in this section."""
    if section == "courses" and parent:
        return f"02-Areas/Academics/{parent}/tasks.md", "## Tasks"
    if section == "projects" and parent:
        return f"03-Projects/{parent}.md", "## Tasks"
    return DESTINATIONS.get(section) or DESTINATIONS["misc"]


def infer_section(text: str, vault) -> tuple:
    """(section, parent) from the words alone — deterministic, no model.

    Matched against what the vault actually contains rather than a keyword list,
    so "finish 311 lab" resolves to CSE-311 only because that course exists. A
    bare three-digit number counts only when exactly one active course carries
    it; two courses numbered 311 make the guess a coin flip, and misc with a
    visible section selector beats a confident wrong answer.

    The AI reword refines this. It is not a replacement for it: the reword is a
    suggestion the user accepts, and a task must be filed the instant it is
    typed whether or not a model ever answers.
    """
    low = f" {_WORD_RE.sub(' ', text.lower()).strip()} "
    if " procertus " in low:
        return "procertus", None

    courses = list(active_courses(vault))
    for code in courses:
        dept, _, num = code.lower().partition("-")
        if f" {dept} {num} " in low or f" {dept}{num} " in low:
            return "courses", code
    by_num: dict = {}
    for code in courses:
        by_num.setdefault(code.partition("-")[2], []).append(code)
    for n in _NUM_RE.findall(low):
        if len(by_num.get(n, ())) == 1:
            return "courses", by_num[n][0]

    for name in active_projects(vault):
        if f" {name.replace('-', ' ').lower()} " in low:
            return "projects", name
    return "misc", None


def compose(text: str, due: str | None = None, urgency: str | None = None) -> str:
    """A task line in the grammar CLAUDE.md documents.

    Urgency is written as the priority emoji rather than kept in the sidecar,
    because Obsidian can express it — so it survives being edited in Obsidian,
    and the Tasks plugin sorts by it. Medium writes nothing: it is already the
    default for an unmarked task, and a 🔼 on everything is noise.
    """
    line = f"- [ ] {' '.join(text.split())}"
    if due:
        line += f" 📅 {due}"
    if urgency == "high":
        line += " 🔺"
    elif urgency == "low":
        line += " 🔽"
    return line


def unsplice(body: str, line_no: int, raw: str) -> str | None:
    """Remove exactly line `line_no` if it still reads `raw`, else None.

    Same staleness contract as the toggle: the caller shows the line it saw, and
    a note that moved underneath it gets a refusal rather than a write to
    whatever now occupies that offset.
    """
    lines = body.split("\n")
    i = line_no - 1
    if not (0 <= i < len(lines)):
        return None
    had_cr = lines[i].endswith("\r")
    if (lines[i][:-1] if had_cr else lines[i]) != raw:
        return None
    return "\n".join(lines[:i] + lines[i + 1:])


def replace_line(body: str, line_no: int, raw: str, new: str) -> str | None:
    """Rewrite exactly line `line_no`, preserving its line ending. None if stale."""
    lines = body.split("\n")
    i = line_no - 1
    if not (0 <= i < len(lines)):
        return None
    had_cr = lines[i].endswith("\r")
    if (lines[i][:-1] if had_cr else lines[i]) != raw:
        return None
    lines[i] = new + ("\r" if had_cr else "")
    return "\n".join(lines)


SKIPPED_RE = re.compile(r"skipped::\d{4}-\d{2}-\d{2}")


def skip_line(raw: str, date: str) -> str | None:
    """`- [ ] …` -> `- [-] … skipped::<date> …`, or None if this line cannot
    be skipped — not an open checkbox, or skipped already.

    The calendar's `cancelled::` grammar, reused: a status the line carries,
    never a removal. The marker lands immediately before the first wikilink,
    so a chain row reads `M04 · skipped::2026-08-09 [[module|title]]` — the
    shape CLAUDE.md's guide section documents — and at the end of a line with
    no link. `[-]` is invisible to TASK_RE, which is the whole mechanism: the
    scan's frontier advances past the row while the note keeps the record,
    and the guide renderer shows it dimmed rather than gone.
    """
    m = re.match(r"^(\s*[-*]\s+)\[ \](.*)$", raw)
    if m is None or SKIPPED_RE.search(raw):
        return None
    head, body = m.group(1), m.group(2)
    marker = f"skipped::{date}"
    at = body.find("[[")
    if at >= 0:
        return f"{head}[-]{body[:at]}{marker} {body[at:]}"
    return f"{head}[-]{body.rstrip()} {marker}"


def splice(body: str, heading: str, line: str) -> str:
    """Insert `line` at the end of `heading`'s section, creating it if absent.

    Appends after the section's last non-blank line rather than immediately
    under the heading, so a new task joins the bottom of the list the way it
    would if it had been typed there.
    """
    lines = body.split("\n")
    want = heading.strip().lower()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().lower() == want), None)
    if start is None:
        out = lines[:]
        while out and not out[-1].strip():
            out.pop()
        out += ["", heading, "", line, ""]
        return "\n".join(out)

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    return "\n".join(lines[:at] + [line] + lines[at:])


# --------------------------------------------------------------------------
# scanning the vault
# --------------------------------------------------------------------------

def _default_split(vault: Path, rels: list) -> tuple:
    """(sealed, no_sync) — delegated to privacy.gitignore_scan, never reimplemented.

    That module is the single implementation of the model boundary, and it
    documents why its two halves fail in opposite directions. Importing it from
    runtime/ is the mirror of what panels.py already does in the other
    direction; if it cannot be imported at all, everything is sealed. The
    failure direction is empty queues, which is loud, rather than internship
    material on screen, which is not recoverable.
    """
    backend = str(HERE.parent / "interface" / "backend")
    if backend not in sys.path:
        sys.path.append(backend)
    try:
        from privacy import gitignore_scan
    except Exception:
        return set(rels), set()
    sealed, no_sync, _ = gitignore_scan(Path(vault), rels)
    return sealed, no_sync


def display_text(raw_text: str) -> str:
    """The task as a human reads it: metadata stripped, wikilinks reduced to labels."""
    t = META_RE.sub("", raw_text)
    t = _LINK_RE.sub(lambda m: (m.group(2) or m.group(1).split("/")[-1]).strip(), t)
    return " ".join(t.split()).strip(" ·—-")


def scan(vault: Path, split=None, tops=None) -> list:
    """Every checkbox in the vault, open **and** done, in document order.

    Done boxes are collected too, and that is the whole completion-detection
    mechanism: a task whose id flips from open to checked between two scans was
    completed, which is what the 06:00 review counts. Reading the ledger instead
    would miss every tick made in Obsidian rather than on the dashboard.

    `tops` is which top-level folders to skip, defaulting to the queue's set.
    It is a parameter rather than a constant because the queue and the calendar
    genuinely disagree about one folder: a daily note's ritual checklist is not
    queue work, but a dated checkbox inside one is still dated work, and the
    calendar has always shown it (see QUEUE_EXCLUDED_TOPS above). One scanner,
    two callers, one argument — rather than a second scanner with a second copy
    of this grammar.
    """
    vault = Path(vault)
    tops = QUEUE_EXCLUDED_TOPS if tops is None else tops
    files = [p for p in vault.rglob("*.md")
             if not (set(p.relative_to(vault).parts[:-1]) & tops)
             and not p.relative_to(vault).parts[0].startswith(".")
             and p.relative_to(vault).parts[0] not in tops]
    rels = [p.relative_to(vault).as_posix() for p in files]
    sealed, no_sync = (split or _default_split)(vault, rels)

    found = []
    for p, rel in zip(files, rels):
        if rel in sealed:
            continue                       # gitignored and not exempted — hidden
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        in_fence, heading, order = False, None, 0
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue     # CLAUDE.md's fenced example task must not become a
                             # phantom item in someone's queue
            h2 = _H2_RE.match(line)
            if h2:
                heading = h2.group(1)
                continue
            m = TASK_RE.match(line)
            if not m:
                continue
            body = m.group(2)
            text = display_text(body)
            if not text:
                continue                   # a checkbox with nothing but metadata
            due = DUE_RE.search(body)
            prio = next((v for e, v in PRIORITY.items() if e in body), None)
            found.append({
                "id": task_id(rel, text), "file": rel, "line": i, "raw": line,
                "text": text, "done": m.group(1) != " ",
                "deadline": due.group(1) if due else None,
                "priority": prio, "heading": heading, "order": order,
                "no_sync": rel in no_sync,
            })
            order += 1
    return found


# --------------------------------------------------------------------------
# the sidecar index
# --------------------------------------------------------------------------

def load_index(path=None) -> tuple:
    """(index, readable). `readable` is False only when a file exists but did not parse.

    The caller must not persist over an unreadable index. A torn read looks
    exactly like an empty one, and treating it as empty would re-adopt every
    task in the vault with today's date — silently resetting the age of all of
    them, which is the one piece of state this file exists to hold.
    """
    p = Path(path or INDEX_PATH)
    if not p.exists():
        return {"version": INDEX_VERSION, "adopted": None, "tasks": {}}, True
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": INDEX_VERSION, "adopted": None, "tasks": {}}, False
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), dict):
        return {"version": INDEX_VERSION, "adopted": None, "tasks": {}}, False
    data.setdefault("version", INDEX_VERSION)
    data.setdefault("adopted", None)
    return data, True


def save_index(index: dict, path=None) -> bool:
    """Atomic replace, because the backend rewrites this every cache miss while
    `sigma todo` may be reading it. Returns False rather than raising: losing an
    index costs task ages, losing the request that was serving the dashboard
    costs the dashboard."""
    p = Path(path or INDEX_PATH)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, p)
        return True
    except (OSError, TypeError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def reconcile(index: dict, found: list, today: str) -> dict:
    """Fold a scan into the index. Pure: returns a new index, mutates nothing.

    Four transitions, and the middle two are what the review reads:
      unseen id      -> a new task, `created` seeded
      open -> done   -> completed today
      done -> open   -> un-completed, original `created` preserved (free undo)
      id disappears  -> kept PRUNE_DAYS, then dropped

    **A completion is a transition, not a state.** Each entry remembers whether
    it was ticked the last time it was seen, and `completed_at` moves only when
    that flips. Keying on "is ticked and has no completion date" instead looks
    identical on the first scan and is catastrophically wrong on the second: a
    box ticked months ago is adopted with no date (correctly), and then the very
    next scan sees ticked-with-no-date and stamps it today. Every historical
    checkbox in the vault would report as finished this morning — 24 of them did,
    which is how this was found.
    """
    tasks = {k: dict(v) for k, v in (index.get("tasks") or {}).items()}
    adopting = index.get("adopted") is None
    seen = set()

    for f in found:
        seen.add(f["id"])
        e = tasks.get(f["id"])
        if e is None:
            e = {
                "file": f["file"], "text": f["text"],
                "section": None, "parent": None,
                # Nothing in the vault records when a checkbox was written. Git
                # could be asked, but a `log -S` per task across ~150 tasks is a
                # subprocess storm for a number that only feeds a 0.5/day term.
                # Ages start when the queue starts, which is at least honest.
                "created": today,
                "urgency": None, "snoozed_until": None, "pinned": False,
                "status": "active", "completed_at": None,
                "done": f["done"], "raw_input": None, "depends_on": [],
            }
            # A box already ticked when the queue was adopted was not completed
            # today — it was completed before any of this existed, and counting
            # it would hand the first review a fictional score.
            if f["done"] and not adopting:
                e["completed_at"] = today
            tasks[f["id"]] = e
        else:
            e["file"], e["text"] = f["file"], f["text"]
            # `.get("done", ...)` because entries written before this field
            # existed have no memory of their last state; falling back to
            # "whatever it is now" makes them report no transition, which is the
            # safe direction — a missed completion costs one row in one review,
            # an invented one corrupts the median every review reads afterwards.
            was = e.get("done", f["done"])
            if f["done"] and not was:
                e["completed_at"] = today
            elif was and not f["done"]:
                # Un-ticked. `created` is deliberately untouched: a task you
                # re-open is the same task, and re-aging it from zero would
                # punish undo.
                e["completed_at"] = None
            e["done"] = f["done"]
        e["last_seen"] = today
        e["gone"] = False

    for tid, e in tasks.items():
        if tid not in seen:
            e["gone"] = True
    horizon = (datetime.date.fromisoformat(today)
               - datetime.timedelta(days=PRUNE_DAYS)).isoformat()
    tasks = {k: v for k, v in tasks.items()
             if not (v.get("gone") and (v.get("last_seen") or "") < horizon)}

    return {"version": INDEX_VERSION,
            "adopted": index.get("adopted") or today,
            "tasks": tasks}


# --------------------------------------------------------------------------
# assembling the queues
# --------------------------------------------------------------------------

def set_meta(updates: dict, index_path=None) -> bool:
    """Merge per-task fields into the index. `{task_id: {field: value}}`.

    The only writer of sidecar-only state — snooze, pin, archive, the section
    override, and the raw text a reword replaced. Refuses fields that are not
    the sidecar's to hold: a title or a due date living here would be a second
    source of truth for something the note already says, which is the whole
    thing this design is avoiding.

    Read-modify-write under no lock. That is survivable because every field is
    last-write-wins per task and the writers are one browser and one person;
    it would not be if anything scheduled ever wrote here.
    """
    allowed = {"section", "parent", "urgency", "snoozed_until", "pinned",
               "status", "raw_input", "depends_on"}
    index, readable = load_index(index_path)
    if not readable:
        return False                       # never write over an index we cannot read
    for tid, fields in (updates or {}).items():
        entry = index["tasks"].setdefault(tid, {
            "file": "", "text": "", "section": None, "parent": None,
            "created": datetime.date.today().isoformat(),
            "urgency": None, "snoozed_until": None, "pinned": False,
            "status": "active", "completed_at": None, "raw_input": None,
            "depends_on": [],
        })
        for k, v in (fields or {}).items():
            if k in allowed:
                entry[k] = v
    return save_index(index, index_path)


# --------------------------------------------------------------------------
# the AI reword — a suggestion, never an action
# --------------------------------------------------------------------------

REWORD_PROMPT = """\
You clean up one hastily-typed todo item for a personal task queue. Today is \
{today}.

Raw input:
{text}

Reply with ONLY a JSON object, no prose and no code fence:

{{"title": str, "section": str, "parent": str|null, "due": "YYYY-MM-DD"|null,
  "urgency": "high"|"medium"|"low"}}

- title: the same task, tidied. Fix casing and obvious typos, expand an
  abbreviation only when it is unambiguous. Do NOT invent detail, do NOT add a
  date or a course code that is not implied by the raw input, and do NOT
  restate what the section already says. Keep it under 100 characters.
- section: exactly one of courses, procertus, projects, misc.
  courses = coursework. procertus = the internship. projects = the personal
  projects listed below. misc = everything else.
- parent: for courses, one of {courses}; for projects, one of {projects};
  otherwise null. Use null rather than guessing.
- due: only if the raw input implies a date. Resolve relative wording against
  today ("friday" is the next {today} or later Friday). null if none is implied.
- urgency: high only if the input says so ("urgent", "asap", "!"), low for
  clearly optional work, otherwise medium.

Return null for anything the input does not support. A field you invented is
worse than a field you left alone."""


def reword_prompt(text: str, vault, today: str | None = None) -> str:
    today = today or datetime.date.today().isoformat()
    return REWORD_PROMPT.format(
        today=today, text=" ".join(str(text).split())[:500],
        courses=", ".join(active_courses(vault)) or "(none)",
        projects=", ".join(active_projects(vault)) or "(none)")


def parse_reword(data, vault, today: str | None = None) -> dict | None:
    """Validate a model's answer into a suggestion that is safe to show.

    Everything here is untrusted input. It never reaches a path, a command line
    or a write — the user accepts a suggestion and *that* is what writes — but
    it is still checked field by field, because a suggestion carrying a course
    code that does not exist would send the accept straight at a directory the
    vault does not have.
    """
    if not isinstance(data, dict):
        return None
    today = today or datetime.date.today().isoformat()

    title = " ".join(str(data.get("title") or "").split())
    title = TASK_RE.sub(r"\2", title)          # a model that echoed "- [ ] x"
    title = META_RE.sub("", title).strip()[:100]
    if not title:
        return None

    section = str(data.get("section") or "").strip().lower()
    if section not in SECTIONS:
        section = "misc"
    parent = str(data.get("parent") or "").strip() or None
    known = (active_courses(vault) if section == "courses"
             else active_projects(vault) if section == "projects" else {})
    if parent not in known:
        parent = None
    if section in PER_PARENT and parent is None:
        # A course task with no course has nowhere to live; misc is where a
        # thing whose home is unknown belongs, and that is a visible answer.
        section = "misc"

    due = str(data.get("due") or "").strip() or None
    if due:
        try:
            d = datetime.date.fromisoformat(due)
        except ValueError:
            due = None
        else:
            # A model that resolved "friday" against its own idea of today can
            # land in the past. A deadline before today is not a deadline.
            due = d.isoformat() if d.isoformat() >= today else None

    urgency = str(data.get("urgency") or "").strip().lower()
    if urgency not in URGENCY_WEIGHT:
        urgency = "medium"

    return {"title": title, "section": section, "parent": parent,
            "due": due, "urgency": urgency}


def _merge(f: dict, e: dict, today: str) -> dict:
    """One scan row + its index entry -> the task the UI renders."""
    sec, parent = section_of(f["file"])
    sec = e.get("section") or sec
    parent = e.get("parent") or parent
    t = {
        "id": f["id"], "file": f["file"], "line": f["line"], "raw": f["raw"],
        "text": f["text"], "heading": f["heading"], "order": f["order"],
        "no_sync": f["no_sync"], "deadline": f["deadline"],
        "section": sec, "parent": parent,
        "urgency": e.get("urgency") or urgency_of(f["priority"]),
        "created": e["created"], "pinned": bool(e.get("pinned")),
        "snoozed_until": e.get("snoozed_until"),
        "raw_input": e.get("raw_input"),
        "blocked_by": None,
    }
    t["parts"] = parts_of(t, today)
    t["score"] = t["parts"]["total"]
    # Whether this task lives in a sequence document. The expanded view renders
    # a sequence as a sequence — rank is meaningless inside a fixed order — and
    # a todo list as a ranked list, so it has to be able to tell them apart.
    t["chain"] = is_chain_file(f["file"])
    t["chain_kind"] = chain_kind(f["file"])
    t["overdue"] = bool(f["deadline"] and f["deadline"] < today)
    t["archived"] = e.get("status") == "archived"
    t["snoozed"] = bool(t["snoozed_until"] and t["snoozed_until"] > today)
    return t


def is_chain_file(rel: str) -> bool:
    parts = rel.split("/")
    if len(parts) < 2:
        return False
    name, folder = parts[-1], parts[-2].lower()
    return any(name == f"{folder}{suffix}" for suffix in CHAIN_SUFFIXES)


def chain_kind(rel: str) -> str | None:
    """"timeline" | "guide" for a chain file, None for everything else.

    The two kinds share the blocking semantics but not a window slot: the
    work view renders a guide head distinctly, and build() keys a course's
    guide head into its own slot so the study frontier and the course's real
    deadline work never displace each other (study plan §7).
    """
    if not is_chain_file(rel):
        return None
    return "guide" if rel.endswith("-guide.md") else "timeline"


def _chain(tasks: list) -> list:
    """Mark everything behind the head of a *sequence document* as blocked.

    The feature brief asks for an explicit `depends_on` DAG. For the documents
    this vault actually holds a timeline is one ordered sequence, so "sequential
    within the file" is behaviourally the same thing and needs no ids written
    into the markdown. The field is kept in the index so an explicit override
    can be honoured later without a migration.

    Everything else in a chain *section* stays flat and fully eligible — see
    CHAIN_SUFFIXES for why the distinction is the file rather than the section.
    """
    ordered = sorted(tasks, key=lambda t: (t["file"], t["order"]))
    head = {}
    for t in ordered:
        if not is_chain_file(t["file"]):
            continue
        if t["archived"]:
            # A suppressed head must not park its chain. build() filters
            # archived tasks out *after* heads are picked here, so counting
            # one as the head would block every follower behind a task
            # nobody can see — the exact deadlock the study plan's skip
            # state (`[-]`, invisible to TASK_RE) exists to avoid.
            continue
        h = head.get(t["file"])
        if h is None:
            head[t["file"]] = t
        else:
            t["blocked_by"] = h["text"]
    return ordered


def build(vault=None, index_path=None, today=None, split=None,
          persist=True) -> dict:
    """Scan, reconcile, score, window. The whole payload behind GET /api/queue.

    Every path is resolved at call time, not bound as a default. A default of
    `index_path=INDEX_PATH` captures the module constant when this function is
    *defined*, so reassigning todo.INDEX_PATH afterwards silently does nothing —
    which is how a test suite that believed it was writing to a temp directory
    was in fact rewriting the live index.
    """
    vault = Path(vault or DEFAULT_VAULT)
    index_path = Path(index_path or INDEX_PATH)
    today = today or datetime.date.today().isoformat()

    found = scan(vault, split=split)
    index, readable = load_index(index_path)
    index = reconcile(index, found, today)
    # Never write over an index that existed but did not parse — see load_index.
    if persist and readable:
        save_index(index, index_path)

    open_tasks = [_merge(f, index["tasks"][f["id"]], today)
                  for f in found if not f["done"]]

    parents = {"courses": active_courses(vault), "projects": active_projects(vault)}

    sections = {}
    for key in SECTIONS:
        mine = [t for t in open_tasks if t["section"] == key]
        if key in PER_PARENT:
            mine = _chain(mine)
        # One task, one bucket, checked in a fixed order — a task that is both
        # blocked and snoozed is reported as blocked, because that is the fact
        # that has to change first.
        blocked = [t for t in mine if t["blocked_by"]]
        archived = [t for t in mine if t["archived"] and not t["blocked_by"]]
        snoozed = [t for t in mine
                   if t["snoozed"] and not t["blocked_by"] and not t["archived"]]
        eligible = [t for t in mine if not t["blocked_by"] and not t["archived"]
                    and not t["snoozed"]]
        eligible.sort(key=sort_key)

        if key in PER_PARENT:
            known = parents[key]
            visible, rest = [], []

            # One slot per parent — except that a *guide* chain head takes a
            # slot of its own beside the course's other work (study plan §7):
            # the study frontier must never displace a problem-set deadline,
            # and must never be displaced by one. A course with one chain
            # still shows one row; only a course running both a timeline and
            # a guide shows two, distinguished by `chain_kind` in the UI.
            def _slot(t):
                return "guide" if t["chain_kind"] == "guide" else "main"

            for t in eligible:
                # An inactive course or archived project keeps its tasks in the
                # queue but never spends a window slot on them.
                if t["parent"] in known and not any(
                        v["parent"] == t["parent"] and _slot(v) == _slot(t)
                        for v in visible):
                    visible.append(t)
                else:
                    rest.append(t)
            visible.extend(t for t in rest if t["pinned"])
            rest = [t for t in rest if not t["pinned"]]
            visible.sort(key=sort_key)
            window = len(known)
        else:
            window = WINDOW[key]
            pinned = [t for t in eligible if t["pinned"]]
            plain = [t for t in eligible if not t["pinned"]]
            visible = pinned + plain[:max(0, window - len(pinned))]
            rest = plain[len(visible) - len(pinned):]

        groups = []
        if key in PER_PARENT:
            for name, label in parents[key].items():
                chain = [t for t in mine if t["parent"] == name]
                chain.sort(key=lambda t: (t["file"], t["order"]))
                if chain:
                    groups.append({"parent": name, "label": label,
                                   "open": len(chain), "chain": chain})

        sections[key] = {
            "key": key, "title": SECTION_TITLE[key],
            "kind": "chain" if key in PER_PARENT else "flat",
            "parent_noun": PARENT_NOUN.get(key),
            "window": window, "visible": visible, "queue": rest,
            "blocked": blocked, "archived": archived, "snoozed": snoozed,
            "groups": groups,
            # Every parent this section *could* take, not just the ones that
            # already have a chain. `groups` is deliberately the latter — it
            # describes work in progress — so a move menu built from it could
            # never file a task into a course that has no tasks yet, which is
            # exactly the case you reach for when a course is new.
            "parents": ([{"key": name, "label": label}
                         for name, label in parents[key].items()]
                        if key in PER_PARENT else []),
        }

    counts = {
        "visible": sum(len(s["visible"]) for s in sections.values()),
        "queued": sum(len(s["queue"]) for s in sections.values()),
        "blocked": sum(len(s["blocked"]) for s in sections.values()),
        "archived": sum(len(s["archived"]) for s in sections.values()),
        "snoozed": sum(len(s["snoozed"]) for s in sections.values()),
    }
    return {"today": today, "adopted": index["adopted"], "index_ok": readable,
            "sections": sections, "counts": counts}


# --------------------------------------------------------------------------
# sigma todo
# --------------------------------------------------------------------------

def _chip(t: dict, today: str) -> str:
    if t["overdue"]:
        return f"overdue {abs(t['parts']['days_until'])}d"
    if t["deadline"]:
        d = t["parts"]["days_until"]
        if d == 0:
            return "due today"
        if d == 1:
            return "due tomorrow"
        if d <= 6:
            return "due " + datetime.date.fromisoformat(t["deadline"]).strftime("%a")
        return f"due {t['deadline'][5:]}"
    if t["parts"]["aging"] >= 1:
        return f"aging {t['parts']['age_days']}d"
    return ""


def _math(t: dict) -> str:
    p = t["parts"]
    bits = []
    if p["deadline"]:
        bits.append(f"due +{p['deadline']:.0f}")
    bits.append(f"{t['urgency'][:4]} +{p['urgency']:.0f}")
    if p["aging"]:
        bits.append(f"age +{p['aging']:.0f}")
    return " · ".join(bits) + f" = {p['total']:.0f}"


def _line(t: dict, today: str, mark: str = "☐") -> str:
    """Two lines per task: what it is, then why it scored what it did."""
    head = f"  {mark:<2}"
    if t["parent"]:
        head += f"{t['parent']}  "
    if t["pinned"]:
        head += "📌 "
    if t["no_sync"]:
        head += "⊘ "
    chip = _chip(t, today)
    return (f"{head}{t['text'][:78]}\n"
            f"       {chip + '  ·  ' if chip else ''}{_math(t)}")


def cmd_print(args) -> int:
    q = build(vault=args.vault or DEFAULT_VAULT, today=args.date,
              persist=not args.no_persist)
    if args.json:
        print(json.dumps(q, indent=2, ensure_ascii=False))
        return 0

    c = q["counts"]
    print()
    print(f"  sigma todo · {q['today']}"
          f"{'' if q['index_ok'] else '   ⚠ index unreadable — not persisting'}")
    print(f"  {c['visible']} visible · {c['queued']} queued · "
          f"{c['blocked']} blocked · {c['archived']} archived · "
          f"{c['snoozed']} snoozed")
    print("  " + "─" * 74)

    for key in SECTIONS:
        s = q["sections"][key]
        tail = []
        if s["queue"]:
            tail.append(f"{len(s['queue'])} queued")
        if s["blocked"]:
            tail.append(f"{len(s['blocked'])} blocked")
        if s["archived"]:
            tail.append(f"{len(s['archived'])} archived")
        if s["snoozed"]:
            tail.append(f"{len(s['snoozed'])} snoozed")
        noun = s["parent_noun"]
        head = (f"{len(s['visible'])} of {s['window']} {noun}s" if noun
                else f"top {s['window']}")
        print()
        print(f"  {s['title'].upper():<12} {head}"
              f"{'  ·  ' + ' · '.join(tail) if tail else ''}")
        if not s["visible"]:
            print("       nothing eligible")
        for t in s["visible"]:
            print(_line(t, q["today"]))
        if args.all:
            for n, t in enumerate(s["queue"], 1):
                print(_line(t, q["today"], mark=f"{n}."))
            for t in s["snoozed"][:args.limit]:
                print(f"  💤 {t['text'][:70]}")
                print(f"       snoozed until {t['snoozed_until']}")
            for t in s["archived"][:args.limit]:
                print(f"  ·· {t['text'][:70]}")
                print(f"       archived · {t['file']}")
            for t in s["blocked"][:args.limit]:
                print(f"  🔒 {t['text'][:70]}")
                print(f"       blocked by “{(t['blocked_by'] or '')[:52]}”")
            if len(s["blocked"]) > args.limit:
                print(f"  🔒 …{len(s['blocked']) - args.limit} more behind their frontier")
    print()
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="sigma todo",
        description="The four priority queues, as the dashboard computes them.")
    ap.add_argument("--all", action="store_true",
                    help="show the full queues, not just the visible windows")
    ap.add_argument("--json", action="store_true", help="the raw payload")
    ap.add_argument("--date", metavar="YYYY-MM-DD",
                    help="score as of this date instead of today")
    ap.add_argument("--vault", metavar="PATH")
    ap.add_argument("--no-persist", action="store_true",
                    help="do not write the index (a pure read)")
    ap.add_argument("--limit", type=int, default=3, metavar="N",
                    help="how many blocked/stale tasks to list per section with --all")
    return ap


if __name__ == "__main__":
    try:
        sys.exit(cmd_print(build_parser().parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
