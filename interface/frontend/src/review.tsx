/**
 * review.tsx — read a proposal's diff and decide, without leaving the dashboard.
 *
 * The loop this closes: the coach and the auditor exist to correct *existing*
 * notes, and a correction to an existing note is staged rather than applied, so
 * reviewing their output meant a terminal and `reflect.py --diff`. Phase 3
 * shipped `reflect merge` as a disabled palette entry reading "waiting on Phase
 * 4's diff UI"; Phase 4 deferred it. This is that UI.
 *
 * The buttons deliberately do not hide what the system will actually do. A
 * change to a note that already exists *stages* rather than applies — that is
 * `applier.py`'s rule, not this component's — so the header says so before you
 * press anything, and whatever the server answers is shown verbatim rather than
 * flattened into "done".
 */
import { useEffect, useState } from "react";
import { ApiError, get, obsidianHref, post } from "./api";
import type { ProposalDetail } from "./api";

function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) {
    return <p className="dim">No content block to diff — this proposal writes nothing.</p>;
  }
  return (
    <pre className="diff">
      {diff.split("\n").map((line, i) => {
        const cls = line.startsWith("+++") || line.startsWith("---") ? "meta"
          : line.startsWith("@@") ? "hunk"
          : line.startsWith("+") ? "add"
          : line.startsWith("-") ? "del" : "";
        return <span key={i} className={cls}>{line || " "}{"\n"}</span>;
      })}
    </pre>
  );
}

export default function Review({ name, vault, onClose, onMutate }: {
  name: string | null; vault: string; onClose: () => void; onMutate: () => void;
}) {
  const [d, setD] = useState<ProposalDetail | null | undefined>(undefined);
  const [busy, setBusy] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  useEffect(() => {
    if (!name) return;
    setD(undefined); setSaid(null); setBusy(null);
    get<ProposalDetail>(`proposals/${encodeURIComponent(name)}`)
      .then(setD).catch(() => setD(null));
  }, [name]);

  if (!name) return null;

  async function act(action: string) {
    if (busy) return;
    setBusy(action); setSaid(null);
    try {
      const r = await post<{ result?: string; detail?: string; status?: string }>(
        `proposals/${encodeURIComponent(name!)}/decide`, { action });
      // Say what actually happened, not what was asked for: "apply" on an
      // existing note comes back as "held", and pretending otherwise is how a
      // UI teaches you the wrong model of its own system.
      setSaid(r.result ? `${action} → ${r.result}${r.detail ? ` · ${r.detail}` : ""}`
        : r.status ? `now ${r.status}`
        : r.detail || `${action} ok`);
      onMutate();
      get<ProposalDetail>(`proposals/${encodeURIComponent(name!)}`)
        .then(setD).catch(() => {});
    } catch (e) {
      setSaid(e instanceof ApiError ? (e.detail || e.code) : "backend unreachable");
    } finally {
      setBusy(null);
    }
  }

  const pending = d?.status === "pending";

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="review" onClick={e => e.stopPropagation()}
           role="dialog" aria-label="Review proposal">
        <header className="review-head">
          <span className="label">REVIEW — {d?.title ?? name}</span>
          <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
        </header>

        {d === undefined && <p className="dim pad">reading…</p>}
        {d === null && <p className="err pad">could not load this proposal</p>}

        {d && (
          <>
            <div className="review-meta">
              <span className={`badge ${d.status ?? ""}`}>{d.status ?? "?"}</span>
              <span className="dim">{d.kind ?? "?"} · risk {d.risk ?? "?"}</span>
              {d.target && (
                <a href={obsidianHref(vault, d.target.replace(/\.md$/, ""))}
                   title="open the target note">{d.target}</a>
              )}
            </div>

            {/* Said before you press anything, because it changes what the
                buttons mean. */}
            <p className={`review-note ${d.would_stage ? "warn" : ""}`}>
              {d.would_stage
                ? "This note already exists, so applying stages the change into 06-System/proposed/ rather than overwriting it. Merge is the deliberate second step."
                : "This note does not exist yet, so applying creates it as its own revertible commit."}
            </p>

            <DiffView diff={d.diff} />

            {said && <p className="review-said">{said}</p>}

            <footer className="review-actions">
              <button onClick={() => act("approve")} disabled={!!busy || !pending}
                      title={pending ? "set status: approved" : "already decided"}>
                {busy === "approve" ? "…" : "approve"}
              </button>
              <button onClick={() => act("reject")} disabled={!!busy || d.status === "rejected"}>
                {busy === "reject" ? "…" : "reject"}
              </button>
              <button onClick={() => act("apply")} disabled={!!busy}
                      title="applier.py decides create vs stage">
                {busy === "apply" ? "…" : d.would_stage ? "stage" : "apply"}
              </button>
              <button onClick={() => act("merge")} disabled={!!busy || !d.would_stage}
                      className="danger"
                      title="overwrite the target with the staged version — the one place Sigma overwrites a note">
                {busy === "merge" ? "…" : "merge"}
              </button>
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
