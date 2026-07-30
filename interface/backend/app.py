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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent import VAULT, build_options

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


def describe(tool: str, args: dict) -> str:
    """One short line naming what the agent is doing, for the activity trail."""
    if tool == "Read":
        return Path(str(args.get("file_path", ""))).name
    if tool == "Grep":
        return f"/{args.get('pattern', '')}/"
    if tool == "Glob":
        return str(args.get("pattern", ""))
    if tool.endswith("propose_change"):
        # The one call that changes something on disk deserves to be legible in
        # the trail rather than showing up as a bare tool name.
        return str(args.get("title") or "").strip()
    return ""


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

    saw_text = False
    try:
        # ClaudeSDKClient rather than query(): it always runs the CLI in
        # streaming mode, which is what makes `can_use_tool` fire at all —
        # query() with a plain string prompt rejects the callback outright, and
        # a privacy guard that silently does not run is the whole failure mode
        # this project keeps rediscovering.
        async with ClaudeSDKClient(options=opts) as client:
            await client.connect(as_stream(ask.question))
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
