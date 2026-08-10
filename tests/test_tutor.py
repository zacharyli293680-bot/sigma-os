#!/usr/bin/env python3
"""
The tutor's backend half (study S5): the pinned context block and the window
hold.

What is testable without a model is exactly what must not regress: the context
the backend assembles (course, module or checkpoint, segment, the exact item
with its reference answer, Zach's attempt state, recent misses) and the two
refusal layers (the window hold at POST — the palette's own policy, inherited —
and a pin that names something that does not exist). The streaming loop itself
is /api/ask's, already exercised live; a test that opened a real SDK session
would spend the rate-limit window to prove a code path the drive proves.
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

from fastapi.testclient import TestClient    # noqa: E402

import app as appmod                          # noqa: E402
import lesson as ln                           # noqa: E402
import panels                                 # noqa: E402
import privacy                                # noqa: E402
from test_checkpoint import _valid_cp         # noqa: E402
from test_lesson import _valid                # noqa: E402


def _git(cwd: Path, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


class TutorBase(unittest.TestCase):
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
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")

        self._saved = (appmod.VAULT, panels.VAULT, ln.ATTEMPTS_PATH,
                       appmod.commands_mod._window_hold)
        appmod.VAULT = self.vault
        panels.VAULT = self.vault
        ln.ATTEMPTS_PATH = root / "study.jsonl"
        panels._cache.clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.client = TestClient(appmod.app)

    def tearDown(self):
        (appmod.VAULT, panels.VAULT, ln.ATTEMPTS_PATH,
         appmod.commands_mod._window_hold) = self._saved
        panels._cache.clear()
        self.tmp.cleanup()

    def ask(self, **over):
        body = {"course": "TEST-101", "module": 1, "question": "help"}
        body.update(over)
        return appmod.TutorAsk(**body)


class TestContext(TutorBase):
    def test_the_module_pin_names_course_note_and_unit(self):
        ctx, err = appmod._tutor_context(self.ask())
        self.assertIsNone(err)
        self.assertIn("TEST-101 · module M01", ctx)
        self.assertIn("guide/test-101-m01-test-module.md", ctx)
        self.assertIn("module sources:", ctx)

    def test_the_segment_and_item_pin_carry_the_reference_material(self):
        ctx, _ = appmod._tutor_context(self.ask(
            seg=1, qid="q-1-1", given="3", hints=1, revealed=False))
        self.assertIn("open segment: S1 · Topic 1", ctx)
        self.assertIn("pinned practice item q-1-1", ctx)
        self.assertIn("What is 1 plus one?", ctx)
        self.assertIn("reference answer: 2", ctx)
        self.assertIn("hints opened: 1 of", ctx)
        self.assertIn("Zach's current attempt: 3", ctx)

    def test_an_unknown_qid_pins_the_module_without_an_item(self):
        ctx, err = appmod._tutor_context(self.ask(qid="q-9-9"))
        self.assertIsNone(err)
        self.assertNotIn("pinned practice item", ctx)

    def test_recent_misses_ride_in_from_the_attempt_log(self):
        rows = [{"ts": "2026-08-09T10:00:00", "course": "TEST-101",
                 "qid": "q-1-4", "seg_title": "Topic 2", "result": "wrong"},
                {"ts": "2026-08-09T10:01:00", "course": "TEST-101",
                 "qid": "q-1-5", "seg_title": "Topic 2", "result": "wrong"}]
        Path(ln.ATTEMPTS_PATH).write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        ctx, _ = appmod._tutor_context(self.ask())
        self.assertIn("recent misses in TEST-101", ctx)
        self.assertIn("Topic 2 ×2", ctx)

    def test_no_attempts_means_no_misses_line_not_an_empty_one(self):
        ctx, _ = appmod._tutor_context(self.ask())
        self.assertNotIn("recent misses", ctx)

    def test_a_checkpoint_pins_as_cp(self):
        ctx, err = appmod._tutor_context(self.ask(module=None, checkpoint=1,
                                                  qid="q-cp1-1"))
        self.assertIsNone(err)
        self.assertIn("checkpoint CP1", ctx)
        self.assertIn("pinned practice item q-cp1-1", ctx)

    def test_a_confused_or_missing_unit_is_an_error(self):
        self.assertIsNotNone(appmod._tutor_context(self.ask(checkpoint=1))[1])
        self.assertIsNotNone(
            appmod._tutor_context(self.ask(module=None))[1])
        _, err = appmod._tutor_context(self.ask(module=9))
        self.assertIn("no such module", err)
        _, err = appmod._tutor_context(self.ask(module=None, checkpoint=9))
        self.assertIn("no such checkpoint", err)


class TestHoldAndRefusals(TutorBase):
    def test_the_hold_is_reported_and_enforced_at_post(self):
        appmod.commands_mod._window_hold = lambda: "reserved for the 09:00 fleet run"
        r = self.client.get("/api/tutor/hold")
        self.assertEqual(r.json()["hold"], "reserved for the 09:00 fleet run")
        r = self.client.post("/api/tutor", json={
            "course": "TEST-101", "module": 1, "question": "hi"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "window")
        self.assertIn("reserved", r.json()["reason"])

    def test_no_hold_reads_null(self):
        appmod.commands_mod._window_hold = lambda: None
        self.assertIsNone(self.client.get("/api/tutor/hold").json()["hold"])

    def test_a_bad_pin_is_refused_before_any_model_call(self):
        appmod.commands_mod._window_hold = lambda: None
        r = self.client.post("/api/tutor", json={
            "course": "TEST-101", "module": 1, "checkpoint": 1, "question": "hi"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/tutor", json={
            "course": "TEST-101", "module": 9, "question": "hi"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("no such module", r.json()["error"])


class TestComposition(TutorBase):
    """The happy path, with the model call stubbed out: what actually reaches
    the agent — the pin riding above the question, the mode's orientation,
    proposals on, the spend actor — is the whole difference between a tutor
    and a second chat drawer, and none of it may drift silently."""

    def setUp(self):
        super().setUp()
        appmod.commands_mod._window_hold = lambda: None
        self.captured: dict = {}
        self._real = (appmod.stream_agent, appmod.build_options)

        async def fake_stream(question, opts, actor):
            self.captured.update(question=question, opts=opts, actor=actor)
            yield "data: {\"type\": \"done\"}\n\n"

        def spy_options(**kw):
            self.captured["options_kw"] = kw
            return self._real[1](**kw)

        appmod.stream_agent = fake_stream
        appmod.build_options = spy_options

    def tearDown(self):
        appmod.stream_agent, appmod.build_options = self._real
        super().tearDown()

    def test_the_pin_rides_above_the_question_and_the_actor_is_tutor(self):
        r = self.client.post("/api/tutor", json={
            "course": "TEST-101", "module": 1, "qid": "q-1-1",
            "mode": "explain", "question": "why is that the answer?"})
        self.assertEqual(r.status_code, 200, r.text)
        q = self.captured["question"]
        self.assertTrue(q.startswith("## Pinned context"), q[:60])
        self.assertIn("pinned practice item q-1-1", q)
        self.assertTrue(q.endswith("why is that the answer?"))
        self.assertEqual(self.captured["actor"], "tutor")
        kw = self.captured["options_kw"]
        self.assertTrue(kw["allow_proposals"])
        self.assertEqual(kw["actor"], "tutor")
        self.assertIn(appmod.TUTOR_MODES["explain"], kw["orientation"])
        self.assertIn("propose_change", kw["orientation"])

    def test_an_unknown_mode_falls_back_to_nudge(self):
        r = self.client.post("/api/tutor", json={
            "course": "TEST-101", "module": 1,
            "mode": "spoiler", "question": "just tell me"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(appmod.TUTOR_MODES["nudge"],
                      self.captured["options_kw"]["orientation"])

    def test_a_session_id_reaches_resume(self):
        r = self.client.post("/api/tutor", json={
            "course": "TEST-101", "module": 1, "question": "hi",
            "session_id": "sess-123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.captured["opts"].resume, "sess-123")


class TestOrientation(TutorBase):
    def test_the_three_modes_exist_and_default_holds(self):
        self.assertEqual(set(appmod.TUTOR_MODES), {"nudge", "explain", "solve"})
        self.assertEqual(self.ask().mode, "nudge")

    def test_nudge_forbids_the_answer_and_solve_walks_it(self):
        self.assertIn("Never state the final answer", appmod.TUTOR_MODES["nudge"])
        self.assertIn("step by step", appmod.TUTOR_MODES["solve"])


if __name__ == "__main__":
    unittest.main()
