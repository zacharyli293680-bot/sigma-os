"""
Shared flags on a group parser and its verbs must mean the same thing in either
order.

This exists because they did not. Every group that offers `--dry-run` on both
the group and its verbs — `intake`, `devlog`, `map` — registered the flag twice
with a concrete default, and argparse parses a subcommand into a *fresh*
namespace and then copies every key of it onto the parent's. So the verb's
`store_true` default overwrote the flag the user had already typed, and
`sigma intake --dry-run run` performed a **real** run: model calls spent, drop
folder cleared, nothing printed to say the flag had been dropped. The failure
was invisible precisely because a dry run and a real run start out looking the
same.

The fix is `cli.shared_flags`, which gives the verbs `argparse.SUPPRESS` as
their default so an untyped flag leaves no key to copy. These tests pin the
behaviour rather than the mechanism: what matters is that both orders agree,
and that omitting the flag still yields a real run.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "runtime"))

import cli                                      # noqa: E402


def parse(argv):
    return cli.build_parser().parse_args(argv.split())


class BothOrdersAgree(unittest.TestCase):
    """`--flag verb` and `verb --flag` are the same command."""

    def test_dry_run_survives_before_the_verb(self):
        # The regression itself. Before the fix this was False.
        for argv in ("intake --dry-run run", "intake --dry-run continue",
                     "devlog --dry-run run", "map --dry-run run"):
            with self.subTest(argv=argv):
                self.assertTrue(parse(argv).dry_run)

    def test_dry_run_survives_after_the_verb(self):
        for argv in ("intake run --dry-run", "intake continue --dry-run",
                     "devlog run --dry-run", "map run --dry-run"):
            with self.subTest(argv=argv):
                self.assertTrue(parse(argv).dry_run)

    def test_omitted_means_a_real_run(self):
        """The half that must not regress in the other direction: suppressing
        the verb's default must not leave the flag mysteriously on."""
        for argv in ("intake run", "intake continue", "devlog run", "map run"):
            with self.subTest(argv=argv):
                self.assertFalse(parse(argv).dry_run)

    def test_value_flags_too(self):
        """`--course`/`--project`/`--max` take values, so a clobbered one sends
        the run at the wrong target rather than merely at the wrong mode."""
        self.assertEqual(parse("intake --course CSE-351 run").course, "CSE-351")
        self.assertEqual(parse("intake run --course CSE-351").course, "CSE-351")
        self.assertEqual(parse("intake run").course, "")
        self.assertEqual(parse("intake --max 3 run").max, "3")
        self.assertTrue(parse("intake --keep run").keep)
        self.assertEqual(parse("devlog --project sigma-os run").project, "sigma-os")
        self.assertEqual(parse("map --project sigma-os run").project, "sigma-os")

    def test_continue_still_marks_itself(self):
        """`cont` comes from `set_defaults` on the same subparser SUPPRESS now
        touches — it must still reach the namespace, or `continue` silently
        becomes `run`, which is the more destructive verb of the two."""
        self.assertTrue(getattr(parse("intake continue"), "cont", False))
        self.assertFalse(getattr(parse("intake run"), "cont", False))


if __name__ == "__main__":
    unittest.main()
