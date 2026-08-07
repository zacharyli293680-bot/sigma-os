"""
The habit on the dashboard: the agenda's `practice` kind and POST /api/practice/log.

Two claims are worth pinning here, and both would fail quietly:

- **the expansion respects `started:`** — a day before the habit existed must
  emit nothing, not an unmet obligation. Getting this wrong back-dates a broken
  streak across the whole calendar the first time anyone pages backwards;
- **a duplicate is a 409 carrying the entry it collided with**, not a 400 and
  not a silent second line. That payload is what turns "already solved" into an
  offer, which is the entire reason for keeping a record of what you have done.

The endpoint is exercised over HTTP through the real router rather than by
calling the function, because the parts that break are the status code and the
shape of the body — neither of which a direct call would check.
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
import agenda as ag                                  # noqa: E402
import leetcode as lc                                # noqa: E402

DAY = "2026-08-06"
OPEN = lambda v, rels: (set(), set())                # noqa: E731 — nothing sealed

NOTE = ("---\ntype: practice-log\npractice: leetcode\nstarted: 2026-08-05\n"
        "tags: [practice]\n---\n\n# LeetCode\n\n## Solved\n"
        "- [x] 1 Two Sum · easy ✅ 2026-08-05\n\n## Related\n\n- x\n")


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


class VaultBase(unittest.TestCase):
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
        ag.invalidate()

    def tearDown(self):
        ag.invalidate()
        self.tmp.cleanup()


# --------------------------------------------------------------------------
# the agenda kind
# --------------------------------------------------------------------------

class TestPracticeOccurrences(VaultBase):
    def occ(self, frm, to):
        ag.invalidate()
        return [o for o in ag.resolve(self.vault, frm, to, split=OPEN)["occurrences"]
                if o["kind"] == "practice"]

    def test_one_all_day_occurrence_per_day_with_its_done_state(self):
        got = self.occ("2026-08-05", "2026-08-07")
        self.assertEqual([(o["date"], o["done"]) for o in got],
                         [("2026-08-05", True), ("2026-08-06", False),
                          ("2026-08-07", False)])
        self.assertTrue(all(o["all_day"] and o["start"] is None for o in got))

    def test_nothing_before_started(self):
        """A day the habit did not exist is not a day it was missed — the same
        rule the review's P component follows by returning None."""
        self.assertEqual(self.occ("2026-08-01", "2026-08-04"), [])
        self.assertEqual([o["date"] for o in self.occ("2026-08-03", "2026-08-05")],
                         ["2026-08-05"])

    def test_it_carries_provenance_like_every_other_occurrence(self):
        o = self.occ(DAY, DAY)[0]
        self.assertEqual(o["source"]["path"], lc.LOG_REL)
        self.assertEqual(o["source"]["field"], "practice")
        self.assertTrue(o["id"].startswith("sg-practice-leetcode@"))

    def test_a_habit_with_no_start_and_no_reps_is_never_due(self):
        """Otherwise creating the note back-dates an obligation to the epoch."""
        self.note.write_text(
            "---\ntype: practice-log\npractice: x\n---\n\n# X\n\n## Solved\n",
            encoding="utf-8")
        self.assertEqual(self.occ("2026-08-01", "2026-08-09"), [])

    def test_reps_alone_start_the_habit_when_frontmatter_does_not(self):
        self.note.write_text(
            "---\ntype: practice-log\npractice: x\n---\n\n# X\n\n## Solved\n"
            "- [x] 1 Two Sum · easy ✅ 2026-08-06\n", encoding="utf-8")
        self.assertEqual([o["date"] for o in self.occ("2026-08-01", "2026-08-07")],
                         ["2026-08-06", "2026-08-07"])

    def test_done_is_none_on_every_other_kind(self):
        """A bare False here would claim every event on the calendar is unmet."""
        (self.vault / "02-Areas" / "Personal" / "Calendar").mkdir(parents=True)
        (self.vault / "02-Areas/Personal/Calendar/2026-08.md").write_text(
            "---\ntype: calendar-month\nmonth: 2026-08\n---\n\n## Events\n"
            "- 2026-08-06 14:30–15:30 Dentist ^sg-evt-8f2a1c04\n", encoding="utf-8")
        ag.invalidate()
        got = ag.resolve(self.vault, DAY, DAY, split=OPEN)["occurrences"]
        others = [o for o in got if o["kind"] != "practice"]
        self.assertTrue(others)
        self.assertTrue(all(o["done"] is None for o in others))


# --------------------------------------------------------------------------
# POST /api/practice/log
# --------------------------------------------------------------------------

class TestPracticeEndpoint(VaultBase):
    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import panels
        import writes
        for mod in (writes, panels, lc, ag):
            if hasattr(mod, "VAULT"):
                self.addCleanup(setattr, mod, "VAULT", mod.VAULT)
                mod.VAULT = self.vault
        # No network in a test run: the lookup degrades to a bare number, which
        # is the documented offline behaviour rather than a special case.
        self._get, lc._get = lc._get, lambda *a, **k: None
        self.addCleanup(setattr, lc, "_get", self._get)
        panels._cache.clear()
        app = FastAPI()
        app.include_router(writes.router)
        self.c = TestClient(app)

    def post(self, **body):
        return self.c.post("/api/practice/log", json=body)

    def test_a_number_logs_the_day(self):
        r = self.post(n="217")
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual(b["n"], 217)
        self.assertTrue(b["practice"]["done"])
        self.assertIn("- [x] 217", self.note.read_text(encoding="utf-8"))

    def test_the_ledger_says_zach_did_it(self):
        """A human typing a number is not an autonomous write, and the undo in
        the activity feed should say whose change it is undoing."""
        import sigma.ledger as led
        self.post(n="217")
        rows = [json.loads(x) for x in
                Path(led.LEDGER_PATH).read_text(encoding="utf-8").splitlines() if x]
        self.assertEqual(rows[-1]["actor"], "zach")

    def test_a_duplicate_is_409_and_carries_the_entry_it_hit(self):
        r = self.post(n="1")             # already solved on 2026-08-05
        self.assertEqual(r.status_code, 409)
        b = r.json()
        self.assertEqual(b["error"], "already solved")
        self.assertEqual(b["duplicate"]["date"], "2026-08-05")
        self.assertEqual(self.note.read_text(encoding="utf-8").count("- [x] 1 "), 1)

    def test_again_gets_past_it_as_a_revisit(self):
        self.assertEqual(self.post(n="1").status_code, 409)
        r = self.post(n="1", again=True)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["revisit"], 2)
        self.assertIn("(revisit 2)", self.note.read_text(encoding="utf-8"))

    def test_nonsense_is_refused_before_anything_is_written(self):
        before = self.note.read_text(encoding="utf-8")
        for bad in ("", "abc", "12/34", "1234567", "-1", "../x"):
            r = self.post(n=bad)
            self.assertEqual(r.status_code, 400, f"{bad!r} -> {r.status_code}")
        self.assertEqual(self.note.read_text(encoding="utf-8"), before)

    def test_a_missing_log_note_is_404_rather_than_a_created_one(self):
        self.note.unlink()
        self.assertEqual(self.post(n="217").status_code, 404)

    def test_the_queue_payload_carries_the_habit_and_drops_its_cache(self):
        import panels
        self.assertFalse(panels._queue_payload()["practice"]["done"])
        self.post(n="217")
        # drop_task_caches ran, so this is a fresh read rather than the 15s copy
        self.assertTrue(panels._queue_payload()["practice"]["done"])

    def test_practice_is_a_sibling_of_sections_not_a_fifth_one(self):
        """Every consumer of `sections` iterates it; a member with no visible/
        queue/blocked lists would break each of them."""
        import panels
        import todo
        q = panels._queue_payload()
        self.assertEqual(set(q["sections"]), set(todo.SECTIONS))
        self.assertNotIn("practice", q["sections"])


if __name__ == "__main__":
    unittest.main()
