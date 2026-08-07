#!/usr/bin/env python3
"""
leetcode.py — the daily-problem habit: one log, one streak, one score component.

The ask was "a daily task where completing it means entering the number of the
problem I solved". The obvious shape — mint `- [ ] Do a LeetCode problem` every
morning — is wrong here for two independent reasons, and both are worth writing
down because they will look like oversights otherwise.

**A repeated checkbox is not a repeated task.** `todo.task_id` hashes the file
path and the *cleaned* text, so an identical line minted daily collides into one
task with one identity and one age. Putting the date in the visible text fixes
the collision and creates a worse problem: every skipped day leaves an open box
behind forever, and a queue that accumulates permanent debt for days that are
already over is a queue you stop reading.

**The vault already settled this shape.** On 2026-08-04 the standing goal "one
task from each course, every day" was deliberately *not* written as five
schedule.md rule rows — it became the review's momentum component. A daily
LeetCode problem is that same claim about what you finish rather than a
commitment on a clock, so it gets the same treatment: no checkbox stands for the
obligation, and arithmetic measures whether you met it.

So what exists is a log, not a to-do:

    02-Areas/Career/leetcode.md
    ## Solved
    - [x] 217 Contains Duplicate · easy · array, hash-table ✅ 2026-08-06

Each line is a **completed** checkbox, which is doing real work rather than
being decorative. `todo.scan` collects done boxes too, so a logged problem is a
genuine completion in the Misc queue, lands in the 06:00 review's "Completed"
list, and feeds throughput at misc weight. It never appears in an open queue
because it is already ticked. One line, both jobs.

`✅ YYYY-MM-DD` is the Tasks plugin's own done-date notation, so Obsidian
understands it, `todo.META_RE` already strips it from display text, and the
streak has a date to read on every line without needing context.

**The number is the identity.** Calendar events carry `^sg-evt-<8 hex>` because
they get rescheduled and cancelled by id; a solved problem is append-only and
already has a natural key, so there is no block ID here and nothing to keep in
sync. It is also what makes "have I done this one?" answerable, which was half
the original ask.

**Backfilling by hand is the one hazard.** `todo.reconcile` stamps a checkbox it
has never seen before, already ticked, as completed *today* — correct for a line
this module just wrote, wrong for twenty historical problems pasted in at once,
which would all report as finished this morning and inflate one day's
throughput. `log()` writes one line at a time and is therefore safe; a bulk
paste is not. Add old problems on the day you solve them, or accept that the
review for that day will read high.

Network use is confined to `catalogue()` and `topics_of()`, both read-only
against public LeetCode endpoints, both cached, and both optional: with no
network `sigma leetcode 217` still logs the problem, just without its title.
"""
import argparse
import datetime
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from sigma import (DEFAULT_VAULT, frontmatter, gitops, ledger,  # noqa: E402
                   make_logger, write_note)

VAULT = DEFAULT_VAULT
LOG_REL = "02-Areas/Career/leetcode.md"
HEADING = "## Solved"
LOG_PATH = HERE / "leetcode.log"
# `.state.json`, so .gitignore's existing `runtime/*.state.json` rule covers it.
# It is a cache of a public catalogue, not a record — losing it costs one fetch.
CACHE_PATH = HERE / "leetcode.state.json"

DIFFICULTIES = ("easy", "medium", "hard")
LEVEL = {1: "easy", 2: "medium", 3: "hard"}
SEP = " · "

CATALOGUE_URL = "https://leetcode.com/api/problems/all/"
GRAPHQL_URL = "https://leetcode.com/graphql"
# LeetCode adds problems weekly and never renumbers one, so a month-old
# catalogue is wrong only about problems newer than the cache. Refetching on a
# miss (see `lookup`) covers that case without a scheduled refresh.
CACHE_DAYS = 30
NET_TIMEOUT = 15
UA = "Mozilla/5.0 (compatible; sigma-os personal vault tool)"

log = make_logger(LOG_PATH, "leetcode")

# `- [x] 217 <anything> ✅ 2026-08-06`. The done-date is required: a line without
# one cannot say which day it belongs to, and a streak computed from lines that
# do not all carry a date is a streak that silently skips them.
LINE_RE = re.compile(
    r"^\s*[-*]\s+\[[xX]\]\s+(\d+)\s+(.*?)\s*✅\s*(\d{4}-\d{2}-\d{2})\s*$")
REVISIT_RE = re.compile(r"\s*\(revisit\s+(\d+)\)\s*$")


# --------------------------------------------------------------------------
# the line
# --------------------------------------------------------------------------

def compose(n: int, title: str = "", difficulty: str = "",
            topics=None, date: str = "", revisit: int = 0) -> str:
    """One log line, in the grammar this module's docstring documents.

    Everything after the number is optional, because the whole point of the
    entry surface is that you type a number. A problem logged offline with no
    title is still a problem you solved on a day.
    """
    head = str(int(n))
    if title:
        head += " " + " ".join(str(title).split())
    if revisit > 1:
        head += f" (revisit {revisit})"
    bits = [head]
    d = (difficulty or "").strip().lower()
    if d in DIFFICULTIES:
        bits.append(d)
    tags = [" ".join(str(t).split()) for t in (topics or []) if str(t).strip()]
    if tags:
        bits.append(", ".join(tags))
    return f"- [x] {SEP.join(bits)} ✅ {date or datetime.date.today().isoformat()}"


def parse_line(raw: str) -> dict | None:
    """One log line -> an entry, or None if it is not one.

    Deliberately strict about the shape it accepts and forgiving about what
    sits inside it: a hand-typed line with no difficulty and no topics is a
    valid record of a solved problem, and refusing it would mean the streak
    disagrees with what is plainly written in the note.
    """
    m = LINE_RE.match(raw or "")
    if not m:
        return None
    n, body, date = int(m.group(1)), m.group(2), m.group(3)
    fields = [f.strip() for f in body.split("·")]
    title = fields[0].strip()
    revisit = 1
    rv = REVISIT_RE.search(title)
    if rv:
        revisit = int(rv.group(1))
        title = REVISIT_RE.sub("", title).strip()
    # Difficulty is only ever the second field. Scanning every field for one of
    # three words would let a topic named "hard" become the difficulty, and a
    # record that quietly rewrites itself is worse than one that omits a field.
    difficulty = ""
    rest = fields[1:]
    if rest and rest[0].lower() in DIFFICULTIES:
        difficulty, rest = rest[0].lower(), rest[1:]
    topics = [t.strip() for f in rest for t in f.split(",") if t.strip()]
    return {"n": n, "title": title, "difficulty": difficulty,
            "topics": topics, "date": date, "revisit": revisit, "raw": raw}


# --------------------------------------------------------------------------
# the log note
# --------------------------------------------------------------------------

def log_path(vault=None, rel: str = LOG_REL) -> Path:
    return Path(vault or VAULT) / rel


def entries(vault=None, rel: str = LOG_REL) -> list:
    """Every solved problem in the log, in document order.

    Reads the markdown and nothing else. There is no sidecar for this and there
    should not be: the note says the number, the title, the difficulty and the
    day, so a second store could only ever disagree with it.
    """
    p = log_path(vault, rel)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue                    # a documented example is not a solve
        e = parse_line(line)
        if e:
            out.append(e)
    return out


def started(vault=None, rel: str = LOG_REL) -> str | None:
    """The first day this habit was being measured, or None if it never was.

    Frontmatter first, because the note can be created before the first solve
    and a day between those two points is a day you *did* miss. Falling back to
    the earliest entry keeps a hand-made log without frontmatter working.
    """
    p = log_path(vault, rel)
    try:
        fm = frontmatter(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if fm.get("started"):
        return str(fm["started"]).strip()
    ds = [e["date"] for e in entries(vault, rel)]
    return min(ds) if ds else None


# --------------------------------------------------------------------------
# what the log knows
# --------------------------------------------------------------------------

def solved_on(items: list, date: str) -> list:
    return [e for e in items if e["date"] == date]


def by_number(items: list) -> dict:
    """{number: first entry}. First rather than last, so "when did I do this?"
    answers with the day you actually first solved it."""
    out = {}
    for e in items:
        out.setdefault(e["n"], e)
    return out


def streak(items: list, today: str) -> dict:
    """Current and longest run of consecutive days with at least one solve.

    Today counts as unbroken while it is still today: at 09:00 you have not
    missed the day, you have not done it yet. So the current run is measured
    from today if today has a solve, and from yesterday otherwise — which is
    also why `at_risk` exists separately rather than being inferred from a
    streak of zero.
    """
    days = sorted({e["date"] for e in items})
    if not days:
        return {"current": 0, "longest": 0, "last": None, "at_risk": False}

    longest = run = 1
    for a, b in zip(days, days[1:]):
        run = run + 1 if _next_day(a) == b else 1
        longest = max(longest, run)

    t = datetime.date.fromisoformat(today)
    have = set(days)
    anchor = today if today in have else (t - datetime.timedelta(days=1)).isoformat()
    current = 0
    cur = anchor
    while cur in have:
        current += 1
        cur = (datetime.date.fromisoformat(cur) - datetime.timedelta(days=1)).isoformat()
    return {"current": current, "longest": longest, "last": days[-1],
            "at_risk": today not in have and current > 0}


def _next_day(d: str) -> str:
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()


def stats(items: list, today: str | None = None) -> dict:
    today = today or datetime.date.today().isoformat()
    uniq = by_number(items)
    counts = {d: 0 for d in DIFFICULTIES}
    for e in uniq.values():
        if e["difficulty"] in counts:
            counts[e["difficulty"]] += 1
    return {"total": len(uniq), "entries": len(items), "by_difficulty": counts,
            "unknown": len(uniq) - sum(counts.values()),
            "today": len(solved_on(items, today)),
            "streak": streak(items, today)}


# --------------------------------------------------------------------------
# the score component
# --------------------------------------------------------------------------

def practice_score(begin: str | None, date: str, solved: int) -> float | None:
    """The review's P component: 5.0, 0.0, or None. The whole rule, in one place.

    Pure, and it lives here rather than in retro.py so that the module owning
    the habit owns its arithmetic — retro calls this with facts it already has,
    and `component()` below calls it after reading the vault. Two callers, one
    implementation, which is the rule the queue's parsing grammar already
    follows for the same reason.

    **Binary on purpose.** The thing being measured is whether you showed up,
    and a difficulty-weighted version scores a day you solved an Easy below
    full marks — punishing you for the exact behaviour the habit is meant to
    produce. Difficulty is recorded on every line and reported in the stats and
    the narrative; it just does not move the number.

    None means *unmeasurable*, not zero, and it is the same rule the rest of
    retro.py already follows: a day before the log existed did not fail to
    solve a problem, nobody was counting. Without this the component would
    stamp a 0 on every day in the vault's history the first time a review is
    re-run over one.
    """
    if not begin or date < begin:
        return None
    return 5.0 if solved else 0.0


def component(vault=None, date: str | None = None, rel: str = LOG_REL,
              items=None) -> float | None:
    """practice_score for one day, read straight from the vault."""
    date = date or datetime.date.today().isoformat()
    items = entries(vault, rel) if items is None else items
    return practice_score(started(vault, rel), date, len(solved_on(items, date)))


# --------------------------------------------------------------------------
# the public catalogue — number -> title, slug, difficulty
# --------------------------------------------------------------------------

def _get(url, data=None, timeout=NET_TIMEOUT) -> dict | None:
    """One JSON read. Never raises: every caller has a working answer without it."""
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Referer": "https://leetcode.com",
                 **({"Content-Type": "application/json"} if body else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        log(f"{url.split('/')[2]} unreachable: {type(e).__name__}: {e}")
        return None


def _load_cache(path=None) -> dict:
    try:
        d = json.loads(Path(path or CACHE_PATH).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("problems"), dict) else {}
    except (OSError, ValueError):
        return {}


def catalogue(path=None, refresh: bool = False, today: str | None = None) -> dict:
    """{"217": ["Contains Duplicate", "contains-duplicate", "easy"], ...}

    Fetched once and cached, because it is 2 MB of JSON to answer a question
    about one number. Only the three fields that end up on a line are kept — a
    cache holding everything the endpoint returns would be a second, staler copy
    of LeetCode's database sitting in a folder that syncs nowhere.
    """
    today = today or datetime.date.today().isoformat()
    cache = _load_cache(path)
    fresh = cache.get("fetched", "") >= (
        datetime.date.fromisoformat(today) - datetime.timedelta(days=CACHE_DAYS)
    ).isoformat()
    if cache.get("problems") and fresh and not refresh:
        return cache["problems"]

    data = _get(CATALOGUE_URL)
    pairs = (data or {}).get("stat_status_pairs")
    if not pairs:
        return cache.get("problems") or {}      # stale beats nothing
    problems = {}
    for row in pairs:
        st = row.get("stat") or {}
        n = st.get("frontend_question_id")
        if n is None:
            continue
        problems[str(n)] = [st.get("question__title") or "",
                            st.get("question__title_slug") or "",
                            LEVEL.get((row.get("difficulty") or {}).get("level"), "")]
    try:
        Path(path or CACHE_PATH).write_text(
            json.dumps({"fetched": today, "problems": problems}), encoding="utf-8")
    except OSError as e:
        log(f"could not cache the catalogue: {e}")   # costs a refetch, nothing else
    return problems


TOPICS_QUERY = ("query q($t:String!){question(titleSlug:$t)"
                "{topicTags{slug}}}")


def topics_of(slug: str) -> list:
    """Topic tags for one problem. Absent from the catalogue endpoint, so this
    is a second call — and a strictly optional one: a line with no topics is
    still a complete record of a solved problem."""
    if not slug:
        return []
    d = _get(GRAPHQL_URL, {"query": TOPICS_QUERY, "variables": {"t": slug}})
    try:
        return [t["slug"] for t in d["data"]["question"]["topicTags"]][:4]
    except (TypeError, KeyError, IndexError):
        return []


def lookup(n: int, path=None, want_topics: bool = True, today=None) -> dict:
    """Everything a line needs, from the number alone. Never raises.

    A miss triggers exactly one forced refresh, for the case the cache is real
    but predates the problem — and then gives up, because a number that is not
    in a freshly fetched catalogue is a number that does not exist, and
    retrying would just be slower about saying so.
    """
    key = str(int(n))
    cat = catalogue(path, today=today)
    row = cat.get(key)
    if row is None and cat:
        row = catalogue(path, refresh=True, today=today).get(key)
    if not row:
        return {"title": "", "slug": "", "difficulty": "", "topics": []}
    title, slug, difficulty = (row + ["", "", ""])[:3]
    return {"title": title, "slug": slug, "difficulty": difficulty,
            "topics": topics_of(slug) if want_topics else []}


# --------------------------------------------------------------------------
# writing one line
# --------------------------------------------------------------------------

def splice(body: str, line: str, heading: str = HEADING) -> str:
    """Append under `heading`, creating it at the end of the note if absent.

    todo.splice does this already and is not reused: it is the queue's writer
    for the queue's own destinations, and importing it here would make a change
    to quick-add's placement rules silently change where solved problems land.
    Ten lines of duplication beats that coupling.
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


def add(n, vault=None, date: str | None = None, title: str = "",
        difficulty: str = "", topics=None, again: bool = False,
        offline: bool = False, rel: str = LOG_REL, cache_path=None) -> dict:
    """Log one solved problem. The only writer in this module.

    No new write path: the same mutex → pull → write → commit → ledger sequence
    every other Sigma write uses, so this appears in the activity ledger with a
    working undo like anything else.

    The duplicate check is the feature, not a guard — "have I already done
    217?" is half of what a record of solved problems is for. `again` turns a
    refusal into a revisit, which is written with a distinct suffix so that
    `todo.task_id` sees a different task rather than silently deduplicating the
    second attempt into the first.
    """
    try:
        n = int(str(n).strip().lstrip("#"))
    except ValueError:
        return {"ok": False, "why": f"'{n}' is not a problem number"}
    if n <= 0:
        return {"ok": False, "why": "a problem number starts at 1"}

    vault = Path(vault or VAULT)
    date = date or datetime.date.today().isoformat()
    dest = log_path(vault, rel)
    if not dest.exists():
        return {"ok": False, "why": f"{rel} does not exist — create the log note first"}

    items = entries(vault, rel)
    seen = by_number(items).get(n)
    if seen and not again:
        return {"ok": False, "why": f"already solved on {seen['date']}",
                "duplicate": seen}
    revisit = (max(e["revisit"] for e in items if e["n"] == n) + 1) if seen else 1

    found = {"title": title, "difficulty": difficulty, "topics": topics or []}
    if not offline and not (title and difficulty):
        got = lookup(n, cache_path)
        found = {"title": title or got["title"],
                 "difficulty": difficulty or got["difficulty"],
                 "topics": topics or got["topics"]}

    line = compose(n, found["title"], found["difficulty"], found["topics"],
                   date, revisit)
    # The only definition of a well-formed line that matters is the parser's,
    # so the composed line is read back before anything is committed — the same
    # round-trip the calendar write path makes for the same reason.
    back = parse_line(line)
    if not back or back["n"] != n or back["date"] != date:
        return {"ok": False, "why": "composed a line the parser will not read back"}

    try:
        with gitops.vault_write(vault) as w:
            body = dest.read_text(encoding="utf-8", errors="replace")
            # Re-read inside the mutex, after the pull: another writer (or
            # Obsidian) may have added the same problem while we were deciding.
            if not again and n in by_number(_parse_all(body)):
                return {"ok": False, "why": "already logged while this was running"}
            write_note(dest, splice(body, line))
            res = w.commit(rel, f"sigma(leetcode): {n}"
                                f"{' ' + found['title'] if found['title'] else ''}")
    except gitops.GitBusy as e:
        log(f"vault busy: {e}")
        return {"ok": False, "why": f"vault busy ({e})"}
    except OSError as e:
        log(f"write failed: {e}")
        return {"ok": False, "why": str(e)}

    ledger.record("leetcode", "append", rel, res["sha"],
                  f"logged LeetCode {n}"
                  f"{' ' + found['title'] if found['title'] else ''} on {date}")
    log(f"{date}: logged {n} {found['title']}".rstrip())
    return {"ok": True, "n": n, "date": date, "line": line, "file": rel,
            "sha": res["sha"], "note": res.get("note"), "revisit": revisit,
            **found}


def _parse_all(text: str) -> list:
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        e = parse_line(line)
        if e:
            out.append(e)
    return out


# --------------------------------------------------------------------------
# sigma leetcode
# --------------------------------------------------------------------------

def summary_line(vault=None, today: str | None = None, rel: str = LOG_REL) -> str:
    """One line for `sigma status`. Empty string when the log does not exist,
    so a vault without this habit shows nothing rather than a nag about it."""
    if not log_path(vault, rel).exists():
        return ""
    today = today or datetime.date.today().isoformat()
    s = stats(entries(vault, rel), today)
    st = s["streak"]
    if s["today"]:
        return (f"leetcode       {s['today']} today · {st['current']}-day streak · "
                f"{s['total']} solved")
    risk = f" · {st['current']}-day streak at risk" if st["at_risk"] else ""
    return f"leetcode       nothing logged today{risk} · {s['total']} solved"


def cmd_status(a) -> int:
    vault = Path(a.vault or VAULT)
    p = log_path(vault)
    if not p.exists():
        print(f"leetcode: no log note at {LOG_REL}")
        return 1
    today = a.date or datetime.date.today().isoformat()
    items = entries(vault)
    s = stats(items, today)
    st, c = s["streak"], s["by_difficulty"]
    print()
    print(f"  sigma leetcode · {today}")
    print(f"  {s['total']} solved · {c['easy']}E {c['medium']}M {c['hard']}H"
          + (f" · {s['unknown']} unrated" if s["unknown"] else ""))
    print(f"  streak {st['current']} day(s) · longest {st['longest']}"
          + (f" · last {st['last']}" if st["last"] else ""))
    if s["today"]:
        for e in solved_on(items, today):
            print(f"    ✓ {e['n']} {e['title']}"
                  + (f" · {e['difficulty']}" if e["difficulty"] else ""))
    else:
        print("    ⚠ nothing logged today"
              + ("  ·  the streak breaks at midnight" if st["at_risk"] else ""))
    if a.recent:
        print()
        for e in items[-a.recent:]:
            print(f"    {e['date']}  {e['n']:>5}  {e['title'][:44]}"
                  + (f"  {e['difficulty']}" if e["difficulty"] else ""))
    print()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="sigma leetcode",
        description="Log the day's problem by number; keep the record and the streak.")
    ap.add_argument("number", nargs="?", help="the problem number you solved")
    ap.add_argument("title", nargs="?", default="",
                    help="only needed if the lookup cannot reach LeetCode")
    ap.add_argument("--difficulty", choices=DIFFICULTIES, default="")
    ap.add_argument("--topics", default="", help="comma-separated, overrides the lookup")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="log it against another day")
    ap.add_argument("--again", action="store_true",
                    help="log a problem you have already solved, as a revisit")
    ap.add_argument("--offline", action="store_true", help="never touch the network")
    ap.add_argument("--recent", type=int, default=0, metavar="N",
                    help="with no number: also list the last N solves")
    ap.add_argument("--vault", metavar="PATH")
    a = ap.parse_args(argv)

    if not a.number:
        return cmd_status(a)

    r = add(a.number, vault=a.vault, date=a.date, title=a.title,
            difficulty=a.difficulty,
            topics=[t.strip() for t in a.topics.split(",") if t.strip()],
            again=a.again, offline=a.offline)
    if not r.get("ok"):
        print(f"leetcode: {r['why']}", file=sys.stderr)
        if r.get("duplicate"):
            print("          use --again to record it as a revisit", file=sys.stderr)
        return 1

    items = entries(a.vault)
    s = stats(items, r["date"])
    st = s["streak"]
    print(f"  logged  {r['n']} {r['title']}".rstrip()
          + (f" · {r['difficulty']}" if r["difficulty"] else "")
          + (f" · {', '.join(r['topics'])}" if r["topics"] else ""))
    print(f"  streak  {st['current']} day(s) · {s['total']} solved "
          f"({s['by_difficulty']['easy']}E {s['by_difficulty']['medium']}M "
          f"{s['by_difficulty']['hard']}H)")
    if r.get("note"):
        print(f"  note    {r['note']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
