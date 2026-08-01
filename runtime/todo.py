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


def scan(vault: Path, split=None) -> list:
    """Every checkbox in the vault, open **and** done, in document order.

    Done boxes are collected too, and that is the whole completion-detection
    mechanism: a task whose id flips from open to checked between two scans was
    completed, which is what the 06:00 review counts. Reading the ledger instead
    would miss every tick made in Obsidian rather than on the dashboard.
    """
    vault = Path(vault)
    files = [p for p in vault.rglob("*.md")
             if not (set(p.relative_to(vault).parts[:-1]) & QUEUE_EXCLUDED_TOPS)
             and not p.relative_to(vault).parts[0].startswith(".")
             and p.relative_to(vault).parts[0] not in QUEUE_EXCLUDED_TOPS]
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
                "raw_input": None, "depends_on": [],
            }
            # A box already ticked when the queue was adopted was not completed
            # today — it was completed before any of this existed, and counting
            # it would hand the first review a fictional score.
            if f["done"] and not adopting:
                e["completed_at"] = today
            tasks[f["id"]] = e
        else:
            e["file"], e["text"] = f["file"], f["text"]
            if f["done"]:
                if not e.get("completed_at"):
                    e["completed_at"] = today
            elif e.get("completed_at"):
                # Un-ticked. `created` is deliberately untouched: a task you
                # re-open is the same task, and re-aging it from zero would
                # punish undo.
                e["completed_at"] = None
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
    t["overdue"] = bool(f["deadline"] and f["deadline"] < today)
    t["archived"] = e.get("status") == "archived"
    t["snoozed"] = bool(t["snoozed_until"] and t["snoozed_until"] > today)
    return t


def _chain(tasks: list) -> list:
    """Mark everything behind the head of a chain as blocked.

    The feature brief asks for an explicit `depends_on` DAG. For the data this
    vault actually holds — a course timeline is one ordered document, a project
    hub is one ordered list — "sequential within a file" is behaviourally the
    same thing and needs no ids written into the markdown. The field is kept in
    the index so an explicit override can be honoured later without a migration.
    """
    ordered = sorted(tasks, key=lambda t: (t["file"], t["order"]))
    head = {}
    for t in ordered:
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
            for t in eligible:
                # An inactive course or archived project keeps its tasks in the
                # queue but never spends a window slot on them.
                if t["parent"] in known and not any(
                        v["parent"] == t["parent"] for v in visible):
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
