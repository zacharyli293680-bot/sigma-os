#!/usr/bin/env python3
"""
The propose tool's in-process attribution record (study S5's review fix).

The fleet used to attribute proposals to a specialist by diffing the proposals
directory before/after its conversation — which swept in anything that merely
APPEARED in the window: a tutor- or chat-raised proposal from the dashboard's
process, a file arriving via git pull. apply_run then auto-applied a kind:note
nobody's run had raised, under the wrong actor. The registry closes that: a
name lands in propose.WRITTEN only when write_proposal actually returned, in
this process, and the fleet slices that list around its own run.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import propose                    # noqa: E402
import reflect as rf              # noqa: E402


def _call(args: dict) -> dict:
    handler = getattr(propose.propose_change, "handler", propose.propose_change)
    return asyncio.run(handler(args))


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._saved = (rf.PROPOSALS, list(propose.WRITTEN))
        rf.PROPOSALS = Path(self.tmp.name)
        propose.WRITTEN.clear()

    def tearDown(self):
        rf.PROPOSALS, saved_written = self._saved
        propose.WRITTEN[:] = saved_written
        self.tmp.cleanup()

    def test_a_successful_write_is_recorded_by_name(self):
        r = _call({"title": "Fix a thing", "kind": "note",
                   "target": "04-Resources/thing.md", "content": "# Thing\n",
                   "rationale": "test", "risk": "low", "scope": "vault"})
        self.assertFalse(r.get("is_error"), r)
        self.assertEqual(len(propose.WRITTEN), 1)
        self.assertTrue((Path(self.tmp.name) / propose.WRITTEN[0]).is_file())

    def test_a_refused_call_records_nothing(self):
        r = _call({"title": "", "kind": "note", "target": "x.md",
                   "content": "y", "rationale": "", "risk": "low", "scope": "vault"})
        self.assertTrue(r.get("is_error"))
        self.assertEqual(propose.WRITTEN, [])

    def test_the_slice_ignores_files_that_merely_appeared(self):
        """fleet.py's exact expression: WRITTEN[before_n:] — a foreign file
        dropped into the directory during the window (another process's
        proposal, a git pull) is invisible to it, where the old directory
        diff attributed and auto-applied it."""
        before_n = len(propose.WRITTEN)
        (Path(self.tmp.name) / "2026-08-09-raised-elsewhere.md").write_text(
            "---\ntype: proposal\nstatus: pending\nkind: note\n---\n",
            encoding="utf-8")
        _call({"title": "Mine", "kind": "note", "target": "04-Resources/m.md",
               "content": "# M\n", "rationale": "", "risk": "low", "scope": "vault"})
        written = sorted(propose.WRITTEN[before_n:])
        self.assertEqual(len(written), 1)
        self.assertNotIn("2026-08-09-raised-elsewhere.md", written)
        # the directory-diff approach would have reported both:
        names = {p.name for p in Path(self.tmp.name).glob("*.md")}
        self.assertEqual(len(names), 2)


if __name__ == "__main__":
    unittest.main()
