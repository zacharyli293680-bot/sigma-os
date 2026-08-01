/**
 * capture.tsx — quick capture (Phase 6), on Ctrl+N.
 *
 * `dashboard-vision`: "an idea, a task, a link, a screenshot → the inbox,
 * without leaving what you were doing." The point is the *without leaving*, so
 * this is a box that opens over whatever you were looking at, takes plain text,
 * and closes. Ctrl+Enter submits, because a capture box that needs a mouse to
 * finish is one you stop using.
 *
 * It writes one note into 00-Inbox as its own revertible commit. A capture you
 * did not mean is one click from gone in the ledger.
 */
import { useEffect, useRef, useState } from "react";
import { ApiError, post } from "./api";

export default function Capture({ open, onClose, onDone }: {
  open: boolean; onClose: () => void; onDone: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open) {
      setText(""); setSaid(null); setBusy(false);
      // The box is useless if you have to click into it.
      setTimeout(() => ref.current?.focus(), 30);
    }
  }, [open]);

  if (!open) return null;

  async function send() {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true); setSaid(null);
    try {
      const r = await post<{ file: string; sha: string | null }>("capture", { text: body });
      setSaid(`saved → ${r.file}${r.sha ? "" : " (no commit)"}`);
      setText("");
      onDone();
      setTimeout(onClose, 900);
    } catch (e) {
      setSaid(e instanceof ApiError ? (e.detail || e.code) : "backend unreachable");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="capture" onClick={e => e.stopPropagation()}
           role="dialog" aria-label="Quick capture">
        <header className="capture-head">
          <span className="label">+ CAPTURE → 00-Inbox</span>
          <span className="dim">Ctrl+Enter to save · Esc to cancel</span>
        </header>
        <textarea ref={ref} value={text} rows={5}
                  placeholder="an idea, a task, a link…"
                  onChange={e => setText(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
                    // Esc is handled by the shell, but the textarea would
                    // otherwise swallow it before it ever gets there.
                    if (e.key === "Escape") { e.preventDefault(); onClose(); }
                  }} />
        {said && <p className="capture-said">{said}</p>}
        <footer className="capture-actions">
          <span className="dim">
            filed as <code>type: resource</code> — re-type it when you triage
          </span>
          <button onClick={send} disabled={busy || !text.trim()}>
            {busy ? "saving…" : "capture"}
          </button>
        </footer>
      </div>
    </div>
  );
}
