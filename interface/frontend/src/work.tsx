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
import { useEffect, useRef, useState } from "react";
import { ApiError, get, obsidianHref, post, QUEUE_ORDER } from "./api";
import type { Queue, QueueAdd, QueueEdit, QueueSection, QueueTask, Reword,
              RewordResp, ReviewResp, ReviewRow } from "./api";
import { NoSyncMark } from "./panels";
import { breakdown, chipsFor, progressOf, useFreshIds, useQueueTick } from "./queue-bits";
import type { RowState } from "./queue-bits";

const SECTION_LABEL: Record<string, string> = {
  courses: "Courses", procertus: "ProCertus", projects: "Projects", misc: "Misc",
};

/**
 * Quick-add. Free text in, a real checkbox in a real note out.
 *
 * `auto` is the default section because the guess is usually right and naming
 * one every time is friction on the thing that must have none. When it is
 * wrong the selector is right there, and the confirmation names the file it
 * landed in — as an Obsidian link, so being wrong costs one click either way.
 *
 * Nothing here waits on a model. The AI reword arrives as a passive suggestion
 * *after* the task is already saved; a task that cannot be filed while the
 * window is rate-limited is not a capture tool.
 */
function QuickAdd({ vault, sections, onAdded, inputRef }: {
  vault: string; sections: Record<string, QueueSection>; onAdded: () => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const [urgency, setUrgency] = useState("medium");
  const [section, setSection] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<QueueAdd | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // What the reword is *about*: the task as filed. Held separately from the
  // input so typing the next task never applies a suggestion to the wrong one.
  const [subject, setSubject] = useState<{ add: QueueAdd; typed: string } | null>(null);
  const [sugg, setSugg] = useState<Reword | null | "waiting">(null);

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true); setErr(null); setSaid(null); setSugg(null); setSubject(null);
    try {
      // "courses:CSE-311" — the picker names a destination, not just a queue.
      // A bare section here was the same defect the move menu had: Courses with
      // no course has no file of its own, so the backend fell through to Misc
      // and titled the note it created "None — Tasks".
      const [sec, par] = section === "auto" ? [null, null] : section.split(":");
      const r = await post<QueueAdd>("queue/add", {
        text: t,
        due: due || null,
        urgency,
        section: sec,
        parent: par ?? null,
      });
      setSaid(r);
      setText(""); setDue("");     // section and urgency persist: adding three
      setBusy(false);              // ProCertus tasks should not mean setting it
      onAdded();                   // three times
      inputRef.current?.focus();
      askReword(r, t);             // deliberately not awaited
    } catch (e2) {
      setBusy(false);
      setErr(e2 instanceof ApiError ? (e2.detail || e2.code) : "backend unreachable");
    }
  }

  /** Fires after the task is already saved, and is allowed to fail quietly. */
  async function askReword(add: QueueAdd, typed: string) {
    setSubject({ add, typed });
    setSugg("waiting");
    try {
      const r = await post<RewordResp>("queue/reword", { text: typed });
      setSugg(r.suggestion);
    } catch {
      // Refused, timed out, or the window is spent. The task is filed either
      // way, so this is not something to interrupt anyone about.
      setSugg(null);
    }
  }

  async function accept() {
    if (!subject || !sugg || sugg === "waiting") return;
    const s = sugg;
    const line = await findLine(subject.add);
    if (line === null) { setSugg(null); return; }
    try {
      await post<QueueEdit>("queue/edit", {
        file: subject.add.file, line, raw: subject.add.raw,
        text: s.title, due: s.due, urgency: s.urgency,
        section: s.section, parent: s.parent,
        raw_input: subject.typed,
      });
      onAdded();
    } catch (e) {
      setErr(e instanceof ApiError ? (e.detail || e.code) : "backend unreachable");
    }
    setSugg(null); setSubject(null);
  }

  /** The add endpoint reports the file, not the offset; the edit needs both.
   *  Asking the queue is cheaper and more honest than making the write path
   *  return a line number that a concurrent pull could already have moved. */
  async function findLine(add: QueueAdd): Promise<number | null> {
    try {
      const q = await get<Queue>("queue");
      for (const k of QUEUE_ORDER) {
        const s = q.sections[k];
        for (const t of [...s.visible, ...s.queue, ...s.blocked, ...s.snoozed]) {
          if (t.file === add.file && t.raw === add.raw) return t.line;
        }
      }
    } catch { /* fall through */ }
    return null;
  }

  // Narrowed once, so the JSX below reads as one condition rather than three.
  // A suggestion identical to what was typed is not worth a strip: the point is
  // to offer a change, and "we agree" is noise wearing the costume of a result.
  const shown = subject && sugg && sugg !== "waiting" ? sugg : null;
  const changed = !!shown && !!subject && (
    shown.title !== subject.typed || !!shown.due || shown.urgency !== "medium"
    || shown.section !== subject.add.section);

  return (
    <form className="q-add" onSubmit={submit}>
      <input ref={inputRef} className="q-add-text" value={text} placeholder="add a task…"
             onChange={e => { setText(e.target.value); setSaid(null); }} />
      <input className="q-add-date" type="date" value={due} title="due date (optional)"
             onChange={e => setDue(e.target.value)} />
      <select className="q-add-sel" value={urgency} title="urgency"
              onChange={e => setUrgency(e.target.value)}>
        <option value="high">high</option>
        <option value="medium">medium</option>
        <option value="low">low</option>
      </select>
      <select className="q-add-sel" value={section} title="section"
              onChange={e => setSection(e.target.value)}>
        <option value="auto">auto</option>
        {QUEUE_ORDER.flatMap(k => {
          const s = sections[k];
          return s?.kind === "chain"
            ? (s.parents ?? []).map(p => (
                <option key={`${k}:${p.key}`} value={`${k}:${p.key}`}
                        title={p.label !== p.key ? p.label : undefined}>
                  {SECTION_LABEL[k]} — {p.key}
                </option>
              ))
            : [<option key={k} value={k}>{SECTION_LABEL[k]}</option>];
        })}
      </select>
      <button className="q-add-go" disabled={busy || !text.trim()}>
        {busy ? "…" : "Add"}
      </button>
      {err && <span className="q-add-said err">{err}</span>}
      {said && !err && (
        <span className="q-add-said">
          ✓ added to {SECTION_LABEL[said.section] ?? said.section} ·{" "}
          <a href={obsidianHref(vault, said.file.replace(/\.md$/, ""))}
             title={said.file}>{said.file.split("/").pop()}</a>
          {said.created_note && " (new note)"}
          {sugg === "waiting" && <span className="q-sugg-wait"> · ◌ tidying…</span>}
        </span>
      )}
      {changed && shown && subject && (
        <div className="q-sugg">
          <span className="q-sugg-mark">✦</span>
          <span className="q-sugg-body">
            <b>{shown.title}</b>
            <span className="q-sugg-meta">
              {shown.section !== subject.add.section
                && ` · move to ${SECTION_LABEL[shown.section] ?? shown.section}`}
              {shown.parent && ` · ${shown.parent}`}
              {shown.due && ` · 📅 ${shown.due}`}
              {shown.urgency !== "medium" && ` · ${shown.urgency}`}
            </span>
          </span>
          <button type="button" className="q-sugg-go" onClick={accept}>Accept</button>
          <button type="button" className="ghost" onClick={() => setSugg(null)}
                  title="keep what I typed">✕</button>
        </div>
      )}
    </form>
  );
}

function iso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  // Local calendar arithmetic, then a local ISO string — toISOString() is UTC
  // and would hand back yesterday for anyone west of Greenwich.
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-`
       + `${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * Snooze, pin, archive, move.
 *
 * The first three are index-only and instant. Move is a real commit, because
 * which queue a task is in *is* where its note lives — an override that left
 * the task in the wrong file would give the note and the sidecar two different
 * answers to one question.
 */
function RowMenu({ t, at, sections, onMeta, onMove, onClose }: {
  t: QueueTask; at: DOMRect;
  sections: Record<string, QueueSection>;
  onMeta: (body: Record<string, unknown>) => void;
  onMove: (section: string, parent: string | null) => void;
  onClose: () => void;
}) {
  // Positioned against the viewport rather than the row. The cards live in a
  // scrolling grid, and an absolutely-positioned menu was clipped by it — the
  // bottom third of the list simply vanished. `fixed` escapes the overflow, and
  // measuring lets it open upward when it would otherwise run off the screen.
  //
  // The height is measured rather than assumed. A flat 250px was close enough
  // while "move to" was four fixed rows, but chain sections now expand to one
  // row per course and project, so the menu can be twice that — and flipping
  // upward only moves an over-tall menu off the *other* edge. Two changes fix
  // it together: open toward whichever side has more room, and cap the height
  // to the room that side actually has, so the remainder scrolls (see .q-menu).
  const GUTTER = 8;        // never let it touch the viewport edge
  const WANTED = 280;      // enough that a short menu never flips for no reason
  const below = window.innerHeight - at.bottom - GUTTER;
  const above = at.top - GUTTER;
  const up = below < WANTED && above > below;
  // The floor matters: a row near the bottom of a short window can leave ~30px
  // below, and a 30px-tall scroller is worse than one that overhangs slightly.
  const room = Math.max(160, up ? above : below);
  const style: React.CSSProperties = {
    position: "fixed", right: window.innerWidth - at.right,
    maxHeight: room,
    ...(up ? { bottom: window.innerHeight - at.top + 2 } : { top: at.bottom + 2 }),
  };
  return (
    <>
      <div className="q-menu-scrim" onClick={onClose} />
      <div className="q-menu" role="menu" style={style}>
        <p className="q-menu-head">snooze</p>
        <button onClick={() => onMeta({ snooze: iso(1) })}>tomorrow</button>
        <button onClick={() => onMeta({ snooze: iso(7) })}>next week</button>
        <label className="q-menu-pick">
          pick…
          <input type="date" onChange={e => e.target.value
            && onMeta({ snooze: e.target.value })} />
        </label>
        <p className="q-menu-head">this task</p>
        <button onClick={() => onMeta({ pin: !t.pinned })}>
          {t.pinned ? "unpin" : "pin — always show"}
        </button>
        <button onClick={() => onMeta({ archive: true })}>archive</button>
        <p className="q-menu-head">move to</p>
        {/* A per-parent section is not a destination by itself — "Courses" does
            not name a file, CSE-311 does. Offering the section alone is what
            made this silently misfile: the request carried no parent, the
            backend fell through to Misc, and a task already in Misc did not
            move at all. So chain sections expand to their actual parents, and
            the current parent is filtered out rather than the whole section. */}
        {QUEUE_ORDER.flatMap(k => {
          const s = sections[k];
          if (s?.kind === "chain") {
            return (s.parents ?? [])
              .filter(p => !(k === t.section && p.key === t.parent))
              .map(p => (
                // The key, not the label: every task row identifies a course by
                // code (AA-210), so a menu offering "Engineering Statics" asks
                // you to translate. Projects already key on their own stem. The
                // human name survives as the tooltip where the two differ.
                <button key={`${k}:${p.key}`} onClick={() => onMove(k, p.key)}
                        title={p.label !== p.key ? p.label : undefined}>
                  {p.key}
                  <span className="q-menu-sub">{SECTION_LABEL[k]}</span>
                </button>
              ));
          }
          return k === t.section ? [] : [
            <button key={k} onClick={() => onMove(k, null)}>{SECTION_LABEL[k]}</button>,
          ];
        })}
      </div>
    </>
  );
}

function Row({ t, s, sections, vault, rows, errs, fresh, onTick, onMeta, onMove }: {
  t: QueueTask; s: QueueSection; sections: Record<string, QueueSection>;
  vault: string;
  rows: RowState; errs: Record<string, string>; fresh: Set<string>;
  onTick: (t: QueueTask) => void;
  onMeta: (t: QueueTask, body: Record<string, unknown>) => void;
  onMove: (t: QueueTask, section: string, parent: string | null) => void;
}) {
  const [menu, setMenu] = useState<DOMRect | null>(null);
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
      <span className="q-menu-wrap">
        <button className="q-dots" aria-haspopup="menu" aria-expanded={!!menu}
                onClick={e => setMenu(m =>
                  m ? null : (e.currentTarget as HTMLElement).getBoundingClientRect())}
                title="snooze, pin, archive, move">⋯</button>
        {menu && (
          <RowMenu t={t} at={menu} sections={sections} onClose={() => setMenu(null)}
                   onMeta={body => { setMenu(null); onMeta(t, body); }}
                   onMove={(sec, par) => { setMenu(null); onMove(t, sec, par); }} />
        )}
      </span>
    </li>
  );
}

/** One line in an expanded list: what it is, and the arithmetic that ranked it.
 *
 *  `shown` marks the rows that are already in the window above. The expanded
 *  list is the whole queue in score order, so those appear here too — leaving
 *  them indistinguishable made the top of the list read as duplication rather
 *  than as "these are the ones you can see". */
function MoreRow({ t, n, glyph, note, shown, action }: {
  t: QueueTask; n?: number; glyph?: string; note?: string; shown?: boolean;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <li className={`q-more-row ${shown ? "shown" : ""}`}>
      <span className="q-more-mark">{glyph ?? (n !== undefined ? `${n}.` : "·")}</span>
      <span className="q-more-body">
        <span className="q-more-text">
          {t.no_sync && <NoSyncMark />}{t.text}
        </span>
        <span className="q-more-why">{note ?? breakdown(t)}</span>
      </span>
      <span className="q-chips">
        {chipsFor(t).slice(0, 2).map(c => (
          <em key={c.label} className={`q-chip ${c.tone}`}>{c.label}</em>
        ))}
      </span>
      {action && (
        <button className="q-more-act" onClick={action.onClick}>{action.label}</button>
      )}
    </li>
  );
}

/**
 * A course timeline, rendered as the sequence it is.
 *
 * Ranking a fixed order would be meaningless — you cannot do Day 5 before Day 4
 * whatever it scores — so this shows the frontier, the rest of the block it sits
 * in, and then the blocks still ahead as a trail. That answers "where am I and
 * what is left", which is the question a timeline is for.
 */
function ChainView({ chain }: { chain: QueueTask[] }) {
  const blocks: { heading: string; items: QueueTask[] }[] = [];
  for (const t of chain) {
    const h = t.heading ?? "—";
    if (!blocks.length || blocks[blocks.length - 1].heading !== h) {
      blocks.push({ heading: h, items: [] });
    }
    blocks[blocks.length - 1].items.push(t);
  }
  if (!blocks.length) return null;
  const [here, ...ahead] = blocks;
  return (
    <>
      <p className="q-more-head">{here.heading}</p>
      <ul className="q-more-rows">
        {here.items.map((t, i) => (
          <MoreRow key={t.id} t={t} glyph={i === 0 ? "▸" : "·"} shown={i === 0}
                   note={i === 0 ? breakdown(t) : "waiting on the one above"} />
        ))}
      </ul>
      {ahead.length > 0 && (
        <p className="q-more-ahead">
          then {ahead.slice(0, 3).map(b => `${b.heading.split("—")[0].trim()} (${b.items.length})`).join(" · ")}
          {ahead.length > 3 && ` · +${ahead.length - 3} more blocks`}
        </p>
      )}
    </>
  );
}

function Expanded({ s, onMeta }: {
  s: QueueSection; onMeta: (t: QueueTask, body: Record<string, unknown>) => void;
}) {
  const groups: Record<string, QueueTask[]> = {};
  for (const t of [...s.visible, ...s.queue, ...s.blocked]) {
    (groups[t.parent ?? ""] ||= []).push(t);
  }

  return (
    <div className="q-more">
      {s.kind === "chain" ? (
        Object.entries(groups).map(([parent, items]) => {
          const seq = items.filter(t => t.chain).sort((a, b) => a.order - b.order);
          const flat = items.filter(t => !t.chain).sort((a, b) => b.score - a.score);
          return (
            <div key={parent} className="q-more-group">
              <p className="q-more-parent">{parent || "unfiled"}</p>
              {seq.length > 0 && <ChainView chain={seq} />}
              {flat.length > 0 && (
                <ul className="q-more-rows">
                  {flat.map((t, i) => (
                    <MoreRow key={t.id} t={t} n={i + 1}
                             shown={s.visible.some(v => v.id === t.id)} />
                  ))}
                </ul>
              )}
            </div>
          );
        })
      ) : (
        <ul className="q-more-rows">
          {[...s.visible, ...s.queue].map((t, i) => (
            <MoreRow key={t.id} t={t} n={i + 1} shown={i < s.visible.length} />
          ))}
        </ul>
      )}

      {/* Suppression is never a one-way door: everything held back carries the
          control that releases it, right where you find it. */}
      {s.snoozed.length > 0 && (
        <>
          <p className="q-more-head">snoozed</p>
          <ul className="q-more-rows">
            {s.snoozed.map(t => (
              <MoreRow key={t.id} t={t} glyph="💤"
                       note={`hidden until ${t.snoozed_until}`}
                       action={{ label: "wake", onClick: () => onMeta(t, { snooze: "" }) }} />
            ))}
          </ul>
        </>
      )}
      {s.archived.length > 0 && (
        <>
          <p className="q-more-head">archived</p>
          <ul className="q-more-rows">
            {s.archived.map(t => (
              <MoreRow key={t.id} t={t} glyph="··" note={t.file}
                       action={{ label: "restore", onClick: () => onMeta(t, { archive: false }) }} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function Card({ s, sections, vault, rows, errs, fresh, onTick, onMeta, onMove }: {
  s: QueueSection; sections: Record<string, QueueSection>; vault: string;
  rows: RowState; errs: Record<string, string>; fresh: Set<string>;
  onTick: (t: QueueTask) => void;
  onMeta: (t: QueueTask, body: Record<string, unknown>) => void;
  onMove: (t: QueueTask, section: string, parent: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
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

  const depth = s.queue.length + s.blocked.length + s.snoozed.length
    + s.archived.length;

  return (
    <section className="panel q-card">
      <h2>
        ◇ {s.title.toUpperCase()}
        <span className="q-head">{[head, ...tail].join(" · ")}</span>
        <button className="q-chev" onClick={() => setOpen(o => !o)} disabled={!depth}
                aria-expanded={open}
                title={depth ? "the whole queue, and why it is in this order"
                             : "nothing behind the window"}>
          {open ? "▾" : "▸"}
        </button>
      </h2>
      {s.visible.length === 0 ? (
        <p className="dim q-empty">
          {depth ? "nothing eligible — everything here is blocked or suppressed"
                 : "nothing queued"}
        </p>
      ) : (
        <ul className="rows q-rows">
          {s.visible.map(t => (
            <Row key={t.id} t={t} s={s} sections={sections} vault={vault}
                 rows={rows} errs={errs} fresh={fresh} onTick={onTick}
                 onMeta={onMeta} onMove={onMove} />
          ))}
        </ul>
      )}
      {open && <Expanded s={s} onMeta={onMeta} />}
    </section>
  );
}

/**
 * Yesterday, in one line, pinned to the bottom.
 *
 * The stars are arithmetic — Python computes them from throughput, deadline
 * adherence and course momentum, and the model that wrote the sentence beside
 * them never saw a number it could change. The tooltip carries the components
 * for the same reason every task row carries its breakdown: a score you cannot
 * take apart is one you stop believing.
 */
function ReviewStrip({ r, vault }: { r: ReviewRow | null; vault: string }) {
  if (!r) {
    return (
      <footer className="q-review dim">
        no review yet — <code>sigma review</code> scores yesterday, or it runs
        itself at 06:00 once scheduled
      </footer>
    );
  }
  const parts = Object.entries(r.components)
    .map(([k, v]) => `${{ T: "throughput", A: "adherence", M: "momentum" }[k]} ${v?.toFixed(1)}`)
    .join(" · ");
  return (
    <footer className="q-review">
      <a href={obsidianHref(vault, `06-System/reviews/${r.date}`)}
         title={parts ? `${parts}\n(weights 0.40 / 0.35 / 0.25, renormalised over what could be measured)`
                      : "not enough history to score yet"}>
        <b className="q-stars">
          {r.score === null ? "—" : "★".repeat(r.score) + "☆".repeat(5 - r.score)}
        </b>
        <span className="q-review-date">{r.date}</span>
        <span className="q-review-facts">
          {Object.entries(r.by_section).filter(([, n]) => n)
            .map(([k, n]) => `${n} ${k}`).join(" · ") || "nothing completed"}
          {r.deadlines_due > 0 && ` · ${r.deadlines_met}/${r.deadlines_due} deadlines`}
          {r.courses > 0 && ` · ${r.advanced}/${r.courses} courses moved`}
        </span>
      </a>
    </footer>
  );
}

export default function WorkView({ open, vault, onClose, onMutate }: {
  open: boolean; vault: string; onClose: () => void; onMutate: () => void;
}) {
  const [q, setQ] = useState<Queue | null | undefined>(undefined);
  const [rev, setRev] = useState<ReviewRow | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const pull = () => get<Queue>("queue").then(setQ).catch(() => setQ(null));

  useEffect(() => {
    if (!open) return;
    setQ(undefined);
    pull();
    get<ReviewResp>("review").then(r => setRev(r.latest)).catch(() => setRev(null));
    // Opening this view is almost always about adding something — Ctrl+; is
    // literally the add shortcut — so the caret starts where the typing goes.
    // Esc still closes: the shell's handler is on document, not the input.
    inputRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // A completion changes both this view and the shell's digest, so the refetch
  // is both: our own payload, and the panels App owns.
  const [oops, setOops] = useState<string | null>(null);
  const { rows, errs, tick } = useQueueTick(() => { pull(); onMutate(); });

  const after = () => { pull(); onMutate(); };
  const fail = (e: unknown) => setOops(
    e instanceof ApiError ? (e.detail || e.code) : "backend unreachable");

  /** Snooze, pin, archive — index-only, so this lands instantly. */
  const meta = (t: QueueTask, body: Record<string, unknown>) => {
    setOops(null);
    post("queue/meta", { id: t.id, ...body }).then(after).catch(fail);
  };

  /** Moving queue is moving note. Same endpoint Accept uses. */
  const move = (t: QueueTask, section: string, parent: string | null) => {
    setOops(null);
    post<QueueEdit>("queue/edit", {
      file: t.file, line: t.line, raw: t.raw, text: t.text,
      due: t.deadline, urgency: t.urgency, section, parent,
    }).then(after).catch(fail);
  };
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

        <QuickAdd vault={vault} sections={q?.sections ?? {}} inputRef={inputRef}
                  onAdded={() => { pull(); onMutate(); }} />

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

        {oops && <p className="err pad">{oops}</p>}

        {q && (
          <div className="work-grid">
            {QUEUE_ORDER.map(k => (
              <Card key={k} s={q.sections[k]} sections={q.sections} vault={vault}
                    rows={rows} errs={errs} fresh={fresh} onTick={tick}
                    onMeta={meta} onMove={move} />
            ))}
          </div>
        )}

        <ReviewStrip r={rev} vault={vault} />
      </div>
    </div>
  );
}
