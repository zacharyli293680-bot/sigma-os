"""
`sigma guide <course> --redo M02,CP1` — re-authoring a note that already exists.

The pipeline could only ever write what was *missing*, which meant a module
authored under an older prompt was frozen: improving the prompt could not reach
anything already on disk. `--redo` is the way back to those notes.

The interesting part is not that it re-authors — it is what it refuses. `--redo`
deliberately skips the blueprint approval gate, so the tests below exist to pin
down why that is sound rather than convenient:

  · it cannot CREATE. Every named row must already exist on disk, so nothing it
    does can put an unapproved module into a course.
  · it cannot RECONCILE. A normal run writes every planned row into the chain
    and the course index; doing that from a draft blueprint would publish the
    whole unapproved plan as links, which is exactly what the gate prevents.

That pairing is what `applier.apply_one` already assumes — it runs
`_blueprint_hold` under `if ctype in ("module", "checkpoint") and not existed` —
and what the contract states: an update to an existing note needs only the
grammar.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import guide as gd                                          # noqa: E402
import lesson as ln                                         # noqa: E402
from test_guide_run import GuideBase, compose_stub, _git    # noqa: E402


class TestParseRedo(unittest.TestCase):
    def test_the_shapes_a_person_types(self):
        for spec, want in (
            ("M02,CP1", ({2}, {1})),
            ("m2, cp1", ({2}, {1})),      # lower case, spaces, no zero pad
            ("M02", ({2}, set())),
            ("CP1", (set(), {1})),
            ("M02,M03", ({2, 3}, set())),
        ):
            with self.subTest(spec=spec):
                mods, cps, bad = gd.parse_redo(spec)
                self.assertEqual((mods, cps), want)
                self.assertEqual(bad, [])

    def test_a_typo_is_returned_rather_than_raised(self):
        """One bad name must not silently decide the fate of the good ones —
        the caller refuses the whole run and says which was unreadable."""
        mods, cps, bad = gd.parse_redo("M02,lecture 4,CP1")
        self.assertEqual((mods, cps), ({2}, {1}))
        self.assertEqual(bad, ["lecture 4"])


class RedoBase(GuideBase):
    """A course whose plan is approved and fully authored, then set back to
    draft — the state a real course is in after a prompt improves."""

    def build(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)

    def unapprove(self):
        bp = self.course / "test-101-guide-blueprint.md"
        bp.write_text(bp.read_text(encoding="utf-8")
                      .replace("status: approved", "status: draft"),
                      encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "back to draft")

    def approve_and_build(self):
        bp = self.course / "test-101-guide-blueprint.md"
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)   # blueprint
        bp.write_text(bp.read_text(encoding="utf-8")
                      .replace("status: draft", "status: approved"),
                      encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "approve")
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)   # author


class TestItRefuses(RedoBase):
    def setUp(self):
        super().setUp()
        self.approve_and_build()

    def test_a_row_that_is_not_in_the_plan(self):
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub,
                    redo="M09")
        self.assertEqual(rc, 2)

    def test_a_row_that_does_not_exist_on_disk(self):
        """The refusal that makes skipping the approval gate safe: --redo can
        never be a back door to creating a module."""
        (self.course / "guide" / "test-101-m02-second-module.md").unlink()
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub,
                    redo="M02")
        self.assertEqual(rc, 2)
        self.assertFalse((self.course / "guide"
                          / "test-101-m02-second-module.md").exists(),
                         "--redo created a note it was told to re-author")

    def test_an_unreadable_name(self):
        self.assertEqual(
            gd.run("TEST-101", vault=self.vault, compose=compose_stub,
                   redo="module two"), 2)


class TestItRewrites(RedoBase):
    def setUp(self):
        super().setUp()
        self.approve_and_build()
        self.m2 = self.course / "guide" / "test-101-m02-second-module.md"
        self.chain = self.course / "test-101-guide.md"

    def test_it_runs_against_a_draft_blueprint(self):
        """The whole point. A prompt improves, the plan is not re-approved, and
        the notes already authorised can still be brought forward."""
        self.unapprove()
        before = self.m2.read_text(encoding="utf-8")
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub,
                    redo="M02")
        self.assertEqual(rc, 0)
        self.assertTrue(self.m2.exists())
        self.assertEqual(ln.validate(self.m2.read_text(encoding="utf-8"),
                                     vault=self.vault), [])
        self.assertTrue(before)     # it was there before, and still is

    def test_a_normal_run_still_needs_approval(self):
        """--redo must not have loosened the gate for everything else."""
        self.unapprove()
        (self.course / "guide" / "test-101-m01-first-module.md").unlink()
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "drop m1")
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.assertFalse((self.course / "guide"
                          / "test-101-m01-first-module.md").exists(),
                         "a draft blueprint authored a missing module")

    def test_it_leaves_the_chain_alone(self):
        """Reconciling from a draft plan would publish every unapproved row as
        a link — the gate's whole purpose, reached by a different door."""
        self.unapprove()
        before = self.chain.read_text(encoding="utf-8")
        gd.run("TEST-101", vault=self.vault, compose=compose_stub, redo="M02")
        self.assertEqual(self.chain.read_text(encoding="utf-8"), before)

    def test_it_overwrites_rather_than_duplicating(self):
        one = sorted((self.course / "guide").glob("*.md"))
        gd.run("TEST-101", vault=self.vault, compose=compose_stub, redo="M02")
        self.assertEqual(sorted((self.course / "guide").glob("*.md")), one)

    def test_several_rows_at_once(self):
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub,
                    redo="M01,M02,CP1")
        self.assertEqual(rc, 0)
        for f in ("test-101-m01-first-module.md",
                  "test-101-m02-second-module.md",
                  "test-101-checkpoint-1.md"):
            self.assertTrue((self.course / "guide" / f).exists(), f)


if __name__ == "__main__":
    unittest.main()
