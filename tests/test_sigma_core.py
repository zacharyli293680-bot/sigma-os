"""
Unit tests for the Phase 4 core: gitops, ledger, spend.

Run from the repo root:
    interface\\backend\\.venv\\Scripts\\python.exe -m unittest discover tests -v

Everything runs against throwaway temp dirs — no test touches the vault, the
real ledger, or the real mutex (the modules read their paths at call time so
the tests can re-point them).
"""
import datetime
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import sigma  # noqa: E402
from sigma import gitops, ledger, spend  # noqa: E402


def _run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8")


def make_repo(tmp) -> Path:
    repo = Path(tmp) / "vault"
    repo.mkdir()
    _run(repo, "init", "-b", "master")
    _run(repo, "config", "user.name", "Test")
    _run(repo, "config", "user.email", "test@example.com")
    (repo / "note.md").write_text("# note\n\n- [ ] a task\n", encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-m", "seed")
    return repo


class TestGitops(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)
        self._old_mutex = gitops.MUTEX_PATH
        gitops.MUTEX_PATH = Path(self.tmp.name) / "git.lock"

    def tearDown(self):
        gitops.MUTEX_PATH = self._old_mutex
        self.tmp.cleanup()

    def test_commit_returns_real_sha(self):
        (self.repo / "new.md").write_text("hello\n", encoding="utf-8")
        with gitops.vault_write(self.repo) as w:
            out = w.commit("new.md", "sigma(test): create new.md")
        self.assertIsNotNone(out["sha"])
        self.assertFalse(out["absorbed"])
        self.assertEqual(out["sha"], gitops.head(self.repo))
        show = _run(self.repo, "show", "--stat", "--format=%s", out["sha"]).stdout
        self.assertIn("sigma(test): create new.md", show)
        self.assertIn("new.md", show)

    def test_commit_scopes_to_named_paths_only(self):
        (self.repo / "a.md").write_text("a\n", encoding="utf-8")
        (self.repo / "b.md").write_text("b\n", encoding="utf-8")
        with gitops.vault_write(self.repo) as w:
            out = w.commit("a.md", "sigma(test): only a")
        files = _run(self.repo, "show", "--name-only", "--format=", out["sha"]).stdout.split()
        self.assertEqual(files, ["a.md"])       # b.md stays uncommitted
        self.assertIn("b.md", _run(self.repo, "status", "--short").stdout)

    def test_absorbed_change_reports_the_absorbing_commit(self):
        # an interleaved commit (obsidian-git's backup) already took the change
        (self.repo / "note.md").write_text("# note\nedited\n", encoding="utf-8")
        _run(self.repo, "add", "-A")
        _run(self.repo, "commit", "-m", "vault backup: interleaved")
        backup_sha = gitops.head(self.repo)
        with gitops.vault_write(self.repo) as w:
            out = w.commit("note.md", "sigma(test): edit note.md")
        self.assertTrue(out["absorbed"])
        self.assertEqual(out["sha"], backup_sha)

    def test_gitignored_path_never_commits(self):
        (self.repo / ".gitignore").write_text("sealed/\n", encoding="utf-8")
        _run(self.repo, "add", "-A")
        _run(self.repo, "commit", "-m", "add gitignore")
        sealed = self.repo / "sealed"
        sealed.mkdir()
        (sealed / "x.md").write_text("private\n", encoding="utf-8")
        with gitops.vault_write(self.repo) as w:
            out = w.commit("sealed/x.md", "sigma(test): should not commit")
        self.assertIsNone(out["sha"])
        self.assertIn("gitignored", out["note"])
        self.assertNotIn("sealed", _run(self.repo, "log", "--name-only").stdout)

    def test_revert_undoes_exactly_one_commit(self):
        (self.repo / "note.md").write_text("# note\n\n- [x] a task\n", encoding="utf-8")
        with gitops.vault_write(self.repo) as w:
            out = w.commit("note.md", "sigma(test): tick")
        r = gitops.revert(self.repo, out["sha"])
        self.assertTrue(r["ok"])
        self.assertIsNotNone(r["sha"])
        self.assertEqual((self.repo / "note.md").read_text(encoding="utf-8"),
                         "# note\n\n- [ ] a task\n")

    def test_conflicting_revert_aborts_cleanly(self):
        (self.repo / "note.md").write_text("v2\n", encoding="utf-8")
        with gitops.vault_write(self.repo) as w:
            first = w.commit("note.md", "sigma(test): v2")
        (self.repo / "note.md").write_text("v3 rewritten\n", encoding="utf-8")
        with gitops.vault_write(self.repo) as w:
            w.commit("note.md", "sigma(test): v3")
        r = gitops.revert(self.repo, first["sha"])
        self.assertFalse(r["ok"])
        self.assertIn("conflict", r["note"])
        # the tree is clean afterwards — no half-applied revert left behind
        self.assertEqual(_run(self.repo, "status", "--short").stdout.strip(), "")
        self.assertEqual((self.repo / "note.md").read_text(encoding="utf-8"),
                         "v3 rewritten\n")

    def test_mutex_blocks_then_raises(self):
        gitops.MUTEX_PATH.write_text(json.dumps(
            {"pid": 1, "at": datetime.datetime.now().isoformat(timespec="seconds")}),
            encoding="utf-8")
        with self.assertRaises(gitops.GitBusy):
            with gitops.vault_write(self.repo, timeout_s=0.6):
                pass

    def test_stale_mutex_is_taken_over(self):
        old = (datetime.datetime.now() - datetime.timedelta(hours=1))
        gitops.MUTEX_PATH.write_text(json.dumps(
            {"pid": 1, "at": old.isoformat(timespec="seconds")}), encoding="utf-8")
        (self.repo / "new.md").write_text("x\n", encoding="utf-8")
        with gitops.vault_write(self.repo, timeout_s=2) as w:
            out = w.commit("new.md", "sigma(test): after stale takeover")
        self.assertIsNotNone(out["sha"])


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = ledger.LEDGER_PATH
        ledger.LEDGER_PATH = Path(self.tmp.name) / "ledger.jsonl"

    def tearDown(self):
        ledger.LEDGER_PATH = self._old
        self.tmp.cleanup()

    def test_append_and_read_newest_first(self):
        ledger.record("planner", "create", "01-Daily/x.md", "aaa", "wrote the daily note")
        ledger.record("dashboard", "toggle", "Home.md", "bbb", "ticked a task")
        got = ledger.entries()
        self.assertEqual([e["sha"] for e in got], ["bbb", "aaa"])
        self.assertFalse(got[0]["reverted"])

    def test_revert_marks_its_target(self):
        ledger.record("coach", "update", "t.md", "aaa", "rebalanced")
        ledger.record("dashboard", "revert", "t.md", "ccc", "undid the rebalance",
                      extra={"reverts": "aaa"})
        got = {e["sha"]: e for e in ledger.entries()}
        self.assertTrue(got["aaa"]["reverted"])
        self.assertFalse(got["ccc"]["reverted"])

    def test_torn_tail_line_is_skipped(self):
        ledger.record("planner", "create", "x.md", "aaa", "ok")
        with ledger.LEDGER_PATH.open("a", encoding="utf-8") as f:
            f.write('{"ts": "2026-07-30T09:00:00", "actor": "torn')
        self.assertEqual(len(ledger.entries()), 1)


class TestSpend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = spend.SPEND_PATH
        spend.SPEND_PATH = Path(self.tmp.name) / "spend.jsonl"

    def tearDown(self):
        spend.SPEND_PATH = self._old
        self.tmp.cleanup()

    def _at(self, minutes_ago, **kw):
        e = spend.record_spend(**kw)
        # rewrite the stamp: record_spend stamps "now", the test needs history
        lines = spend.SPEND_PATH.read_text(encoding="utf-8").splitlines()
        e["ts"] = (datetime.datetime.now()
                   - datetime.timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        lines[-1] = json.dumps(e)
        spend.SPEND_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_window_counts_only_inside_the_window(self):
        self._at(10, actor="planner", model="sonnet", cost_usd=0.12)
        self._at(20, actor="ask", model="sonnet", cost_usd=0.05)
        self._at(60 * 6, actor="ancient", model="haiku", cost_usd=9.99)  # outside
        w = spend.window()
        self.assertEqual(w["calls"], 2)
        self.assertAlmostEqual(w["cost_usd"], 0.17)
        self.assertNotIn("ancient", w["by_actor"])

    def test_unknown_costs_stay_unknown(self):
        self._at(5, actor="reflect", model="sonnet")     # claude -p: no cost signal
        self.assertIsNone(spend.window()["cost_usd"])

    def test_rate_limit_drives_degrade_and_resume(self):
        self._at(200, actor="planner", model="sonnet", cost_usd=0.2)
        self._at(30, actor="coach", model="sonnet", rate_limited=True)
        w = spend.window()
        self.assertIsNotNone(w["last_rate_limit"])
        self.assertTrue(spend.rate_limited_within(60, w))
        self.assertFalse(spend.rate_limited_within(10, w))
        est = spend.resume_estimate(w)
        self.assertIsNotNone(est)
        # the estimate is the oldest in-window call ageing out: ~5h - 200min ahead
        delta = datetime.datetime.fromisoformat(est) - datetime.datetime.now()
        self.assertGreater(delta.total_seconds(), 0)
        self.assertLess(delta.total_seconds(), 5 * 3600)


if __name__ == "__main__":
    unittest.main()


class WriteNote(unittest.TestCase):
    """`write_text` translates newlines on Windows, so every script write of a
    vault note silently rewrote the whole file. The first dev log entry landed as
    35 insertions and 35 deletions on a 35-line note — nothing lost, but the
    diff, the staged-change review and the ledger's one-click undo all stop
    meaning anything when every line changes every time."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = Path(self.tmp.name) / "note.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_lf_note_stays_lf(self):
        self.p.write_bytes(b"a\nb\nc\n")
        sigma.write_note(self.p, "a\nb\nc\nd\n")
        self.assertEqual(self.p.read_bytes(), b"a\nb\nc\nd\n")

    def test_a_crlf_note_stays_crlf(self):
        self.p.write_bytes(b"a\r\nb\r\n")
        sigma.write_note(self.p, "a\nb\nc\n")
        self.assertEqual(self.p.read_bytes(), b"a\r\nb\r\nc\r\n")

    def test_a_new_note_defaults_to_lf(self):
        sigma.write_note(self.p, "a\nb\n")
        self.assertEqual(self.p.read_bytes(), b"a\nb\n")

    def test_crlf_in_the_content_does_not_double_up(self):
        """Content assembled from a universal-newline read can carry either."""
        self.p.write_bytes(b"a\r\n")
        sigma.write_note(self.p, "a\r\nb\n")
        self.assertEqual(self.p.read_bytes(), b"a\r\nb\r\n")
        self.assertNotIn(b"\r\r", self.p.read_bytes())

    def test_a_mixed_note_settles_on_its_majority(self):
        self.p.write_bytes(b"a\r\nb\r\nc\r\nd\n")
        sigma.write_note(self.p, "a\nb\n")
        self.assertEqual(self.p.read_bytes(), b"a\r\nb\r\n")
