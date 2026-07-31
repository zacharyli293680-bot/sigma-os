"""
The Phase 5 boundary: what is hidden, what is shown-and-marked, and what is
neither. Adversarial rather than happy-path, per dashboard-plan §11 — the lane
was always "one bug from being the leak it exists to prevent", and opening the
model boundary did not retire that risk, it moved it.

The marker makes a claim about confidentiality ("you may keep client material
here, it cannot leave"). So the tests that matter most are the ones asserting
it is *absent*: on tracked files, on a misconfigured exemption, and whenever git
declines to answer.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import panels                                  # noqa: E402
import privacy                                 # noqa: E402

TASK = "- [ ] a task 📅 2026-07-30\n"


def _run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8")


class NoSyncBase(unittest.TestCase):
    """A throwaway vault with all three kinds of path:

        01-Daily/           tracked, syncs
        Client/             gitignored AND exempted  -> shown, marked
        Secret/             gitignored, NOT exempted -> hidden entirely
    """

    exempt = ["Client/", "03-Projects/client-proj.md"]

    def setUp(self):
        # ignore_cleanup_errors: git leaves read-only objects under .git on
        # Windows, and a fixture that cannot delete itself must not fail a run.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        for d in ("01-Daily", "Client", "Secret", "03-Projects"):
            (self.vault / d).mkdir(parents=True)
        (self.vault / "01-Daily" / "2026-07-30.md").write_text(
            "---\ntype: daily\n---\n\n" + TASK, encoding="utf-8")
        (self.vault / "Client" / "work.md").write_text(
            "---\ntype: resource\n---\n\n" + TASK, encoding="utf-8")
        (self.vault / "Secret" / "hidden.md").write_text(
            "---\ntype: resource\n---\n\n" + TASK, encoding="utf-8")
        (self.vault / "03-Projects" / "open-proj.md").write_text(
            "---\ntype: project\nstatus: active\n---\n", encoding="utf-8")
        (self.vault / "03-Projects" / "client-proj.md").write_text(
            "---\ntype: project\nstatus: active\n---\n", encoding="utf-8")
        (self.vault / ".gitignore").write_text(
            "Client/\nSecret/\n03-Projects/client-proj.md\n", encoding="utf-8")
        _run(self.vault, "init", "-b", "master")
        _run(self.vault, "config", "user.name", "T")
        _run(self.vault, "config", "user.email", "t@e.com")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "seed")

        # Point both modules at the fixture, and give privacy.py a config dir
        # of its own. Both accessors are lru_cached, so they must be cleared.
        self._saved = (panels.VAULT, privacy._RUNTIME)
        panels.VAULT = self.vault
        privacy._RUNTIME = self.root
        (self.root / "privacy.config.json").write_text(
            json.dumps({"model_allow": self.exempt}), encoding="utf-8")
        self._bust()

    def _bust(self):
        privacy.model_allow_raw.cache_clear()
        privacy.model_allow_prefixes.cache_clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        panels._cache.clear()

    def tearDown(self):
        (panels.VAULT, privacy._RUNTIME) = self._saved
        self._bust()
        self.tmp.cleanup()


class TestNoSyncBoundary(NoSyncBase):
    def test_exempt_is_shown_and_marked_everywhere(self):
        """One fact, three surfaces — they must not disagree."""
        tasks = panels._scan_tasks()["tasks"]
        client = [t for t in tasks if t["file"].startswith("Client/")]
        self.assertEqual(len(client), 1, tasks)
        self.assertTrue(client[0]["no_sync"])

        projects = {p["name"]: p for p in panels._scan_projects()["projects"]}
        self.assertIn("client-proj", projects)
        self.assertTrue(projects["client-proj"]["no_sync"])

        nodes = {n["id"]: n for n in panels._build_graph()["nodes"]}
        self.assertIn("Client/work.md", nodes)
        self.assertTrue(nodes["Client/work.md"]["no_sync"])

        audit = panels._scan_nosync()
        self.assertTrue(audit["ok"])
        self.assertIn("Client/work.md", [f["path"] for f in audit["files"]])

    def test_tracked_material_is_never_marked(self):
        """A false mark is the one error here that could cause a breach."""
        tasks = panels._scan_tasks()["tasks"]
        daily = [t for t in tasks if t["file"].startswith("01-Daily/")]
        self.assertEqual(len(daily), 1)
        self.assertFalse(daily[0]["no_sync"])

        projects = {p["name"]: p for p in panels._scan_projects()["projects"]}
        self.assertFalse(projects["open-proj"]["no_sync"])

        nodes = {n["id"]: n for n in panels._build_graph()["nodes"]}
        self.assertFalse(nodes["01-Daily/2026-07-30.md"]["no_sync"])

        paths = [f["path"] for f in panels._scan_nosync()["files"]]
        self.assertNotIn("01-Daily/2026-07-30.md", paths)

    def test_gitignored_but_unlisted_appears_nowhere(self):
        """The fail-closed default, unchanged by Phase 5: sealed means gone,
        not shown-with-a-badge. This is the leak test."""
        for path in [t["file"] for t in panels._scan_tasks()["tasks"]]:
            self.assertFalse(path.startswith("Secret/"), path)
        self.assertNotIn("Secret/hidden.md",
                         [n["id"] for n in panels._build_graph()["nodes"]])
        audit = panels._scan_nosync()
        for f in audit["files"]:
            self.assertFalse(f["path"].startswith("Secret/"), f)

    def test_audit_groups_use_the_operator_s_own_spelling(self):
        groups = {g["prefix"]: g for g in panels._scan_nosync()["groups"] if g["count"]}
        self.assertIn("Client/", groups)          # not "client"
        self.assertEqual(groups["Client/"]["count"], 1)

    def test_no_unlisted_bucket_in_a_correct_scan(self):
        """Membership in no_sync *is* prefix membership, so the "(unlisted)"
        group must stay empty — if it ever fills, the two have drifted."""
        prefixes = [g["prefix"] for g in panels._scan_nosync()["groups"]]
        self.assertNotIn("(unlisted)", prefixes)


class TestMarkRequiresTwoYeses(NoSyncBase):
    """A path the operator exempted but git does *not* ignore. The exemption
    list is the "second declaration that can drift" the design once refused, so
    a stale entry in it must not be able to invent a confidentiality claim."""

    exempt = ["Client/", "01-Daily/"]      # 01-Daily is tracked — wrongly listed

    def test_listed_but_tracked_is_not_marked(self):
        tasks = panels._scan_tasks()["tasks"]
        daily = [t for t in tasks if t["file"].startswith("01-Daily/")]
        self.assertEqual(len(daily), 1)
        self.assertFalse(daily[0]["no_sync"],
                         "a tracked file was marked 'never leaves this machine'")
        self.assertNotIn("01-Daily/2026-07-30.md",
                         [f["path"] for f in panels._scan_nosync()["files"]])


class TestGitSilence(NoSyncBase):
    """git cannot answer. Hiding fails on; marking fails off."""

    def setUp(self):
        super().setUp()
        self.orig = privacy.subprocess.run

        def boom(*a, **k):
            raise OSError("git is not available")
        privacy.subprocess.run = boom
        panels._cache.clear()

    def tearDown(self):
        privacy.subprocess.run = self.orig
        super().tearDown()

    def test_marking_fails_off_and_says_so(self):
        audit = panels._scan_nosync()
        self.assertFalse(audit["ok"], "an unverified boundary reported itself verified")
        self.assertEqual(audit["total"], 0)
        self.assertEqual(audit["files"], [])

    def test_hiding_fails_on(self):
        """Everything non-exempt vanishes rather than leaking — the pre-existing
        guarantee, re-asserted because Phase 5 touched this code path.

        Note what this leaves on screen: an unanswered git seals *tracked*
        material too, so the only rows that survive are the exempt ones. The
        dashboard briefly shows the carve-out and nothing else. That is
        pre-existing Option B behaviour rather than anything Phase 5 introduced,
        it self-heals on the next 60s refetch, and it errs toward hiding — but
        it is surprising enough to be worth a test that states it out loud."""
        files = {t["file"] for t in panels._scan_tasks()["tasks"]}
        self.assertNotIn("01-Daily/2026-07-30.md", files)   # tracked: sealed
        self.assertNotIn("Secret/hidden.md", files)         # unlisted: sealed
        self.assertEqual(files, {"Client/work.md"})         # exempt: survives

    def test_survivors_are_shown_but_never_marked(self):
        """The pairing that matters: material still visible during a git
        outage carries no mark, because the claim behind the mark is exactly
        what could not be checked."""
        tasks = panels._scan_tasks()["tasks"]
        self.assertTrue(tasks)
        for t in tasks:
            self.assertFalse(t["no_sync"], t["file"])

    def test_nothing_is_marked_when_nothing_is_verified(self):
        sealed, no_sync, answered = privacy.gitignore_scan(self.vault, ["Client/work.md"])
        self.assertFalse(answered)
        self.assertEqual(no_sync, set())
        self.assertNotIn("Client/work.md", sealed)   # exempt: still readable


class TestBrokenConfig(NoSyncBase):
    def test_unreadable_config_yields_no_exemptions_and_no_marks(self):
        (self.root / "privacy.config.json").write_text("{ not json", encoding="utf-8")
        self._bust()
        self.assertEqual(privacy.model_allow_prefixes(), ())
        # Client/ is now merely gitignored, so it is sealed — hidden, unmarked.
        audit = panels._scan_nosync()
        self.assertEqual(audit["total"], 0)
        for path in [t["file"] for t in panels._scan_tasks()["tasks"]]:
            self.assertFalse(path.startswith("Client/"), path)


if __name__ == "__main__":
    unittest.main()
