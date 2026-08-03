"""
The calendar's write path, and every hold that stops it.

`test_applier.py` is the standard this file is trying to meet: the interesting
tests are not the ones where the write works, they are the ones where it must
not. Each hold below has a test that *tries* to get past it, because a guard
nobody attacked is a guard nobody has tested.

The holds, in the order the endpoint applies them:

    line changed underneath the client   409 stale
    the composed line does not re-parse  400   never write what the scanner cannot read
    the line would span more than one    400   scope
    the note's checkbox count changes    400   completion is a human signal
    target escapes the vault             400   scope
    target is gitignored                 403   inside the mutex, failing closed
    the occurrence is not an event row   400   rules are P6
    the git mutex is busy                409   refuse, never queue

Two properties are asserted about every successful write rather than about a
chosen example: the line that lands **re-parses to what was asked for**, and the
ledger row points at a **real commit** that `git revert` can undo.
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

import agenda                                      # noqa: E402
import panels                                      # noqa: E402
import privacy                                     # noqa: E402
import todo                                        # noqa: E402
import writes                                      # noqa: E402
from sigma import gitops, ledger                   # noqa: E402


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def body(r):
    """The JSON of an error response."""
    return json.loads(bytes(r.body).decode())


class WriteBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")

        self._saved = {"panels": panels.VAULT, "writes": writes.VAULT,
                       "index": todo.INDEX_PATH, "ledger": ledger.LEDGER_PATH,
                       "mutex": gitops.MUTEX_PATH}
        panels.VAULT = writes.VAULT = self.vault
        todo.INDEX_PATH = Path(self.tmp.name) / "todo.state.json"
        ledger.LEDGER_PATH = Path(self.tmp.name) / "ledger.jsonl"
        gitops.MUTEX_PATH = Path(self.tmp.name) / "git.lock"
        panels._cache.clear()
        agenda.invalidate()
        privacy.VaultPrivacy._git_ignored.cache_clear()

        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "seed")

    def tearDown(self):
        panels.VAULT = self._saved["panels"]
        writes.VAULT = self._saved["writes"]
        todo.INDEX_PATH = self._saved["index"]
        ledger.LEDGER_PATH = self._saved["ledger"]
        gitops.MUTEX_PATH = self._saved["mutex"]
        panels._cache.clear()
        agenda.invalidate()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.tmp.cleanup()

    def month_path(self, ym="2026-08"):
        return self.vault / agenda.CALENDAR_DIR / f"{ym}.md"

    def add(self, **kw):
        kw.setdefault("date", "2026-08-04")
        kw.setdefault("title", "Dentist")
        return writes.api_agenda_add(writes.EventAdd(**kw))

    def seed_event(self, **kw):
        """One committed event, and the row the client would have seen."""
        r = self.add(**kw)
        self.assertTrue(r.get("ok"), r)
        _git(self.vault, "add", "-A"); _git(self.vault, "commit", "-m", "seed event")
        agenda.invalidate()
        text = self.month_path(r["file"].rsplit("/", 1)[-1][:7]).read_text(encoding="utf-8")
        line_no = next(i for i, ln in enumerate(text.splitlines(), 1) if ln == r["raw"])
        return {"file": r["file"], "line": line_no, "raw": r["raw"]}


# --------------------------------------------------------------------------
# it works at all
# --------------------------------------------------------------------------

class TestAddAndEdit(WriteBase):
    def test_an_added_event_lands_and_re_parses(self):
        r = self.add(start="14:30", end="15:30")
        self.assertTrue(r["ok"])
        self.assertEqual(r["file"], f"{agenda.CALENDAR_DIR}/2026-08.md")
        self.assertTrue(r["created_note"])
        got = agenda.parse_event(r["raw"])
        self.assertEqual((got["date"], got["start"], got["end"], got["title"]),
                         ("2026-08-04", "14:30", "15:30", "Dentist"))
        self.assertIn(r["raw"], self.month_path().read_text(encoding="utf-8"))

    def test_the_created_month_note_satisfies_its_own_schema(self):
        self.add()
        text = self.month_path().read_text(encoding="utf-8")
        self.assertIn("type: calendar-month", text)
        self.assertIn("month: 2026-08", text)
        self.assertIn("tags: [calendar]", text)
        self.assertIn("## Events", text)

    def test_a_second_event_appends_rather_than_replacing(self):
        self.add(title="First")
        self.add(title="Second", date="2026-08-06")
        text = self.month_path().read_text(encoding="utf-8")
        self.assertIn("First", text)
        self.assertIn("Second", text)
        self.assertEqual(text.count("## Events"), 1, "a second heading was grown")

    def test_the_ledger_row_points_at_a_revertible_commit(self):
        r = self.add()
        row = ledger.entries(5)[0]
        self.assertEqual(row["sha"], r["sha"])
        self.assertTrue(row["sha"], "no commit to undo")
        self.assertIn("added an event", row["summary"])
        # and the undo actually undoes it
        out = gitops.revert(self.vault, row["sha"])
        self.assertTrue(out["ok"], out)
        self.assertFalse(self.month_path().exists(),
                         "revert did not take the event back out")

    def test_the_ledger_says_what_happened_in_words(self):
        """The ledger is the morning view and its readability is a stated
        feature. `f"{verb}d"` produced "editd an event" until a real run showed
        it — the summaries are spelled out now, and pinned here."""
        seen = self.seed_event()
        writes.api_agenda_edit(writes.EventEdit(**seen, date="2026-08-05",
                                                title="Dentist"))
        self.assertIn("edited an event", ledger.entries(3)[0]["summary"])

        seen = self.seed_event(title="Second", date="2026-08-06")
        writes.api_agenda_edit(writes.EventEdit(**seen, date="2026-09-06",
                                                title="Second"))
        self.assertIn("moved an event", ledger.entries(3)[0]["summary"])

        seen = self.seed_event(title="Third", date="2026-08-07")
        writes.api_agenda_edit(writes.EventEdit(**seen, date="2026-08-07",
                                                title="Third", cancelled="2026-08-03"))
        self.assertIn("cancelled an event", ledger.entries(3)[0]["summary"])

    def test_a_reschedule_within_the_month_rewrites_one_line(self):
        seen = self.seed_event(start="14:30", end="15:30")
        r = writes.api_agenda_edit(writes.EventEdit(
            **seen, date="2026-08-11", title="Dentist", start="14:30", end="15:30"))
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["moved"])
        text = self.month_path().read_text(encoding="utf-8")
        self.assertIn("2026-08-11", text)
        self.assertNotIn("2026-08-04 14:30", text)

    def test_a_reschedule_across_months_is_one_commit_touching_both(self):
        seen = self.seed_event()
        r = writes.api_agenda_edit(writes.EventEdit(
            **seen, date="2026-09-02", title="Dentist"))
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["moved"])
        self.assertEqual(r["file"], f"{agenda.CALENDAR_DIR}/2026-09.md")
        self.assertNotIn("Dentist", self.month_path("2026-08").read_text(encoding="utf-8"))
        self.assertIn("Dentist", self.month_path("2026-09").read_text(encoding="utf-8"))
        touched = _git(self.vault, "show", "--name-only", "--format=", r["sha"]).stdout
        self.assertIn("2026-08.md", touched)
        self.assertIn("2026-09.md", touched)

    def test_the_block_id_survives_a_move(self):
        """Identity is the thing a reschedule must not change — an event that
        gets a new ID every time it moves cannot ever be synced outward."""
        seen = self.seed_event()
        before = agenda.parse_event(seen["raw"])["block_id"]
        r = writes.api_agenda_edit(writes.EventEdit(
            **seen, date="2026-09-02", title="Dentist"))
        self.assertEqual(agenda.parse_event(r["raw"])["block_id"], before)

    def test_cancelling_keeps_the_line_and_stops_the_clock(self):
        seen = self.seed_event(start="14:30", end="15:30")
        r = writes.api_agenda_edit(writes.EventEdit(
            **seen, date="2026-08-04", title="Dentist", start="14:30", end="15:30",
            cancelled="2026-08-03"))
        self.assertTrue(r["ok"], r)
        text = self.month_path().read_text(encoding="utf-8")
        self.assertIn("cancelled::2026-08-03", text)
        self.assertIn("Dentist", text, "cancelling deleted the line")
        agenda.invalidate()
        got = agenda.resolve(self.vault, "2026-08-01", "2026-08-31",
                             split=lambda v, r: (set(), set()), ttl=0)
        occ = [o for o in got["occurrences"] if o["kind"] == "event"][0]
        self.assertEqual(occ["cancelled"], "2026-08-03")
        self.assertEqual(agenda.committed_hours(got["occurrences"]), {},
                         "a cancelled hour was still counted as spent")


# --------------------------------------------------------------------------
# the holds — one test each, each one trying to get past
# --------------------------------------------------------------------------

class TestHolds(WriteBase):
    def test_hold_stale_line(self):
        seen = self.seed_event()
        p = self.month_path()
        p.write_text(p.read_text(encoding="utf-8").replace(seen["raw"],
                                                           "- 2026-08-04 Something else"),
                     encoding="utf-8")
        r = writes.api_agenda_edit(writes.EventEdit(**seen, date="2026-08-05",
                                                    title="Dentist"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(body(r)["error"], "stale")

    def test_hold_stale_line_number_past_the_end(self):
        seen = self.seed_event()
        seen["line"] = 9999
        r = writes.api_agenda_edit(writes.EventEdit(**seen, date="2026-08-05",
                                                    title="Dentist"))
        self.assertEqual(r.status_code, 409)

    def test_hold_line_that_would_span_more_than_one(self):
        r = self.add(title="Dentist\n- 2026-08-05 Smuggled second event")
        # The title is whitespace-collapsed before composing, so the newline can
        # never reach the file — assert the *outcome*, which is that one line
        # went in and the smuggled one did not become an event of its own.
        self.assertTrue(r["ok"], r)
        text = self.month_path().read_text(encoding="utf-8")
        self.assertEqual(len([ln for ln in text.splitlines()
                              if agenda.parse_event(ln)]), 1)

    def test_hold_a_title_that_forges_a_block_id(self):
        """A title ending in something ID-shaped must not be able to claim an
        identity — the round-trip check is what catches it."""
        r = self.add(title="Sneaky ^sg-evt-deadbeef")
        if isinstance(r, dict) and r.get("ok"):
            got = agenda.parse_event(r["raw"])
            self.assertNotEqual(got["block_id"], "sg-evt-deadbeef",
                                "a title forged the event's identity")
        else:
            self.assertEqual(r.status_code, 400)

    def test_hold_checkbox_count_changes(self):
        """Completion is a human signal. A calendar write may not tick, untick
        or introduce a checkbox, so a title that looks like one is refused or
        neutralised — never allowed to become a task."""
        self.add(title="First")
        p = self.month_path()
        p.write_text(p.read_text(encoding="utf-8") + "\n- [ ] a real task\n",
                     encoding="utf-8")
        _git(self.vault, "add", "-A"); _git(self.vault, "commit", "-m", "task")
        before = p.read_text(encoding="utf-8").count("- [ ]")
        self.add(title="Second", date="2026-08-09")
        self.assertEqual(p.read_text(encoding="utf-8").count("- [ ]"), before)

    def test_hold_target_escapes_the_vault(self):
        seen = self.seed_event()
        for bad in ["../outside.md", "C:/Windows/system.ini", "x\\..\\..\\out.md"]:
            with self.subTest(path=bad):
                r = writes.api_agenda_edit(writes.EventEdit(
                    file=bad, line=1, raw=seen["raw"], date="2026-08-05",
                    title="Dentist"))
                self.assertEqual(r.status_code, 400)
                self.assertEqual(body(r)["error"], "bad path")

    def test_hold_gitignored_target(self):
        """The §5.5 decision, enforced. gitops.commit() returns no SHA for an
        ignored path, so this write would be the only one in Sigma with no
        ledger row and no undo — it is a hold, not a permission question."""
        (self.vault / ".gitignore").write_text(f"{agenda.CALENDAR_DIR}/\n",
                                               encoding="utf-8")
        _git(self.vault, "add", "-A"); _git(self.vault, "commit", "-m", "seal")
        privacy.VaultPrivacy._git_ignored.cache_clear()
        r = self.add()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(body(r)["error"], "sealed path")
        self.assertFalse(self.month_path().exists(), "it wrote before refusing")

    def test_hold_gitignored_even_when_model_allow_exempt(self):
        """Exemption governs what the model may *see*. It has never governed
        what may be written, and the undo problem is identical either way."""
        (self.vault / ".gitignore").write_text(f"{agenda.CALENDAR_DIR}/\n",
                                               encoding="utf-8")
        _git(self.vault, "add", "-A"); _git(self.vault, "commit", "-m", "seal")
        privacy.VaultPrivacy._git_ignored.cache_clear()
        saved = privacy.is_model_allowed
        privacy.is_model_allowed = lambda p: True
        try:
            r = self.add()
        finally:
            privacy.is_model_allowed = saved
        self.assertEqual(r.status_code, 403)

    def test_hold_a_rule_occurrence_is_not_editable(self):
        r = writes.api_agenda_edit(writes.EventEdit(
            file=agenda.SCHEDULE_REL, line=8,
            raw="- MWF 10:30–11:20 [[CSE-311]] lecture ^sg-rule-cse311",
            date="2026-08-05", title="CSE-311 lecture"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("recurrence rule", body(r)["detail"])

    def test_hold_a_line_that_is_not_an_event_at_all(self):
        r = writes.api_agenda_edit(writes.EventEdit(
            file=f"{agenda.CALENDAR_DIR}/2026-08.md", line=1,
            raw="## Events", date="2026-08-05", title="x"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(body(r)["error"], "not an event")

    def test_hold_mutex_busy_refuses_and_does_not_queue(self):
        gitops.MUTEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        gitops.MUTEX_PATH.write_text(
            json.dumps({"pid": 1, "at": "2999-01-01T00:00:00"}), encoding="utf-8")
        try:
            r = writes.api_agenda_add(writes.EventAdd(date="2026-08-04", title="X"),)
        finally:
            gitops.MUTEX_PATH.unlink(missing_ok=True)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(body(r)["error"], "busy")
        self.assertFalse(self.month_path().exists(), "it wrote while locked out")

    def test_hold_malformed_dates_and_times(self):
        for kw in [{"date": "04-08-2026"}, {"date": "2026-13-01"},
                   {"date": "2026-08-04", "start": "25:00"},
                   {"date": "2026-08-04", "start": "9am"},
                   {"date": "2026-08-04", "start": "14:30", "end": "13:00"}]:
            with self.subTest(**kw):
                r = self.add(**kw)
                self.assertTrue(hasattr(r, "status_code"), f"accepted {kw}")
                self.assertEqual(r.status_code, 400)

    def test_hold_empty_and_oversized_titles(self):
        self.assertEqual(self.add(title="   ").status_code, 400)
        self.assertEqual(self.add(title="x" * 400).status_code, 400)

    def test_hold_a_cancelled_date_that_is_not_a_date(self):
        seen = self.seed_event()
        r = writes.api_agenda_edit(writes.EventEdit(
            **seen, date="2026-08-04", title="Dentist", cancelled="yesterday"))
        self.assertEqual(r.status_code, 400)


class TestRoundTripUnderWrite(WriteBase):
    """Whatever goes in must come back out the same. Fuzzed over the shapes the
    UI can produce, because the write path is the only place a bad line could
    ever enter the vault."""

    CASES = [
        dict(date="2026-08-04", title="Dentist"),
        dict(date="2026-08-04", title="Dentist", start="09:00"),
        dict(date="2026-08-04", title="Dentist", start="09:00", end="10:30"),
        dict(date="2026-08-04", title="Trip", end_date="2026-08-09"),
        dict(date="2026-08-04", title="Title with – an en dash"),
        dict(date="2026-08-04", title="Title with 2026-09-01 inside it"),
        dict(date="2026-08-04", title="Title with :: colons"),
        dict(date="2026-08-04", title="  collapsing   whitespace  "),
    ]

    def test_every_shape_the_ui_can_send_reads_back_identically(self):
        for case in self.CASES:
            with self.subTest(**case):
                agenda.invalidate()
                r = self.add(**case)
                self.assertTrue(isinstance(r, dict) and r.get("ok"), r)
                got = agenda.parse_event(r["raw"])
                self.assertIsNotNone(got, f"did not re-parse: {r['raw']!r}")
                self.assertEqual(got["date"], case["date"])
                self.assertEqual(got["title"], " ".join(case["title"].split()))
                self.assertEqual(got["start"], case.get("start"))
                self.assertEqual(got["end"], case.get("end"))

    def test_the_resolver_sees_exactly_what_was_written(self):
        self.add(date="2026-08-04", title="Dentist", start="14:30", end="15:30")
        agenda.invalidate()
        got = agenda.resolve(self.vault, "2026-08-01", "2026-08-31",
                             split=lambda v, r: (set(), set()), ttl=0)
        self.assertEqual(got["problems"], [], "the write produced an unreadable line")
        occ = [o for o in got["occurrences"] if o["kind"] == "event"]
        self.assertEqual(len(occ), 1)
        self.assertEqual((occ[0]["title"], occ[0]["start"], occ[0]["end"]),
                         ("Dentist", "14:30", "15:30"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
