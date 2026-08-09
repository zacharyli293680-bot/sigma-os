/**
 * study.tsx — exam mode (Phase 6), on the rail's ST slot.
 *
 * `dashboard-vision` asks for "everything for one exam in one place: what is
 * covered, what you have not practised, what you got wrong last time."
 *
 * All three are here now. The third was deliberately absent until study S6:
 * with no real record of a wrong answer, a "weak areas" panel would have been
 * a confident guess wearing the costume of data. The practice engine (S3)
 * changed that — every resolution lands in the attempt log, and each session
 * rollup splices a durable digest row into the course's study log — so the
 * panel renders those records, names which source each half comes from, and
 * still says "nothing recorded" when that is the truth. It never guesses.
 *
 * "Not practised" means *no note embeds this source*. Intake writes
 * `> Source: ![[file]]` into everything it produces and hand-written notes use
 * the same embed, so the embed set is the actual record of what has been worked
 * through — a fact about the vault rather than a guess from filenames.
 */
import { useEffect, useState } from "react";
import { get, obsidianHref } from "./api";
import type { CourseCoverage, PracticeHistory, Study } from "./api";

function Bar({ covered, total }: { covered: number; total: number }) {
  const pct = total ? Math.round((covered / total) * 100) : 0;
  return (
    <span className="cov-bar" title={`${covered} of ${total} (${pct}%)`}>
      <span className="cov-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

function Course({ c, vault }: { c: CourseCoverage; vault: string }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="cov-course">
      <button className="cov-head" onClick={() => setOpen(o => !o)}>
        <span className="cov-name">{open ? "▾" : "▸"} {c.course}</span>
        <Bar covered={c.covered} total={c.sources} />
        <span className="dim">{c.covered}/{c.sources} worked through</span>
      </button>
      {open && (
        <>
          <ul className="cov-folders">
            {c.by_folder.map(g => (
              <li key={g.folder}>
                <span>{g.folder === "." ? "(course root)" : g.folder}</span>
                <Bar covered={g.covered} total={g.total} />
                <b className={g.covered === 0 ? "none" : ""}>{g.covered}/{g.total}</b>
              </li>
            ))}
          </ul>
          {c.uncovered_sample.length > 0 && (
            <>
              <p className="dim sub">not yet worked through</p>
              <ul className="cov-files">
                {c.uncovered_sample.map(f => (
                  <li key={f}>
                    <a href={obsidianHref(vault, f)} title={f}>{f.split("/").pop()}</a>
                  </li>
                ))}
              </ul>
              {c.uncovered_more > 0 && (
                <p className="dim">+{c.uncovered_more} more — the list caps at 12.</p>
              )}
            </>
          )}
        </>
      )}
    </li>
  );
}

function Misses({ p, vault }: { p: PracticeHistory; vault: string }) {
  return (
    <li className="miss-course">
      <div className="miss-head">
        <span className="cov-name">{p.course}</span>
        <span className="dim">
          {p.answered} answered · <b className={p.wrong ? "miss-n" : ""}>{p.wrong} missed</b>
          {p.last ? ` · last ${p.last}` : ""}
        </span>
      </div>
      {p.by_topic.length > 0 && (
        <ul className="miss-topics">
          {p.by_topic.map(t => (
            <li key={t.topic}>
              <span>{t.topic}</span>
              <b>×{t.wrong}</b>
              <span className="dim">{t.last}</span>
            </li>
          ))}
        </ul>
      )}
      {p.sessions.length > 0 && (
        <>
          <p className="dim sub">
            sessions, from <a href={obsidianHref(vault,
              `02-Areas/Academics/${p.course}/${p.course.toLowerCase()}-study-log`)}>
              the study log</a>
          </p>
          <ul className="miss-sessions">
            {p.sessions.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
          {p.sessions_more > 0 && (
            <p className="dim">+{p.sessions_more} more in the note.</p>
          )}
        </>
      )}
    </li>
  );
}

export default function StudyView({ open, vault, onClose, onWorkbench }: {
  open: boolean; vault: string; onClose: () => void; onWorkbench?: () => void;
}) {
  const [d, setD] = useState<Study | null | undefined>(undefined);

  useEffect(() => {
    if (!open) return;
    setD(undefined);
    get<Study>("study").then(setD).catch(() => setD(null));
  }, [open]);

  if (!open) return null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="study" onClick={e => e.stopPropagation()}
           role="dialog" aria-label="Study">
        <header className="study-head">
          <span className="label">◇ STUDY — EXAM MODE</span>
          <span>
            {/* The door to the S1 workbench. Exam mode folds into it as
                panels in S2; until then the two views point at each other. */}
            {onWorkbench && (
              <button className="ghost" onClick={onWorkbench}
                      title="Study workbench (Ctrl+\)">◫ workbench</button>
            )}
            <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
          </span>
        </header>

        {d === undefined && <p className="dim pad">reading…</p>}
        {d === null && <p className="err pad">backend unreachable</p>}

        {d && (
          <div className="study-body">
            <h3>Exam units</h3>
            {d.exams.length === 0 ? (
              <p className="dim">No `exam-prep` notes yet. Study intake makes them —
                drop a study guide in <code>00-Inbox/intake/&lt;COURSE&gt;/</code>.</p>
            ) : (
              <ul className="exam-list">
                {d.exams.map(e => (
                  <li key={e.file}>
                    <a href={obsidianHref(vault, e.file.replace(/\.md$/, ""))}>
                      <span className="exam-course">{e.course}</span>
                      <span className="exam-id">{e.exam ?? "—"}</span>
                      <span className="exam-title">{e.title}</span>
                    </a>
                    <span className={`exam-when ${e.days !== null && e.days <= 7 ? "soon" : ""}`}>
                      {/* No date is not "today" and not zero. */}
                      {e.date === null ? "no date set"
                        : e.days === null ? e.date
                        : e.days < 0 ? `${-e.days}d ago`
                        : e.days === 0 ? "today"
                        : `in ${e.days}d`}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            <h3>What you have actually worked through</h3>
            <p className="dim study-note">
              A source counts as worked through when a note embeds it. That is the
              vault's own record, not a guess from filenames.
            </p>
            <ul className="cov-list">
              {d.coverage.map(c => <Course key={c.course} c={c} vault={vault} />)}
            </ul>

            <h3>What you got wrong last time</h3>
            {d.practice.length === 0 ? (
              <p className="dim study-note">
                Nothing recorded yet. Practising in the workbench writes the
                attempt log this panel reads, and each session's rollup leaves a
                durable digest row in the course's study log — records, never
                guesses.
              </p>
            ) : (
              <>
                <p className="dim study-note">
                  Topics from this machine's attempt log; session rows from the
                  study log note — the digest that survives a sidecar wipe.
                </p>
                <ul className="miss-list">
                  {d.practice.map(p => <Misses key={p.course} p={p} vault={vault} />)}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
