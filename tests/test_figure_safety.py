#!/usr/bin/env python3
"""
Figures — the one thing a module carries that IS markup.

Everything else in a note is data to some parser: prose to a markdown renderer,
TeX to KaTeX, a path to a resolver. A figure is a fragment of SVG that a browser
will execute the semantics of, so it gets an allow-list at both ends — refused
by `lesson.validate_svg` before it is written, and rebuilt element by element by
`figure.tsx` when it is read, never injected.

Two ends means two copies of the same two sets, and a divergence between them is
the failure mode worth a test: the Python half refusing something the frontend
would happily draw is a hole, and the frontend dropping something Python allowed
is a diagram with a piece missing and no error anywhere.

So the cases below are mostly **refusals**. An allow-list that accepts the happy
path proves very little; what it has to do is say no.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import lesson as ln  # noqa: E402

FRONTEND = REPO / "interface" / "frontend" / "src" / "figure.tsx"

OK = ('<svg viewBox="0 0 240 160">'
      '<line x1="20" y1="80" x2="120" y2="80" stroke="currentColor" '
      'stroke-width="2" marker-end="url"/>'
      '<text x="60" y="70" text-anchor="middle" font-size="12">F = 40 N</text>'
      '</svg>')


def _js_set(name: str) -> set[str]:
    """Pull a `const NAME = new Set([...])` literal out of figure.tsx."""
    src = FRONTEND.read_text(encoding="utf-8")
    m = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", src, re.S)
    assert m, f"{name} not found in figure.tsx"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


class TestTheTwoCopiesAgree(unittest.TestCase):
    def test_the_tag_lists_are_identical(self):
        self.assertEqual(ln.SVG_TAGS, _js_set("TAGS"),
                         "lesson.py and figure.tsx disagree about which "
                         "elements a figure may contain")

    def test_the_attribute_lists_are_identical(self):
        self.assertEqual(ln.SVG_ATTRS, _js_set("ATTRS"),
                         "lesson.py and figure.tsx disagree about which "
                         "attributes survive")

    def test_nothing_dangerous_leaked_onto_the_allow_list(self):
        """The two sets are edited by hand. This is the check that a careless
        addition cannot quietly re-open the door."""
        for bad in ln.SVG_DANGEROUS:
            self.assertNotIn(bad, ln.SVG_TAGS, bad)
        for attr in ln.SVG_ATTRS:
            self.assertFalse(attr.lower().startswith("on"), attr)
            self.assertNotIn("href", attr.lower(), attr)


class TestItRefuses(unittest.TestCase):
    def refused(self, svg: str, because: str):
        errs = ln.validate_svg(svg)
        self.assertTrue(errs, f"ACCEPTED what it must refuse: {because}")
        return errs

    def test_a_script_element(self):
        self.refused('<svg viewBox="0 0 1 1"><script>alert(1)</script></svg>',
                     "a script element")

    def test_an_event_handler(self):
        self.refused('<svg viewBox="0 0 1 1"><rect onload="steal()"/></svg>',
                     "an onload handler")

    def test_a_link_out(self):
        self.refused('<svg viewBox="0 0 1 1"><a href="http://x"><rect/></a></svg>',
                     "an anchor with an href")

    def test_an_embedded_document(self):
        self.refused('<svg viewBox="0 0 1 1"><foreignObject/></svg>',
                     "a foreignObject")

    def test_a_remote_image(self):
        self.refused('<svg viewBox="0 0 1 1"><image href="http://x/a.png"/></svg>',
                     "an external image")

    def test_a_url_reference_in_a_paint(self):
        """`url(...)` can reach outside the document, so a figure that is
        supposed to be self-contained may not use one."""
        self.refused('<svg viewBox="0 0 1 1"><rect fill="url(http://x#a)"/></svg>',
                     "a url() paint")

    def test_a_javascript_scheme(self):
        self.refused('<svg viewBox="0 0 1 1"><rect fill="javascript:x"/></svg>',
                     "a javascript: value")

    def test_an_unknown_element(self):
        self.refused('<svg viewBox="0 0 1 1"><blink/></svg>', "an unknown element")

    def test_malformed_markup(self):
        """Well-formedness is required so that what was validated and what gets
        drawn cannot be two different documents."""
        self.refused('<svg viewBox="0 0 1 1"><rect>', "an unclosed element")

    def test_a_missing_viewbox(self):
        self.refused('<svg><rect/></svg>', "no viewBox")

    def test_something_that_is_not_an_svg(self):
        self.refused('<div>hello</div>', "a non-svg root")

    def test_nothing_at_all(self):
        self.refused("", "an empty figure")


class TestItAccepts(unittest.TestCase):
    def test_a_plausible_free_body_diagram(self):
        self.assertEqual(ln.validate_svg(OK), [])

    def test_currentcolor_is_not_mistaken_for_a_url(self):
        self.assertEqual(
            ln.validate_svg('<svg viewBox="0 0 1 1">'
                            '<rect fill="currentColor" stroke="currentColor"/></svg>'), [])


class TestTheGrammar(unittest.TestCase):
    """`figure::` parsing, in the shape a module actually writes it."""

    def module(self, body: str) -> dict:
        return ln.parse(
            "---\ntype: module\ncourse: AA-210\nmodule: 2\nunit: \n"
            "title: T\nestimate: 30\nsources:\n  - a.md\nverified: \n"
            "tags: [guide]\n---\n\n" + body)

    def test_a_figure_is_parsed_off_the_segment_head(self):
        d = self.module(
            "## S1 · T ⏱ 30\nsource:: a.md\n"
            f"figure:: Forces on the bracket at O\n{OK}\n\n"
            "### Summary\n\nx\n\n### Normal\n\ny\n\n### In depth\n\nz\n")
        seg = d["segments"][0]
        self.assertEqual(seg["figure"]["caption"], "Forces on the bracket at O")
        self.assertIn("<svg", seg["figure"]["svg"])
        self.assertEqual(d["problems"], [])

    def test_it_survives_a_round_trip(self):
        """Generated modules are canonicalised through serialize() on the way to
        disk, so a figure this dropped would be authored, validated and then
        silently deleted."""
        body = ("## S1 · T ⏱ 30\nsource:: a.md\n"
                f"figure:: A caption\n{OK}\n\n"
                "### Summary\n\nx\n\n### Normal\n\ny\n\n### In depth\n\nz\n")
        once = self.module(body)
        again = ln.parse(ln.serialize(once))
        self.assertEqual(again["segments"][0]["figure"]["caption"], "A caption")
        self.assertIn("<svg", again["segments"][0]["figure"]["svg"])

    def test_an_uncaptioned_figure_is_a_problem(self):
        d = self.module("## S1 · T ⏱ 30\nsource:: a.md\n"
                        f"figure::\n{OK}\n\n"
                        "### Summary\n\nx\n\n### Normal\n\ny\n\n### In depth\n\nz\n")
        self.assertTrue(any("no caption" in p for p in d["problems"]))

    def test_an_unclosed_figure_is_a_problem(self):
        d = self.module("## S1 · T ⏱ 30\nsource:: a.md\n"
                        'figure:: C\n<svg viewBox="0 0 1 1">\n')
        self.assertTrue(any("never closed" in p for p in d["problems"]))

    def test_two_figures_in_one_segment_is_a_problem(self):
        d = self.module("## S1 · T ⏱ 30\nsource:: a.md\n"
                        f"figure:: One\n{OK}\nfigure:: Two\n{OK}\n\n"
                        "### Summary\n\nx\n\n### Normal\n\ny\n\n### In depth\n\nz\n")
        self.assertTrue(any("one figure per segment" in p for p in d["problems"]))

    def test_a_bad_figure_is_held_by_validate(self):
        text = ("---\ntype: module\ncourse: AA-210\nmodule: 2\nunit: \n"
                "title: T\nestimate: 30\nsources:\n  - a.md\nverified: \n"
                "tags: [guide]\n---\n\n"
                "## S1 · T ⏱ 30\nsource:: a.md\n"
                'figure:: C\n<svg viewBox="0 0 1 1"><script/></svg>\n\n'
                "### Summary\n\nx\n\n### Normal\n\ny\n\n### In depth\n\nz\n")
        self.assertTrue(any("<script>" in e for e in ln.validate(text)))


if __name__ == "__main__":
    unittest.main()
