#!/usr/bin/env python3
"""
Chain recognition, the frontier, and the widened Courses window (study S2).

Recognition died silently once: commit a84695b renamed every timeline to
`<code>-timeline.md` while CHAIN_FILES still said `timeline.md`, and every
chain in the vault quietly went flat — nothing failed, the queues just
stopped sequencing. These tests pin the repaired rule (the folder vouches for
the basename) and the semantics the skip state depends on: the frontier
advances past `[x]` and `[-]` alike, an archived head no longer parks its
chain, and a course running both a timeline and a guide gets two window slots
without either head displacing the other.

The adherence half defends the retro: a guide chain of undated rows must add
zero deadlines to the daily score, and one dated checkpoint row exactly one —
a dated module row silently dragging the score is the failure the contract's
"module rows are undated" rule names.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import retro                                       # noqa: E402
import todo                                        # noqa: E402

TODAY = "2026-08-09"
OPEN = lambda v, rels: (set(), set())              # noqa: E731 — nothing sealed


# --------------------------------------------------------------------------
# recognition — the folder vouches for the basename
# --------------------------------------------------------------------------

class TestRecognition(unittest.TestCase):
    def test_a_course_timeline_and_guide_are_chains(self):
        self.assertTrue(todo.is_chain_file(
            "02-Areas/Academics/AA-210/aa-210-timeline.md"))
        self.assertTrue(todo.is_chain_file(
            "02-Areas/Academics/AA-210/aa-210-guide.md"))

    def test_the_pre_rename_name_no_longer_matches(self):
        # The exact-basename era: this is what a84695b renamed away from.
        self.assertFalse(todo.is_chain_file(
            "02-Areas/Academics/AA-210/timeline.md"))

    def test_study_guides_and_resources_are_never_sequenced(self):
        """The vault really holds these — a bare `-guide.md` suffix match
        would have put an exam-prep note's checkboxes behind a head."""
        for rel in ("02-Areas/Academics/AA-210/exams/exam-1-study-guide.md",
                    "02-Areas/Academics/AA-210/workbook-guide.md",
                    "02-Areas/Academics/MATH-208/exams/final-study-guide.md",
                    "04-Resources/git-guide.md"):
            self.assertFalse(todo.is_chain_file(rel), rel)

    def test_the_blueprint_is_not_a_chain(self):
        self.assertFalse(todo.is_chain_file(
            "02-Areas/Academics/AA-210/aa-210-guide-blueprint.md"))

    def test_a_rootless_path_is_not_a_chain(self):
        self.assertFalse(todo.is_chain_file("aa-210-guide.md"))

    def test_chain_kind_names_the_two_kinds_apart(self):
        self.assertEqual(todo.chain_kind(
            "02-Areas/Academics/AA-210/aa-210-timeline.md"), "timeline")
        self.assertEqual(todo.chain_kind(
            "02-Areas/Academics/AA-210/aa-210-guide.md"), "guide")
        self.assertIsNone(todo.chain_kind(
            "02-Areas/Academics/AA-210/tasks.md"))


# --------------------------------------------------------------------------
# the skip grammar — a status the line carries, never a removal
# --------------------------------------------------------------------------

class TestSkipLine(unittest.TestCase):
    def test_the_marker_lands_before_the_wikilink(self):
        got = todo.skip_line(
            "- [ ] M04 · [[aa-210-m04-moments|Moments and couples]]", TODAY)
        self.assertEqual(
            got, "- [-] M04 · skipped::2026-08-09 "
                 "[[aa-210-m04-moments|Moments and couples]]")

    def test_a_linkless_line_gets_the_marker_at_the_end(self):
        got = todo.skip_line("- [ ] drill the FBD habit", TODAY)
        self.assertEqual(got, "- [-] drill the FBD habit skipped::2026-08-09")

    def test_only_an_open_box_can_be_skipped(self):
        self.assertIsNone(todo.skip_line("- [x] M04 · done", TODAY))
        self.assertIsNone(todo.skip_line(
            "- [-] M04 · skipped::2026-08-01 [[m]]", TODAY))
        self.assertIsNone(todo.skip_line("not a task at all", TODAY))

    def test_the_skipped_row_is_invisible_to_the_scanner(self):
        """The whole mechanism: TASK_RE matches ` |x|X`, so the frontier
        advances and no open box remains."""
        got = todo.skip_line("- [ ] M04 · [[m|M]]", TODAY)
        self.assertIsNone(todo.TASK_RE.match(got))

    def test_indentation_and_bullet_style_survive(self):
        got = todo.skip_line("  * [ ] nested row", TODAY)
        self.assertEqual(got, "  * [-] nested row skipped::2026-08-09")


# --------------------------------------------------------------------------
# the frontier and the widened window, over a real scan
# --------------------------------------------------------------------------

TIMELINE = (
    "---\ntype: resource\ncourse: AA-210\nsource:\ntags: [resource]\n---\n\n"
    "## Block 1\n\n"
    "- [x] T1 read day two\n"
    "- [ ] T2 read day three\n"
    "- [ ] T3 read day four\n"
)

GUIDE = (
    "---\ntype: guide\ncourse: AA-210\ntags: [guide]\n---\n\n"
    "## Modules\n\n"
    "- [x] M01 · [[aa-210-m01-basics|Basics]]\n"
    "- [-] M02 · skipped::2026-08-08 [[aa-210-m02-vectors|Vectors]]\n"
    "- [ ] M03 · [[aa-210-m03-forces|Forces]]\n"
    "- [ ] M04 · [[aa-210-m04-moments|Moments]]\n"
)

LONE = (
    "---\ntype: resource\ncourse: MATH-208\nsource:\ntags: [resource]\n---\n\n"
    "- [ ] L1 the only head\n"
    "- [ ] L2 behind it\n"
)


class ChainBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        self.index = root / "todo.json"
        aa = self.vault / "02-Areas" / "Academics" / "AA-210"
        m2 = self.vault / "02-Areas" / "Academics" / "MATH-208"
        aa.mkdir(parents=True)
        m2.mkdir(parents=True)
        (aa / "aa-210-timeline.md").write_text(TIMELINE, encoding="utf-8")
        (aa / "aa-210-guide.md").write_text(GUIDE, encoding="utf-8")
        (m2 / "math-208-timeline.md").write_text(LONE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        return todo.build(vault=self.vault, index_path=self.index,
                          today=TODAY, split=OPEN)

    def courses(self):
        return self.build()["sections"]["courses"]


class TestFrontier(ChainBase):
    def test_both_heads_are_visible_and_neither_displaces_the_other(self):
        s = self.courses()
        texts = [t["text"] for t in s["visible"]]
        self.assertIn("T2 read day three", texts)      # timeline head
        self.assertIn("M03 · Forces", texts)           # guide head
        self.assertIn("L1 the only head", texts)       # the one-chain course
        self.assertEqual(len(s["visible"]), 3)

    def test_the_guide_frontier_advances_past_done_and_skipped_alike(self):
        """M01 is [x], M02 is [-]: the scanner sees neither, so M03 is the
        head — no open box remains behind the frontier."""
        s = self.courses()
        guide_rows = [t for t in s["visible"] if t["chain_kind"] == "guide"]
        self.assertEqual(len(guide_rows), 1)
        self.assertEqual(guide_rows[0]["text"], "M03 · Forces")
        blocked = [t["text"] for t in s["blocked"]]
        self.assertIn("M04 · Moments", blocked)

    def test_a_course_with_one_chain_still_shows_one_row(self):
        s = self.courses()
        math = [t for t in s["visible"] if t["parent"] == "MATH-208"]
        self.assertEqual(len(math), 1)

    def test_the_visible_rows_carry_their_chain_kind(self):
        s = self.courses()
        kinds = {t["text"]: t["chain_kind"] for t in s["visible"]}
        self.assertEqual(kinds["T2 read day three"], "timeline")
        self.assertEqual(kinds["M03 · Forces"], "guide")

    def test_an_archived_head_no_longer_parks_its_chain(self):
        """The /api/queue/meta deadlock the skip state replaces: archiving
        used to hide the head while its followers stayed blocked behind it."""
        first = self.courses()
        t2 = next(t for t in first["visible"] if t["text"] == "T2 read day three")
        self.assertTrue(todo.set_meta({t2["id"]: {"status": "archived"}},
                                      self.index))
        s = self.courses()
        texts = [t["text"] for t in s["visible"]]
        self.assertIn("T3 read day four", texts)       # promoted
        self.assertNotIn("T2 read day three", texts)
        self.assertIn("T2 read day three", [t["text"] for t in s["archived"]])


class TestAdherence(unittest.TestCase):
    """A guide chain must be invisible to the retro's deadline arithmetic."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        self.index = root / "todo.json"
        self.aa = self.vault / "02-Areas" / "Academics" / "AA-210"
        self.aa.mkdir(parents=True)
        (self.aa / "aa-210-guide.md").write_text(GUIDE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def facts(self):
        return retro.day_facts(TODAY, vault=self.vault,
                               index_path=self.index, split=OPEN)

    def test_undated_module_rows_add_zero_deadlines(self):
        self.assertEqual(self.facts()["deadlines_due"], 0)

    def test_one_dated_checkpoint_row_adds_exactly_one(self):
        (self.aa / "aa-210-guide.md").write_text(
            GUIDE + f"- [ ] CP1 · [[aa-210-checkpoint-1|Checkpoint 1]] "
                    f"📅 {TODAY}\n", encoding="utf-8")
        self.assertEqual(self.facts()["deadlines_due"], 1)


if __name__ == "__main__":
    unittest.main()
