"""
The priority-queue engine (todo.py).

Two claims are worth defending here, and they fail in different directions.

The **ordering** claim is that a task's position is derived, inspectable, and
stable — so the tests pin the exact score arithmetic, the overdue ceiling, and
the tie-break that stops a SHA prefix from deciding the visible window on a day
when every task scores the same.

The **memory** claim is that the sidecar only ever adds to what the notes say.
Those tests are the adversarial half: a torn index must not silently re-adopt
the vault, un-ticking must not re-age a task, and a checkbox inside a fenced
code block is not work.
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

import privacy                                     # noqa: E402
import todo                                        # noqa: E402

TODAY = "2026-08-01"
OPEN = lambda v, rels: (set(), set())              # noqa: E731 — nothing sealed


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def task(**kw):
    """A scored task dict, with the fields sort_key and parts_of need."""
    t = {"created": TODAY, "deadline": None, "urgency": "medium",
         "pinned": False, "file": "a.md", "order": 0}
    t.update(kw)
    t["parts"] = todo.parts_of(t, TODAY)
    t["score"] = t["parts"]["total"]
    return t


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

class TestScore(unittest.TestCase):
    def test_no_deadline_scores_urgency_plus_aging_only(self):
        self.assertEqual(todo.score(task(), TODAY), 15.0)
        self.assertEqual(todo.score(task(urgency="high"), TODAY), 30.0)
        self.assertEqual(todo.score(task(urgency="low"), TODAY), 5.0)

    def test_due_today_is_the_full_hundred(self):
        self.assertEqual(todo.parts_of(task(deadline=TODAY), TODAY)["deadline"], 100.0)

    def test_deadline_pressure_decays(self):
        # a week out is worth an eighth of today, which is the whole point of
        # the shape: deadlines dominate only as they arrive.
        self.assertEqual(todo.parts_of(task(deadline="2026-08-08"), TODAY)["deadline"], 12.5)
        self.assertEqual(todo.parts_of(task(deadline="2026-08-02"), TODAY)["deadline"], 50.0)

    def test_overdue_caps_at_a_hundred_rather_than_escalating(self):
        """A forgotten task must never be able to monopolise its window."""
        long_gone = todo.parts_of(task(deadline="2026-01-01"), TODAY)
        self.assertEqual(long_gone["deadline"], 100.0)
        self.assertEqual(long_gone["deadline"],
                         todo.parts_of(task(deadline=TODAY), TODAY)["deadline"])
        self.assertLess(long_gone["days_until"], 0)      # still known to be late

    def test_aging_is_capped(self):
        self.assertEqual(todo.parts_of(task(created="2026-07-30"), TODAY)["aging"], 1.0)
        # 0.5/day would be 90 after half a year; the anti-starvation term is a
        # nudge into view, not a way to outrank a real deadline.
        self.assertEqual(todo.parts_of(task(created="2026-02-01"), TODAY)["aging"], 15.0)

    def test_urgency_bands_come_from_the_priority_emoji(self):
        self.assertEqual(todo.urgency_of(4), "high")     # 🔺
        self.assertEqual(todo.urgency_of(3), "high")     # ⏫
        self.assertEqual(todo.urgency_of(2), "medium")   # 🔼
        self.assertEqual(todo.urgency_of(1), "low")      # 🔽
        self.assertEqual(todo.urgency_of(0), "low")      # ⏬
        # unmarked is medium, never below something explicitly deprioritised
        self.assertEqual(todo.urgency_of(None), "medium")


class TestOrdering(unittest.TestCase):
    def test_pinned_bypasses_scoring(self):
        pinned = task(pinned=True, urgency="low")
        urgent = task(urgency="high", deadline=TODAY)
        self.assertEqual(sorted([urgent, pinned], key=todo.sort_key)[0], pinned)

    def test_ties_break_by_age_then_document_order(self):
        """The day-one case: same score, same date, so the file decides.

        Without the document-order term this fell through to a SHA prefix and
        the visible window was effectively random.
        """
        a = task(file="Todo.md", order=0)
        b = task(file="Todo.md", order=1)
        older = task(file="Todo.md", order=9, created="2026-07-01")
        self.assertEqual([t["order"] for t in sorted([b, a], key=todo.sort_key)], [0, 1])
        # age wins over position, and it wins on its own aging term too
        self.assertIs(sorted([a, b, older], key=todo.sort_key)[0], older)


class TestSection(unittest.TestCase):
    def test_path_decides_the_queue(self):
        self.assertEqual(todo.section_of("02-Areas/Academics/AA-210/timeline.md"),
                         ("courses", "AA-210"))
        self.assertEqual(todo.section_of("02-Areas/ProCertus/Todo.md"),
                         ("procertus", None))
        self.assertEqual(todo.section_of("03-Projects/sigma-os.md"),
                         ("projects", "sigma-os"))
        self.assertEqual(todo.section_of("03-Projects/sigma-os/brain.md"),
                         ("projects", "sigma-os"))
        self.assertEqual(todo.section_of("01-Daily/2026-08-01.md"), ("misc", None))
        self.assertEqual(todo.section_of("02-Areas/Clubs/table-tennis.md"), ("misc", None))


# --------------------------------------------------------------------------
# vault-backed
# --------------------------------------------------------------------------

class QueueBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        self.index = Path(self.tmp.name) / "todo.json"

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, rel: str, body: str):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def course(self, code: str, body: str, status="active"):
        self.note(f"02-Areas/Academics/{code}/{code.lower()}.md",
                  f"---\ntype: course-index\ncourse: {code}\nstatus: {status}\n---\n")
        self.note(f"02-Areas/Academics/{code}/timeline.md", body)

    def project(self, name: str, body: str, status="active"):
        self.note(f"03-Projects/{name}.md",
                  f"---\ntype: project\nstatus: {status}\n---\n\n{body}")

    def build(self, today=TODAY, persist=True):
        return todo.build(vault=self.vault, index_path=self.index, today=today,
                          split=OPEN, persist=persist)


class TestScan(QueueBase):
    def test_a_fenced_checkbox_is_not_work(self):
        """CLAUDE.md documents the task grammar with a fenced example."""
        self.note("00-Inbox/doc.md",
                  "```\n- [ ] Finish PSet 3 📅 2026-01-20 🔺\n```\n- [ ] a real one\n")
        texts = [t["text"] for t in todo.scan(self.vault, split=OPEN)]
        self.assertEqual(texts, ["a real one"])

    def test_done_boxes_are_collected_too(self):
        """They are how completion is detected — see reconcile."""
        self.note("00-Inbox/a.md", "- [x] shipped\n- [ ] pending\n")
        found = {t["text"]: t["done"] for t in todo.scan(self.vault, split=OPEN)}
        self.assertEqual(found, {"shipped": True, "pending": False})

    def test_wikilinks_reduce_to_their_label(self):
        self.note("00-Inbox/a.md",
                  "- [ ] Read [[02-Areas/Academics/AA-210/Day 3.pptx|Day 3]] then "
                  "[[git-guide]]\n")
        self.assertEqual(todo.scan(self.vault, split=OPEN)[0]["text"],
                         "Read Day 3 then git-guide")

    def test_metadata_is_parsed_out_of_the_text_but_not_lost(self):
        self.note("00-Inbox/a.md", "- [ ] Finish PSet 3 📅 2026-01-20 🔺\n")
        t = todo.scan(self.vault, split=OPEN)[0]
        self.assertEqual(t["text"], "Finish PSet 3")
        self.assertEqual((t["deadline"], t["priority"]), ("2026-01-20", 4))

    def test_the_h2_above_a_task_is_carried_with_it(self):
        self.note("00-Inbox/a.md", "## Block 2 — Vectors\n\n- [ ] span\n")
        self.assertEqual(todo.scan(self.vault, split=OPEN)[0]["heading"],
                         "Block 2 — Vectors")

    def test_archive_and_system_folders_are_not_scanned(self):
        self.note("05-Archive/old.md", "- [ ] finished course work\n")
        self.note("06-System/sessions/log.md", "- [ ] an agent's own note\n")
        self.note("00-Inbox/a.md", "- [ ] live\n")
        self.assertEqual([t["text"] for t in todo.scan(self.vault, split=OPEN)], ["live"])

class TestPrivacyBoundary(QueueBase):
    """The real boundary, with real git and the real privacy module — not the
    injected stub. The two halves are deliberately asymmetric (see privacy.py),
    so both are pinned here: gitignored-and-unlisted vanishes, gitignored-and-
    listed is shown *and* marked."""

    def setUp(self):
        super().setUp()
        self._saved = privacy.model_allow_prefixes
        privacy.model_allow_prefixes = lambda: ("02-areas/procertus",)

    def tearDown(self):
        privacy.model_allow_prefixes = self._saved
        super().tearDown()

    def test_a_sealed_note_never_reaches_the_queue(self):
        (self.vault / ".gitignore").write_text("02-Areas/Personal/\n", encoding="utf-8")
        self.note("02-Areas/Personal/private.md", "- [ ] nobody's business\n")
        self.note("00-Inbox/a.md", "- [ ] ordinary\n")
        self.assertEqual([t["text"] for t in todo.scan(self.vault)], ["ordinary"])

    def test_a_model_exempt_note_is_shown_and_marked(self):
        """ProCertus: readable by the model, never syncable. Shown, wearing ⊘."""
        (self.vault / ".gitignore").write_text("02-Areas/ProCertus/\n", encoding="utf-8")
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] client material\n")
        found = todo.scan(self.vault)
        self.assertEqual([t["text"] for t in found], ["client material"])
        self.assertTrue(found[0]["no_sync"])

    def test_an_unanswerable_git_seals_everything(self):
        """Empty queues are loud; internship material on screen is not recoverable."""
        self.note("00-Inbox/a.md", "- [ ] ordinary\n")
        self.assertEqual(todo.scan(Path(self.tmp.name) / "not-a-repo", split=None), [])


class TestChains(QueueBase):
    def test_only_the_first_open_task_in_a_chain_is_eligible(self):
        self.course("AA-210", "- [x] day 1\n- [ ] day 2\n- [ ] day 3\n")
        s = self.build()["sections"]["courses"]
        self.assertEqual([t["text"] for t in s["visible"]], ["day 2"])
        self.assertEqual([t["text"] for t in s["blocked"]], ["day 3"])

    def test_a_checked_task_mid_chain_does_not_unblock_what_follows(self):
        """Ticking out of order must not open two frontiers in one course."""
        self.course("AA-210", "- [ ] day 1\n- [x] day 2\n- [ ] day 3\n")
        s = self.build()["sections"]["courses"]
        self.assertEqual([t["text"] for t in s["visible"]], ["day 1"])
        self.assertEqual([t["blocked_by"] for t in s["blocked"]], ["day 1"])

    def test_one_frontier_per_course_not_per_chain_file(self):
        self.course("AA-210", "- [ ] timeline task\n")
        self.note("02-Areas/Academics/AA-210/assignments/hw1.md", "- [ ] hw one\n")
        s = self.build()["sections"]["courses"]
        self.assertEqual(len(s["visible"]), 1)
        self.assertEqual(len(s["queue"]), 1)          # the other head still queued

    def test_an_inactive_course_keeps_its_tasks_but_spends_no_slot(self):
        self.course("AA-210", "- [ ] live work\n")
        self.course("CSE-344", "- [ ] next quarter\n", status="planned")
        s = self.build()["sections"]["courses"]
        self.assertEqual([t["text"] for t in s["visible"]], ["live work"])
        self.assertEqual([t["text"] for t in s["queue"]], ["next quarter"])
        # the header reads "1 of 1 courses" — the planned course is not counted
        self.assertEqual((len(s["visible"]), s["window"], s["parent_noun"]),
                         (1, 1, "course"))

    def test_a_silent_parent_still_counts_toward_the_header(self):
        """"1 of 2 projects" is the point: a project contributing nothing is a
        fact worth seeing, not one to hide behind "one per project"."""
        self.project("sigma-os", "- [ ] first\n")
        self.project("httc-website", "no tasks here\n")
        s = self.build()["sections"]["projects"]
        self.assertEqual((len(s["visible"]), s["window"]), (1, 2))

    def test_a_course_folder_without_an_index_note_still_counts(self):
        """A missing manifest is missing documentation, not a dropped course."""
        self.note("02-Areas/Academics/PHYS-121/timeline.md", "- [ ] mechanics\n")
        self.assertEqual([t["text"] for t in self.build()["sections"]["courses"]["visible"]],
                         ["mechanics"])

    def test_projects_chain_the_same_way(self):
        self.project("sigma-os", "- [ ] first\n- [ ] second\n")
        s = self.build()["sections"]["projects"]
        self.assertEqual([t["text"] for t in s["visible"]], ["first"])
        self.assertEqual([t["blocked_by"] for t in s["blocked"]], ["first"])

    def test_the_projects_moc_is_not_a_project(self):
        self.note("03-Projects/projects.md", "---\ntype: moc\n---\n\n- [ ] tidy the MOC\n")
        s = self.build()["sections"]["projects"]
        self.assertEqual(s["window"], 0)
        self.assertEqual(s["visible"], [])            # no active parent to belong to


class TestWindows(QueueBase):
    def test_a_window_is_a_maximum_not_a_quota(self):
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] one\n- [ ] two\n")
        s = self.build()["sections"]["procertus"]
        self.assertEqual(len(s["visible"]), 2)        # window is 3; no filler
        self.assertEqual(s["queue"], [])

    def test_the_rest_queue_behind_the_window(self):
        self.note("02-Areas/ProCertus/Todo.md",
                  "".join(f"- [ ] task {i}\n" for i in range(6)))
        s = self.build()["sections"]["procertus"]
        self.assertEqual(len(s["visible"]), 3)
        self.assertEqual(len(s["queue"]), 3)
        self.assertEqual([t["text"] for t in s["visible"]],
                         ["task 0", "task 1", "task 2"])

    def test_a_deadline_promotes_past_document_order(self):
        self.note("02-Areas/ProCertus/Todo.md",
                  "- [ ] a\n- [ ] b\n- [ ] c\n- [ ] d 📅 2026-08-01\n")
        s = self.build()["sections"]["procertus"]
        self.assertEqual(s["visible"][0]["text"], "d")

    def test_pinning_beats_the_window(self):
        self.note("02-Areas/ProCertus/Todo.md",
                  "".join(f"- [ ] task {i}\n" for i in range(5)))
        self.build()
        idx = json.loads(self.index.read_text(encoding="utf-8"))
        last = [k for k, v in idx["tasks"].items() if v["text"] == "task 4"][0]
        idx["tasks"][last]["pinned"] = True
        self.index.write_text(json.dumps(idx), encoding="utf-8")
        s = self.build()["sections"]["procertus"]
        self.assertEqual(s["visible"][0]["text"], "task 4")
        self.assertEqual(len(s["visible"]), 3)


class TestSuppression(QueueBase):
    def test_daily_notes_do_not_feed_the_queue(self):
        """They were the old system's output — a ritual checklist minted every
        morning plus a restatement of work that already lives in the other three
        queues. Ingesting them made Misc a duplicate of Courses and ProCertus."""
        self.note("01-Daily/2026-08-01.md",
                  "- [ ] Triage 📥 Inbox to zero\n- [ ] Log the day below\n")
        self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        s = self.build()["sections"]["misc"]
        self.assertEqual([t["text"] for t in s["visible"]], ["book the court"])

    def test_a_dated_daily_task_is_still_excluded(self):
        """/api/tasks and the calendar strip still read daily notes; the queue
        does not. The exclusion is the queue's alone."""
        self.note("01-Daily/2026-08-01.md", "- [ ] pay the fee 📅 2026-08-03\n")
        self.assertEqual(self.build()["counts"]["visible"], 0)

    def test_nothing_expires_on_its_own(self):
        """Suppression is a decision, not a timer — an untouched task is still
        yours a year later, wearing the aging chip that says so."""
        self.note("02-Areas/Clubs/table-tennis.md", "- [ ] book the court\n")
        self.build()                                   # adopt, so it has an age
        s = self.build(today="2027-01-01")["sections"]["misc"]
        self.assertEqual(len(s["visible"]), 1)
        self.assertEqual(s["visible"][0]["parts"]["aging"], todo.AGING_CAP)

    def test_archiving_removes_a_task_from_the_running_without_deleting_it(self):
        self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        self.build()
        idx = json.loads(self.index.read_text(encoding="utf-8"))
        idx["tasks"][next(iter(idx["tasks"]))]["status"] = "archived"
        self.index.write_text(json.dumps(idx), encoding="utf-8")
        s = self.build()["sections"]["misc"]
        self.assertEqual(s["visible"], [])
        self.assertEqual([t["text"] for t in s["archived"]], ["book the court"])

    def test_snooze_suppresses_then_returns(self):
        self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        self.build()
        idx = json.loads(self.index.read_text(encoding="utf-8"))
        tid = next(iter(idx["tasks"]))
        idx["tasks"][tid]["snoozed_until"] = "2026-08-04"
        self.index.write_text(json.dumps(idx), encoding="utf-8")

        s = self.build()["sections"]["misc"]
        self.assertEqual(s["visible"], [])
        self.assertEqual(len(s["snoozed"]), 1)
        # on the day itself it is back — snoozed_until is inclusive
        self.assertEqual(len(self.build(today="2026-08-04")["sections"]["misc"]["visible"]), 1)


# --------------------------------------------------------------------------
# the index
# --------------------------------------------------------------------------

class TestReconcile(QueueBase):
    def test_tasks_age_from_adoption_and_keep_ageing(self):
        """Nothing in the vault records when a checkbox was written, so ages
        start when the queue starts — and then run from that fixed point."""
        self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        self.assertEqual(self.build()["sections"]["misc"]["visible"][0]["created"], TODAY)
        later = self.build(today="2026-08-11")["sections"]["misc"]["visible"][0]
        self.assertEqual(later["created"], TODAY)
        self.assertEqual(later["parts"]["age_days"], 10)

    def test_ticking_records_the_completion_date(self):
        p = self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        self.build()
        p.write_text("- [x] book the court\n", encoding="utf-8")
        self.build()
        entry = next(iter(json.loads(self.index.read_text(encoding="utf-8"))["tasks"].values()))
        self.assertEqual(entry["completed_at"], TODAY)

    def test_boxes_already_ticked_at_adoption_are_not_todays_work(self):
        """Otherwise the first review scores a day that never happened."""
        self.note("02-Areas/Clubs/tt.md", "- [x] done long ago\n")
        self.build()
        entry = next(iter(json.loads(self.index.read_text(encoding="utf-8"))["tasks"].values()))
        self.assertIsNone(entry["completed_at"])

    def test_unticking_restores_the_task_with_its_original_age(self):
        """Re-aging a task you re-opened would punish undo."""
        p = self.note("02-Areas/Clubs/tt.md", "- [ ] mistake\n")
        self.build()
        p.write_text("- [x] mistake\n", encoding="utf-8")
        self.build()
        p.write_text("- [ ] mistake\n", encoding="utf-8")
        t = self.build(today="2026-08-13")["sections"]["misc"]["visible"][0]
        self.assertEqual(t["created"], TODAY)
        self.assertEqual(t["parts"]["age_days"], 12)
        entry = next(iter(json.loads(self.index.read_text(encoding="utf-8"))["tasks"].values()))
        self.assertIsNone(entry["completed_at"])

    def test_adding_a_due_date_keeps_the_task_and_its_age(self):
        """The common edit. Identity keys on the cleaned text for exactly this."""
        p = self.note("02-Areas/Clubs/tt.md", "- [ ] file the form\n")
        before = self.build()["sections"]["misc"]["visible"][0]["id"]
        p.write_text("- [ ] file the form 📅 2026-08-05 🔺\n", encoding="utf-8")
        after = self.build(today="2026-08-03")["sections"]["misc"]["visible"][0]
        self.assertEqual(after["id"], before)
        self.assertEqual(after["created"], TODAY)      # age survived the edit
        self.assertEqual(after["urgency"], "high")

    def test_rewording_a_task_mints_a_new_identity(self):
        """The honest failure. Fuzzy matching would mis-attribute history, which
        is worse than losing it."""
        p = self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        before = self.build()["sections"]["misc"]["visible"][0]["id"]
        p.write_text("- [ ] book the squash court\n", encoding="utf-8")
        self.assertNotEqual(self.build()["sections"]["misc"]["visible"][0]["id"], before)

    def test_a_vanished_task_is_kept_for_a_while_then_pruned(self):
        p = self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        self.build()
        p.write_text("", encoding="utf-8")
        self.build()
        self.assertEqual(len(json.loads(self.index.read_text(encoding="utf-8"))["tasks"]), 1)
        self.build(today="2026-09-15")                # past PRUNE_DAYS
        self.assertEqual(json.loads(self.index.read_text(encoding="utf-8"))["tasks"], {})


class TestIndexSafety(QueueBase):
    def test_a_torn_index_is_never_written_over(self):
        """Treating an unreadable index as empty would re-adopt the whole vault
        with today's date, silently resetting every task's age — the one piece
        of state this file exists to hold."""
        self.note("01-Daily/2026-07-20.md", "- [ ] a task\n")
        self.index.write_text('{"tasks": {"broke', encoding="utf-8")
        q = self.build()
        self.assertFalse(q["index_ok"])
        self.assertEqual(self.index.read_text(encoding="utf-8"), '{"tasks": {"broke')

    def test_a_missing_index_is_simply_adopted(self):
        self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        q = self.build()
        self.assertTrue(q["index_ok"])
        self.assertEqual(q["adopted"], TODAY)

    def test_the_index_survives_a_scan_that_finds_nothing(self):
        self.note("02-Areas/Clubs/tt.md", "- [ ] book the court\n")
        self.build()
        (self.vault / "02-Areas" / "Clubs" / "tt.md").unlink()
        self.build()
        self.assertEqual(len(json.loads(self.index.read_text(encoding="utf-8"))["tasks"]), 1)


class TestCounts(QueueBase):
    def test_the_header_counts_match_the_sections(self):
        self.course("AA-210", "- [ ] one\n- [ ] two\n- [ ] three\n")
        self.note("02-Areas/ProCertus/Todo.md",
                  "".join(f"- [ ] p{i}\n" for i in range(5)))
        self.note("01-Daily/2026-08-01.md", "- [ ] Triage 📥 Inbox to zero\n")
        q = self.build()
        self.assertEqual(q["counts"]["visible"], 1 + 3)
        self.assertEqual(q["counts"]["queued"], 2)     # procertus overflow
        self.assertEqual(q["counts"]["blocked"], 2)    # the course chain
        self.assertEqual(q["counts"]["archived"], 0)   # the daily note is not scanned


if __name__ == "__main__":
    unittest.main()
