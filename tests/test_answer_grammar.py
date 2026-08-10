#!/usr/bin/env python3
"""
The confidence line — the one part of §4's answer grammar that spans the seam.

Everything else in the answer grammar is a rendering decision the frontend makes
alone. Confidence is not: the model has to emit it, `agent.ORIENTATION` is what
asks, and `answer.tsx` is what parses. Those two live in different languages in
different folders, and nothing but this file stops one from being edited without
the other — the failure would be silent and would look exactly like a model that
declined to state its confidence.

So the regex below is a **copy of the frontend's**, and the strings it is run
against are **extracted from the prompt itself** rather than written out here.
Change the shape in either place and this fails.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "interface" / "backend"))

import agent  # noqa: E402

FRONTEND = REPO / "interface" / "frontend" / "src" / "answer.tsx"

# Transliterated from answer.tsx's CONF_RE. JS `[\s\S]*` is Python's `.*` under
# DOTALL; the rest is character-for-character the same pattern.
CONF_RE = re.compile(
    r"^confidence::\s*(high|medium|low)\b\s*[-–—:]?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL)


def examples() -> list[str]:
    """The `confidence::` lines the orientation shows the model."""
    return [ln.strip() for ln in agent.ORIENTATION.splitlines()
            if ln.strip().startswith("confidence::")]


class TestTheOrientationAsks(unittest.TestCase):
    def test_it_names_the_key_and_all_three_levels(self):
        o = agent.ORIENTATION
        self.assertIn("confidence::", o)
        for level in ("high", "medium", "low"):
            self.assertIn(level, o, level)

    def test_it_shows_one_example_per_level(self):
        got = {CONF_RE.match(e).group(1).lower() for e in examples()}
        self.assertEqual(got, {"high", "medium", "low"},
                         "a level with no worked example is a level the model "
                         "will format its own way")

    def test_every_example_carries_a_reason(self):
        """A bare level is exactly what §4 forbids — the sentence saying what
        the word means here is the element; the dots are how you find it."""
        for e in examples():
            with self.subTest(e=e):
                self.assertTrue(CONF_RE.match(e).group(2).strip(),
                                "example has no justification after the level")

    def test_it_says_where_the_line_goes_and_what_happens_to_it(self):
        # Collapsed, because the prompt is hard-wrapped for the human reading
        # it and a phrase may straddle a newline. The test should not be the
        # reason a sentence gets reflowed.
        o = " ".join(agent.ORIENTATION.lower().split())
        self.assertIn("last line", o)
        # The renderer strips it, so the model must be told not to say it twice.
        self.assertIn("strips it", o)
        # Omission has to be legal, or a model with nothing to say invents one.
        self.assertIn("leave the line out", o)

    def test_it_still_asks_for_a_claim_first(self):
        """§2.3 — the headline is the finding. `splitClaim` promotes the first
        paragraph, which is only right if the prompt asks for one."""
        self.assertIn("one sentence stating the finding",
                      " ".join(agent.ORIENTATION.split()))


class TestTheFrontendParses(unittest.TestCase):
    """The regex here is a copy; these cases prove the copy is honest by
    running it over the prompt's own examples and the shapes a model drifts to."""

    def test_the_prompts_own_examples_parse(self):
        for e in examples():
            with self.subTest(e=e):
                self.assertIsNotNone(CONF_RE.match(e))

    def test_the_dashes_a_model_reaches_for_anyway(self):
        for dash in ("-", "–", "—", ":"):
            line = f"confidence:: medium {dash} two notes are stale."
            with self.subTest(dash=dash):
                m = CONF_RE.match(line)
                self.assertEqual(m.group(1), "medium")
                self.assertEqual(m.group(2), "two notes are stale.")

    def test_a_level_with_no_reason_still_parses(self):
        """Degrading to dots-and-a-word beats dropping the element entirely."""
        m = CONF_RE.match("confidence:: high")
        self.assertEqual((m.group(1), m.group(2).strip()), ("high", ""))

    def test_a_made_up_level_is_not_matched(self):
        self.assertIsNone(CONF_RE.match("confidence:: fairly sure - hmm"))
        self.assertIsNone(CONF_RE.match("confidence:: highish - no"))

    def test_the_frontend_regex_has_not_drifted_from_this_copy(self):
        """Reads answer.tsx and checks the literal is still the one above. A
        comment claiming two things match is worth less than a test that looks."""
        src = FRONTEND.read_text(encoding="utf-8")
        self.assertIn(
            r"/^confidence::\s*(high|medium|low)\b\s*[-–—:]?\s*([\s\S]*)$/im",
            src,
            "answer.tsx's CONF_RE changed — update the copy in this test and "
            "re-check the cases below still describe what the model emits")


if __name__ == "__main__":
    unittest.main()
