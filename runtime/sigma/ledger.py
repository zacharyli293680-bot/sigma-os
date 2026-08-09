"""
ledger — the append-only record of everything Sigma changed.

One JSONL line per change, written at the moment the change is made, carrying
the commit SHA that made it revertible. This is the morning view's source of
truth (GET /api/activity) and the thing a revert button consults — so unlike
the state files, which are caches of what already happened, losing this file
would lose the *only* index of which commits were Sigma's. Append-only with
no rewrite path, by construction: there is no function in this module that
truncates.

Entries are appended while the git mutex is held (every writer commits and
records inside the same vault_write/revert block), so concurrent appends do
not interleave in practice; the read side tolerates a torn tail line anyway.
"""
import datetime
import json
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
LEDGER_PATH = RUNTIME / "ledger.jsonl"

# "skip" joined in study S2: flipping a chain row to `[-]` is neither a toggle
# (no completion happened) nor an update (nothing was reworded), and collapsing
# it to "update" would make the morning ledger read as if the row was edited.
ACTIONS = ("create", "update", "toggle", "revert", "append", "skip")


def record(actor: str, action: str, target: str, sha: str | None,
           summary: str, extra: dict | None = None) -> dict:
    """Append one entry. Returns it. Swallows OSError like the loggers do —
    a full disk must cost a ledger line, not the change that just landed
    (the change is still in git either way)."""
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "actor": actor,
        "action": action if action in ACTIONS else "update",
        "target": str(target).replace("\\", "/"),
        "sha": sha,
        "summary": " ".join(str(summary).split())[:300],
        **({"extra": extra} if extra else {}),
    }
    try:
        with LEDGER_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def entries(limit: int = 100) -> list[dict]:
    """Newest first, with `reverted` stamped onto any entry whose commit a
    later revert entry undid — the UI needs that to strike the row and
    disable its undo button rather than offering to revert a revert."""
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue                             # a torn tail line, skip
        if isinstance(e, dict):
            out.append(e)
    reverted = {(e.get("extra") or {}).get("reverts")
                for e in out if e.get("action") == "revert" and e.get("sha")}
    reverted.discard(None)
    for e in out:
        e["reverted"] = bool(e.get("sha")) and e["sha"] in reverted
    out.reverse()
    return out[:max(1, int(limit))]
