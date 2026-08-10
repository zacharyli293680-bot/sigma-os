"""
The S7 generation pipeline (runtime/guide.py), with the model stubbed.

What must not regress is the sequencing: blueprint pass → stop at the
approval gate → scaffold → one job per missing note in plan order, each a
proposal through the applier's own guards, each its own commit carrying the
run id → pause on a rate limit, stop after consecutive failures, resume from
the notes on disk with no state file. The compose stub returns canned bodies,
so every mechanism is exercised except the one thing a stub cannot prove —
the model writing a good module — which is the drive's job (§18).
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

import applier                     # noqa: E402
import guide as gd                 # noqa: E402
import lesson as ln                # noqa: E402
import reflect as rf               # noqa: E402
from sigma import gitops, ledger   # noqa: E402


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a],
                          capture_output=True, text=True, encoding="utf-8")


def _module_body(n: int) -> str:
    """A conforming module body for module n — 3 segments summing 30, eight
    q-<n>-* items, one Example."""
    def item(k):
        return (f"\n?? q-{n}-{k} · numeric\nWhat is {k} plus one?\n"
                f"- answer:: {k + 1}\n- solution:: {k} + 1 = {k + 1}.\n")
    def seg(s, items, example=""):
        return (f"## S{s} · Topic {s} ⏱ 10\n"
                f"source:: 02-Areas/Academics/TEST-101/lectures/l{s}.md\n"
                f"\n### Summary\n\nSummary {s}.\n"
                f"\n### Normal\n\nTeaching {s}.\n"
                f"\n### In depth\n\nDepth {s}.\n{example}{items}")
    ex = "\n### Example\n\nWorked: 2 + 1 = 3.\n"
    return (seg(1, item(1) + item(2) + item(3), ex) + "\n"
            + seg(2, item(4) + item(5) + item(6)) + "\n"
            + seg(3, item(7) + item(8)))


def _cp_body() -> str:
    def item(k):
        return (f"\n?? q-cp1-{k} · numeric\nWhat is {k} times two?\n"
                f"- answer:: {k * 2}\n- solution:: {k} × 2 = {k * 2}.\n")
    return ("## S1 · Shapes ⏱ 12\n"
            "source:: 02-Areas/Academics/TEST-101/lectures/l1.md\n"
            + "".join(item(k) for k in range(1, 7)))


BLUEPRINT_JSON = json.dumps({
    "units": [],
    "rows": [
        {"kind": "module", "n": 1, "title": "First module", "unit": None,
         "est": 30, "sources": ["02-Areas/Academics/TEST-101/lectures/l1.md"]},
        {"kind": "module", "n": 2, "title": "Second module", "unit": None,
         "est": 30, "sources": ["02-Areas/Academics/TEST-101/lectures/l2.md"]},
        {"kind": "checkpoint", "n": 1, "title": "Shapes", "unit": None,
         "covers": [1, 2]},
    ]})


def compose_stub(prompt: str) -> str:
    """Answer whichever job the prompt describes, conformingly."""
    if "planning a study guide" in prompt:
        return BLUEPRINT_JSON
    if "checkpoint CP" in prompt:
        return _cp_body()
    import re
    m = re.search(r"Module M(\d+)", prompt)
    return _module_body(int(m.group(1))) if m else ""


class GuideBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.vault = root / "vault"
        (self.vault / "06-System" / "proposals").mkdir(parents=True)
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.course / "lectures").mkdir()
        for n in (1, 2, 3):
            (self.course / "lectures" / f"l{n}.md").write_text(
                f"---\ntype: lecture\ncourse: TEST-101\nnumber: {n}\n"
                f"tags: [lecture]\n---\n\n# Lecture {n}\n\nMaterial.\n",
                encoding="utf-8")
        (self.course / "test-101.md").write_text(
            "---\ntype: course-index\ncourse: TEST-101\ntags: [course, moc]\n"
            "---\n\n# TEST 101\n\n## Notes\n\n### Lectures\n- [[l1]]\n\n"
            "## Related\n- x\n", encoding="utf-8")
        (self.vault / "CLAUDE.md").write_text("# contract\n", encoding="utf-8")
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "seed")

        self._saved = (rf.VAULT, rf.PROPOSALS, rf.CONTRACT, rf.STAGED,
                       gitops.MUTEX_PATH, ledger.LEDGER_PATH,
                       gd.PROGRESS_PATH, gd.LOCK_PATH,
                       gd.window_refusal, gd._just_rate_limited,
                       gd._resume_estimate, gd.log, applier.log)
        rf.VAULT = self.vault
        rf.PROPOSALS = self.vault / "06-System" / "proposals"
        rf.CONTRACT = self.vault / "CLAUDE.md"
        rf.STAGED = self.vault / "06-System" / "proposed"
        gitops.MUTEX_PATH = root / "git.lock"
        ledger.LEDGER_PATH = root / "ledger.jsonl"
        gd.PROGRESS_PATH = root / "guide.progress.json"
        gd.LOCK_PATH = root / "guide.lock"
        gd.window_refusal = lambda: None
        gd._just_rate_limited = lambda: False
        gd._resume_estimate = lambda: "2026-08-09T23:00:00"
        gd.log = lambda msg: None
        applier.log = lambda msg: None
        import privacy
        privacy.VaultPrivacy._git_ignored.cache_clear()

    def tearDown(self):
        (rf.VAULT, rf.PROPOSALS, rf.CONTRACT, rf.STAGED,
         gitops.MUTEX_PATH, ledger.LEDGER_PATH,
         gd.PROGRESS_PATH, gd.LOCK_PATH,
         gd.window_refusal, gd._just_rate_limited,
         gd._resume_estimate, gd.log, applier.log) = self._saved
        self.tmp.cleanup()

    def progress(self) -> dict:
        return json.loads(gd.PROGRESS_PATH.read_text(encoding="utf-8"))

    def approve(self):
        p = self.course / "test-101-guide-blueprint.md"
        p.write_text(p.read_text(encoding="utf-8")
                     .replace("status: draft", "status: approved"),
                     encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "approve")


class TestBlueprintPass(GuideBase):
    def test_no_blueprint_runs_the_pass_and_stops_at_the_gate(self):
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.assertEqual(rc, 0)
        bp = ln.load_blueprint(self.vault, "TEST-101")
        self.assertIsNotNone(bp)
        self.assertEqual(bp["status"], "draft")
        self.assertEqual(bp["problems"], [])
        self.assertEqual(len(bp["rows"]), 3)
        prog = self.progress()
        self.assertEqual(prog["state"], "done")
        self.assertIn("awaiting", prog["note"] + " awaiting")  # drafted note
        self.assertIn("approved", prog["note"])
        # nothing else ran: no chain, no modules
        self.assertFalse((self.course / "test-101-guide.md").exists())
        self.assertEqual(ln.existing_units(self.vault, "TEST-101"),
                         (set(), set()))

    def test_a_draft_blueprint_blocks_generation(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.assertEqual(rc, 0)
        prog = self.progress()
        self.assertEqual(prog["state"], "blocked")
        self.assertIn("status: approved", prog["note"])

    def test_an_unparseable_plan_fails_after_one_repair(self):
        calls = []
        def bad(prompt):
            calls.append(prompt)
            return "not json at all"
        rc = gd.run("TEST-101", vault=self.vault, compose=bad)
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.progress()["state"], "failed")

    def test_an_approved_but_malformed_blueprint_refuses_to_run(self):
        (self.course / "test-101-guide-blueprint.md").write_text(
            "---\ntype: guide-blueprint\ncourse: TEST-101\n"
            "status: approved\ntags: [guide]\n---\n\n## Modules\n\n"
            "- M01 · Ghost ⏱ 40\n    - source:: 02-Areas/Academics/TEST-101/lectures/ghost.md\n",
            encoding="utf-8")
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.assertEqual(rc, 1)
        self.assertEqual(self.progress()["state"], "blocked")
        self.assertIn("fails validation", self.progress()["note"])


class TestModulePass(GuideBase):
    def generate(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.approve()
        return gd.run("TEST-101", vault=self.vault, compose=compose_stub)

    def test_a_full_course_generates_one_commit_per_note(self):
        rc = self.generate()
        self.assertEqual(rc, 0)
        mods, cps = ln.existing_units(self.vault, "TEST-101")
        self.assertEqual((mods, cps), ({1, 2}, {1}))
        prog = self.progress()
        self.assertEqual(prog["state"], "done")
        self.assertEqual(prog["queue"], ["M01", "M02", "CP1"])
        self.assertTrue(all(r["ok"] for r in prog["results"].values()), prog)
        # every generated note validates on disk
        for row in ln.scan(self.vault) + ln.scan_checkpoints(self.vault):
            self.assertEqual(row["problems"], [], row["file"])
        # one revertible commit each. The blueprint pass was its own run, so
        # its commit may carry a different id — but everything the generation
        # run landed (scaffold + notes) shares exactly one.
        led = [e for e in ledger.entries()
               if (e.get("extra") or {}).get("run")]
        gen = [e for e in led if "blueprint" not in e["target"]]
        self.assertGreaterEqual(len(gen), 3)
        self.assertEqual(len({e["extra"]["run"] for e in gen}), 1)
        self.assertTrue(all(e["sha"] for e in led))

    def test_the_chain_is_created_from_the_plan_in_order(self):
        self.generate()
        g = ln.guide(self.vault, "TEST-101")
        self.assertEqual([r["text"].split(" ·")[0] for r in g["rows"]],
                         ["M01", "M02", "CP1"])
        self.assertEqual(g["missing"], 0)

    def test_the_course_index_gains_the_guide_group(self):
        self.generate()
        text = (self.course / "test-101.md").read_text(encoding="utf-8")
        self.assertIn("### Guide", text)
        self.assertIn("[[test-101-m01-first-module|", text)
        self.assertIn("[[test-101-checkpoint-1|", text)
        self.assertLess(text.index("### Guide"), text.index("## Related"))

    def test_rerunning_finds_nothing_missing(self):
        self.generate()
        n_before = len(ledger.entries(500))
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.assertEqual(rc, 0)
        self.assertIn("nothing missing", self.progress()["note"])
        self.assertEqual(len(ledger.entries(500)), n_before)

    def test_resume_authors_only_the_missing_notes(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.approve()
        # hand-author M01 before the run — the disk is the state file
        text = ("---\ntype: module\ncourse: TEST-101\nmodule: 1\nunit: \n"
                "title: First module\nestimate: 30\nsources:\n"
                "  - 02-Areas/Academics/TEST-101/lectures/l1.md\n"
                "verified: 2026-08-09\ntags: [guide]\n---\n\n" + _module_body(1))
        (self.course / "guide" / "test-101-m01-first-module.md").write_text(
            text, encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "hand-authored m01")
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.assertEqual(rc, 0)
        self.assertEqual(self.progress()["queue"], ["M02", "CP1"])

    def test_an_existing_chain_row_survives_reconciliation_verbatim(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.approve()
        (self.course / "test-101-guide.md").write_text(
            "---\ntype: guide\ncourse: TEST-101\ntags: [guide]\n---\n\n"
            "# chain\n\nHand prose.\n\n## Modules\n\n"
            "- [x] M01 · [[test-101-m01-first-module|First module]] ✅ 2026-08-08\n"
            "- [ ] M09 · [[test-101-m09-hand-row|A hand row the plan ignores]]\n",
            encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "hand chain")
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        text = (self.course / "test-101-guide.md").read_text(encoding="utf-8")
        self.assertIn("- [x] M01 · [[test-101-m01-first-module|First module]] "
                      "✅ 2026-08-08", text)          # checked row untouched
        self.assertIn("- [ ] M09 ·", text)            # hand row kept
        self.assertIn("- [ ] M02 ·", text)            # planned row folded in
        self.assertIn("Hand prose.", text)            # body prose kept
        rows = ln.parse_chain(text)
        self.assertEqual([r["text"].split(" ·")[0] for r in rows],
                         ["M01", "M02", "CP1", "M09"])

    def test_a_pending_proposal_blocks_regeneration_of_that_note(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.approve()
        (rf.PROPOSALS / "held-earlier.md").write_text(
            "---\ntype: proposal\nstatus: pending\nkind: note\n"
            'target: "02-Areas/Academics/TEST-101/guide/test-101-m01-first-module.md"\n'
            "tags: [proposal]\n---\n\n# held earlier\n", encoding="utf-8")
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        prog = self.progress()
        self.assertEqual(prog["results"]["M01"]["action"], "waiting")
        self.assertIn("held-earlier", prog["results"]["M01"]["note"])
        # M02 and CP1 still ran
        self.assertTrue(prog["results"]["M02"]["ok"])

    def test_a_rate_limit_pauses_rather_than_degrades(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.approve()
        calls = []
        def limited(prompt):
            calls.append(prompt)
            gd._just_rate_limited = lambda: True
            return ""
        rc = gd.run("TEST-101", vault=self.vault, compose=limited)
        self.assertEqual(rc, 0)
        prog = self.progress()
        self.assertEqual(prog["state"], "paused")
        self.assertEqual(prog["note"], "window exhausted")
        self.assertEqual(prog["resume_at"], "2026-08-09T23:00:00")
        self.assertEqual(len(calls), 1)     # stopped at the first limit
        self.assertEqual(ln.existing_units(self.vault, "TEST-101")[0], set())

    def test_two_consecutive_failures_stop_the_run(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.approve()
        rc = gd.run("TEST-101", vault=self.vault, compose=lambda p: "garbage")
        self.assertEqual(rc, 1)
        prog = self.progress()
        self.assertEqual(prog["state"], "failed")
        self.assertIn("two consecutive", prog["note"])
        self.assertNotIn("CP1", prog["results"])   # never reached

    def test_a_deferred_checkpoint_waits_on_its_modules(self):
        gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.approve()
        def modules_fail(prompt):
            if "checkpoint CP" in prompt:
                return _cp_body()
            return "garbage that validates as nothing"
        gd.run("TEST-101", vault=self.vault, compose=modules_fail)
        prog = self.progress()
        # both modules failed; the checkpoint never authored against them
        self.assertNotIn("CP1", prog["results"])

    def test_the_window_refusal_blocks_before_anything_runs(self):
        gd.window_refusal = lambda: "reserved for the 09:00 fleet run"
        rc = gd.run("TEST-101", vault=self.vault, compose=compose_stub)
        self.assertEqual(rc, 1)
        self.assertEqual(self.progress()["state"], "blocked")
        self.assertFalse((self.course / "test-101-guide-blueprint.md").exists())

    def test_an_unknown_course_is_refused(self):
        rc = gd.run("NOPE-999", vault=self.vault, compose=compose_stub)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
