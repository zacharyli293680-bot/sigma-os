"""
Endpoint tests for writes.py — toggle, activity, revert — over a throwaway
vault repo. The FastAPI TestClient drives the real router; git underneath is
real; only the vault paths are re-pointed.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

from fastapi import FastAPI                    # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402

import writes                                  # noqa: E402
from sigma import gitops, ledger               # noqa: E402


def _run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8")


NOTE = ("---\ntype: daily\ntags: [daily]\n---\n\n"
        "- [ ] first task 📅 2026-07-30\n"
        "- [ ] second task 📅 2026-08-01\n"
        "- [x] done already 📅 2026-07-28\n")


class TestWritesApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        (self.vault / "01-Daily").mkdir(parents=True)
        (self.vault / "01-Daily" / "2026-07-30.md").write_text(NOTE, encoding="utf-8")
        (self.vault / ".gitignore").write_text("sealed/\n", encoding="utf-8")
        (self.vault / "sealed").mkdir()
        (self.vault / "sealed" / "s.md").write_text("- [ ] hidden 📅 2026-07-30\n",
                                                    encoding="utf-8")
        _run(self.vault, "init", "-b", "master")
        _run(self.vault, "config", "user.name", "Test")
        _run(self.vault, "config", "user.email", "t@example.com")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "seed")

        self._saved = (writes.VAULT, gitops.MUTEX_PATH, ledger.LEDGER_PATH)
        writes.VAULT = self.vault
        gitops.MUTEX_PATH = root / "git.lock"
        ledger.LEDGER_PATH = root / "ledger.jsonl"

        app = FastAPI()
        app.include_router(writes.router)
        self.client = TestClient(app)

    def tearDown(self):
        (writes.VAULT, gitops.MUTEX_PATH, ledger.LEDGER_PATH) = self._saved
        self.tmp.cleanup()

    def toggle(self, **over):
        body = {"file": "01-Daily/2026-07-30.md", "line": 6,
                "raw": "- [ ] first task 📅 2026-07-30", "done": True}
        body.update(over)
        return self.client.post("/api/tasks/toggle", json=body)

    def test_toggle_ticks_commits_and_ledgers(self):
        r = self.toggle()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIsNotNone(body["sha"])
        text = (self.vault / "01-Daily" / "2026-07-30.md").read_text(encoding="utf-8")
        self.assertIn("- [x] first task", text)
        self.assertIn("- [ ] second task", text)      # only the named line moved
        subject = _run(self.vault, "log", "-1", "--format=%s").stdout
        self.assertIn("zach (dashboard): tick", subject)
        led = self.client.get("/api/activity").json()["entries"]
        self.assertEqual(led[0]["actor"], "zach")
        self.assertEqual(led[0]["action"], "toggle")

    def test_stale_line_is_refused(self):
        r = self.toggle(raw="- [ ] first task 📅 2026-07-30 (edited elsewhere)")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "stale")
        self.assertIn("- [ ] first task",
                      (self.vault / "01-Daily" / "2026-07-30.md")
                      .read_text(encoding="utf-8"))

    def test_wrong_line_number_is_stale(self):
        self.assertEqual(self.toggle(line=99).status_code, 409)

    def test_escaping_and_ads_paths_are_refused(self):
        self.assertEqual(self.toggle(file="../outside.md").status_code, 400)
        self.assertEqual(
            self.toggle(file="01-Daily/2026-07-30.md::$DATA").status_code, 400)

    def test_sealed_path_is_refused(self):
        r = self.toggle(file="sealed/s.md", line=1,
                        raw="- [ ] hidden 📅 2026-07-30")
        self.assertEqual(r.status_code, 403)

    def test_non_task_line_is_refused(self):
        r = self.toggle(line=1, raw="---")
        self.assertEqual(r.status_code, 400)

    def test_untick_flips_back(self):
        r = self.toggle(line=8, raw="- [x] done already 📅 2026-07-28", done=False)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("- [ ] done already",
                      (self.vault / "01-Daily" / "2026-07-30.md")
                      .read_text(encoding="utf-8"))

    def test_crlf_notes_keep_their_line_endings(self):
        p = self.vault / "01-Daily" / "crlf.md"
        p.write_bytes(NOTE.replace("\n", "\r\n").encode("utf-8"))
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "crlf note")
        r = self.toggle(file="01-Daily/crlf.md")
        self.assertEqual(r.status_code, 200, r.text)
        raw = p.read_bytes().decode("utf-8")
        self.assertIn("- [x] first task 📅 2026-07-30\r\n", raw)
        self.assertNotIn("\n- [ ] second task 📅 2026-08-01\n",
                         raw.replace("\r\n", "␍"))   # no line lost its CR

    def test_revert_from_the_ledger_in_one_call(self):
        sha = self.toggle().json()["sha"]
        r = self.client.post("/api/activity/revert", json={"sha": sha})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("- [ ] first task",
                      (self.vault / "01-Daily" / "2026-07-30.md")
                      .read_text(encoding="utf-8"))
        led = self.client.get("/api/activity").json()["entries"]
        self.assertEqual(led[0]["action"], "revert")
        by_sha = {e["sha"]: e for e in led}
        self.assertTrue(by_sha[sha]["reverted"])

    def test_revert_refuses_unknown_and_double(self):
        r = self.client.post("/api/activity/revert", json={"sha": "0" * 40})
        self.assertEqual(r.status_code, 404)
        sha = self.toggle().json()["sha"]
        self.client.post("/api/activity/revert", json={"sha": sha})
        r = self.client.post("/api/activity/revert", json={"sha": sha})
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
