"""
audit — the append-only record of what an agent *tried* to do.

Deliberately not the ledger sitting next to it, and the distinction is the
whole design:

    ledger.jsonl   what CHANGED. Every entry carries a commit SHA and is
                   revertible; losing it loses the only index of which commits
                   were Sigma's.
    audit.jsonl    what was ATTEMPTED. Refusals now, and every URL a
                   browser-enabled agent touches once that lands. Nothing here
                   is revertible, because nothing here happened.

**Why this exists before the browser does.** `privacy.py` now refuses any tool
it has not been vetted for. The danger of that default is not that it blocks an
attacker — it is that it blocks something *legitimate*, silently, and the run
comes back with a quietly worse answer. That is failure mode #3 in SYSTEM.md
§12 in a new costume: the two times this guard was found not to be running, the
evidence was a run reporting zero denials. "Zero denials" and "nothing needed
denying" look identical from outside, and so do "no false refusals" and "nobody
looked". So every refusal is written down and `doctor` reports the count.

A guard you cannot audit is a guard you cannot trust to have been *right*.

**This file must never sync.** A refused Read names the path it refused, and
those paths are the carve-out. `runtime/*.jsonl` in .gitignore covers it, which
is why the name ends in `.jsonl` rather than `.log`.
"""
import datetime
import json
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
AUDIT_PATH = RUNTIME / "audit.jsonl"

# What happened. `refused` is the only one B0 writes; `navigated` and `fetched`
# are the browser lane's, declared here so the shape of the file is settled
# before two modules start guessing at it.
KINDS = ("refused", "navigated", "fetched")

# Which rule fired, for a refusal. Named rather than free text because the
# doctor keys on `unvetted-tool` specifically: that is the new rule, so it is
# the one whose false positives have not been paid for yet.
RULES = ("unvetted-tool", "write-tool", "path", "origin")


def record(kind: str, actor: str, tool: str, detail: str = "",
           rule: str | None = None, extra: dict | None = None) -> dict:
    """Append one entry. Returns it.

    Swallows OSError the way the loggers and the ledger do: a full disk must
    cost an audit line, not the decision that was just made correctly. The
    refusal still reaches the model either way — this file is for the human.
    """
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "kind": kind if kind in KINDS else "refused",
        "actor": actor or "unknown",
        "tool": str(tool or ""),
        "detail": " ".join(str(detail).split())[:300],
        **({"rule": rule} if rule else {}),
        **({"extra": extra} if extra else {}),
    }
    try:
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def entries(limit: int = 100, path: Path | None = None) -> list[dict]:
    """Newest first. Tolerates a torn tail line, like the ledger's reader."""
    try:
        lines = (path or AUDIT_PATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            out.append(e)
    out.reverse()
    return out[:max(1, int(limit))]


def _parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def recent(hours: float = 24.0, kind: str | None = None, rule: str | None = None,
           path: Path | None = None) -> list[dict]:
    """Entries from the last `hours`, optionally filtered — what the doctor asks.

    An entry with an unparseable timestamp is **included**, not dropped. This
    is a record of things that were refused; the failure direction here should
    be showing you one line too many, never hiding one.
    """
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=float(hours))
    out = []
    for e in entries(limit=10_000, path=path):
        ts = _parse_ts(e.get("ts"))
        if ts is not None and ts < cutoff:
            continue
        if kind and e.get("kind") != kind:
            continue
        if rule and e.get("rule") != rule:
            continue
        out.append(e)
    return out
