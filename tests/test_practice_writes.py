#!/usr/bin/env python3
"""
The practice engine's write half (study S2+S3): POST /api/tasks/skip,
/api/lesson/state, /api/lesson/attempt, /api/lesson/session-end.

Three weights, three defences. The skip is a line edit and gets the toggle's
whole discipline — staleness, sealed paths, a commit and a ledger row. The
state and attempt writes are sidecar-only and must never touch git. The
rollup is the one durable write: its row must carry the digest, land as one
commit, and be idempotent through the watermark — the close handler and the
explicit button can both fire, and firing twice must write once.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI                    # noqa: E402
from fastapi.testclient import TestClient      # noqa: E402

import lesson as ln                            # noqa: E402
import panels                                  # noqa: E402
import privacy                                 # noqa: E402
import todo as td                              # noqa: E402
import writes                                  # noqa: E402
from sigma import gitops, ledger               # noqa: E402
from test_checkpoint import _valid_cp          # noqa: E402
from test_lesson import _valid                 # noqa: E402


def _run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8")


CHAIN = (
    "---\ntype: guide\ncourse: TEST-101\ntags: [guide]\n---\n\n"
    "## Modules\n\n"
    "- [ ] M01 · [[test-101-m01-test-module|Test module]]\n"
    "- [ ] M02 · [[test-101-m02-later|Later]]\n"
)
CHAIN_ROW = "- [ ] M01 · [[test-101-m01-test-module|Test module]]"

STUDY_LOG = (
    "---\ntype: study-log\ncourse: TEST-101\ntags: [guide]\n---\n\n"
    "# TEST-101 — study log\n\n## Sessions\n"
)


class PracticeWritesBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.course / "lectures").mkdir()
        for n in (1, 2, 3):
            (self.course / "lectures" / f"l{n}.md").write_text(
                "# src\n", encoding="utf-8")
        (self.course / "guide" / "test-101-m01-test-module.md").write_text(
            _valid(), encoding="utf-8")
        (self.course / "guide" / "test-101-checkpoint-1.md").write_text(
            _valid_cp(), encoding="utf-8")
        (self.course / "test-101-guide.md").write_text(CHAIN, encoding="utf-8")
        (self.course / "test-101-study-log.md").write_text(
            STUDY_LOG, encoding="utf-8")
        (self.vault / ".gitignore").write_text("sealed/\n", encoding="utf-8")
        (self.vault / "sealed").mkdir()
        (self.vault / "sealed" / "s.md").write_text("- [ ] hidden\n",
                                                    encoding="utf-8")
        _run(self.vault, "init", "-b", "master")
        _run(self.vault, "config", "user.name", "Test")
        _run(self.vault, "config", "user.email", "t@example.com")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "seed")

        self._saved = (writes.VAULT, panels.VAULT, gitops.MUTEX_PATH,
                       ledger.LEDGER_PATH, ln.STATE_PATH, ln.ATTEMPTS_PATH,
                       td.INDEX_PATH)
        writes.VAULT = self.vault
        panels.VAULT = self.vault          # writes leans on panels' split
        gitops.MUTEX_PATH = root / "git.lock"
        ledger.LEDGER_PATH = root / "ledger.jsonl"
        ln.STATE_PATH = root / "study.state.json"
        ln.ATTEMPTS_PATH = root / "study.jsonl"
        # Since S8 the rollup snoozes freshly-raised recall cards through the
        # queue's sidecar, so this suite now writes there too — and a test that
        # wrote the developer's real task index would age or defer real tasks.
        td.INDEX_PATH = root / "todo.state.json"
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()

        app = FastAPI()
        app.include_router(writes.router)
        app.include_router(panels.router)
        self.client = TestClient(app)

    def tearDown(self):
        (writes.VAULT, panels.VAULT, gitops.MUTEX_PATH,
         ledger.LEDGER_PATH, ln.STATE_PATH, ln.ATTEMPTS_PATH,
         td.INDEX_PATH) = self._saved
        panels._cache.clear()
        self.tmp.cleanup()

    def skip(self, **over):
        body = {"file": "02-Areas/Academics/TEST-101/test-101-guide.md",
                "line": 9, "raw": CHAIN_ROW}
        body.update(over)
        return self.client.post("/api/tasks/skip", json=body)

    def attempt(self, **over):
        body = {"course": "TEST-101", "module": 1, "qid": "q-1-1",
                "result": "correct", "hints": 0, "revealed": False}
        body.update(over)
        return self.client.post("/api/lesson/attempt", json=body)


class TestSkip(PracticeWritesBase):
    def test_skip_flips_marks_commits_and_ledgers(self):
        r = self.skip()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertIsNotNone(body["sha"])
        text = (self.course / "test-101-guide.md").read_text(encoding="utf-8")
        self.assertIn("- [-] M01 · skipped::", text)
        self.assertIn("skipped::", body["raw"])
        self.assertLess(text.index("skipped::"), text.index("[[test-101-m01"))
        self.assertIn("- [ ] M02", text)               # only the named row moved
        subject = _run(self.vault, "log", "-1", "--format=%s").stdout
        self.assertIn("skip task", subject)
        led = self.client.get("/api/activity").json()["entries"]
        self.assertEqual(led[0]["action"], "skip")
        self.assertEqual(led[0]["actor"], "zach")

    def test_a_stale_line_is_refused(self):
        r = self.skip(raw=CHAIN_ROW + " (edited elsewhere)")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "stale")

    def test_a_done_row_cannot_be_skipped(self):
        text = (self.course / "test-101-guide.md").read_text(encoding="utf-8")
        (self.course / "test-101-guide.md").write_text(
            text.replace("- [ ] M01", "- [x] M01"), encoding="utf-8")
        r = self.skip(raw=CHAIN_ROW.replace("- [ ]", "- [x]"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "not an open task")

    def test_a_sealed_path_is_refused(self):
        r = self.skip(file="sealed/s.md", line=1, raw="- [ ] hidden")
        self.assertEqual(r.status_code, 403)

    def test_a_model_exempt_gitignored_path_is_still_refused(self):
        """Exemption governs what the model may see, never what may be
        written: a skip committed nowhere would be the only ledger row in
        Sigma with no undo behind it. The pre-mutex sealed_paths() check
        passes exempted paths — the inside-the-mutex check must not."""
        saved = privacy.model_allow_prefixes
        privacy.model_allow_prefixes = lambda: ("sealed",)
        try:
            r = self.skip(file="sealed/s.md", line=1, raw="- [ ] hidden")
            self.assertEqual(r.status_code, 403)
            self.assertIn("- [ ] hidden",
                          (self.vault / "sealed" / "s.md")
                          .read_text(encoding="utf-8"))
        finally:
            privacy.model_allow_prefixes = saved

    def test_the_skip_is_revertible_from_the_ledger(self):
        sha = self.skip().json()["sha"]
        r = self.client.post("/api/activity/revert", json={"sha": sha})
        self.assertEqual(r.status_code, 200, r.text)
        text = (self.course / "test-101-guide.md").read_text(encoding="utf-8")
        self.assertIn(CHAIN_ROW, text)
        self.assertNotIn("skipped::", text)


class TestState(PracticeWritesBase):
    def test_state_round_trips_through_the_sidecar_and_the_lesson(self):
        st = {"depth": {"1": "in_depth"}, "practice": {"q-1-1": {"hints": 1}}}
        r = self.client.post("/api/lesson/state",
                             json={"course": "TEST-101", "module": 1, "state": st})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(ln.load_state()["modules"]["TEST-101/1"], st)
        d = self.client.get("/api/lesson/test-101/1").json()
        self.assertEqual(d["state"], st)

    def test_state_never_touches_git(self):
        before = _run(self.vault, "rev-parse", "HEAD").stdout
        self.client.post("/api/lesson/state",
                         json={"course": "TEST-101", "module": 1, "state": {}})
        self.assertEqual(_run(self.vault, "rev-parse", "HEAD").stdout, before)

    def test_an_oversized_state_is_refused(self):
        r = self.client.post("/api/lesson/state",
                             json={"course": "TEST-101", "module": 1,
                                   "state": {"blob": "x" * 30_000}})
        self.assertEqual(r.status_code, 413)


class TestAttempt(PracticeWritesBase):
    def test_an_attempt_lands_with_derived_identity(self):
        r = self.attempt(result="wrong", hints=1, answer="3")
        self.assertEqual(r.status_code, 200, r.text)
        rows = [json.loads(x) for x in
                Path(ln.ATTEMPTS_PATH).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["qid"], "q-1-1")
        self.assertEqual(row["kind"], "numeric")       # from the module, not the client
        self.assertEqual(row["seg_title"], "Topic 1")
        self.assertEqual(row["qhash"], ln.qhash("What is 1 plus one?"))
        self.assertEqual(row["result"], "wrong")
        self.assertEqual(row["answer"], "3")

    def test_unknown_question_module_and_result_are_refused(self):
        self.assertEqual(self.attempt(qid="q-1-99").status_code, 404)
        self.assertEqual(self.attempt(module=9).status_code, 404)
        self.assertEqual(self.attempt(result="maybe").status_code, 400)

    def test_an_attempt_never_touches_git(self):
        before = _run(self.vault, "rev-parse", "HEAD").stdout
        self.attempt()
        self.assertEqual(_run(self.vault, "rev-parse", "HEAD").stdout, before)


class TestCheckpointPractice(PracticeWritesBase):
    """Checkpoint attempts share the one attempt log and the one state
    sidecar (study S6) — a `cp<n>` key and a `checkpoint` field, never a
    second mechanism."""

    def test_a_checkpoint_attempt_lands_with_derived_identity(self):
        r = self.attempt(module=None, checkpoint=1, qid="q-cp1-1",
                         result="wrong")
        self.assertEqual(r.status_code, 200, r.text)
        row = json.loads(Path(ln.ATTEMPTS_PATH)
                         .read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["checkpoint"], 1)
        self.assertIsNone(row["module"])
        self.assertEqual(row["kind"], "numeric")     # from the note, not the client
        self.assertEqual(row["seg_title"], "Topic A")
        self.assertEqual(row["qhash"], ln.qhash("What is 1 plus one?"))

    def test_naming_both_or_neither_unit_is_refused(self):
        self.assertEqual(self.attempt(checkpoint=1).status_code, 400)
        self.assertEqual(self.attempt(module=None).status_code, 400)
        r = self.client.post("/api/lesson/state",
                             json={"course": "TEST-101", "state": {}})
        self.assertEqual(r.status_code, 400)

    def test_an_unknown_checkpoint_is_a_404(self):
        r = self.attempt(module=None, checkpoint=9, qid="q-cp9-1")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "no such checkpoint")

    def test_checkpoint_state_keys_under_cp(self):
        st = {"practice": {"q-cp1-1": {"revealed": True}}}
        r = self.client.post("/api/lesson/state",
                             json={"course": "TEST-101", "checkpoint": 1,
                                   "state": st})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(ln.load_state()["modules"]["TEST-101/cp1"], st)
        d = self.client.get("/api/checkpoint/test-101/1").json()
        self.assertEqual(d["state"], st)

    def test_the_rollup_labels_modules_and_checkpoints_together(self):
        self.attempt(qid="q-1-1", result="correct")
        self.attempt(module=None, checkpoint=1, qid="q-cp1-2", result="wrong")
        r = self.client.post("/api/lesson/session-end",
                             json={"course": "TEST-101"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["wrote"])
        self.assertIn("· M01+CP1 ·", body["raw"])
        self.assertEqual(body["digest"], "Topic A ×1")

    def test_a_checkpoint_only_session_labels_cp_not_a_question_mark(self):
        """The normal way a checkpoint is used — no module rows at all. The
        durable study-log row must say which unit was studied, not 'M?'."""
        self.attempt(module=None, checkpoint=1, qid="q-cp1-1", result="correct")
        body = self.client.post("/api/lesson/session-end",
                                json={"course": "TEST-101"}).json()
        self.assertTrue(body["wrote"])
        self.assertIn("· CP1 ·", body["raw"])
        self.assertNotIn("M?", body["raw"])

    def test_practice_writes_drop_the_study_panel_cache(self):
        """/api/study renders the attempt log and the study log now — a 60s
        stale 'nothing recorded yet' right after a recorded miss is the
        write-then-stale-panel bug the task caches exist for."""
        panels._cache["study"] = (0, {"stale": True})
        self.attempt(result="wrong")
        self.assertNotIn("study", panels._cache)
        panels._cache["study"] = (0, {"stale": True})
        body = self.client.post("/api/lesson/session-end",
                                json={"course": "TEST-101"}).json()
        self.assertTrue(body["wrote"])
        self.assertNotIn("study", panels._cache)


class TestSessionEnd(PracticeWritesBase):
    def end(self):
        return self.client.post("/api/lesson/session-end",
                                json={"course": "TEST-101"})

    def test_the_rollup_lands_as_one_commit_with_the_digest(self):
        self.attempt(qid="q-1-1", result="correct")
        self.attempt(qid="q-1-2", result="correct")
        self.attempt(qid="q-1-4", result="wrong")      # segment 2: "Topic 2"
        self.attempt(qid="q-1-7", result="skipped")
        r = self.end()
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["wrote"])
        self.assertEqual(body["rows"], 4)
        self.assertEqual(body["digest"], "Topic 2 ×1")
        text = (self.course / "test-101-study-log.md").read_text(encoding="utf-8")
        self.assertIn("· M01 · 3 answered, 2 correct · skipped 1 · missed 1 "
                      "· Topic 2 ×1", text)
        self.assertLess(text.index("## Sessions"), text.index("· M01 ·"))
        subject = _run(self.vault, "log", "-1", "--format=%s").stdout
        self.assertIn("study session rollup", subject)
        led = self.client.get("/api/activity").json()["entries"]
        self.assertIn("1 missed", led[0]["summary"])

    def test_firing_twice_writes_once(self):
        self.attempt()
        first = self.end().json()
        self.assertTrue(first["wrote"])
        second = self.end().json()
        self.assertFalse(second["wrote"])
        self.assertEqual(second["rows"], 0)
        text = (self.course / "test-101-study-log.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("1 answered"), 1)

    def test_nothing_to_cover_is_a_clean_no_write(self):
        before = _run(self.vault, "rev-parse", "HEAD").stdout
        r = self.end()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["wrote"])
        self.assertEqual(_run(self.vault, "rev-parse", "HEAD").stdout, before)

    def test_a_missing_study_log_is_named_not_created(self):
        (self.course / "test-101-study-log.md").unlink()
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "drop log")
        self.attempt()
        r = self.end()
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"], "no study log note")

    def test_a_failed_session_stays_uncovered_for_the_next_end(self):
        """The watermark advances only past covered rows: attempts made after
        one rollup are the next session, not lost history."""
        self.attempt(qid="q-1-1")
        self.end()
        self.attempt(qid="q-1-2", result="wrong")
        r = self.end().json()
        self.assertTrue(r["wrote"])
        self.assertEqual(r["digest"], "Topic 1 ×1")

    def test_a_lost_watermark_is_repaired_from_the_note_not_rewritten(self):
        """The content half of the idempotency: a watermark that failed to
        save (or a race past the same read) must not mean the same digest row
        twice. The next call finds the identical row in the note, advances
        the watermark, and writes nothing."""
        self.attempt(qid="q-1-4", result="wrong")
        first = self.end().json()
        self.assertTrue(first["wrote"])
        st = ln.load_state()
        st["rollup"] = {}                      # simulate the lost save
        self.assertTrue(ln.save_state(st))
        second = self.end().json()
        self.assertFalse(second["wrote"])
        text = (self.course / "test-101-study-log.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("Topic 2 ×1"), 1)
        # and the watermark is back — a third call is the cheap path again
        self.assertTrue((ln.load_state()["rollup"] or {}).get("TEST-101"))

    def test_an_unwritable_sidecar_is_a_warning_never_a_duplicate(self):
        """save_state failing must be said out loud, and the note-side check
        keeps even repeated calls to one row while it stays unwritable."""
        self.attempt(qid="q-1-4", result="wrong")
        good = ln.STATE_PATH
        broken = Path(self.tmp.name) / "state-as-dir"
        broken.mkdir()
        ln.STATE_PATH = broken                 # save_state -> OSError -> False
        try:
            first = self.end().json()
            self.assertTrue(first["wrote"])
            self.assertIn("warning", first)
            second = self.end().json()
            self.assertFalse(second["wrote"])
            text = (self.course / "test-101-study-log.md").read_text(encoding="utf-8")
            self.assertEqual(text.count("Topic 2 ×1"), 1)
        finally:
            ln.STATE_PATH = good


if __name__ == "__main__":
    unittest.main()
