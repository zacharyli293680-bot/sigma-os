# Phase 3 — Interface

Ask the vault a question; get an answer grounded in notes it actually read, with
working `[[wikilinks]]` back to them.

```
backend/    FastAPI + Claude Agent SDK   → localhost:8787 (serves the built UI too)
frontend/   React + Vite                 → dist/ built once; npm run dev only for UI work
```

## Running it

```powershell
sigma ui            # one process, one port — backend + built frontend on :8787
```

For frontend development only, run the Vite dev server against that backend:
`cd interface\frontend; npm run dev` (any loopback port is allowed by CORS).

First time only: `python -m venv .venv` then `.\.venv\Scripts\pip install -r requirements.txt`
in `backend/`, and `npm install && npm run build` in `frontend/`.

**No API key.** The Agent SDK is Claude Code packaged as a library — it spawns the same
CLI and inherits the same login. Verified with `ANTHROPIC_API_KEY` explicitly unset.

## What it does

- **Reads the vault live.** No index to rebuild; answers reflect what is on disk now.
- **Cites by wikilink**, and each one is an `obsidian://` link — click it and the note
  opens in Obsidian.
- **Streams tokens** as they are written, with a collapsible trail of every lookup.
- **Surfaces the watchdog.** `/api/health` runs `doctor.py`, so the header shows whether
  Sigma itself is healthy.
- **Proposes, never applies** (2026-07-28). Ask it to change something and it files a
  pending proposal via the `propose_change` tool (`propose.py`) — the backend writes the
  file, so the agent holds no filesystem write primitive. Same `--apply` gate as the
  weekly reflection: one approval path in the whole OS.

## The privacy guard

The vault's `.gitignore` already declares what must never leave this machine — the
internship carve-out lives there, and the pre-push hook enforces it at the push
boundary. `privacy.py` enforces the same declaration at the **model** boundary:

> if git will not sync it, the model does not see it.

That matters because those notes sit on disk, readable, inside the very folder the agent
is pointed at. "What am I behind on?" walks straight into them. Reusing `.gitignore`
rather than keeping a second list is the point — a parallel list drifts, silently and in
the unsafe direction.

### Getting it to actually run took three attempts, and each failure was silent

Worth recording, because "the guard is configured" and "the guard executes" are different
claims — the same lesson the hook-quoting bug taught, in a different costume.

| Attempt | Why nothing fired |
|---|---|
| `can_use_tool` + `allowed_tools=["Read",…]` | `allowed_tools` means *callable without being prompted*. Since the callback **is** the prompt, listing a tool there auto-approves it. |
| `can_use_tool` + `tools=[…]`, `allowed_tools=[]` | In `permission_mode="default"`, Claude Code treats Read/Grep/Glob as safe and never routes them through a permission prompt at all. |
| **`PreToolUse` hook** ✅ | Hooks fire for **every** tool call, whatever the permission mode. |

The first two both looked like success: the agent answered, and the run reported
`0 denials` — because nothing had been denied, not because nothing needed denying. The
test that caught it was adversarial (*"read these two carved-out notes and summarise
them"*), not a happy-path check. `can_use_tool` is kept as a second layer; the guarantee
is the hook.

`warnings.simplefilter("always")` is set in `app.py` so the SDK's own
`CanUseToolShadowedWarning` can't pass unnoticed next time.

## Next

- Multi-turn continuity is wired (`session_id` round-trips) but untested across long threads.
- Package as Tauri so it launches like an app rather than a uvicorn command.
- The dashboard (see the vault's `dashboard-plan` note) grows this backend from two
  routes into the read API for panels, fleet progress, and the graph.

*(Two former items shipped 2026-07-28: writing via propose-don't-apply, and the backend
serving its own built UI.)*
