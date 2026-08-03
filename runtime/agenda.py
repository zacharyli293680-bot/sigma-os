#!/usr/bin/env python3
"""
agenda.py — the resolver behind the calendar (P1 of the agenda subsystem).

**One resolver, and disagreement is visible rather than silent.** Four kinds of
thing land on the calendar — a dated task, a dated note, an event row, a
recurrence rule — and they are merged here, once, so that nothing else in the
system has to have its own opinion about what is due. Every occurrence carries
where it came from: a path, and a line or a block ID. When two sources disagree
about the same subject (a task's 📅 against its own note's `date:`) both are
emitted and both are flagged, because a resolver that silently picks a winner is
a resolver you cannot audit.

**Markdown stays the truth.** Titles, dates, times and recurrence are re-derived
on every scan and never cached anywhere but in this module's TTL. Nothing here
writes to the vault — `compose_event` exists so the round-trip test can prove
that every line the future writer emits parses back to the same occurrence, and
it returns a string like every other function in this file.

**Why this file is not called calendar.py.** `runtime/` is inserted at the front
of `sys.path`, so a module named `calendar` here would shadow the stdlib
`calendar` for every consumer in the process, uvicorn's dependency tree
included. `todo.py` is not `queue.py` and `retro.py` is not `review.py` for
exactly this reason; this is the third time and the first one that was cheap.

Grammar lives in CLAUDE.md §Calendar events. Two notes on how it is read here:

  *Liberal on input, canonical on output.* The contract writes a time range with
  an en dash and a date range with an arrow. A hand-edit in Obsidian will use
  whatever the keyboard has, so `-`, `–` and `—` all parse, as do `->` and `→`.
  `compose_event` only ever writes the canonical form, so the round-trip
  property holds for anything Sigma produces without punishing anything you
  type.

  *The block ID is optional on read.* A line you type by hand will not have one,
  and a line that does not parse is a line that has silently vanished from your
  calendar. When it is absent, identity falls back to a hash of path + date +
  title — the same thing `todo.task_id` does for a checkbox, which has no ID of
  its own either. The writer always emits one. **This is a decision beyond the
  brief's §5 and is flagged as such**: the alternative is that hand-written
  events are invisible until something adds an ID, and nothing would.
"""
import datetime
import hashlib
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from sigma import frontmatter  # noqa: E402

import todo as td  # noqa: E402  — task scanning, sections, identity

# --------------------------------------------------------------------------
# where the hand-written agenda lives
# --------------------------------------------------------------------------

CALENDAR_DIR = "02-Areas/Personal/Calendar"
SCHEDULE_REL = f"{CALENDAR_DIR}/schedule.md"
MONTH_NAME_RE = re.compile(r"^(\d{4})-(\d{2})\.md$")

# The two exclusions differ by exactly one folder, and the difference is not an
# oversight.
#
# A daily note carries `date:` and is journal, not commitment: putting all of
# them on the calendar buries every real event under one entry per day (the
# vault has seven such notes and zero real dated notes today, so the ratio is
# not hypothetical). But a dated *checkbox* inside a daily note is real work
# with a real deadline, and `/api/tasks` has always shown it — todo.py says so
# where it defines QUEUE_EXCLUDED_TOPS. So notes skip 01-Daily and tasks do not.
NOTE_EXCLUDED_TOPS = td.QUEUE_EXCLUDED_TOPS
TASK_EXCLUDED_TOPS = td.EXCLUDED_TOPS

# A range guard, because expansion is per-day. `?from=1900-01-01&to=2999-12-31`
# would walk 400,000 days per rule; refusing is the only sane answer and saying
# so beats a request that never returns.
MAX_RANGE_DAYS = 800

# --------------------------------------------------------------------------
# the line grammar
# --------------------------------------------------------------------------

_DASH = r"[–—-]"          # en dash, em dash, hyphen
_ARROW = r"(?:→|->)"           # → or ->
_DATE = r"\d{4}-\d{2}-\d{2}"
_TIME = r"\d{1,2}:\d{2}"

# The block ID is stripped off the end *before* the body is matched, rather
# than being an optional trailing group in the body's own pattern. As a trailing
# optional it is ambiguous against a lazy title — the engine happily reads
# `Dentist ^sg-evt-8f2a1c04` as a five-word title and skips the group, so the ID
# ends up in the title and the event still "parses". Stripping first makes the
# two decisions independent and the failure loud.
EVENT_BID_RE = re.compile(r"\s+\^(sg-evt-[0-9a-fA-F]{8})\s*$")
RULE_RID_RE = re.compile(r"\s+\^(sg-rule-[A-Za-z0-9._-]+)\s*$")
# Cancelling is a status, never a line removal (CLAUDE.md §3). The date is the
# day it was cancelled, not the day it was going to happen — this vault does not
# delete the record of something that was once true, and "when did I drop this"
# is usually the interesting half when you look back.
CANCELLED_RE = re.compile(rf"\s+cancelled::\s*({_DATE})\s*$")
# A marker left in the body after stripping is a malformed ID, not a title.
_ORPHAN_ID_RE = re.compile(r"\^sg-(?:evt|rule)-")

EVENT_RE = re.compile(
    r"^\s*[-*]\s+"
    rf"(?P<date>{_DATE})"
    rf"(?:\s*{_ARROW}\s*(?P<end_date>{_DATE}))?"
    rf"(?:\s+(?P<start>{_TIME})"
    rf"(?:\s*(?P<dash>{_DASH})(?:\s*(?P<end>{_TIME}))?)?)?"
    r"\s+(?P<title>\S.*?)\s*$")

RULE_RE = re.compile(
    r"^\s*[-*]\s+(?P<days>[MTWRFSU]+)"
    rf"(?:\s+(?P<start>{_TIME})(?:\s*{_DASH}\s*(?P<end>{_TIME}))?)?"
    r"\s+(?P<rest>\S.*?)\s*$")

# What *looks* like it was meant to be one, for reporting a line that is not.
LOOKS_LIKE_EVENT_RE = re.compile(rf"^\s*[-*]\s+(?:{_DATE}|.*\^sg-evt-)")
LOOKS_LIKE_RULE_RE = re.compile(r"^\s*[-*]\s+(?:[MTWRFSU]+\s|.*\^sg-rule-)")

_RULE_FIELD_RE = re.compile(
    rf"\b(from|until|except)::\s*({_DATE}(?:\s*,\s*{_DATE})*)")

# M T W R F S U — Thursday is R and Sunday is U, the US course-catalogue
# convention, so "MWF" and "TR" read the way a schedule of classes reads.
WEEKDAYS = {"M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "S": 5, "U": 6}

_H1_RE = re.compile(r"^#\s+(.*\S)\s*$", re.M)
_H2_RE = td._H2_RE


def _valid_date(s: str) -> str | None:
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except (ValueError, TypeError):
        return None


def _valid_time(s: str | None) -> str | None:
    """Normalise to HH:MM, or None. `9:05` is accepted and written back as
    `09:05`, so a hand-typed line converges on the canonical form the first
    time anything rewrites it."""
    if not s:
        return None
    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def parse_event(line: str) -> dict | None:
    """One event row → its fields, or None if the line is not one.

    None is the answer for anything that does not parse, including a line that
    looks close. The write path's "must re-parse as the same kind of occurrence"
    hold depends on this being strict about what it accepts back.
    """
    # Strip from the end inward: the ID last written is the outermost token, and
    # the status sits between it and the title.
    body, block_id, cancelled = (line or "").rstrip(), None, None
    hit = EVENT_BID_RE.search(body)
    if hit:
        block_id, body = hit.group(1), body[:hit.start()]
    hit = CANCELLED_RE.search(body)
    if hit:
        cancelled, body = hit.group(1), body[:hit.start()]
    m = EVENT_RE.match(body)
    if not m:
        return None
    date = _valid_date(m.group("date"))
    if not date:
        return None
    end_date = _valid_date(m.group("end_date")) if m.group("end_date") else None
    if end_date and end_date < date:
        return None                      # a span that ends before it starts
    start = _valid_time(m.group("start"))
    if m.group("start") and not start:
        return None                      # 25:00 is not a time, and not a title
    end = _valid_time(m.group("end"))
    if m.group("end") and not end:
        return None
    if start and end and end < start:
        return None
    title = " ".join(m.group("title").split())
    if not title or _ORPHAN_ID_RE.search(title):
        # A leftover marker means the ID was malformed — `^sg-evt-abc`, four hex
        # instead of eight, a typo'd caret. Refusing is the honest answer, and
        # `collect()` reports the line rather than dropping it, so a mistyped ID
        # shows up as a problem instead of as an event that quietly stopped
        # existing.
        return None
    return {"date": date, "end_date": end_date, "start": start, "end": end,
            "all_day": start is None,
            "open_ended": bool(start and not end),
            "title": title, "block_id": block_id, "cancelled": cancelled}


def compose_event(date: str, title: str, start: str | None = None,
                  end: str | None = None, end_date: str | None = None,
                  block_id: str | None = None, cancelled: str | None = None) -> str:
    """The canonical line for an event. Writes nothing — the endpoint does that.

    The single serialiser: the write path composes through here and then parses
    the result back before committing, so "never write something the scanner
    cannot read" is enforced by construction rather than by discipline.
    """
    line = f"- {date}"
    if end_date and end_date != date:
        line += f"→{end_date}"
    if start:
        line += f" {start}–{end}" if end else f" {start}–"
    line += f" {' '.join(title.split())}"
    if cancelled:
        line += f" cancelled::{cancelled}"
    if block_id:
        line += f" ^{block_id}"
    return line


# The month note a write creates when the month has none yet. It lives here
# rather than in the endpoint because this module owns the grammar, and the
# heading is part of it — `splice` appends under `## Events`, and a note without
# that heading would grow a second one.
MONTH_NOTE = """---
type: calendar-month
month: {month}
tags: [calendar]
---

# {month}

## Events
"""
EVENTS_HEADING = "## Events"


def new_block_id(*parts: str) -> str:
    """`sg-evt-<8 hex>`, derived rather than random so a given line always gets
    the same ID — which keeps the round-trip test deterministic and means a
    re-run of any future backfill does not mint a second ID for one event."""
    h = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:8]
    return f"sg-evt-{h}"


def parse_rule(line: str) -> dict | None:
    """One `schedule.md` row → a rule, or None.

    Deliberately small: weekly by weekday, with a start, an end and named
    exceptions. Not RRULE. `expand()` is the only thing that reads the result,
    so replacing this pair is how the grammar grows if it ever needs to.
    """
    body, rule_id = (line or "").rstrip(), None
    hit = RULE_RID_RE.search(body)
    if hit:
        rule_id, body = hit.group(1), body[:hit.start()]
    m = RULE_RE.match(body)
    if not m:
        return None
    days = m.group("days")
    weekdays = sorted({WEEKDAYS[c] for c in days})
    if not weekdays:
        return None
    start = _valid_time(m.group("start"))
    if m.group("start") and not start:
        return None
    end = _valid_time(m.group("end"))
    if m.group("end") and not end:
        return None
    if start and end and end < start:
        return None

    rest = m.group("rest")
    fields: dict = {}
    for key, val in _RULE_FIELD_RE.findall(rest):
        dates = [d for d in (_valid_date(x.strip()) for x in val.split(",")) if d]
        if key == "except":
            fields.setdefault("except", []).extend(dates)
        elif dates:
            fields[key] = dates[0]
    title = " ".join(_RULE_FIELD_RE.sub("", rest).split())
    if not title or _ORPHAN_ID_RE.search(title):
        return None
    return {"weekdays": weekdays, "days": days, "start": start, "end": end,
            "title": title, "from": fields.get("from"),
            "until": fields.get("until"),
            "except": sorted(set(fields.get("except", []))),
            "rule_id": rule_id}


def expand(rule: dict, frm: str, to: str) -> list:
    """The dates a rule occurs on, within [frm, to] inclusive.

    Never materialised into a note: the rule row is the only record, and this is
    recomputed on every read. One swappable function, per the brief — the
    grammar and this expander are a pair, and neither is used anywhere else.
    """
    lo, hi = _valid_date(frm), _valid_date(to)
    if not lo or not hi:
        return []
    lo = max(lo, rule["from"]) if rule.get("from") else lo
    hi = min(hi, rule["until"]) if rule.get("until") else hi
    if lo > hi:
        return []
    skip = set(rule.get("except") or ())
    day, last = datetime.date.fromisoformat(lo), datetime.date.fromisoformat(hi)
    days, out = set(rule["weekdays"]), []
    while day <= last:
        iso = day.isoformat()
        if day.weekday() in days and iso not in skip:
            out.append(iso)
        day += datetime.timedelta(days=1)
    return out


# --------------------------------------------------------------------------
# scanning the vault
# --------------------------------------------------------------------------

def _rels(vault: Path) -> tuple:
    files = [p for p in vault.rglob("*.md")
             if not (set(p.relative_to(vault).parts[:-1]) & NOTE_EXCLUDED_TOPS)
             and not p.relative_to(vault).parts[0].startswith(".")
             and p.relative_to(vault).parts[0] not in NOTE_EXCLUDED_TOPS]
    return files, [p.relative_to(vault).as_posix() for p in files]


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _lines_outside_fences(text: str):
    """(line_no, line) for every line not inside a ``` fence.

    The same reason todo.scan does it: CLAUDE.md's own example event row must
    not become a phantom dentist appointment in August 2026.
    """
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield i, line


def collect(vault, split=None) -> dict:
    """Every calendar source in the vault, range-independent.

    This is the expensive half — one walk, one privacy split — and it is what
    the TTL caches. Projecting a date range out of it is cheap, which is the
    whole point: a month render must not rescan the vault per cell, and the
    lesson the auditor already paid for was *precompute the scan*, not *make the
    scan faster*.
    """
    vault = Path(vault)
    files, rels = _rels(vault)
    sealed, no_sync = (split or td._default_split)(vault, rels)

    events, notes, rules, problems, timezone = [], [], [], [], None
    for p, rel in zip(files, rels):
        if rel in sealed:
            continue                     # gitignored and not exempt — hidden
        name = rel.rsplit("/", 1)[-1]
        in_calendar = rel.startswith(CALENDAR_DIR + "/")
        text = _read(p)
        if text is None:
            continue

        if in_calendar and MONTH_NAME_RE.match(name):
            for i, line in _lines_outside_fences(text):
                ev = parse_event(line)
                if ev:
                    ev.update({"file": rel, "line": i, "raw": line,
                               "no_sync": rel in no_sync})
                    events.append(ev)
                elif LOOKS_LIKE_EVENT_RE.match(line):
                    # A line that was meant to be an event and is not. Reported
                    # rather than skipped: an event that silently stopped
                    # existing because of a typo'd ID is the worst failure this
                    # subsystem can have, since nothing about your calendar
                    # looks wrong — it just quietly has less in it.
                    problems.append({"path": rel, "line": i, "raw": line.strip(),
                                     "why": "does not parse as an event row"})
            continue
        if rel == SCHEDULE_REL:
            timezone = (frontmatter(text).get("timezone") or "").strip() or None
            for i, line in _lines_outside_fences(text):
                ru = parse_rule(line)
                if ru:
                    ru.update({"file": rel, "line": i, "raw": line,
                               "no_sync": rel in no_sync})
                    rules.append(ru)
                elif LOOKS_LIKE_RULE_RE.match(line):
                    problems.append({"path": rel, "line": i, "raw": line.strip(),
                                     "why": "does not parse as a recurrence rule"})
            continue

        fm = frontmatter(text)
        for field in ("date", "due"):
            when = _valid_date((fm.get(field) or "").strip())
            if not when:
                continue
            h1 = _H1_RE.search(text)
            notes.append({"file": rel, "field": field, "date": when,
                          "title": (h1.group(1) if h1 else name[:-3]),
                          "type": (fm.get("type") or "").strip() or None,
                          "no_sync": rel in no_sync})

    return {"events": events, "notes": notes, "rules": rules,
            "tasks": td.scan(vault, split=split, tops=TASK_EXCLUDED_TOPS),
            "timezone": timezone, "problems": problems}


# --------------------------------------------------------------------------
# the TTL — one cache, keyed by vault
# --------------------------------------------------------------------------

CACHE_TTL = 15.0
_CACHE: dict = {}


def invalidate(vault=None):
    """Drop the cached scan. P5's write path calls this after every commit; the
    read endpoints never need to."""
    _CACHE.pop(str(vault), None) if vault is not None else _CACHE.clear()


def collect_cached(vault, split=None, ttl: float = CACHE_TTL) -> dict:
    key, now = str(vault), time.monotonic()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < ttl:
        return hit[1]
    value = collect(vault, split)
    _CACHE[key] = (now, value)
    return value


# --------------------------------------------------------------------------
# occurrences — one shape, provenance on every one
# --------------------------------------------------------------------------

def _display(text: str) -> str:
    """A title as a human reads it: wikilinks reduced to their label.

    `[[CSE-311]] lecture` is right in `schedule.md` — the link is what keeps the
    row and the course connected in the graph — and wrong on a calendar, where
    it should read as a sentence. Same reduction `todo.display_text` does for a
    checkbox, borrowing its regex rather than writing a second one.

    **`parse_event`/`parse_rule` keep the literal title**, because `compose_event`
    has to round-trip it byte-for-byte; only the occurrence carries the reduced
    form. The two are different jobs and the round-trip test pins the first.
    """
    return " ".join(td._LINK_RE.sub(
        lambda m: (m.group(2) or m.group(1).split("/")[-1]).strip(), text).split())


def _occ(kind, oid, date, title, *, path, line=None, block_id=None,
         rule_id=None, field=None, start=None, end=None, span=None,
         no_sync=False, owner="sigma", section=None, parent=None,
         raw=None, priority=None, cancelled=None) -> dict:
    if section is None:
        section, parent = td.section_of(path)
    return {
        "kind": kind, "id": oid, "date": date,
        "start": start, "end": end, "all_day": start is None,
        "title": title, "owner": owner,
        # `raw` is the exact line, and it is the staleness token every write in
        # this system already runs on — the client hands back what it saw and
        # the server refuses on mismatch. It is here from P1 so P5 does not have
        # to add a field to a shape four call sites already build. None for a
        # note occurrence, whose source is frontmatter rather than a line.
        "raw": raw, "priority": priority,
        # The date it was cancelled, or None. The occurrence is still emitted —
        # a cancelled thing that vanishes is indistinguishable from one that was
        # deleted, and this vault does not delete. The UI dims it; capacity
        # ignores it.
        "cancelled": cancelled,
        # Provenance is not optional and not conditional. Every occurrence can
        # answer "which file, which line" — that is what the [?] affordance
        # renders, and asserting it as an invariant is cheaper than trusting
        # four call sites to remember.
        "source": {"path": path, "line": line, "block_id": block_id,
                   "rule_id": rule_id, "field": field},
        "span": span, "section": section, "parent": parent,
        "no_sync": no_sync, "conflict": None,
    }


def _event_occurrences(ev: dict, lo: str, hi: str) -> list:
    """One occurrence per covered day inside the window.

    A multi-day event emits one per day rather than one row carrying a span,
    because a range query for September must see an event that started on
    31 August. Each carries `span` so the week view can draw it as one bar and
    know which end it is looking at.
    """
    first = ev["date"]
    last = ev["end_date"] or ev["date"]
    if last < lo or first > hi:
        return []
    base = ev["block_id"] or new_block_id(ev["file"], first, td.normalise(ev["title"]))
    d0 = datetime.date.fromisoformat(first)
    total = (datetime.date.fromisoformat(last) - d0).days + 1
    out = []
    for n in range(total):
        day = (d0 + datetime.timedelta(days=n)).isoformat()
        if not (lo <= day <= hi):
            continue
        multi = total > 1
        out.append(_occ(
            "event", f"{base}@{day}" if multi else base, day, _display(ev["title"]),
            path=ev["file"], line=ev["line"], block_id=ev["block_id"],
            start=ev["start"] if (n == 0 or not multi) else None,
            end=ev["end"] if (n == total - 1 or not multi) else None,
            span=({"start": first, "end": last, "index": n, "length": total}
                  if multi else None),
            no_sync=ev["no_sync"], raw=ev["raw"], cancelled=ev["cancelled"]))
    return out


def _task_occurrences(tasks: list) -> list:
    """Open, dated checkboxes as occurrences.

    Both `resolve()` and `/api/tasks` go through here, which is the point: one
    definition of *an open dated task is a commitment on a day*, rather than the
    endpoint keeping its own and drifting from the calendar it feeds.

    Done boxes are dropped — `todo.scan` collects them for completion detection,
    but the calendar shows what is still owed. An undated task is queue work,
    not a commitment on a day, and has no place to be drawn.
    """
    return [_occ("task", t["id"], t["deadline"], t["text"],
                 path=t["file"], line=t["line"], raw=t["raw"],
                 priority=t["priority"], no_sync=t["no_sync"])
            for t in tasks if not t["done"] and t["deadline"]]


def due_tasks(vault, split=None, ttl: float = None) -> list:
    """Every open dated task, unbounded by range — what `/api/tasks` serves.

    Deliberately not `resolve(a_very_wide_range)`: expansion is per-day, so a
    range wide enough to hold every deadline would walk decades of calendar per
    recurrence rule to answer a question about checkboxes.
    """
    src = collect_cached(vault, split, CACHE_TTL if ttl is None else ttl)
    return _task_occurrences(src["tasks"])


def _conflicts(occurrences: list) -> int:
    """Flag a note whose own tasks disagree with its frontmatter date.

    The case the brief names, and the only one detectable without guessing: a
    note says it is due on one day and a checkbox inside it says another. Both
    stay visible and both get told about the other — resolving it is a decision,
    and nothing here is entitled to make it.
    """
    by_path: dict = {}
    for o in occurrences:
        by_path.setdefault(o["source"]["path"], []).append(o)
    n = 0
    for path, group in by_path.items():
        notes = [o for o in group if o["kind"] == "note"]
        tasks = [o for o in group if o["kind"] == "task"]
        if not notes or not tasks:
            continue
        for note in notes:
            others = [t for t in tasks if t["date"] != note["date"]]
            if not others:
                continue
            note["conflict"] = {"with": [t["id"] for t in others],
                                "why": f"the note's {note['source']['field']}: is "
                                       f"{note['date']}; a task in it says "
                                       f"{others[0]['date']}"}
            n += 1
            for t in others:
                t["conflict"] = {"with": [note["id"]],
                                 "why": f"this note's {note['source']['field']}: "
                                        f"says {note['date']}"}
    return n


def resolve(vault, frm: str, to: str, split=None, ttl: float = CACHE_TTL) -> dict:
    """Everything on the calendar between two dates, merged, with provenance.

    The one place that answers "what is due". `/api/tasks` is folded in behind
    this at P2 rather than left running beside it — two endpoints computing the
    same thing from two scans is the disagreement this module exists to prevent.
    """
    lo, hi = _valid_date(frm), _valid_date(to)
    if not lo or not hi or lo > hi:
        return {"from": frm, "to": to, "occurrences": [], "conflicts": 0,
                "timezone": None, "problems": [], "error": "bad range"}
    span = (datetime.date.fromisoformat(hi) - datetime.date.fromisoformat(lo)).days
    if span > MAX_RANGE_DAYS:
        return {"from": lo, "to": hi, "occurrences": [], "conflicts": 0,
                "timezone": None, "problems": [],
                "error": f"range too wide ({span} days, max {MAX_RANGE_DAYS})"}

    src = collect_cached(vault, split, ttl)

    # **Conflicts are found over every note and task in the vault, then the
    # window is applied** — not the other way round. A note due today whose own
    # checkbox says next week is in disagreement whether or not you happened to
    # ask for next week; detecting it only when both sides fall inside the range
    # makes the warning blink in and out as you page through months, which is a
    # worse failure than not having it, because it teaches you to distrust it.
    dated = [_occ("note", n["file"], n["date"], _display(n["title"]),
                  path=n["file"], field=n["field"], no_sync=n["no_sync"])
             for n in src["notes"]]
    dated += _task_occurrences(src["tasks"])
    _conflicts(dated)

    out = [o for o in dated if lo <= o["date"] <= hi]

    for ev in src["events"]:
        out.extend(_event_occurrences(ev, lo, hi))

    for ru in src["rules"]:
        for day in expand(ru, lo, hi):
            rid = ru["rule_id"] or f"sg-rule-{td.task_id(ru['file'], ru['title'])}"
            out.append(_occ("rule", f"{rid}@{day}", day, _display(ru["title"]),
                            path=ru["file"], line=ru["line"], rule_id=rid,
                            start=ru["start"], end=ru["end"],
                            no_sync=ru["no_sync"], raw=ru["raw"]))

    out.sort(key=lambda o: (o["date"], o["start"] or "", o["kind"], o["title"]))
    # How many occurrences *in this window* disagree with another source. Both
    # sides of a disagreement are flagged, so a note and its own task count as
    # two — the number is "how many rows carry a warning", which is what the UI
    # can actually point at, not an estimate of how many arguments exist.
    return {"from": lo, "to": hi, "occurrences": out,
            "conflicts": sum(1 for o in out if o["conflict"]),
            "timezone": src["timezone"], "problems": src["problems"]}


def committed_hours(occurrences: list) -> dict:
    """{date: hours} from timed occurrences only.

    P3 renders this number and nothing acts on it; P7 is where it may shrink the
    queue window. Untimed occurrences contribute nothing rather than a guessed
    default — a deadline is not an hour of work, and inventing one would put a
    fabricated number in front of a real decision.
    """
    hours: dict = {}
    for o in occurrences:
        if not o["start"] or not o["end"] or o.get("cancelled"):
            continue                     # a cancelled hour is not a spent one
        sh, sm = (int(x) for x in o["start"].split(":"))
        eh, em = (int(x) for x in o["end"].split(":"))
        mins = (eh * 60 + em) - (sh * 60 + sm)
        if mins > 0:
            hours[o["date"]] = round(hours.get(o["date"], 0.0) + mins / 60.0, 2)
    return hours
