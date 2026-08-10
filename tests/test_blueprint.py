"""
The blueprint grammar (study S7, §5.3) — lesson.py's third grammar.

Approval is only load-bearing if what was approved is well-formed and what was
parsed is what was written: the round trip, the §4 grouping arithmetic, and
the guide() payload's planned/missing counts are what the module pass and the
applier hold both stand on.
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

import lesson  # noqa: E402


def _bp(rows: str, status: str = "draft", course: str = "TEST-101") -> str:
    return ("---\n"
            "type: guide-blueprint\n"
            f"course: {course}\n"
            f"status: {status}\n"
            "tags: [guide]\n"
            "---\n\n"
            "# blueprint\n\nProse the parser must ignore.\n\n"
            "## Modules\n\n" + rows)


def _mod_row(n: int, title: str = None, est: int = 40,
             src: str = "02-Areas/Academics/TEST-101/lectures/l1.md") -> str:
    return (f"- M{n:02} · {title or f'Topic {n}'} ⏱ {est}\n"
            f"    - source:: {src}\n")


class TestGrammar(unittest.TestCase):
    def test_rows_parse_in_document_order_with_sources(self):
        d = lesson.parse_blueprint(_bp(
            _mod_row(1) + _mod_row(2)
            + "- CP1 · Shapes · covers M1–M2\n"))
        self.assertEqual([r["kind"] for r in d["rows"]],
                         ["module", "module", "checkpoint"])
        self.assertEqual(d["rows"][0]["sources"],
                         ["02-Areas/Academics/TEST-101/lectures/l1.md"])
        self.assertEqual(d["rows"][2]["covers"], [1, 2])
        self.assertEqual(d["rows"][2]["title"], "Shapes")
        self.assertEqual(d["status"], "draft")

    def test_covers_accepts_ranges_lists_and_padded_numbers(self):
        self.assertEqual(lesson._covers_numbers("M01–M04"), [1, 2, 3, 4])
        self.assertEqual(lesson._covers_numbers("M2, M3"), [2, 3])
        self.assertEqual(lesson._covers_numbers("2-4, 7"), [2, 3, 4, 7])
        self.assertEqual(lesson._covers_numbers("junk"), [])
        self.assertEqual(lesson._covers_numbers("M4–M2"), [])

    def test_covers_ranges_accept_spaces_around_the_dash(self):
        """Natural typography in a grammar whose own separator is a spaced
        `·` — tokenising on whitespace first made the regex's space
        allowance dead code (S7 review)."""
        self.assertEqual(lesson._covers_numbers("M1 – M4"), [1, 2, 3, 4])
        self.assertEqual(lesson._covers_numbers("M01 - M04"), [1, 2, 3, 4])
        self.assertEqual(lesson._covers_numbers("M1, M3 – M5"), [1, 3, 4, 5])

    def test_a_plan_row_after_a_near_miss_heading_is_flagged_not_lost(self):
        """'## Unit 2 addendum' fails BP_UNIT_RE and used to silently swallow
        every row after it with problems=[] — an approved plan whose M05 no
        code path could see (S7 review, verified by execution)."""
        text = _bp(_mod_row(1)) + "\n## Unit 2 addendum\n\n" + _mod_row(5)
        d = lesson.parse_blueprint(text)
        self.assertEqual(len(d["rows"]), 1)
        self.assertTrue(any("ended the plan section" in p for p in d["problems"]),
                        d["problems"])
        self.assertNotEqual(lesson.validate_blueprint(text), [])

    def test_serialize_never_emits_a_unit_none_heading(self):
        """A None-unit row in a united plan used to serialize as a literal
        '## Unit None' its own parser reads as end-of-plan, silently dropping
        every row beneath it (S7 review, verified by execution)."""
        d = {"course": "TEST-101", "status": "draft",
             "units": [{"n": 1, "title": None, "line": 0}],
             "rows": ([{"kind": "module", "n": n, "title": f"T{n}", "unit": 1,
                        "est": 40, "sources": ["x/a.md"], "line": 0}
                       for n in (1, 2, 3, 4)]
                      + [{"kind": "checkpoint", "n": 1, "title": None,
                          "unit": None, "covers": [1, 2, 3, 4], "line": 0}]
                      + [{"kind": "module", "n": 5, "title": "Tail",
                          "unit": None, "est": 40, "sources": ["x/a.md"],
                          "line": 0}])}
        t = lesson.serialize_blueprint(d)
        self.assertNotIn("Unit None", t)
        p = lesson.parse_blueprint(t)
        self.assertEqual(len(p["rows"]), len(d["rows"]),
                         "the round trip must not lose rows")

    def test_leading_unitless_rows_get_a_modules_section(self):
        d = {"course": "TEST-101", "status": "draft",
             "units": [{"n": 1, "title": None, "line": 0}],
             "rows": ([{"kind": "module", "n": 1, "title": "Lead",
                        "unit": None, "est": 40, "sources": ["x/a.md"],
                        "line": 0}]
                      + [{"kind": "module", "n": n, "title": f"T{n}", "unit": 1,
                          "est": 40, "sources": ["x/a.md"], "line": 0}
                         for n in (2, 3, 4, 5)]
                      + [{"kind": "checkpoint", "n": 1, "title": None,
                          "unit": 1, "covers": [2, 3], "line": 0}])}
        t = lesson.serialize_blueprint(d)
        p = lesson.parse_blueprint(t)
        self.assertEqual(len(p["rows"]), 6)
        self.assertIsNone(p["rows"][0]["unit"])

    def test_a_checkpoint_row_without_covers_is_a_problem(self):
        d = lesson.parse_blueprint(_bp(_mod_row(1) + "- CP1 · just a title\n"))
        self.assertTrue(any("must name what it covers" in p
                            for p in d["problems"]))

    def test_an_unrecognised_bullet_in_the_plan_is_a_problem(self):
        d = lesson.parse_blueprint(_bp(_mod_row(1) + "- M2 broken row\n"))
        self.assertTrue(any("unrecognised row" in p for p in d["problems"]))

    def test_a_fence_hides_rows_from_the_parser(self):
        d = lesson.parse_blueprint(_bp(
            _mod_row(1) + "```\n- M09 · Fenced ⏱ 40\n```\n"))
        self.assertEqual(len(d["rows"]), 1)

    def test_rows_outside_a_plan_section_are_ignored(self):
        text = _bp(_mod_row(1)) + "\n## Notes\n- M09 · Not a plan row ⏱ 40\n"
        self.assertEqual(len(lesson.parse_blueprint(text)["rows"]), 1)

    def test_unit_sections_assign_units(self):
        rows = ("## Unit 1 · Vectors\n\n"
                + "".join(_mod_row(n) for n in (1, 2, 3, 4))
                + "- CP1 · covers M1–M4\n\n"
                "## Unit 2\n\n"
                + "".join(_mod_row(n) for n in (5, 6, 7, 8))
                + "- CP2 · covers M5–M8\n")
        text = _bp("").replace("## Modules\n\n", rows)
        d = lesson.parse_blueprint(text)
        self.assertEqual(d["rows"][0]["unit"], 1)
        self.assertEqual(d["rows"][5]["unit"], 2)
        self.assertEqual([u["n"] for u in d["units"]], [1, 2])
        self.assertEqual(lesson.validate_blueprint(text), [])

    def test_round_trip_is_byte_stable(self):
        d = lesson.parse_blueprint(_bp(
            _mod_row(1) + _mod_row(2) + "- CP1 · Shapes · covers M1, M2\n"))
        t = lesson.serialize_blueprint(d)
        self.assertEqual(lesson.serialize_blueprint(lesson.parse_blueprint(t)), t)

    def test_serialize_with_units_round_trips(self):
        d = {"course": "TEST-101", "status": "approved",
             "units": [{"n": 1, "title": "Vectors", "line": 0},
                       {"n": 2, "title": None, "line": 0}],
             "rows": ([{"kind": "module", "n": n, "title": f"T{n}", "unit": 1,
                        "est": 40, "sources": ["x/a.md"], "line": 0}
                       for n in (1, 2, 3, 4)]
                      + [{"kind": "checkpoint", "n": 1, "title": None, "unit": 1,
                          "covers": [1, 2, 3, 4], "line": 0}]
                      + [{"kind": "module", "n": n, "title": f"T{n}", "unit": 2,
                          "est": 40, "sources": ["x/a.md"], "line": 0}
                         for n in (5, 6, 7, 8)]
                      + [{"kind": "checkpoint", "n": 2, "title": None, "unit": 2,
                          "covers": [5, 6], "line": 0}])}
        t = lesson.serialize_blueprint(d)
        p = lesson.parse_blueprint(t)
        self.assertEqual(lesson.serialize_blueprint(p), t)
        self.assertEqual(p["rows"][4]["kind"], "checkpoint")
        self.assertEqual(p["rows"][5]["unit"], 2)


class TestValidation(unittest.TestCase):
    def assert_problem(self, text: str, needle: str, vault=None):
        problems = lesson.validate_blueprint(text, vault=vault)
        self.assertTrue(any(needle in p for p in problems),
                        f"expected {needle!r}, got: {problems}")

    def test_a_conforming_flat_plan_validates_clean(self):
        self.assertEqual(lesson.validate_blueprint(_bp(
            _mod_row(1) + _mod_row(2) + _mod_row(3) + _mod_row(4)
            + "- CP1 · covers M1–M4\n")), [])

    def test_status_must_be_draft_or_approved(self):
        self.assert_problem(_bp(_mod_row(1), status="maybe"), "status")

    def test_duplicate_module_numbers_are_a_problem(self):
        self.assert_problem(_bp(_mod_row(1) + _mod_row(1)), "duplicate module")

    def test_estimate_outside_the_band_is_a_problem(self):
        self.assert_problem(_bp(_mod_row(1, est=90)), "outside")

    def test_a_sourceless_module_row_is_a_problem(self):
        self.assert_problem(_bp("- M01 · Topic ⏱ 40\n"), "no source::")

    def test_covers_naming_an_unplanned_module_is_a_problem(self):
        self.assert_problem(_bp(_mod_row(1) + "- CP1 · covers M1, M9\n"),
                            "no module row plans")

    def test_an_assessment_cannot_precede_its_material(self):
        self.assert_problem(_bp(
            "- CP1 · covers M1\n" + _mod_row(1)), "cannot precede")

    def test_nine_flat_modules_demand_units(self):
        rows = "".join(_mod_row(n) for n in range(1, 10))
        self.assert_problem(_bp(rows), "grouped into units")

    def test_flat_checkpoint_cadence_is_enforced(self):
        rows = "".join(_mod_row(n) for n in range(1, 9))
        self.assert_problem(_bp(rows), "one every 4 modules")

    def test_an_oversized_unit_is_a_problem(self):
        rows = ("## Unit 1\n\n" + "".join(_mod_row(n) for n in range(1, 8))
                + "- CP1 · covers M1–M7\n")
        text = _bp("").replace("## Modules\n\n", rows)
        self.assert_problem(text, "units are 4–6")

    def test_a_unit_without_a_checkpoint_is_a_problem(self):
        rows = ("## Unit 1\n\n" + "".join(_mod_row(n) for n in (1, 2, 3, 4)))
        text = _bp("").replace("## Modules\n\n", rows)
        self.assert_problem(text, "no checkpoint row")

    def test_sources_resolve_only_when_a_vault_is_given(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            vault = Path(tmp) / "vault"
            (vault / "02-Areas/Academics/TEST-101/lectures").mkdir(parents=True)
            text = _bp(_mod_row(1))
            self.assert_problem(text, "does not resolve", vault=vault)
            (vault / "02-Areas/Academics/TEST-101/lectures/l1.md").write_text(
                "# l1\n", encoding="utf-8")
            self.assertEqual(lesson.validate_blueprint(text, vault=vault), [])


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a],
                          capture_output=True, text=True)


class TestVaultSide(unittest.TestCase):
    """load_blueprint / existing_units / scan_blueprints / guide() — what the
    pipeline's resume logic and the workbench affordance stand on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.course / "lectures").mkdir()
        (self.course / "lectures" / "l1.md").write_text("# l1\n", encoding="utf-8")
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")
        import privacy
        privacy.VaultPrivacy._git_ignored.cache_clear()

    def tearDown(self):
        self.tmp.cleanup()

    def bp(self, rows: str, status: str = "draft"):
        (self.course / "test-101-guide-blueprint.md").write_text(
            _bp(rows, status=status), encoding="utf-8")

    def module_note(self, n: int):
        from test_lesson import _valid
        text = _valid().replace("module: 1", f"module: {n}")
        (self.course / "guide" / f"test-101-m{n:02}-topic.md").write_text(
            text, encoding="utf-8")

    def test_load_blueprint_carries_identity_and_problems(self):
        self.bp(_mod_row(1, est=90))
        d = lesson.load_blueprint(self.vault, "TEST-101")
        self.assertEqual(d["file"],
                         "02-Areas/Academics/TEST-101/test-101-guide-blueprint.md")
        self.assertTrue(any("outside" in p for p in d["problems"]))
        self.assertIsNone(lesson.load_blueprint(self.vault, "NOPE-999"))

    def test_existing_units_reads_the_disk_not_a_state_file(self):
        self.assertEqual(lesson.existing_units(self.vault, "TEST-101"),
                         (set(), set()))
        self.module_note(2)
        from test_checkpoint import _valid_cp
        (self.course / "guide" / "test-101-checkpoint-1.md").write_text(
            _valid_cp(), encoding="utf-8")
        mods, cps = lesson.existing_units(self.vault, "TEST-101")
        self.assertEqual((mods, cps), ({2}, {1}))

    def test_scan_blueprints_surfaces_hand_edit_breakage(self):
        self.bp(_mod_row(1) + "- M2 broken row\n")
        rows = lesson.scan_blueprints(self.vault)
        self.assertEqual(len(rows), 1)
        self.assertTrue(any("unrecognised row" in p for p in rows[0]["problems"]))

    def test_guide_reports_planned_and_missing(self):
        self.bp(_mod_row(1) + _mod_row(2) + "- CP1 · covers M1, M2\n",
                status="approved")
        (self.course / "test-101-guide.md").write_text(
            "---\ntype: guide\ncourse: TEST-101\ntags: [guide]\n---\n\n"
            "## Modules\n\n- [ ] M01 · [[test-101-m01-topic|Topic 1]]\n",
            encoding="utf-8")
        self.module_note(1)
        g = lesson.guide(self.vault, "TEST-101")
        self.assertEqual((g["planned"], g["missing"]), (3, 2))

    def test_a_blueprint_without_a_chain_is_a_payload_not_a_404(self):
        self.bp(_mod_row(1))
        g = lesson.guide(self.vault, "TEST-101")
        self.assertIsNotNone(g)
        self.assertIsNone(g["file"])
        self.assertEqual(g["rows"], [])
        self.assertEqual(g["blueprint"], "draft")
        self.assertEqual(g["planned"], 1)

    def test_neither_note_is_still_none(self):
        self.assertIsNone(lesson.guide(self.vault, "TEST-101"))

    def test_a_blank_status_blueprint_still_answers_a_payload(self):
        """'leave unknowns blank' is the contract's own habit — a real
        blueprint note with an empty status: used to 404 the course and
        dead-end the workbench into offering to draft a plan that already
        exists (S7 review)."""
        (self.course / "test-101-guide-blueprint.md").write_text(
            _bp(_mod_row(1)).replace("status: draft", "status:"),
            encoding="utf-8")
        g = lesson.guide(self.vault, "TEST-101")
        self.assertIsNotNone(g)
        self.assertEqual(g["blueprint"], "unstated")
        self.assertEqual(g["planned"], 1)

    def test_a_sealed_blueprint_reads_as_absent(self):
        self.bp(_mod_row(1))
        (self.course / "test-101-guide.md").write_text(
            "---\ntype: guide\ncourse: TEST-101\ntags: [guide]\n---\n",
            encoding="utf-8")
        (self.vault / ".gitignore").write_text(
            "02-Areas/Academics/TEST-101/test-101-guide-blueprint.md\n",
            encoding="utf-8")
        import privacy
        privacy.VaultPrivacy._git_ignored.cache_clear()
        g = lesson.guide(self.vault, "TEST-101")
        self.assertIsNotNone(g)
        self.assertIsNone(g["blueprint"])
        self.assertIsNone(g["planned"])

    def test_validate_any_dispatches_blueprints(self):
        errs = lesson.validate_any(_bp(_mod_row(1, est=90)))
        self.assertTrue(any("outside" in p for p in errs))


if __name__ == "__main__":
    unittest.main()
