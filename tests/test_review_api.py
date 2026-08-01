"""
review.py — approving a proposal from the dashboard.

`name` is the only user-supplied string in this app that reaches disk, so most
of what matters here is that it never becomes a path. It is resolved against a
dictionary built from the directory listing, exactly like the palette's verb
whitelist, and the tests below try to escape that on purpose.

The rest guards the division of responsibility: this module must not grow its
own opinion about what may be applied or merged. Those rules live in applier.py
and reflect.do_merge, and a second copy that can disagree is the mistake this
project keeps refusing to make.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

from fastapi import FastAPI                      # noqa: E402
from fastapi.testclient import TestClient        # noqa: E402

import reflect as rf                             # noqa: E402
import review                                    # noqa: E402

PROPOSAL = """---
type: proposal
date: 2026-07-31
status: pending
kind: note
target: "01-Daily/2026-07-31.md"
risk: low
applied:
tags: [proposal]
---

# Add a daily note

## Why
Because.

<!-- proposal:content -->
```markdown
---
type: daily
status: whatever
tags: [daily]
---

# 2026-07-31

new body
```
"""


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


class ReviewBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "06-System" / "proposals").mkdir(parents=True)
        (self.vault / "01-Daily").mkdir(parents=True)
        self.prop = self.vault / "06-System" / "proposals" / "p-one.md"
        self.prop.write_text(PROPOSAL, encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "seed")

        self._saved = (rf.VAULT, rf.PROPOSALS)
        rf.VAULT = self.vault
        rf.PROPOSALS = self.vault / "06-System" / "proposals"

        app = FastAPI()
        app.include_router(review.router)
        self.client = TestClient(app)

    def tearDown(self):
        (rf.VAULT, rf.PROPOSALS) = self._saved
        self.tmp.cleanup()

    def status_of(self) -> str:
        for line in self.prop.read_text(encoding="utf-8").splitlines():
            if line.startswith("status:"):
                return line.split(":", 1)[1].strip()
        return ""


class TestNameIsNeverAPath(ReviewBase):
    """The whole security surface of this module."""

    def test_hostile_names_never_resolve(self):
        """Called at the handler, not through the URL router.

        Routing normalises `a/../b` to `b` before the handler ever sees it,
        which is correct HTTP and made the first version of this test fail on a
        request that was simply asking for the real proposal. The defence being
        tested is the dictionary lookup, so it is tested where it lives.
        """
        for bad in ("../../CLAUDE", "..\\..\\CLAUDE", "/etc/passwd",
                    "C:/Windows/win", "p-one.md::$DATA", "p-one.md",
                    "", ".", "..", "06-System/proposals/p-one"):
            r = review.api_proposal(bad)
            self.assertEqual(getattr(r, "status_code", None), 404,
                             f"{bad!r} did not 404")

    def test_no_path_is_ever_built_from_the_name(self):
        """The property behind the test above: every candidate comes from the
        directory listing, so a name that is not a key cannot name a file."""
        keys = set(review._by_name())
        self.assertEqual(keys, {"p-one"})
        for bad in ("../../CLAUDE", "p-one.md", "06-System/proposals/p-one"):
            self.assertNotIn(bad, keys)

    def test_a_real_name_resolves(self):
        r = self.client.get("/api/proposals/p-one")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["name"], "p-one")

    def test_decide_rejects_an_unknown_name(self):
        r = self.client.post("/api/proposals/nope/decide", json={"action": "approve"})
        self.assertEqual(r.status_code, 404)

    def test_unknown_action_is_refused(self):
        r = self.client.post("/api/proposals/p-one/decide", json={"action": "delete"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.status_of(), "pending")   # nothing happened


class TestReading(ReviewBase):
    def test_it_returns_the_content_block_and_a_new_file_diff(self):
        d = self.client.get("/api/proposals/p-one").json()
        self.assertIn("new body", d["proposed"])
        self.assertFalse(d["target_exists"])
        self.assertFalse(d["would_stage"])
        self.assertIn("+new body", d["diff"])

    def test_an_existing_target_produces_a_real_diff_and_stages(self):
        (self.vault / "01-Daily" / "2026-07-31.md").write_text(
            "---\ntype: daily\ntags: [daily]\n---\n\n# 2026-07-31\n\nold body\n",
            encoding="utf-8")
        d = self.client.get("/api/proposals/p-one").json()
        self.assertTrue(d["target_exists"])
        self.assertTrue(d["would_stage"], "a change to an existing note must stage")
        self.assertIn("-old body", d["diff"])
        self.assertIn("+new body", d["diff"])


class TestDeciding(ReviewBase):
    def test_approve_and_reject_only_touch_the_frontmatter_status(self):
        """The content block contains its own `status:` line — a blanket
        replace would corrupt the very note being approved."""
        r = self.client.post("/api/proposals/p-one/decide", json={"action": "approve"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.status_of(), "approved")
        self.assertIn("status: whatever", self.prop.read_text(encoding="utf-8"))

        self.client.post("/api/proposals/p-one/decide", json={"action": "reject"})
        self.assertEqual(self.status_of(), "rejected")
        self.assertIn("status: whatever", self.prop.read_text(encoding="utf-8"))

    def test_merge_refusal_is_409_with_the_reason(self):
        """A guard refusing is not a server error, and its reason is the most
        useful thing to show."""
        r = self.client.post("/api/proposals/p-one/decide", json={"action": "merge"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertTrue(r.json().get("detail"))


if __name__ == "__main__":
    unittest.main()
