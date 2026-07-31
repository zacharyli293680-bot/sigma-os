"""
The backup check (doctor.check_backup).

This check exists because on 2026-07-31 the vault went 19 hours without a
successful push and every other check stayed green throughout. So the thing
worth testing is not the happy path — it is that the check actually goes red
when the remote cannot be reached, and that it does not cry wolf over a backlog
obsidian-git is simply about to clear.
"""
import datetime
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import doctor                                    # noqa: E402
import session_logger as sl                      # noqa: E402

ALERT, TODO, INFO, OK = doctor.ALERT, doctor.TODO, doctor.INFO, doctor.OK


def _run(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", env=env)


class BackupBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        _run(self.vault, "init", "-b", "master")
        _run(self.vault, "config", "user.name", "T")
        _run(self.vault, "config", "user.email", "t@e.com")
        (self.vault / "a.md").write_text("one\n", encoding="utf-8")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "seed")

        self._saved = sl.DEFAULT_VAULT
        sl.DEFAULT_VAULT = self.vault

    def tearDown(self):
        sl.DEFAULT_VAULT = self._saved
        self.tmp.cleanup()

    def add_remote(self):
        """A real, reachable remote: a bare repo on disk."""
        self.origin = self.root / "origin.git"
        _run(self.root, "init", "--bare", str(self.origin))
        _run(self.vault, "remote", "add", "origin", str(self.origin))
        _run(self.vault, "push", "-u", "origin", "master")

    def commit(self, name: str, days_ago: int = 0):
        (self.vault / name).write_text("x\n", encoding="utf-8")
        _run(self.vault, "add", "-A")
        when = (datetime.datetime.now()
                - datetime.timedelta(days=days_ago)).isoformat()
        env = {**__import__("os").environ,
               "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        _run(self.vault, "commit", "-m", f"add {name}", env=env)

    def levels(self):
        out = []
        doctor.check_backup(out)
        return out


class TestBackupCheck(BackupBase):
    def test_no_remote_is_stated_not_alerted(self):
        """Having no remote is a configuration choice, not a fault."""
        found = self.levels()
        self.assertTrue(any(lvl == INFO and "no git remote" in msg
                            for lvl, msg, _ in found), found)

    def test_everything_pushed_is_ok(self):
        self.add_remote()
        found = self.levels()
        self.assertTrue(any(lvl == OK and "vault is pushed" in msg
                            for lvl, msg, _ in found), found)

    def test_a_fresh_backlog_does_not_cry_wolf(self):
        """obsidian-git pushes every 30 minutes; a just-made commit is normal."""
        self.add_remote()
        self.commit("b.md")
        found = self.levels()
        self.assertTrue(any(lvl == OK and "not yet pushed" in msg
                            for lvl, msg, _ in found), found)

    def test_a_stale_backlog_with_a_reachable_remote_is_a_todo(self):
        """Pushing is possible but is not happening — worth saying, not alarming."""
        self.add_remote()
        self.commit("b.md", days_ago=2)
        found = self.levels()
        self.assertTrue(any(lvl == TODO and "remote is reachable" in msg
                            for lvl, msg, _ in found), found)

    def test_a_stale_backlog_with_an_unreachable_remote_alerts(self):
        """The 2026-07-31 case, which nothing caught."""
        self.add_remote()
        self.commit("b.md", days_ago=2)
        _run(self.vault, "remote", "set-url", "origin",
             str(self.root / "gone.git"))
        found = self.levels()
        self.assertTrue(any(lvl == ALERT and "cannot be reached" in msg
                            for lvl, msg, _ in found), found)
        self.assertTrue(any(lvl == ALERT and "nothing is backed up" in msg
                            for lvl, msg, _ in found), found)

    def test_no_upstream_is_reported(self):
        self.origin = self.root / "origin.git"
        _run(self.root, "init", "--bare", str(self.origin))
        _run(self.vault, "remote", "add", "origin", str(self.origin))
        found = self.levels()          # remote exists, but nothing tracks it
        self.assertTrue(any(lvl == TODO and "no upstream" in msg
                            for lvl, msg, _ in found), found)


if __name__ == "__main__":
    unittest.main()
