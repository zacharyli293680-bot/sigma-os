"""
The 06:00 review's arithmetic (retro.py).

The claim this feature makes is that the score is *auditable* — that you can
recompute it by hand and get the same number, and that a model never touched
it. So these tests are about the arithmetic and its edges, not about the prose:
what happens with no history, no deadlines, no courses, a re-run of a day
already recorded, and an index that did not parse.

The adversarial case throughout is the one that would quietly inflate or deflate
a score rather than fail: a component counted as zero when it had nothing to
measure, a day scored against its own recorded row, a completion attributed to
the wrong day.
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

import retro as rv                                  # noqa: E402
import todo                                         # noqa: E402

DAY = "2026-08-01"
OPEN = lambda v, rels: (set(), set())               # noqa: E731 — nothing sealed


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


# --------------------------------------------------------------------------
# the components, in isolation
# --------------------------------------------------------------------------

def facts(**kw):
    base = {"date": DAY, "index_ok": True, "adopted": DAY, "covered": True,
            "done": [], "weighted": 0.0,
            "by_section": {k: 0 for k in todo.SECTIONS},
            "deadlines_due": 0, "deadlines_met": 0, "missed": [],
            "courses_with_timelines": [], "advanced": [], "starved": [],
            "visible": 0, "queued": 0}
    base.update(kw)
    return base


class TestComponents(unittest.TestCase):
    def test_throughput_is_measured_against_your_own_median(self):
        p = rv.components(facts(weighted=4.4), [3.1, 3.1, 3.1])
        self.assertEqual(p["T"], 5.0)                       # clamped
        p = rv.components(facts(weighted=1.55), [3.1, 3.1, 3.1])
        self.assertEqual(p["T"], 2.5)

    def test_throughput_is_omitted_until_there_is_a_baseline(self):
        """Scoring against one or two samples says more about the sample."""
        self.assertNotIn("T", rv.components(facts(weighted=9.0), [3.1, 3.1]))
        self.assertIn("T", rv.components(facts(weighted=9.0), [3.1, 3.1, 3.1]))

    def test_three_days_of_nothing_is_a_real_baseline(self):
        self.assertEqual(rv.components(facts(weighted=1.0), [0, 0, 0])["T"], 5.0)
        self.assertEqual(rv.components(facts(weighted=0.0), [0, 0, 0])["T"], 0.0)

    def test_adherence_is_omitted_when_nothing_was_due(self):
        """A day with no deadlines did not fail to meet any. Counting it 0/5
        would punish a Tuesday for being a Tuesday."""
        self.assertNotIn("A", rv.components(facts(), []))
        self.assertEqual(rv.components(facts(deadlines_due=2, deadlines_met=1), [])["A"],
                         2.5)

    def test_a_day_before_the_queue_existed_is_not_scored_as_zero(self):
        """Completions were never recorded then. Reporting 0/5 throughput would
        be a confident claim about a day nobody was measuring — and the first
        week of reviews would all be about days that predate the index."""
        f = facts(covered=False, adopted="2026-08-01",
                  courses_with_timelines=["AA-210"], advanced=[])
        p = rv.components(f, [3.1, 3.1, 3.1])
        self.assertNotIn("T", p)
        self.assertNotIn("M", p)
        self.assertIsNone(rv.score_of(p))
        # deadlines still work: they live in the notes, which predate the index
        f["deadlines_due"], f["deadlines_met"] = 2, 2
        self.assertEqual(rv.components(f, [3.1, 3.1, 3.1])["A"], 5.0)

    def test_the_uncovered_note_says_so_rather_than_showing_a_bare_zero(self):
        text = rv.note_text(facts(covered=False, adopted="2026-08-01"), {}, None, "")
        self.assertIn("predates the task queue", text)
        self.assertIn("2026-08-01", text)

    def test_momentum_is_omitted_when_no_course_has_a_timeline(self):
        self.assertNotIn("M", rv.components(facts(), []))
        p = rv.components(facts(courses_with_timelines=["AA-210", "MATH-208"],
                                advanced=["AA-210"]), [])
        self.assertEqual(p["M"], 2.5)


class TestScore(unittest.TestCase):
    def test_the_worked_example(self):
        """4 done (1 PC, 2 course, 1 misc) = 4.4 weighted, median 3.1, 1/1
        deadline, 1 of 2 frontiers: 0.40(5) + 0.35(5) + 0.25(2.5) = 4.375."""
        p = rv.components(facts(weighted=4.4, deadlines_due=1, deadlines_met=1,
                                courses_with_timelines=["AA-210", "MATH-208"],
                                advanced=["AA-210"]), [3.1, 3.1, 3.1])
        self.assertEqual((p["T"], p["A"], p["M"]), (5.0, 5.0, 2.5))
        self.assertEqual(rv.score_of(p), 4)

    def test_missing_components_renormalise_rather_than_score_zero(self):
        """Adherence alone at 5/5 is a 5, not a 1.75."""
        self.assertEqual(rv.score_of({"A": 5.0}), 5)
        self.assertEqual(rv.score_of({"T": 5.0, "M": 0.0}), 3)   # .40*5/.65

    def test_nothing_measurable_is_no_score_rather_than_zero(self):
        self.assertIsNone(rv.score_of({}))
        self.assertEqual(rv.stars(None), "—")
        self.assertEqual(rv.stars(4), "★★★★☆")

    def test_the_score_is_never_out_of_range(self):
        for w in (0, 0.1, 50, 500):
            p = rv.components(facts(weighted=w, deadlines_due=1, deadlines_met=1), [1, 1, 1])
            self.assertTrue(0 <= rv.score_of(p) <= 5)


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.rows = Path(self.tmp.name) / "reviews.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *rows):
        self.rows.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                             encoding="utf-8")

    def test_a_rerun_never_scores_a_day_against_its_own_row(self):
        """Otherwise re-running a review moves its score."""
        self.write({"date": "2026-07-30", "weighted": 1.0},
                   {"date": "2026-07-31", "weighted": 2.0},
                   {"date": DAY, "weighted": 99.0})
        self.assertEqual(rv.history(self.rows, before=DAY), [1.0, 2.0])

    def test_a_torn_tail_line_is_skipped_not_fatal(self):
        self.rows.write_text('{"date":"2026-07-30","weighted":1.0}\n{"date":"2026-0',
                             encoding="utf-8")
        self.assertEqual(rv.history(self.rows), [1.0])

    def test_history_is_capped_to_the_window(self):
        self.write(*[{"date": f"2026-06-{i:02d}", "weighted": float(i)}
                     for i in range(1, 26)])
        self.assertEqual(len(rv.history(self.rows)), rv.WINDOW)

    def test_a_missing_file_is_simply_no_history(self):
        self.assertEqual(rv.history(self.rows), [])
        self.assertIsNone(rv.latest(self.rows))

    def test_latest_reads_the_newest_usable_row(self):
        self.write({"date": "2026-07-30", "weighted": 1.0, "score": 2},
                   {"date": "2026-07-31", "weighted": 2.0, "score": 4})
        self.assertEqual(rv.latest(self.rows)["date"], "2026-07-31")


# --------------------------------------------------------------------------
# against a real vault
# --------------------------------------------------------------------------

class ReviewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")
        self.index = Path(self.tmp.name) / "todo.state.json"
        self.rows = Path(self.tmp.name) / "reviews.jsonl"
        self._ledger = rv.ledger.LEDGER_PATH
        rv.ledger.LEDGER_PATH = Path(self.tmp.name) / "ledger.jsonl"

    def tearDown(self):
        rv.ledger.LEDGER_PATH = self._ledger
        self.tmp.cleanup()

    def note(self, rel, body):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def adopt(self, today=DAY):
        return todo.build(vault=self.vault, index_path=self.index, today=today,
                          split=OPEN)

    def facts(self, date=DAY):
        return rv.day_facts(date, vault=self.vault, index_path=self.index, split=OPEN)


class TestDayFacts(ReviewBase):
    def test_completions_are_counted_by_queue_and_weighted(self):
        p = self.note("02-Areas/ProCertus/Todo.md", "- [ ] a\n- [ ] b\n")
        self.note("02-Areas/Clubs/tt.md", "- [ ] c\n")
        self.adopt()
        p.write_text("- [x] a\n- [ ] b\n", encoding="utf-8")
        self.adopt()
        f = self.facts()
        self.assertEqual(f["by_section"]["procertus"], 1)
        self.assertEqual(f["weighted"], 1.5)
        self.assertEqual([d["text"] for d in f["done"]], ["a"])

    def test_a_deadline_met_early_is_still_met(self):
        """Finishing Monday's task on Sunday is not a miss."""
        p = self.note("02-Areas/ProCertus/Todo.md", "- [ ] ship it 📅 2026-08-02\n")
        self.adopt(today="2026-08-01")
        p.write_text("- [x] ship it 📅 2026-08-02\n", encoding="utf-8")
        self.adopt(today="2026-08-01")           # completed on the 1st
        f = self.facts("2026-08-02")             # due on the 2nd
        self.assertEqual((f["deadlines_due"], f["deadlines_met"]), (1, 1))

    def test_an_unmet_deadline_is_named(self):
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] ship it 📅 2026-08-01\n")
        self.adopt()
        f = self.facts()
        self.assertEqual((f["deadlines_due"], f["deadlines_met"]), (1, 0))
        self.assertEqual(f["missed"], ["ship it"])

    def test_momentum_counts_only_the_timeline_moving(self):
        self.note("02-Areas/Academics/AA-210/aa-210.md",
                  "---\ntype: course-index\nstatus: active\n---\n")
        tl = self.note("02-Areas/Academics/AA-210/timeline.md", "- [ ] day 2\n- [ ] day 3\n")
        tasks = self.note("02-Areas/Academics/AA-210/tasks.md", "- [ ] email the TA\n")
        self.adopt()

        # An ad-hoc course task is work, but it is not the frontier advancing.
        tasks.write_text("- [x] email the TA\n", encoding="utf-8")
        self.adopt()
        self.assertEqual(self.facts()["advanced"], [])

        tl.write_text("- [x] day 2\n- [ ] day 3\n", encoding="utf-8")
        self.adopt()
        self.assertEqual(self.facts()["advanced"], ["AA-210"])

    def test_a_course_without_a_timeline_is_not_counted_against_momentum(self):
        self.note("02-Areas/Academics/CSE-351/cse-351.md",
                  "---\ntype: course-index\nstatus: active\n---\n")
        self.assertEqual(self.facts()["courses_with_timelines"], [])

    def test_long_ignored_work_is_surfaced(self):
        self.note("02-Areas/ProCertus/Todo.md",
                  "".join(f"- [ ] task {i}\n" for i in range(6)))
        self.adopt(today="2026-07-01")            # adopted, so it has an age
        f = rv.day_facts("2026-08-01", vault=self.vault, index_path=self.index,
                         split=OPEN)
        self.assertTrue(f["starved"])
        self.assertGreaterEqual(f["starved"][0]["days"], rv.STARVED_DAYS)


class TestRun(ReviewBase):
    def run_it(self, **kw):
        return rv.run(date=DAY, vault=self.vault, index_path=self.index,
                      rows_path=self.rows, split=OPEN, **kw)

    def test_a_dry_run_writes_nothing_and_calls_no_model(self):
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] a\n")
        self.adopt()
        saved, rv.call_model = rv.call_model, self.fail
        try:
            r = self.run_it(dry_run=True)
        finally:
            rv.call_model = saved
        self.assertTrue(r["ok"])
        self.assertFalse((self.vault / rv.REVIEWS).exists())
        self.assertFalse(self.rows.exists())
        self.assertIn("type: review", r["note"])

    def test_it_writes_a_note_a_row_and_one_commit(self):
        p = self.note("02-Areas/ProCertus/Todo.md", "- [ ] a\n")
        self.adopt()
        p.write_text("- [x] a\n", encoding="utf-8")
        self.adopt()
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "work")

        saved, rv.call_model = rv.call_model, lambda *a, **k: "ProCertus moved."
        try:
            r = self.run_it()
        finally:
            rv.call_model = saved

        self.assertTrue(r["ok"])
        body = (self.vault / r["file"]).read_text(encoding="utf-8")
        self.assertTrue(body.startswith(f"---\ntype: review\ndate: {DAY}\n"))
        self.assertIn("ProCertus moved.", body)
        self.assertIn("tags: [review]", body)
        row = json.loads(self.rows.read_text(encoding="utf-8").strip())
        self.assertEqual((row["date"], row["weighted"]), (DAY, 1.5))

    def test_the_model_can_never_change_the_number(self):
        """It is handed the finished score and asked for prose. A model that
        answers with a different score changes nothing."""
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] a 📅 2026-08-01\n")
        self.adopt()
        saved = rv.call_model
        rv.call_model = lambda *a, **k: "score: 5/5. Outstanding day! ★★★★★"
        try:
            r = self.run_it()
        finally:
            rv.call_model = saved
        self.assertEqual(r["components"], {"A": 0.0})     # the deadline was missed
        self.assertEqual(r["score"], 0)
        row = json.loads(self.rows.read_text(encoding="utf-8").strip())
        self.assertEqual(row["score"], 0)

    def test_a_model_failure_still_produces_the_review(self):
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] a\n")
        self.adopt()
        saved = rv.call_model
        def boom(*a, **k):
            raise RuntimeError("claude is not on PATH")
        rv.call_model = boom
        try:
            r = self.run_it()
        finally:
            rv.call_model = saved
        self.assertTrue(r["ok"])
        self.assertIn("type: review", (self.vault / r["file"]).read_text(encoding="utf-8"))

    def test_an_unreadable_index_refuses_to_score(self):
        """Completion dates are the entire input. Scoring off an index that did
        not parse would invent a day that never happened, and then record it."""
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] a\n")
        self.index.write_text('{"tasks": {"broke', encoding="utf-8")
        r = self.run_it()
        self.assertFalse(r["ok"])
        self.assertFalse(self.rows.exists())

    def test_rerunning_a_day_replaces_its_note_rather_than_doubling_it(self):
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] a\n")
        self.adopt()
        saved, rv.call_model = rv.call_model, lambda *a, **k: "ok"
        try:
            self.run_it()
            r = self.run_it()
        finally:
            rv.call_model = saved
        body = (self.vault / r["file"]).read_text(encoding="utf-8")
        self.assertEqual(body.count("# Review —"), 1)


if __name__ == "__main__":
    unittest.main()
