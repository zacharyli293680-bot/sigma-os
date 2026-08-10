"""
The two S7 applier guards (study plan §6), with test_applier.py's own
discipline: every hold checks the action, the reason, still-pending, and the
target untouched — because these run unattended, and a malformed or
unauthorised module slipping through reads as a shipped lesson.
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

import applier                     # noqa: E402
import reflect as rf               # noqa: E402
from sigma import gitops, ledger   # noqa: E402
from test_applier import PROPOSAL_TEMPLATE, _run   # noqa: E402
from test_checkpoint import _valid_cp              # noqa: E402
from test_lesson import _valid                     # noqa: E402


def _blueprint(status="approved", rows=None):
    rows = rows if rows is not None else (
        "- M01 · Test module ⏱ 30\n"
        "    - source:: 02-Areas/Academics/TEST-101/lectures/l1.md\n"
        "- M02 · Second module ⏱ 30\n"
        "    - source:: 02-Areas/Academics/TEST-101/lectures/l2.md\n"
        "- CP1 · covers M1, M2\n")
    return ("---\n"
            "type: guide-blueprint\n"
            "course: TEST-101\n"
            f"status: {status}\n"
            "tags: [guide]\n"
            "---\n\n# plan\n\n## Modules\n\n" + rows)


class HoldBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        (self.vault / "06-System" / "proposals").mkdir(parents=True)
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.course / "lectures").mkdir()
        for n in (1, 2, 3):
            (self.course / "lectures" / f"l{n}.md").write_text(
                "# src\n", encoding="utf-8")
        (self.vault / "CLAUDE.md").write_text("# contract\n", encoding="utf-8")
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _run(self.vault, "init", "-b", "master")
        _run(self.vault, "config", "user.name", "T")
        _run(self.vault, "config", "user.email", "t@e.com")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "seed")

        self._saved = (rf.VAULT, rf.PROPOSALS, rf.CONTRACT, rf.STAGED,
                       gitops.MUTEX_PATH, ledger.LEDGER_PATH, applier.log)
        rf.VAULT = self.vault
        rf.PROPOSALS = self.vault / "06-System" / "proposals"
        rf.CONTRACT = self.vault / "CLAUDE.md"
        rf.STAGED = self.vault / "06-System" / "proposed"
        gitops.MUTEX_PATH = root / "git.lock"
        ledger.LEDGER_PATH = root / "ledger.jsonl"
        applier.log = lambda msg: None
        import privacy
        privacy.VaultPrivacy._git_ignored.cache_clear()

    def tearDown(self):
        (rf.VAULT, rf.PROPOSALS, rf.CONTRACT, rf.STAGED,
         gitops.MUTEX_PATH, ledger.LEDGER_PATH, applier.log) = self._saved
        self.tmp.cleanup()

    def blueprint(self, status="approved", rows=None):
        (self.course / "test-101-guide-blueprint.md").write_text(
            _blueprint(status, rows), encoding="utf-8")

    def proposal(self, name, target, content):
        p = rf.PROPOSALS / f"{name}.md"
        p.write_text(PROPOSAL_TEMPLATE.format(
            status="pending", kind="note", target=target,
            title=name.replace("-", " "), content=content), encoding="utf-8")
        return p

    def module_target(self, n=1, base="test-101-m01-test-module"):
        return f"02-Areas/Academics/TEST-101/guide/{base}"

    def assert_held(self, out, p, needle, target_rel=None):
        self.assertEqual(out["action"], "held", out)
        self.assertIn(needle, out["reason"])
        text = p.read_text(encoding="utf-8")
        self.assertIn("status: pending", text)
        self.assertIn("**Held ", text)
        if target_rel:
            self.assertFalse((self.vault / target_rel).exists(),
                             "the held target must stay unwritten")


class TestStructuralHold(HoldBase):
    def test_a_module_that_fails_the_grammar_is_held(self):
        self.blueprint()
        target = "02-Areas/Academics/TEST-101/guide/test-101-m01-test-module.md"
        broken = _valid().replace("- answer:: 2\n", "", 1)
        p = self.proposal("broken-module", target, broken)
        self.assert_held(applier.apply_one(p, "guide"), p,
                         "module fails the grammar", target)

    def test_an_unresolvable_source_is_held(self):
        self.blueprint()
        target = "02-Areas/Academics/TEST-101/guide/test-101-m01-test-module.md"
        bad = _valid().replace("lectures/l1.md", "lectures/ghost.md")
        p = self.proposal("ghost-source", target, bad)
        self.assert_held(applier.apply_one(p, "guide"), p, "grammar", target)

    def test_a_checkpoint_with_a_depth_heading_is_held(self):
        self.blueprint()
        target = "02-Areas/Academics/TEST-101/guide/test-101-checkpoint-1.md"
        bad = _valid_cp() + "\n### Summary\n\nteaching text\n"
        p = self.proposal("depth-checkpoint", target, bad)
        self.assert_held(applier.apply_one(p, "guide"), p,
                         "checkpoint fails the grammar", target)

    def test_a_grammar_update_to_an_existing_module_is_still_held(self):
        """The structural hold guards updates too — a tutor correction that
        breaks the note must not land just because the note exists."""
        dest = self.course / "guide" / "test-101-m01-test-module.md"
        dest.write_text(_valid(), encoding="utf-8")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "module")
        broken = _valid().replace("### Normal", "### Wrong", 1)
        p = self.proposal("break-existing",
                          "02-Areas/Academics/TEST-101/guide/test-101-m01-test-module.md",
                          broken)
        self.assert_held(applier.apply_one(p, "tutor"), p, "grammar")
        self.assertEqual(dest.read_text(encoding="utf-8"), _valid())

    def test_a_non_guide_note_is_untouched_by_the_new_guards(self):
        (self.vault / "04-Resources").mkdir()
        p = self.proposal("plain-note", "04-Resources/plain.md",
                          "# Plain\n\nA resource note.")
        out = applier.apply_one(p, "auditor")
        self.assertEqual(out["action"], "create")


class TestBlueprintHold(HoldBase):
    def target(self):
        return "02-Areas/Academics/TEST-101/guide/test-101-m01-test-module.md"

    def test_a_new_module_without_any_blueprint_is_held(self):
        p = self.proposal("no-blueprint", self.target(), _valid())
        self.assert_held(applier.apply_one(p, "guide"), p,
                         "no blueprint", self.target())

    def test_a_draft_blueprint_does_not_authorise(self):
        self.blueprint(status="draft")
        p = self.proposal("draft-only", self.target(), _valid())
        self.assert_held(applier.apply_one(p, "guide"), p,
                         "not approved", self.target())

    def test_a_module_the_plan_does_not_name_is_held(self):
        self.blueprint(rows="- M07 · Something else ⏱ 40\n"
                            "    - source:: 02-Areas/Academics/TEST-101/lectures/l1.md\n")
        p = self.proposal("uninvited", self.target(), _valid())
        self.assert_held(applier.apply_one(p, "guide"), p,
                         "not in TEST-101's approved blueprint", self.target())

    def test_a_title_that_drifted_from_the_plan_is_held(self):
        self.blueprint(rows="- M01 · A Different Title ⏱ 30\n"
                            "    - source:: 02-Areas/Academics/TEST-101/lectures/l1.md\n")
        p = self.proposal("drifted-title", self.target(), _valid())
        self.assert_held(applier.apply_one(p, "guide"), p,
                         "does not match", self.target())

    def test_a_checkpoint_covering_an_unplanned_module_is_held(self):
        self.blueprint()
        cp = _valid_cp().replace("covers: [1, 2]", "covers: [1, 9]")
        target = "02-Areas/Academics/TEST-101/guide/test-101-checkpoint-1.md"
        p = self.proposal("over-covers", target, cp)
        self.assert_held(applier.apply_one(p, "guide"), p,
                         "does not plan", target)

    def test_a_planned_module_applies_and_carries_the_run_id(self):
        self.blueprint()
        p = self.proposal("planned-module", self.target(), _valid())
        out = applier.apply_one(p, "guide", extra={"run": "gen-test-1"})
        self.assertEqual(out["action"], "create", out)
        self.assertIsNotNone(out["sha"])
        led = ledger.entries()
        self.assertEqual(led[0]["extra"]["run"], "gen-test-1")
        self.assertEqual(led[0]["extra"]["proposal"], "planned-module")

    def test_a_planned_checkpoint_applies(self):
        self.blueprint()
        target = "02-Areas/Academics/TEST-101/guide/test-101-checkpoint-1.md"
        p = self.proposal("planned-cp", target, _valid_cp())
        out = applier.apply_one(p, "guide", extra={"run": "gen-test-1"})
        self.assertEqual(out["action"], "create", out)

    def test_an_update_to_an_existing_module_needs_no_blueprint(self):
        """Creation-only, deliberately: the pilot's hand-written modules
        predate any blueprint, and an auditor fixing one must not be blocked
        on a plan that never existed."""
        dest = self.course / "guide" / "test-101-m01-test-module.md"
        dest.write_text(_valid(), encoding="utf-8")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "module")
        better = _valid().replace("A preamble paragraph.",
                                  "A corrected preamble paragraph.")
        p = self.proposal("fix-existing",
                          "02-Areas/Academics/TEST-101/guide/test-101-m01-test-module.md",
                          better)
        out = applier.apply_one(p, "tutor")
        self.assertEqual(out["action"], "update", out)


if __name__ == "__main__":
    unittest.main()
