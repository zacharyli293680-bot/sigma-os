#!/usr/bin/env python3
"""
Recall cards and the measured pace (recall.py, study mode S8).

Two claims, and both are the kind that fail *quietly* if they fail at all —
which is why they are pinned here rather than left to the drive.

**A card cannot distort the score.** Cards are the only tasks in this vault
Sigma raises for itself, so every path by which one could inflate a number gets
a test: the throughput weight, the momentum policy, the adherence scan, and the
queue's own ordering — where the claim is arithmetic (a card's score is capped
below any unmarked task's floor by the expiry) rather than a promise.

**A measured pace is never an assumed one.** Below the sample floor the
multiplier is None, not 1.0; a module measured against another unit's minutes is
dropped rather than split; a session left open overnight is dropped rather than
averaged in. Every one of those, wrong, produces a plausible number — which is
worse than producing none.
"""
import datetime
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))
sys.path.insert(0, str(REPO / "interface" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import recall as rc                                 # noqa: E402
import retro as rv                                  # noqa: E402
import todo as td                                   # noqa: E402
from sigma import gitops, ledger                    # noqa: E402
from test_lesson import _valid                      # noqa: E402

DAY = "2026-08-10"
OPEN = lambda v, rels: (set(), set())               # noqa: E731 — nothing sealed
REL = "02-Areas/Academics/TEST-101/test-101-recall.md"


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True,
                          text=True)


def _days_before(n: int, today: str = DAY) -> str:
    return (datetime.date.fromisoformat(today)
            - datetime.timedelta(days=n)).isoformat()


def _note(*cards: str) -> str:
    return rc.note_text("TEST-101", DAY) + "".join(f"{c}\n" for c in cards)


def _want(seg="Topic 1", target="test-101-m01-test-module", label="M01",
          misses=2) -> dict:
    return {"seg": seg, "target": target, "label": label, "misses": misses,
            "unit": ("m", 1)}


# --------------------------------------------------------------------------
# the grammar
# --------------------------------------------------------------------------

class TestGrammar(unittest.TestCase):
    def test_a_composed_card_parses_back_to_what_composed_it(self):
        line = rc.compose("The moment of a force", "aa-210-m04-moments", "M04",
                          3, DAY)
        (card,) = rc.parse_cards(line)
        self.assertEqual(card["seg"], "The moment of a force")
        self.assertEqual(card["target"], "aa-210-m04-moments")
        self.assertEqual(card["label"], "M04")
        self.assertEqual((card["state"], card["raised"], card["misses"]),
                         ("open", DAY, 3))

    def test_the_queue_row_reads_as_a_sentence(self):
        """`display_text` has to strip the 🔽 and the ➕ date, or every card in
        the queue would render its own metadata at the reader."""
        line = rc.compose("Topic 1", "test-101-m01-test-module", "M01", 2, DAY)
        body = td.TASK_RE.match(line).group(2)
        self.assertEqual(td.display_text(body),
                         "Recall · Topic 1 · M01 — missed ×2")

    def test_a_card_carries_no_deadline(self):
        """A 📅 would become a deadline in the adherence scan at weight 0.35 —
        Sigma grading Zach against homework Sigma set."""
        line = rc.compose("Topic 1", "test-101-m01-test-module", "M01", 1, DAY)
        self.assertNotIn("📅", line)
        self.assertIsNone(td.DUE_RE.search(line))

    def test_a_card_is_low_urgency_to_the_queue(self):
        line = rc.compose("Topic 1", "test-101-m01-test-module", "M01", 1, DAY)
        prio = next((v for e, v in td.PRIORITY.items() if e in line), None)
        self.assertEqual(td.urgency_of(prio), "low")

    def test_a_hand_written_line_in_the_note_is_not_a_card(self):
        """The cap and the expiry both only manage what Sigma raised."""
        self.assertEqual(rc.parse_cards("- [ ] reread the moments handout\n"), [])

    def test_a_fenced_card_is_not_a_card(self):
        text = "```\n" + rc.compose("Topic 1", "t", "M01", 1, DAY) + "\n```\n"
        self.assertEqual(rc.parse_cards(text), [])


# --------------------------------------------------------------------------
# the cap and the expiry
# --------------------------------------------------------------------------

class TestReconcile(unittest.TestCase):
    def test_a_miss_becomes_a_card(self):
        res = rc.reconcile(_note(), [_want()], DAY)
        self.assertEqual(len(res["raised"]), 1)
        (card,) = rc.parse_cards(res["text"])
        self.assertEqual(card["seg"], "Topic 1")

    def test_the_same_concept_missed_again_does_not_double_up(self):
        first = rc.reconcile(_note(), [_want()], DAY)
        again = rc.reconcile(first["text"], [_want(misses=5)], DAY)
        self.assertEqual(again["raised"], [])
        self.assertEqual(len(rc.parse_cards(again["text"])), 1)

    def test_the_cap_holds_and_says_what_it_withheld(self):
        wanted = [_want(seg=f"Topic {i}", misses=9 - i) for i in range(1, 9)]
        res = rc.reconcile(_note(), wanted, DAY)
        self.assertEqual(len(res["raised"]), rc.CAP)
        self.assertEqual(res["withheld"], len(wanted) - rc.CAP)
        # worst first — the cap must drop the least-missed, never the last-read
        self.assertEqual([r["seg"] for r in res["raised"]],
                         [f"Topic {i}" for i in range(1, rc.CAP + 1)])

    def test_a_card_that_has_sat_two_weeks_retires_itself(self):
        old = rc.compose("Topic 1", "test-101-m01", "M01", 2,
                         _days_before(rc.EXPIRY_DAYS))
        res = rc.reconcile(_note(old), [], DAY)
        self.assertEqual(len(res["expired"]), 1)
        (card,) = rc.parse_cards(res["text"])
        self.assertEqual((card["state"], card["expired"]), ("retired", DAY))
        # the line stays in the note — nothing here deletes a record
        self.assertIn("Recall · Topic 1", res["text"])

    def test_a_retired_card_leaves_no_open_box_behind(self):
        old = rc.compose("Topic 1", "test-101-m01", "M01", 2,
                         _days_before(rc.EXPIRY_DAYS + 3))
        res = rc.reconcile(_note(old), [], DAY)
        line = next(ln_ for ln_ in res["text"].split("\n") if "Topic 1" in ln_)
        self.assertIsNone(td.TASK_RE.match(line))

    def test_a_card_one_day_short_of_the_expiry_stays(self):
        young = rc.compose("Topic 1", "test-101-m01", "M01", 2,
                           _days_before(rc.EXPIRY_DAYS - 1))
        self.assertEqual(rc.reconcile(_note(young), [], DAY)["expired"], [])

    def test_a_hand_written_card_is_never_retired(self):
        """No ➕ means nobody here raised it."""
        mine = "- [ ] Recall · Topic 9 · [[test-101-m01|M01]] — missed ×1 🔽"
        res = rc.reconcile(_note(mine), [], DAY)
        self.assertEqual(res["expired"], [])

    def test_expiry_runs_first_so_it_frees_a_slot_the_same_day(self):
        old = [rc.compose(f"Old {i}", "test-101-m01", "M01", 1,
                          _days_before(rc.EXPIRY_DAYS)) for i in range(rc.CAP)]
        res = rc.reconcile(_note(*old), [_want(seg="Fresh")], DAY)
        self.assertEqual(len(res["expired"]), rc.CAP)
        self.assertEqual(len(res["raised"]), 1)

    def test_a_done_card_does_not_hold_a_slot(self):
        done = rc.compose("Topic 1", "test-101-m01", "M01", 1, DAY
                          ).replace("[ ]", "[x]", 1)
        res = rc.reconcile(_note(done), [_want(seg="Topic 2")], DAY)
        self.assertEqual(len(res["raised"]), 1)

    def test_a_concept_missed_again_after_completing_its_card_comes_back(self):
        """Spacing by evidence: the dedupe is against *open* cards only."""
        done = rc.compose("Topic 1", "test-101-m01-test-module", "M01", 1, DAY
                          ).replace("[ ]", "[x]", 1)
        res = rc.reconcile(_note(done), [_want()], DAY)
        self.assertEqual(len(res["raised"]), 1)


class TestWantedFrom(unittest.TestCase):
    UNITS = {("m", 1): ("test-101-m01-test-module", "M01"),
             ("cp", 1): ("test-101-checkpoint-1", "CP1")}

    def rows(self, *specs):
        return [{"course": "TEST-101", "module": m, "checkpoint": c,
                 "seg_title": s, "result": r, "qid": "q-1-1"}
                for m, c, s, r in specs]

    def test_only_wrong_answers_become_cards(self):
        got = rc.wanted_from(self.rows((1, None, "Topic 1", "correct"),
                                       (1, None, "Topic 2", "skipped"),
                                       (1, None, "Topic 3", "wrong")), self.UNITS)
        self.assertEqual([w["seg"] for w in got], ["Topic 3"])

    def test_misses_group_by_segment_and_sort_worst_first(self):
        got = rc.wanted_from(self.rows((1, None, "Topic 1", "wrong"),
                                       (1, None, "Topic 2", "wrong"),
                                       (1, None, "Topic 2", "wrong")), self.UNITS)
        self.assertEqual([(w["seg"], w["misses"]) for w in got],
                         [("Topic 2", 2), ("Topic 1", 1)])

    def test_a_checkpoint_miss_links_the_checkpoint(self):
        got = rc.wanted_from(self.rows((None, 1, "Topic 1", "wrong")), self.UNITS)
        self.assertEqual((got[0]["target"], got[0]["label"]),
                         ("test-101-checkpoint-1", "CP1"))

    def test_a_miss_with_no_note_to_point_at_is_dropped(self):
        """A card you cannot click through to is not actionable."""
        got = rc.wanted_from(self.rows((7, None, "Topic 1", "wrong")), self.UNITS)
        self.assertEqual(got, [])


# --------------------------------------------------------------------------
# the arithmetic a card must not distort
# --------------------------------------------------------------------------

class TestCardsCannotDistortTheScore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "02-Areas" / "Academics" / "TEST-101").mkdir(parents=True)
        (self.vault / "02-Areas" / "Academics" / "TEST-101"
         / "test-101.md").write_text(
            "---\ntype: course-index\ncourse: TEST-101\nstatus: active\n---\n",
            encoding="utf-8")
        self.index = Path(self.tmp.name) / "todo.state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def note(self, rel, body):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def adopt(self):
        return td.build(vault=self.vault, index_path=self.index, today=DAY,
                        split=OPEN)

    def facts(self):
        return rv.day_facts(DAY, vault=self.vault, index_path=self.index,
                            split=OPEN)

    def card(self, seg="Topic 1"):
        return rc.compose(seg, "test-101-m01-test-module", "M01", 2, DAY)

    def test_a_card_completion_is_worth_a_quarter_not_a_course_task(self):
        p = self.note(REL, _note(self.card()))
        self.adopt()
        p.write_text(_note(self.card().replace("[ ]", "[x]", 1)),
                     encoding="utf-8")
        self.adopt()
        f = self.facts()
        self.assertEqual(f["weighted"], rv.RECALL_WEIGHT)
        self.assertEqual(f["recall_done"], 1)
        # still counted, and still visibly a course completion
        self.assertEqual(f["by_section"]["courses"], 1)
        self.assertTrue(f["done"][0]["recall"])

    def test_a_card_does_not_satisfy_the_one_task_per_course_policy(self):
        """Otherwise the momentum component could be moved by raising more
        cards — a metric its own author can pay into."""
        p = self.note(REL, _note(self.card()))
        self.adopt()
        p.write_text(_note(self.card().replace("[ ]", "[x]", 1)),
                     encoding="utf-8")
        self.adopt()
        f = self.facts()
        self.assertEqual(f["advanced"], [])
        self.assertEqual(f["active_courses"], ["TEST-101"])

    def test_real_course_work_still_counts_beside_a_card(self):
        p = self.note(REL, _note(self.card()))
        t = self.note("02-Areas/Academics/TEST-101/tasks.md", "- [ ] email the TA\n")
        self.adopt()
        p.write_text(_note(self.card().replace("[ ]", "[x]", 1)), encoding="utf-8")
        t.write_text("- [x] email the TA\n", encoding="utf-8")
        self.adopt()
        f = self.facts()
        self.assertEqual(f["advanced"], ["TEST-101"])
        self.assertEqual(f["weighted"], round(rv.WEIGHT["courses"]
                                              + rv.RECALL_WEIGHT, 2))

    def test_a_full_deck_of_cards_adds_no_deadlines(self):
        cards = [self.card(f"Topic {i}") for i in range(rc.CAP)]
        self.note(REL, _note(*cards))
        self.adopt()
        self.assertEqual(self.facts()["deadlines_due"], 0)

    def test_a_card_can_never_outrank_an_unmarked_task(self):
        """The invariant the cap and the expiry are really for: 🔽 (5.0) plus
        aging capped by a 14-day life is 12.0, under the 15.0 floor of any task
        nobody bothered to mark."""
        oldest = {"deadline": None, "urgency": "low",
                  "created": _days_before(rc.EXPIRY_DAYS)}
        plain = {"deadline": None, "urgency": None, "created": DAY}
        self.assertLess(td.score(oldest, DAY), td.score(plain, DAY))
        self.assertLessEqual(td.score(oldest, DAY),
                             td.URGENCY_WEIGHT["low"]
                             + td.AGING_PER_DAY * rc.EXPIRY_DAYS)

    def test_a_card_gets_its_own_window_slot(self):
        """Which is the other half of the same argument: scoring guarantees a
        card never displaces work, so without a slot it would never surface."""
        self.note("02-Areas/Academics/TEST-101/test-101-timeline.md",
                  "- [ ] day 2\n- [ ] day 3\n")
        self.note("02-Areas/Academics/TEST-101/test-101-guide.md",
                  "- [ ] M01 · [[test-101-m01-test-module|Test module]]\n")
        self.note(REL, _note(self.card()))
        vis = self.adopt()["sections"]["courses"]["visible"]
        self.assertEqual(sorted(t["text"] for t in vis),
                         ["M01 · Test module", "Recall · Topic 1 · M01 — missed ×2",
                          "day 2"])
        self.assertEqual([t["recall"] for t in vis].count(True), 1)

    def test_the_recall_note_is_recognised_by_its_folder(self):
        self.assertTrue(td.is_recall_file(REL))
        self.assertFalse(td.is_recall_file("04-Resources/test-101-recall.md"))
        self.assertFalse(td.is_recall_file("02-Areas/Academics/TEST-101/recall.md"))
        self.assertFalse(td.is_chain_file(REL))


# --------------------------------------------------------------------------
# the measured pace
# --------------------------------------------------------------------------

LOG_HEAD = ("---\ntype: study-log\ncourse: TEST-101\ntags: [guide]\n---\n\n"
            "# TEST-101 — study log\n\n## Sessions\n")


class PaceBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.vault = Path(self.tmp.name) / "vault"
        self.course = self.vault / "02-Areas" / "Academics" / "TEST-101"
        (self.course / "guide").mkdir(parents=True)
        (self.course / "lectures").mkdir()
        for n in (1, 2, 3):
            (self.course / "lectures" / f"l{n}.md").write_text("# src\n",
                                                               encoding="utf-8")
        (self.course / "test-101.md").write_text(
            "---\ntype: course-index\ncourse: TEST-101\nstatus: active\n---\n",
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def module(self, n: int, estimate=30):
        text = (_valid().replace("module: 1", f"module: {n}")
                .replace("estimate: 30", f"estimate: {estimate}")
                .replace("q-1-", f"q-{n}-"))
        (self.course / "guide" / f"test-101-m{n:02d}-mod.md").write_text(
            text, encoding="utf-8")

    def chain(self, done: list, open_: list = ()):
        rows = ["---\ntype: guide\ncourse: TEST-101\ntags: [guide]\n---\n",
                "## Modules\n"]
        rows += [f"- [x] M{n:02d} · [[test-101-m{n:02d}-mod|Mod {n}]]" for n in done]
        rows += [f"- [ ] M{n:02d} · [[test-101-m{n:02d}-mod|Mod {n}]]" for n in open_]
        (self.course / "test-101-guide.md").write_text("\n".join(rows) + "\n",
                                                        encoding="utf-8")

    def log(self, *rows: str):
        (self.course / "test-101-study-log.md").write_text(
            LOG_HEAD + "".join(f"{r}\n" for r in rows), encoding="utf-8")

    def row(self, label, minutes=None, date=DAY):
        time = f" · ⏱ {minutes} min" if minutes is not None else ""
        return f"- {date} · {label}{time} · 8 answered, 5 correct · missed 3"


class TestPace(PaceBase):
    def three_measured(self):
        for n in (1, 2, 3):
            self.module(n, estimate=30)
        self.chain(done=[1, 2, 3])
        self.log(self.row("M01", 36), self.row("M02", 45), self.row("M03", 39))

    def test_the_multiplier_is_actual_over_estimated(self):
        self.three_measured()
        p = rc.pace(self.vault, "TEST-101", split=OPEN)
        self.assertEqual((p["basis"], p["n"]), ("course", 3))
        self.assertEqual((p["estimate"], p["actual"]), (90, 120))
        self.assertEqual(p["multiplier"], round(120 / 90, 2))

    def test_below_the_floor_it_reports_unmeasured_not_one(self):
        """1.0 is a number. 'I have not measured this yet' is a different
        thing, and a dashboard that cannot tell them apart shows the number."""
        for n in (1, 2):
            self.module(n)
        self.chain(done=[1, 2])
        self.log(self.row("M01", 36), self.row("M02", 45))
        p = rc.pace(self.vault, "TEST-101", split=OPEN)
        self.assertIsNone(p["multiplier"])
        self.assertIsNone(p["basis"])
        self.assertEqual(p["n"], 2)

    def test_an_unfinished_module_is_not_measured(self):
        """Half a module studied today against a whole module's estimate would
        report you as twice as fast as you are."""
        self.three_measured()
        self.chain(done=[1, 2], open_=[3])
        p = rc.pace(self.vault, "TEST-101", split=OPEN)
        self.assertIsNone(p["multiplier"])
        self.assertEqual(p["n"], 2)

    def test_a_session_covering_two_units_is_dropped_not_split(self):
        self.three_measured()
        self.log(self.row("M01", 36), self.row("M02", 45),
                 self.row("M03+CP1", 60))
        p = rc.pace(self.vault, "TEST-101", split=OPEN)
        self.assertEqual(p["n"], 2)          # M03's minutes are unattributable
        self.assertIsNone(p["multiplier"])

    def test_a_session_left_open_overnight_is_dropped(self):
        self.three_measured()
        self.log(self.row("M01", 36), self.row("M02", 45), self.row("M03", 600))
        p = rc.pace(self.vault, "TEST-101", split=OPEN)
        self.assertEqual(p["n"], 2)

    def test_rows_written_before_the_clock_existed_contribute_nothing(self):
        self.three_measured()
        self.log(self.row("M01"), self.row("M02"), self.row("M03"))
        self.assertEqual(rc.pace(self.vault, "TEST-101", split=OPEN)["n"], 0)

    def test_two_sessions_on_one_module_add_up(self):
        self.three_measured()
        self.log(self.row("M01", 20), self.row("M01", 16, date="2026-08-09"),
                 self.row("M02", 45), self.row("M03", 39))
        p = rc.pace(self.vault, "TEST-101", split=OPEN)
        self.assertEqual((p["n"], p["actual"]), (3, 120))

    def test_a_course_with_too_little_data_borrows_the_vaults_pool(self):
        self.three_measured()
        p = rc.pace(self.vault, "TEST-101", split=OPEN)
        self.assertEqual(p["basis"], "course")
        # a second course with nothing of its own gets the vault-wide answer
        other = self.vault / "02-Areas" / "Academics" / "TEST-202"
        other.mkdir(parents=True)
        (other / "test-202.md").write_text(
            "---\ntype: course-index\ncourse: TEST-202\nstatus: active\n---\n",
            encoding="utf-8")
        q = rc.pace(self.vault, "TEST-202", split=OPEN)
        self.assertEqual((q["basis"], q["multiplier"]), ("vault", p["multiplier"]))

    def test_the_projection_says_estimated_until_it_can_say_measured(self):
        for n in (1, 2, 3, 4):
            self.module(n, estimate=30)
        self.chain(done=[1, 2], open_=[3, 4])
        self.log(self.row("M01", 36), self.row("M02", 45))
        pr = rc.projected(self.vault, "TEST-101", split=OPEN)
        self.assertEqual((pr["open"], pr["estimate"]), (2, 60))
        self.assertIsNone(pr["minutes"])

        self.chain(done=[1, 2, 3], open_=[4])
        self.log(self.row("M01", 36), self.row("M02", 45), self.row("M03", 39))
        pr = rc.projected(self.vault, "TEST-101", split=OPEN)
        self.assertEqual(pr["open"], 1)
        self.assertEqual(pr["minutes"], 40)      # 30 min × the measured 1.33

    def test_an_unwritten_module_is_counted_but_not_estimated(self):
        self.module(1)
        self.chain(done=[], open_=[1, 9])
        pr = rc.projected(self.vault, "TEST-101", split=OPEN)
        self.assertEqual((pr["open"], pr["unestimated"]), (2, 1))


# --------------------------------------------------------------------------
# the daily sweep, and the course index link
# --------------------------------------------------------------------------

class TestSweep(PaceBase):
    def setUp(self):
        super().setUp()
        (self.vault / ".gitignore").write_text("", encoding="utf-8")
        _git(self.vault, "init", "-b", "master")
        _git(self.vault, "config", "user.name", "T")
        _git(self.vault, "config", "user.email", "t@e.com")
        self._saved = (ledger.LEDGER_PATH, gitops.MUTEX_PATH, rc.log)
        ledger.LEDGER_PATH = Path(self.tmp.name) / "ledger.jsonl"
        gitops.MUTEX_PATH = Path(self.tmp.name) / "git.lock"
        rc.log = lambda msg: None

    def tearDown(self):
        ledger.LEDGER_PATH, gitops.MUTEX_PATH, rc.log = self._saved
        super().tearDown()

    def write_cards(self, *cards):
        p = self.course / "test-101-recall.md"
        p.write_text(_note(*cards), encoding="utf-8")
        _git(self.vault, "add", "-A")
        _git(self.vault, "commit", "-m", "seed")
        return p

    def test_a_sweep_with_nothing_due_writes_nothing(self):
        self.write_cards(rc.compose("Topic 1", "m", "M01", 1, DAY))
        before = _git(self.vault, "rev-parse", "HEAD").stdout
        out = rc.sweep(self.vault, today=DAY)
        self.assertEqual(out["expired"], 0)
        self.assertEqual(_git(self.vault, "rev-parse", "HEAD").stdout, before)

    def test_a_sweep_retires_commits_and_ledgers(self):
        self.write_cards(rc.compose("Topic 1", "m", "M01", 1,
                                    _days_before(rc.EXPIRY_DAYS)))
        out = rc.sweep(self.vault, today=DAY)
        self.assertEqual(out["expired"], 1)
        self.assertEqual(out["notes"][0]["file"], REL)
        self.assertIsNotNone(out["notes"][0]["sha"])
        text = (self.course / "test-101-recall.md").read_text(encoding="utf-8")
        self.assertIn(f"expired::{DAY}", text)
        entry = ledger.entries()[0]
        self.assertEqual((entry["actor"], entry["target"]), ("recall", REL))
        self.assertEqual(entry["sha"], out["notes"][0]["sha"])

    def test_a_sweep_is_idempotent(self):
        self.write_cards(rc.compose("Topic 1", "m", "M01", 1,
                                    _days_before(rc.EXPIRY_DAYS)))
        rc.sweep(self.vault, today=DAY)
        self.assertEqual(rc.sweep(self.vault, today=DAY)["expired"], 0)

    def test_a_course_with_no_recall_note_is_skipped_not_created(self):
        out = rc.sweep(self.vault, today=DAY)
        self.assertEqual(out["expired"], 0)
        self.assertFalse((self.course / "test-101-recall.md").exists())


class TestIndexLink(unittest.TestCase):
    HEAD = ("---\ntype: course-index\ncourse: TEST-101\n---\n\n"
            "# TEST-101\n\n## Material\n\n- a.pdf\n\n## Notes\n\n- [[some-note]]\n")

    def test_the_link_joins_the_guide_group_when_there_is_one(self):
        text = self.HEAD + "\n### Guide\n- [[test-101-guide|the chain]]\n"
        out = rc.link_in_index(text, "TEST-101")
        self.assertIn("[[test-101-recall|recall cards raised from misses]]", out)
        self.assertGreater(out.index("test-101-recall"), out.index("### Guide"))

    def test_it_falls_back_to_the_notes_section(self):
        out = rc.link_in_index(self.HEAD, "TEST-101")
        self.assertGreater(out.index("test-101-recall"), out.index("## Notes"))

    def test_an_index_with_nowhere_to_put_it_is_left_alone(self):
        self.assertIsNone(rc.link_in_index("# TEST-101\n\njust prose\n", "TEST-101"))

    def test_it_never_links_twice(self):
        out = rc.link_in_index(self.HEAD, "TEST-101")
        self.assertIsNone(rc.link_in_index(out, "TEST-101"))


if __name__ == "__main__":
    unittest.main()
