"""
Sequencer tests for the section-8 window policy: degrade on the first rate
limit, pause with a resume point on a rate limit while already degraded.

run_one is mocked (burning the real window to prove the pause would be the
joke writing itself); everything downstream of it — the degrade decision, the
model overrides, progress and state files — is the real code.
"""
import asyncio
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import fleet                     # noqa: E402
import isolation                 # noqa: E402
import specialists as sp         # noqa: E402
from sigma import spend          # noqa: E402


def _result(spec, model_override, ok=True, error=None):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    return {"key": spec.key, "started": now, "ok": ok, "proposals": 0,
            "attempts": 0, "denials": 0, "cost_usd": 0.01, "summary": "",
            "error": error, "model": model_override or spec.model,
            "files": [], "finished": now, "seconds": 1.0}


class TestWindowPolicy(unittest.TestCase):
    def setUp(self):
        # First: run() truncates FIRE_PATH, which the four lines below did not
        # cover — so every pass of this suite erased the last real fleet run's
        # feed out of runtime/.
        isolation.sandbox(self)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._saved = (fleet.STATE_PATH, fleet.PROGRESS_PATH, fleet.LOCK_PATH,
                       spend.SPEND_PATH, fleet.log, fleet.run_one)
        fleet.STATE_PATH = root / "fleet.state.json"
        fleet.PROGRESS_PATH = root / "fleet.progress.json"
        fleet.LOCK_PATH = root / "fleet.lock"
        spend.SPEND_PATH = root / "spend.jsonl"
        fleet.log = lambda m: None
        self.calls = []

    def tearDown(self):
        (fleet.STATE_PATH, fleet.PROGRESS_PATH, fleet.LOCK_PATH,
         spend.SPEND_PATH, fleet.log, fleet.run_one) = self._saved
        self.tmp.cleanup()

    def _script(self, outcomes):
        """outcomes: {key: (ok, error)} — anything unlisted succeeds."""
        async def fake(spec, timeout_s=420, model_override=None):
            self.calls.append((spec.key, model_override))
            ok, error = outcomes.get(spec.key, (True, None))
            return _result(spec, model_override, ok=ok, error=error)
        fleet.run_one = fake

    def _progress(self):
        return json.loads(fleet.PROGRESS_PATH.read_text(encoding="utf-8"))

    @property
    def first(self) -> str:
        """Whoever runs first, rather than a name. These tests are about the
        window policy, not about the roster — hardcoding "planner" here meant
        retiring it broke two tests that had nothing to do with it."""
        return sp.in_run_order()[0].key

    def test_first_rate_limit_degrades_and_continues(self):
        first = self.first
        self._script({first: (False, "rate limit reached")})
        asyncio.run(fleet.run_fleet(force=True))
        # the first ran at full model, everyone after ran degraded
        self.assertEqual(self.calls[0], (first, None))
        by_key = dict(self.calls[1:])
        for key, override in by_key.items():
            native = sp.BY_KEY[key].model
            self.assertEqual(override, None if native == "haiku" else "haiku",
                             f"{key} should run on haiku after the degrade")
        self.assertEqual(len(self.calls), len(sp.BY_KEY))   # nobody was skipped
        prog = self._progress()
        self.assertEqual(prog["state"], "done")
        self.assertTrue(prog["degraded"])
        # the failed specialist made no progress and stays due
        state = fleet.load_state()
        self.assertNotIn("last_ok", state["specialists"][first])
        self.assertTrue(fleet.is_due(sp.BY_KEY[first], state,
                                     datetime.datetime.now()))

    def test_rate_limit_while_degraded_pauses_with_resume(self):
        # a rate limit 10 minutes ago: this run starts degraded
        first = self.first
        spend.record_spend(actor="seed", model="sonnet", rate_limited=True)
        self._script({first: (False, "usage limit reached")})
        asyncio.run(fleet.run_fleet(force=True))
        self.assertEqual(len(self.calls), 1)                # stopped, not burned
        prog = self._progress()
        self.assertEqual(prog["state"], "paused")
        self.assertEqual(prog["note"], "window exhausted")
        self.assertIsNotNone(prog["resume_at"])
        state = fleet.load_state()
        self.assertIsNotNone(state.get("stopped_early_at"))
        self.assertEqual(state["paused"]["remaining"],
                         [k for k in sp.BY_KEY if k != first])

    def test_clean_run_clears_the_pause(self):
        fleet.save_state({"specialists": {}, "stopped_early_at": "2026-07-30T09:00:00",
                          "paused": {"at": "x", "resume_at": "y", "remaining": []}})
        self._script({})
        asyncio.run(fleet.run_fleet(force=True))
        state = fleet.load_state()
        self.assertNotIn("stopped_early_at", state)
        self.assertNotIn("paused", state)
        self.assertEqual(self._progress()["state"], "done")


if __name__ == "__main__":
    unittest.main()
