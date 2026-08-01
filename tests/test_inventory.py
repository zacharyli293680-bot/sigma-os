"""
inventory.py — the precomputed scan that replaced the auditor's grepping.

The property worth guarding is not the formatting, it is the *division of
labour*: this module reports facts and the contract decides what they mean.
A test that asserted "type X is invalid" here would be the beginning of the
second declaration that drifts, which is the thing the module exists not to be.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import inventory                                 # noqa: E402


class InventoryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, rel: str, fm: str = "", body: str = "") -> Path:
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((f"---\n{fm}\n---\n\n{body}\n" if fm else body), encoding="utf-8")
        return p


class TestScan(InventoryBase):
    def test_frontmatter_is_reported_per_note(self):
        self.note("01-Daily/2026-07-31.md", "type: daily\ndate: 2026-07-31")
        rows = dict(inventory.scan_notes(self.vault))
        self.assertEqual(rows["01-Daily/2026-07-31.md"]["type"], "daily")

    def test_machinery_folders_are_skipped(self):
        self.note("99-Meta/Templates/daily.md", "type: daily")
        self.note(".obsidian/x.md", "type: daily")
        self.note("01-Daily/real.md", "type: daily")
        rels = [r for r, _ in inventory.scan_notes(self.vault)]
        self.assertEqual(rels, ["01-Daily/real.md"])

    def test_sealed_paths_are_excluded_when_given(self):
        """This module does not re-implement the privacy boundary; it honours
        the set it is handed."""
        self.note("Client/secret.md", "type: resource")
        self.note("01-Daily/ok.md", "type: daily")
        rels = [r for r, _ in inventory.scan_notes(self.vault, sealed={"Client/secret.md"})]
        self.assertNotIn("Client/secret.md", rels)
        self.assertIn("01-Daily/ok.md", rels)

    def test_a_note_with_no_frontmatter_is_still_listed(self):
        """Missing frontmatter is a finding, so it must not vanish."""
        self.note("01-Daily/bare.md", body="no frontmatter here")
        out = inventory.render(self.vault)
        self.assertIn("01-Daily/bare.md", out)
        self.assertIn("no frontmatter fields", out)


class TestManifestCoverage(InventoryBase):
    """The check whose cost started all of this."""

    def course(self, code: str, index_body: str = ""):
        self.note(f"02-Areas/Academics/{code}/{code.lower()}.md",
                  f"type: course-index\ncourse: {code}", index_body)

    def test_unlinked_sibling_notes_are_named(self):
        self.course("CSE-311")
        self.note("02-Areas/Academics/CSE-311/lectures/induction.md", "type: lecture")
        rows = {c: (linked, missing)
                for c, _, linked, missing in inventory.manifest_coverage(self.vault)}
        self.assertEqual(rows["CSE-311"], ([], ["induction"]))

    def test_a_linked_sibling_counts_as_linked(self):
        self.course("CSE-311", "See [[induction]] for the proof.")
        self.note("02-Areas/Academics/CSE-311/lectures/induction.md", "type: lecture")
        _, _, linked, missing = inventory.manifest_coverage(self.vault)[0]
        self.assertEqual((linked, missing), (["induction"], []))

    def test_full_path_links_count_too(self):
        """Course indexes in this vault link by full path with an alias."""
        self.course("CSE-311",
                    "- [[02-Areas/Academics/CSE-311/lectures/induction|induction]]")
        self.note("02-Areas/Academics/CSE-311/lectures/induction.md", "type: lecture")
        _, _, linked, missing = inventory.manifest_coverage(self.vault)[0]
        self.assertEqual((linked, missing), (["induction"], []))

    def test_a_course_folder_with_no_index_is_reported(self):
        self.note("02-Areas/Academics/CSE-999/notes.md", "type: lecture")
        rows = inventory.manifest_coverage(self.vault)
        self.assertEqual(rows[0][0], "CSE-999")
        self.assertIsNone(rows[0][1])

    def test_missing_notes_are_flagged_loudly_in_the_render(self):
        self.course("CSE-311")
        self.note("02-Areas/Academics/CSE-311/lectures/induction.md", "type: lecture")
        self.assertIn("NOT linked", inventory.render(self.vault))


class TestRenderContract(InventoryBase):
    def test_it_tells_the_model_not_to_rescan(self):
        """The whole point: the instruction not to re-derive this must survive
        any future edit to the renderer."""
        self.note("01-Daily/a.md", "type: daily")
        out = inventory.render(self.vault)
        self.assertIn("Do not re-derive", out)

    def test_it_reports_types_without_judging_them(self):
        """An unknown type is listed, not labelled invalid — CLAUDE.md decides."""
        self.note("01-Daily/a.md", "type: notathing")
        out = inventory.render(self.vault)
        self.assertIn("notathing", out)
        for verdict in ("invalid", "illegal", "not allowed", "violation"):
            self.assertNotIn(verdict, out.lower())

    def test_for_auditor_never_raises(self):
        """A broken gatherer must cost the run its shortcut, not its life."""
        saved = inventory.render
        inventory.render = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            out = inventory.for_auditor()
        finally:
            inventory.render = saved
        self.assertIn("Could not be precomputed", out)


if __name__ == "__main__":
    unittest.main()
