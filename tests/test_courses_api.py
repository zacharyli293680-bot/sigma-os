#!/usr/bin/env python3
"""
POST /api/courses — starting a course from the dashboard.

A course has always been *a folder on disk*: `todo.active_courses` counts any
directory under Academics, and a folder with no index note reads as **active**.
That makes the interesting tests the ones about what the write refuses and what
it leaves behind, not the happy path:

  · a typo'd code becomes a real course nobody can rename cheaply — so the code
    is validated here, the first and only validator in the codebase;
  · a folder created without its index note is a course that is enrolled in and
    has no name, so both must land in one commit or neither;
  · a refusal must leave no folder behind, or the retry hits "already exists".
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

import panels                                      # noqa: E402
import privacy                                     # noqa: E402
import todo                                        # noqa: E402
import writes                                      # noqa: E402
from sigma import frontmatter, gitops, ledger      # noqa: E402
from test_queue_api import QueueApiBase, _git      # noqa: E402


class CourseAddBase(QueueApiBase):
    def setUp(self):
        super().setUp()
        self._mutex = gitops.MUTEX_PATH
        gitops.MUTEX_PATH = Path(self.tmp.name) / "git.lock"
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "init")

    def tearDown(self):
        gitops.MUTEX_PATH = self._mutex
        super().tearDown()

    def add(self, code="CSE-421", **over):
        body = {"code": code, "name": "Software Design", "term": "Fall 2026"}
        body.update(over)
        return writes.api_course_add(writes.CourseAdd(**body))

    def folder(self, code="CSE-421"):
        return self.vault / "02-Areas" / "Academics" / code

    def assert_refused(self, r, status, code, folder="CSE-421"):
        self.assertNotIsInstance(r, dict, r)
        self.assertEqual(r.status_code, status)
        self.assertFalse(self.folder(folder).exists(),
                         "a refusal must leave no folder behind")


class TestItWrites(CourseAddBase):
    def test_a_course_is_a_folder_an_index_note_and_one_commit(self):
        r = self.add()
        self.assertEqual(r["ok"], True)
        self.assertEqual(r["course"], "CSE-421")
        self.assertEqual(r["file"], "02-Areas/Academics/CSE-421/cse-421.md")
        self.assertIsNotNone(r["sha"])

        note = (self.vault / r["file"]).read_text(encoding="utf-8")
        fm = frontmatter(note)
        self.assertEqual(fm["type"], "course-index")
        self.assertEqual(fm["course"], "CSE-421")
        self.assertEqual(fm["name"], "Software Design")
        self.assertEqual(fm["term"], "Fall 2026")
        self.assertEqual(fm["status"], "active")
        self.assertIn("# CSE 421 — Software Design", note)
        self.assertIn("[[academics|Academics MOC]]", note)

    def test_status_is_written_out_rather_than_left_blank(self):
        """Blank also reads as active. A field that means something by being
        empty is the drift this contract keeps closing."""
        self.add()
        note = (self.folder() / "cse-421.md").read_text(encoding="utf-8")
        self.assertIn("status: active", note)

    def test_the_index_lands_in_the_same_commit_as_the_folder(self):
        """Between the two writes the course would be enrolled in and nameless
        — `active_courses` reads a folder with no index as active."""
        r = self.add()
        touched = _git(self.vault, "show", "--name-only", "--format=", r["sha"])
        self.assertEqual(touched.stdout.split(),
                         ["02-Areas/Academics/CSE-421/cse-421.md"])

    def test_the_subfolders_exist_on_disk_and_not_in_the_commit(self):
        """git does not track empty directories. They are there so dropping a
        PDF in the right place works the moment the course is made."""
        self.add()
        for sub in ("lectures", "assignments", "exams"):
            self.assertTrue((self.folder() / sub).is_dir(), sub)
        self.assertEqual(_git(self.vault, "status", "--short").stdout.strip(), "")

    def test_it_lands_in_the_ledger_as_one_revertible_row(self):
        r = self.add()
        entry = ledger.entries()[0]
        self.assertEqual((entry["actor"], entry["action"]), ("zach", "create"))
        self.assertEqual(entry["target"], r["file"])
        self.assertEqual(entry["sha"], r["sha"])
        self.assertIn("CSE-421", entry["summary"])

    def test_the_new_course_is_immediately_active_and_in_the_payload(self):
        self.add()
        self.assertIn("CSE-421", todo.active_courses(self.vault))
        panels._cache.clear()
        got = panels.api_courses()
        self.assertEqual([c["course"] for c in got["courses"]], ["CSE-421"])
        self.assertEqual(got["courses"][0]["name"], "Software Design")

    def test_a_course_with_no_term_is_fine(self):
        """Every existing index in this vault carries `term:` blank."""
        r = self.add(code="AA-260", name="Thermodynamics", term="")
        note = (self.vault / r["file"]).read_text(encoding="utf-8")
        self.assertIn("term:", note)
        self.assertEqual(frontmatter(note)["status"], "active")

    def test_undoing_the_add_leaves_a_folder_the_payload_names(self):
        """git cannot track the empty subfolders, so a revert deletes the note
        and leaves the folder — which `active_courses` still counts as active.
        The state is reachable by hand too, so the payload names it rather than
        the card rendering a nameless course as if it were fine."""
        r = self.add()
        (self.vault / r["file"]).unlink()       # what `git revert` does to it
        self.assertIn("CSE-421", todo.active_courses(self.vault))
        panels._cache.clear()
        row = next(c for c in panels.api_courses()["courses"]
                   if c["course"] == "CSE-421")
        self.assertFalse(row["indexed"])

    def test_an_indexed_course_says_so(self):
        self.add()
        panels._cache.clear()
        self.assertTrue(panels.api_courses()["courses"][0]["indexed"])

    def test_the_response_says_the_groupings_block_is_still_yours(self):
        """academics.md's Dataview tables pick it up; the hand-written
        department sequence cannot be inferred and is never touched."""
        r = self.add()
        self.assertIn("hand", r["groupings"])
        self.assertEqual(list(r["subfolders"]), ["lectures", "assignments", "exams"])


class TestItRefuses(CourseAddBase):
    def test_a_code_that_is_not_dept_number_is_refused(self):
        for bad in ("cse421", "CSE-42", "CSE-4211", "TOOLONG-421", "", "  ",
                    "CSE 421", "../escape", "CSE-421/x"):
            with self.subTest(bad=bad):
                r = self.add(code=bad)
                self.assertNotIsInstance(r, dict, bad)
                self.assertEqual(r.status_code, 400)

    def test_a_lowercase_code_is_accepted_and_normalised(self):
        """The folder name is uppercase by contract; refusing the shift-key is
        pedantry, writing `cse-421/` would be drift."""
        r = self.add(code="  cse-421 ")
        self.assertEqual(r["course"], "CSE-421")
        self.assertTrue(self.folder("CSE-421").is_dir())

    def test_an_existing_course_is_refused_rather_than_merged_into(self):
        self.add()
        before = _git(self.vault, "rev-parse", "HEAD").stdout
        r = self.add(name="Something Else")
        self.assertNotIsInstance(r, dict)
        self.assertEqual(r.status_code, 409)
        # the first course is untouched, and no commit happened
        self.assertEqual(_git(self.vault, "rev-parse", "HEAD").stdout, before)
        note = (self.folder() / "cse-421.md").read_text(encoding="utf-8")
        self.assertIn("name: Software Design", note)

    def test_an_existing_folder_with_no_index_is_still_a_course(self):
        """It reads as active to the queue, so this is a merge, not a create."""
        self.folder("MATH-999").mkdir(parents=True)
        r = self.add(code="MATH-999")
        self.assertNotIsInstance(r, dict)
        self.assertEqual(r.status_code, 409)

    def test_a_name_longer_than_a_title_is_refused(self):
        self.assert_refused(self.add(name="x" * 121), 400, "too long")

    def test_a_nameless_course_is_refused(self):
        """active_courses falls back to the folder name, so a blank name makes
        a card that reads CSE-421 twice and has told you nothing."""
        self.assert_refused(self.add(name="   "), 400, "empty")

    def test_a_colon_is_refused_because_dataview_parses_real_yaml(self):
        """`sigma.frontmatter` is a hand-rolled regex and would take it. The
        tables in academics.md are Dataview, which is not — one note with
        `name: CSE 421: Design` would take both course tables down, and it
        would fail in Obsidian rather than here."""
        self.assert_refused(self.add(name="CSE 421: Design"), 400, "bad name")
        self.assert_refused(self.add(term="Autumn: 2026"), 400, "bad term")

    def test_a_sealed_academics_folder_is_refused_before_the_write(self):
        (self.vault / ".gitignore").write_text("02-Areas/Academics/\n",
                                               encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "seal")
        privacy.VaultPrivacy._git_ignored.cache_clear()
        r = self.add()
        self.assertNotIsInstance(r, dict)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.folder().exists())


class TestThePaceScratchSpace(CourseAddBase):
    def test_a_prescanned_module_list_gives_the_same_answer(self):
        """The courses payload hands one scan and one pool cache to every pace
        question; the numbers must not depend on having done so."""
        import lesson as ln
        import recall as rc
        self.add()
        scan = ln.scan(self.vault)
        pools: dict = {}
        fresh = rc.pace(self.vault, "CSE-421")
        cached = rc.pace(self.vault, "CSE-421", scan=scan, pools=pools)
        self.assertEqual(fresh, cached)
        self.assertEqual(rc.projected(self.vault, "CSE-421", fresh),
                         rc.projected(self.vault, "CSE-421", cached, scan=scan))
        # and the cache was actually used
        self.assertIn("CSE-421", pools)


if __name__ == "__main__":
    unittest.main()
