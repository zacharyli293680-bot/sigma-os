#!/usr/bin/env python3
"""
Multiple-choice options — the shape the prompt asks for, read by the renderer.

This is a contract between two files in two languages. `MODULE_PROMPT` tells
the model "for mcq include options A)-D)", and `workbench.tsx` has to find
those options twice over: once in `Rich` (rich.tsx), to keep each on its own
line, and once in `mcqLetters` (workbench.tsx), to offer them as choices.

It was wrong in both places at once, and silently. `mcqLetters` matched
`/\\(([a-h])\\)/` — parenthesised, lower case — which no generated note has ever
contained, so every mcq found zero options and fell back to reveal-the-answer.
Nothing errored: a question with no options is a legal question. And `Rich`
joined the four option lines into one paragraph, so the fallback rendered as
"points along: A) A x B B) B x A C) A . B D) The angle...".

Neither half announces a mismatch, which is exactly why the agreement is
pinned here rather than left to be noticed. Same reason `test_figure_safety`
pins the two SVG allow-lists against each other.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import guide as gd  # noqa: E402

RICH = REPO / "interface" / "frontend" / "src" / "rich.tsx"


def option_re() -> re.Pattern:
    """`OPTION_RE` out of workbench.tsx, as a Python regex.

    The two engines agree on this subset — anchors, a character class, an
    escaped literal class and `\\s`/`\\S` — so the pattern transfers verbatim.
    """
    src = RICH.read_text(encoding="utf-8")
    m = re.search(r"^export const OPTION_RE = /(.+)/;\s*$", src, re.M)
    assert m, "OPTION_RE not found in rich.tsx"
    return re.compile(m.group(1))


class TestTheRendererReadsWhatThePromptAsksFor(unittest.TestCase):
    def setUp(self):
        self.re = option_re()

    def test_the_prompt_still_asks_for_this_shape(self):
        """If the authoring prompt ever changes the option shape, this test is
        the one that has to be looked at before the renderer silently stops
        finding them again."""
        self.assertIn("options A)-D)".replace("-", "–"),
                      gd.MODULE_PROMPT + gd.CHECKPOINT_PROMPT)

    def test_the_shape_the_prompt_asks_for_is_matched(self):
        for line in ("A) $\\mathbf{A}\\times\\mathbf{B}$",
                     "B) The angle between them",
                     "C) 12 N·m",
                     "D) None of these"):
            with self.subTest(line=line):
                self.assertTrue(self.re.match(line), line)

    def test_the_letter_comes_back_for_grading(self):
        self.assertEqual(self.re.match("C) 12 N·m").group(1), "C")

    def test_the_older_shapes_still_match(self):
        """Notes are hand-writable, so the reader accepts what a person would
        plausibly type — not only what the generator emits."""
        for line in ("(a) first", "a) first", "A. first", "(C) third"):
            with self.subTest(line=line):
                self.assertTrue(self.re.match(line), line)

    def test_prose_is_not_an_option_list(self):
        """The old pattern was unanchored, so any parenthetical letter counted.
        A sentence that happens to contain one is not a four-way question."""
        for line in ("The couple (a) is free to slide along its line.",
                     "Resolve it into (x) and (y) components.",
                     "A moment about O is r x F.",
                     "$A_x$ is the component along x."):
            with self.subTest(line=line):
                self.assertIsNone(self.re.match(line), line)


class TestFigureLabels(unittest.TestCase):
    """A `<text>` label is the one place a note writes maths as characters —
    SVG cannot hold LaTeX. The first generated figures labelled a projection
    `A_perp` and an angle `theta`, which reads as source code on the page."""

    def test_both_prompts_rule_out_underscore_notation(self):
        for name, prompt in (("MODULE_PROMPT", gd.MODULE_PROMPT),
                             ("CHECKPOINT_PROMPT", gd.CHECKPOINT_PROMPT)):
            with self.subTest(prompt=name):
                self.assertIn("A_perp", prompt, "the rule names what to avoid")
                self.assertIn("θ", prompt, "the rule shows what to write")


if __name__ == "__main__":
    unittest.main()
