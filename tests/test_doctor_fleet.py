"""
The fleet check's headline count (doctor.check_fleet).

On 2026-08-06 the doctor reported `fleet healthy (4/3 specialist(s) reporting)`
— a count over its own denominator. Nothing was broken: the planner was retired
from the registry on 2026-08-01 and `fleet.state.json` still carried its record,
because state is append-only history and nothing prunes it. The check counted
state records and called them specialists.

That is this project's recurring counting defect in a third costume — `--dry-run`
reporting "logged 3" while writing nothing, and the fleet runner counting refused
tool *calls* as proposals. So the test that matters is the one where the state
holds a record the registry no longer does.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import doctor                                    # noqa: E402
import fleet as fl                               # noqa: E402
import specialists as sp                         # noqa: E402

ALERT, TODO, INFO, OK = doctor.ALERT, doctor.TODO, doctor.INFO, doctor.OK

LAST_RUN = "2026-08-05T09:00:32"


def _healthy(key: str) -> dict:
    # `last_ok` today, so no specialist is ever stale for reasons of clock.
    import datetime
    return {"last_result": "ok", "last_ok": datetime.date.today().isoformat()}


class FleetCountBase(unittest.TestCase):
    """The check reads three things from outside; all three are stubbed.

    Neither the real state file nor the real scheduled task may decide whether
    this passes — the whole point is a state that the machine happens not to be
    in right now.
    """

    def setUp(self):
        self._saved = (fl.load_state, fl._task_installed, doctor._task_fields)
        fl._task_installed = lambda: True
        doctor._task_fields = lambda name: {}      # no failed scheduled run

    def tearDown(self):
        fl.load_state, fl._task_installed, doctor._task_fields = self._saved

    def check(self, specialists: dict):
        fl.load_state = lambda: {"last_run": LAST_RUN,
                                 "specialists": specialists}
        out = []
        doctor.check_fleet(out)
        return out

    def healthy_line(self, found):
        for lvl, msg, _ in found:
            if lvl == OK and "fleet healthy" in msg:
                return msg
        return None


class TestFleetCount(FleetCountBase):
    def test_a_full_registry_counts_itself(self):
        found = self.check({s.key: _healthy(s.key) for s in sp.FLEET})
        msg = self.healthy_line(found)
        self.assertIsNotNone(msg, found)
        self.assertIn(f"{len(sp.FLEET)}/{len(sp.FLEET)} specialist(s)", msg)

    def test_a_retired_specialist_does_not_inflate_the_count(self):
        """The 2026-08-06 case: a record for something that no longer runs."""
        specs = {s.key: _healthy(s.key) for s in sp.FLEET}
        specs["planner"] = _healthy("planner")     # retired 2026-08-01
        msg = self.healthy_line(self.check(specs))
        self.assertIsNotNone(msg)
        self.assertIn(f"{len(sp.FLEET)}/{len(sp.FLEET)} specialist(s)", msg)
        self.assertNotIn(f"{len(sp.FLEET) + 1}/{len(sp.FLEET)}", msg)

    def test_the_numerator_can_never_exceed_the_denominator(self):
        """The invariant, stated once rather than per-scenario."""
        specs = {s.key: _healthy(s.key) for s in sp.FLEET}
        for ghost in ("planner", "summariser", "does-not-exist"):
            specs[ghost] = _healthy(ghost)
        msg = self.healthy_line(self.check(specs))
        n, total = msg.split("(")[1].split(" specialist")[0].split("/")
        self.assertLessEqual(int(n), int(total))

    def test_a_missing_specialist_is_stale_not_a_lower_count(self):
        """The fix must not turn a silent absence into a quiet 2/3.

        A registry specialist with no record at all has never reported, which
        `stale` already catches — and a stale fleet prints no healthy line. The
        headline must stay absent rather than shrink, or the failure this file
        exists to surface would be reported as a smaller success.
        """
        specs = {s.key: _healthy(s.key) for s in sp.FLEET[1:]}
        found = self.check(specs)
        self.assertIsNone(self.healthy_line(found), found)
        self.assertTrue(any(lvl == TODO and "overdue" in msg
                            and sp.FLEET[0].key in msg for lvl, msg, _ in found),
                        found)


if __name__ == "__main__":
    unittest.main()
