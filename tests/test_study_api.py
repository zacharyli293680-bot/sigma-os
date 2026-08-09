"""
Exam mode's data (panels._scan_study).

The claim this makes is "you have worked through N of M sources", so the tests
are about what counts as worked through — an embed, not a filename that looks
related — and about not inventing a date the vault never stated.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import json                                      # noqa: E402
import lesson as ln                              # noqa: E402
import panels                                    # noqa: E402
import privacy                                   # noqa: E402


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


class StudyBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.course = self.vault / "02-Areas" / "Academics" / "AA-210"
        (self.course / "Exams").mkdir(parents=True)
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")
        self._saved = (panels.VAULT, ln.ATTEMPTS_PATH)
        panels.VAULT = self.vault
        # The practice-history half reads the attempt log by module global —
        # pointed into the sandbox so the suite never reads this machine's.
        ln.ATTEMPTS_PATH = Path(self.tmp.name) / "study.jsonl"
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()

    def tearDown(self):
        panels.VAULT, ln.ATTEMPTS_PATH = self._saved
        panels._cache.clear()
        self.tmp.cleanup()

    def source(self, rel: str):
        p = self.course / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-fake")
        return p

    def note(self, rel: str, fm: str, body: str = ""):
        p = self.course / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")
        return p


class TestCoverage(StudyBase):
    def test_a_source_counts_only_when_a_note_embeds_it(self):
        self.source("Exams/SG1.pdf")
        self.source("Exams/SG2.pdf")
        self.note("Exams/guide.md", "type: exam-prep\ncourse: AA-210\nexam: exam-1",
                  "> Source: ![[SG1.pdf]]")
        cov = panels._scan_study()["coverage"][0]
        self.assertEqual((cov["covered"], cov["sources"]), (1, 2))
        self.assertEqual(cov["uncovered_sample"],
                         ["02-Areas/Academics/AA-210/Exams/SG2.pdf"])

    def test_a_similar_filename_does_not_count(self):
        """Coverage is an embed, not a guess. AA-210's EX1/SG1 convention is one
        course's habit and must not become the rule."""
        self.source("Exams/EX1PPSol.pdf")
        self.note("Exams/exam-1-study-guide.md",
                  "type: exam-prep\ncourse: AA-210\nexam: exam-1",
                  "All about exam 1 and EX1PPSol, but embedding nothing.")
        cov = panels._scan_study()["coverage"][0]
        self.assertEqual(cov["covered"], 0)

    def test_notes_do_not_count_as_sources(self):
        self.source("Exams/SG1.pdf")
        self.note("Exams/a.md", "type: exam-prep\ncourse: AA-210\nexam: exam-1")
        self.note("Exams/b.md", "type: lecture\ncourse: AA-210\nnumber: 1")
        self.assertEqual(panels._scan_study()["coverage"][0]["sources"], 1)

    def test_the_uncovered_list_is_capped_and_says_so(self):
        for i in range(20):
            self.source(f"Exams/f{i:02}.pdf")
        cov = panels._scan_study()["coverage"][0]
        self.assertEqual(len(cov["uncovered_sample"]), panels._COVERAGE_SAMPLE)
        self.assertEqual(cov["uncovered_more"], 20 - panels._COVERAGE_SAMPLE)


class TestExamUnits(StudyBase):
    def test_a_blank_date_stays_none_rather_than_becoming_today(self):
        """Every AA-210 study guide is in exactly this position: the source
        never stated a date, and zero days is not the same as no date."""
        self.note("Exams/g.md", "type: exam-prep\ncourse: AA-210\nexam: exam-1\ndate: \nstatus: studying")
        e = panels._scan_study()["exams"][0]
        self.assertIsNone(e["date"])
        self.assertIsNone(e["days"])

    def test_a_real_date_produces_a_day_count(self):
        import datetime
        soon = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        self.note("Exams/g.md",
                  f"type: exam-prep\ncourse: AA-210\nexam: exam-1\ndate: {soon}")
        self.assertEqual(panels._scan_study()["exams"][0]["days"], 5)

    def test_dated_exams_sort_before_undated_ones(self):
        import datetime
        soon = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        self.note("Exams/undated.md", "type: exam-prep\ncourse: AA-210\nexam: exam-9\ndate: ")
        self.note("Exams/dated.md",
                  f"type: exam-prep\ncourse: AA-210\nexam: exam-1\ndate: {soon}")
        exams = panels._scan_study()["exams"]
        self.assertEqual(exams[0]["exam"], "exam-1")
        self.assertIsNone(exams[-1]["days"])

    def test_only_exam_prep_notes_become_units(self):
        self.note("Exams/lec.md", "type: lecture\ncourse: AA-210\nnumber: 2")
        self.assertEqual(panels._scan_study()["exams"], [])


class TestPracticeHistory(StudyBase):
    """Exam mode's third panel (study S6): records, never guesses. A course
    appears only when the attempt log or the study log actually has rows."""

    def attempts(self, *rows):
        Path(ln.ATTEMPTS_PATH).write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_no_records_means_an_empty_list_not_zeros(self):
        self.assertEqual(panels._scan_study()["practice"], [])

    def test_misses_group_by_topic_worst_first(self):
        self.attempts(
            {"ts": "2026-08-08T10:00:00", "course": "AA-210", "qid": "q-2-7",
             "seg_title": "The dot product", "result": "wrong"},
            {"ts": "2026-08-09T10:00:00", "course": "AA-210", "qid": "q-2-8",
             "seg_title": "The dot product", "result": "wrong"},
            {"ts": "2026-08-09T11:00:00", "course": "AA-210", "qid": "q-2-1",
             "seg_title": "Components", "result": "wrong"},
            {"ts": "2026-08-09T12:00:00", "course": "AA-210", "qid": "q-2-2",
             "seg_title": "Components", "result": "correct"})
        hist = panels._scan_study()["practice"]
        self.assertEqual(len(hist), 1)
        h = hist[0]
        self.assertEqual((h["course"], h["attempts"], h["wrong"]), ("AA-210", 4, 3))
        self.assertEqual(h["by_topic"][0],
                         {"topic": "The dot product", "wrong": 2, "last": "2026-08-09"})
        self.assertEqual(h["by_topic"][1]["topic"], "Components")
        self.assertEqual(h["last"], "2026-08-09")

    def test_session_rows_come_from_the_study_log_newest_first(self):
        self.note("aa-210-study-log.md",
                  "type: study-log\ncourse: AA-210\ntags: [guide]",
                  "# log\n\n## Sessions\n"
                  "- 2026-08-08 · M02 · 4 answered, 3 correct · missed 1\n"
                  "- 2026-08-09 · CP1 · 5 answered, 5 correct · missed 0\n\n"
                  "## Not sessions\n- a bullet that must not leak in\n")
        h = panels._scan_study()["practice"][0]
        self.assertEqual(h["sessions"][0],
                         "2026-08-09 · CP1 · 5 answered, 5 correct · missed 0")
        self.assertEqual(len(h["sessions"]), 2)
        self.assertEqual(h["sessions_more"], 0)
        self.assertEqual(h["attempts"], 0)   # the two sources are independent

    def test_a_sealed_study_log_stays_unread(self):
        self.note("aa-210-study-log.md",
                  "type: study-log\ncourse: AA-210\ntags: [guide]",
                  "## Sessions\n- 2026-08-09 · M02 · secret row\n")
        (self.vault / ".gitignore").write_text(
            "02-Areas/Academics/AA-210/aa-210-study-log.md\n", encoding="utf-8")
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.assertEqual(panels._scan_study()["practice"], [])


if __name__ == "__main__":
    unittest.main()
