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


@app.get("/api/health")
def api_health():
    """Sigma's own watchdog, surfaced to the UI — same checks, same source."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runtime"))
    import doctor
    findings = [{"level": lv, "what": w, "fix": f} for lv, w, f in doctor.collect()]
    return {"vault": str(VAULT),
            "ok": all(f["level"] == "ok" for f in findings),
            "findings": findings}
