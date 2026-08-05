#!/usr/bin/env python3
"""
retro.py — the 06:00 retrospective that replaced the 09:00 rebuild.

**Not named review.py.** `interface/backend/review.py` already owns that name
(it is the proposal approve/merge API), and panels.py puts `runtime/` at the
front of sys.path — so a `runtime/review.py` would have been what `app.py`'s
`import review` resolved to, silently replacing the proposal API with this. The
same shadowing hazard that made the queue engine `todo.py` rather than
`queue.py`. The CLI verb is still `sigma review`.

The old todo list was re-planned every morning: a specialist read the vault and
rewrote the day's daily note. The queues maintain themselves, so there is
nothing left to rebuild — what is worth doing each morning is looking at
*yesterday* and saying what actually moved.

**The score is arithmetic, and the model never touches it.** Python computes a
0–5 from three components; the model is handed the finished numbers and asked
for one or two sentences of narrative. This is the whole point of the split: a
number a model assigns is a number that drifts with its mood, and a
productivity score you cannot audit is one you stop believing by the second
week. The code here never reads a number back out of the model's answer.

    T  throughput   yesterday's weighted completions against your own trailing
                    14-day median, so the scale calibrates to your workload
                    instead of to a constant someone invented
    A  adherence    deadlines met / deadlines that came due
    M  momentum     active courses with a task completed — the standing goal is
                    one from every course, every day, so this is a straight
                    touched/active fraction

    score = round(0.40·T + 0.35·A + 0.25·M)

A component with nothing to measure is **omitted and the weights renormalise**,
rather than counted as zero. A day with no deadlines due did not fail to meet
any; scoring that as 0/5 adherence would punish a Tuesday for being a Tuesday.

Two outputs, because they answer different questions: one note per day in
`06-System/reviews/` that you read, and one row in `runtime/reviews.jsonl` that
the next review's median and the dashboard strip read.

It reports; it never completes anything. applier.py refuses any proposal that
ticks a checkbox because completion is a human signal, and the same rule holds
here by construction — nothing in this file writes to a task.
"""
import argparse
import datetime
import json
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import todo as td                                        # noqa: E402
from sigma import (DEFAULT_VAULT, call_model, gitops, ledger,  # noqa: E402
                   make_logger, write_note)

VAULT = DEFAULT_VAULT
REVIEWS = "06-System/reviews"
LOG_PATH = HERE / "review.log"
ROWS_PATH = HERE / "reviews.jsonl"
TASK_NAME = "SigmaOS-DailyReview"
MODEL = "haiku"

# What a completion is worth, by section. ProCertus highest because a missed
# internship commitment costs someone else's time; misc lowest because it is
# where errands live. These are the one genuinely arbitrary numbers in the file
# and the place to start if a score ever feels wrong.
WEIGHT = {"procertus": 1.5, "courses": 1.2, "projects": 1.0, "misc": 0.5}
COMPONENT_WEIGHT = {"T": 0.40, "A": 0.35, "M": 0.25}
# Below this many recorded days there is no meaningful median, and scoring
# throughput against one or two samples says more about the sample than the day.
MIN_HISTORY = 3
WINDOW = 14
# A task that has been eligible and ignored this long is worth a mention. It is
# a nudge in the narrative, never an action — hiding something because it was
# ignored is how a queue quietly loses work.
STARVED_DAYS = 14

log = make_logger(LOG_PATH, "review")


# --------------------------------------------------------------------------
# the facts
# --------------------------------------------------------------------------

def day_facts(date: str, vault=None, index_path=None, split=None) -> dict:
    """Everything deterministic about one day. No scoring, no model.

    Reads the scan for deadlines (they live in the note, not the index) and the
    index for completion dates (they live in the index, not the note). Neither
    half can answer alone.
    """
    vault = Path(vault or VAULT)
    found = td.scan(vault, split=split)
    index, readable = td.load_index(index_path)
    tasks = index.get("tasks") or {}
    # Completion dates only exist from the day the queue adopted the vault.
    # Before that the index has nothing to say, and "0 completed" would be a
    # confident claim about a day nobody was recording.
    adopted = index.get("adopted")
    covered = bool(adopted) and date >= adopted

    done, by_section = [], {k: 0 for k in td.SECTIONS}
    for tid, e in tasks.items():
        if e.get("completed_at") != date:
            continue
        sec = e.get("section") or td.section_of(e.get("file") or "")[0]
        by_section[sec] = by_section.get(sec, 0) + 1
        done.append({"id": tid, "text": e.get("text", ""), "section": sec,
                     "file": e.get("file", "")})
    weighted = round(sum(WEIGHT.get(s, 0.5) * n for s, n in by_section.items()), 2)

    # Deadlines that came due on the day, met or not. A task is met if it was
    # completed on or before its own due date — finishing Monday's task on
    # Sunday is not a miss.
    due, met, missed = 0, 0, []
    for f in found:
        if f["deadline"] != date:
            continue
        due += 1
        when = (tasks.get(f["id"]) or {}).get("completed_at")
        if f["done"] and when and when <= date:
            met += 1
        else:
            missed.append(f["text"])

    # Momentum: one task from each active course, every day — Zach's stated
    # policy (2026-08-04), and the denominator is *every* active course rather
    # than only those carrying a timeline.md. The old rule scored the frontier
    # advancing, which is the better idea and the wrong measure here: three of
    # the five active courses have no timeline, so no amount of work on them
    # could move the score at all. A component two thirds of your courses
    # cannot reach is not measuring momentum, it is measuring which folders
    # happen to hold a timeline.
    courses = td.active_courses(vault)
    advanced, frontier = set(), set()
    for tid, e in tasks.items():
        if e.get("completed_at") != date:
            continue
        parts = (e.get("file") or "").split("/")
        if len(parts) >= 4 and parts[1] == "Academics" and parts[2] in courses:
            advanced.add(parts[2])
            # The frontier distinction survives, in the narrative rather than in
            # the arithmetic: "emailed the TA" and "finished the next timeline
            # block" both count as showing up, and the review should still be
            # able to say which one it was.
            if td.is_chain_file(e["file"]):
                frontier.add(parts[2])

    # Starvation: eligible, never suppressed, and sitting still.
    q = td.build(vault=vault, index_path=index_path, today=date, split=split,
                 persist=False)
    starved = []
    for key in td.SECTIONS:
        for t in q["sections"][key]["queue"]:
            if t["parts"]["age_days"] >= STARVED_DAYS:
                starved.append({"text": t["text"], "section": key,
                                "days": t["parts"]["age_days"]})
    starved.sort(key=lambda s: -s["days"])

    return {"date": date, "index_ok": readable,
            "adopted": adopted, "covered": covered,
            "done": done, "by_section": by_section, "weighted": weighted,
            "deadlines_due": due, "deadlines_met": met, "missed": missed,
            "active_courses": sorted(courses),
            "advanced": sorted(advanced),
            "frontier": sorted(frontier),
            "starved": starved[:5],
            "visible": q["counts"]["visible"], "queued": q["counts"]["queued"]}


def history(rows_path=None, before: str | None = None) -> list:
    """Past `weighted` values, newest last, excluding `before` and after."""
    p = Path(rows_path or ROWS_PATH)
    out = []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue                        # a torn tail line, skip
        if not isinstance(r, dict) or "weighted" not in r:
            continue
        if before and str(r.get("date", "")) >= before:
            continue                        # a re-run must not score against itself
        out.append(float(r["weighted"]))
    return out[-WINDOW:]


# --------------------------------------------------------------------------
# the score
# --------------------------------------------------------------------------

def components(facts: dict, past: list) -> dict:
    """The three 0–5 components. A key is absent when it had nothing to measure."""
    out: dict = {}

    # A day before the queue existed has no recorded completions, so throughput
    # and momentum would both read zero — and be wrong. Adherence still works:
    # deadlines live in the notes, which predate the index.
    if not facts.get("covered", True):
        if facts["deadlines_due"] > 0:
            out["A"] = 5.0 * facts["deadlines_met"] / facts["deadlines_due"]
        return {k: round(v, 2) for k, v in out.items()}

    if len(past) >= MIN_HISTORY:
        median = statistics.median(past)
        if median > 0:
            out["T"] = max(0.0, min(5.0, 5.0 * facts["weighted"] / median))
        else:
            # Three days of nothing is a real baseline: anything clears it.
            out["T"] = 5.0 if facts["weighted"] > 0 else 0.0

    if facts["deadlines_due"] > 0:
        out["A"] = 5.0 * facts["deadlines_met"] / facts["deadlines_due"]

    n = len(facts["active_courses"])
    if n:
        out["M"] = 5.0 * len(facts["advanced"]) / n

    return {k: round(v, 2) for k, v in out.items()}


def score_of(parts: dict):
    """0–5, or None when nothing could be measured.

    Renormalised over the components that are present: a day with no deadlines
    due did not fail to meet any, and counting that as 0/5 would punish a
    Tuesday for being a Tuesday.
    """
    if not parts:
        return None
    total = sum(COMPONENT_WEIGHT[k] for k in parts)
    return round(sum(COMPONENT_WEIGHT[k] * v for k, v in parts.items()) / total)


def stars(score) -> str:
    return "—" if score is None else "★" * score + "☆" * (5 - score)


# --------------------------------------------------------------------------
# the narrative — the only part a model writes
# --------------------------------------------------------------------------

NARRATIVE_PROMPT = """\
Write ONE or TWO sentences about yesterday's work. Plain, specific, no preamble,
no bullet points, no heading, no markdown. Address the reader as "you".

The numbers below are already final and were computed deterministically. Do NOT
restate the score, do NOT assign a score of your own, and do NOT praise or
scold. Say what actually moved and what did not — name the specific course,
project or task, and prefer the thing that has been still the longest.

Date: {date}
Completed yesterday, by queue: {by_section}
Deadlines that came due: {due} · met: {met}{missed}
Active courses: {courses} · touched yesterday: {advanced} · of those, the
timeline itself moved for: {frontier}
The standing goal is one task from every active course, every day — name the
courses that went untouched.
Sitting still 14+ days: {starved}
Queue right now: {visible} visible, {queued} waiting"""


def narrative(facts: dict, model: str = MODEL, timeout: int = 60) -> str:
    """One or two sentences. Never a number, and never allowed to fail loudly."""
    missed = facts["missed"]
    prompt = NARRATIVE_PROMPT.format(
        date=facts["date"],
        by_section=", ".join(f"{k} {v}" for k, v in facts["by_section"].items() if v)
                   or "nothing",
        due=facts["deadlines_due"], met=facts["deadlines_met"],
        missed=(" · missed: " + "; ".join(m[:60] for m in missed[:3])) if missed else "",
        courses=", ".join(facts["active_courses"]) or "none",
        advanced=", ".join(facts["advanced"]) or "none",
        frontier=", ".join(facts["frontier"]) or "none",
        starved="; ".join(f"{s['text'][:50]} ({s['days']}d)" for s in facts["starved"])
                or "nothing",
        visible=facts["visible"], queued=facts["queued"])
    try:
        out = call_model(prompt, model, timeout=timeout, actor="review").strip()
    except Exception as e:
        log(f"narrative failed: {type(e).__name__}: {e}")
        return ""
    # One paragraph, no markdown scaffolding, and never long enough to bury the
    # numbers it sits next to.
    out = " ".join(out.replace("#", "").replace("*", "").split())
    return out[:400]


# --------------------------------------------------------------------------
# the note and the row
# --------------------------------------------------------------------------

def note_text(facts: dict, parts: dict, score, said: str) -> str:
    rows = [f"| {k} | {n} | {v:.2f} |" for k, (n, v) in {
        "T": ("throughput", parts.get("T")), "A": ("adherence", parts.get("A")),
        "M": ("momentum", parts.get("M"))}.items() if v is not None]
    done = "\n".join(
        f"- {d['section']} — {d['text'][:90]}" for d in facts["done"]) or "- nothing"
    starved = "\n".join(
        f"- {s['text'][:80]} — {s['section']}, {s['days']}d untouched"
        for s in facts["starved"])

    return (
        f"---\ntype: review\ndate: {facts['date']}\n"
        f"score: {'' if score is None else score}\n"
        f"weighted: {facts['weighted']}\ntags: [review]\n---\n\n"
        f"# Review — {facts['date']}\n\n"
        f"> {stars(score)}  ·  {facts['weighted']} weighted  ·  "
        f"{facts['deadlines_met']}/{facts['deadlines_due']} deadlines  ·  "
        f"{len(facts['advanced'])}/{len(facts['active_courses'])} courses touched\n\n"
        + (f"{said}\n\n" if said else "")
        + ("" if facts.get("covered", True) else
           f"> ⚠ This day predates the task queue, which started recording on "
           f"**{facts.get('adopted')}**. Completions and course momentum are not "
           f"*zero* here — they were never recorded.\n\n")
        + ("| | component | 0–5 |\n|---|---|---|\n" + "\n".join(rows) + "\n\n"
           if rows else
           "*Nothing measurable yet — the components need a few days of recorded "
           "work, a deadline that came due, or an active course.*\n\n")
        + f"## Completed\n\n{done}\n\n"
        + (f"## Sitting still\n\n{starved}\n\n" if starved else "")
        + f"## Queue\n\n{facts['visible']} visible · {facts['queued']} waiting\n\n"
        + "---\n\n*Written by `sigma review`. The score is arithmetic — see "
          "`runtime/retro.py`; only the sentence above it was written by a model.*\n")


def row_of(facts: dict, parts: dict, score) -> dict:
    return {"date": facts["date"], "score": score,
            "weighted": facts["weighted"], "by_section": facts["by_section"],
            "components": parts,
            "deadlines_due": facts["deadlines_due"],
            "deadlines_met": facts["deadlines_met"],
            "advanced": len(facts["advanced"]),
            "frontier": len(facts["frontier"]),
            "courses": len(facts["active_courses"]),
            "visible": facts["visible"], "queued": facts["queued"]}


def append_row(row: dict, rows_path=None) -> bool:
    p = Path(rows_path or ROWS_PATH)
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        log(f"could not append row: {e}")
        return False


def latest(rows_path=None) -> dict | None:
    """The newest recorded review, for GET /api/review."""
    p = Path(rows_path or ROWS_PATH)
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and r.get("date"):
            return r
    return None


def run(date: str | None = None, vault=None, dry_run: bool = False,
        model: str = MODEL, rows_path=None, index_path=None, split=None) -> dict:
    """Review one day. Defaults to yesterday, which is what 06:00 is for."""
    vault = Path(vault or VAULT)
    if date is None:
        date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    facts = day_facts(date, vault=vault, index_path=index_path, split=split)
    if not facts["index_ok"]:
        # Completion dates are the entire input. Scoring off an index that did
        # not parse would invent a day that never happened, and then record it.
        log("the task index did not parse — refusing to score")
        return {"ok": False, "why": "index unreadable"}

    parts = components(facts, history(rows_path, before=date))
    score = score_of(parts)
    said = "" if dry_run else narrative(facts, model=model)
    rel = f"{REVIEWS}/{date}.md"
    text = note_text(facts, parts, score, said)

    if dry_run:
        return {"ok": True, "dry_run": True, "date": date, "score": score,
                "components": parts, "facts": facts, "note": text, "file": rel}

    dest = vault / rel
    sha = None
    try:
        with gitops.vault_write(vault) as w:
            dest.parent.mkdir(parents=True, exist_ok=True)
            existed = dest.exists()
            write_note(dest, text)
            res = w.commit(rel, f"sigma(review): {'update' if existed else 'create'} "
                                f"{rel} - {stars(score)}")
            sha = res["sha"]
    except gitops.GitBusy as e:
        log(f"vault busy: {e}")
        return {"ok": False, "why": f"vault busy ({e})"}
    except OSError as e:
        log(f"write failed: {e}")
        return {"ok": False, "why": str(e)}

    ledger.record("review", "create", rel, sha,
                  f"review {date}: {stars(score)} ({facts['weighted']} weighted)")
    append_row(row_of(facts, parts, score), rows_path)
    log(f"{date}: {stars(score)} - {facts['weighted']} weighted, "
        f"{facts['deadlines_met']}/{facts['deadlines_due']} deadlines")
    return {"ok": True, "date": date, "score": score, "components": parts,
            "file": rel, "sha": sha, "facts": facts}


# --------------------------------------------------------------------------
# schedule
# --------------------------------------------------------------------------

def install_schedule(time_of_day="06:00"):
    """Daily at 06:00 — before the day starts, about the day that ended."""
    cmd = f'"{sys.executable}" "{Path(__file__).resolve()}"'
    r = subprocess.run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", cmd,
                        "/sc", "daily", "/st", time_of_day, "/f", "/rl", "limited"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        print(f"installed '{TASK_NAME}' - daily at {time_of_day}")
        print(f"  runs: {cmd}")
        # Same settings the other two scheduled tasks learned to carry:
        # StartWhenAvailable so a machine asleep at 06:00 does not silently skip
        # the day, and a retry so one blip does not cost a review.
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"$s = Get-ScheduledTask -TaskName '{TASK_NAME}'; "
                        f"$s.Settings.RestartCount = 3; "
                        f"$s.Settings.RestartInterval = 'PT10M'; "
                        f"$s.Settings.StartWhenAvailable = $true; "
                        f"Set-ScheduledTask -TaskName '{TASK_NAME}' "
                        f"-Settings $s.Settings | Out-Null"],
                       capture_output=True, text=True, timeout=30)
    else:
        print(f"could not install: {(r.stderr or r.stdout).strip()}")
    return r.returncode


def cmd_status() -> int:
    last = latest()
    print(f"review     rows: {ROWS_PATH.name}")
    if last:
        print(f"  last: {last['date']}  {stars(last.get('score'))}  "
              f"{last.get('weighted')} weighted")
    else:
        print("  never run")
    r = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME],
                       capture_output=True, timeout=20)
    print(f"  schedule: {'installed' if r.returncode == 0 else 'NOT installed'}"
          f"{'' if r.returncode == 0 else '  ->  sigma review --install-schedule'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score yesterday and write it down.")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="review this day instead")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the note; call no model and write nothing")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--install-schedule", action="store_true")
    ap.add_argument("--at", default="06:00", help="time for --install-schedule")
    a = ap.parse_args(argv)

    if a.status:
        return cmd_status()
    if a.install_schedule:
        return install_schedule(a.at)

    r = run(date=a.date, dry_run=a.dry_run)
    if not r.get("ok"):
        print(f"review: {r.get('why')}", file=sys.stderr)
        return 1
    if a.dry_run:
        print(r["note"])
        return 0
    print(f"review {r['date']}: {stars(r['score'])}  ->  {r['file']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
