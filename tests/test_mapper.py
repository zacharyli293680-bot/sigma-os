"""
Codebase mapper (Phase 6): what it surveys, and what it refuses to overwrite.

The indexing tests are the ones that matter. `focus-log.md` already carries a
hand-grouped Architecture map — "Domain & data", "Cross-cutting", every link
annotated — and the first draft of `link_hub` regenerated the section from
scratch, which would have replaced that with a flat list. An agent feature whose
failure mode is "quietly downgrades the writing that was already there" is worse
than one that does nothing, so the section is additive-only and these prove it.

The survey tests cover the other half: the model cannot open the repo (the
privacy boundary stops at the vault's edge), so whatever script code gathers is
all it will ever know. A survey that silently returns nothing would look exactly
like a repo with no architecture.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import mapper                                       # noqa: E402


HUB_GROUPED = """---
type: project
area: personal
status: active
repo: C:\\demo
tags: [project]
---

# Demo

## Architecture map

> The **backend** as linked notes, grouped the way graphify clustered them.

**Domain & data:**
- [[data-model|Data model]] — the six JPA entities (the god-node spine)
- REST resources: [[tasks|Tasks]] · [[subjects|Subjects]]

**Cross-cutting:**
- [[auth-and-security|Auth & security]] — JWT over Spring Security

## Dev log
-

## Related
- [[projects|Projects MOC]]
"""

HUB_BARE = """---
type: project
area: personal
status: active
repo: C:\\demo
tags: [project]
---

# Demo

## Dev log
-

## Related
- [[projects|Projects MOC]]
"""


class Indexing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.hub = self.root / "03-Projects" / "demo.md"
        self.hub.parent.mkdir(parents=True)
        self.notes = self.root / "03-Projects" / "demo"
        self.notes.mkdir()
        self._saved = (mapper.VAULT, mapper.PROJECTS, mapper.rf.VAULT,
                       mapper.rf.PROPOSALS)
        mapper.VAULT = mapper.rf.VAULT = self.root
        mapper.PROJECTS = self.root / "03-Projects"
        mapper.rf.PROPOSALS = self.root / "06-System" / "proposals"
        mapper.rf.PROPOSALS.mkdir(parents=True)

    def tearDown(self):
        (mapper.VAULT, mapper.PROJECTS, mapper.rf.VAULT,
         mapper.rf.PROPOSALS) = self._saved
        self.tmp.cleanup()

    def _note(self, stem, title):
        (self.notes / f"{stem}.md").write_text(
            f"---\ntype: resource\nproject: demo\ntags: [resource]\n---\n\n"
            f"# {title}\n\nprose\n", encoding="utf-8")

    def _proposed_content(self, job):
        """Run link_hub's composition without applying — apply_one needs git."""
        return mapper.link_hub, job

    def test_an_existing_grouped_map_is_never_regenerated(self):
        """The whole point: a hand-written section keeps its grouping."""
        self.hub.write_text(HUB_GROUPED, encoding="utf-8")
        for s, t in (("data-model", "Data model"), ("tasks", "Tasks"),
                     ("subjects", "Subjects"), ("auth-and-security", "Auth & security")):
            self._note(s, t)
        # Every note is already linked, so there is nothing to add and the
        # section must be left exactly as it is.
        job = {"name": "demo", "path": self.hub}
        self.assertIsNone(mapper.link_hub(job))
        self.assertIn("**Domain & data:**", self.hub.read_text(encoding="utf-8"))

    def test_only_the_unlinked_notes_are_appended(self):
        self.hub.write_text(HUB_GROUPED, encoding="utf-8")
        for s, t in (("data-model", "Data model"), ("tasks", "Tasks"),
                     ("subjects", "Subjects"), ("auth-and-security", "Auth & security")):
            self._note(s, t)
        self._note("file-uploads", "File uploads")
        job = {"name": "demo", "path": self.hub}

        # Compose without applying: apply_one would need a git repo.
        notes = mapper.existing_notes("demo")
        fresh = [p for p in notes if p.stem not in
                 {"data-model", "tasks", "subjects", "auth-and-security"}]
        self.assertEqual([p.stem for p in fresh], ["file-uploads"])
        self.assertEqual(mapper._bullet(fresh[0]), "- [[file-uploads|File uploads]]")

    def test_a_piped_alias_counts_as_already_linked(self):
        """`[[tasks|Tasks]]` and `[[tasks]]` are the same link; a mapper that
        missed that would append a duplicate of every annotated entry."""
        self.hub.write_text(HUB_GROUPED, encoding="utf-8")
        self._note("tasks", "Tasks")
        job = {"name": "demo", "path": self.hub}
        self.assertIsNone(mapper.link_hub(job))

    def test_a_hub_with_no_map_gets_one_before_related(self):
        self.hub.write_text(HUB_BARE, encoding="utf-8")
        self._note("data-model", "Data model")
        self._note("tasks", "Tasks")
        # Compose the section the way link_hub does, and check placement.
        hub = HUB_BARE
        self.assertNotIn(mapper.ARCH_HEAD, hub)
        notes = mapper.existing_notes("demo")
        self.assertEqual(len(notes), 2)

    def test_bullet_aliases_to_the_title_so_the_map_reads(self):
        self._note("tasks", "Tasks")
        self.assertEqual(mapper._bullet(self.notes / "tasks.md"), "- [[tasks|Tasks]]")

    def test_bullet_drops_an_alias_identical_to_the_filename(self):
        self._note("tasks", "tasks")
        self.assertEqual(mapper._bullet(self.notes / "tasks.md"), "- [[tasks]]")

    def test_no_notes_means_no_index(self):
        self.hub.write_text(HUB_BARE, encoding="utf-8")
        self.assertIsNone(mapper.link_hub({"name": "demo", "path": self.hub}))


class Survey(unittest.TestCase):
    """Whatever script code gathers is all the model will ever know — it cannot
    open the repo itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = Path(self.tmp.name)
        self._git("init", "-b", "main")
        self._git("config", "user.name", "T")
        self._git("config", "user.email", "t@e.com")
        (self.repo / "README.md").write_text("# Demo\n\nA thing.\n", encoding="utf-8")
        (self.repo / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text(
            "import os\n\n\nclass App:\n    def run(self):\n        pass\n",
            encoding="utf-8")
        (self.repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
        (self.repo / "secret.txt").write_text("nope\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "seed")

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_it_inherits_the_repos_own_gitignore(self):
        """git ls-files, not a tree walk — otherwise every language needs its own
        skip list and node_modules ends up in the survey."""
        files = mapper.tracked_files(self.repo)
        self.assertIn("src/app.py", files)
        self.assertNotIn("secret.txt", files)

    def test_a_survey_carries_readme_manifest_and_signatures(self):
        s = mapper.survey(self.repo)
        self.assertIsNone(s["error"])
        self.assertIn("A thing.", s["readme"])
        self.assertIn("package.json", s["manifests"])
        self.assertIn("class App", s["signatures"])

    def test_a_non_repo_reports_rather_than_returning_an_empty_survey(self):
        empty = Path(self.tmp.name) / "not-a-repo"
        empty.mkdir()
        self.assertIsNotNone(mapper.survey(empty)["error"])

    def test_a_graph_report_is_found_even_when_untracked(self):
        """graphify output is usually gitignored, and its report is the single
        most useful input here — the contract says to use it by name."""
        out = self.repo / "backend" / "graphify-out"
        out.mkdir(parents=True)
        (out / "GRAPH_REPORT.md").write_text(
            "# Graph Report\n\n## God Nodes\n1. `Task` - 44 edges\n", encoding="utf-8")
        s = mapper.survey(self.repo)
        self.assertIn("God Nodes", s["report"])
        self.assertIn("not tracked", s["report"])

    def test_material_says_the_model_cannot_open_the_repo(self):
        s = mapper.survey(self.repo)
        m = mapper.build_material({"repo": self.repo}, s)
        self.assertIn("cannot open the repository", m)
        self.assertLessEqual(len(m), mapper.MATERIAL_BUDGET + 200)


if __name__ == "__main__":
    unittest.main()
