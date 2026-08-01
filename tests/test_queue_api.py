"""
GET /api/queue, and the boundary it inherits.

todo.py is tested on its own in test_queue.py; what is defended here is the
wiring. Three things can go wrong at this seam and none of them are visible
from either side alone: the endpoint could grow its own copy of the privacy
split, the 15s cache could serve one section's answer to another, and
/api/tasks — which the calendar strip still reads — could be broken by the
queue's extra folder exclusion.
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

import panels                                      # noqa: E402
import privacy                                     # noqa: E402
import todo                                        # noqa: E402
import writes                                      # noqa: E402


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


class QueueApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir(parents=True)
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")

        # panels.VAULT and writes.VAULT are two module-level bindings to one
        # object; patching only the first left the toggle addressing the *real*
        # vault. It refused — the line-verification guard answered 409 stale
        # rather than writing to a line it had not been shown — but a test that
        # depends on a safety net catching it is not a hermetic test.
        self._saved = {"panels": panels.VAULT, "writes": writes.VAULT,
                       "index": todo.INDEX_PATH}
        panels.VAULT = writes.VAULT = self.vault
        todo.INDEX_PATH = Path(self.tmp.name) / "todo.state.json"
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()

    def tearDown(self):
        panels.VAULT = self._saved["panels"]
        writes.VAULT = self._saved["writes"]
        todo.INDEX_PATH = self._saved["index"]
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.tmp.cleanup()

    def note(self, rel: str, body: str):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p


class TestQueueEndpoint(QueueApiBase):
    def test_it_returns_all_four_sections_even_when_empty(self):
        """A missing section would make the UI render three cards and no
        explanation for the fourth."""
        q = panels.api_queue()
        self.assertEqual(sorted(q["sections"]), ["courses", "misc", "procertus", "projects"])
        self.assertEqual(q["counts"]["visible"], 0)

    def test_the_shape_the_work_view_reads(self):
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] push the repo 📅 2026-08-01 🔺\n")
        s = panels.api_queue()["sections"]["procertus"]
        self.assertEqual(s["kind"], "flat")
        self.assertIsNone(s["parent_noun"])
        t = s["visible"][0]
        for key in ("id", "file", "line", "raw", "text", "deadline", "urgency",
                    "created", "pinned", "score", "parts", "overdue", "no_sync"):
            self.assertIn(key, t)
        self.assertEqual(t["urgency"], "high")
        self.assertEqual(t["parts"]["urgency"], 30.0)

    def test_a_chain_section_names_what_its_window_counts(self):
        self.note("02-Areas/Academics/AA-210/aa-210.md",
                  "---\ntype: course-index\nstatus: active\n---\n")
        self.note("02-Areas/Academics/AA-210/timeline.md", "- [ ] day 2\n- [ ] day 3\n")
        s = panels.api_queue()["sections"]["courses"]
        self.assertEqual((s["kind"], s["parent_noun"], s["window"]), ("chain", "course", 1))
        self.assertEqual(len(s["visible"]), 1)
        self.assertEqual(len(s["blocked"]), 1)

    def test_the_endpoint_is_cached_and_the_cache_is_its_own(self):
        """Two 15s caches under one dict — a shared key would serve the task
        panel's payload to the work view."""
        self.note("00-Inbox/a.md", "- [ ] first 📅 2026-08-01\n")
        panels.api_queue()
        panels.api_tasks()
        self.assertIn("queue", panels._cache)
        self.assertIn("tasks", panels._cache)
        self.note("00-Inbox/a.md", "- [ ] first 📅 2026-08-01\n- [ ] second\n")
        self.assertEqual(panels.api_queue()["counts"]["visible"], 1)   # still cached
        panels._cache.clear()
        self.assertEqual(panels.api_queue()["counts"]["visible"], 2)


class TestBoundaryIsInherited(QueueApiBase):
    def setUp(self):
        super().setUp()
        self._allow = privacy.model_allow_prefixes
        privacy.model_allow_prefixes = lambda: ("02-areas/procertus",)

    def tearDown(self):
        privacy.model_allow_prefixes = self._allow
        super().tearDown()

    def test_sealed_material_does_not_reach_the_queue_endpoint(self):
        (self.vault / ".gitignore").write_text("02-Areas/Personal/\n", encoding="utf-8")
        self.note("02-Areas/Personal/private.md", "- [ ] nobody's business\n")
        self.note("00-Inbox/a.md", "- [ ] ordinary\n")
        texts = [t["text"] for t in panels.api_queue()["sections"]["misc"]["visible"]]
        self.assertEqual(texts, ["ordinary"])

    def test_model_exempt_material_is_marked_not_hidden(self):
        (self.vault / ".gitignore").write_text("02-Areas/ProCertus/\n", encoding="utf-8")
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] client material\n")
        t = panels.api_queue()["sections"]["procertus"]["visible"][0]
        self.assertTrue(t["no_sync"])


class TestTasksEndpointStillWorks(QueueApiBase):
    """The queue excludes 01-Daily; /api/tasks must not. The calendar strip is
    about dated work wherever it lives, and it reads /api/tasks."""

    def test_a_dated_daily_task_still_reaches_the_task_panel(self):
        self.note("01-Daily/2026-08-01.md", "- [ ] pay the fee 📅 2026-08-03\n")
        self.assertEqual([t["text"] for t in panels.api_tasks()["tasks"]], ["pay the fee"])
        self.assertEqual(panels.api_queue()["counts"]["visible"], 0)

    def test_the_task_panel_still_drops_undated_work(self):
        self.note("00-Inbox/a.md", "- [ ] undated\n- [ ] dated 📅 2026-08-03\n")
        self.assertEqual([t["text"] for t in panels.api_tasks()["tasks"]], ["dated"])
        # ...which is exactly what the queue exists to stop doing
        self.assertEqual(panels.api_queue()["counts"]["visible"], 2)

    def test_both_endpoints_agree_on_the_task_grammar(self):
        """One grammar, imported, not two copies that can drift."""
        self.assertIs(panels._TASK_RE, todo.TASK_RE)
        self.assertIs(panels._DUE_RE, todo.DUE_RE)
        self.assertIs(panels._META_RE, todo.META_RE)
        self.assertIs(panels._PRIORITY, todo.PRIORITY)


class TestCompletionPromotes(QueueApiBase):
    """Ticking a visible task must promote the next one *now*.

    The queue's whole claim is that it maintains itself, and a 15s stale cache
    would break that at exactly the moment it is meant to feel immediate — the
    row leaves, and the slot it vacated sits empty until the TTL rolls.
    """

    def setUp(self):
        super().setUp()
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "init")

    def toggle(self, t, done=True):
        """The endpoint returns a plain dict on success and a JSONResponse on
        refusal, so a failure must be read as a failure rather than duck-typed
        into one."""
        r = writes.api_toggle(writes.ToggleReq(
            file=t["file"], line=t["line"], raw=t["raw"], done=done))
        self.assertIsInstance(r, dict, f"toggle refused: {getattr(r, 'body', r)}")
        return r

    def test_ticking_drops_both_task_caches(self):
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] first\n- [ ] second\n")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "todo")

        first = panels.api_queue()["sections"]["procertus"]["visible"][0]
        panels.api_tasks()
        self.assertIn("queue", panels._cache)

        self.assertTrue(self.toggle(first).get("ok"))
        # Not merely expired — actively dropped, both of them.
        self.assertNotIn("queue", panels._cache)
        self.assertNotIn("tasks", panels._cache)

        after = panels.api_queue()["sections"]["procertus"]["visible"]
        self.assertEqual([t["text"] for t in after], ["second"])

    def test_the_tick_is_recorded_as_a_completion(self):
        """What the 06:00 review counts. Reading the ledger instead would miss
        every tick made in Obsidian rather than on the dashboard."""
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] first\n")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "todo")

        t = panels.api_queue()["sections"]["procertus"]["visible"][0]
        self.toggle(t)
        panels.api_queue()
        entry = json.loads(todo.INDEX_PATH.read_text(encoding="utf-8"))["tasks"][t["id"]]
        self.assertEqual(entry["completed_at"], todo.datetime.date.today().isoformat())

    def test_unticking_puts_it_back_at_the_head_of_its_chain(self):
        self.note("02-Areas/Academics/AA-210/timeline.md", "- [ ] day 2\n- [ ] day 3\n")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "timeline")

        head = panels.api_queue()["sections"]["courses"]["visible"][0]
        self.toggle(head)
        self.assertEqual(
            [t["text"] for t in panels.api_queue()["sections"]["courses"]["visible"]],
            ["day 3"])

        # The undo path: the same endpoint, done=False.
        back = self.toggle({**head, "raw": head["raw"].replace("[ ]", "[x]")}, done=False)
        self.assertTrue(back.get("ok"))
        s = panels.api_queue()["sections"]["courses"]
        self.assertEqual([t["text"] for t in s["visible"]], ["day 2"])
        self.assertEqual([t["text"] for t in s["blocked"]], ["day 3"])


class TestSerialisable(QueueApiBase):
    def test_the_payload_survives_json(self):
        """FastAPI will serialise this; a stray set or Path fails at runtime in
        the browser rather than here."""
        self.note("02-Areas/ProCertus/Todo.md", "- [ ] a 📅 2026-08-02\n")
        self.note("02-Areas/Academics/AA-210/timeline.md", "## Block 1\n- [ ] b\n- [ ] c\n")
        blob = json.dumps(panels.api_queue())
        self.assertIn("procertus", blob)
        self.assertIn("Block 1", blob)


if __name__ == "__main__":
    unittest.main()
