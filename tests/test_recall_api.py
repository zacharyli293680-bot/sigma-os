#!/usr/bin/env python3
"""
S8 through the API: the rollup that raises cards and records the clock.

`test_practice_writes.py` pins the rollup's own discipline; this pins what S8
added to it, and every case here is one where being wrong looks like being
right. A second row written because the measured minutes differed. A card
raised into a note nobody links. A clock the client can set to eight hours. A
card raised for a course whose folder is sealed. The cards and the digest
landing as two commits, so undoing the session leaves half of it behind.
"""
import datetime
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import recall as rc                                     # noqa: E402
import todo as td                                       # noqa: E402
import writes                                           # noqa: E402
from sigma import ledger                                # noqa: E402
from test_practice_writes import PracticeWritesBase, _run   # noqa: E402

REL = "02-Areas/Academics/TEST-101/test-101-recall.md"


class RecallApiBase(PracticeWritesBase):
    def miss(self, qid="q-1-1", **over):
        return self.attempt(qid=qid, result="wrong", **over)

    def end(self, **over):
        body = {"course": "TEST-101"}
        body.update(over)
        return self.client.post("/api/lesson/session-end", json=body)

    def cards(self):
        p = self.vault / REL
        return (rc.parse_cards(p.read_text(encoding="utf-8"))
                if p.is_file() else [])


class TestCardsFromASession(RecallApiBase):
    def test_a_missed_segment_becomes_a_card_in_the_rollup_commit(self):
        self.miss("q-1-1")                     # segment 1 — "Topic 1"
        self.miss("q-1-4")                     # segment 2 — "Topic 2"
        self.attempt(qid="q-1-2", result="correct")
        r = self.end(seconds=22 * 60)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["recall"]["raised"], 2)
        self.assertEqual(body["recall"]["file"], REL)

        got = self.cards()
        self.assertEqual(sorted(c["seg"] for c in got), ["Topic 1", "Topic 2"])
        self.assertTrue(all(c["target"] == "test-101-m01-test-module"
                            and c["label"] == "M01" for c in got))

        # One commit, both files — a session is one record and one undo.
        touched = _run(self.vault, "show", "--name-only", "--format=", "HEAD")
        self.assertIn(REL, touched.stdout)
        self.assertIn("test-101-study-log.md", touched.stdout)

    def test_a_question_got_on_the_second_go_still_raises_its_card(self):
        """The retry flow's load-bearing consequence. Before it, every wrong
        answer was final and every one raised a card; if a recovered answer
        raised none, then trying again — the thing the flow exists to
        encourage — would quietly cost you the card telling you to come back
        to it. A question you got on the third go is a question you missed."""
        self.attempt(qid="q-1-1", result="correct", tries=3)   # segment 1
        self.attempt(qid="q-1-2", result="correct")            # clean, same segment
        body = self.end().json()
        self.assertEqual(body["recall"]["raised"], 1)
        self.assertEqual([c["seg"] for c in self.cards()], ["Topic 1"])

    def test_a_session_with_nothing_missed_or_recovered_raises_nothing(self):
        self.attempt(qid="q-1-1", result="correct")
        body = self.end().json()
        self.assertEqual(body["recall"], None)
        self.assertEqual(self.cards(), [])

    def test_answering_wrong_and_then_skipping_is_not_a_way_out_of_the_card(self):
        """The hole the retry flow would otherwise open. Skipping is "never
        counted as missed" — that promise is about a question you did not
        attempt. Attempting one, getting it wrong and walking away is a miss,
        and before the item stayed open after a wrong answer it was not even
        reachable."""
        self.attempt(qid="q-1-1", result="skipped", tries=2)
        body = self.end().json()
        self.assertEqual(body["recall"]["raised"], 1)
        self.assertEqual([c["seg"] for c in self.cards()], ["Topic 1"])
        # …and the counts still say what they always said about a skip.
        self.assertIn("0 answered, 0 correct · skipped 1 · missed 0", body["raw"])

    def test_a_plain_skip_raises_nothing_and_needs_no_tries_field(self):
        self.attempt(qid="q-1-1", result="skipped")
        body = self.end().json()
        self.assertEqual(body["recall"], None)
        self.assertEqual(self.cards(), [])

    def test_the_row_records_the_measured_minutes(self):
        self.miss()
        r = self.end(seconds=22 * 60 + 10).json()
        self.assertEqual(r["minutes"], 22)
        self.assertIn("⏱ 22 min", r["raw"])
        log = (self.vault / "02-Areas/Academics/TEST-101/test-101-study-log.md"
               ).read_text(encoding="utf-8")
        self.assertIn("⏱ 22 min", log)

    def test_a_session_with_no_clock_writes_the_row_unchanged(self):
        """Rows without a time contribute nothing to the pace, deliberately —
        a back-filled minute is a guess wearing a measurement's clothes."""
        self.miss()
        r = self.end().json()
        self.assertIsNone(r["minutes"])
        self.assertNotIn("⏱", r["raw"])

    def test_a_clock_the_client_got_wrong_is_clamped_not_trusted(self):
        self.miss()
        r = self.end(seconds=48 * 3600).json()
        self.assertEqual(r["minutes"], writes.MAX_SESSION_MINUTES)

    def test_a_few_seconds_is_not_a_session(self):
        self.miss()
        self.assertIsNone(self.end(seconds=5).json()["minutes"])

    def test_a_second_call_writes_neither_a_row_nor_a_card(self):
        """The close handler and the button both fire. The measured minutes
        differ between the two calls, so the row-signature check has to ignore
        the ⏱ field or the digest lands twice."""
        self.miss()
        first = self.end(seconds=20 * 60).json()
        self.assertTrue(first["wrote"])
        second = self.end(seconds=31 * 60).json()
        self.assertFalse(second["wrote"])
        log = (self.vault / "02-Areas/Academics/TEST-101/test-101-study-log.md"
               ).read_text(encoding="utf-8")
        self.assertEqual(log.count("missed 1"), 1)
        self.assertEqual(len(self.cards()), 1)

    def test_a_rollup_past_the_watermark_still_writes_no_second_card(self):
        """The watermark repaired rather than the row rewritten — the path
        where `wrote` is False but the reconcile still runs."""
        self.miss()
        self.end(seconds=20 * 60)
        import lesson as ln
        st = ln.load_state()
        st["rollup"] = {}                       # the sidecar lost its watermark
        ln.save_state(st)
        r = self.end(seconds=20 * 60).json()
        self.assertFalse(r["wrote"])
        self.assertEqual(len(self.cards()), 1)

    def test_a_clean_session_mints_no_empty_note(self):
        self.attempt(qid="q-1-1", result="correct")
        r = self.end(seconds=10 * 60).json()
        self.assertIsNone(r["recall"])
        self.assertFalse((self.vault / REL).exists())

    def test_a_checkpoint_miss_cards_the_checkpoint(self):
        self.attempt(module=None, checkpoint=1, qid="q-cp1-1", result="wrong")
        self.end(seconds=15 * 60)
        (card,) = self.cards()
        self.assertEqual(card["label"], "CP1")
        self.assertEqual(card["target"], "test-101-checkpoint-1")

    def test_a_fresh_card_sleeps_one_night(self):
        """Spacing, in the queue's own snooze field — a 📅 would become a
        deadline in the adherence scan."""
        self.miss()
        self.end(seconds=20 * 60)
        (card,) = self.cards()
        tid = rc.card_task_id(REL, card["raw"])
        index, ok = td.load_index(td.INDEX_PATH)
        self.assertTrue(ok)
        want = (datetime.date.today()
                + datetime.timedelta(days=rc.FIRST_REVIEW_DAYS)).isoformat()
        self.assertEqual(index["tasks"][tid]["snoozed_until"], want)
        # ...and the queue honours it: the card is deferred, not visible.
        q = td.build(vault=self.vault, index_path=td.INDEX_PATH,
                     split=lambda v, r: (set(), set()))
        sec = q["sections"]["courses"]
        self.assertEqual([t["text"] for t in sec["snoozed"] if t["recall"]],
                         [td.display_text(td.TASK_RE.match(card["raw"]).group(2))])
        self.assertEqual([t for t in sec["visible"] if t["recall"]], [])

    def test_one_card_per_segment_however_many_questions_were_missed(self):
        """The digest groups by segment because that is the unit a re-study
        decision is made at; the cards have to group the same way or the note
        and the card would disagree about what was missed."""
        self.miss("q-1-1")                      # both in segment 1
        self.miss("q-1-2")
        self.miss("q-1-4")                      # segment 2
        self.end(seconds=20 * 60)
        got = self.cards()
        self.assertEqual([(c["seg"], c["misses"]) for c in got],
                         [("Topic 1", 2), ("Topic 2", 1)])


class TestTheCourseIndexLearnsAboutIt(RecallApiBase):
    def setUp(self):
        super().setUp()
        self.index = self.course / "test-101.md"
        self.index.write_text(
            "---\ntype: course-index\ncourse: TEST-101\nstatus: active\n---\n\n"
            "# TEST-101\n\n## Notes\n\n- [[test-101-guide|the chain]]\n",
            encoding="utf-8")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "index")

    def test_a_new_recall_note_is_linked_from_the_course_index(self):
        """An unreachable note is the AA-210 intake failure with a different
        author."""
        self.miss()
        r = self.end(seconds=20 * 60).json()
        self.assertTrue(r["recall"]["raised"])
        self.assertIn("[[test-101-recall|", self.index.read_text(encoding="utf-8"))
        touched = _run(self.vault, "show", "--name-only", "--format=", "HEAD")
        self.assertIn("test-101.md", touched.stdout)

    def test_the_index_is_not_rewritten_on_every_later_session(self):
        self.miss("q-1-1")
        self.end(seconds=20 * 60)
        before = self.index.read_text(encoding="utf-8")
        self.miss("q-1-4")
        self.end(seconds=20 * 60)
        self.assertEqual(self.index.read_text(encoding="utf-8"), before)


class TestTheCoursesPayload(RecallApiBase):
    def test_it_reports_the_pace_as_unmeasured_rather_than_one(self):
        row = self.client.get("/api/courses").json()["courses"][0]
        self.assertIsNone(row["pace"]["multiplier"])
        self.assertIsNone(row["pace"]["basis"])
        self.assertIsNone(row["projected"]["minutes"])
        self.assertEqual(row["recall"], {"open": 0, "cap": rc.CAP, "file": None})

    def test_open_cards_show_up_on_the_course(self):
        self.miss()
        self.end(seconds=20 * 60)
        import panels
        panels._cache.clear()
        row = self.client.get("/api/courses").json()["courses"][0]
        self.assertEqual(row["recall"]["open"], 1)
        self.assertEqual(row["recall"]["file"], REL)


class TestLedger(RecallApiBase):
    def test_the_session_row_names_the_minutes(self):
        self.miss()
        self.end(seconds=22 * 60)
        entry = ledger.entries()[0]
        self.assertIn("22 min", entry["summary"])
        self.assertEqual(entry["actor"], "zach")


if __name__ == "__main__":
    unittest.main()
