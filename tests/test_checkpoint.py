#!/usr/bin/env python3
"""
The checkpoint grammar and its API (study S6).

A checkpoint is the module grammar minus the teaching — same parser, opposite
contract about depth levels — so the tests pin both directions: everything a
module must have that a checkpoint must NOT (Summary/Normal/In depth, Example),
and everything both must have (grounding source:: lines, answer:: and
solution:: on every item, ids in their own `q-cp<n>-<m>` shape that can never
collide with a module's). The API half proves a chain row can open one exactly
like a module: list, load, state attach, 404s, sealing.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI                  # noqa: E402
from fastapi.testclient import TestClient    # noqa: E402
import lesson                                # noqa: E402
import panels                                # noqa: E402
import privacy                               # noqa: E402


def _git(cwd: Path, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _cp_item(n: int, kind: str = "numeric") -> str:
    return (f"?? q-cp1-{n} · {kind}\n"
            f"What is {n} plus one?\n"
            f"- hint:: count up\n"
            f"- answer:: {n + 1}\n"
            f"- solution:: {n} + 1 = {n + 1}.\n\n")


def _valid_cp() -> str:
    """A minimal conforming checkpoint: two sourced segments, five items,
    no depth levels anywhere."""
    fm = ("---\n"
          "type: checkpoint\n"
          "course: TEST-101\n"
          "checkpoint: 1\n"
          "covers: [1, 2]\n"
          "date:\n"
          "tags: [guide]\n"
          "---\n\n"
          "Course: [[test-101]]\n\n# Checkpoint 1 — test\n\n")
    s1 = ("## S1 · Topic A ⏱ 10\n"
          "source:: 02-Areas/Academics/TEST-101/lectures/l1.md\n\n"
          + "".join(_cp_item(n) for n in (1, 2, 3)))
    s2 = ("## S2 · Topic B ⏱ 8\n"
          "source:: 02-Areas/Academics/TEST-101/lectures/l2.md\n\n"
          + "".join(_cp_item(n) for n in (4, 5)))
    return fm + s1 + "\n" + s2


class CheckpointBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.course / "lectures").mkdir()
        for n in (1, 2, 3):
            (self.course / "lectures" / f"l{n}.md").write_text(
                "# src\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def assert_problem(self, text: str, needle: str):
        problems = lesson.validate_checkpoint(text, vault=self.vault)
        self.assertTrue(any(needle in p for p in problems),
                        f"expected a problem containing {needle!r}, got: {problems}")


class TestCheckpointGrammar(CheckpointBase):
    def test_a_conforming_checkpoint_validates_clean(self):
        self.assertEqual(lesson.validate_checkpoint(_valid_cp(), vault=self.vault), [])

    def test_a_depth_level_holds_it(self):
        """The inversion that makes this its own validator: what a module
        must have, a checkpoint must not."""
        text = _valid_cp().replace(
            "## S1 · Topic A ⏱ 10\n"
            "source:: 02-Areas/Academics/TEST-101/lectures/l1.md\n",
            "## S1 · Topic A ⏱ 10\n"
            "source:: 02-Areas/Academics/TEST-101/lectures/l1.md\n\n"
            "### Normal\n\nSome teaching prose.\n")
        self.assert_problem(text, "practice only, never depth levels")

    def test_an_example_holds_it(self):
        text = _valid_cp().replace(
            "?? q-cp1-4", "### Example\n\nWorked thing.\n\n?? q-cp1-4")
        self.assert_problem(text, "practice only")

    def test_teaching_prose_inside_a_segment_is_named(self):
        text = _valid_cp().replace(
            "?? q-cp1-1", "A paragraph of teaching.\n\n?? q-cp1-1")
        self.assert_problem(text, "content before the first")

    def test_a_module_shaped_id_is_refused(self):
        text = _valid_cp().replace("q-cp1-1", "q-1-1")
        self.assert_problem(text, "does not match q-cp<checkpoint>-<n>")

    def test_an_id_naming_another_checkpoint_is_refused(self):
        text = _valid_cp().replace("q-cp1-1", "q-cp2-1")
        self.assert_problem(text, "names checkpoint 2")

    def test_a_duplicate_id_is_refused(self):
        text = _valid_cp().replace("q-cp1-2", "q-cp1-1")
        self.assert_problem(text, "duplicate question id")

    def test_a_missing_answer_or_solution_holds_it(self):
        self.assert_problem(_valid_cp().replace("- answer:: 2\n", "", 1),
                            "answer:: is missing")
        self.assert_problem(_valid_cp().replace("- solution:: 2 + 1 = 3.\n", "", 1),
                            "solution:: is missing")

    def test_an_unresolvable_source_holds_it(self):
        text = _valid_cp().replace("lectures/l2.md", "lectures/ghost.md")
        self.assert_problem(text, "does not resolve")

    def test_a_missing_source_holds_it(self):
        text = _valid_cp().replace(
            "source:: 02-Areas/Academics/TEST-101/lectures/l2.md\n", "")
        self.assert_problem(text, "no source:: line")

    def test_empty_covers_holds_it(self):
        self.assert_problem(_valid_cp().replace("covers: [1, 2]", "covers:"),
                            "covers is empty")

    def test_the_wrong_type_holds_it(self):
        self.assert_problem(_valid_cp().replace("type: checkpoint", "type: module"),
                            "expected 'checkpoint'")

    def test_no_items_hold_it(self):
        fm_and_seg = _valid_cp().split("?? q-cp1-1")[0]
        self.assert_problem(fm_and_seg, "assesses nothing")

    def test_no_segments_hold_it(self):
        fm = _valid_cp().split("## S1")[0]
        self.assert_problem(fm, "no segments")

    def test_a_malformed_date_is_named(self):
        self.assert_problem(_valid_cp().replace("date:", "date: soon"),
                            "not YYYY-MM-DD")

    def test_unknown_kind_is_refused(self):
        self.assert_problem(_valid_cp().replace("· numeric", "· essay", 1),
                            "unknown kind")


class TestScanAndLoad(CheckpointBase):
    def _open(self, split=None):
        return lesson.scan_checkpoints(self.vault, split=split or (lambda v, r: (set(), set())))

    def test_scan_finds_it_with_covers_and_counts(self):
        (self.course / "guide" / "test-101-checkpoint-1.md").write_text(
            _valid_cp(), encoding="utf-8")
        rows = self._open()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["course"], r["checkpoint"], r["covers"]),
                         ("TEST-101", 1, [1, 2]))
        self.assertEqual(r["practice"], 5)
        self.assertEqual(r["title"], "Checkpoint 1 — test")
        self.assertEqual(r["problems"], [])

    def test_a_module_note_is_not_a_checkpoint(self):
        from test_lesson import _valid
        (self.course / "guide" / "test-101-m01-test-module.md").write_text(
            _valid(), encoding="utf-8")
        self.assertEqual(self._open(), [])

    def test_a_drifted_course_field_is_flagged(self):
        (self.course / "guide" / "test-101-checkpoint-1.md").write_text(
            _valid_cp().replace("course: TEST-101", "course: OTHER-999"),
            encoding="utf-8")
        rows = self._open()
        self.assertTrue(any("course field" in p for p in rows[0]["problems"]))

    def test_two_files_claiming_one_number_flag_both(self):
        (self.course / "guide" / "test-101-checkpoint-1.md").write_text(
            _valid_cp(), encoding="utf-8")
        (self.course / "guide" / "test-101-checkpoint-1b.md").write_text(
            _valid_cp(), encoding="utf-8")
        rows = self._open()
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertTrue(any("duplicate checkpoint" in p for p in r["problems"]), r)

    def test_load_returns_the_full_parse(self):
        (self.course / "guide" / "test-101-checkpoint-1.md").write_text(
            _valid_cp(), encoding="utf-8")
        d = lesson.load_checkpoint(self.vault, "test-101", 1,
                                   split=lambda v, r: (set(), set()))
        self.assertEqual(d["checkpoint"], 1)
        self.assertEqual(d["covers"], [1, 2])
        self.assertEqual([s["title"] for s in d["segments"]], ["Topic A", "Topic B"])
        self.assertIsNone(lesson.load_checkpoint(self.vault, "test-101", 9,
                                                 split=lambda v, r: (set(), set())))


class CheckpointApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.course / "lectures").mkdir()
        for n in (1, 2, 3):
            (self.course / "lectures" / f"l{n}.md").write_text(
                "# src\n", encoding="utf-8")
        (self.course / "guide" / "test-101-checkpoint-1.md").write_text(
            _valid_cp(), encoding="utf-8")
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")

        self._saved = (panels.VAULT, lesson.STATE_PATH)
        panels.VAULT = self.vault
        lesson.STATE_PATH = root / "study.state.json"
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()

        app = FastAPI()
        app.include_router(panels.router)
        self.client = TestClient(app)

    def tearDown(self):
        panels.VAULT, lesson.STATE_PATH = self._saved
        panels._cache.clear()
        self.tmp.cleanup()


class TestCheckpointApi(CheckpointApiBase):
    def test_the_checkpoint_arrives_parsed_with_identity(self):
        r = self.client.get("/api/checkpoint/test-101/1")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d["course"], "TEST-101")
        self.assertEqual(d["checkpoint"], 1)
        self.assertEqual(d["covers"], [1, 2])
        self.assertEqual(d["problems"], [])
        self.assertEqual(len(d["segments"]), 2)
        self.assertIsNone(d["state"])

    def test_the_sidecar_state_rides_along_under_its_cp_key(self):
        st = lesson.load_state()
        st["modules"]["TEST-101/cp1"] = {"practice": {"q-cp1-1": {"hints": 1}}}
        self.assertTrue(lesson.save_state(st))
        d = self.client.get("/api/checkpoint/test-101/1").json()
        self.assertEqual(d["state"], {"practice": {"q-cp1-1": {"hints": 1}}})

    def test_missing_is_a_404(self):
        self.assertEqual(self.client.get("/api/checkpoint/test-101/9").status_code, 404)
        self.assertEqual(self.client.get("/api/checkpoint/no-999/1").status_code, 404)

    def test_the_lesson_list_carries_checkpoints(self):
        body = self.client.get("/api/lesson").json()
        self.assertIn("checkpoints", body)
        self.assertEqual(body["checkpoints"][0]["checkpoint"], 1)
        self.assertNotIn("segments", body["checkpoints"][0].get("rows", {}))

    def test_a_sealed_checkpoint_is_a_404(self):
        (self.vault / ".gitignore").write_text(
            "02-Areas/Academics/TEST-101/guide/test-101-checkpoint-1.md\n",
            encoding="utf-8")
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.assertEqual(self.client.get("/api/checkpoint/test-101/1").status_code, 404)

    def test_a_held_checkpoint_arrives_with_its_problems_named(self):
        (self.course / "guide" / "test-101-checkpoint-1.md").write_text(
            _valid_cp().replace("- answer:: 2\n", "", 1), encoding="utf-8")
        d = self.client.get("/api/checkpoint/test-101/1").json()
        self.assertTrue(any("answer:: is missing" in p for p in d["problems"]))


if __name__ == "__main__":
    unittest.main()
