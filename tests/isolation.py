"""Keep a test run out of `runtime/`.

Importing this module points every Sigma log at a scratch directory for the
life of the process; `sandbox(self)` does the same for the state files a module
binds at import time.

It exists because the suite was writing into the live runtime. `test_intake`
appended to `intake.log`, the review suites appended to `review.log`, and
`test_fleet_window` **truncated** `fleet.fire.jsonl` — erasing the feed of the
last real fleet run, which is destruction rather than noise. The log pollution
cost the most: the review suite's own fixture raises
`RuntimeError("claude is not on PATH")` to prove the score survives a dead
model, and with that line sitting in the production log among real ones, the
06:00 review was diagnosed as broken when nothing had failed.

Two mechanisms, because there are two kinds of path:

- **Logs** go through `sigma.make_logger`, which resolves `SIGMA_STATE_DIR` at
  write time. Set here at *import* rather than per-test on purpose: unittest
  discovery imports every test module before running any of them, so one import
  anywhere redirects the whole run's logs — including suites that never call
  `sandbox()`.
- **State files** are module-level constants read at use time, so they are
  patched per test case and restored on cleanup.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

SCRATCH = Path(tempfile.mkdtemp(prefix="sigma-tests-"))
os.environ["SIGMA_STATE_DIR"] = str(SCRATCH)
atexit.register(shutil.rmtree, SCRATCH, True)

# (module, attribute, filename). The list is here and nowhere else: it was one
# hand-maintained tuple per suite before, and the drift was exactly what a
# second declaration always does — test_fleet_window redirected four of fleet's
# five paths and truncated the real file with the fifth.
_STATE = [
    ("fleet", "STATE_PATH", "fleet.state.json"),
    ("fleet", "PROGRESS_PATH", "fleet.progress.json"),
    ("fleet", "LOCK_PATH", "fleet.lock"),
    ("fleet", "FIRE_PATH", "fleet.fire.jsonl"),
    ("retro", "ROWS_PATH", "reviews.jsonl"),
    ("todo", "INDEX_PATH", "todo.state.json"),
    ("doctor", "STATE_PATH", "doctor.state.json"),
    ("reflect", "STATE_PATH", "reflect.state.json"),
    ("sigma.spend", "SPEND_PATH", "spend.jsonl"),
    ("sigma.ledger", "LEDGER_PATH", "ledger.jsonl"),
    ("sigma.audit", "AUDIT_PATH", "audit.jsonl"),
    ("sigma.gitops", "MUTEX_PATH", "git.lock"),
]


def sandbox(case, root=None):
    """Point every *already imported* runtime module's state file into `root`.

    Only modules in `sys.modules` are touched: importing the rest here would
    fire their import-time side effects in an order no suite asked for. A suite
    that redirects a path itself afterwards still wins — cleanups run after
    tearDown, so the original is restored last either way.

    Returns the directory, so a caller can assert on what landed in it.
    """
    root = Path(root) if root else Path(
        tempfile.mkdtemp(prefix="case-", dir=SCRATCH))
    for mod_name, attr, filename in _STATE:
        mod = sys.modules.get(mod_name)
        if mod is None or not hasattr(mod, attr):
            continue
        case.addCleanup(setattr, mod, attr, getattr(mod, attr))
        setattr(mod, attr, root / filename)
    return root
