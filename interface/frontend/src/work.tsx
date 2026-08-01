/**
 * work.tsx — the four priority queues, on the rail's WK slot.
 *
 * The Today panel bucketed tasks by day and discarded everything without a 📅,
 * which is most of the vault's real work. This replaces that with four queues —
 * Courses, ProCertus, Projects, Misc — sharing one scoring function, each
 * showing a small window of its highest-scoring *eligible* task. Completing one
 * pops it and promotes the next; there is no daily rebuild.
 *
 * The chips, the score breakdown and the completion flow live in queue-bits.ts
 * because the QUEUE digest in the right column renders the same queue smaller
 * and must say the same things about it.
 *
 * No per-card icon, deliberately. Every other panel in this dashboard is titled
 * by a bare word in the `.panel > h2` treatment, and the one time chrome reached
 * for a decorative glyph it collided with font fallback (see NoSyncMark). The
 * rail's ◇ is reused as "a place in the dashboard", which is what these are.
 */
import { useEffect, useState } from "react";
import { get, obsidianHref, QUEUE_ORDER } from "./api";
import type { Queue, QueueSection, QueueTask } from "./api";
import { NoSyncMark } from "./panels";
import { breakdown, chipsFor, progressOf, useFreshIds, useQueueTick } from "./queue-bits";
import type { RowState } from "./queue-bits";

function Row({ t, s, vault, rows, errs, fresh, onTick }: {
  t: QueueTask; s: QueueSection; vault: string;
  rows: RowState; errs: Record<string, string>; fresh: Set<string>;
  onTick: (t: QueueTask) => void;
}) {
  const st = rows[t.id];
  const sub = s.kind === "chain" ? progressOf(s, t) : null;
  return (
    <li className={`q-row ${t.overdue ? "overdue" : ""} ${st ?? ""}`
                   + (fresh.has(t.id) ? " promoted" : "")}>
      <button className={`q-box ${st ?? ""}`} disabled={!!st} onClick={() => onTick(t)}
              title={st === "leaving"
                ? "ticked — one commit of its own, revertible in the ledger (Ctrl+J)"
                : "tick it — writes to the note as its own revertible commit"}>
        {st === "leaving" ? "☑" : st === "busy" ? "◌" : "☐"}
      </button>
      <a className="q-main" href={obsidianHref(vault, t.file.replace(/\.md$/, ""))}
         title={`${t.file}:${t.line}\n${breakdown(t)}`}>
        <span className="q-text">
          {t.pinned && <span className="q-pin" title="pinned">📌</span>}
          {t.no_sync && <NoSyncMark />}
          {t.parent && s.kind === "chain" && <b className="q-parent">{t.parent}</b>}
          {t.text}
        </span>
        {sub && <span className="q-sub">{sub}</span>}
        {errs[t.id] && <span className="row-err">{errs[t.id]}</span>}
      </a>
      <span className="q-chips">
        {chipsFor(t).map(c => (
          <em key={c.label} className={`q-chip ${c.tone}`}>{c.label}</em>
        ))}
      </span>
    </li>
  );
}

function Card({ s, vault, rows, errs, fresh, onTick }: {
  s: QueueSection; vault: string;
  rows: RowState; errs: Record<string, string>; fresh: Set<string>;
  onTick: (t: QueueTask) => void;
}) {
  const tail: string[] = [];
  if (s.queue.length) tail.push(`${s.queue.length} queued`);
  if (s.blocked.length) tail.push(`${s.blocked.length} blocked`);
  if (s.snoozed.length) tail.push(`${s.snoozed.length} snoozed`);
  if (s.archived.length) tail.push(`${s.archived.length} archived`);
  // "2 of 5 courses" rather than "top 5": the number counts parents, not tasks,
  // and it puts the silent ones on screen — three active courses have no
  // timeline and contribute nothing, which "one per course" would hide.
  const head = s.parent_noun
    ? `${s.visible.length} of ${s.window} ${s.parent_noun}s`
    : `top ${s.window}`;

  return (
    <section className="panel q-card">
      <h2>
        ◇ {s.title.toUpperCase()}
        <span className="q-head">{[head, ...tail].join(" · ")}</span>
      </h2>
      {s.visible.length === 0 ? (
        <p className="dim q-empty">
          {s.blocked.length || s.queue.length
            ? "nothing eligible — everything here is blocked or suppressed"
            : "nothing queued"}
        </p>
      ) : (
        <ul className="rows q-rows">
          {s.visible.map(t => (
            <Row key={t.id} t={t} s={s} vault={vault}
                 rows={rows} errs={errs} fresh={fresh} onTick={onTick} />
          ))}
        </ul>
      )}
    </section>
  );
}

export default function WorkView({ open, vault, onClose, onMutate }: {
  open: boolean; vault: string; onClose: () => void; onMutate: () => void;
}) {
  const [q, setQ] = useState<Queue | null | undefined>(undefined);

  const pull = () => get<Queue>("queue").then(setQ).catch(() => setQ(null));

  useEffect(() => {
    if (!open) return;
    setQ(undefined);
    pull();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // A completion changes both this view and the shell's digest, so the refetch
  // is both: our own payload, and the panels App owns.
  const { rows, errs, tick } = useQueueTick(() => { pull(); onMutate(); });
  const visibleIds = q
    ? QUEUE_ORDER.flatMap(k => q.sections[k].visible.map(t => t.id))
    : [];
  const fresh = useFreshIds(visibleIds);

  if (!open) return null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="work" onClick={e => e.stopPropagation()}
           role="dialog" aria-label="Work">
        <header className="work-head">
          <span className="label">◇ WORK — THE QUEUES</span>
          {q && (
            <span className="work-counts">
              {q.counts.visible} visible · {q.counts.queued} queued
              {q.counts.blocked ? ` · ${q.counts.blocked} blocked` : ""}
            </span>
          )}
          <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
        </header>

        {q === undefined && <p className="dim pad">reading…</p>}
        {q === null && <p className="err pad">backend unreachable</p>}

        {q && !q.index_ok && (
          // Never swallowed: the ages on screen are whatever the last good
          // index held, and nothing is being written until it is repaired.
          <p className="err pad">
            the task index did not parse — ages may be stale and nothing is being
            recorded. Delete <code>runtime/todo.state.json</code> to re-adopt.
          </p>
        )}

        {q && (
          <div className="work-grid">
            {QUEUE_ORDER.map(k => (
              <Card key={k} s={q.sections[k]} vault={vault}
                    rows={rows} errs={errs} fresh={fresh} onTick={tick} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
