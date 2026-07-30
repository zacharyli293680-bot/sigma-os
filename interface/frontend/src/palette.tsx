/**
 * The command palette (dashboard-plan D3): the `sigma` verbs, one keystroke
 * away. Renders from GET /api/commands — the server's own whitelist — so the
 * UI cannot offer anything the backend would refuse. Disabled verbs are shown
 * greyed with their reason (disabled beats hidden, as with the sealed lane).
 *
 * Launching closes the palette; the output belongs to the activity dock,
 * which streams the job's lines as they arrive.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { API, get } from "./api";
import type { CommandInfo } from "./api";

/** Subsequence fuzzy match: every query char must appear in order; earlier
 *  and denser matches score higher. Zero deps, good enough for 17 verbs. */
function score(query: string, text: string): number {
  if (!query) return 1;
  const q = query.toLowerCase(), t = text.toLowerCase();
  let qi = 0, s = 0, last = -1;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      s += last === ti - 1 ? 3 : 1;      // consecutive hits beat scattered ones
      last = ti;
      qi++;
    }
  }
  return qi === q.length ? s / (t.length * 0.1 + 1) : 0;
}

export default function Palette({ open, onClose, onLaunched }: {
  open: boolean; onClose: () => void; onLaunched: () => void;
}) {
  const [verbs, setVerbs] = useState<CommandInfo[] | null>(null);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open && !verbs) {
      get<{ verbs: CommandInfo[] }>("commands").then(r => setVerbs(r.verbs))
        .catch(() => setVerbs(null));
    }
    if (open) {
      setQuery("");
      setSel(0);
      setNotice(null);
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open, verbs]);

  const rows = useMemo(() => {
    if (!verbs) return [];
    return verbs
      .map(v => ({ v, s: score(query, `${v.title} ${v.verb}`) }))
      .filter(r => r.s > 0)
      .sort((a, b) => (b.v.enabled ? b.s : b.s - 100) - (a.v.enabled ? a.s : a.s - 100))
      .map(r => r.v);
  }, [verbs, query]);

  useEffect(() => { if (sel >= rows.length) setSel(0); }, [rows, sel]);

  async function run(v: CommandInfo) {
    if (!v.enabled) return;
    try {
      const r = await fetch(`${API}/api/commands/${v.verb}`, { method: "POST" });
      if (r.status === 409) {
        const body = await r.json();
        setNotice(`busy — ${body.running} is still running`);
        return;
      }
      if (!r.ok) { setNotice(`refused (${r.status})`); return; }
      onLaunched();
      onClose();
    } catch {
      setNotice("backend unreachable");
    }
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel(s => Math.min(s + 1, rows.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel(s => Math.max(s - 1, 0)); }
    else if (e.key === "Enter" && rows[sel]) { e.preventDefault(); run(rows[sel]); }
  }

  if (!open) return null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={e => e.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          onChange={e => { setQuery(e.target.value); setSel(0); }}
          onKeyDown={onKey}
          placeholder="run a sigma verb…"
          spellCheck={false}
        />
        {notice && <div className="palette-notice">{notice}</div>}
        <ul>
          {rows.map((v, i) => (
            <li key={v.verb}
                className={`${i === sel ? "sel" : ""} ${v.enabled ? "" : "off"}`}
                onMouseEnter={() => setSel(i)}
                onClick={() => run(v)}
                title={v.enabled ? v.hint : v.hint}>
              <span className="p-title">{v.title}</span>
              <span className="p-meta">
                {v.writes && <em className="p-badge writes">writes</em>}
                {v.model && <em className="p-badge model">model</em>}
                {!v.enabled && <em className="p-badge off-badge">unavailable</em>}
                <code>{v.verb}</code>
              </span>
            </li>
          ))}
          {verbs && rows.length === 0 && <li className="off"><span className="p-title">no match</span></li>}
          {!verbs && <li className="off"><span className="p-title">loading…</span></li>}
        </ul>
        <div className="palette-foot dim">
          ↑↓ choose · Enter run · Esc close — output streams into the dock
        </div>
      </div>
    </div>
  );
}
