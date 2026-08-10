#!/usr/bin/env python3
"""
recall.py — spaced recall cards, and the study pace measured rather than
assumed (study mode, S8).

Two halves of one idea: **what you missed comes back, and what studying costs
is measured instead of guessed.**

**A card is a checkbox line in a note.** Markdown stays the truth here as
everywhere else — a card lives in `02-Areas/Academics/<COURSE>/<code>-recall.md`,
the queue scans it like any other task, ticking it is the same commit through
writes.py, and undo is the same one click. Nothing about a card is a second task
mechanism. Never `misc.md`: that note is a short hand-curated list holding live
deadlines, and one course's cards would bury it inside a week.

**A cap and an expiry, because a queue nobody can empty is one nobody reads.**
At most CAP open cards per course, and a card that has sat open EXPIRY_DAYS
retires itself. Retirement is a *status*, never a deletion: the row flips to
`[-]` and gains `expired::YYYY-MM-DD` — the grammar a skipped chain row and a
cancelled calendar event already use. `todo.TASK_RE` cannot see a `[-]` row, so
the queue is clean with no open box left behind, while the line stays in the
note, dimmed, saying what was once true.

The pair is also what keeps a card in its place *arithmetically*. A card is
written 🔽 (urgency low, 5.0) and carries no 📅 at all — a dated checkbox
becomes a deadline in `retro.py`'s adherence scan at weight 0.35, and a system
that raised its own dated tasks would be grading Zach against homework it set
itself. With aging at 0.5/day capped by the expiry, a card's score cannot pass
5 + EXPIRY_DAYS/2 = 12 before it retires, which is strictly below the 15.0 of
*any* unmarked task. A recall card can never outrank real work, and that is
arithmetic rather than good manners.

**The raise date is written `➕ YYYY-MM-DD`** — the Tasks plugin's own created
marker, already in `todo.META_RE`'s strip set. The note says when each card was
raised (Obsidian renders it natively, and the expiry sweep reads it back) while
the queue row still reads as a clean sentence. Neither end needed a new grammar.

**Spacing is by evidence, not by curve.** A card is raised from a miss, snoozed
one day so the review is never the same sitting as the mistake, and retired if
it goes stale. Missing the same thing again raises it again. There is no 1/3/7
ladder and no ease factor: this system has no scheduler that runs between
06:00 jobs, and a made-up interval dressed as spaced repetition would be exactly
the assumed number the other half of S8 exists to delete.

**Pace is measured from the vault, not from a sidecar.** Every rollup row in
`<code>-study-log.md` records a session; S8 adds `⏱ N min` of *active* time to
it, and the multiplier is (minutes actually spent on a finished module) ÷ (that
module's own `estimate:`), aggregated over finished modules. Both inputs are
committed notes, so the number survives a sidecar wipe and can be checked by
hand. Below MIN_MODULES measured modules it reports **unmeasured** rather than
1.0 — an assumed multiplier is the thing this is here to replace.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from sigma import DEFAULT_VAULT, make_logger, write_note  # noqa: E402
from sigma import gitops, ledger                          # noqa: E402

import lesson as ln     # noqa: E402
import todo as td       # noqa: E402

log = make_logger(HERE / "recall.log", "sigma recall")

CAP = 5                 # open cards per course
EXPIRY_DAYS = 14        # how long a card may sit open before it retires
FIRST_REVIEW_DAYS = 1   # the snooze a fresh card is raised with
MIN_MODULES = 3         # measured modules before a pace multiplier is reported
# A module left open in a background tab is not a two-hour module, and a module
# whose row says one minute was not studied. Both are dropped from the pool
# rather than averaged into it.
PACE_FLOOR, PACE_CEILING = 0.15, 5.0

HEADING = "## Cards"
SUFFIX = "-recall.md"   # todo.is_recall_file owns the predicate; this is the
                        # name it is built from, and the two must agree.


# --------------------------------------------------------------------------
# the card grammar
# --------------------------------------------------------------------------
# - [ ] Recall · <segment title> · [[<note>|<unit>]] — missed ×2 🔽 ➕ 2026-08-10
#
# Everything after the wikilink is metadata the queue strips: `display_text`
# removes the 🔽 and the ➕ date, so the row reads "Recall · <segment> · M04 —
# missed ×2" and `task_id` hashes that. Adding the expiry marker later flips the
# box, which takes the line out of TASK_RE entirely — the identity never has to
# survive the edit, because there is no task left to identify.

CARD_RE = re.compile(
    r"^\s*[-*]\s+\[( |x|X|-)\]\s+Recall\s+·\s+(.+?)\s+·\s+"
    r"\[\[([^\]|]+?)(?:\|([^\]]*))?\]\](.*)$")
RAISED_RE = re.compile(r"➕\s*(\d{4}-\d{2}-\d{2})")
EXPIRED_RE = re.compile(r"expired::(\d{4}-\d{2}-\d{2})")
MISSES_RE = re.compile(r"missed\s*×\s*(\d+)")


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def card_key(target: str, seg: str) -> tuple:
    """What makes two cards the same card: the note it points at and the
    segment it names. Not the miss count — that number is a fact about the
    session that raised it, and letting it into the key would make every
    repeat miss a second card for the same concept."""
    return (_norm(target), _norm(seg))


def rel_for(course: str) -> str:
    return f"02-Areas/Academics/{course}/{course.lower()}{SUFFIX}"


def compose(seg: str, target: str, label: str, misses: int, today: str) -> str:
    """One card line. `misses` is written `×N` in the digest's own shape, so a
    card and the study-log row it came from read the same way."""
    return (f"- [ ] Recall · {' '.join(str(seg).split())} · "
            f"[[{target}|{label}]] — missed ×{max(1, int(misses))} "
            f"🔽 ➕ {today}")


def card_task_id(rel: str, line: str) -> str | None:
    """The queue's own id for a card line, computed with the queue's own
    functions. The snooze that spaces a fresh card is keyed by it, and a second
    implementation of `display_text` here would silently key it to nothing."""
    m = td.TASK_RE.match(line)
    return td.task_id(rel, td.display_text(m.group(2))) if m else None


def parse_cards(text: str) -> list[dict]:
    """Every recognised card in a recall note, in document order.

    A line this does not recognise is *not* a card — it is a task somebody wrote
    in this note by hand, and the cap and the expiry both leave it alone. Sigma
    manages what Sigma raised.
    """
    out, in_fence = [], False
    for i, raw in enumerate(text.split("\n")):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = CARD_RE.match(raw.rstrip("\r"))
        if not m:
            continue
        box, seg, target, label, tail = m.groups()
        rm, em, mm = (RAISED_RE.search(tail), EXPIRED_RE.search(tail),
                      MISSES_RE.search(tail))
        out.append({
            "index": i, "raw": raw, "seg": seg.strip(),
            "target": target.strip(), "label": (label or target).strip(),
            "state": ("done" if box in "xX"
                      else "retired" if box == "-" else "open"),
            "raised": rm.group(1) if rm else None,
            "expired": em.group(1) if em else None,
            "misses": int(mm.group(1)) if mm else 1,
        })
    return out


def _retire(raw: str, today: str) -> str:
    cr = raw.endswith("\r")
    s = raw[:-1] if cr else raw
    s = s.replace("[ ]", "[-]", 1).rstrip() + f" expired::{today}"
    return s + "\r" if cr else s


def note_text(course: str, today: str) -> str:
    """The scaffold for a course's first card. A container note like a month
    note or a manifest, so the atomic-note rule does not apply to it.

    The one wikilink is the study log, which the session-end path has already
    proved exists — a scaffold that linked a note that might not be there would
    mint the dangling graph node the contract's linking rules forbid.
    """
    code = course.lower()
    return (
        "---\n"
        "type: recall\n"
        f"course: {course}\n"
        f"started: {today}\n"
        "tags: [guide]\n"
        "---\n\n"
        f"# {course} — recall\n\n"
        f"Cards Sigma raised from what was missed in [[{code}-study-log|the "
        f"study log]]. At most {CAP} stay open at once, and one that sits "
        f"{EXPIRY_DAYS} days retires itself — the line flips to `[-]` and gains "
        f"`expired::`, so it leaves the queue without leaving the record.\n\n"
        f"{HEADING}\n")


def link_in_index(text: str, code: str) -> str | None:
    """Put the recall note into the course index's note list, or None if it is
    already there or there is nowhere to put it.

    A course index that lists the sources but hides what was written from them
    is the wrong way round — the contract records that lesson from AA-210's
    intake, and a note nothing links to is the same failure with a different
    author. Appended to the `### Guide` group when the guide made one, else to
    `## Notes`; a course index with neither is left alone rather than
    restructured, because inventing sections in a hand-written manifest is not
    this function's business.
    """
    base = f"{code.lower()}{SUFFIX[:-3]}"
    if f"[[{base}]" in text or f"[[{base}|" in text:
        return None
    line = f"- [[{base}|recall cards raised from misses]]"
    for pattern in (r"^### Guide\s*$", r"^## Notes\s*$"):
        m = re.search(pattern, text, re.M)
        if not m:
            continue
        tail = text[m.end():]
        nxt = re.search(r"^#{2,3} ", tail, re.M)
        at = m.end() + (nxt.start() if nxt else len(tail))
        return (text[:at].rstrip("\n") + "\n" + line + "\n\n"
                + text[at:].lstrip("\n"))
    return None


def wanted_from(rows: list, units: dict) -> list[dict]:
    """The cards this session's misses deserve, worst first.

    One card per *segment*, because that is the unit a re-study decision is made
    at — the same choice `lesson.digest` already made about the rollup row, and
    the two must group the same way or the note and the card would disagree
    about what was missed. A miss whose unit has no note on disk is dropped: a
    card you cannot click through to is not actionable.
    """
    counts: dict = {}
    for r in rows:
        if str(r.get("result") or "") != "wrong":
            continue
        cp, mod = r.get("checkpoint"), r.get("module")
        unit = ("cp", int(cp)) if isinstance(cp, int) else (
            ("m", int(mod)) if isinstance(mod, int) else None)
        if unit is None or unit not in units:
            continue
        seg = str(r.get("seg_title") or r.get("qid") or "").strip()
        if not seg:
            continue
        key = (unit, seg)
        counts[key] = counts.get(key, 0) + 1
    out = []
    for (unit, seg), n in counts.items():
        target, label = units[unit]
        out.append({"seg": seg, "target": target, "label": label, "misses": n,
                    "unit": unit})
    out.sort(key=lambda w: (-w["misses"], w["label"], _norm(w["seg"])))
    return out


def units_for(vault: Path, course: str, split=None) -> dict:
    """{("m", 4): (basename, "M04"), ("cp", 1): (basename, "CP1")} — every unit
    of this course with a note to link to."""
    out = {}
    for m in ln.scan(vault, split=split):
        if _norm(m["course"]) == _norm(course) and isinstance(m["module"], int):
            out[("m", m["module"])] = (Path(m["file"]).stem, f"M{m['module']:02d}")
    for c in ln.scan_checkpoints(vault, split=split):
        if _norm(c["course"]) == _norm(course) and isinstance(c["checkpoint"], int):
            out[("cp", c["checkpoint"])] = (Path(c["file"]).stem,
                                            f"CP{c['checkpoint']}")
    return out


def reconcile(text: str, wanted: list, today: str, cap=CAP,
              expiry=EXPIRY_DAYS) -> dict:
    """Retire what has gone stale, then raise what is new — in that order, so a
    card retiring today frees its slot for the miss that happened today.

    Returns the whole new note text plus what changed, and `withheld`: how many
    misses did not become cards because the cap was full. That number is
    reported rather than swallowed — a cap that silently drops evidence is
    indistinguishable from a bug.
    """
    lines = text.split("\n")
    cards = parse_cards(text)
    horizon = (datetime.date.fromisoformat(today)
               - datetime.timedelta(days=expiry)).isoformat()

    expired = []
    for c in cards:
        # No `➕` means nobody here raised it — a hand-written card is Zach's,
        # and Sigma does not retire what it did not write.
        if c["state"] != "open" or not c["raised"]:
            continue
        if c["raised"] <= horizon:
            lines[c["index"]] = _retire(c["raw"], today)
            c["state"] = "retired"
            c["expired"] = today
            expired.append(c)

    open_keys = {card_key(c["target"], c["seg"]) for c in cards
                 if c["state"] == "open"}
    fresh = [w for w in wanted
             if card_key(w["target"], w["seg"]) not in open_keys]
    room = max(0, cap - len(open_keys))

    body = "\n".join(lines)
    raised = []
    for w in fresh[:room]:
        line = compose(w["seg"], w["target"], w["label"], w["misses"], today)
        body = td.splice(body, HEADING, line)
        raised.append({**w, "raw": line})

    return {"text": body, "raised": raised, "expired": expired,
            "withheld": max(0, len(fresh) - room),
            "open": len(open_keys) + len(raised)}


# --------------------------------------------------------------------------
# the measured pace
# --------------------------------------------------------------------------
# A study-log row: `- 2026-08-10 · M04 · ⏱ 22 min · 8 answered, 5 correct · ...`
# The date and the unit label are the row's first two fields (writes.py builds
# them), and `⏱ N min` is S8's addition. Rows written before S8 carry no time
# and simply contribute nothing — the pool is what was measured, never what was
# back-filled.

ROW_RE = re.compile(r"^\s*-\s+(\d{4}-\d{2}-\d{2})\s+·\s+([^·]+?)\s+·")
ROW_TIME_RE = re.compile(r"·\s*⏱\s*(\d+)\s*min")
SINGLE_MODULE_RE = re.compile(r"^M(\d+)$")


def study_rows(vault: Path, course: str) -> list[dict]:
    """Every session row of a course's study log: date, unit label, minutes."""
    folder = ln.course_folder(vault, course)
    if folder is None:
        return []
    p = folder / f"{folder.name.lower()}-study-log.md"
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    out, in_fence = [], False
    for raw in text.split("\n"):
        if raw.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = ROW_RE.match(raw.rstrip("\r"))
        if not m:
            continue
        t = ROW_TIME_RE.search(raw)
        out.append({"date": m.group(1), "label": m.group(2).strip(),
                    "minutes": int(t.group(1)) if t else None})
    return out


def _scanned(vault: Path, split=None, scan=None) -> list:
    """Every module note, scanned once per caller rather than once per question.

    `ln.scan` globs, reads, parses and *validates* every module note in the
    vault. Nothing here needs a second copy of that within one request, and the
    courses payload asks the pace question once per course — so callers that ask
    repeatedly pass the list they already have (see `panels._scan_courses`).
    """
    return ln.scan(vault, split=split) if scan is None else scan


def _pool(vault: Path, course: str, split=None, scan=None) -> dict:
    """(estimated, actual, n) over this course's *finished* modules.

    Finished, because a multiplier compares a whole module's estimate against
    the whole time it took — half a module studied today would otherwise be
    measured against a full module's estimate and report you as twice as fast
    as you are. Done-ness comes from the chain checkbox, which is Zach's click.

    A module named in a row beside another unit is dropped rather than split:
    `M04+CP1 · ⏱ 50 min` genuinely does not say how the fifty minutes divided,
    and inventing a division would put a made-up number into the one measurement
    S8 exists to make honest.
    """
    modules = {m["module"]: m for m in _scanned(vault, split, scan)
               if _norm(m["course"]) == _norm(course)
               and isinstance(m["module"], int)}
    minutes: dict = {}
    poisoned: set = set()
    for row in study_rows(vault, course):
        if not row["minutes"]:
            continue
        sm = SINGLE_MODULE_RE.match(row["label"])
        if sm:
            minutes[int(sm.group(1))] = minutes.get(int(sm.group(1)), 0) + row["minutes"]
        else:
            for part in row["label"].split("+"):
                pm = SINGLE_MODULE_RE.match(part.strip())
                if pm:
                    poisoned.add(int(pm.group(1)))

    done = set()
    g = ln.guide(vault, course, split=split)
    by_target = {Path(m["file"]).stem.casefold(): n for n, m in modules.items()}
    for row in (g or {}).get("rows") or []:
        if row["state"] == "done" and row.get("target"):
            n = by_target.get(_norm(row["target"]))
            if n is not None:
                done.add(n)

    est_total = act_total = 0
    measured = []
    for n, spent in sorted(minutes.items()):
        m = modules.get(n)
        if n in poisoned or n not in done or m is None or not m.get("estimate"):
            continue
        ratio = spent / m["estimate"]
        if not (PACE_FLOOR <= ratio <= PACE_CEILING):
            continue
        est_total += m["estimate"]
        act_total += spent
        measured.append({"module": n, "estimate": m["estimate"], "actual": spent})
    return {"estimate": est_total, "actual": act_total, "n": len(measured),
            "modules": measured}


def pace(vault=None, course: str | None = None, split=None, scan=None,
         pools=None) -> dict:
    """How long study actually takes, as a multiplier on the written estimate.

    `basis` says where the number came from and is never omitted: "course" when
    this course has enough finished modules of its own, "vault" when it borrows
    the whole vault's pool, and None when nothing has been measured yet — in
    which case `multiplier` is None rather than 1.0. A dashboard that cannot
    tell an unmeasured pace from an average one will print the average.

    `scan` and `pools` are the caller's scratch space, and asking for both is
    what keeps this linear. The vault-wide fallback runs whenever a course has
    fewer than three measured modules — which is every course until study mode
    has been used for a while — so a caller asking per course was doing N pools
    per course, each with its own full scan of every module note in the vault.
    Handing in one `{}` for the whole payload makes each pool cost once.
    """
    vault = Path(vault or DEFAULT_VAULT)
    seen = pools if pools is not None else {}

    def pool(code: str) -> dict:
        if code not in seen:
            seen[code] = _pool(vault, code, split=split, scan=scan)
        return seen[code]

    own = (pool(course) if course
           else {"estimate": 0, "actual": 0, "n": 0, "modules": []})
    if course and own["n"] >= MIN_MODULES:
        return {"course": course, "basis": "course", "n": own["n"],
                "estimate": own["estimate"], "actual": own["actual"],
                "multiplier": round(own["actual"] / own["estimate"], 2)}

    est = act = n = 0
    for code in td.active_courses(vault):
        p = pool(code)
        est, act, n = est + p["estimate"], act + p["actual"], n + p["n"]
    if n >= MIN_MODULES and est:
        return {"course": course, "basis": "vault", "n": n,
                "estimate": est, "actual": act,
                "multiplier": round(act / est, 2)}
    return {"course": course, "basis": None, "n": own["n"] if course else n,
            "estimate": own["estimate"], "actual": own["actual"],
            "multiplier": None}


def projected(vault: Path, course: str, measured: dict | None = None,
              split=None, scan=None) -> dict:
    """What finishing this course's guide is going to cost, in minutes.

    The estimate is the written one; `minutes` applies the measured multiplier
    when there is one and is None when there is not — deliberately, so a caller
    cannot silently render an unmeasured projection as a measured one. This is
    the number agenda P7 consumes when it lands; `agenda.committed_hours` is
    untouched, because that function counts hours actually on a clock and its
    own docstring refuses to invent any.
    """
    vault = Path(vault)
    measured = measured or pace(vault, course, split=split, scan=scan)
    g = ln.guide(vault, course, split=split)
    modules = {Path(m["file"]).stem.casefold(): m
               for m in _scanned(vault, split, scan)
               if _norm(m["course"]) == _norm(course)}
    left, est, unknown = 0, 0, 0
    for row in (g or {}).get("rows") or []:
        if row["state"] != "open":
            continue
        left += 1
        m = modules.get(_norm(row.get("target") or ""))
        if m and m.get("estimate"):
            est += m["estimate"]
        else:
            unknown += 1
    mult = measured.get("multiplier")
    return {"open": left, "estimate": est, "unestimated": unknown,
            "minutes": round(est * mult) if mult and est else None}


# --------------------------------------------------------------------------
# the daily sweep — expiry, whether or not anyone studied
# --------------------------------------------------------------------------

def sweep(vault=None, today=None, courses=None) -> dict:
    """Retire every card that has sat open long enough, across every course.

    Session-end reconciles the course you just studied; this is what retires
    cards for the course you stopped studying — which is the case the cap and
    the expiry exist for. One commit per note and one ledger row per commit, so
    undo stays a single click on a single course.
    """
    vault = Path(vault or DEFAULT_VAULT)
    today = today or datetime.date.today().isoformat()
    base = vault / "02-Areas" / "Academics"
    out = {"expired": 0, "notes": [], "problems": []}
    if not base.is_dir():
        return out

    names = courses if courses is not None else [d.name for d in sorted(base.iterdir())
                                                 if d.is_dir()]
    for code in names:
        p = vault / rel_for(code)
        if not p.is_file():
            continue
        rel = p.relative_to(vault).as_posix()
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            out["problems"].append(f"{rel}: {e}")
            continue
        res = reconcile(text, [], today)
        if not res["expired"]:
            continue
        try:
            with gitops.vault_write(vault) as w:
                write_note(p, res["text"])
                r = w.commit(rel, f"sigma(recall): retire {len(res['expired'])} "
                                  f"expired card(s) in {rel}")
        except gitops.GitBusy as e:
            out["problems"].append(f"{rel}: vault busy ({e})")
            continue
        except OSError as e:
            out["problems"].append(f"{rel}: {e}")
            continue
        ledger.record("recall", "update", rel, r["sha"],
                      f"{len(res['expired'])} recall card(s) expired in {code}")
        out["expired"] += len(res["expired"])
        out["notes"].append({"course": code, "file": rel,
                             "expired": len(res["expired"]), "sha": r["sha"]})
        log(f"{code}: retired {len(res['expired'])} card(s)")
    return out


def status(vault=None) -> list[dict]:
    """One row per course with a recall note or a guide: cards and pace."""
    vault = Path(vault or DEFAULT_VAULT)
    out = []
    for code in td.active_courses(vault):
        p = vault / rel_for(code)
        cards = parse_cards(p.read_text(encoding="utf-8-sig", errors="replace")
                            ) if p.is_file() else []
        m = pace(vault, code)
        out.append({
            "course": code, "file": p.relative_to(vault).as_posix() if p.is_file() else None,
            "open": sum(1 for c in cards if c["state"] == "open"),
            "done": sum(1 for c in cards if c["state"] == "done"),
            "retired": sum(1 for c in cards if c["state"] == "retired"),
            "pace": m,
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Recall cards and the measured pace.")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status", help="cards and pace, per course")
    sw = sub.add_parser("sweep", help="retire cards that have sat open too long")
    sw.add_argument("--course", help="limit to one course")
    a = ap.parse_args(argv)
    vault = Path(a.vault)

    if a.cmd == "sweep":
        res = sweep(vault, courses=[a.course] if a.course else None)
        print(f"retired {res['expired']} card(s) across {len(res['notes'])} note(s)")
        for pr in res["problems"]:
            print(f"  ! {pr}")
        return 1 if res["problems"] else 0

    for row in status(vault):
        m = row["pace"]
        pc = (f"pace ×{m['multiplier']} ({m['basis']}, n={m['n']})"
              if m["multiplier"] else f"pace unmeasured (n={m['n']})")
        print(f"{row['course']:<10} {row['open']} open, {row['done']} done, "
              f"{row['retired']} retired  ·  {pc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
