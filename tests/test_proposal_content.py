"""
Regression test for proposal_content: a proposed note that itself contains
fenced blocks must survive extraction whole. The first real daily-note
proposal embedded a ```dataview block, and the original lazy regex truncated
the payload at it — found the day auto-apply would have written the gutted
version unattended.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import reflect as rf  # noqa: E402

BODY = (
    "---\ntype: proposal\nstatus: pending\n---\n\n# Daily note\n\n"
    "## The change\n"
    "<!-- proposal:content -->\n"
    "```markdown\n"
    "# Thursday\n\n"
    "## Tasks due\n"
    "```dataview\nTASK\nWHERE !completed\n```\n\n"
    "## Log\n-\n"
    "```\n\n"
    "## How to approve\n"
    "1. Edit the block above.\n"
)


class TestProposalContent(unittest.TestCase):
    def test_nested_fences_survive(self):
        content = rf.proposal_content(BODY)
        self.assertIn("```dataview", content)
        self.assertIn("## Log", content)          # nothing truncated at the fence
        self.assertTrue(content.rstrip().endswith("-"))

    def test_stamps_below_the_approve_heading_never_leak(self):
        stamped = BODY + ("\n---\n**Staged 2026-07-30** — review it:\n\n"
                          "```\npython reflect.py --diff x\n```\n")
        self.assertEqual(rf.proposal_content(stamped), rf.proposal_content(BODY))

    def test_no_marker_is_empty(self):
        self.assertEqual(rf.proposal_content("# nothing here"), "")


if __name__ == "__main__":
    unittest.main()
