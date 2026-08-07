"""
The daily-problem habit (leetcode.py) and its component in the 06:00 review.

The claims worth testing here are the ones that would fail *quietly*:

- a component that reads 0 on a day it could not be measured, which would stamp
  a zero on every day in the vault's history the first time a review is re-run;
- a duplicate that gets logged anyway, since "have I already done 217?" is half
  of what a record of solved problems is for;
- a composed line the parser cannot read back, which would leave a solve in the
  note that the streak silently skips;
- adding P moving a *historical* score, which would rewrite the median every
  later review is computed against.

Nothing here touches the network. `lookup` is stubbed wherever a write path
would otherwise reach for it, and one test asserts the offline path produces a
usable line on its own — the whole point of the fallback.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import isolation                                     # noqa: E402,F401
import leetcode as lc                                # noqa: E402
import retro as rv                                   # noqa: E402
import todo                                          # noqa: E402

DAY = "2026-08-06"
OPEN = lambda v, rels: (set(), set())                # noqa: E731 — nothing sealed


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


# --------------------------------------------------------------------------
# the line
# --------------------------------------------------------------------------

class TestLine(unittest.TestCase):
    def test_the_documented_line_round_trips(self):
        line = lc.compose(217, "Contains Duplicate", "easy",
                          ["array", "hash-table"], DAY)
        self.assertEqual(
            line, "- [x] 217 Contains Duplicate · easy · array, hash-table ✅ 2026-08-06")
        e = lc.parse_line(line)
        self.assertEqual((e["n"], e["title"], e["difficulty"], e["date"]),
                         (217, "Contains Duplicate", "easy", DAY))
        self.assertEqual(e["topics"], ["array", "hash-table"])

    def test_a_number_and_a_date_are_the_only_required_fields(self):
        """The entry surface is a number. A problem logged offline with no title
        is still a problem solved on a day, and refusing it would make the
        streak disagree with what is plainly written in the note."""
        e = lc.parse_line(lc.compose(217, date=DAY))
        self.assertEqual((e["n"], e["title"], e["difficulty"], e["topics"]),
                         (217, "", "", []))

    def test_a_topic_named_hard_cannot_become_the_difficulty(self):
        """Difficulty is only ever the second field. Scanning every field for
        one of three words would let a record quietly rewrite itself."""
        e = lc.parse_line("- [x] 4 Median of Two Sorted Arrays · hard ✅ 2026-08-06")
        self.assertEqual(e["difficulty"], "hard")
        e = lc.parse_line("- [x] 9 Palindrome Number · easy · math, hard ✅ 2026-08-06")
        self.assertEqual((e["difficulty"], e["topics"]), ("easy", ["math", "hard"]))

    def test_an_open_box_is_not_a_solve(self):
        """The log records what happened. An unticked line is an intention."""
        self.assertIsNone(lc.parse_line("- [ ] 217 Contains Duplicate ✅ 2026-08-06"))

    def test_a_line_without_a_done_date_is_refused(self):
        """A streak computed from lines that do not all carry a date is a streak
        that silently skips them."""
        self.assertIsNone(lc.parse_line("- [x] 217 Contains Duplicate · easy"))

    def test_a_revisit_keeps_the_title_and_is_a_distinct_line(self):
        line = lc.compose(217, "Contains Duplicate", "easy", [], DAY, revisit=2)
        e = lc.parse_line(line)
        self.assertEqual((e["title"], e["revisit"]), ("Contains Duplicate", 2))
        # distinct text -> distinct task id, so the queue counts it as its own
        # completion instead of deduplicating it into the original
        self.assertNotEqual(todo.task_id("a.md", todo.display_text(line[6:])),
                            todo.task_id("a.md", "217 Contains Duplicate · easy"))

    def test_a_fenced_example_is_not_a_solve(self):
        """The log note documents its own grammar, and CLAUDE.md's fenced
        example task already had to be excluded from the queue for this reason."""
        body = ("---\nstarted: 2026-08-01\n---\n\n```\n"
                "- [x] 1 Two Sum · easy ✅ 2026-08-01\n```\n\n## Solved\n"
                "- [x] 217 Contains Duplicate · easy ✅ 2026-08-06\n")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            p = Path(d) / "02-Areas" / "Career"
            p.mkdir(parents=True)
            (p / "leetcode.md").write_text(body, encoding="utf-8")
            self.assertEqual([e["n"] for e in lc.entries(d)], [217])


# --------------------------------------------------------------------------
# what the log knows
# --------------------------------------------------------------------------

def items(*pairs):
    return [{"n": n, "title": "", "difficulty": "", "topics": [], "date": d,
             "revisit": 1, "raw": ""} for n, d in pairs]


class TestStreak(unittest.TestCase):
    def test_today_not_yet_done_does_not_break_the_run(self):
        """At 09:00 you have not missed the day, you have not done it yet."""
        s = lc.streak(items((1, "2026-08-04"), (2, "2026-08-05")), "2026-08-06")
        self.assertEqual((s["current"], s["at_risk"]), (2, True))

    def test_a_solve_today_extends_it_and_clears_the_risk(self):
        s = lc.streak(items((1, "2026-08-04"), (2, "2026-08-05"), (3, "2026-08-06")),
                      "2026-08-06")
        self.assertEqual((s["current"], s["at_risk"]), (3, False))

    def test_a_missed_day_ends_the_run_without_being_at_risk(self):
        s = lc.streak(items((1, "2026-08-01"), (2, "2026-08-02")), "2026-08-06")
        self.assertEqual((s["current"], s["at_risk"]), (0, False))

    def test_two_problems_in_one_day_are_one_day_of_streak(self):
        s = lc.streak(items((1, "2026-08-05"), (2, "2026-08-05"), (3, "2026-08-06")),
                      "2026-08-06")
        self.assertEqual((s["current"], s["longest"]), (2, 2))

    def test_longest_survives_a_break(self):
        s = lc.streak(items((1, "2026-08-01"), (2, "2026-08-02"), (3, "2026-08-03"),
                            (4, "2026-08-06")), "2026-08-06")
        self.assertEqual((s["current"], s["longest"]), (1, 3))

    def test_an_empty_log_is_no_streak_rather_than_a_broken_one(self):
        self.assertEqual(lc.streak([], DAY),
                         {"current": 0, "longest": 0, "last": None, "at_risk": False})


class TestStats(unittest.TestCase):
    def test_a_revisit_is_not_a_second_solved_problem(self):
        s = lc.stats(items((217, "2026-08-01"), (217, "2026-08-06"),
                           (1, "2026-08-06")), "2026-08-06")
        self.assertEqual((s["total"], s["entries"], s["today"]), (2, 3, 2))

    def test_by_number_reports_the_first_time_you_solved_it(self):
        seen = lc.by_number(items((217, "2026-08-01"), (217, "2026-08-06")))
        self.assertEqual(seen[217]["date"], "2026-08-01")


# --------------------------------------------------------------------------
# the component
# --------------------------------------------------------------------------

class TestComponent(unittest.TestCase):
    def test_it_is_binary(self):
        self.assertEqual(lc.practice_score("2026-08-01", DAY, 1), 5.0)
        self.assertEqual(lc.practice_score("2026-08-01", DAY, 3), 5.0)
        self.assertEqual(lc.practice_score("2026-08-01", DAY, 0), 0.0)

    def test_a_day_before_the_log_existed_is_unmeasurable_not_zero(self):
        """Otherwise every day in the vault's history scores a zero the first
        time a review is re-run over it."""
        self.assertIsNone(lc.practice_score("2026-08-06", "2026-08-05", 0))
        self.assertIsNone(lc.practice_score(None, DAY, 0))
        self.assertEqual(lc.practice_score("2026-08-06", "2026-08-06", 0), 0.0)


class TestScoreIntegration(unittest.TestCase):
    def facts(self, **kw):
        base = {"date": DAY, "index_ok": True, "adopted": "2026-07-01", "covered": True,
                "done": [], "weighted": 0.0,
                "by_section": {k: 0 for k in todo.SECTIONS},
                "deadlines_due": 0, "deadlines_met": 0, "missed": [],
                "active_courses": [], "advanced": [], "frontier": [], "starved": [],
                "practice": [], "practice_started": "2026-08-01",
                "practice_streak": {"current": 0, "longest": 0, "last": None,
                                    "at_risk": False},
                "practice_total": 0, "visible": 0, "queued": 0}
        base.update(kw)
        return base

    def test_adding_p_did_not_move_any_historical_score(self):
        """The weights sum to 1.15 on purpose: score_of divides by whichever are
        present, so a day with no practice component renormalises over exactly
        0.40/0.35/0.25 — the ratios it had before P existed."""
        old = {"T": 5.0, "A": 5.0, "M": 2.5}
        self.assertEqual(rv.score_of(old), 4)                    # the worked example
        self.assertEqual(rv.score_of({"T": 5.0, "M": 0.0}), 3)   # .40*5/.65
        f = self.facts(practice_started=None, weighted=4.4, deadlines_due=1,
                       deadlines_met=1, active_courses=["AA-210", "MATH-208"],
                       advanced=["AA-210"])
        p = rv.components(f, [3.1, 3.1, 3.1])
        self.assertNotIn("P", p)
        self.assertEqual(rv.score_of(p), 4)

    def test_skipping_the_daily_problem_costs_part_of_the_day(self):
        done = rv.components(self.facts(practice=items((217, DAY)), weighted=4.4,
                                        deadlines_due=1, deadlines_met=1),
                             [3.1, 3.1, 3.1])
        skipped = rv.components(self.facts(weighted=4.4, deadlines_due=1,
                                           deadlines_met=1), [3.1, 3.1, 3.1])
        self.assertEqual((done["P"], skipped["P"]), (5.0, 0.0))
        self.assertGreater(rv.score_of(done), rv.score_of(skipped))

    def test_practice_is_measurable_even_on_a_day_the_queue_never_saw(self):
        """It reads the log, not the index — the two records are independent."""
        p = rv.components(self.facts(covered=False, practice=items((217, DAY))), [])
        self.assertEqual(p["P"], 5.0)
        self.assertNotIn("T", p)

    def test_the_note_shows_practice_in_the_table_and_the_header(self):
        f = self.facts(practice=items((217, DAY)),
                       practice_streak={"current": 3, "longest": 3, "last": DAY,
                                        "at_risk": False})
        text = rv.note_text(f, rv.components(f, []), 4, "")
        self.assertIn("| P | practice | 5.00 |", text)
        self.assertIn("1 LeetCode, 3d streak", text)

    def test_the_header_omits_practice_when_it_could_not_be_measured(self):
        """A day before the log started did not do zero problems."""
        f = self.facts(practice_started=None)
        text = rv.note_text(f, rv.components(f, []), None, "")
        self.assertNotIn("LeetCode", text)


# --------------------------------------------------------------------------
# writing one line, against a real vault
# --------------------------------------------------------------------------

NOTE = ("---\ntype: practice-log\npractice: leetcode\nstarted: 2026-08-01\n"
        "tags: [practice]\n---\n\n# LeetCode\n\n## Solved\n\n## Related\n\n- x\n")


class TestAdd(unittest.TestCase):
    def setUp(self):
        isolation.sandbox(self)
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "02-Areas" / "Career").mkdir(parents=True)
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        self.note = self.vault / lc.LOG_REL
        self.note.write_text(NOTE, encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "init")
        self.cache = Path(self.tmp.name) / "cache.json"
        self.cache.write_text(json.dumps({"fetched": DAY, "problems": {
            "217": ["Contains Duplicate", "contains-duplicate", "easy"]}}),
            encoding="utf-8")
        self._topics, lc.topics_of = lc.topics_of, lambda slug: ["array"]

    def tearDown(self):
        lc.topics_of = self._topics
        self.tmp.cleanup()

    def add(self, n, **kw):
        kw.setdefault("cache_path", self.cache)
        return lc.add(n, vault=self.vault, date=kw.pop("date", DAY), **kw)

    def body(self):
        return self.note.read_text(encoding="utf-8")

    def test_a_number_is_all_it_takes(self):
        r = self.add(217)
        self.assertTrue(r["ok"], r.get("why"))
        self.assertIn("- [x] 217 Contains Duplicate · easy · array ✅ 2026-08-06",
                      self.body())
        self.assertEqual([e["n"] for e in lc.entries(self.vault)], [217])

    def test_it_lands_under_solved_rather_than_at_the_end_of_the_note(self):
        self.add(217)
        b = self.body()
        self.assertLess(b.index("- [x] 217"), b.index("## Related"))

    def test_it_is_one_commit_with_a_ledger_row(self):
        r = self.add(217)
        self.assertTrue(r["sha"])
        show = _git(self.vault, "show", "--stat", "--format=%s", r["sha"]).stdout
        self.assertIn("sigma(leetcode): 217 Contains Duplicate", show)
        self.assertIn("leetcode.md", show)
        rows = [json.loads(x) for x in
                Path(rv.ledger.LEDGER_PATH).read_text(encoding="utf-8").splitlines() if x]
        self.assertEqual(rows[-1]["actor"], "leetcode")
        self.assertEqual(rows[-1]["sha"], r["sha"])

    def test_a_duplicate_is_refused_and_says_when_you_did_it(self):
        """The record answering 'have I done this one?' is half the point."""
        self.add(217, date="2026-08-04")
        r = self.add(217)
        self.assertFalse(r["ok"])
        self.assertIn("2026-08-04", r["why"])
        self.assertEqual(self.body().count("- [x] 217"), 1)

    def test_again_records_a_revisit_rather_than_a_second_identical_line(self):
        self.add(217, date="2026-08-04")
        r = self.add(217, again=True)
        self.assertTrue(r["ok"], r.get("why"))
        self.assertEqual(r["revisit"], 2)
        self.assertIn("(revisit 2)", self.body())

    def test_offline_still_logs_the_problem(self):
        """The fallback is the reason the lookup is allowed to be optional."""
        def boom(*a, **k):
            raise AssertionError("the network must not be touched")
        saved, lc._get = lc._get, boom
        try:
            r = self.add(9999, offline=True)
        finally:
            lc._get = saved
        self.assertTrue(r["ok"], r.get("why"))
        self.assertIn("- [x] 9999 ✅ 2026-08-06", self.body())

    def test_an_unreachable_lookup_is_not_a_failure(self):
        saved, lc._get = lc._get, lambda *a, **k: None
        self.cache.unlink()
        try:
            r = self.add(217)
        finally:
            lc._get = saved
        self.assertTrue(r["ok"], r.get("why"))
        self.assertEqual(lc.entries(self.vault)[0]["title"], "")

    def test_a_missing_log_note_is_refused_rather_than_created(self):
        """Creating it would mint frontmatter — including `started:`, which
        decides which days are scored — as a side effect of logging a problem."""
        self.note.unlink()
        r = self.add(217)
        self.assertFalse(r["ok"])
        self.assertIn("does not exist", r["why"])

    def test_a_nonsense_number_is_refused(self):
        for bad in ("abc", "0", "-3", ""):
            self.assertFalse(self.add(bad)["ok"], bad)

    def test_a_hash_prefix_is_accepted(self):
        self.assertTrue(self.add("#217")["ok"])

    def test_the_line_is_read_back_before_anything_is_committed(self):
        """The only definition of a well-formed line that matters is the
        parser's — the same round-trip the calendar write path makes."""
        saved, lc.parse_line = lc.parse_line, lambda raw: None
        try:
            r = self.add(217)
        finally:
            lc.parse_line = saved
        self.assertFalse(r["ok"])
        self.assertIn("read back", r["why"])
        self.assertNotIn("- [x] 217", self.body())
        self.assertEqual(_git(self.vault, "status", "--porcelain").stdout.strip(), "")


class TestQueueIntegration(unittest.TestCase):
    """A logged problem is a real completion, and never an open task."""

    def setUp(self):
        isolation.sandbox(self)
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "02-Areas" / "Career").mkdir(parents=True)
        self.note = self.vault / lc.LOG_REL
        self.note.write_text(NOTE, encoding="utf-8")
        self.index = Path(self.tmp.name) / "todo.state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, today=DAY):
        return todo.build(vault=self.vault, index_path=self.index, today=today,
                          split=OPEN)

    def test_a_solve_is_a_misc_completion_and_not_an_open_task(self):
        self.build(today="2026-08-05")                      # adopt while empty
        self.note.write_text(
            NOTE.replace("## Solved\n",
                         "## Solved\n- [x] 217 Contains Duplicate · easy ✅ 2026-08-06\n"),
            encoding="utf-8")
        self.build()
        f = rv.day_facts(DAY, vault=self.vault, index_path=self.index, split=OPEN)
        self.assertEqual(f["by_section"]["misc"], 1)
        self.assertEqual([d["text"] for d in f["done"]],
                         ["217 Contains Duplicate · easy"])
        self.assertEqual([e["n"] for e in f["practice"]], [217])
        self.assertEqual(rv.components(f, [])["P"], 5.0)
        # ...and nothing of it sits in an open queue
        self.assertEqual(self.build()["sections"]["misc"]["visible"], [])


if __name__ == "__main__":
    unittest.main()
