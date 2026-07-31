/**
 * nosync.tsx — the Phase 5 audit view (dashboard-plan §6).
 *
 * "Everything that never leaves this machine", in one list. Nothing exclusive
 * lives here: every file it names also appears in Today, Projects or the brain
 * carrying the same bronze mark. What this view adds is the **total**, which no
 * inline marker can show, and which answers a question the rest of the
 * dashboard cannot: what exists on one disk only?
 *
 * The screen deliberately does *not* look sealed-off the way the original lane
 * would have. Option B opened the model boundary, so bronze no longer means
 * "Sigma is blind to this" — it means "git refuses this". A room that still
 * looked like a vault door would be teaching a boundary that no longer exists.
 */
import { useEffect, useState } from "react";
import { get, obsidianHref, rel } from "./api";
import type { NoSync } from "./api";

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function NoSyncView({ open, vault, onClose }: {
  open: boolean; vault: string; onClose: () => void;
}) {
  const [data, setData] = useState<NoSync | null | undefined>(undefined);

  useEffect(() => {
    if (!open) return;
    setData(undefined);
    get<NoSync>("nosync").then(setData).catch(() => setData(null));
  }, [open]);

  if (!open) return null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="nosync" onClick={e => e.stopPropagation()}
           role="dialog" aria-label="No-sync audit">
        <header className="nosync-head">
          <span className="label">⊘ NO-SYNC — NEVER LEAVES THIS MACHINE</span>
          <span className="dim">
            {data === undefined ? "reading…"
              : data === null ? "backend unreachable"
              : `${data.total} file${data.total === 1 ? "" : "s"} · ${bytes(data.bytes)}`}
          </span>
          <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
        </header>

        {/* The claim, stated once and plainly. It is the whole point of the
            view, so it is not a footnote. */}
        <p className="claim">
          These never leave this machine. <b>git refuses them</b>, so they have
          no off-machine backup — and Sigma <i>can</i> read them, since the model
          boundary opened on 2026-07-30. Two different boundaries; this is the
          one that is still shut.
        </p>

        {data === null && (
          <p className="err">Could not reach the backend — start it with <code>sigma ui</code>.</p>
        )}

        {/* An unverified boundary must never render as a reassuring empty list. */}
        {data && !data.ok && (
          <p className="err">
            ⚠ git could not answer, so nothing here is verified. This list is
            deliberately empty rather than guessed — a "never leaves this
            machine" claim is not one to make on a hunch.
          </p>
        )}

        {data && data.ok && data.total === 0 && (
          <p className="allclear">
            ✓ nothing is carved out — every note in the vault syncs and is backed up.
          </p>
        )}

        {data && data.total > 0 && (
          <>
            <ul className="groups">
              {data.groups.filter(g => g.count > 0).map(g => (
                <li key={g.prefix}>
                  <code>{g.prefix}</code>
                  <span className="dim">
                    {g.count} file{g.count === 1 ? "" : "s"} · {bytes(g.bytes)}
                    {g.newest ? ` · touched ${rel(g.newest)} ago` : ""}
                  </span>
                </li>
              ))}
            </ul>

            <ul className="files">
              {data.files.map(f => (
                <li key={f.path}>
                  <a href={obsidianHref(vault, f.path.replace(/\.md$/, ""))}
                     title={f.path}>{f.path}</a>
                  <span className="dim">
                    {f.bytes != null ? bytes(f.bytes) : "—"}
                    {f.mtime ? ` · ${rel(f.mtime)}` : ""}
                  </span>
                </li>
              ))}
            </ul>

            {data.truncated > 0 && (
              <p className="dim">
                +{data.truncated} more not listed — the view caps at 500 rows.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
