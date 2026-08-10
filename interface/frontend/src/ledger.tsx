/**
 * The activity ledger (dashboard-plan §7): the morning view. Everything Sigma
 * changed while you were away, one row per commit, each with [undo] — which is
 * `git revert` of exactly that commit, surfaced as a button. Ctrl+J.
 *
 * A reverted row stays visible, struck through, with its undo disabled: the
 * ledger is a record, not a todo list, and history does not get tidied away.
 *
 * Study S7: rows sharing an `extra.run` id collapse into one expandable entry
 * — a 19-module generation run is one line here, not nineteen. Grouping is
 * consecutive-only so the backend's newest-first order survives untouched,
 * and undo stays per-commit: the group is a fold, never a bigger lever.
 * Row/Run live at module scope on purpose — declared inside Ledger they were
 * new component identities every render, so each keystroke remounted the DOM
 * and dropped keyboard focus mid-operation.
 */
import { useEffect, useMemo, useState } from "react";
import { ApiError, get, obsidianHref, post, rel } from "./api";
import type { Activity, LedgerEntry } from "./api";

const GLYPH: Record<LedgerEntry["action"], string> = {
  create: "✚", update: "✎", toggle: "☑", append: "⊕", revert: "↩", skip: "⊘",
};

type RunGroup = { run: string; key: string; rows: LedgerEntry[] };
type Folded = LedgerEntry | RunGroup;

function foldRuns(entries: LedgerEntry[]): Folded[] {
  const out: Folded[] = [];
  for (const e of entries) {
    const run = e.extra?.run;
    const last = out[out.length - 1];
    if (run && last && "run" in last && last.run === run) {
      last.rows.push(e);
    } else if (run) {
      // The key carries the newest member's identity, not just the run id:
      // an interleaved non-run commit splits a run into two groups, and two
      // groups sharing one expansion key expanded and collapsed together.
      out.push({ run, key: `${run}:${e.sha ?? e.ts}`, rows: [e] });
    } else {
      out.push(e);
    }
  }
  // a "group" of one renders as a plain row — the fold has nothing to say
  return out.map(g => ("run" in g && g.rows.length === 1 ? g.rows[0] : g));
}

function Row({ e, nested, vault, busy, err, onUndo }: {
  e: LedgerEntry; nested?: boolean; vault: string;
  busy: string | null; err?: string; onUndo: (e: LedgerEntry) => void;
}) {
  return (
    <li className={`led-row ${e.reverted ? "reverted" : ""} led-${e.action}`
                   + (nested ? " led-nested" : "")}>
      <span className="led-when" title={e.ts}>{rel(e.ts)}</span>
      <span className="led-actor">{e.actor}</span>
      <span className="led-glyph" title={e.action}>{GLYPH[e.action] ?? "•"}</span>
      <span className="led-what">
        {e.target ? (
          <a href={obsidianHref(vault, e.target.replace(/\.md$/, ""))}
             title={`open ${e.target} in Obsidian`}>{e.target}</a>
        ) : <span className="dim">—</span>}
        <span className="led-summary"> · {e.summary}</span>
        {err && <span className="led-err"> {err}</span>}
      </span>
      {e.sha && e.action !== "revert" && !e.reverted ? (
        <button className="led-undo" disabled={busy === e.sha}
                title={`git revert ${e.sha.slice(0, 10)} — one commit, surgically`}
                onClick={() => onUndo(e)}>
          {busy === e.sha ? "…" : "undo"}
        </button>
      ) : (
        <span className="led-undo-slot dim" title={
          e.reverted ? "already reverted"
          : e.action === "revert" ? "a revert is not re-revertible from here"
          : "no commit exists for this change (untracked path)"}>
          {e.reverted ? "undone" : e.action === "revert" ? "" : "no commit"}
        </span>
      )}
    </li>
  );
}

function Run({ g, isOpen, onToggle, vault, busy, errs, onUndo }: {
  g: RunGroup; isOpen: boolean; onToggle: () => void; vault: string;
  busy: string | null; errs: Record<string, string>;
  onUndo: (e: LedgerEntry) => void;
}) {
  const newest = g.rows[0];
  const undone = g.rows.filter(r => r.reverted).length;
  return (
    <>
      <li className="led-row led-group">
        <span className="led-when" title={newest.ts}>{rel(newest.ts)}</span>
        <span className="led-actor">{newest.actor}</span>
        <span className="led-glyph" title="generation run">▣</span>
        <span className="led-what">
          <button className="led-run-toggle" aria-expanded={isOpen}
                  onClick={onToggle}>
            {isOpen ? "▾" : "▸"} {g.run}
          </button>
          <span className="led-summary">
            {" "}· {g.rows.length} commit{g.rows.length === 1 ? "" : "s"}
            {undone ? ` · ${undone} undone` : ""}
          </span>
        </span>
        <span className="led-undo-slot dim"
              title="expand the run — undo is per-commit, never a bigger lever">
          {isOpen ? "" : "…"}
        </span>
      </li>
      {isOpen && g.rows.map((e, i) => (
        <Row key={`${e.ts}-${e.sha ?? i}`} e={e} nested vault={vault}
             busy={busy} err={errs[e.sha ?? ""]} onUndo={onUndo} />
      ))}
    </>
  );
}

export default function Ledger({ open, vault, onClose, onMutate }: {
  open: boolean; vault: string; onClose: () => void; onMutate: () => void;
}) {
  // undefined = loading, null = the fetch failed (same convention as App)
  const [entries, setEntries] = useState<LedgerEntry[] | null | undefined>(undefined);
  const [busy, setBusy] = useState<string | null>(null);   // sha mid-revert
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [openRuns, setOpenRuns] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!open) return;
    setEntries(undefined);
    setErrs({});
    get<Activity>("activity").then(r => setEntries(r.entries)).catch(() => setEntries(null));
  }, [open]);

  const folded = useMemo(() => (entries ? foldRuns(entries) : []), [entries]);

  async function undo(e: LedgerEntry) {
    const sha = e.sha;            // captured: TS narrowing does not survive closures
    if (!sha || busy) return;
    setBusy(sha);
    setErrs(({ [sha]: _drop, ...rest }) => rest);
    try {
      await post("activity/revert", { sha });
      const r = await get<Activity>("activity");
      setEntries(r.entries);
      onMutate();                       // the panels are stale the moment it lands
    } catch (err) {
      const msg = err instanceof ApiError ? (err.detail || err.code) : "backend unreachable";
      setErrs(prev => ({ ...prev, [sha]: msg }));
    } finally {
      setBusy(null);
    }
  }

  if (!open) return null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="ledger" onClick={e => e.stopPropagation()}>
        <header className="ledger-head">
          <span className="label">ACTIVITY — EVERYTHING SIGMA CHANGED</span>
          <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
        </header>
        <ul className="ledger-rows">
          {entries === undefined && <li className="dim">loading…</li>}
          {entries === null && <li className="dim">ledger unavailable — is the backend up?</li>}
          {entries && entries.length === 0 && (
            <li className="dim">nothing yet — the first autonomous change lands here,
              with its undo button</li>
          )}
          {folded.map((g, i) =>
            "run" in g
              ? <Run key={g.key} g={g} isOpen={openRuns.has(g.key)}
                     onToggle={() => setOpenRuns(prev => {
                       const next = new Set(prev);
                       if (next.has(g.key)) next.delete(g.key); else next.add(g.key);
                       return next;
                     })}
                     vault={vault} busy={busy} errs={errs} onUndo={undo} />
              : <Row key={`${g.ts}-${g.sha ?? i}`} e={g} vault={vault}
                     busy={busy} err={errs[g.sha ?? ""]} onUndo={undo} />
          )}
        </ul>
        <div className="palette-foot dim">
          each row is one commit · undo is git revert of that commit alone · Esc close
        </div>
      </div>
    </div>
  );
}
