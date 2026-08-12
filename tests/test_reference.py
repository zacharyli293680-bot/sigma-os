#!/usr/bin/env python3
"""
The reference sheet (study S9) — one note, two tiers.

The tiers are the whole design, so most of what is worth pinning is about
them: that `exam` is a *filter* over the same entries rather than a second
document, that a sheet with no exam tier is refused (the simplified view is
the one with a hard constraint, and an empty one is not a simplification), and
that the budget is enforced rather than suggested — a "fits on one sheet" that
does not fit is the single promise this tier makes.

The rest is the usual: structure is checked, content is not. Whether an
equation is *correct* is Zach's judgement at review, exactly as with a module.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import lesson as ln  # noqa: E402

FM = ("---\n"
      "type: reference\n"
      "course: AA-210\n"
      "tags: [guide]\n"
      "---\n\n")

OK = FM + """## Vectors

### Dot product
kind:: equation
tier:: exam
$$\\mathbf{A}\\cdot\\mathbf{B} = \\lVert\\mathbf{A}\\rVert\\lVert\\mathbf{B}\\rVert\\cos\\theta$$
Zero means perpendicular.

### Scalar triple product
kind:: equation
tier:: full
$$\\mathbf{A}\\cdot(\\mathbf{B}\\times\\mathbf{C})$$
The volume of the parallelepiped.

## Constants

### Standard gravity
kind:: constant
tier:: exam
$g = 9.81\\ \\mathrm{m/s^2}$ near the Earth's surface.
"""


class TestTheGrammar(unittest.TestCase):
    def test_a_plausible_sheet_validates(self):
        self.assertEqual(ln.validate_reference(OK), [])

    def test_sections_and_entries_come_back(self):
        d = ln.parse_reference(OK)
        self.assertEqual([s["title"] for s in d["sections"]],
                         ["Vectors", "Constants"])
        self.assertEqual(len(ln.reference_entries(d)), 3)

    def test_the_body_survives_the_keys(self):
        d = ln.parse_reference(OK)
        first = ln.reference_entries(d)[0]
        self.assertIn("perpendicular", first["body"])
        self.assertNotIn("tier::", first["body"])

    def test_a_key_below_the_body_is_prose(self):
        """`kind::` and `tier::` are a header, not a syntax that can appear
        anywhere — otherwise a sentence *about* the tiers rewrites the entry."""
        d = ln.parse_reference(FM + "## S\n\n### E\nkind:: definition\n"
                                    "tier:: exam\nThe word tier:: exam is prose here.\n")
        e = ln.reference_entries(d)[0]
        self.assertEqual(e["tier"], "exam")
        self.assertIn("prose here", e["body"])


class TestTheTiers(unittest.TestCase):
    def test_exam_is_a_subset_of_full(self):
        d = ln.parse_reference(OK)
        full = {e["title"] for e in ln.reference_entries(d, "full")}
        exam = {e["title"] for e in ln.reference_entries(d, "exam")}
        self.assertTrue(exam < full, "exam must be a strict subset")
        self.assertEqual(exam, {"Dot product", "Standard gravity"})

    def test_the_detailed_view_is_everything(self):
        """Not the complement of the simplified one — a reader on the detailed
        tier wants the whole sheet, including what is also on the exam sheet."""
        d = ln.parse_reference(OK)
        self.assertEqual(len(ln.reference_entries(d, "full")),
                         len(ln.reference_entries(d)))

    def test_a_sheet_with_no_exam_tier_is_refused(self):
        errs = ln.validate_reference(OK.replace("tier:: exam", "tier:: full"))
        self.assertTrue(any("no exam-tier" in e for e in errs), errs)

    def test_the_budget_is_enforced(self):
        fat = FM + "## S\n\n### Long one\nkind:: definition\ntier:: exam\n" \
            + ("word " * (ln.EXAM_BUDGET // 5 + 200)) + "\n"
        errs = ln.validate_reference(fat)
        self.assertTrue(any("over the" in e for e in errs), errs)

    def test_size_counts_only_the_exam_tier(self):
        d = ln.parse_reference(OK)
        exam_only = sum(len(e["title"]) + len(e["body"]) + 2
                        for e in ln.reference_entries(d, "exam"))
        self.assertEqual(ln.reference_size(d), exam_only)


class TestItRefuses(unittest.TestCase):
    def refused(self, text: str, needle: str):
        errs = ln.validate_reference(text)
        self.assertTrue(any(needle in e for e in errs),
                        f"expected {needle!r} in {errs}")

    def test_an_unknown_kind(self):
        self.refused(OK.replace("kind:: constant", "kind:: mnemonic"), "kind::")

    def test_an_unknown_tier(self):
        self.refused(OK.replace("tier:: full", "tier:: medium"), "tier::")

    def test_a_duplicate_title(self):
        self.refused(OK.replace("Scalar triple product", "Dot product"),
                     "duplicate")

    def test_an_entry_with_no_body(self):
        self.refused(FM + "## S\n\n### Empty\nkind:: definition\ntier:: exam\n",
                     "no body")

    def test_a_section_with_no_entries(self):
        self.refused(OK + "\n## Orphan section\n", "no entries")

    def test_the_wrong_type(self):
        self.refused(OK.replace("type: reference", "type: module"), "type is")

    def test_content_before_the_first_entry(self):
        self.refused(FM + "## S\n\nloose prose\n\n### E\nkind:: definition\n"
                          "tier:: exam\nbody\n",
                     "content before the first")


class TestDispatch(unittest.TestCase):
    def test_validate_any_routes_by_the_note_s_own_type(self):
        """Running the module validator over a reference would report a dozen
        missing-segment complaints and bury the real one."""
        self.assertEqual(ln.validate_any(OK), [])
        self.assertTrue(ln.validate(OK), "the module validator should reject it")


if __name__ == "__main__":
    unittest.main()
