/**
 * work.tsx — the four priority queues, on the rail's WK slot.
 *
 * The Today panel bucketed tasks by day and discarded everything without a 📅,
 * which is most of the vault's real work. This replaces that with four queues —
 * Courses, ProCertus, Projects, Misc — sharing one scoring function, each
 * showing a small window of its highest-scoring *eligible* task. Completing one
 * pops it and promotes the next; there is no daily rebuild.
 *
 * Two house rules are load-bearing here:
 *
 * - **Status is never colour alone.** Every chip pairs a word with its tone —
 *   `overdue 3d`, `due Fri`, `aging 9d`, `high`. A red pill that only means
 *   something if you know the legend is not a status.
 * - **The ordering must be interrogable.** Every row's tooltip carries the
 *   score arithmetic that put it there (`due Fri +33 · high +30 · age +2 = 65`).
 *   A priority list nobody can question is one nobody trusts, and the breakdown
 *   also says which constant to turn when the order looks wrong.
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

type Chip = { label: string; tone: string };

const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Deadline, aging and urgency, as words that carry their own meaning. */
function chipsFor(t: QueueTask): Chip[] {
  const out: Chip[] = [];
  const d = t.parts.days_until;
  if (t.deadline && d !== null) {
    if (d < 0) out.push({ label: `overdue ${-d}d`, tone: "danger" });
    else if (d === 0) out.push({ label: "due today", tone: "danger" });
    else if (d === 1) out.push({ label: "due tomorrow", tone: "warn" });
    else if (d <= 6) {
      // Date-only parsing, so this is a local calendar day rather than UTC —
      // `new Date("2026-08-07")` is midnight UTC and lands on the 6th in any
      // negative offset, which would print the wrong weekday half the year.
      const [y, m, day] = t.deadline.split("-").map(Number);
      out.push({ label: `due ${WEEKDAY[new Date(y, m - 1, day).getDay()]}`, tone: "warn" });
    } else out.push({ label: `due ${t.deadline.slice(5)}`, tone: "dim" });
  }
  // The anti-starvation term, surfaced: this is *why* a long-ignored task with
  // no deadline eventually climbs into view.
  if (t.parts.aging >= 1) {
    out.push({ label: `aging ${t.parts.age_days}d`, tone: "age" });
  }
  if (t.urgency === "high") out.push({ label: "high", tone: "urg" });
  else if (t.urgency === "low") out.push({ label: "low", tone: "dim" });
  return out;
}

/** The tooltip that answers "why is this here?". Local until the digest panel
 *  needs it too, at which point it and chipsFor belong in their own module —
 *  exporting them from here trips the fast-refresh rule for no present gain. */
function breakdown(t: QueueTask): string {
  const p = t.parts;
  const bits: string[] = [];
  if (p.deadline) {
    const d = p.days_until;
    bits.push(`${d !== null && d < 0 ? "overdue" : "due"} +${Math.round(p.deadline)}`);
  }
  bits.push(`${t.urgency} +${Math.round(p.urgency)}`);
  if (p.aging) bits.push(`age +${Math.round(p.aging)}`);
  const math = `${bits.join(" · ")} = ${Math.round(p.total)}`;
  return t.pinned ? `pinned — shown regardless of score\n(${math})` : math;
}

/** "Block 3 of 10 · next: strong induction notes" for a course frontier. */
function progressOf(s: QueueSection, t: QueueTask): string | null {
  const g = s.groups.find(x => x.parent === t.parent);
  if (!g) return null;
  if (s.key === "projects") {
    const behind = g.open - 1;
    return behind > 0 ? `${behind} queued behind` : null;
  }
  const headings: string[] = [];
  for (const c of g.chain) {
    if (c.heading && headings[headings.length - 1] !== c.heading) headings.push(c.heading);
  }
  const at = t.heading ? headings.indexOf(t.heading) + 1 : 0;
  // The heading is a full block title ("Block 3 — Distributed Loads"); the
  // leading label alone is what fits on a sub-line.
  const label = (t.heading ?? "").split("—")[0].trim() || `${at}`;
  const next = g.chain[1];
  const where = at && headings.length ? `${label} of ${headings.length}` : label;
  if (!next) return where || null;
  // Elide rather than cut: "…examples; packet" reads as a finished phrase and
  // silently misstates what the next task is.
  const peek = next.text.length > 46 ? `${next.text.slice(0, 45).trimEnd()}…` : next.text;
  return `${where} · next: ${peek}`;
}

function Row({ t, s, vault }: { t: QueueTask; s: QueueSection; vault: string }) {
  const sub = s.kind === "chain" ? progressOf(s, t) : null;
  return (
    <li className={`q-row ${t.overdue ? "overdue" : ""}`}>
      {/* Inert until the completion slice — the box is here so the layout is
          the real one, not a sketch of it. */}
      <span className="q-box" aria-hidden="true">☐</span>
      <a className="q-main" href={obsidianHref(vault, t.file.replace(/\.md$/, ""))}
         title={`${t.file}:${t.line}\n${breakdown(t)}`}>
        <span className="q-text">
          {t.pinned && <span className="q-pin" title="pinned">📌</span>}
          {t.no_sync && <NoSyncMark />}
          {t.parent && s.kind === "chain" && <b className="q-parent">{t.parent}</b>}
          {t.text}
        </span>
        {sub && <span className="q-sub">{sub}</span>}
      </a>
      <span className="q-chips">
        {chipsFor(t).map(c => (
          <em key={c.label} className={`q-chip ${c.tone}`}>{c.label}</em>
        ))}
      </span>
    </li>
  );
}

function Card({ s, vault }: { s: QueueSection; vault: string }) {
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
          {s.visible.map(t => <Row key={t.id} t={t} s={s} vault={vault} />)}
        </ul>
      )}
    </section>
  );
}

export default function WorkView({ open, vault, onClose }: {
  open: boolean; vault: string; onClose: () => void;
}) {
  const [q, setQ] = useState<Queue | null | undefined>(undefined);

  useEffect(() => {
    if (!open) return;
    setQ(undefined);
    get<Queue>("queue").then(setQ).catch(() => setQ(null));
  }, [open]);

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
              <Card key={k} s={q.sections[k]} vault={vault} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
