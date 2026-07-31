"""
Study intake (Phase 6): what it agrees to read, and when it lets go of a source.

Two things here are worth more than the rest. The privacy test, because intake
is the one place where *script code* reads a file straight into a prompt — the
agent never calls Read, so the PreToolUse guard that protects every other path
is structurally blind to this one. And the retention tests, because the first
real run cleared the drop folder after the model finished but before the note
landed, and a source deleted with nothing to show for it is the one failure that
loses work rather than postponing it.
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

import intake                                   # noqa: E402
import privacy                                  # noqa: E402


def _run(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8")


class IntakeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        (self.vault / "02-Areas" / "Academics" / "CSE-311").mkdir(parents=True)
        (self.vault / "02-Areas" / "Academics" / "MATH-208").mkdir(parents=True)
        self.drop = self.vault / "00-Inbox" / "intake"
        (self.drop / "CSE-311").mkdir(parents=True)

        (self.vault / ".gitignore").write_text("secret/\n", encoding="utf-8")
        _run(self.vault, "init", "-b", "master")
        _run(self.vault, "config", "user.name", "T")
        _run(self.vault, "config", "user.email", "t@e.com")
        _run(self.vault, "add", "-A")
        _run(self.vault, "commit", "-m", "seed")

        self._saved = (intake.VAULT, intake.DROP, intake.ATTACH,
                       intake.COURSES_ROOT, privacy._RUNTIME)
        intake.VAULT = self.vault
        intake.DROP = self.drop
        intake.ATTACH = self.vault / "99-Meta" / "Attachments"
        intake.COURSES_ROOT = self.vault / "02-Areas" / "Academics"
        privacy._RUNTIME = self.root
        (self.root / "privacy.config.json").write_text(
            json.dumps({"model_allow": []}), encoding="utf-8")
        self._bust()

    def _bust(self):
        privacy.model_allow_raw.cache_clear()
        privacy.model_allow_prefixes.cache_clear()
        privacy.VaultPrivacy._git_ignored.cache_clear()

    def tearDown(self):
        (intake.VAULT, intake.DROP, intake.ATTACH,
         intake.COURSES_ROOT, privacy._RUNTIME) = self._saved
        self._bust()
        self.tmp.cleanup()

    def drop_file(self, rel: str, body: str = "x" * 500) -> Path:
        p = self.drop / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p


class TestScan(IntakeBase):
    def test_a_course_subfolder_is_picked_up(self):
        self.drop_file("CSE-311/week-4.md")
        items, problems = intake.scan()
        self.assertEqual([(p.name, c) for p, c in items], [("week-4.md", "CSE-311")])
        self.assertEqual(problems, [])

    def test_course_folder_matching_is_case_insensitive_but_files_use_the_real_name(self):
        self.drop_file("cse-311/week-4.md")
        items, _ = intake.scan()
        self.assertEqual(items[0][1], "CSE-311")   # not "cse-311"

    def test_an_unknown_course_is_reported_never_guessed(self):
        """The contract's filing rule: do not invent a location."""
        self.drop_file("CSE-999/mystery.md")
        items, problems = intake.scan()
        self.assertEqual(items, [])
        self.assertTrue(any("no such course folder" in why for _, why in problems))

    def test_a_top_level_file_is_reported(self):
        self.drop_file("loose.md")
        items, problems = intake.scan()
        self.assertEqual(items, [])
        self.assertTrue(any("top level" in why for _, why in problems))

    def test_an_unsupported_type_is_reported(self):
        # .docx, not .pptx — slides became supported on 2026-07-31 and this
        # fixture was the first thing to notice.
        self.drop_file("CSE-311/essay.docx")
        items, problems = intake.scan()
        self.assertEqual(items, [])
        self.assertTrue(any("unsupported type" in why for _, why in problems))


class TestPrivacyBoundary(IntakeBase):
    """Intake reads files into a prompt itself, so it must apply the model
    boundary itself. Nothing else in the system would catch this."""

    def test_a_gitignored_source_is_refused(self):
        (self.vault / ".gitignore").write_text(
            "secret/\n00-Inbox/intake/CSE-311/\n", encoding="utf-8")
        self._bust()
        self.drop_file("CSE-311/private.md")
        items, problems = intake.scan()
        self.assertEqual(items, [], "a gitignored file reached the intake queue")
        self.assertTrue(any("refused at the model boundary" in why
                            for _, why in problems), problems)

    def test_an_exempted_gitignored_source_is_allowed(self):
        """Option B's exemption applies here too — if the model may read it,
        intake may feed it."""
        (self.vault / ".gitignore").write_text(
            "00-Inbox/intake/CSE-311/\n", encoding="utf-8")
        (self.root / "privacy.config.json").write_text(
            json.dumps({"model_allow": ["00-Inbox/intake/CSE-311/"]}), encoding="utf-8")
        self._bust()
        self.drop_file("CSE-311/allowed.md")
        items, problems = intake.scan()
        self.assertEqual(len(items), 1, problems)


class TestExtract(IntakeBase):
    def test_long_text_is_truncated_and_says_so(self):
        p = self.drop_file("CSE-311/big.txt", "y" * (intake.TEXT_BUDGET + 5000))
        text, truncated = intake.extract(p)
        self.assertTrue(truncated)
        self.assertEqual(len(text), intake.TEXT_BUDGET)

    def test_short_text_is_not_flagged(self):
        p = self.drop_file("CSE-311/small.txt", "z" * 300)
        _, truncated = intake.extract(p)
        self.assertFalse(truncated)


def _has_pptx():
    try:
        import pptx  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_pptx(), "python-pptx not installed")
class TestPptx(IntakeBase):
    """Lecture slides. 49 of AA-210's 125 source documents are decks, so this
    path carries a large share of the course's material."""

    def deck(self, name="d.pptx", slides=3, footer="Copyright (c) 2020"):
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        for i in range(slides):
            s = prs.slides.add_slide(prs.slide_layouts[5])   # title only
            s.shapes.title.text = f"Topic {i + 1}"
            box = s.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(3))
            tf = box.text_frame
            tf.text = f"point {i + 1}a"
            tf.add_paragraph().text = f"point {i + 1}b"
            if footer:                       # master-slide furniture on every slide
                tf.add_paragraph().text = footer
            s.notes_slide.notes_text_frame.text = f"note {i + 1}"
        p = self.drop / "CSE-311" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        prs.save(str(p))
        return p

    def test_slide_structure_is_kept(self):
        text = intake.extract_pptx(self.deck())
        self.assertIn("### Slide 1 — Topic 1", text)
        self.assertIn("### Slide 3 — Topic 3", text)
        self.assertIn("- point 2a", text)

    def test_speaker_notes_are_included(self):
        """Often the only place a deck explains itself rather than listing."""
        text = intake.extract_pptx(self.deck())
        self.assertIn("> Speaker notes: note 2", text)

    def test_repeated_footer_is_stripped(self):
        text = intake.extract_pptx(self.deck(slides=6))
        self.assertNotIn("Copyright", text)
        self.assertIn("- point 1a", text)     # real content survives

    def test_a_line_on_only_one_slide_is_content_not_furniture(self):
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        for i in range(6):
            s = prs.slides.add_slide(prs.slide_layouts[5])
            s.shapes.title.text = f"T{i}"
            tf = s.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(2)).text_frame
            tf.text = "shared footer"
            if i == 0:
                tf.add_paragraph().text = "a unique and important claim"
        p = self.drop / "CSE-311" / "u.pptx"
        prs.save(str(p))
        text = intake.extract_pptx(p)
        self.assertNotIn("shared footer", text)
        self.assertIn("a unique and important claim", text)

    def test_pptx_is_accepted_by_the_scanner(self):
        self.deck(name="lecture.pptx")
        items, problems = intake.scan()
        self.assertEqual([p.name for p, _ in items], ["lecture.pptx"], problems)

    def test_extract_routes_pptx_and_reports_length(self):
        text, truncated = intake.extract(self.deck())
        self.assertFalse(truncated)
        self.assertIn("### Slide 1", text)


class TestSourceRetention(IntakeBase):
    """When may intake clear the drop folder? Only once a note actually reached
    the vault. Proposing is not landing."""

    def _fake_run(self, result, applied):
        async def fake_intake_one(path, course, model="sonnet"):
            return result
        import applier
        self._orig = (intake.intake_one, applier.apply_run)
        intake.intake_one = fake_intake_one
        applier.apply_run = lambda files, actor: applied
        self.addCleanup(self._restore)

    def _restore(self):
        import applier
        intake.intake_one, applier.apply_run = self._orig

    def test_source_is_cleared_when_a_note_lands(self):
        src = self.drop_file("CSE-311/week-4.md")
        self._fake_run({"ok": True, "proposals": 1, "files": ["p.md"], "summary": ""},
                       [{"action": "create", "target": "x.md", "reason": None}])
        intake.run()
        self.assertFalse(src.exists())

    def test_source_survives_when_applying_fails(self):
        """The regression: the first real run proposed a note, could not apply
        it because git hung, and cleared the source anyway."""
        src = self.drop_file("CSE-311/week-4.md")
        self._fake_run({"ok": True, "proposals": 1, "files": ["p.md"], "summary": ""},
                       [])
        rc = intake.run()
        self.assertTrue(src.exists(), "source was cleared with nothing applied")
        self.assertEqual(rc, 1)

    def test_source_survives_when_the_model_proposes_nothing(self):
        src = self.drop_file("CSE-311/week-4.md")
        self._fake_run({"ok": True, "proposals": 0, "files": [], "summary": ""}, [])
        intake.run()
        self.assertTrue(src.exists())

    def test_a_held_proposal_still_counts_as_landed(self):
        """A hold means the change is staged and recorded, not lost."""
        src = self.drop_file("CSE-311/week-4.md")
        self._fake_run({"ok": True, "proposals": 1, "files": ["p.md"], "summary": ""},
                       [{"action": "held", "target": "x.md", "reason": "already exists"}])
        intake.run()
        self.assertFalse(src.exists())

    def test_keep_leaves_the_source_alone(self):
        src = self.drop_file("CSE-311/week-4.md")
        self._fake_run({"ok": True, "proposals": 1, "files": ["p.md"], "summary": ""},
                       [{"action": "create", "target": "x.md", "reason": None}])
        intake.run(keep=True)
        self.assertTrue(src.exists())


if __name__ == "__main__":
    unittest.main()
