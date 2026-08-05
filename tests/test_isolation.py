"""
The suite must not write into `runtime/`.

These are the adversarial half of tests/isolation.py: each one tries to reach a
real production file the way a suite actually did, and asserts it did not land
there. A guard that is merely configured is the failure this project keeps
re-learning, so every case here checks the *real* file's bytes rather than the
redirect's presence.
"""
import sys
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import fleet                     # noqa: E402
import isolation                 # noqa: E402
import retro                     # noqa: E402
import todo                      # noqa: E402
from sigma import make_logger    # noqa: E402


def _snapshot(p: Path):
    """(exists, bytes) — the only claim that matters is that this is unchanged."""
    return (p.exists(), p.read_bytes() if p.exists() else b"")


class TestLogsStayOutOfRuntime(unittest.TestCase):
    def test_a_logger_bound_to_the_real_path_writes_to_the_scratch_dir(self):
        """The exact shape that put 'claude is not on PATH' into review.log:
        retro binds runtime/review.log at import, and a test calls its log."""
        real = RUNTIME / "review.log"
        before = _snapshot(real)
        make_logger(real, "review")("narrative failed: a test wrote this")
        self.assertEqual(_snapshot(real), before, "the suite wrote to review.log")
        landed = Path(isolation.SCRATCH) / "review.log"
        self.assertIn("a test wrote this", landed.read_text(encoding="utf-8"))

    def test_the_redirect_is_what_moves_the_write(self):
        """The control for the case above, since a test that passes for the
        wrong reason is the whole risk here: with the variable cleared, the
        same logger writes to the path it was bound to."""
        import os
        import tempfile
        stand_in = Path(tempfile.mkdtemp()) / "review.log"
        saved = os.environ.pop("SIGMA_STATE_DIR")
        try:
            make_logger(stand_in, "review")("redirect-off")
            self.assertIn("redirect-off", stand_in.read_text(encoding="utf-8"))
        finally:
            os.environ["SIGMA_STATE_DIR"] = saved
        make_logger(stand_in, "review")("redirect-on")
        self.assertNotIn("redirect-on", stand_in.read_text(encoding="utf-8"))
        self.assertIn("redirect-on",
                      (Path(isolation.SCRATCH) / "review.log").read_text(encoding="utf-8"))

    def test_the_module_level_loggers_are_redirected_too(self):
        """retro.log and fleet.log are closures built at import time — the
        redirect has to be resolved per write or they keep the old path."""
        for mod, name in ((retro, "review.log"), (fleet, "fleet.log")):
            with self.subTest(log=name):
                real = RUNTIME / name
                before = _snapshot(real)
                mod.log(f"isolation check for {name}")
                self.assertEqual(_snapshot(real), before)


class TestStateFilesStayOutOfRuntime(unittest.TestCase):
    def test_sandbox_moves_every_state_path_off_the_runtime_dir(self):
        isolation.sandbox(self)
        for owner, attr in (("fleet", "STATE_PATH"), ("fleet", "PROGRESS_PATH"),
                            ("fleet", "LOCK_PATH"), ("fleet", "FIRE_PATH"),
                            ("retro", "ROWS_PATH"), ("todo", "INDEX_PATH")):
            with self.subTest(path=f"{owner}.{attr}"):
                p = Path(getattr(sys.modules[owner], attr)).resolve()
                self.assertNotEqual(p.parent, RUNTIME.resolve())

    def test_reset_fires_cannot_truncate_the_real_feed(self):
        """test_fleet_window did exactly this on every pass: fleet.run() calls
        reset_fires(), and FIRE_PATH was the one path it had not redirected."""
        real = RUNTIME / "fleet.fire.jsonl"
        before = _snapshot(real)
        isolation.sandbox(self)
        fleet.reset_fires()
        fleet.write_fire("coach", "Glob", "02-Areas/Academics/*/timeline.md")
        self.assertEqual(_snapshot(real), before, "the suite truncated the fire feed")
        self.assertIn("coach", Path(fleet.FIRE_PATH).read_text(encoding="utf-8"))

    def test_the_originals_come_back_after_a_case(self):
        """A leaked sandbox path is worse than no sandbox: the next suite would
        assert against a temp file that no production code ever writes."""
        self.assertEqual(Path(todo.INDEX_PATH).parent.resolve(), RUNTIME.resolve())


if __name__ == "__main__":
    unittest.main()
