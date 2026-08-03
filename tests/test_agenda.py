"""
The calendar resolver (agenda.py).

Three claims, and they fail in different directions.

The **grammar** claim is that a line means exactly one thing. Those tests are
strict: a close-but-wrong line must parse as None rather than as an event on
some other day, because the write path's "must re-parse as the same kind of
occurrence" hold is only worth anything if parsing is not generous. The
round-trip pass is the same claim from the other side — everything the writer
will emit has to come back identical.

The **expansion** claim is that a rule is a rule and never a set of notes. Month
boundaries, until-dates and exceptions are where an expander is wrong, so those
are pinned rather than sampled.

The **merge** claim is that nothing arrives without provenance and nothing
disagrees silently. Those are the adversarial ones: a sealed note must not
appear at all, an exempt one must appear *marked*, and a note whose own task
contradicts it must produce two flagged occurrences rather than one quiet
winner.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))

import agenda  # noqa: E402
import todo  # noqa: E402

OPEN = lambda v, rels: (set(), set())            # noqa: E731 — nothing sealed


def vault_with(files: dict) -> Path:
    """A throwaway vault. Keys are vault-relative posix paths."""
    root = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def month(*lines: str) -> str:
    return ("---\ntype: calendar-month\nmonth: 2026-08\ntags: [calendar]\n---\n\n"
            "## Events\n" + "\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# the event grammar
# --------------------------------------------------------------------------

class TestParseEvent(unittest.TestCase):
    def test_the_four_canonical_forms(self):
        timed = agenda.parse_event("- 2026-08-04 14:30–15:30 Dentist ^sg-evt-8f2a1c04")
        self.assertEqual((timed["date"], timed["start"], timed["end"]),
                         ("2026-08-04", "14:30", "15:30"))
        self.assertEqual(timed["title"], "Dentist")
        self.assertEqual(timed["block_id"], "sg-evt-8f2a1c04")
        self.assertFalse(timed["all_day"])

        allday = agenda.parse_event("- 2026-08-06 Flight to SFO ^sg-evt-91c3ade7")
        self.assertTrue(allday["all_day"])
        self.assertIsNone(allday["start"])
        self.assertEqual(allday["title"], "Flight to SFO")

        span = agenda.parse_event("- 2026-08-12→2026-08-15 Family visit ^sg-evt-a77d2b19")
        self.assertEqual(span["end_date"], "2026-08-15")

        open_ended = agenda.parse_event("- 2026-08-19 09:00– Career fair ^sg-evt-c04b6e83")
        self.assertEqual(open_ended["start"], "09:00")
        self.assertIsNone(open_ended["end"])
        self.assertTrue(open_ended["open_ended"])

    def test_ascii_punctuation_parses_and_pads(self):
        """A hand-edit in Obsidian uses whatever the keyboard has."""
        a = agenda.parse_event("- 2026-08-04 9:05-10:00 Standup")
        self.assertEqual((a["start"], a["end"]), ("09:05", "10:00"))
        b = agenda.parse_event("- 2026-08-12->2026-08-15 Family visit")
        self.assertEqual(b["end_date"], "2026-08-15")

    def test_block_id_is_optional_and_not_swallowed_into_the_title(self):
        no_id = agenda.parse_event("- 2026-08-04 Dentist")
        self.assertIsNone(no_id["block_id"])
        self.assertEqual(no_id["title"], "Dentist")
        with_id = agenda.parse_event("- 2026-08-04 Dentist ^sg-evt-8f2a1c04")
        self.assertEqual(with_id["title"], "Dentist")

    def test_refuses_what_is_not_an_event(self):
        for line in [
            "",
            "- Dentist",                                  # no date
            "- [ ] a task 📅 2026-08-04",                 # a checkbox is not an event
            "## Events",                                  # a heading
            "- 2026-13-04 Dentist",                       # month 13
            "- 2026-08-04 25:00 Dentist",                 # not a time
            "- 2026-08-04 14:30–13:00 Dentist",           # ends before it starts
            "- 2026-08-15→2026-08-12 Backwards",          # span ends before it starts
            "- 2026-08-04 ^sg-evt-8f2a1c04",                  # no title
        ]:
            with self.subTest(line=line):
                self.assertIsNone(agenda.parse_event(line))

    def test_a_bad_time_is_a_refusal_not_a_title(self):
        """The failure that matters: 25:00 must not silently become part of the
        title, which would file the event as all-day and lose the time."""
        self.assertIsNone(agenda.parse_event("- 2026-08-04 25:00 Dentist"))


class TestRoundTrip(unittest.TestCase):
    """Every line the writer will emit must parse back to the same occurrence."""

    CASES = [
        dict(date="2026-08-04", title="Dentist", start="14:30", end="15:30"),
        dict(date="2026-08-06", title="Flight to SFO"),
        dict(date="2026-08-12", title="Family visit", end_date="2026-08-15"),
        dict(date="2026-08-19", title="Career fair", start="09:00"),
        dict(date="2026-12-31", title="Title with – a dash in it", start="23:00",
             end="23:59"),
        dict(date="2026-08-04", title="Ambiguous 2026-08-05 in the title"),
        dict(date="2026-08-04", title="Trailing caret ^ in the title"),
    ]

    def test_compose_then_parse_is_identity(self):
        for case in self.CASES:
            with self.subTest(**case):
                bid = agenda.new_block_id(case["date"], case["title"])
                line = agenda.compose_event(block_id=bid, **case)
                got = agenda.parse_event(line)
                self.assertIsNotNone(got, f"did not re-parse: {line!r}")
                self.assertEqual(got["date"], case["date"])
                self.assertEqual(got["title"], case["title"])
                self.assertEqual(got["start"], case.get("start"))
                self.assertEqual(got["end"], case.get("end"))
                self.assertEqual(got["end_date"], case.get("end_date"))
                self.assertEqual(got["block_id"], bid)

    def test_block_id_is_derived_not_random(self):
        a = agenda.new_block_id("2026-08-04", "Dentist")
        b = agenda.new_block_id("2026-08-04", "Dentist")
        self.assertEqual(a, b)
        self.assertRegex(a, r"^sg-evt-[0-9a-f]{8}$")


# --------------------------------------------------------------------------
# rules and expansion
# --------------------------------------------------------------------------

class TestParseRule(unittest.TestCase):
    def test_the_contract_example(self):
        r = agenda.parse_rule(
            "- MWF 10:30–11:20 [[CSE-311]] lecture until::2026-12-12 "
            "except::2026-09-14 ^sg-rule-cse311")
        self.assertEqual(r["weekdays"], [0, 2, 4])
        self.assertEqual((r["start"], r["end"]), ("10:30", "11:20"))
        self.assertEqual(r["until"], "2026-12-12")
        self.assertEqual(r["except"], ["2026-09-14"])
        self.assertEqual(r["rule_id"], "sg-rule-cse311")
        self.assertEqual(r["title"], "[[CSE-311]] lecture")

    def test_thursday_is_R_and_sunday_is_U(self):
        self.assertEqual(agenda.parse_rule("- TR 09:00 Seminar")["weekdays"], [1, 3])
        self.assertEqual(agenda.parse_rule("- SU 11:00 Long run")["weekdays"], [5, 6])

    def test_several_exceptions_and_a_start_date(self):
        r = agenda.parse_rule(
            "- M Standup from::2026-09-01 except::2026-09-07, 2026-09-14 ^sg-rule-su")
        self.assertEqual(r["from"], "2026-09-01")
        self.assertEqual(r["except"], ["2026-09-07", "2026-09-14"])
        self.assertEqual(r["title"], "Standup")

    def test_refuses_what_is_not_a_rule(self):
        for line in ["- 2026-08-04 Dentist", "- lecture on Mondays", "",
                     "- MWF ^sg-rule-x"]:
            with self.subTest(line=line):
                self.assertIsNone(agenda.parse_rule(line))


class TestExpand(unittest.TestCase):
    def rule(self, **kw):
        base = {"weekdays": [0, 2, 4], "start": None, "end": None,
                "title": "lecture", "from": None, "until": None, "except": []}
        base.update(kw)
        return base

    def test_crosses_a_month_boundary(self):
        got = agenda.expand(self.rule(), "2026-08-28", "2026-09-02")
        self.assertEqual(got, ["2026-08-28", "2026-08-31", "2026-09-02"])

    def test_until_is_inclusive_and_clips(self):
        got = agenda.expand(self.rule(until="2026-08-31"), "2026-08-28", "2026-09-04")
        self.assertEqual(got, ["2026-08-28", "2026-08-31"])

    def test_from_clips_the_near_end(self):
        got = agenda.expand(self.rule(**{"from": "2026-08-31"}),
                            "2026-08-24", "2026-09-02")
        self.assertEqual(got, ["2026-08-31", "2026-09-02"])

    def test_an_exception_removes_exactly_one_occurrence(self):
        got = agenda.expand(self.rule(**{"except": ["2026-08-31"]}),
                            "2026-08-28", "2026-09-02")
        self.assertEqual(got, ["2026-08-28", "2026-09-02"])

    def test_a_window_before_the_rule_starts_is_empty(self):
        self.assertEqual(
            agenda.expand(self.rule(**{"from": "2026-09-01"}),
                          "2026-08-01", "2026-08-31"), [])

    def test_an_inverted_window_is_empty_not_an_error(self):
        self.assertEqual(agenda.expand(self.rule(), "2026-09-02", "2026-08-28"), [])


# --------------------------------------------------------------------------
# the merge
# --------------------------------------------------------------------------

class TestResolve(unittest.TestCase):
    def setUp(self):
        agenda.invalidate()
        self.v = vault_with({
            f"{agenda.CALENDAR_DIR}/2026-08.md": month(
                "- 2026-08-04 14:30–15:30 Dentist ^sg-evt-8f2a1c04",
                "- 2026-08-12→2026-08-15 Family visit ^sg-evt-a77d2b19"),
            f"{agenda.CALENDAR_DIR}/schedule.md":
                "---\ntype: schedule\ntimezone: America/Los_Angeles\n"
                "tags: [calendar]\n---\n\n"
                "- MWF 10:30–11:20 [[CSE-311]] lecture until::2026-08-31 "
                "^sg-rule-cse311\n",
            "02-Areas/Academics/CSE-311/exams/midterm.md":
                "---\ntype: exam-prep\ncourse: CSE-311\ndate: 2026-08-05\n"
                "tags: [exam]\n---\n\n# CSE 311 Midterm 1\n",
            "03-Projects/thing.md":
                "---\ntype: project\ntags: [project]\n---\n\n"
                "# Thing\n\n- [ ] Ship it 📅 2026-08-07\n"
                "- [x] Already done 📅 2026-08-03\n",
        })

    def get(self, frm="2026-08-01", to="2026-08-31"):
        return agenda.resolve(self.v, frm, to, split=OPEN, ttl=0)

    def test_every_kind_arrives(self):
        kinds = {o["kind"] for o in self.get()["occurrences"]}
        self.assertEqual(kinds, {"event", "note", "rule", "task"})

    def test_provenance_is_on_every_occurrence(self):
        for o in self.get()["occurrences"]:
            with self.subTest(id=o["id"]):
                self.assertTrue(o["source"]["path"], "no path")
                self.assertTrue(o["id"])
                self.assertTrue(o["date"])
                self.assertEqual(o["owner"], "sigma")

    def test_occurrences_are_sorted_by_date_then_time(self):
        dates = [(o["date"], o["start"] or "") for o in self.get()["occurrences"]]
        self.assertEqual(dates, sorted(dates))

    def test_a_done_task_is_not_a_commitment(self):
        titles = [o["title"] for o in self.get()["occurrences"]]
        self.assertIn("Ship it", titles)
        self.assertNotIn("Already done", titles)

    def test_a_multi_day_event_appears_on_every_covered_day(self):
        got = [o for o in self.get()["occurrences"] if o["title"] == "Family visit"]
        self.assertEqual([o["date"] for o in got],
                         ["2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15"])
        self.assertEqual(got[0]["span"]["length"], 4)
        self.assertEqual(len({o["id"] for o in got}), 4, "ids must stay unique")

    def test_a_span_is_visible_from_a_window_it_only_overlaps(self):
        """The reason a span emits per-day rather than one row: a September
        query must still see an event that began in August."""
        got = agenda.resolve(self.v, "2026-08-14", "2026-08-14",
                             split=OPEN, ttl=0)["occurrences"]
        self.assertIn("Family visit", [o["title"] for o in got])

    def test_a_rule_expands_but_is_never_written_down(self):
        got = [o for o in self.get()["occurrences"] if o["kind"] == "rule"]
        self.assertTrue(got)
        self.assertTrue(all(o["source"]["rule_id"] == "sg-rule-cse311" for o in got))
        self.assertTrue(all(o["source"]["path"] == agenda.SCHEDULE_REL for o in got))
        text = (self.v / agenda.CALENDAR_DIR / "2026-08.md").read_text(encoding="utf-8")
        self.assertNotIn("lecture", text)

    def test_the_timezone_comes_from_schedule_md(self):
        self.assertEqual(self.get()["timezone"], "America/Los_Angeles")

    def test_a_bad_range_is_reported_not_guessed(self):
        got = agenda.resolve(self.v, "2026-08-31", "2026-08-01", split=OPEN, ttl=0)
        self.assertEqual(got["occurrences"], [])
        self.assertEqual(got["error"], "bad range")

    def test_sections_come_from_todo_not_a_second_classifier(self):
        by_title = {o["title"]: o for o in self.get()["occurrences"]}
        self.assertEqual(by_title["CSE 311 Midterm 1"]["section"], "courses")
        self.assertEqual(by_title["CSE 311 Midterm 1"]["parent"], "CSE-311")
        self.assertEqual(by_title["Ship it"]["section"], "projects")

    def test_committed_hours_counts_only_timed_things(self):
        hours = agenda.committed_hours(self.get()["occurrences"])
        self.assertEqual(hours["2026-08-04"], 1.0)          # Tue: the dentist only
        self.assertEqual(hours["2026-08-05"], 0.83)         # Wed: the lecture rule
        # Thu, inside the all-day family visit and with no rule: an untimed
        # occurrence contributes nothing rather than a guessed default.
        self.assertNotIn("2026-08-13", hours)


class TestFences(unittest.TestCase):
    def test_an_example_row_in_a_fence_is_not_an_event(self):
        v = vault_with({f"{agenda.CALENDAR_DIR}/2026-08.md": month(
            "- 2026-08-04 Real ^sg-evt-8f2a1c04",
            "",
            "```",
            "- 2026-08-09 Example from the docs ^sg-evt-dddddddd",
            "```")})
        titles = [o["title"] for o in
                  agenda.resolve(v, "2026-08-01", "2026-08-31",
                                 split=OPEN, ttl=0)["occurrences"]]
        self.assertEqual(titles, ["Real"])


class TestProblems(unittest.TestCase):
    """A line that was meant to be an event and is not gets reported.

    This is the failure that has no symptom: the calendar looks fine, it just
    quietly has less in it. A four-hex block ID — which is what the contract's
    own examples showed before P1 caught it — is exactly that shape of mistake.
    """

    def test_a_malformed_id_is_reported_not_swallowed(self):
        agenda.invalidate()
        v = vault_with({f"{agenda.CALENDAR_DIR}/2026-08.md": month(
            "- 2026-08-04 Real ^sg-evt-8f2a1c04",
            "- 2026-08-05 Typo'd id ^sg-evt-8f2a",
            "- 2026-08-06 25:00 Not a time")})
        got = agenda.resolve(v, "2026-08-01", "2026-08-31", split=OPEN, ttl=0)
        self.assertEqual([o["title"] for o in got["occurrences"]], ["Real"])
        self.assertEqual(len(got["problems"]), 2)
        self.assertEqual({p["line"] for p in got["problems"]}, {9, 10})
        self.assertTrue(all(p["path"].endswith("2026-08.md") for p in got["problems"]))

    def test_ordinary_prose_bullets_are_not_problems(self):
        agenda.invalidate()
        v = vault_with({f"{agenda.CALENDAR_DIR}/2026-08.md": month(
            "- 2026-08-04 Real ^sg-evt-8f2a1c04",
            "",
            "## Notes",
            "- remember to call the clinic first")})
        got = agenda.resolve(v, "2026-08-01", "2026-08-31", split=OPEN, ttl=0)
        self.assertEqual(got["problems"], [])

    def test_a_broken_rule_row_is_reported(self):
        agenda.invalidate()
        v = vault_with({f"{agenda.CALENDAR_DIR}/schedule.md":
                        "---\ntype: schedule\ntimezone: America/Los_Angeles\n"
                        "tags: [calendar]\n---\n\n"
                        "- MWF 10:30–11:20 lecture ^sg-rule-ok\n"
                        "- MWF 99:99 broken ^sg-rule-bad\n"})
        got = agenda.resolve(v, "2026-08-01", "2026-08-31", split=OPEN, ttl=0)
        self.assertEqual(len(got["problems"]), 1)
        self.assertIn("recurrence rule", got["problems"][0]["why"])


class TestPrivacy(unittest.TestCase):
    """Sealed is dropped, exempt is marked. The two fail in opposite directions
    and must not be conflated — a sealed occurrence leaking is unrecoverable,
    an unmarked exempt one is a false claim about confidentiality."""

    def setUp(self):
        agenda.invalidate()
        self.v = vault_with({
            f"{agenda.CALENDAR_DIR}/2026-08.md": month(
                "- 2026-08-04 Public thing ^sg-evt-8f2a1c04"),
            "02-Areas/ProCertus/standup.md":
                "---\ntype: resource\ndate: 2026-08-05\ntags: [resource]\n---\n\n"
                "# Client standup\n",
            "02-Areas/Secret/thing.md":
                "---\ntype: resource\ndate: 2026-08-06\ntags: [resource]\n---\n\n"
                "# Sealed thing\n",
        })

    def split(self, _vault, rels):
        sealed = {r for r in rels if r.startswith("02-Areas/Secret/")}
        no_sync = {r for r in rels if r.startswith("02-Areas/ProCertus/")}
        return sealed, no_sync

    def test_a_sealed_occurrence_never_appears(self):
        got = agenda.resolve(self.v, "2026-08-01", "2026-08-31",
                             split=self.split, ttl=0)["occurrences"]
        self.assertNotIn("Sealed thing", [o["title"] for o in got])

    def test_an_exempt_occurrence_is_marked_not_hidden(self):
        got = {o["title"]: o for o in
               agenda.resolve(self.v, "2026-08-01", "2026-08-31",
                              split=self.split, ttl=0)["occurrences"]}
        self.assertIn("Client standup", got)
        self.assertTrue(got["Client standup"]["no_sync"])
        self.assertFalse(got["Public thing"]["no_sync"])


class TestConflicts(unittest.TestCase):
    def test_a_note_and_its_own_task_disagreeing_flags_both(self):
        agenda.invalidate()
        v = vault_with({
            "02-Areas/Academics/CSE-311/assignments/hw3.md":
                "---\ntype: assignment\ncourse: CSE-311\ndue: 2026-08-10\n"
                "tags: [assignment]\n---\n\n# HW3\n\n- [ ] Finish HW3 📅 2026-08-12\n",
        })
        got = agenda.resolve(v, "2026-08-01", "2026-08-31", split=OPEN, ttl=0)
        self.assertEqual(got["conflicts"], 1)
        flagged = [o for o in got["occurrences"] if o["conflict"]]
        self.assertEqual(len(flagged), 2, "both sides are flagged, not one")
        self.assertEqual({o["kind"] for o in flagged}, {"note", "task"})
        self.assertIn("2026-08-10", flagged[0]["conflict"]["why"]
                      + flagged[1]["conflict"]["why"])

    def test_agreement_is_not_a_conflict(self):
        agenda.invalidate()
        v = vault_with({
            "02-Areas/Academics/CSE-311/assignments/hw4.md":
                "---\ntype: assignment\ncourse: CSE-311\ndue: 2026-08-10\n"
                "tags: [assignment]\n---\n\n# HW4\n\n- [ ] Finish HW4 📅 2026-08-10\n",
        })
        got = agenda.resolve(v, "2026-08-01", "2026-08-31", split=OPEN, ttl=0)
        self.assertEqual(got["conflicts"], 0)
        self.assertTrue(all(o["conflict"] is None for o in got["occurrences"]))


class TestCache(unittest.TestCase):
    def test_the_ttl_serves_the_same_scan_and_invalidate_drops_it(self):
        agenda.invalidate()
        v = vault_with({f"{agenda.CALENDAR_DIR}/2026-08.md":
                        month("- 2026-08-04 First ^sg-evt-8f2a1c04")})
        first = agenda.resolve(v, "2026-08-01", "2026-08-31", split=OPEN, ttl=300)
        self.assertEqual([o["title"] for o in first["occurrences"]], ["First"])

        (v / agenda.CALENDAR_DIR / "2026-08.md").write_text(
            month("- 2026-08-04 Second ^sg-evt-8f2a1c04"), encoding="utf-8")
        cached = agenda.resolve(v, "2026-08-01", "2026-08-31", split=OPEN, ttl=300)
        self.assertEqual([o["title"] for o in cached["occurrences"]], ["First"],
                         "the TTL is the whole point of P1's precompute")

        agenda.invalidate()
        fresh = agenda.resolve(v, "2026-08-01", "2026-08-31", split=OPEN, ttl=300)
        self.assertEqual([o["title"] for o in fresh["occurrences"]], ["Second"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
