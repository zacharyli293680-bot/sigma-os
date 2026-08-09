#!/usr/bin/env python3
"""
The study-guide module grammar (lesson.py, study mode S1).

The claim this suite pins: a module that violates any hard rule of the
contract's Study-guides grammar is *named* by validate(), and a conforming one
passes clean — because generated modules auto-apply, the parser is the only
thing standing between a malformed lesson and a commit that reads as shipped.
Every rule gets a failing fixture; a rule without one would fail quietly, which
is exactly how the format would drift.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import lesson  # noqa: E402

OPEN = lambda v, rels: (set(), set())  # noqa: E731 — tests see everything
SEAL_ALL = lambda v, rels: (set(rels), set())  # noqa: E731


def _segment(n: int, items: str = "", example: str = "") -> str:
    return (
        f"## S{n} · Topic {n} ⏱ 10\n"
        f"source:: 02-Areas/Academics/TEST-101/lectures/l{n}.md\n"
        f"\n### Summary\n\nOne-line summary {n}.\n"
        f"\n### Normal\n\nThe teaching text for topic {n}.\n"
        f"\n### In depth\n\nThe deeper cut for topic {n}.\n"
        f"{example}{items}"
    )


def _item(k: int, kind: str = "numeric") -> str:
    return (
        f"\n?? q-1-{k} · {kind}\n"
        f"What is {k} plus one?\n"
        f"- hint:: count up by one\n"
        f"- answer:: {k + 1}\n"
        f"- solution:: {k} + 1 = {k + 1}.\n"
    )


EXAMPLE = "\n### Example\n\nWorked: 2 + 1 = 3, carried through in full.\n"


def _valid() -> str:
    """A minimal module that satisfies every hard rule: 3 segments of 10
    minutes against estimate 30, 8 practice items, one Example."""
    fm = (
        "---\n"
        "type: module\n"
        "course: TEST-101\n"
        "module: 1\n"
        "unit: \n"
        "title: Test module\n"
        "estimate: 30\n"
        "sources:\n"
        "  - 02-Areas/Academics/TEST-101/lectures/l1.md\n"
        "verified: 2026-08-09\n"
        "tags: [guide]\n"
        "---\n\n"
        "Course: [[test-101]]\n\n# Test module\n\nA preamble paragraph.\n\n"
    )
    s1 = _segment(1, "".join(_item(k) for k in (1, 2, 3)), EXAMPLE)
    s2 = _segment(2, "".join(_item(k) for k in (4, 5, 6)))
    s3 = _segment(3, "".join(_item(k) for k in (7, 8)))
    return fm + s1 + "\n" + s2 + "\n" + s3


class LessonBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        lectures = self.vault / "02-Areas" / "Academics" / "TEST-101" / "lectures"
        lectures.mkdir(parents=True)
        for n in (1, 2, 3):
            (lectures / f"l{n}.md").write_text("# source\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def assert_problem(self, text: str, needle: str):
        problems = lesson.validate(text, vault=self.vault)
        self.assertTrue(any(needle in p for p in problems),
                        f"expected a problem containing {needle!r}, got: {problems}")


class TestParse(LessonBase):
    def test_a_conforming_module_parses_into_its_own_structure(self):
        d = lesson.parse(_valid())
        self.assertEqual(d["course"], "TEST-101")
        self.assertEqual(d["module"], 1)
        self.assertEqual(d["estimate"], 30)
        self.assertEqual(len(d["segments"]), 3)
        self.assertEqual(sum(len(s["practice"]) for s in d["segments"]), 8)
        self.assertEqual(d["segments"][0]["minutes"], 10)
        self.assertEqual(d["segments"][0]["sources"][0]["path"],
                         "02-Areas/Academics/TEST-101/lectures/l1.md")
        self.assertIn("preamble paragraph", d["preamble"])
        self.assertEqual(d["problems"], [])

    def test_every_structural_element_carries_its_line_number(self):
        d = lesson.parse(_valid())
        for seg in d["segments"]:
            self.assertGreater(seg["line"], 0)
            for src in seg["sources"]:
                self.assertEqual(src["line"], seg["line"] + 1)
            for it in seg["practice"]:
                self.assertGreater(it["line"], seg["line"])

    def test_a_practice_item_keeps_prompt_hints_answer_and_solution(self):
        d = lesson.parse(_valid())
        it = d["segments"][0]["practice"][0]
        self.assertEqual(it["id"], "q-1-1")
        self.assertEqual(it["kind"], "numeric")
        self.assertEqual(it["prompt"], "What is 1 plus one?")
        self.assertEqual(it["hints"], ["count up by one"])
        self.assertEqual(it["answer"], "2")
        self.assertIn("1 + 1 = 2", it["solution"])

    def test_a_fenced_code_block_cannot_open_a_segment(self):
        text = _valid() + "\n```\n## S9 · Fenced ⏱ 99\n```\n"
        d = lesson.parse(text)
        self.assertEqual(len(d["segments"]), 3)


class TestValidate(LessonBase):
    def test_the_conforming_module_is_clean(self):
        self.assertEqual(lesson.validate(_valid(), vault=self.vault), [])

    def test_a_missing_depth_heading_is_named(self):
        self.assert_problem(_valid().replace("\n### In depth\n\nThe deeper cut for topic 2.\n", "\n"),
                            "In depth")

    def test_a_missing_answer_holds_the_module(self):
        self.assert_problem(_valid().replace("- answer:: 2\n", "", 1), "answer::")

    def test_a_missing_solution_holds_the_module(self):
        self.assert_problem(_valid().replace("- solution:: 1 + 1 = 2.\n", "", 1),
                            "solution::")

    def test_a_budget_sum_mismatch_is_named(self):
        self.assert_problem(_valid().replace("estimate: 30", "estimate: 45"), "sum to")

    def test_an_estimate_outside_the_band_is_named(self):
        self.assert_problem(_valid().replace("estimate: 30", "estimate: 20")
                            .replace("⏱ 10", "⏱ 6", 1), "outside")

    def test_too_few_segments_is_named(self):
        text = _valid()
        cut = text.index("## S3")
        self.assert_problem(text[:cut].replace("estimate: 30", "estimate: 30"),
                            "segment(s)")

    def test_an_unknown_practice_kind_is_named(self):
        self.assert_problem(_valid().replace("q-1-8 · numeric", "q-1-8 · essay"),
                            "unknown kind")

    def test_a_duplicate_question_id_is_named(self):
        self.assert_problem(_valid().replace("q-1-8", "q-1-7"), "duplicate question id")

    def test_an_id_naming_the_wrong_module_is_named(self):
        self.assert_problem(_valid().replace("q-1-8", "q-9-8"), "names module")

    def test_a_segment_without_provenance_is_held(self):
        self.assert_problem(
            _valid().replace("source:: 02-Areas/Academics/TEST-101/lectures/l2.md\n", ""),
            "no source::")

    def test_a_module_with_no_example_is_named(self):
        self.assert_problem(_valid().replace(EXAMPLE, ""), "no segment has")

    def test_practice_count_outside_the_band_is_named(self):
        self.assert_problem(_valid().replace(_item(8), ""), "practice item(s)")

    def test_an_unknown_h2_is_named(self):
        self.assert_problem(_valid() + "\n## Related\n- stray\n", "unexpected H2")

    def test_an_unresolvable_segment_source_is_named(self):
        self.assert_problem(_valid().replace("lectures/l3.md", "lectures/ghost.md"),
                            "does not resolve")

    def test_a_wrong_type_is_named(self):
        self.assert_problem(_valid().replace("type: module", "type: resource"),
                            "type is")

    def test_content_after_a_practice_items_keys_is_named(self):
        self.assert_problem(
            _valid().replace("- solution:: 1 + 1 = 2.\n",
                             "- solution:: 1 + 1 = 2.\nstray trailing prose\n"),
            "after a practice item")


class TestSerialize(LessonBase):
    def test_canonical_text_is_a_byte_fixed_point(self):
        once = lesson.serialize(lesson.parse(_valid()))
        twice = lesson.serialize(lesson.parse(once))
        self.assertEqual(once, twice)

    def test_reserialising_preserves_the_structure(self):
        d1 = lesson.parse(_valid())
        d2 = lesson.parse(lesson.serialize(d1))
        self.assertEqual(d2["problems"], [])

        def shape(d):
            return [(s["n"], s["title"], s["minutes"],
                     [src["path"] for src in s["sources"]],
                     s["summary"], s["normal"], s["in_depth"], s["example"],
                     [(i["id"], i["kind"], i["prompt"], i["hints"],
                       i["answer"], i["solution"]) for i in s["practice"]])
                    for s in d["segments"]]
        self.assertEqual(shape(d1), shape(d2))
        self.assertEqual(lesson.validate(lesson.serialize(d1), vault=self.vault), [])


class TestScanAndLoad(LessonBase):
    def _write_module(self, name="test-101-m01-test-module.md", text=None):
        guide = self.vault / "02-Areas" / "Academics" / "TEST-101" / "guide"
        guide.mkdir(parents=True, exist_ok=True)
        (guide / name).write_text(text or _valid(), encoding="utf-8")

    def test_scan_finds_a_module_and_reports_it_clean(self):
        self._write_module()
        rows = lesson.scan(self.vault, split=OPEN)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["course"], "TEST-101")
        self.assertEqual(rows[0]["module"], 1)
        self.assertEqual(rows[0]["segments"], 3)
        self.assertEqual(rows[0]["practice"], 8)
        self.assertEqual(rows[0]["problems"], [])

    def test_scan_skips_notes_that_are_not_modules(self):
        self._write_module()
        self._write_module("notes.md", "---\ntype: resource\n---\nnot a module\n")
        self.assertEqual(len(lesson.scan(self.vault, split=OPEN)), 1)

    def test_a_sealed_module_never_appears(self):
        self._write_module()
        self.assertEqual(lesson.scan(self.vault, split=SEAL_ALL), [])

    def test_load_answers_case_insensitively_and_none_for_absent(self):
        self._write_module()
        d = lesson.load(self.vault, "test-101", 1, split=OPEN)
        self.assertIsNotNone(d)
        self.assertEqual(d["title"], "Test module")
        self.assertTrue(d["file"].endswith("test-101-m01-test-module.md"))
        self.assertIsNone(lesson.load(self.vault, "TEST-101", 2, split=OPEN))


if __name__ == "__main__":
    unittest.main()
