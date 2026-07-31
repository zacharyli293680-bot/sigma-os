"""
fleet.is_due — cadence on calendar days.

The bug this pins down: a hand-run in the afternoon used to suppress the next
morning's scheduled run, because the old rule asked for 23 elapsed hours rather
than a new day. Three consecutive 09:00 runs logged "nothing due" and reported
success while writing no daily note.
"""
import datetime
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import fleet as fl                               # noqa: E402
import specialists as sp                         # noqa: E402


def at(y, m, d, hh=9, mm=0):
    return datetime.datetime(y, m, d, hh, mm)


def state(key, last_ok):
    return {"specialists": {key: {"last_ok": last_ok}}}


DAILY = sp.Specialist(key="planner", title="t", cadence="daily",
                      model="sonnet", effort="medium", brief="")
WEEKLY = sp.Specialist(key="coach", title="t", cadence="weekly",
                       model="sonnet", effort="medium", brief="")


class TestIsDue(unittest.TestCase):
    def test_never_run_is_due(self):
        self.assertTrue(fl.is_due(DAILY, {}, at(2026, 7, 31)))

    def test_an_afternoon_hand_run_does_not_suppress_the_next_morning(self):
        """The regression. 2026-07-30 13:56 -> 2026-07-31 09:00 is 19.1h, which
        the old rolling-23h rule treated as not-due."""
        s = state("planner", "2026-07-30T13:56:42")
        self.assertTrue(fl.is_due(DAILY, s, at(2026, 7, 31, 9, 0)))

    def test_already_run_today_is_not_due(self):
        """The other half: once a day, not twice."""
        s = state("planner", "2026-07-31T08:00:00")
        self.assertFalse(fl.is_due(DAILY, s, at(2026, 7, 31, 9, 0)))

    def test_late_night_run_still_counts_for_that_day(self):
        s = state("planner", "2026-07-31T23:50:00")
        self.assertFalse(fl.is_due(DAILY, s, at(2026, 7, 31, 23, 55)))
        self.assertTrue(fl.is_due(DAILY, s, at(2026, 8, 1, 9, 0)))

    def test_weekly_waits_seven_calendar_days(self):
        s = state("coach", "2026-07-26T09:00:00")
        self.assertFalse(fl.is_due(WEEKLY, s, at(2026, 8, 1)))    # 6 days
        self.assertTrue(fl.is_due(WEEKLY, s, at(2026, 8, 2)))     # 7 days

    def test_unreadable_timestamp_runs_rather_than_skips(self):
        self.assertTrue(fl.is_due(DAILY, state("planner", "not-a-date"),
                                  at(2026, 7, 31)))
        self.assertTrue(fl.is_due(DAILY, state("planner", None), at(2026, 7, 31)))


if __name__ == "__main__":
    unittest.main()
