"""
Rule exceptions — skipping and moving one occurrence (agenda P6).

`test_agenda_writes.py` is the standard this file follows: the interesting
tests are the ones where the write must *not* happen. P6 inherits all eight of
P5's holds through the same endpoint machinery, so what is tested here is the
four it adds:

    the file is not schedule.md            400   a rule lives in exactly one file
    the line is not a recurrence rule      400   wrong kind of occurrence
    the rule does not occur on that date   400   a write whose only effect is a ledger row
    the date is already excepted           400   same, and it would read as success

Plus the property that makes a move safe: the rule row and the override event
land in **one commit**, because an undo that restored only one of them would
leave the occurrence in neither place or in both.
"""
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # the harness next door

import agenda                                       # noqa: E402
import privacy                                      # noqa: E402
import todo                                         # noqa: E402
import writes                                       # noqa: E402
from sigma import gitops, ledger                    # noqa: E402
from test_agenda_writes import WriteBase, body, _git  # noqa: E402

RULE = "- MWF 10:30–11:20 [[CSE-311]] lecture until::2026-12-12 ^sg-rule-cse311"
SCHEDULE = """---
type: schedule
timezone: America/Los_Angeles
tags: [calendar]
---

# Schedule

## Rules

{rule}
"""


# --------------------------------------------------------------------------
# the grammar, with no endpoint in the way
# --------------------------------------------------------------------------

class TestExceptRule(unittest.TestCase):
    def test_the_date_is_inserted_before_the_block_id_and_sorted(self):
        line, why = agenda.except_rule(RULE, "2026-09-14")
        self.assertIsNone(why)
        line, why = agenda.except_rule(line, "2026-09-07")
        self.assertIsNone(why)
        self.assertEqual(
            line,
            "- MWF 10:30–11:20 [[CSE-311]] lecture until::2026-12-12 "
            "except::2026-09-07,2026-09-14 ^sg-rule-cse311")

    def test_everything_but_the_exception_list_survives_byte_for_byte(self):
        """The title keeps its literal wikilink. Re-serialising from parse_rule
        would write the display form and break the link that keeps the row
        attached to its course."""
        line, _ = agenda.except_rule(RULE, "2026-09-14")
        was, got = agenda.parse_rule(RULE), agenda.parse_rule(line)
        for k in ("weekdays", "start", "end", "title", "from", "until", "rule_id"):
            self.assertEqual(got[k], was[k], k)
        self.assertIn("[[CSE-311]]", line)

    def test_a_date_already_excepted_is_refused_rather_than_written_twice(self):
        line, _ = agenda.except_rule(RULE, "2026-09-14")
        again, why = agenda.except_rule(line, "2026-09-14")
        self.assertIsNone(again)
        self.assertIn("already", why)

    def test_a_line_that_is_not_a_rule_is_refused(self):
        out, why = agenda.except_rule("- 2026-08-04 Dentist ^sg-evt-8f2a1c04", "2026-09-14")
        self.assertIsNone(out)
        self.assertIn("not a recurrence rule", why)

    def test_a_bad_date_is_refused(self):
        self.assertIsNone(agenda.except_rule(RULE, "the 14th")[0])


class TestSkippedExpansion(unittest.TestCase):
    def rule(self, *dates):
        line = RULE
        for d in dates:
            line, _ = agenda.except_rule(line, d)
        return agenda.parse_rule(line)

    def test_expand_drops_the_excepted_day_and_skipped_returns_it(self):
        ru = self.rule("2026-09-07", "2026-09-14")
        self.assertEqual(agenda.expand(ru, "2026-09-07", "2026-09-14"),
                         ["2026-09-09", "2026-09-11"])
        self.assertEqual(agenda.skipped(ru, "2026-09-07", "2026-09-14"),
                         ["2026-09-07", "2026-09-14"])

    def test_a_date_the_rule_never_produces_is_not_reported_as_skipped(self):
        """An except:: on a Tuesday of an MWF rule removes nothing, so there is
        nothing to draw struck through either."""
        ru = self.rule("2026-09-08")                 # a Tuesday
        self.assertEqual(agenda.skipped(ru, "2026-09-07", "2026-09-14"), [])

    def test_an_exception_outside_the_rules_own_window_is_not_skipped(self):
        ru = self.rule("2027-01-04")                 # past until::2026-12-12
        self.assertEqual(agenda.skipped(ru, "2027-01-01", "2027-01-31"), [])


# --------------------------------------------------------------------------
# the endpoint
# --------------------------------------------------------------------------

class ExceptBase(WriteBase):
    def seed_rule(self, rule=RULE):
        p = self.vault / agenda.SCHEDULE_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(SCHEDULE.format(rule=rule), encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "seed schedule")
        agenda.invalidate()
        text = p.read_text(encoding="utf-8")
        line_no = next(i for i, ln in enumerate(text.splitlines(), 1) if ln == rule)
        return {"file": agenda.SCHEDULE_REL, "line": line_no, "raw": rule}

    def skip(self, **kw):
        seed = kw.pop("seed", None) or self.seed_rule()
        kw = {**seed, "date": "2026-09-14", **kw}
        return writes.api_agenda_except(writes.RuleException(**kw))

    def schedule_text(self):
        return (self.vault / agenda.SCHEDULE_REL).read_text(encoding="utf-8")


class TestItWorks(ExceptBase):
    def test_a_skip_lands_and_the_row_still_parses_as_the_same_rule(self):
        r = self.skip()
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(r["moved"])
        got = agenda.parse_rule(r["raw"])
        self.assertEqual(got["except"], ["2026-09-14"])
        self.assertEqual(got["title"], "[[CSE-311]] lecture")
        self.assertIn("except::2026-09-14", self.schedule_text())

    def test_a_skipped_occurrence_renders_struck_rather_than_vanishing(self):
        self.skip()
        agenda.invalidate()
        r = agenda.resolve(self.vault, "2026-09-14", "2026-09-14")
        occ = [o for o in r["occurrences"] if o["kind"] == "rule"]
        self.assertEqual(len(occ), 1, "the occurrence vanished instead of being struck")
        self.assertTrue(occ[0]["skipped"])
        # ...and its hours stop counting.
        self.assertEqual(agenda.committed_hours(occ), {})

    def test_cancelled_is_not_used_to_carry_a_skip(self):
        """`cancelled` means the day you called it off; `except::` only records
        the day the thing would have happened. Reusing the key would read fine
        and be wrong."""
        self.skip()
        agenda.invalidate()
        r = agenda.resolve(self.vault, "2026-09-14", "2026-09-14")
        occ = next(o for o in r["occurrences"] if o["kind"] == "rule")
        self.assertIsNone(occ["cancelled"])

    def test_a_move_writes_the_rule_and_the_override_in_one_commit(self):
        r = self.skip(to_date="2026-09-15", start="14:00", end="14:50")
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r["moved"])
        files = _git(self.vault, "show", "--name-only", "--format=", r["sha"]).stdout.split()
        self.assertEqual(sorted(files),
                         sorted([agenda.SCHEDULE_REL, f"{agenda.CALENDAR_DIR}/2026-09.md"]),
                         "a move must touch both files in one commit")

    def test_the_override_is_a_plain_event_row_on_the_new_day(self):
        r = self.skip(to_date="2026-09-15", start="14:00", end="14:50")
        got = agenda.parse_event(r["event"])
        self.assertEqual((got["date"], got["start"], got["end"]),
                         ("2026-09-15", "14:00", "14:50"))
        self.assertTrue(got["block_id"].startswith("sg-evt-"))
        # No back-reference to the rule: that would be new grammar (2026-08-04).
        self.assertNotIn("sg-rule-", r["event"])

    def test_the_move_inherits_the_rules_time_and_title_when_unspecified(self):
        r = self.skip(to_date="2026-09-15")
        got = agenda.parse_event(r["event"])
        self.assertEqual((got["start"], got["end"]), ("10:30", "11:20"))
        self.assertIn("[[CSE-311]] lecture", r["event"])

    def test_undo_restores_both_files(self):
        r = self.skip(to_date="2026-09-15")
        row = ledger.entries(5)[0]
        self.assertEqual(row["sha"], r["sha"])
        self.assertIn("moved one occurrence", row["summary"])
        out = _git(self.vault, "revert", "--no-edit", r["sha"])
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("except::", self.schedule_text())
        self.assertFalse((self.vault / agenda.CALENDAR_DIR / "2026-09.md").exists())

    def test_the_ledger_summary_reads_as_a_sentence(self):
        """The ledger is the morning view; its readability is a stated feature,
        and P5 shipped `editd an event` before anyone read one."""
        self.skip()
        self.assertEqual(ledger.entries(1)[0]["summary"],
                         "skipped one occurrence: CSE-311 lecture on 2026-09-14")


# --------------------------------------------------------------------------
# the holds — each test tries to get past one
# --------------------------------------------------------------------------

class TestHolds(ExceptBase):
    def test_a_row_edited_underneath_the_client_is_refused(self):
        """A retimed rule still parses and still occurs on the day, so it gets
        all the way to the splice — where the line it claims to be editing is
        not the line that is there."""
        seed = self.seed_rule()
        r = self.skip(seed={**seed, "raw": RULE.replace("10:30", "09:30")})
        self.assertEqual(r.status_code, 409)
        self.assertIn("changed underneath", body(r)["detail"])

    def test_a_row_that_moved_underneath_the_client_is_refused(self):
        seed = self.seed_rule()
        r = self.skip(seed={**seed, "line": seed["line"] + 3})
        self.assertEqual(r.status_code, 409)
        self.assertIn("changed underneath", body(r)["detail"])

    def test_a_rule_outside_the_schedule_is_refused(self):
        """A rule row pasted into a month note is not a rule this may edit."""
        seed = self.seed_rule()
        r = self.skip(seed={**seed, "file": f"{agenda.CALENDAR_DIR}/2026-09.md"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(body(r)["error"], "not the schedule")

    def test_an_event_line_is_refused(self):
        seed = self.seed_rule()
        r = self.skip(seed={**seed, "raw": "- 2026-09-14 Dentist ^sg-evt-8f2a1c04"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(body(r)["error"], "not a rule")

    def test_a_date_the_rule_does_not_occur_on_is_refused(self):
        """The silent no-op: excepting a Tuesday from an MWF lecture parses,
        commits, changes nothing visible, and still writes a ledger row saying
        something happened."""
        r = self.skip(date="2026-09-15")             # a Tuesday
        self.assertEqual(r.status_code, 400)
        self.assertEqual(body(r)["error"], "no such occurrence")

    def test_a_date_past_the_rules_until_is_refused(self):
        r = self.skip(date="2027-01-04")             # past until::2026-12-12
        self.assertEqual(r.status_code, 400)
        self.assertEqual(body(r)["error"], "no such occurrence")

    def test_excepting_the_same_day_twice_is_refused_and_says_why(self):
        """An already-excepted day is also a day the rule no longer expands to,
        so the naive check order answers "this rule does not occur on
        2026-09-14" — true of the expansion, misleading about the rule."""
        seed = self.seed_rule()
        first = self.skip(seed=seed)
        self.assertTrue(first.get("ok"), first)
        _git(self.vault, "add", "-A"); _git(self.vault, "commit", "-m", "x")
        agenda.invalidate()
        r = self.skip(seed={**seed, "raw": first["raw"], "line": seed["line"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(body(r)["error"], "already excepted")

    def test_a_path_escaping_the_vault_is_refused(self):
        seed = self.seed_rule()
        r = self.skip(seed={**seed, "file": "../outside.md"})
        self.assertEqual(r.status_code, 400)

    def test_a_gitignored_schedule_is_refused_inside_the_mutex(self):
        """§5.5: gitops.commit() returns no SHA for an ignored path, so this
        would be the only write in Sigma with no ledger row and no undo.

        The seal goes on *before* the file is tracked, because git does not
        report a tracked file as ignored — a schedule that was both would
        violate the `pre-push` guard's invariant long before it reached here."""
        (self.vault / ".gitignore").write_text(
            f"{agenda.CALENDAR_DIR}/\n", encoding="utf-8")
        _git(self.vault, "add", "-A"); _git(self.vault, "commit", "-m", "seal")
        privacy.VaultPrivacy._git_ignored.cache_clear()
        p = self.vault / agenda.SCHEDULE_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(SCHEDULE.format(rule=RULE), encoding="utf-8")
        agenda.invalidate()
        line_no = next(i for i, ln in enumerate(
            p.read_text(encoding="utf-8").splitlines(), 1) if ln == RULE)
        r = self.skip(seed={"file": agenda.SCHEDULE_REL, "line": line_no, "raw": RULE})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(body(r)["error"], "sealed path")
        self.assertNotIn("except::", p.read_text(encoding="utf-8"),
                         "it wrote before refusing")

    def test_a_busy_mutex_refuses_rather_than_queueing(self):
        seed = self.seed_rule()
        with gitops.vault_write(self.vault):
            r = self.skip(seed=seed)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(body(r)["error"], "busy")

    def test_a_write_that_would_change_a_checkbox_is_refused(self):
        """Completion stays a human signal. Forced by making the splice inject
        one, since a well-formed override row never carries a checkbox."""
        seed = self.seed_rule()
        real = todo.splice
        todo.splice = lambda b, h, line: real(b, h, line + "\n- [ ] snuck in")
        try:
            r = self.skip(seed=seed, to_date="2026-09-15")
        finally:
            todo.splice = real
        self.assertEqual(r.status_code, 400)
        self.assertEqual(body(r)["error"], "checkbox")

    def test_nothing_is_written_when_a_hold_fires(self):
        """The property that matters across all of the above: a refusal leaves
        the tree exactly as it was."""
        seed = self.seed_rule()
        before = self.schedule_text()
        self.skip(date="2026-09-15")                 # no such occurrence
        self.skip(seed={**seed, "raw": "- 2026-09-14 Dentist ^sg-evt-8f2a1c04"})
        self.assertEqual(self.schedule_text(), before)
        self.assertEqual(_git(self.vault, "status", "--porcelain").stdout.strip(), "")


# --------------------------------------------------------------------------
# mutation tests — disable the guard, watch its test go red
# --------------------------------------------------------------------------

class TestTheGuardsAreLoadBearing(ExceptBase):
    """A suite that passes the first time is a suite nobody has checked. Each of
    these breaks one guard and asserts the write it was stopping now gets
    through — so a future refactor that quietly removes it fails here."""

    def test_without_the_occurrence_check_a_tuesday_would_be_excepted(self):
        seed = self.seed_rule()
        real = agenda.expand
        agenda.expand = lambda ru, a, b: [a]         # pretend every day occurs
        try:
            r = self.skip(seed=seed, date="2026-09-15")
        finally:
            agenda.expand = real
        self.assertTrue(r.get("ok"), "the guard was not what stopped it")
        self.assertIn("except::2026-09-15", self.schedule_text())

    def test_without_the_schedule_check_a_month_note_would_be_edited(self):
        seed = self.seed_rule()
        real = writes.ag.SCHEDULE_REL
        try:
            writes.ag.SCHEDULE_REL = f"{agenda.CALENDAR_DIR}/2026-09.md"
            r = self.skip(seed={**seed, "file": f"{agenda.CALENDAR_DIR}/2026-09.md"})
        finally:
            writes.ag.SCHEDULE_REL = real
        # It gets past the file check and dies later, on the file not existing —
        # which is the point: the 400 above was the schedule check, not luck.
        self.assertNotEqual(body(r).get("error"), "not the schedule")


if __name__ == "__main__":
    unittest.main()
