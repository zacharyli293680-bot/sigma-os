import { useEffect, useRef, useState } from "react";
import "./App.css";

// Served by the backend itself in production, so same-origin ("") is correct on
// any port. The absolute fallback is for `npm run dev`, where Vite serves the UI
// and the backend lives on its default port.
const API = import.meta.env.DEV ? "http://127.0.0.1:8787" : "";

type Tool = { name: string; detail: string };
type Turn = {
  q: string;
  a: string;
  tools: Tool[];
  blocked: string[];
  stats?: { turns: number; cost: number | null };
  error?: string;
};

type Finding = { level: string; what: string; fix: string | null };
type Health = { vault: string; ok: boolean; findings: Finding[] };

/**
 * Minimal renderer for the subset of Markdown the agent actually emits.
 *
 * Deliberately builds React nodes rather than HTML: notes are arbitrary files on
 * disk, and a vault that ever contains a stray <script> should render as text,
 * not run. No dangerouslySetInnerHTML anywhere means that class of bug cannot
 * exist here — worth more than complete Markdown coverage.
 */
function inline(text: string, vault: string, key: string) {
  const parts = text.split(/(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.filter(Boolean).map((p, i) => {
    const k = `${key}-${i}`;
    if (p.startsWith("[[") && p.endsWith("]]")) {
      const [target, label] = p.slice(2, -2).split("|");
      // Wikilinks are the whole point of citing by note name: clicking one opens
      // the actual note in Obsidian rather than a dead-end copy of its text.
      const href = `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(target)}`;
      return (
        <a key={k} className="wikilink" href={href} title={`Open ${target} in Obsidian`}>
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

export default function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API}/api/health`).then(r => r.json()).then(setHealth).catch(() => setHealth(null));
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const vaultName = health?.vault ? health.vault.split(/[\\/]/).pop() || "" : "";

  async function ask(question: string) {
    setBusy(true);
    setTurns(t => [...t, { q: question, a: "", tools: [], blocked: [] }]);
    const patch = (fn: (t: Turn) => Turn) =>
      setTurns(ts => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));

    try {
      const res = await fetch(`${API}/api/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, session_id: session }),
      });
      if (!res.body) throw new Error("no response body");

      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const chunks = buf.split("\n\n");
        buf = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const line = chunk.split("\n").find(l => l.startsWith("data: "));
          if (!line) continue;
          const e = JSON.parse(line.slice(6));
          if (e.type === "token") patch(t => ({ ...t, a: t.a + e.text }));
          else if (e.type === "tool")
            patch(t => ({ ...t, tools: [...t.tools, { name: e.name, detail: e.detail }] }));
          else if (e.type === "denied")
            patch(t => ({ ...t, blocked: [...t.blocked, e.message] }));
          else if (e.type === "error") patch(t => ({ ...t, error: e.message }));
          else if (e.type === "done") {
            setSession(e.session_id);
            patch(t => ({ ...t, stats: { turns: e.turns, cost: e.cost_usd } }));
          }
        }
      }
    } catch (err) {
      patch(t => ({ ...t, error: String(err) }));
    } finally {
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

  const alerts = health?.findings.filter(f => f.level !== "ok") ?? [];

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="sigma">Σ</span>
          <div>
            <h1>Sigma</h1>
            <p>{vaultName ? `asking ${vaultName}` : "connecting…"}</p>
          </div>
        </div>
        <div
          className={`health ${health ? (health.ok ? "ok" : "warn") : "down"}`}
          title={health ? alerts.map(a => a.what).join("\n") || "all clear" : "backend unreachable"}
        >
          <span className="dot" />
          {health ? (health.ok ? "all clear" : `${alerts.length} to review`) : "offline"}
        </div>
      </header>

      <main>
        {turns.length === 0 && (
          <div className="empty">
            <p>Ask your vault a question. Answers are grounded in notes it actually reads, and cite them.</p>
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
              <div key={j} className="blocked" title="This path is gitignored, so it is never sent to the model.">
                <span>🔒</span> {b}
              </div>
            ))}

            <div className="a">
              {t.a ? (
                <Markdown text={t.a} vault={vaultName} />
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

      <form onSubmit={submit}>
        <input
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder={busy ? "thinking…" : "Ask the vault…"}
          disabled={busy}
          autoFocus
        />
        <button type="submit" disabled={busy || !draft.trim()}>Ask</button>
      </form>
    </div>
  );
}
