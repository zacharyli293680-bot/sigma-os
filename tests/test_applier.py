"""
Tests for applier.py — the deterministic auto-apply layer.

Every guard gets an adversarial case, because this module is the exact spot
where "propose, don't self-apply" was reversed: anything that slips through
here runs unattended at 09:00 with nobody watching.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import applier                     # noqa: E402
import reflect as rf               # noqa: E402
from sigma import gitops, ledger   # noqa: E402


def _run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8")


PROPOSAL_TEMPLATE = (
    "---\n"
    "type: proposal\n"
    "date: 2026-07-30\n"
    "status: {status}\n"
    "kind: {kind}\n"
    'target: "{target}"\n'
    "risk: low\n"
    'source_insight: ""\n'
    "applied:\n"
    "tags: [proposal]\n"
    "---\n\n"
    "# {title}\n\n"
    "> **{kind}** -> `{target}` · risk **low** · status **pending**\n\n"
    "## Why\nTesting.\n\n"
    "## The change\n"
    "<!-- proposal:content -->\n"
    "```markdown\n"
    "{content}\n"
    "```\n\n"
    "## Related\n- x\n"
)


class TestApplier(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        (self.vault / "06-System" / "proposals").mkdir(parents=True)
        (self.vault / "04-Resources").mkdir()
        (self.vault / "CLAUDE.md").write_text("# contract\n", encoding="utf-8")
        (self.vault / ".gitignore").write_text("02-Areas/Sealed/\n", encoding="utf-8")
        (self.vault / "04-Resources" / "existing.md").write_text(
            "---\ntype: resource\ntags: [resource]\n---\n\n# Existing\n\n"
            "A body long enough that a hollow rewrite is measurably smaller "
            "than what it replaces, several times over. " * 3 +
            "\n- [ ] an open task Zach owns\n", encoding="utf-8")
        _run(self.vault, "init", "-b", "master")
        _run(self.vault, "config", "user.name", "Test")
        _run(self.vault, "config", "user.email", "t@example.com")
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

    def tearDown(self):
        (rf.VAULT, rf.PROPOSALS, rf.CONTRACT, rf.STAGED,
         gitops.MUTEX_PATH, ledger.LEDGER_PATH, applier.log) = self._saved
        self.tmp.cleanup()

    def proposal(self, name, kind="note", target="04-Resources/new-note.md",
                 content="# New note\n\nFresh content.", status="pending"):
        p = rf.PROPOSALS / f"{name}.md"
        p.write_text(PROPOSAL_TEMPLATE.format(
            status=status, kind=kind, target=target,
            title=name.replace("-", " "), content=content), encoding="utf-8")
        return p

    def test_new_note_applies_with_its_own_commit(self):
        p = self.proposal("create-a-note")
        out = applier.apply_one(p, "planner")
        self.assertEqual(out["action"], "create")
        self.assertIsNotNone(out["sha"])
        dest = self.vault / "04-Resources" / "new-note.md"
        self.assertIn("Fresh content", dest.read_text(encoding="utf-8"))
        show = _run(self.vault, "show", "--name-only", "--format=%s", out["sha"]).stdout
        self.assertIn("sigma(planner): create 04-Resources/new-note.md", show)
        led = ledger.entries()
        self.assertEqual(led[0]["sha"], out["sha"])
        self.assertEqual(led[0]["actor"], "planner")
        text = p.read_text(encoding="utf-8")
        self.assertIn("status: applied", text)
        self.assertIn("Auto-applied", text)

    def test_update_replaces_and_is_revertible(self):
        p = self.proposal("fix-existing", target="04-Resources/existing.md",
                          content="# Existing\n\nCorrected body, still substantial "
                                  "enough to pass the hollow guard. " * 4 +
                                  "\n- [ ] an open task Zach owns")
        out = applier.apply_one(p, "auditor")
        self.assertEqual(out["action"], "update")
        self.assertIsNotNone(out["sha"])
        r = gitops.revert(self.vault, out["sha"])
        self.assertTrue(r["ok"])
        self.assertIn("A body long enough",
                      (self.vault / "04-Resources" / "existing.md")
                      .read_text(encoding="utf-8"))

    def _assert_held(self, out, p, needle):
        self.assertEqual(out["action"], "held")
        self.assertIn(needle, out["reason"])
        text = p.read_text(encoding="utf-8")
        self.assertIn("status: pending", text)
        self.assertIn("**Held ", text)

    def test_contract_kind_is_held(self):
        p = self.proposal("amend-contract", kind="contract", target="CLAUDE.md",
                          content="- new convention")
        self._assert_held(applier.apply_one(p, "auditor"), p, "approval gate")

    def test_note_kind_aimed_at_contract_is_still_held(self):
        p = self.proposal("sneaky-contract", kind="note", target="CLAUDE.md",
                          content="# rewritten contract")
        self._assert_held(applier.apply_one(p, "auditor"), p, "single held category")
        self.assertEqual((self.vault / "CLAUDE.md").read_text(encoding="utf-8"),
                         "# contract\n")

    def test_checkbox_tick_is_held(self):
        p = self.proposal("tick-a-box", target="04-Resources/existing.md",
                          content="# Existing\n\nA body long enough that a hollow "
                                  "rewrite is measurably smaller than what it "
                                  "replaces, several times over. " * 3 +
                                  "\n- [x] an open task Zach owns")
        self._assert_held(applier.apply_one(p, "coach"), p, "human signal")
        self.assertIn("- [ ] an open task",
                      (self.vault / "04-Resources" / "existing.md")
                      .read_text(encoding="utf-8"))

    def test_hollow_rewrite_is_held(self):
        p = self.proposal("gut-the-note", target="04-Resources/existing.md",
                          content="# Existing\n\ngutted")
        self._assert_held(applier.apply_one(p, "coach"), p, "shrink")

    def test_gitignored_target_is_held_and_never_written(self):
        p = self.proposal("write-sealed", target="02-Areas/Sealed/private.md",
                          content="# should never land")
        self._assert_held(applier.apply_one(p, "planner"), p, "gitignored")
        self.assertFalse((self.vault / "02-Areas" / "Sealed" / "private.md").exists())

    def test_escaping_target_is_held(self):
        p = self.proposal("escape", target="../outside.md", content="# nope")
        self._assert_held(applier.apply_one(p, "planner"), p, "does not resolve")
        self.assertFalse((Path(self.tmp.name) / "outside.md").exists())

    def test_proposal_machinery_target_is_held(self):
        p = self.proposal("self-referential",
                          target="06-System/proposals/other.md", content="# no")
        self._assert_held(applier.apply_one(p, "planner"), p, "machinery")

    def test_non_pending_proposal_is_ignored(self):
        p = self.proposal("already-applied", status="applied")
        out = applier.apply_one(p, "planner")
        self.assertEqual(out["action"], "held")
        self.assertIn("not a pending proposal", out["reason"])

    def test_apply_run_processes_only_named_files(self):
        a = self.proposal("first-note", target="04-Resources/a.md", content="# a\n\nbody")
        self.proposal("second-note", target="04-Resources/b.md", content="# b\n\nbody")
        out = applier.apply_run([a.name], "planner")
        self.assertEqual(len(out), 1)
        self.assertTrue((self.vault / "04-Resources" / "a.md").exists())
        self.assertFalse((self.vault / "04-Resources" / "b.md").exists())


if __name__ == "__main__":
    unittest.main()
