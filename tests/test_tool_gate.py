"""
The tool gate: what an agent may call at all, and what gets written down.

This covers the half of `privacy.py` that had no tests before 2026-08-01 —
`_classify`, `pre_tool_hook` and `can_use_tool`, i.e. every function that
actually stands between a model and the carve-out. The path half was well
covered through `gitignore_scan` (test_nosync); the *tool* half, which is the
one the two historical bypasses went through, was not covered at all.

Adversarial rather than happy-path, for the reason SYSTEM.md §12 keeps giving:
both times this guard was found not to be running, the evidence was a run
reporting zero denials. So the tests that matter are the ones asserting a
refusal *happened*, and — for the newly inverted default — that it happened
for the right reason and left a record a human can find.
"""
import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import privacy                                     # noqa: E402
from sigma import audit                            # noqa: E402


def _run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8")


class GateBase(unittest.TestCase):
    """A throwaway vault with one open note and one sealed one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "Client").mkdir(parents=True)
        _run(self.vault, "init", "-b", "master")
        _run(self.vault, "config", "user.name", "Test")
        _run(self.vault, "config", "user.email", "test@example.com")
        (self.vault / ".gitignore").write_text("Client/\n", encoding="utf-8")
        (self.vault / "open.md").write_text("# open\n", encoding="utf-8")
        (self.vault / "Client" / "work.md").write_text("# secret\n", encoding="utf-8")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "seed")

        # Re-point privacy's config dir and the audit log at the fixture, and
        # clear the caches the module keeps across constructions.
        self._saved = (privacy._RUNTIME, audit.AUDIT_PATH)
        privacy._RUNTIME = self.root
        audit.AUDIT_PATH = self.root / "audit.jsonl"
        privacy.model_allow_raw.cache_clear()
        privacy.model_allow_prefixes.cache_clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()

    def tearDown(self):
        (privacy._RUNTIME, audit.AUDIT_PATH) = self._saved
        privacy.model_allow_raw.cache_clear()
        privacy.model_allow_prefixes.cache_clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()
        self.tmp.cleanup()

    def guard(self, **kw):
        return privacy.VaultPrivacy(self.vault, **kw)

    def hook(self, guard, tool, args):
        """Drive the PreToolUse hook the way the SDK does."""
        return asyncio.run(guard.pre_tool_hook(
            {"tool_name": tool, "tool_input": args}, "id", None))

    def denied(self, result) -> bool:
        spec = (result or {}).get("hookSpecificOutput") or {}
        return spec.get("permissionDecision") == "deny"


class TheInvertedDefault(GateBase):
    """The change of 2026-08-01: an un-granted tool is refused, not waved through.

    The old behaviour is the one worth naming in a test name, because it read
    as safe: `refusal()` returned None for anything it had no rule for.
    """

    def test_an_unknown_tool_is_refused(self):
        rule, why = self.guard()._classify("browser_navigate",
                                           {"url": "https://example.com"})
        self.assertEqual(rule, "unvetted-tool")
        self.assertIn("not available in this run", why)

    def test_a_url_argument_cannot_slip_past_the_path_checks(self):
        # The precise old hole: no `file_path`, so every loop in _classify
        # skipped it and the call was allowed with zero denials recorded.
        self.assertTrue(self.denied(self.hook(
            self.guard(), "browser_navigate", {"url": "https://example.com"})))

    def test_granted_tools_narrows_the_floor(self):
        g = self.guard(granted_tools=["Read"])
        self.assertIsNone(g._classify("Read", {"file_path": "open.md"})[0])
        self.assertEqual(g._classify("Grep", {"path": "."})[0], "unvetted-tool")

    def test_granted_tools_can_admit_a_tool_the_module_has_never_heard_of(self):
        # This is how the browser lane will arrive: build_options grants it,
        # so the gate vets it rather than refusing it.
        g = self.guard(granted_tools=["mcp__playwright__browser_navigate"])
        self.assertIsNone(
            g._classify("mcp__playwright__browser_navigate",
                        {"url": "https://example.com"})[0])

    def test_without_a_grant_list_the_floor_still_holds(self):
        # A caller that forgets `granted_tools` must fail closed, not open —
        # the property must not depend on anyone having remembered.
        self.assertEqual(self.guard()._classify("Whatever", {})[0], "unvetted-tool")

    def test_the_vetted_floor_covers_what_the_interface_actually_grants(self):
        for tool in ("Read", "Glob", "Grep", privacy.PROPOSE_TOOL_NAME):
            self.assertIn(tool, privacy.VETTED_TOOLS, tool)


class TheOldGuaranteesStillHold(GateBase):
    """Nothing above may have loosened what already worked."""

    def test_a_sealed_path_is_still_refused(self):
        rule, why = self.guard()._classify("Read", {"file_path": "Client/work.md"})
        self.assertEqual(rule, "path")
        self.assertIn("local-only", why)

    def test_an_open_path_is_still_allowed(self):
        self.assertIsNone(self.guard()._classify("Read", {"file_path": "open.md"})[0])

    def test_write_tools_are_still_refused_with_their_own_message(self):
        rule, why = self.guard()._classify("Write", {"file_path": "open.md"})
        self.assertEqual(rule, "write-tool")
        self.assertIn("read-only", why)

    def test_write_tools_are_refused_even_when_granted(self):
        # "may call it" and "may write" must stay two switches. A grant list
        # that mentions Write must not amount to allow_writes=True.
        rule, _ = self.guard(granted_tools=["Write"])._classify(
            "Write", {"file_path": "open.md"})
        self.assertEqual(rule, "write-tool")

    def test_outside_the_vault_is_still_refused(self):
        rule, why = self.guard()._classify("Read", {"file_path": "C:/Windows/notepad.exe"})
        self.assertEqual(rule, "path")
        self.assertIn("outside the vault", why)

    def test_the_ntfs_stream_bypass_is_still_closed(self):
        """Two mechanisms, and they cover different halves — measured, not assumed.

        For a file that EXISTS, `resolve()` canonicalizes the stream away, so
        the path arrives at the gitignore check as its plain self and is
        refused for being sealed. For one that does NOT exist there is nothing
        to canonicalize, so the explicit colon rule is what catches it. Both
        are needed; neither covers the other's case.
        """
        rule, why = self.guard()._classify("Read", {"file_path": "Client/work.md::$DATA"})
        self.assertEqual(rule, "path")
        self.assertIn("local-only", why)          # canonicalized, then sealed

        rule, why = self.guard()._classify("Read", {"file_path": "Client/ghost.md::$DATA"})
        self.assertEqual(rule, "path")
        self.assertIn("NTFS stream", why)         # nothing to canonicalize

    def test_a_stream_on_open_material_is_allowed(self):
        # The rule is "a stream must never reach sealed material", not "colons
        # are suspicious". Asserting the allow keeps the boundary's real shape
        # on the record — a future tightening that refuses this is a change of
        # meaning, and should have to fail a test to happen.
        self.assertIsNone(self.guard()._classify("Read", {"file_path": "open.md::$DATA"})[0])

    def test_refusal_still_returns_a_plain_string(self):
        # `_classify` is new; `refusal` is the shape three call sites expect.
        self.assertIsNone(self.guard().refusal("Read", {"file_path": "open.md"}))
        self.assertIsInstance(
            self.guard().refusal("Read", {"file_path": "Client/work.md"}), str)


class BothLayersAgree(GateBase):
    """can_use_tool used to carry its own copy of the decision. It no longer does."""

    def _permission(self, guard, tool, args):
        return asyncio.run(guard.can_use_tool(tool, args, None))

    def test_the_hook_and_the_callback_refuse_the_same_things(self):
        cases = [("Read", {"file_path": "Client/work.md"}),
                 ("Write", {"file_path": "open.md"}),
                 ("browser_navigate", {"url": "https://example.com"})]
        for tool, args in cases:
            with self.subTest(tool=tool):
                self.assertTrue(self.denied(self.hook(self.guard(), tool, args)))
                self.assertEqual(
                    getattr(self._permission(self.guard(), tool, args),
                            "behavior", None), "deny")

    def test_both_allow_the_same_things(self):
        self.assertEqual(self.hook(self.guard(), "Read", {"file_path": "open.md"}), {})
        self.assertEqual(
            getattr(self._permission(self.guard(), "Read", {"file_path": "open.md"}),
                    "behavior", None), "allow")


class TheAuditTrail(GateBase):
    """A false refusal must be findable. That is the whole point of the log."""

    def lines(self):
        return audit.entries(path=audit.AUDIT_PATH)

    def test_a_refusal_is_written_down(self):
        self.hook(self.guard(actor="coach"), "browser_navigate",
                  {"url": "https://example.com"})
        rows = self.lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rule"], "unvetted-tool")
        self.assertEqual(rows[0]["actor"], "coach")
        self.assertEqual(rows[0]["tool"], "browser_navigate")

    def test_a_path_refusal_records_which_path(self):
        self.hook(self.guard(), "Read", {"file_path": "Client/work.md"})
        self.assertEqual(self.lines()[0]["detail"], "Client/work.md")

    def test_an_allowed_call_writes_nothing(self):
        self.hook(self.guard(), "Read", {"file_path": "open.md"})
        self.assertEqual(self.lines(), [])

    def test_classify_is_pure(self):
        # Tests, the doctor and any future caller must be able to ask the
        # question without writing to the record of what an agent did.
        self.guard()._classify("browser_navigate", {"url": "https://example.com"})
        self.assertEqual(self.lines(), [])

    def test_a_refusal_survives_an_unwritable_log(self):
        # A full disk costs an audit line, never the refusal itself.
        audit.AUDIT_PATH = self.root / "nope" / "deep" / "audit.jsonl"
        self.assertTrue(self.denied(self.hook(
            self.guard(), "browser_navigate", {"url": "https://example.com"})))

    def test_recent_filters_by_rule_and_keeps_undated_lines(self):
        p = self.root / "audit.jsonl"
        p.write_text(
            json.dumps({"ts": "2000-01-01T00:00:00", "kind": "refused",
                        "rule": "unvetted-tool", "tool": "old"}) + "\n"
            + json.dumps({"ts": "bogus", "kind": "refused",
                          "rule": "unvetted-tool", "tool": "undated"}) + "\n"
            + json.dumps({"ts": "2000-01-01T00:00:00", "kind": "refused",
                          "rule": "path", "tool": "Read"}) + "\n",
            encoding="utf-8")
        got = audit.recent(24, kind="refused", rule="unvetted-tool", path=p)
        # The old line ages out; the undated one is kept, because this log's
        # failure direction is one row too many, never one hidden.
        self.assertEqual([r["tool"] for r in got], ["undated"])

    def test_a_torn_tail_line_does_not_break_the_reader(self):
        p = self.root / "audit.jsonl"
        p.write_text(json.dumps({"ts": "2026-08-01T00:00:00", "kind": "refused",
                                 "tool": "a"}) + "\n{\"half", encoding="utf-8")
        self.assertEqual(len(audit.entries(path=p)), 1)


class TheDeclarationsAgree(unittest.TestCase):
    """privacy.py copies two names it deliberately does not import. Surface the
    drift rather than trusting it — the same treatment `model_allow` gets."""

    def test_the_propose_tool_name_matches_propose_py(self):
        import propose
        self.assertEqual(privacy.PROPOSE_TOOL_NAME, propose.PROPOSE_TOOL)

    def test_the_gate_admits_everything_build_options_grants(self):
        # The property that must hold for the interface to work at all: every
        # tool the single construction site hands out is one the gate vets.
        import agent
        for tool in agent.READ_ONLY_TOOLS + agent.WRITE_TOOLS:
            self.assertIn(tool, privacy.VETTED_TOOLS, tool)


if __name__ == "__main__":
    unittest.main()
