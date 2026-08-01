"""
Scaffold (Phase 6): the only thing in Sigma that writes outside the vault.

Everything else here proposes a note and lets `applier.py` land it inside one
directory tree. This creates a folder in `CS Projects\\`, and the folder's name
comes from a model — so the name is untrusted input on its way to becoming a
filesystem path, and `vet` is the whole safety story. Traversal, absolute paths,
and "that project already exists" each get a test, because the failure mode is
not a bad note you delete but a write into someone else's repo.

The `.gitignore` tests matter for a quieter reason: the model picks a stack
*name* and this file supplies the contents. A model-written .gitignore that
omits `.env` is a credential leak with a plausible explanation, so the secrets
block is asserted to be present for every stack, including the one called
"other" that has no stack-specific rules at all.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import scaffold                                     # noqa: E402


PLAN = {
    "name": "thumbwatch", "title": "Thumbwatch", "stack": "python",
    "summary": "Watches a folder and thumbnails any image dropped in it.",
    "goal": "A CLI that runs in the background and writes thumbnails.",
    "readme": "# Thumbwatch\n\nWatches a folder.\n",
    "tasks": ["Pick a file-watching library", "Write the thumbnailer"],
}


class Gitignore(unittest.TestCase):
    def test_every_stack_ignores_secrets(self):
        """The reason the model does not write this file."""
        for stack in scaffold.STACKS:
            body = scaffold.gitignore_for(stack)
            self.assertIn(".env", body, stack)
            self.assertIn("*.key", body, stack)

    def test_a_stack_contributes_its_own_rules(self):
        self.assertIn("__pycache__/", scaffold.gitignore_for("python"))
        self.assertIn("node_modules/", scaffold.gitignore_for("node"))
        self.assertIn("target/", scaffold.gitignore_for("java-maven"))

    def test_an_unknown_stack_degrades_to_other_rather_than_raising(self):
        body = scaffold.gitignore_for("cobol-on-cogs")
        self.assertIn(".env", body)

    def test_the_python_example_env_stays_committable(self):
        self.assertIn("!.env.example", scaffold.gitignore_for("python"))


class Vet(unittest.TestCase):
    """The model chose the name, so the name is untrusted input."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name) / "CS Projects"
        self.root.mkdir(parents=True)
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "03-Projects").mkdir(parents=True)
        self._saved = (scaffold.VAULT, scaffold.PROJECTS)
        scaffold.VAULT = self.vault
        scaffold.PROJECTS = self.vault / "03-Projects"

    def tearDown(self):
        scaffold.VAULT, scaffold.PROJECTS = self._saved
        self.tmp.cleanup()

    def _vet(self, **over):
        return scaffold.vet({**PLAN, **over}, self.root)

    def test_a_clean_plan_passes(self):
        name, stack, why = self._vet()
        self.assertIsNone(why)
        self.assertEqual((name, stack), ("thumbwatch", "python"))

    def test_traversal_in_the_name_is_refused(self):
        for bad in ("../escape", "..\\escape", "a/../../b", "/etc/passwd",
                    "C:\\Windows\\System32", ".", ".."):
            _, _, why = self._vet(name=bad, title="")
            self.assertIsNotNone(why, f"{bad!r} was not refused")

    def test_a_name_with_separators_is_refused(self):
        for bad in ("sub/dir", "sub\\dir", "a:b"):
            _, _, why = self._vet(name=bad, title="")
            self.assertIsNotNone(why, f"{bad!r} was not refused")

    def test_a_spaced_name_is_salvaged_rather_than_failed(self):
        """A model that answers "Thumb Watch" meant a valid slug."""
        name, _, why = self._vet(name="Thumb Watch")
        self.assertIsNone(why)
        self.assertEqual(name, "thumb-watch")

    def test_an_empty_name_falls_back_to_the_title(self):
        name, _, why = self._vet(name="", title="Thumb Watch")
        self.assertIsNone(why)
        self.assertEqual(name, "thumb-watch")

    def test_a_name_with_nothing_salvageable_is_refused(self):
        _, _, why = self._vet(name="!!!", title="???")
        self.assertIsNotNone(why)

    def test_an_unknown_stack_becomes_other_rather_than_failing(self):
        _, stack, why = self._vet(stack="haskell-on-rails")
        self.assertIsNone(why)
        self.assertEqual(stack, "other")

    def test_a_non_empty_existing_directory_is_refused(self):
        d = self.root / "thumbwatch"
        d.mkdir()
        (d / "main.py").write_text("print(1)\n", encoding="utf-8")
        _, _, why = self._vet()
        self.assertIn("already exists", why)

    def test_an_empty_existing_directory_is_allowed(self):
        """mkdir'ing ahead of the tool is a normal thing to have done."""
        (self.root / "thumbwatch").mkdir()
        _, _, why = self._vet()
        self.assertIsNone(why)

    def test_an_existing_hub_note_is_refused(self):
        (scaffold.PROJECTS / "thumbwatch.md").write_text(
            "---\ntype: project\n---\n# Thumbwatch\n", encoding="utf-8")
        _, _, why = self._vet()
        self.assertIn("already exists", why)


class Creation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dest = Path(self.tmp.name) / "thumbwatch"

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_writes_the_boring_files_and_inits_git(self):
        made = scaffold.create_repo(self.dest, "thumbwatch", PLAN, "python")
        self.assertTrue((self.dest / "README.md").exists())
        self.assertTrue((self.dest / ".gitignore").exists())
        self.assertTrue((self.dest / ".git").exists())
        self.assertIn("README.md", made)

    def test_it_creates_no_remote(self):
        """The one step that leaves the machine is deliberately not taken."""
        import subprocess
        scaffold.create_repo(self.dest, "thumbwatch", PLAN, "python")
        r = subprocess.run(["git", "-C", str(self.dest), "remote"],
                           capture_output=True, text=True)
        self.assertEqual((r.stdout or "").strip(), "")

    def test_a_readme_without_a_heading_gets_one(self):
        scaffold.create_repo(self.dest, "thumbwatch",
                             {**PLAN, "readme": "Just prose, no heading."}, "python")
        body = (self.dest / "README.md").read_text(encoding="utf-8")
        self.assertTrue(body.startswith("# Thumbwatch"))


class HubNote(unittest.TestCase):
    def _hub(self, **over):
        return scaffold.hub_note("thumbwatch", {**PLAN, **over},
                                 Path(r"C:\Users\tusha\Documents\CS Projects\thumbwatch"),
                                 "python")

    def test_it_has_the_sections_devlog_and_mapper_write_into(self):
        """A hub built from 99-Meta/Templates/project.md would have neither, and
        would be invisible to both tools from the day it was created."""
        hub = self._hub()
        self.assertIn("## Dev log", hub)
        self.assertIn("## Decisions", hub)

    def test_the_repo_field_is_the_real_path(self):
        hub = self._hub()
        self.assertIn(r"repo: C:\Users\tusha\Documents\CS Projects\thumbwatch", hub)
        self.assertIn("file:///C:/Users/tusha/Documents/CS%20Projects/thumbwatch", hub)

    def test_tasks_become_real_checkboxes(self):
        hub = self._hub()
        self.assertIn("- [ ] Pick a file-watching library", hub)

    def test_no_tasks_leaves_a_placeholder_not_an_empty_section(self):
        hub = self._hub(tasks=[])
        self.assertIn("## Tasks\n- \n", hub)

    def test_the_frontmatter_matches_the_project_schema(self):
        from sigma import frontmatter
        fm = frontmatter(self._hub())
        for key in ("type", "area", "status", "started", "due", "repo", "tags"):
            self.assertIn(key, fm, key)
        self.assertEqual(fm["type"], "project")
        self.assertEqual(fm["status"], "active")


class EndToEnd(unittest.TestCase):
    """The repo, the proposal and the applied hub note in one pass.

    Everything above tests a piece; this tests that the pieces are wired to each
    other. Run against a throwaway vault and a throwaway code root rather than
    against Zach's — a feature whose test suite creates real projects in
    `CS Projects\\` is a feature that litters.
    """

    def setUp(self):
        import applier
        import reflect as rf
        from sigma import gitops, ledger
        self.applier, self.rf, self.gitops, self.ledger = applier, rf, gitops, ledger

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.code = root / "CS Projects"
        self.code.mkdir()
        self.vault = root / "vault"
        (self.vault / "06-System" / "proposals").mkdir(parents=True)
        (self.vault / "03-Projects").mkdir()
        (self.vault / "CLAUDE.md").write_text("# contract\n", encoding="utf-8")
        (self.vault / ".gitignore").write_text("nothing/\n", encoding="utf-8")
        for a in (("init", "-b", "master"), ("config", "user.name", "T"),
                  ("config", "user.email", "t@e.com"), ("add", "-A"),
                  ("commit", "-m", "seed")):
            import subprocess
            subprocess.run(["git", "-C", str(self.vault), *a],
                           capture_output=True, text=True)

        self._saved = (scaffold.VAULT, scaffold.PROJECTS, rf.VAULT, rf.PROPOSALS,
                       rf.CONTRACT, rf.STAGED, gitops.MUTEX_PATH,
                       ledger.LEDGER_PATH, applier.log, scaffold.log)
        scaffold.VAULT = rf.VAULT = self.vault
        scaffold.PROJECTS = self.vault / "03-Projects"
        rf.PROPOSALS = self.vault / "06-System" / "proposals"
        rf.CONTRACT = self.vault / "CLAUDE.md"
        rf.STAGED = self.vault / "06-System" / "proposed"
        gitops.MUTEX_PATH = root / "git.lock"
        ledger.LEDGER_PATH = root / "ledger.jsonl"
        applier.log = scaffold.log = lambda msg: None

    def tearDown(self):
        (scaffold.VAULT, scaffold.PROJECTS, self.rf.VAULT, self.rf.PROPOSALS,
         self.rf.CONTRACT, self.rf.STAGED, self.gitops.MUTEX_PATH,
         self.ledger.LEDGER_PATH, self.applier.log, scaffold.log) = self._saved
        self.tmp.cleanup()

    def test_repo_and_hub_note_both_land(self):
        name, stack, why = scaffold.vet(PLAN, self.code)
        self.assertIsNone(why)
        dest = self.code / name

        made = scaffold.create_repo(dest, name, PLAN, stack)
        self.assertIn("first commit", made)

        prop = scaffold.propose_hub(name, scaffold.hub_note(name, PLAN, dest, stack),
                                    "a folder watcher")
        done = self.applier.apply_one(prop, actor="scaffold")

        self.assertEqual(done["action"], "create")
        self.assertEqual(done["target"], "03-Projects/thumbwatch.md")
        hub = (self.vault / "03-Projects" / "thumbwatch.md").read_text(encoding="utf-8")
        self.assertIn("## Dev log", hub)
        self.assertIn(str(dest), hub)
        # A real commit, so the ledger's one-click undo has something to revert.
        self.assertTrue(done["sha"])

    def test_the_scaffolded_hub_is_immediately_visible_to_devlog(self):
        """The integration that makes this worth having: a project scaffolded
        today should be dev-loggable from its first commit, with no hand-editing
        of the hub in between."""
        import devlog
        name, stack, _ = scaffold.vet(PLAN, self.code)
        dest = self.code / name
        scaffold.create_repo(dest, name, PLAN, stack)
        hub = scaffold.hub_note(name, PLAN, dest, stack)

        saved = devlog.VAULT
        devlog.VAULT = self.vault
        try:
            # No entries yet, so devlog would cover the whole history...
            self.assertEqual(devlog.last_logged(hub), (None, None))
            # ...and its entry has somewhere to go.
            entry = "- **2026-07-31** — Scaffolded. _(through `abc1234`)_"
            spliced = devlog.splice(hub, entry)
            self.assertIsNotNone(spliced)
            self.assertIn(entry, devlog.dev_log_section(spliced))
        finally:
            devlog.VAULT = saved


if __name__ == "__main__":
    unittest.main()
