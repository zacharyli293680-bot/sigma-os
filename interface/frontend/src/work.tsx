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
import type { Queue, QueueAdd, QueueEdit, QueueSection, QueueTask, Reword, RewordResp } from "./api";
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
function QuickAdd({ vault, onAdded, inputRef }: {
  vault: string; onAdded: () => void;
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
      const r = await post<QueueAdd>("queue/add", {
        text: t,
        due: due || null,
        urgency,
        section: section === "auto" ? null : section,
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
        {QUEUE_ORDER.map(k => <option key={k} value={k}>{SECTION_LABEL[k]}</option>)}
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
  const inputRef = useRef<HTMLInputElement | null>(null);

  const pull = () => get<Queue>("queue").then(setQ).catch(() => setQ(null));

  useEffect(() => {
    if (!open) return;
    setQ(undefined);
    pull();
    // Opening this view is almost always about adding something — Ctrl+; is
    // literally the add shortcut — so the caret starts where the typing goes.
    // Esc still closes: the shell's handler is on document, not the input.
    inputRef.current?.focus();
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

        <QuickAdd vault={vault} inputRef={inputRef}
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
