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
import type { Practice, PracticeLog, Queue, QueueAdd, QueueEdit, QueueSection,
              QueueTask, Rep, Reword, RewordResp, ReviewResp, ReviewRow } from "./api";
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
          {t.chain_kind === "guide" && (
            <em className="q-guide"
                title="the study guide's frontier — its own slot beside the course's other work, never displacing it">
              guide
            </em>
          )}
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
 *  than as "these are the ones you can see". Both copies are the same object,
 *  so `rows[t.id]` drives them together: ticking the frontier slides both out
 *  at once, which reads as one task leaving one queue drawn at two sizes.
 *
 *  `off` marks a row that is not next in its sequence. It is a *marking*, not a
 *  lock — the checkbox works. Markdown is the truth and doing Day 5 before Day 4
 *  is a thing people actually do; `_chain` re-derives the frontier as the
 *  earliest still-open task on every build, so ticking here moves nothing else
 *  and opens no second frontier. The row already says "waiting on the one above"
 *  in words, so the amber tick only sharpens what is written — status is never
 *  colour alone.
 *
 *  The ⋯ menu is omitted for snoozed and archived rows: they carry `wake` and
 *  `restore` instead, and "snooze a snoozed task" is not an action. */
function MoreRow({ t, n, glyph, note, shown, off, action, vault, sections,
                  rows, errs, onTick, onMeta, onMove }: {
  t: QueueTask; n?: number; glyph?: string; note?: string;
  shown?: boolean; off?: boolean;
  action?: { label: string; onClick: () => void };
  vault: string; sections?: Record<string, QueueSection>;
  rows: RowState; errs: Record<string, string>;
  onTick: (t: QueueTask) => void;
  onMeta?: (t: QueueTask, body: Record<string, unknown>) => void;
  onMove?: (t: QueueTask, section: string, parent: string | null) => void;
}) {
  const [menu, setMenu] = useState<DOMRect | null>(null);
  const st = rows[t.id];
  return (
    <li className={`q-more-row ${shown ? "shown" : ""} ${off ? "off" : ""} ${st ?? ""}`}>
      <button className={`q-more-box ${off ? "off" : ""} ${st ?? ""}`} disabled={!!st}
              onClick={() => onTick(t)}
              aria-label={off ? `tick "${t.text}" out of sequence` : `tick "${t.text}"`}
              title={off
                ? "not next in this sequence — ticking writes to the note anyway "
                  + "and the frontier does not move. One commit of its own, "
                  + "revertible in the ledger (Ctrl+J)"
                : "tick it — writes to the note as its own revertible commit"}>
        {st === "leaving" ? "☑" : st === "busy" ? "◌" : "☐"}
      </button>
      <span className="q-more-mark">{glyph ?? (n !== undefined ? `${n}.` : "·")}</span>
      <span className="q-more-body">
        <a className="q-more-text" href={obsidianHref(vault, t.file.replace(/\.md$/, ""))}
           title={`${t.file}:${t.line}`}>
          {t.no_sync && <NoSyncMark />}{t.text}
        </a>
        <span className="q-more-why">{note ?? breakdown(t)}</span>
        {errs[t.id] && <span className="row-err">{errs[t.id]}</span>}
      </span>
      <span className="q-chips">
        {chipsFor(t).slice(0, 2).map(c => (
          <em key={c.label} className={`q-chip ${c.tone}`}>{c.label}</em>
        ))}
      </span>
      {action && (
        <button className="q-more-act" onClick={action.onClick}>{action.label}</button>
      )}
      {sections && onMeta && onMove && (
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
 *
 * `show all` opens the trail into real rows. The summary alone was right while
 * the list was something you read; it stopped being enough the moment every row
 * grew a checkbox, because a task with no DOM node cannot be ticked — and on
 * this vault that was ~95 of the 100 blocked course tasks. The bounded scroller
 * and the sticky headings are what make the long form usable, which is why this
 * arrived with them rather than before them.
 */
function ChainView({ chain, vault, sections, rows, errs, onTick, onMeta, onMove }: {
  chain: QueueTask[]; vault: string; sections: Record<string, QueueSection>;
  rows: RowState; errs: Record<string, string>;
  onTick: (t: QueueTask) => void;
  onMeta: (t: QueueTask, body: Record<string, unknown>) => void;
  onMove: (t: QueueTask, section: string, parent: string | null) => void;
}) {
  const [all, setAll] = useState(false);
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
  const rowProps = { vault, sections, rows, errs, onTick, onMeta, onMove };
  return (
    <>
      {/* Each block is its own element so its heading has its own stick region.
          As plain siblings under the group, every heading shared one — all six
          of AA-210's pinned to the same 26px and the last one in the DOM
          painted on top, so scrolling through Block 3 showed "Block 6". */}
      <div className="q-more-block">
        <p className="q-more-head">{here.heading}</p>
        <ul className="q-more-rows">
          {here.items.map((t, i) => (
            <MoreRow key={t.id} t={t} glyph={i === 0 ? "▸" : "·"} shown={i === 0}
                     off={i > 0}
                     note={i === 0 ? breakdown(t) : "waiting on the one above"}
                     {...rowProps} />
          ))}
        </ul>
      </div>
      {ahead.length > 0 && !all && (
        <p className="q-more-ahead">
          then {ahead.slice(0, 3).map(b => `${b.heading.split("—")[0].trim()} (${b.items.length})`).join(" · ")}
          {ahead.length > 3 && ` · +${ahead.length - 3} more blocks`}
          <button className="q-more-all" onClick={() => setAll(true)}
                  title="open the blocks ahead as rows, so they can be ticked">
            show all
          </button>
        </p>
      )}
      {ahead.length > 0 && all && (
        <>
          {ahead.map((b, bi) => (
            <div key={`${b.heading}-${bi}`} className="q-more-block">
              <p className="q-more-head">{b.heading}</p>
              <ul className="q-more-rows">
                {b.items.map(t => (
                  <MoreRow key={t.id} t={t} glyph="·" off
                           note="waiting on the one above" {...rowProps} />
                ))}
              </ul>
            </div>
          ))}
          <p className="q-more-ahead">
            <button className="q-more-all" onClick={() => setAll(false)}>
              hide the blocks ahead
            </button>
          </p>
        </>
      )}
    </>
  );
}

function Expanded({ s, sections, vault, rows, errs, onTick, onMeta, onMove }: {
  s: QueueSection; sections: Record<string, QueueSection>; vault: string;
  rows: RowState; errs: Record<string, string>;
  onTick: (t: QueueTask) => void;
  onMeta: (t: QueueTask, body: Record<string, unknown>) => void;
  onMove: (t: QueueTask, section: string, parent: string | null) => void;
}) {
  const groups: Record<string, QueueTask[]> = {};
  for (const t of [...s.visible, ...s.queue, ...s.blocked]) {
    (groups[t.parent ?? ""] ||= []).push(t);
  }
  // Spread rather than nine repeated attributes at seven call sites. MoreRow's
  // own signature stays flat, matching Row — this is only how it is called.
  const rowProps = { vault, sections, rows, errs, onTick, onMeta, onMove };
  // The held-back lists get no ⋯: they carry wake/restore instead.
  const heldProps = { vault, rows, errs, onTick };

  return (
    <div className="q-more" id={`q-more-${s.key}`}>
      {s.kind === "chain" ? (
        Object.entries(groups).map(([parent, items]) => {
          // Sorted by file first, matching todo.py's own `sorted(key=(file,
          // order))` — then split into one ChainView per sequence *document*.
          // A course can run a timeline and a guide at once (study S2), and
          // feeding both files into one ChainView rendered the timeline's
          // real frontier as "waiting on the one above" — a dependency claim
          // nothing in either note makes.
          const seq = items.filter(t => t.chain)
            .sort((a, b) => a.file.localeCompare(b.file) || a.order - b.order);
          const seqFiles = [...new Set(seq.map(t => t.file))];
          const flat = items.filter(t => !t.chain).sort((a, b) => b.score - a.score);
          return (
            <div key={parent} className="q-more-group">
              <p className="q-more-parent">{parent || "unfiled"}</p>
              {seqFiles.map(f => (
                <ChainView key={f} chain={seq.filter(t => t.file === f)}
                           {...rowProps} />
              ))}
              {flat.length > 0 && (
                <ul className="q-more-rows">
                  {flat.map((t, i) => (
                    <MoreRow key={t.id} t={t} n={i + 1}
                             shown={s.visible.some(v => v.id === t.id)}
                             {...rowProps} />
                  ))}
                </ul>
              )}
            </div>
          );
        })
      ) : (
        <>
          <ul className="q-more-rows">
            {[...s.visible, ...s.queue].map((t, i) => (
              <MoreRow key={t.id} t={t} n={i + 1}
                       shown={s.visible.some(v => v.id === t.id)} {...rowProps} />
            ))}
          </ul>
          {/* Unreachable today — only chain files set blocked_by, and _chain
              runs for the two per-parent sections only — but the chevron's
              `depth` counts these, so a flat section holding nothing but
              blocked work would open an empty list. No rank numbers on them: a
              number claims a position in an order they are not in. */}
          {s.blocked.length > 0 && (
            <div className="q-more-group">
              <p className="q-more-head">blocked</p>
              <ul className="q-more-rows">
                {s.blocked.map(t => (
                  <MoreRow key={t.id} t={t} glyph="·" off
                           note={t.blocked_by ? `waiting on "${t.blocked_by}"` : "blocked"}
                           {...rowProps} />
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {/* Suppression is never a one-way door: everything held back carries the
          control that releases it, right where you find it. Each list is its own
          group so its sticky heading is bounded by it — two sticky headings
          sharing the scroller would pile up at the top of it. */}
      {s.snoozed.length > 0 && (
        <div className="q-more-group">
          <p className="q-more-head">snoozed</p>
          <ul className="q-more-rows">
            {s.snoozed.map(t => (
              <MoreRow key={t.id} t={t} glyph="💤"
                       note={`hidden until ${t.snoozed_until}`}
                       action={{ label: "wake", onClick: () => onMeta(t, { snooze: "" }) }}
                       {...heldProps} />
            ))}
          </ul>
        </div>
      )}
      {s.archived.length > 0 && (
        <div className="q-more-group">
          <p className="q-more-head">archived</p>
          <ul className="q-more-rows">
            {s.archived.map(t => (
              <MoreRow key={t.id} t={t} glyph="··" note={t.file}
                       action={{ label: "restore", onClick: () => onMeta(t, { archive: false }) }}
                       {...heldProps} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Card({ s, sections, vault, rows, errs, fresh, onTick, onMeta, onMove,
                open, onToggle, gridRef }: {
  s: QueueSection; sections: Record<string, QueueSection>; vault: string;
  rows: RowState; errs: Record<string, string>; fresh: Set<string>;
  onTick: (t: QueueTask) => void;
  onMeta: (t: QueueTask, body: Record<string, unknown>) => void;
  onMove: (t: QueueTask, section: string, parent: string | null) => void;
  /** Which card is expanded lives in WorkView now — one at a time. An open card
   *  spans the whole grid, so two of them cannot coexist anyway. */
  open: boolean; onToggle: () => void;
  gridRef: React.RefObject<HTMLDivElement | null>;
}) {
  const cardRef = useRef<HTMLElement | null>(null);
  // Bring the card you just opened to the top of the grid — otherwise opening
  // PROJECTS leaves you looking at COURSES. Rect deltas rather than offsetTop:
  // `.work-grid` is position:static, so both elements' offsetParent is the
  // backdrop and offsetTop would not account for the grid's own scroll. Same
  // reason RowMenu measures rather than computes. scrollTo on the grid
  // specifically, so nothing else on the page moves.
  useEffect(() => {
    const card = cardRef.current, grid = gridRef.current;
    if (!open || !card || !grid) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    grid.scrollTo({
      top: grid.scrollTop + card.getBoundingClientRect().top
           - grid.getBoundingClientRect().top - 12,   // .work-grid's padding-top
      behavior: still ? "auto" : "smooth",
    });
  }, [open, gridRef]);

  const tail: string[] = [];
  if (s.queue.length) tail.push(`${s.queue.length} queued`);
  if (s.blocked.length) tail.push(`${s.blocked.length} blocked`);
  if (s.snoozed.length) tail.push(`${s.snoozed.length} snoozed`);
  if (s.archived.length) tail.push(`${s.archived.length} archived`);
  // "2 of 5 courses" rather than "top 5": the number counts parents, not tasks,
  // and it puts the silent ones on screen — three active courses have no
  // timeline and contribute nothing, which "one per course" would hide.
  // Distinct parents, not rows: a course showing both its timeline head and
  // its guide head (study S2's widened window) is still one course.
  const head = s.parent_noun
    ? `${new Set(s.visible.map(t => t.parent)).size} of ${s.window} ${s.parent_noun}s`
    : `top ${s.window}`;

  const depth = s.queue.length + s.blocked.length + s.snoozed.length
    + s.archived.length;

  return (
    <section ref={cardRef} className={`panel q-card ${open ? "open" : ""}`}>
      <h2>
        ◇ {s.title.toUpperCase()}
        <span className="q-head">{[head, ...tail].join(" · ")}</span>
        <button className="q-chev" onClick={onToggle} disabled={!depth}
                aria-expanded={open} aria-controls={`q-more-${s.key}`}
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
      {open && (
        <Expanded s={s} sections={sections} vault={vault} rows={rows} errs={errs}
                  onTick={onTick} onMeta={onMeta} onMove={onMove} />
      )}
    </section>
  );
}

/**
 * The daily habit — the one card in this grid with no checkbox.
 *
 * It is deliberately not a fifth queue. The four queues hold checkbox lines
 * that can be ticked, snoozed, pinned and moved between sections; a habit has
 * none of those affordances, and folding it into `sections` would mean every
 * consumer special-casing one member. It sits beside them because it is work
 * you owe today, which is what this view is for.
 *
 * **Typing the number is the completion.** There is no open box to tick — a
 * repeated checkbox collides on identity and leaves debt behind on skipped days
 * (see `runtime/leetcode.py`) — so the input *is* the affordance, and the line
 * it writes is simultaneously the record of which problem you solved.
 *
 * A duplicate comes back 409 with the date you first solved it, and becomes an
 * offer rather than an error: that prompt is the whole reason for keeping a
 * record of what you have already done.
 *
 * **The day's problem being logged does not close the card.** `p.done` dims the
 * border and flips the mark, and that is all it does — the input stays live and
 * asks for another one, because the habit's floor is one a day and nothing about
 * it is a ceiling. A second solve is refused only if it is the same *number*,
 * which is a duplicate rather than a quota.
 */
function PracticeCard({ p, vault, onLogged }: {
  p: Practice; vault: string; onLogged: () => void;
}) {
  const [n, setN] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dupe, setDupe] = useState<Rep | null>(null);
  const [said, setSaid] = useState<PracticeLog | null>(null);
  // Collapsed by default: this card sits in a grid with four queues, and a
  // habit that permanently occupied a dozen rows would crowd out the work that
  // is actually still open.
  const [past, setPast] = useState(false);

  async function submit(again = false) {
    const num = n.trim().replace(/^#/, "");
    if (!num || busy) return;
    setBusy(true); setErr(null); setDupe(null); setSaid(null);
    try {
      const r = await post<PracticeLog>("practice/log", { n: num, again });
      setSaid(r); setN(""); setBusy(false); onLogged();
    } catch (e) {
      setBusy(false);
      if (e instanceof ApiError && e.code === "already solved") {
        // Not an error state: the record did its job. Keep the number in the
        // input so the offer below can act on exactly what was typed.
        setDupe((e.body as { duplicate?: Rep } | undefined)?.duplicate ?? null);
        setErr(e.detail || "already solved");
      } else {
        setErr(e instanceof ApiError ? (e.detail || e.code) : "backend unreachable");
      }
    }
  }

  const st = p.streak;
  const c = p.by_difficulty;
  return (
    <section className={`panel q-card q-practice ${p.done ? "is-done" : ""}`}>
      <h2>
        ◇ PRACTICE
        <span className="q-head">
          {p.goal}
          {st.current > 0 && ` · ${st.current}-day streak`}
          {st.at_risk && " · at risk"}
        </span>
      </h2>

      <div className="q-pr-body">
        <span className={`q-pr-mark ${p.done ? "done" : ""}`}
              title={p.done ? "today's problem is logged"
                            : "nothing logged today — the review's practice component reads 0"}>
          {p.done ? "◉" : "◎"}
        </span>
        <a className="q-pr-title" href={obsidianHref(vault, p.file)}
           title={`${p.file} — the record of every problem solved`}>{p.title}</a>
        <span className="q-pr-stats dim">
          {p.total} solved · {c.easy}E {c.medium}M {c.hard}H
          {st.longest > 0 && ` · longest ${st.longest}d`}
        </span>
      </div>

      {p.today.length > 0 && (
        <ul className="rows q-pr-today">
          {p.today.map(r => <RepRow key={`${r.n}-${r.revisit}`} r={r} />)}
        </ul>
      )}

      <form className="q-pr-add" onSubmit={e => { e.preventDefault(); submit(); }}>
        {/* type=text with inputMode numeric, not type=number: the spinner and
            the scroll-wheel increment are both wrong for an identifier, and a
            mis-scrolled 217 -> 218 would log a problem you did not solve. */}
        <input value={n} onChange={e => setN(e.target.value)}
               inputMode="numeric" pattern="[0-9]*" maxLength={6}
               placeholder={p.done ? "another one?" : "problem #"}
               aria-label="LeetCode problem number" disabled={busy} />
        <button type="submit" disabled={busy || !n.trim()}>
          {busy ? "…" : "LOG"}
        </button>
      </form>

      {said && (
        <p className="q-pr-said">
          logged <b>{said.n}</b> {said.title}
          {said.difficulty && <span className={`q-pr-d d-${said.difficulty}`}>{said.difficulty}</span>}
        </p>
      )}
      {err && (
        <p className={dupe ? "q-pr-dupe" : "err"}>
          {err}
          {dupe && (
            <button className="ghost" onClick={() => submit(true)} disabled={busy}
                    title="record it again as a revisit — a distinct line, its own completion">
              LOG AS REVISIT
            </button>
          )}
        </p>
      )}

      {/* The record, read side. `p.recent` excludes today — that list is above —
          and is capped by the backend, so this is the glance and the note is
          the full history. Rendered only when there is a past to show, so a
          first-day vault gets no empty disclosure. */}
      {p.recent.length > 0 && (
        <div className="q-pr-past">
          <button className="ghost q-pr-more" onClick={() => setPast(v => !v)}
                  aria-expanded={past}
                  title={`the last ${p.recent.length} before today — ${p.file} has them all`}>
            {past ? "▾ EARLIER" : "▸ EARLIER"}
          </button>
          {past && (
            <ul className="rows q-pr-hist">
              {p.recent.map(r => (
                <RepRow key={`${r.date}-${r.n}-${r.revisit}`} r={r} date />
              ))}
              <li className="q-pr-all">
                <a href={obsidianHref(vault, p.file)}
                   title="the full record — every problem, every day">
                  all {p.total} in {p.file.split("/").pop()}
                </a>
              </li>
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

/** One solved problem, in the practice card. Shared by today's list and the
 *  history below it so the two cannot drift apart visually — `date` is the only
 *  difference, and only history needs it (today's rows are all today). */
function RepRow({ r, date = false }: { r: Rep; date?: boolean }) {
  return (
    <li>
      {date && <span className="q-pr-day dim">{r.date.slice(5)}</span>}
      <span className="q-pr-n">{r.n}</span>
      <span className="q-pr-name">{r.title || "(no title)"}</span>
      {r.difficulty && <span className={`q-pr-d d-${r.difficulty}`}>{r.difficulty}</span>}
      {r.revisit > 1 && <span className="dim">revisit {r.revisit}</span>}
    </li>
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
    .map(([k, v]) => `${{ T: "throughput", A: "adherence", M: "momentum",
                          P: "practice" }[k]} ${v?.toFixed(1)}`)
    .join(" · ");
  return (
    <footer className="q-review">
      <a href={obsidianHref(vault, `06-System/reviews/${r.date}`)}
         title={parts ? `${parts}\n(weights 0.40 / 0.35 / 0.25 / 0.15, renormalised over what could be measured)`
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
  // One card expanded at a time. Lifted out of Card: an open card spans the
  // whole grid, and four cards each growing their own grid row without bound
  // was the crowding this replaces.
  const [openKey, setOpenKey] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);

  const pull = () => get<Queue>("queue").then(setQ).catch(() => setQ(null));

  useEffect(() => {
    if (!open) return;
    setQ(undefined);
    // Collapse on every open. The expansion now outlives Card's unmount, and
    // reopening WK onto a 600px expanded card fights the reason the caret goes
    // to the quick-add below — this view is almost always entered to add.
    setOpenKey(null);
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
          <div className="work-grid" ref={gridRef}>
            {QUEUE_ORDER.map(k => (
              <Card key={k} s={q.sections[k]} sections={q.sections} vault={vault}
                    rows={rows} errs={errs} fresh={fresh} onTick={tick}
                    onMeta={meta} onMove={move}
                    open={openKey === k} gridRef={gridRef}
                    onToggle={() => setOpenKey(o => (o === k ? null : k))} />
            ))}
            {/* Null when the vault has no practice-log note, so a vault that
                never opted into a habit renders no card rather than a
                permanently-unmet obligation nobody signed up for. */}
            {q.practice && (
              <PracticeCard p={q.practice} vault={vault}
                            onLogged={() => { pull(); onMutate(); }} />
            )}
          </div>
        )}

        <ReviewStrip r={rev} vault={vault} />
      </div>
    </div>
  );
}
