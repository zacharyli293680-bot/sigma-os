#!/usr/bin/env python3
"""
GET /api/lesson — study mode S1's read surface.

The claim this suite pins: the endpoint serves exactly what lesson.py parses,
hides sealed modules the same fail-closed way every other panel does, and
answers absence with a 404 the frontend can read — through HTTP, because the
parts that break are the status code and the shape of the body.
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
from test_lesson import _valid               # noqa: E402 — the shared conforming fixture


def _git(cwd: Path, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


class LessonApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (course / "guide").mkdir(parents=True)
        (course / "lectures").mkdir()
        for n in (1, 2, 3):
            (course / "lectures" / f"l{n}.md").write_text("# src\n", encoding="utf-8")
        (course / "guide" / "test-101-m01-test-module.md").write_text(
            _valid(), encoding="utf-8")
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


class TestLessonList(LessonApiBase):
    def test_the_list_names_the_module_and_its_health(self):
        r = self.client.get("/api/lesson")
        self.assertEqual(r.status_code, 200, r.text)
        mods = r.json()["modules"]
        self.assertEqual(len(mods), 1)
        m = mods[0]
        self.assertEqual(m["course"], "TEST-101")
        self.assertEqual(m["module"], 1)
        self.assertEqual(m["segments"], 3)
        self.assertEqual(m["practice"], 8)
        self.assertEqual(m["problems"], [])
        self.assertTrue(m["file"].endswith("test-101-m01-test-module.md"))


class TestLessonOne(LessonApiBase):
    def test_one_module_arrives_parsed_with_provenance_lines(self):
        r = self.client.get("/api/lesson/test-101/1")
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d["title"], "Test module")
        self.assertEqual(len(d["segments"]), 3)
        seg = d["segments"][0]
        self.assertEqual(seg["sources"][0]["path"],
                         "02-Areas/Academics/TEST-101/lectures/l1.md")
        self.assertGreater(seg["sources"][0]["line"], 0)
        self.assertEqual(seg["practice"][0]["answer"], "2")
        self.assertEqual(d["problems"], [])

    def test_absence_is_a_404_with_a_readable_error(self):
        r = self.client.get("/api/lesson/test-101/9")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "no such module")

    def test_a_broken_module_still_arrives_but_names_its_problems(self):
        p = (self.vault / "02-Areas" / "Academics" / "TEST-101" / "guide"
             / "test-101-m01-test-module.md")
        p.write_text(_valid().replace("- answer:: 2\n", "", 1), encoding="utf-8")
        panels._cache.clear()
        r = self.client.get("/api/lesson/test-101/1")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("answer::" in x for x in r.json()["problems"]))


class TestSealed(LessonApiBase):
    def test_a_sealed_module_is_absent_from_list_and_404_alone(self):
        (self.vault / ".gitignore").write_text(
            "02-Areas/Academics/TEST-101/guide/\n", encoding="utf-8")
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.assertEqual(self.client.get("/api/lesson").json()["modules"], [])
        self.assertEqual(self.client.get("/api/lesson/test-101/1").status_code, 404)


if __name__ == "__main__":
    unittest.main()
