/**
 * The activity ledger (dashboard-plan §7): the morning view. Everything Sigma
 * changed while you were away, one row per commit, each with [undo] — which is
 * `git revert` of exactly that commit, surfaced as a button. Ctrl+J.
 *
 * A reverted row stays visible, struck through, with its undo disabled: the
 * ledger is a record, not a todo list, and history does not get tidied away.
 */
import { useEffect, useState } from "react";
import { ApiError, get, obsidianHref, post, rel } from "./api";
import type { Activity, LedgerEntry } from "./api";

const GLYPH: Record<LedgerEntry["action"], string> = {
  create: "✚", update: "✎", toggle: "☑", append: "⊕", revert: "↩",
};

export default function Ledger({ open, vault, onClose, onMutate }: {
  open: boolean; vault: string; onClose: () => void; onMutate: () => void;
}) {
  // undefined = loading, null = the fetch failed (same convention as App)
  const [entries, setEntries] = useState<LedgerEntry[] | null | undefined>(undefined);
  const [busy, setBusy] = useState<string | null>(null);   // sha mid-revert
  const [errs, setErrs] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!open) return;
    setEntries(undefined);
    setErrs({});
    get<Activity>("activity").then(r => setEntries(r.entries)).catch(() => setEntries(null));
  }, [open]);

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
          {entries?.map((e, i) => (
            <li key={`${e.ts}-${e.sha ?? i}`}
                className={`led-row ${e.reverted ? "reverted" : ""} led-${e.action}`}>
              <span className="led-when" title={e.ts}>{rel(e.ts)}</span>
              <span className="led-actor">{e.actor}</span>
              <span className="led-glyph" title={e.action}>{GLYPH[e.action] ?? "•"}</span>
              <span className="led-what">
                {e.target ? (
                  <a href={obsidianHref(vault, e.target.replace(/\.md$/, ""))}
                     title={`open ${e.target} in Obsidian`}>{e.target}</a>
                ) : <span className="dim">—</span>}
                <span className="led-summary"> · {e.summary}</span>
                {errs[e.sha ?? ""] && <span className="led-err"> {errs[e.sha ?? ""]}</span>}
              </span>
              {e.sha && e.action !== "revert" && !e.reverted ? (
                <button className="led-undo" disabled={busy === e.sha}
                        title={`git revert ${e.sha.slice(0, 10)} — one commit, surgically`}
                        onClick={() => undo(e)}>
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
          ))}
        </ul>
        <div className="palette-foot dim">
          each row is one commit · undo is git revert of that commit alone · Esc close
        </div>
      </div>
    </div>
  );
}
