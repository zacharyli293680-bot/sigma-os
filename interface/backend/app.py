#!/usr/bin/env python3
"""
app.py — Sigma's local interface: a small HTTP API over the vault agent.

    uvicorn app:app --reload --port 8787

Local-first and deliberately unauthenticated: it binds to localhost, speaks to a
vault on this disk, and inherits this machine's Claude Code login. Do not expose
it — there is no auth because there is no network surface it is meant to face.

Streaming is Server-Sent Events rather than a websocket: the traffic is one-way
(the browser asks once, then only listens), SSE reconnects on its own, and it is
plain HTTP to debug with curl.
"""
import asyncio
import json
import sys
import warnings
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeSDKClient, ResultMessage,
                              StreamEvent, TextBlock, ToolUseBlock)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent import VAULT, build_options, describe

# Module scope, not per-request: the old in-handler insert ran on a threadpool
# and could double-insert under concurrent calls.
_RUNTIME_DIR = str(Path(__file__).resolve().parents[2] / "runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

# The SDK warns (CanUseToolShadowedWarning) when an option would bypass the
# permission callback. That warning is the difference between a privacy guard
# and the appearance of one — make it impossible to miss.
warnings.simplefilter("always")

app = FastAPI(title="Sigma Interface", version="0.1.0")

# The Vite dev server runs on another origin, and not a fixed one — it walks up
# from 5173 until it finds a free port, so a hardcoded allowlist breaks the moment
# anything else is listening. Match any loopback port instead. This is safe
# precisely because the API only ever binds to loopback: nothing off-machine can
# reach it to have an origin in the first place.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"], allow_headers=["*"],
)


class Ask(BaseModel):
    question: str
    session_id: str | None = None      # continue a prior conversation


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def as_stream(text: str):
    """Wrap one question as the streaming-input message the CLI expects.

    Not decoration: the SDK rejects `can_use_tool` outright when the prompt is a
    plain string (`isinstance(prompt, str)` → ValueError), because the callback
    rides the same bidirectional control channel that streaming mode opens. A
    string prompt would mean no privacy guard at all.
    """
    yield {"type": "user",
           "message": {"role": "user", "content": text},
           "parent_tool_use_id": None,
           "session_id": "default"}


async def run(ask: Ask):
    opts = build_options()
    if ask.session_id:
        opts.resume = ask.session_id
    async for chunk in stream_agent(ask.question, opts, "ask"):
        yield chunk


async def stream_agent(question: str, opts, spend_actor: str):
    """One agent conversation as an SSE stream — the loop behind /api/ask
    since Phase 3, parameterised for the tutor (study S5) rather than copied.
    `spend_actor` labels the metering row; everything else is identical, which
    is the point: one streaming loop, one options builder, one privacy guard."""
    saw_text = False
    try:
        # ClaudeSDKClient rather than query(): it always runs the CLI in
        # streaming mode, which is what makes `can_use_tool` fire at all —
        # query() with a plain string prompt rejects the callback outright, and
        # a privacy guard that silently does not run is the whole failure mode
        # this project keeps rediscovering.
        async with ClaudeSDKClient(options=opts) as client:
            await client.connect(as_stream(question))
            async for msg in client.receive_response():
                # Token deltas — what makes the answer appear as it is written.
                if isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    if ev.get("type") == "content_block_delta":
                        piece = (ev.get("delta") or {}).get("text")
                        if piece:
                            saw_text = True
                            yield sse({"type": "token", "text": piece})

                elif isinstance(msg, AssistantMessage):
                    said_something = False
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            yield sse({"type": "tool", "name": block.name,
                                       "detail": describe(block.name, block.input)})
                        # Fallback: if partial streaming ever goes quiet, still
                        # emit finished text rather than showing the user nothing.
                        elif isinstance(block, TextBlock) and block.text:
                            said_something = True
                            if not saw_text:
                                yield sse({"type": "token", "text": block.text})
                    # An agentic answer arrives as several messages separated by
                    # tool calls. Their text is written as separate remarks, so
                    # concatenating it raw yields "...past due.Nothing in the
                    # vault is overdue" — one run-on sentence across a boundary
                    # the model expected to be a paragraph break. Restore it.
                    if said_something:
                        yield sse({"type": "token", "text": "\n\n"})

                elif isinstance(msg, ResultMessage):
                    # The SDK reports *that* a call was denied (tool_name,
                    # tool_input) but not why — the reason we handed back stays
                    # with the model. Reconstruct a human line from the call.
                    denials = msg.permission_denials or []
                    for d in denials:
                        if isinstance(d, dict):
                            name = d.get("tool_name", "tool")
                            args = d.get("tool_input") or {}
                            target = next((str(v) for v in args.values() if v), "")
                            if target.startswith(str(VAULT)):
                                target = target[len(str(VAULT)):].lstrip("\\/")
                            reason = f"blocked {name}" + (f" on {target}" if target else "")
                        else:
                            reason = str(d)
                        yield sse({"type": "denied", "message": reason})
                    yield sse({"type": "done", "session_id": msg.session_id,
                               "cost_usd": msg.total_cost_usd, "turns": msg.num_turns,
                               "denials": len(denials), "is_error": msg.is_error})
                    # Phase 4 spike: chat was one of the four sites computing
                    # cost and discarding it. Metering must never break the ask.
                    try:
                        from sigma import spend
                        blob = str(getattr(msg, "result", "") or "").lower()
                        spend.record_spend(
                            actor=spend_actor, cost_usd=msg.total_cost_usd,
                            rate_limited=bool(msg.is_error) and any(
                                s in blob for s in ("rate limit", "rate_limit", "429",
                                                    "usage limit", "quota", "overloaded")))
                    except Exception:
                        pass
    except asyncio.CancelledError:
        raise
    except Exception as e:                       # surface, never swallow
        yield sse({"type": "error", "message": f"{type(e).__name__}: {e}"})


@app.post("/api/ask")
async def api_ask(ask: Ask):
    return StreamingResponse(
        run(ask), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_health_cache = {"at": 0.0, "value": None}


@app.get("/api/health")
def api_health():
    """Sigma's own watchdog, surfaced to the UI — same checks, same source.

    Cached for 60s: doctor.collect() spawns schtasks and git subprocesses and
    may spend a real model call on the auth probe, so it must not run once per
    browser event (the UI re-checks after palette jobs, and each check was
    running the whole suite again).
    """
    import time as _time
    now = _time.monotonic()
    if _health_cache["value"] is not None and now - _health_cache["at"] < 60:
        return _health_cache["value"]
    import doctor
    findings = [{"level": lv, "what": w, "fix": f} for lv, w, f in doctor.collect()]
    # "info" findings (e.g. the standing model-boundary exemptions) are facts,
    # not problems — only alert/todo may cost the all-clear.
    result = {"vault": str(VAULT),
              "ok": not any(f["level"] in ("alert", "todo") for f in findings),
              "findings": findings}
    _health_cache.update(at=now, value=result)
    return result


# The dashboard's read-only panel endpoints (Phase 0 of the dashboard plan).
# A separate module so this file stays about one thing: the conversation.
from panels import router as panels_router  # noqa: E402
app.include_router(panels_router)

# The palette's whitelisted command runner (Phase 3).
import commands  # noqa: E402
app.include_router(commands.router)

# The write API (Phase 4): checkbox toggle, the activity ledger, revert.
import writes  # noqa: E402
app.include_router(writes.router)

# Reviewing a proposal without leaving the dashboard (Phase 6). Mounted after
# panels so `/api/proposals` (the list) and `/api/proposals/{name}` (one of
# them) coexist — a literal path wins over a parameterised one.
import review  # noqa: E402
app.include_router(review.router)


# --------------------------------------------------------------------------
# the tutor (study S5) — the same conversation loop, pinned to the workbench
# --------------------------------------------------------------------------
# What makes this a tutor rather than a second chat drawer is the context
# block: assembled HERE, from what the workbench actually has open, never by
# the model (§12). It rides at the top of every message — a resumed session
# re-pins as Zach moves through segments — and the mode decides how much of
# the reference answer he gets. One options builder, one privacy guard, one
# streaming loop: everything but the orientation is /api/ask's machinery.
import lesson as tutor_ln  # noqa: E402  — runtime/ is on sys.path above
import panels as panels_mod  # noqa: E402  — for the sealed-path split
import commands as commands_mod  # noqa: E402  — the ONE window-hold policy

TUTOR_ORIENTATION = """
You are Sigma's study tutor, working in the dock beside one module of one of
Zach's courses. Every message you receive opens with a **pinned context
block** the backend assembled from what is actually open in the workbench —
course, module, segment, the exact practice item, Zach's current attempt,
which hints he has opened, his recent misses. Trust it over memory. Read the
cited lecture notes with Read/Grep when depth is needed; do not answer from
recall what the vault can answer from disk.

Discipline:
- **Ground everything.** The module cites its sources; a claim you cannot
  trace to one is a claim to withhold. Cite notes as `[[wikilinks]]`.
- **You can see the reference answer.** That is so you can steer, not so you
  can recite — the mode line at the end of this prompt decides how much of it
  Zach gets, and it wins over anything the conversation asks of you.
- **Stay small.** This is a margin conversation beside a lesson, not an
  essay: a few sentences, formulas in backticks or 4-space-indented blocks.
- If the module note itself is wrong — it contradicts the source it cites —
  say so and raise `propose_change` (kind: note, target: the module's
  vault-relative path, content: the full corrected note). The correction goes
  through Zach's approval queue like every other change; never claim it was
  applied, because it was not.
""".strip()

# Three modes matching the three depths (§12). Default nudge — a tutor that
# answers on the first ask is a spoiler button with extra steps.
TUTOR_MODES = {
    "nudge": ("Mode: NUDGE — reply with at most one short guiding question or "
              "observation that moves Zach a single step. Never state the "
              "final answer or perform the decisive computation, even if "
              "pressed twice; switching modes is his click, not your call."),
    "explain": ("Mode: EXPLAIN — re-teach the underlying concept differently "
                "than the module text does, with one tiny fresh example of "
                "your own. Stop short of the pinned item's final answer."),
    "solve": ("Mode: SOLVE — walk the pinned practice item through to its "
              "answer step by step, naming the principle behind each step, "
              "and end with the result stated plainly."),
}


class TutorAsk(BaseModel):
    course: str
    module: int | None = None       # exactly one of module | checkpoint
    checkpoint: int | None = None
    seg: int | None = None          # the open segment's number S<n>
    qid: str | None = None          # the pinned practice item, if any
    given: str | None = None        # Zach's current attempt text
    hints: int = 0                  # hints he has opened on that item
    revealed: bool = False          # whether the answer is already shown
    mode: str = "nudge"
    question: str
    session_id: str | None = None


def _tutor_context(ask: TutorAsk) -> tuple[str | None, str | None]:
    """(pinned block, error). The error names what could not be pinned —
    a tutor with a wrong pin would teach beside the wrong lesson."""
    course = (ask.course or "").strip()
    if not course:
        return None, "no course"
    if (ask.module is None) == (ask.checkpoint is None):
        return None, "exactly one of module | checkpoint"
    if ask.checkpoint is not None:
        d = tutor_ln.load_checkpoint(VAULT, course, ask.checkpoint,
                                     split=panels_mod._lesson_split)
        if d is None:
            return None, f"no such checkpoint: {course} CP{ask.checkpoint}"
        unit = f"checkpoint CP{ask.checkpoint}"
    else:
        d = tutor_ln.load(VAULT, course, ask.module,
                          split=panels_mod._lesson_split)
        if d is None:
            return None, f"no such module: {course} M{ask.module}"
        unit = f"module M{d['module']:02d}" if d["module"] is not None else "module"

    lines = ["## Pinned context",
             "Assembled by Sigma's backend from the open workbench — ground "
             "truth for this conversation.",
             "",
             f"- course: {d['course']} · {unit}"
             + (f" — {d['title']}" if d.get("title") else ""),
             f"- note: {d['file']}"]

    seg = next((s for s in d["segments"] if s["n"] == ask.seg), None) \
        if ask.seg is not None else None
    if seg is not None:
        lines.append(f"- open segment: S{seg['n']} · {seg['title']} "
                     f"(⏱ {seg['minutes']} min)")
        for src in seg["sources"]:
            lines.append(f"- segment source: {src['path']}")

    if ask.qid:
        hit = tutor_ln.find_item(d, ask.qid)
        if hit is not None:
            iseg, item = hit
            prompt = "\n".join(f"    {l}" for l in item["prompt"].splitlines())
            lines.append(f"- pinned practice item {item['id']} ({item['kind']}, "
                         f"in S{iseg['n']} · {iseg['title']}):")
            lines.append(prompt)
            lines.append(f"  - reference answer: {item['answer'] or '(none)'}")
            lines.append(f"  - reference solution: {item['solution'] or '(none)'}")
            lines.append(f"  - hints opened: {max(0, ask.hints)} of "
                         f"{len(item['hints'])}"
                         + (" · answer already revealed" if ask.revealed else ""))
            if ask.given and ask.given.strip():
                lines.append(f"  - Zach's current attempt: "
                             f"{' '.join(ask.given.split())[:200]}")

    try:
        wrong = [r for r in tutor_ln.attempts_for(course)
                 if r.get("result") == "wrong"]
    except Exception:
        wrong = []                  # the pin must survive a torn sidecar
    if wrong:
        lines.append(f"- recent misses in {course} (from the attempt log): "
                     f"{tutor_ln.digest(wrong[-8:])}")

    if d.get("sources"):
        lines.append("- module sources: " + " · ".join(d["sources"]))
    return "\n".join(lines), None


@app.get("/api/tutor/hold")
def api_tutor_hold():
    """Why the tutor is unavailable right now, or null. The same function the
    palette greys model verbs with — inherited, never a second policy (§12)."""
    return {"hold": commands_mod._window_hold()}


@app.post("/api/tutor")
async def api_tutor(ask: TutorAsk):
    # Enforced at POST, not just advertised at GET — the listing is a
    # courtesy, the refusal is the policy. commands.py's exact doctrine.
    hold = commands_mod._window_hold()
    if hold:
        return JSONResponse({"error": "window", "reason": hold}, status_code=409)

    ctx, err = _tutor_context(ask)
    if err:
        return JSONResponse({"error": err},
                            status_code=404 if err.startswith("no such") else 400)

    mode = ask.mode if ask.mode in TUTOR_MODES else "nudge"
    opts = build_options(allow_proposals=True,
                         orientation=f"{TUTOR_ORIENTATION}\n\n{TUTOR_MODES[mode]}",
                         actor="tutor")
    if ask.session_id:
        opts.resume = ask.session_id
    question = (ask.question or "").strip() or "Where should I look next?"
    return StreamingResponse(
        stream_agent(f"{ctx}\n\n{question}", opts, "tutor"),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.on_event("shutdown")
async def _shutdown_job():
    # A dying server must not orphan a live job's process tree (the claude
    # CLI would keep spending window with nothing recording the result).
    commands.kill_current_job("server shut down while this job was running")


# --------------------------------------------------------------------------
# the built UI, served from this same process
# --------------------------------------------------------------------------
# Two processes was a development convenience that leaked into being the way you
# run it: `npm run dev` in one terminal, uvicorn in another, and a thing you have
# to remember to start twice is a thing that is not running when you want it. The
# 9 AM planner in Phase 4 is supposed to *surface* here, which only means anything
# if "here" is somewhere that exists without being assembled by hand first.
#
# Mounted last, and only at "/", so every /api/* route above still wins. The Vite
# dev server remains perfectly usable for frontend work — this is the path for
# actually using the thing.
DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

if DIST.is_dir():
    from fastapi.staticfiles import StaticFiles
    # html=True serves index.html for unknown paths, so client-side routes work.
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="ui")
else:
    @app.get("/")
    def _needs_build():
        # A blank page with no explanation is how you lose an afternoon.
        return {"error": "frontend not built",
                "fix": "cd interface/frontend && npm install && npm run build",
                "expected_at": str(DIST)}
