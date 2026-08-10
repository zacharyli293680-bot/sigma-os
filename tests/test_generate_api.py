"""
The S7 API surface: POST /api/guide/generate (the palette POST's own policy,
inherited — whitelisted course, window hold, single job slot) and the
blueprint-aware GET /api/guide/{course} payload.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

from fastapi.testclient import TestClient   # noqa: E402

import app as appmod                        # noqa: E402
import commands                             # noqa: E402
import panels                               # noqa: E402
import privacy                              # noqa: E402


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a],
                          capture_output=True, text=True)


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")
        self._saved = (appmod.VAULT, panels.VAULT, commands._window_hold,
                       commands._job, commands._run)
        appmod.VAULT = self.vault
        panels.VAULT = self.vault
        commands._window_hold = lambda: None
        commands._job = None

        async def fake_run(spec):
            self.launched = spec
            commands._job.update(state="done", exit=0)
        commands._run = fake_run
        self.launched = None
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.client = TestClient(appmod.app)

    def tearDown(self):
        (appmod.VAULT, panels.VAULT, commands._window_hold,
         commands._job, commands._run) = self._saved
        panels._cache.clear()
        self.tmp.cleanup()


class TestGenerate(ApiBase):
    def test_an_unknown_course_is_a_404(self):
        r = self.client.post("/api/guide/generate", json={"course": "NOPE-1"})
        self.assertEqual(r.status_code, 404)
        self.assertIsNone(self.launched)

    def test_the_discovered_name_rides_the_argv_not_the_request_string(self):
        r = self.client.post("/api/guide/generate", json={"course": "test-101"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["started"], "TEST-101")
        self.assertIn("TEST-101", self.launched["argv"])
        self.assertNotIn("test-101", self.launched["argv"])
        self.assertTrue(self.launched.get("model"))

    def test_the_window_hold_refuses_with_409(self):
        commands._window_hold = lambda: "reserved for the 09:00 fleet run"
        r = self.client.post("/api/guide/generate", json={"course": "TEST-101"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "window")
        self.assertIsNone(self.launched)

    def test_a_running_job_refuses_with_409(self):
        commands._job = {"verb": "fleet-run", "state": "running", "lines": []}
        r = self.client.post("/api/guide/generate", json={"course": "TEST-101"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "busy")
        self.assertEqual(r.json()["running"], "fleet-run")

    def test_a_traversal_shaped_course_never_reaches_an_argv(self):
        r = self.client.post("/api/guide/generate",
                             json={"course": "../../etc/passwd"})
        self.assertEqual(r.status_code, 404)
        self.assertIsNone(self.launched)

    def test_guide_status_is_a_palette_verb(self):
        self.assertIn("guide-status", commands.VERBS)
        self.assertFalse(commands.VERBS["guide-status"].get("model"))


class TestGuideRead(ApiBase):
    def test_a_blueprint_only_course_answers_with_a_chainless_payload(self):
        (self.course / "test-101-guide-blueprint.md").write_text(
            "---\ntype: guide-blueprint\ncourse: TEST-101\nstatus: draft\n"
            "tags: [guide]\n---\n\n## Modules\n\n- M01 · Topic ⏱ 40\n"
            "    - source:: 02-Areas/Academics/TEST-101/lectures/l1.md\n",
            encoding="utf-8")
        r = self.client.get("/api/guide/TEST-101")
        self.assertEqual(r.status_code, 200, r.text)
        g = r.json()
        self.assertIsNone(g["file"])
        self.assertEqual(g["blueprint"], "draft")
        self.assertEqual((g["planned"], g["missing"]), (1, 1))

    def test_a_course_with_neither_note_still_404s(self):
        r = self.client.get("/api/guide/TEST-101")
        self.assertEqual(r.status_code, 404)

    def test_the_progress_route_registers_before_the_course_route(self):
        """/api/guide/progress must resolve as the SSE stream, never be
        captured as course "progress". Both live in the panels router and
        FastAPI matches in registration order, so the literal path has to
        precede the parameterised one — asserted on the router's own table,
        because actually opening the infinite stream under TestClient never
        returns (and app.routes hides included routers behind lazy wrappers)."""
        paths = [r.path for r in panels.router.routes]
        self.assertIn("/api/guide/progress", paths)
        self.assertLess(paths.index("/api/guide/progress"),
                        paths.index("/api/guide/{course}"))


if __name__ == "__main__":
    unittest.main()
