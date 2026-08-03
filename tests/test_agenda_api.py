"""
GET /api/agenda, and the seam /api/tasks now sits behind.

agenda.py is tested on its own in test_agenda.py; what is defended here is the
wiring, and three things can go wrong at it that are invisible from either side.

The endpoint could grow **its own copy of the privacy split** — the failure that
already happened once between panels.py and todo.py, which is why `_split` is a
single function passed as an argument rather than a boundary each module
reimplements.

`/api/tasks` could **change shape while changing engine**. It feeds the Today
panel and the 14-day strip; P2 replaces what computes it and deliberately not
what it looks like, so the contract is pinned here rather than trusted.

And the two caches could **come apart**. Since P2 there are two behind one
panel — the panel's own TTL and the resolver's — and a write that drops only the
first hands back the task that was just ticked from the second. That is the
exact 15-second lag TASK_PANELS was named to prevent, one layer down.
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
from sigma import ledger                           # noqa: E402


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


class AgendaApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")

        self._saved = {"panels": panels.VAULT, "writes": writes.VAULT,
                       "index": todo.INDEX_PATH, "ledger": ledger.LEDGER_PATH}
        panels.VAULT = writes.VAULT = self.vault
        todo.INDEX_PATH = Path(self.tmp.name) / "todo.state.json"
        ledger.LEDGER_PATH = Path(self.tmp.name) / "ledger.jsonl"
        panels._cache.clear()
        agenda.invalidate()
        privacy.VaultPrivacy._git_ignored.cache_clear()

    def tearDown(self):
        panels.VAULT = self._saved["panels"]
        writes.VAULT = self._saved["writes"]
        todo.INDEX_PATH = self._saved["index"]
        ledger.LEDGER_PATH = self._saved["ledger"]
        panels._cache.clear()
        agenda.invalidate()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.tmp.cleanup()

    def note(self, rel: str, body: str):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def month(self, *lines):
        return self.note(
            f"{agenda.CALENDAR_DIR}/2026-08.md",
            "---\ntype: calendar-month\nmonth: 2026-08\ntags: [calendar]\n---\n\n"
            "## Events\n" + "\n".join(lines) + "\n")


class TestAgendaEndpoint(AgendaApiBase):
    def test_the_shape_the_calendar_will_read(self):
        self.month("- 2026-08-04 14:30–15:30 Dentist ^sg-evt-8f2a1c04")
        got = panels.api_agenda(frm="2026-08-01", to="2026-08-31")
        for key in ("from", "to", "today", "occurrences", "conflicts",
                    "problems", "timezone"):
            self.assertIn(key, got)
        o = got["occurrences"][0]
        for key in ("kind", "id", "date", "start", "end", "all_day", "title",
                    "owner", "source", "span", "section", "parent", "no_sync",
                    "conflict", "raw", "priority"):
            self.assertIn(key, o)
        self.assertEqual(o["source"]["path"], f"{agenda.CALENDAR_DIR}/2026-08.md")

    def test_it_is_json_serialisable(self):
        """FastAPI will serialise this; a set or a Path in the payload is a 500
        that no unit test on the resolver alone would ever catch."""
        self.month("- 2026-08-04 Dentist ^sg-evt-8f2a1c04")
        json.dumps(panels.api_agenda(frm="2026-08-01", to="2026-08-31"))

    def test_defaults_to_a_fourteen_day_window_from_today(self):
        got = panels.api_agenda()
        self.assertEqual(got["from"], got["today"])
        span = (__import__("datetime").date.fromisoformat(got["to"])
                - __import__("datetime").date.fromisoformat(got["from"])).days
        self.assertEqual(span, panels.AGENDA_DEFAULT_DAYS - 1)

    def test_a_bad_date_is_a_400_not_an_empty_calendar(self):
        for bad in [{"frm": "not-a-date"}, {"frm": "2026-08-01", "to": "31-08-2026"}]:
            with self.subTest(**bad):
                r = panels.api_agenda(**bad)
                self.assertEqual(r.status_code, 400)

    def test_an_inverted_range_is_a_400(self):
        r = panels.api_agenda(frm="2026-08-31", to="2026-08-01")
        self.assertEqual(r.status_code, 400)
        self.assertIn("bad range", json.loads(bytes(r.body).decode())["error"])

    def test_an_absurd_range_is_refused_rather_than_expanded(self):
        """Expansion is per-day. Without this guard a client asking for a
        century walks 36,500 days per recurrence rule and never answers."""
        r = panels.api_agenda(frm="1900-01-01", to="2999-12-31")
        self.assertEqual(r.status_code, 400)
        self.assertIn("too wide", json.loads(bytes(r.body).decode())["error"])

    def test_an_empty_window_is_an_empty_list_not_an_error(self):
        got = panels.api_agenda(frm="2030-01-01", to="2030-01-07")
        self.assertEqual(got["occurrences"], [])
        self.assertNotIn("error", got)


class TestBoundary(AgendaApiBase):
    """The privacy split is inherited, never reimplemented."""

    def seal(self, *patterns):
        (self.vault / ".gitignore").write_text("\n".join(patterns) + "\n",
                                               encoding="utf-8")
        privacy.VaultPrivacy._git_ignored.cache_clear()
        panels._cache.clear()
        agenda.invalidate()

    def test_a_sealed_note_never_reaches_the_response(self):
        self.note("02-Areas/Secret/plan.md",
                  "---\ntype: resource\ndate: 2026-08-05\ntags: [resource]\n---\n\n"
                  "# Sealed thing\n")
        self.seal("02-Areas/Secret/")
        got = panels.api_agenda(frm="2026-08-01", to="2026-08-31")
        self.assertEqual(got["occurrences"], [])

    def test_the_endpoint_has_no_second_copy_of_the_boundary(self):
        """If it grew one, patching the single implementation would stop
        working — which is how the drift between panels.py and todo.py was
        found the first time."""
        calls = []
        real = panels._split

        def spy(rels):
            calls.append(len(rels))
            return real(rels)

        panels._split = spy
        try:
            self.month("- 2026-08-04 Dentist ^sg-evt-8f2a1c04")
            panels.api_agenda(frm="2026-08-01", to="2026-08-31")
        finally:
            panels._split = real
        self.assertTrue(calls, "the resolver did not use the panel's splitter")


class TestTasksFoldedIn(AgendaApiBase):
    """/api/tasks changed engine at P2 and deliberately not shape."""

    def test_the_shape_the_today_panel_reads(self):
        self.note("03-Projects/thing.md",
                  "---\ntype: project\ntags: [project]\n---\n\n"
                  "# Thing\n\n- [ ] Ship it 📅 2026-08-07 🔺\n")
        got = panels.api_tasks()
        self.assertEqual(sorted(got), ["block", "tasks", "today"])
        t = got["tasks"][0]
        self.assertEqual(sorted(t), ["due", "file", "line", "no_sync",
                                     "overdue", "priority", "raw", "text"])
        self.assertEqual(t["text"], "Ship it")
        self.assertEqual(t["due"], "2026-08-07")
        self.assertEqual(t["priority"], 4)
        self.assertEqual(t["raw"], "- [ ] Ship it 📅 2026-08-07 🔺")
        self.assertEqual(t["line"], 8)          # 1-based, past the frontmatter

    def test_undated_and_done_tasks_stay_out(self):
        self.note("03-Projects/thing.md",
                  "---\ntype: project\ntags: [project]\n---\n\n# Thing\n\n"
                  "- [ ] no date here\n"
                  "- [x] finished 📅 2026-08-07\n"
                  "- [ ] real 📅 2026-08-08\n")
        self.assertEqual([t["text"] for t in panels.api_tasks()["tasks"]], ["real"])

    def test_a_dated_task_in_a_daily_note_still_shows(self):
        """The one folder the queue and the calendar disagree about. A daily
        note's `date:` is journal, but a dated checkbox inside one is work, and
        this panel has always shown it — folding it behind the resolver must not
        quietly drop it."""
        self.note("01-Daily/2026-08-07.md",
                  "---\ntype: daily\ndate: 2026-08-07\ntags: [daily]\n---\n\n"
                  "- [ ] call the clinic 📅 2026-08-07\n")
        self.assertIn("call the clinic",
                      [t["text"] for t in panels.api_tasks()["tasks"]])

    def test_a_daily_notes_own_date_is_not_an_occurrence(self):
        """...and the other half of the same asymmetry: seven daily notes must
        not become seven entries on the calendar."""
        self.note("01-Daily/2026-08-07.md",
                  "---\ntype: daily\ndate: 2026-08-07\ntags: [daily]\n---\n\n"
                  "- [ ] call the clinic 📅 2026-08-07\n")
        got = panels.api_agenda(frm="2026-08-01", to="2026-08-31")
        kinds = {o["kind"] for o in got["occurrences"]}
        self.assertEqual(kinds, {"task"})

    def test_tasks_and_agenda_agree_about_the_same_task(self):
        """The whole point of one resolver: these two endpoints cannot disagree
        about what is due, because they are the same scan."""
        self.note("03-Projects/thing.md",
                  "---\ntype: project\ntags: [project]\n---\n\n# Thing\n\n"
                  "- [ ] Ship it 📅 2026-08-07\n")
        panel = {(t["text"], t["due"]) for t in panels.api_tasks()["tasks"]}
        cal = {(o["title"], o["date"]) for o in
               panels.api_agenda(frm="2026-08-01", to="2026-08-31")["occurrences"]
               if o["kind"] == "task"}
        self.assertEqual(panel, cal)


class TestCacheCoupling(AgendaApiBase):
    def test_a_tick_does_not_come_back_from_the_resolvers_cache(self):
        """The regression P2 could have introduced. /api/tasks is now a
        projection of a cached scan; dropping the panel key alone recomputes the
        projection from a scan that still has the ticked task in it."""
        self.note("03-Projects/thing.md",
                  "---\ntype: project\ntags: [project]\n---\n\n# Thing\n\n"
                  "- [ ] Ship it 📅 2026-08-07\n")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "seed")

        before = panels.api_tasks()["tasks"]
        self.assertEqual(len(before), 1)
        row = before[0]

        r = writes.api_toggle(writes.ToggleReq(file=row["file"], line=row["line"],
                                               raw=row["raw"], done=True))
        self.assertTrue(getattr(r, "status_code", 200) == 200, getattr(r, "body", r))
        self.assertEqual(panels.api_tasks()["tasks"], [],
                         "the ticked task came back from the resolver's cache")

    def test_drop_task_caches_clears_both_layers(self):
        self.month("- 2026-08-04 Dentist ^sg-evt-8f2a1c04")
        panels.api_agenda(frm="2026-08-01", to="2026-08-31")
        panels.api_tasks()
        self.assertTrue(agenda._CACHE, "the resolver cached nothing")
        panels.drop_task_caches()
        self.assertFalse(agenda._CACHE, "the resolver's scan survived")
        self.assertNotIn("tasks", panels._cache)


if __name__ == "__main__":
    unittest.main(verbosity=2)
