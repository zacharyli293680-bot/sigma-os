/**
 * The chat, relocated into a drawer (dashboard-plan §3: "chat becomes a panel").
 *
 * The conversation logic is the Phase 3 interface's, moved not rewritten: the
 * same SSE reader over POST /api/ask, the same React-nodes-only Markdown subset
 * (no dangerouslySetInnerHTML anywhere — a vault note containing a stray
 * <script> must render as text, never run).
 *
 * That Markdown subset now lives in `answer.tsx` with the rest of §4's answer
 * grammar, and this file is what it says on the tin again: the SSE reader, the
 * turn list, and the composer.
 */
import { useEffect, useRef, useState } from "react";
import { API } from "./api";
import Answer from "./answer";

type Tool = { name: string; detail: string };
type Turn = {
  q: string;
  a: string;
  tools: Tool[];
  blocked: string[];
  /** Where the answer proper begins in `a`.
   *
   *  An agentic turn narrates: "I'll check the vault's structure", a Read, "now
   *  let me look for overdue work", a Grep, and only then the answer. Those
   *  remarks arrive as ordinary token deltas and app.py joins them into one
   *  string with a blank line between, so `a` is preamble + preamble + answer —
   *  and §4's claim, which is the first paragraph, would have been "I'll check
   *  the vault's structure." promoted to a headline.
   *
   *  The boundary is knowable without a backend change: text written *before*
   *  the last lookup is narration about the lookups. Recorded on every `tool`
   *  event, so it ends up at the last one. */
  answerAt?: number;
  stats?: { turns: number; cost: number | null };
  error?: string;
};

export default function ChatDrawer({ open, vault, onClose, onTool }: {
  open: boolean; vault: string; onClose: () => void;
  onTool?: (detail: string) => void;   // feeds the brain's live firing (D2)
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // React state is async — two clicks in one tick both read busy===false and
  // stream into each other's bubble. The ref is checked and set synchronously.
  const inFlight = useRef(false);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);

  async function ask(question: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setTurns(t => [...t, { q: question, a: "", tools: [], blocked: [] }]);
    const patch = (fn: (t: Turn) => Turn) =>
      setTurns(ts => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));

    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    try {
      const res = await fetch(`${API}/api/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, session_id: session }),
      });
      if (!res.body) throw new Error("no response body");

      reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const chunks = buf.split(/\r?\n\r?\n/);
        buf = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const line = chunk.split(/\r?\n/).find(l => l.startsWith("data: "));
          if (!line) continue;
          let e;
          try { e = JSON.parse(line.slice(6)); } catch { continue; }  // skip a torn frame, keep the stream
          if (e.type === "token") patch(t => ({ ...t, a: t.a + e.text }));
          else if (e.type === "tool") {
            patch(t => ({ ...t, answerAt: t.a.length,
                          tools: [...t.tools, { name: e.name, detail: e.detail }] }));
            if (e.detail) onTool?.(e.detail);
          }
          else if (e.type === "denied")
            patch(t => ({ ...t, blocked: [...t.blocked, e.message] }));
          else if (e.type === "error") {
            patch(t => ({ ...t, error: e.message }));
            // A dead session id fails every subsequent question identically —
            // drop it so the next ask starts a fresh conversation.
            setSession(null);
          }
          else if (e.type === "done") {
            setSession(e.session_id);
            patch(t => ({ ...t, stats: { turns: e.turns, cost: e.cost_usd } }));
          }
        }
      }
    } catch (err) {
      patch(t => ({ ...t, error: String(err) }));
      setSession(null);
    } finally {
      // Without the cancel, an aborted stream leaves the backend agent
      // running to completion with nothing consuming it.
      try { await reader?.cancel(); } catch { /* already closed */ }
      inFlight.current = false;
      setBusy(false);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const q = draft.trim();
    if (!q || busy) return;
    setDraft("");
    ask(q);
  }

  return (
    <aside className={`drawer ${open ? "open" : ""}`} aria-hidden={!open}>
      <header className="drawer-head">
        <span className="label">ASK SIGMA</span>
        <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
      </header>

      <main className="drawer-main">
        {/* §3.3 — the empty state is the most designed screen: a centred mark,
            one headline, one sentence, three tiles that teach what this is for
            in about four seconds and then get out of the way. The tiles were
            already here as bare strings; what they lacked was a title saying
            what *kind* of question each one is, which is the part that
            generalises past the three examples. */}
        {turns.length === 0 && (
          <div className="empty">
            <span className="empty-mark" aria-hidden="true">Σ</span>
            <h2>Ask the vault.</h2>
            <p>
              Every answer is grounded in notes read this turn, and cites them.
              If the vault is silent on something, it says so.
            </p>
            <div className="suggestions">
              {[
                ["◷", "Catch up", "What am I behind on?"],
                ["◇", "Where each course stands", "What's the status of each course?"],
                ["⟳", "Sigma on itself", "What has Sigma learned about itself?"],
              ].map(([icon, title, q]) => (
                <button key={q} onClick={() => !busy && ask(q)}>
                  <span className="sugg-icon" aria-hidden="true">{icon}</span>
                  <span className="sugg-title">{title}</span>
                  <span className="sugg-eg">{q}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) => {
          const live = busy && i === turns.length - 1;
          return (
          <article key={i}>
            <div className="q">{t.q}</div>

            {/* While the answer is being written, the trail *is* the content —
                it is the only sign anything is happening. Once the answer
                exists the same facts render below it as provenance (§4), so
                keeping both would be saying it twice. */}
            {live && t.tools.length > 0 && (
              <details className="trail" open>
                <summary>{t.tools.length} lookup{t.tools.length > 1 ? "s" : ""}</summary>
                {t.tools.map((x, j) => (
                  <div key={j} className="step">
                    <span className="tool">{x.name}</span> {x.detail}
                  </div>
                ))}
              </details>
            )}

            <div className="a">
              {t.a ? (
                <Answer text={t.a} answerAt={t.answerAt} tools={t.tools}
                        blocked={t.blocked} vault={vault} streaming={live} />
              ) : live ? (
                <span className="thinking">thinking…</span>
              ) : null}
            </div>

            {t.error && <div className="error">{t.error}</div>}
            {t.stats && (
              <div className="stats">
                {t.stats.turns} turns
                {t.stats.cost != null && ` · $${t.stats.cost.toFixed(3)}`}
              </div>
            )}
          </article>
          );
        })}
        <div ref={endRef} />
      </main>

      <form className="drawer-form" onSubmit={submit}>
        <input
          ref={inputRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder={busy ? "thinking…" : "Ask the vault…"}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>Ask</button>
      </form>
    </aside>
  );
}
