"""
spend — the honest half of the window meter.

Nothing documented exposes subscription headroom, so this does not pretend to
know it. What it CAN know: every model call Sigma makes, when, by whom, what
the SDK said it notionally cost, and — the load-bearing fact — when a call
last hit the rate limit. The dashboard-plan section 8 spike concluded these
proxies are the meter until something better exists; this module is those
proxies, persisted instead of computed-and-discarded at four separate sites.

The subscription window is a rolling ~5 hours, so summaries look back that
far. A rate-limit event inside the window is the signal the fleet degrades
on; a second one while already degraded is the signal it pauses on.
"""
import datetime
import json
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
SPEND_PATH = RUNTIME / "spend.jsonl"
WINDOW_HOURS = 5


def record_spend(actor: str, model: str | None = None, cost_usd=None,
                 seconds=None, rate_limited: bool = False,
                 note: str | None = None) -> dict:
    """Append one model-call record. Never raises — a metering failure must
    never cost the call it was metering."""
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "actor": actor,
        "model": model,
        "cost_usd": round(float(cost_usd), 6) if isinstance(cost_usd, (int, float)) else None,
        "seconds": round(float(seconds), 1) if isinstance(seconds, (int, float)) else None,
        "rate_limited": bool(rate_limited),
        **({"note": " ".join(str(note).split())[:200]} if note else {}),
    }
    try:
        with SPEND_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def _parse_ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def window(hours: float = WINDOW_HOURS) -> dict:
    """What happened inside the current rolling window. All proxies, and
    labelled as such at the API layer — calls and cost are facts, headroom
    remains unknown."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    calls, cost, first, last, last_rl = 0, 0.0, None, None, None
    cost_known = False
    by_actor = {}
    try:
        lines = SPEND_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(e.get("ts"))
        if ts is None or ts < cutoff:
            continue
        calls += 1
        by_actor[e.get("actor") or "?"] = by_actor.get(e.get("actor") or "?", 0) + 1
        if isinstance(e.get("cost_usd"), (int, float)):
            cost += e["cost_usd"]
            cost_known = True
        first = first or e.get("ts")
        last = e.get("ts")
        if e.get("rate_limited"):
            last_rl = e.get("ts")
    return {"hours": hours, "calls": calls,
            "cost_usd": round(cost, 4) if cost_known else None,
            "first_call": first, "last_call": last,
            "last_rate_limit": last_rl, "by_actor": by_actor}


def rate_limited_within(minutes: float, win: dict | None = None) -> bool:
    w = win or window()
    ts = _parse_ts(w.get("last_rate_limit"))
    return ts is not None and (datetime.datetime.now() - ts).total_seconds() < minutes * 60


def resume_estimate(win: dict | None = None) -> str | None:
    """When a spent window probably rolls over. An estimate and labelled so:
    the window is rolling, so the earliest call inside it ageing out is the
    honest guess — clamped to at least 15 minutes after the limit was hit,
    because 'resumes in the past' is a lie the UI must never tell."""
    w = win or window()
    if not w.get("last_rate_limit"):
        return None
    base = _parse_ts(w.get("first_call")) or _parse_ts(w.get("last_rate_limit"))
    if base is None:
        return None
    est = base + datetime.timedelta(hours=w.get("hours", WINDOW_HOURS))
    floor = _parse_ts(w["last_rate_limit"]) + datetime.timedelta(minutes=15)
    return max(est, floor).isoformat(timespec="minutes")
