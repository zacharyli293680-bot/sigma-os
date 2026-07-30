/**
 * The chat, relocated into a drawer (dashboard-plan §3: "chat becomes a panel").
 *
 * The conversation logic is the Phase 3 interface's, moved not rewritten: the
 * same SSE reader over POST /api/ask, the same React-nodes-only Markdown subset
 * (no dangerouslySetInnerHTML anywhere — a vault note containing a stray
 * <script> must render as text, never run).
 */
import { useEffect, useRef, useState } from "react";
import { API, obsidianHref } from "./api";

type Tool = { name: string; detail: string };
type Turn = {
  q: string;
  a: string;
  tools: Tool[];
  blocked: string[];
  stats?: { turns: number; cost: number | null };
  error?: string;
};

function inline(text: string, vault: string, key: string) {
  const parts = text.split(/(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.filter(Boolean).map((p, i) => {
    const k = `${key}-${i}`;
    if (p.startsWith("[[") && p.endsWith("]]")) {
      const [target, label] = p.slice(2, -2).split("|");
      return (
        <a key={k} className="wikilink" href={obsidianHref(vault, target)}
           title={`Open ${target} in Obsidian`}>
          {label || target}
        </a>
      );
    }
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={k}>{p.slice(2, -2)}</strong>;
    if (p.startsWith("`") && p.endsWith("`")) return <code key={k}>{p.slice(1, -1)}</code>;
    return <span key={k}>{p}</span>;
  });
}

function Markdown({ text, vault }: { text: string; vault: string }) {
  const blocks: React.ReactNode[] = [];
  let list: React.ReactNode[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={`ul-${blocks.length}`}>{list}</ul>);
      list = [];
    }
  };

  text.split("\n").forEach((line, i) => {
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      blocks.push(<h3 key={i}>{inline(heading[2], vault, `h${i}`)}</h3>);
    } else if (bullet) {
      list.push(<li key={i}>{inline(bullet[1], vault, `li${i}`)}</li>);
    } else if (line.trim() === "") {
      flush();
    } else {
      flush();
      blocks.push(<p key={i}>{inline(line, vault, `p${i}`)}</p>);
    }
  });
  flush();
  return <>{blocks}</>;
}

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
            patch(t => ({ ...t, tools: [...t.tools, { name: e.name, detail: e.detail }] }));
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
        {turns.length === 0 && (
          <div className="empty">
            <p>Ask the vault a question. Answers cite the notes actually read.</p>
            <div className="suggestions">
              {[
                "What am I behind on?",
                "What's the status of each course?",
                "What has Sigma learned about itself?",
              ].map(s => (
                <button key={s} onClick={() => !busy && ask(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) => (
          <article key={i}>
            <div className="q">{t.q}</div>

            {t.tools.length > 0 && (
              <details className="trail">
                <summary>{t.tools.length} lookup{t.tools.length > 1 ? "s" : ""}</summary>
                {t.tools.map((x, j) => (
                  <div key={j} className="step">
                    <span className="tool">{x.name}</span> {x.detail}
                  </div>
                ))}
              </details>
            )}

            {t.blocked.map((b, j) => (
              <div key={j} className="blocked"
                   title="This path is gitignored, so it is never sent to the model.">
                <span>🔒</span> {b}
              </div>
            ))}

            <div className="a">
              {t.a ? (
                <Markdown text={t.a} vault={vault} />
              ) : busy && i === turns.length - 1 ? (
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
        ))}
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
