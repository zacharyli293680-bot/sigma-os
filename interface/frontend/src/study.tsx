/**
 * study.tsx — exam mode (Phase 6), on the rail's ST slot.
 *
 * `dashboard-vision` asks for "everything for one exam in one place: what is
 * covered, what you have not practised, what you got wrong last time."
 *
 * The first two are here. **The third is deliberately absent**: nothing in the
 * vault records a wrong answer, so a "weak areas" panel would be a confident
 * guess wearing the costume of data. Saying the number does not exist is more
 * useful than inventing it, and the view says so out loud rather than quietly
 * omitting a third of what was asked for.
 *
 * "Not practised" means *no note embeds this source*. Intake writes
 * `> Source: ![[file]]` into everything it produces and hand-written notes use
 * the same embed, so the embed set is the actual record of what has been worked
 * through — a fact about the vault rather than a guess from filenames.
 */
import { useEffect, useState } from "react";
import { get, obsidianHref } from "./api";
import type { CourseCoverage, Study } from "./api";

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
            <p className="dim study-note">
              Not built, deliberately. Nothing in the vault records a wrong answer,
              so any "weak areas" figure here would be invented. It needs a real
              source first — graded work, or a practice log.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
