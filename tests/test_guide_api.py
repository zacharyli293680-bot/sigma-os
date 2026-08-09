#!/usr/bin/env python3
"""
GET /api/guide/{course} and GET /api/courses — study S2's read surface.

The claim this suite pins: the chain endpoint serves every row a chain note
holds — `[-]` included, which todo's scanner deliberately cannot see — with
the frontier, the progress counts and the blueprint status read mechanically;
and the courses list gives every active course its guide summary without
leaking the full rows into what should be a glance.
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

from fastapi import FastAPI                  # noqa: E402
from fastapi.testclient import TestClient    # noqa: E402
import panels                                # noqa: E402
import privacy                               # noqa: E402
from test_lesson import _valid               # noqa: E402 — the conforming module


def _git(cwd: Path, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


CHAIN = (
    "---\ntype: guide\ncourse: TEST-101\ntags: [guide]\n---\n\n"
    "## Modules\n\n"
    "- [ ] M01 · [[test-101-m01-test-module|Test module]]\n"
    "- [ ] M02 · [[test-101-m02-later|Later]]\n"
)

BLUEPRINT = (
    "---\ntype: guide-blueprint\ncourse: TEST-101\nstatus: draft\n"
    "tags: [guide]\n---\n\n# Blueprint\n"
)


class GuideApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.course / "lectures").mkdir()
        for n in (1, 2, 3):
            (self.course / "lectures" / f"l{n}.md").write_text(
                "# src\n", encoding="utf-8")
        (self.course / "guide" / "test-101-m01-test-module.md").write_text(
            _valid(), encoding="utf-8")
        (self.course / "test-101-guide.md").write_text(CHAIN, encoding="utf-8")
        (self.course / "test-101-guide-blueprint.md").write_text(
            BLUEPRINT, encoding="utf-8")
        other = self.vault / "02-Areas" / "Academics" / "TEST-102"
        other.mkdir(parents=True)
        (other / "test-102-timeline.md").write_text(
            "---\ntype: resource\ncourse: TEST-102\nsource:\ntags: [resource]\n"
            "---\n\n- [ ] first\n", encoding="utf-8")
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")

        self._saved = panels.VAULT
        panels.VAULT = self.vault
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()

        app = FastAPI()
        app.include_router(panels.router)
        self.client = TestClient(app)

    def tearDown(self):
        panels.VAULT = self._saved
        panels._cache.clear()
        self.tmp.cleanup()


class TestGuide(GuideApiBase):
    def test_the_chain_arrives_with_frontier_counts_and_blueprint(self):
        r = self.client.get("/api/guide/test-101")
        self.assertEqual(r.status_code, 200, r.text)
        g = r.json()
        self.assertEqual(g["course"], "TEST-101")
        self.assertTrue(g["file"].endswith("test-101-guide.md"))
        self.assertEqual(g["total"], 2)
        self.assertEqual(g["done"], 0)
        self.assertEqual(g["skipped"], 0)
        self.assertEqual(g["blueprint"], "draft")
        self.assertEqual(g["frontier"]["target"], "test-101-m01-test-module")
        self.assertEqual(g["rows"][0]["state"], "open")
        self.assertEqual(g["rows"][0]["label"], "Test module")
        self.assertGreater(g["rows"][0]["line"], 0)
        self.assertTrue(g["rows"][0]["raw"].startswith("- [ ] M01"))

    def test_done_and_skipped_rows_keep_their_state_and_date(self):
        (self.course / "test-101-guide.md").write_text(
            "---\ntype: guide\ncourse: TEST-101\ntags: [guide]\n---\n\n"
            "- [x] M01 · [[test-101-m01-test-module|Test module]]\n"
            "- [-] M02 · skipped::2026-08-05 [[test-101-m02-later|Later]]\n"
            "- [ ] M03 · [[test-101-m03-next|Next]]\n", encoding="utf-8")
        g = self.client.get("/api/guide/test-101").json()
        self.assertEqual([r["state"] for r in g["rows"]],
                         ["done", "skipped", "open"])
        self.assertEqual(g["rows"][1]["skipped"], "2026-08-05")
        self.assertEqual(g["done"], 1)
        self.assertEqual(g["skipped"], 1)
        self.assertEqual(g["frontier"]["target"], "test-101-m03-next")

    def test_a_finished_chain_has_no_frontier(self):
        (self.course / "test-101-guide.md").write_text(
            "---\ntype: guide\ncourse: TEST-101\ntags: [guide]\n---\n\n"
            "- [x] M01 · [[test-101-m01-test-module|Test module]]\n",
            encoding="utf-8")
        g = self.client.get("/api/guide/test-101").json()
        self.assertIsNone(g["frontier"])

    def test_a_course_without_a_chain_is_a_404(self):
        r = self.client.get("/api/guide/test-102")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "no guide chain")

    def test_a_sealed_chain_is_a_404(self):
        (self.vault / ".gitignore").write_text(
            "02-Areas/Academics/TEST-101/test-101-guide.md\n", encoding="utf-8")
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.assertEqual(self.client.get("/api/guide/test-101").status_code, 404)


class TestCourses(GuideApiBase):
    def test_every_active_course_reports_its_study_surface(self):
        r = self.client.get("/api/courses")
        self.assertEqual(r.status_code, 200, r.text)
        rows = {c["course"]: c for c in r.json()["courses"]}
        self.assertIn("TEST-101", rows)
        self.assertIn("TEST-102", rows)
        c1 = rows["TEST-101"]
        self.assertEqual(c1["modules"], 1)
        self.assertEqual(c1["held"], 0)
        self.assertFalse(c1["timeline"])
        self.assertEqual(c1["guide"]["total"], 2)
        self.assertEqual(c1["guide"]["blueprint"], "draft")
        self.assertNotIn("rows", c1["guide"])   # a glance, not the chain
        c2 = rows["TEST-102"]
        self.assertTrue(c2["timeline"])
        self.assertIsNone(c2["guide"])


if __name__ == "__main__":
    unittest.main()
