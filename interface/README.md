# Phase 3 — Interface

Ask the vault a question; get an answer grounded in notes it actually read, with
working `[[wikilinks]]` back to them.

```
backend/    FastAPI + Claude Agent SDK   → localhost:8787
frontend/   React + Vite                 → localhost:5173 (or the next free port)
```

## Running it

```powershell
# terminal 1
cd interface\backend
.\.venv\Scripts\python.exe -m uvicorn app:app --port 8787

# terminal 2
cd interface\frontend
npm run dev
```

First time only: `python -m venv .venv` then `.\.venv\Scripts\pip install -r requirements.txt`
in `backend/`, and `npm install` in `frontend/`.

**No API key.** The Agent SDK is Claude Code packaged as a library — it spawns the same
CLI and inherits the same login. Verified with `ANTHROPIC_API_KEY` explicitly unset.

## What it does

- **Reads the vault live.** No index to rebuild; answers reflect what is on disk now.
- **Cites by wikilink**, and each one is an `obsidian://` link — click it and the note
  opens in Obsidian.
- **Streams tokens** as they are written, with a collapsible trail of every lookup.
- **Surfaces the watchdog.** `/api/health` runs `doctor.py`, so the header shows whether
  Sigma itself is healthy.
- **Read-only.** Writing would have to obey propose-don't-apply like the rest of Sigma,
  so it is a deliberate later step rather than something left ajar.

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
- Writing, via the propose-don't-apply path — proposals a human approves, never direct edits.
- Serve the built frontend from the backend so it is one process, then Tauri.
