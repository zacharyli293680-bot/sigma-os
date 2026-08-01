"""
Dev log (Phase 6): where the next run starts, and what it refuses to apply.

The vetting tests carry most of the weight here. Every auto-applied proposal
before this one *created* a note; a dev log entry **updates** a hub note Zach
wrote by hand, and the model has to hand back the whole file to add one bullet to
it. So the failure mode is not "a bad new note appears" — which is obvious and
deletable — but "a section of his writing quietly stops existing". `_vet` is the
only thing standing between a sloppy round-trip and that, so each of its rules
gets a test that proves it fires.

The range tests matter for a different reason: `sigma-os.md` has eight months of
dev log entries written before this module existed, none of them stamped with a
sha. Reading that as "never logged" would have asked the model to write up a
history the note already documents.
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import devlog                                    # noqa: E402


HUB = """---
type: project
area: personal
status: active
repo: C:\\Users\\tusha\\Documents\\CS Projects\\demo
tags: [project]
---

# Demo

**Code:** `C:\\Users\\tusha\\Documents\\CS Projects\\demo`

## Tasks
- [ ] Ship the thing 📅 2026-08-04

## Dev log
- **2026-07-01** — Started it. _(through `aaaaaaa`)_
- **2026-07-20** — Kept going, and this one runs to a second line
  because entries here do that.

## Decisions
- Chose SQLite over Postgres.

## Related
- [[projects|Projects MOC]]
"""


class Section(unittest.TestCase):
    def test_section_is_bounded_by_the_next_h2(self):
        body = devlog.dev_log_section(HUB)
        self.assertIn("2026-07-20", body)
        self.assertNotIn("SQLite", body)

    def test_no_dev_log_section_is_empty_not_an_error(self):
        self.assertEqual(devlog.dev_log_section("# Just a note\n\nprose\n"), "")


class WhereToStart(unittest.TestCase):
    def test_a_stamp_wins_and_document_order_decides(self):
        sha, date = devlog.last_logged(HUB)
        self.assertEqual(sha, "aaaaaaa")
        self.assertIsNone(date)

    def test_the_last_stamp_in_the_file_is_the_one_used(self):
        text = HUB.replace("  because entries here do that.",
                           "  because entries here do that. _(through `bbbbbbb`)_")
        self.assertEqual(devlog.last_logged(text)[0], "bbbbbbb")

    def test_an_unstamped_log_falls_back_to_its_last_date(self):
        """The bootstrap: every hand-written dev log predates the sha stamp."""
        text = re.sub(r" _\(through `\w+`\)_", "", HUB)
        sha, date = devlog.last_logged(text)
        self.assertIsNone(sha)
        self.assertEqual(date, "2026-07-20")

    def test_an_empty_log_means_the_whole_history(self):
        text = HUB.replace("- **2026-07-01** — Started it. _(through `aaaaaaa`)_", "-")
        text = re.sub(r"- \*\*2026-07-20\*\*.*?\n  because.*?\n", "", text, flags=re.S)
        sha, date = devlog.last_logged(text)
        self.assertIsNone(sha)
        self.assertIsNone(date)

    def test_span_names_which_boundary_it_used(self):
        self.assertIn("the last entry's stamp",
                      devlog._span({"n": 3, "base": "aaaaaaa", "since": None}))
        self.assertIn("date of the last dev log entry",
                      devlog._span({"n": 3, "base": None, "since": "2026-07-20"}))
        self.assertIn("whole history",
                      devlog._span({"n": 3, "base": None, "since": None}))


class EntryFrom(unittest.TestCase):
    """The model is asked for prose and told the runner adds the date, the bullet
    and the stamp — but it has just been shown five entries in the full form, so
    it sometimes hands one back that way. Stripping is cheaper than refusing."""

    def _e(self, said):
        return devlog.entry_from(said, "ccccccc", "2026-07-31")

    def test_the_tags_discard_everything_around_them(self):
        """The real failure: the first sigma-os run opened with "I can't read the
        repo itself…" and that sentence went into the note."""
        got = self._e("I can't read the repo itself — only the commit subjects "
                      "are available to me. I'll write the entry from those.\n\n"
                      "<entry>Phase 5 landed the sync boundary, and the two halves "
                      "fail in opposite directions on purpose.</entry>\n\n"
                      "Let me know if you'd like it shorter.")
        self.assertTrue(got.startswith("- **2026-07-31** — Phase 5 landed"))
        self.assertNotIn("I can't read", got)
        self.assertNotIn("Let me know", got)

    def test_the_refusal_word_wins_over_stray_prose(self):
        self.assertIsNone(self._e("Looking at these two commits — NOTHING here is "
                                  "worth an entry."))

    def test_the_refusal_word_inside_an_entry_is_not_a_refusal(self):
        got = self._e("<entry>NOTHING about the parser changed, but the auth stack "
                      "was rebuilt around a public read allowlist.</entry>")
        self.assertIsNotNone(got)
        self.assertIn("auth stack", got)

    def test_plain_prose_gets_dated_bulleted_and_stamped(self):
        got = self._e("Added the auth stack and locked public reads to an allowlist.")
        self.assertTrue(got.startswith("- **2026-07-31** — Added the auth"))
        self.assertTrue(got.endswith("_(through `ccccccc`)_"))

    def test_a_self_written_bullet_and_date_are_not_doubled(self):
        got = self._e("- **2026-07-31** — Added the auth stack and the allowlist "
                      "that goes with it.")
        self.assertEqual(got.count("2026-07-31"), 1)
        self.assertFalse(got.startswith("- - "))

    def test_a_self_written_stamp_is_not_doubled(self):
        got = self._e("Added the auth stack and the public read allowlist beside "
                      "it. _(through `ccccccc`)_")
        self.assertEqual(got.count("through"), 1)

    def test_a_code_fence_is_unwrapped(self):
        got = self._e("```markdown\nAdded the auth stack and the read allowlist.\n```")
        self.assertNotIn("```", got)
        self.assertIn("Added the auth stack", got)

    def test_the_refusal_word_declines(self):
        self.assertIsNone(self._e("NOTHING"))
        self.assertIsNone(self._e("NOTHING worth recording here."))

    def test_a_too_short_answer_declines_rather_than_landing(self):
        self.assertIsNone(self._e("Bumped deps."))

    def test_newlines_collapse_to_one_bullet(self):
        got = self._e("First the scaffold.\n\nThen the parser that reads it, which "
                      "is the part that matters.")
        self.assertNotIn("\n", got)


class Splice(unittest.TestCase):
    """A splice is a rule: it inserts between two known offsets, so it cannot
    drop a section or reword a decision the way a re-transcription can."""

    ENTRY = "- **2026-07-31** — Did the thing. _(through `ccccccc`)_"

    def test_it_appends_after_the_last_entry(self):
        got = devlog.splice(HUB, self.ENTRY)
        log = devlog.dev_log_section(got)
        self.assertTrue(log.rstrip().endswith("_(through `ccccccc`)_"))
        self.assertIn("2026-07-01", log)
        self.assertIn("2026-07-20", log)

    def test_it_keeps_every_other_section_byte_for_byte(self):
        got = devlog.splice(HUB, self.ENTRY)
        for chunk in ("## Tasks\n- [ ] Ship the thing 📅 2026-08-04",
                      "## Decisions\n- Chose SQLite over Postgres.",
                      "repo: C:\\Users\\tusha\\Documents\\CS Projects\\demo"):
            self.assertIn(chunk, got)
        self.assertEqual(len(devlog.H2.findall(HUB)), len(devlog.H2.findall(got)))

    def test_the_empty_placeholder_is_replaced_not_kept(self):
        hub = HUB.replace(
            "- **2026-07-01** — Started it. _(through `aaaaaaa`)_\n"
            "- **2026-07-20** — Kept going, and this one runs to a second line\n"
            "  because entries here do that.\n", "-\n")
        got = devlog.splice(hub, self.ENTRY)
        log = devlog.dev_log_section(got)
        self.assertNotIn("\n-\n", "\n" + log + "\n")
        self.assertTrue(log.strip().startswith("- **2026-07-31**"))

    def test_a_note_with_no_dev_log_gains_one_before_decisions(self):
        hub = re.sub(r"## Dev log\n.*?\n\n## Decisions", "## Decisions", HUB, flags=re.S)
        self.assertNotIn("## Dev log", hub)
        got = devlog.splice(hub, self.ENTRY)
        self.assertLess(got.index("## Dev log"), got.index("## Decisions"))
        self.assertIn("- Chose SQLite over Postgres.", got)

    def test_a_note_with_no_headings_at_all_is_refused(self):
        self.assertIsNone(devlog.splice("just prose, no structure\n", self.ENTRY))

    def test_the_spliced_note_passes_its_own_vet(self):
        """The splice and the vet must agree — a vet that refuses every splice
        would make the feature fail closed and look like a model problem."""
        v = Vet("test_a_clean_append_passes")
        v.setUp()
        try:
            got = devlog.splice(HUB, self.ENTRY)
            self.assertIsNone(devlog._vet(v._prop(got), v.job))
        finally:
            v.tearDown()


class Vet(unittest.TestCase):
    """Each rule, proven to fire. A refusal leaves the proposal pending, so a
    false positive costs a hand-merge and a false negative costs Zach's writing —
    which is why these lean strict."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.hub = self.root / "03-Projects" / "demo.md"
        self.hub.parent.mkdir(parents=True)
        self.hub.write_text(HUB, encoding="utf-8")

        self._saved = (devlog.VAULT, devlog.rf.VAULT)
        devlog.VAULT = devlog.rf.VAULT = self.root

        self.job = {"path": self.hub, "text": HUB, "head": "ccccccc",
                    "base": "aaaaaaa", "since": None, "name": "demo"}
        # A well-formed entry appended to the log — the case that must pass.
        self.good = HUB.replace(
            "\n## Decisions",
            "\n- **2026-07-31** — Did the next thing. _(through `ccccccc`)_\n\n## Decisions")

    def tearDown(self):
        devlog.VAULT, devlog.rf.VAULT = self._saved
        self.tmp.cleanup()

    def _prop(self, content, target="03-Projects/demo.md", kind="note"):
        p = self.root / "p.md"
        p.write_text(
            f"---\ntype: proposal\nstatus: pending\nkind: {kind}\n"
            f"target: {target}\nrisk: low\ntags: [proposal]\n---\n\n"
            f"{devlog.rf.CONTENT_MARKER}\n````markdown\n{content}\n````\n\n"
            f"## How to approve\n", encoding="utf-8")
        return p

    def test_a_clean_append_passes(self):
        self.assertIsNone(devlog._vet(self._prop(self.good), self.job))

    def test_a_dropped_section_is_refused(self):
        bad = self.good.replace("## Decisions\n- Chose SQLite over Postgres.\n", "")
        # Padded so it is the missing heading that trips, not the length rule.
        bad += "\n" + "- filler filler filler\n" * 20
        self.assertIn("Decisions", devlog._vet(self._prop(bad), self.job))

    def test_a_dropped_dev_log_entry_is_refused(self):
        bad = self.good.replace(
            "- **2026-07-01** — Started it. _(through `aaaaaaa`)_\n", "")
        bad += "- filler filler filler filler filler\n" * 10
        why = devlog._vet(self._prop(bad), self.job)
        self.assertIn("2026-07-01", why)

    def test_a_shorter_note_is_refused(self):
        self.assertIn("shorter", devlog._vet(self._prop("# Demo\n\ntiny\n"), self.job))

    def test_an_unstamped_entry_is_refused(self):
        """Without the stamp the next run rewrites the same work again."""
        bad = self.good.replace(" _(through `ccccccc`)_", "")
        self.assertIn("stamped", devlog._vet(self._prop(bad), self.job))

    def test_a_rewritten_repo_line_is_refused(self):
        bad = self.good.replace("repo: C:\\Users\\tusha\\Documents\\CS Projects\\demo",
                                "repo: C:\\somewhere\\else")
        self.assertIn("repo:", devlog._vet(self._prop(bad), self.job))

    def test_a_dropped_repo_line_is_refused(self):
        bad = self.good.replace(
            "repo: C:\\Users\\tusha\\Documents\\CS Projects\\demo\n", "")
        bad += "- filler filler filler filler\n" * 10
        self.assertIn("repo:", devlog._vet(self._prop(bad), self.job))

    def test_another_target_is_refused(self):
        """A dev log run may touch exactly one note: the hub it was given."""
        why = devlog._vet(self._prop(self.good, target="CLAUDE.md"), self.job)
        self.assertIn("may only touch", why)

    def test_a_target_escaping_the_vault_is_refused(self):
        why = devlog._vet(self._prop(self.good, target="../../etc/passwd"), self.job)
        self.assertIn("may only touch", why)

    def test_a_non_note_kind_is_refused(self):
        why = devlog._vet(self._prop(self.good, kind="contract"), self.job)
        self.assertIn("not note", why)

    def test_an_empty_content_block_is_refused(self):
        self.assertIn("empty", devlog._vet(self._prop(""), self.job))


class Ranges(unittest.TestCase):
    """`gather` against a real repo — the empty-tree fallback is the part that
    would otherwise show a one-commit project as having changed no files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = Path(self.tmp.name)
        self._git("init", "-b", "main")
        self._git("config", "user.name", "T")
        self._git("config", "user.email", "t@e.com")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "first commit")
        self.first = self._git("rev-parse", "--short", "HEAD").stdout.strip()
        (self.repo / "b.txt").write_text("two\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "second commit")

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_whole_history_shows_the_root_commits_files(self):
        got = devlog.gather(self.repo, None)
        self.assertEqual(got["n"], 2)
        self.assertIn("a.txt", got["churn"])       # the empty-tree fallback
        self.assertIn("b.txt", got["churn"])

    def test_commits_come_back_oldest_first(self):
        got = devlog.gather(self.repo, None)
        self.assertIn("first commit", got["commits"][0])
        self.assertIn("second commit", got["commits"][1])

    def test_a_base_excludes_what_it_already_covered(self):
        got = devlog.gather(self.repo, self.first)
        self.assertEqual(got["n"], 1)
        self.assertIn("second commit", got["commits"][0])
        self.assertNotIn("a.txt", got["churn"])

    def test_a_stale_sha_is_detected_rather_than_crashing_the_run(self):
        self.assertFalse(devlog._rev_ok(self.repo, "0" * 40))
        self.assertTrue(devlog._rev_ok(self.repo, self.first))


if __name__ == "__main__":
    unittest.main()
