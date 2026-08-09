/**
 * workbench.tsx — study mode's lesson view (S1 of the study plan).
 *
 * Two panes: a reading column and a dock. S1 ships one dock slot, provenance —
 * the answer to "where did this claim come from" is the segment's source::
 * lines, rendered as links into the vault, the same fixed-foot idea as the
 * agenda's provenance strip. Work pad, code sandbox and tutor arrive with
 * later phases and take the same dock.
 *
 * All view state lives in one reducer on purpose (the plan's §11/§15): focus
 * mode is the same component tree with the dock collapsed — a class flip, not
 * a second view — and extracting the workbench later stays a build-config
 * change. This is the app's first useReducer; the flat-useState convention
 * elsewhere is for views whose state has no invariants between fields.
 *
 * A module whose parse reports problems is HELD and never rendered as if fine
 * — the renderer, doctor and applier hold all read the same validate().
 *
 * Esc peels in arrival order inside the view first (focus, then the dock),
 * in the capture phase, theme-view's pattern; only a bare two-pane view lets
 * the event through to App's ladder to close the whole thing.
 */
import { useEffect, useReducer, useState } from "react";
import { get, obsidianHref } from "./api";
import type { Lesson, LessonList, LessonListRow, LessonSegment } from "./api";

type Depth = "summary" | "normal" | "in_depth";
const DEPTH_LABEL: [Depth, string][] = [
  ["summary", "SUMMARY"], ["normal", "NORMAL"], ["in_depth", "IN DEPTH"]];

type WbState = {
  seg: number;                       // index into segments
  depth: Record<number, Depth>;      // per-segment and sticky…
  fallback: Depth;                   // …over a course-level default
  focus: boolean;                    // dock collapsed, column centred
  dock: boolean;                     // the provenance slot
};
type WbAction =
  | { t: "seg"; i: number }
  | { t: "depth"; i: number; d: Depth }
  | { t: "focus" }
  | { t: "dock"; open: boolean }
  | { t: "reset" };

const START: WbState = { seg: 0, depth: {}, fallback: "normal", focus: false, dock: true };

function reduce(st: WbState, a: WbAction): WbState {
  switch (a.t) {
    case "seg": return { ...st, seg: a.i };
    case "depth": return { ...st, depth: { ...st.depth, [a.i]: a.d } };
    case "focus": return { ...st, focus: !st.focus };
    case "dock": return { ...st, dock: a.open };
    case "reset": return START;
  }
}

/** The content grammar is markdown-lite by construction — paragraphs and
 *  4-space-indented formula blocks, with bold, backticks and wikilinks
 *  inline. Rendering it needs no library, and adding one for this would be
 *  the first runtime dependency beyond react itself. */
function Rich({ text }: { text: string }) {
  const blocks: { pre: boolean; text: string }[] = [];
  let para: string[] = [], pre: string[] = [];
  const flush = () => {
    if (para.length) { blocks.push({ pre: false, text: para.join(" ") }); para = []; }
    if (pre.length) { blocks.push({ pre: true, text: pre.join("\n") }); pre = []; }
  };
  for (const line of text.split("\n")) {
    if (/^\s{4,}\S/.test(line)) {
      if (para.length) flush();
      pre.push(line.slice(4));
    } else if (!line.trim()) flush();
    else {
      if (pre.length) flush();
      para.push(line.trim());
    }
  }
  flush();
  return (
    <>
      {blocks.map((b, i) => b.pre
        ? <pre key={i}>{b.text}</pre>
        : <p key={i}><Inline text={b.text} /></p>)}
    </>
  );
}

function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[\[[^\]]+\]\])/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**")) return <b key={i}>{p.slice(2, -2)}</b>;
        if (p.startsWith("`") && p.endsWith("`")) return <code key={i}>{p.slice(1, -1)}</code>;
        if (p.startsWith("[[") && p.endsWith("]]")) {
          const inner = p.slice(2, -2);
          const bar = inner.indexOf("|");
          const label = bar >= 0 ? inner.slice(bar + 1) : inner.split("/").pop() ?? inner;
          return <span key={i} className="wb-link">{label}</span>;
        }
        return p;
      })}
    </>
  );
}

function Segment({ seg, depth, onDepth }: {
  seg: LessonSegment; depth: Depth; onDepth: (d: Depth) => void;
}) {
  return (
    <>
      <div className="wb-seghead">
        <h3>S{seg.n} · {seg.title}</h3>
        <span className="wb-min">⏱ {seg.minutes} min</span>
      </div>
      <div className="wb-depths" role="tablist" aria-label="Depth">
        {DEPTH_LABEL.map(([d, label]) => (
          <button key={d} role="tab" aria-selected={depth === d}
                  className={depth === d ? "active" : ""}
                  onClick={() => onDepth(d)}>{label}</button>
        ))}
      </div>
      <Rich text={seg[depth]} />
      {seg.example && (
        <div className="wb-example">
          <h4>Example</h4>
          <Rich text={seg.example} />
        </div>
      )}
      {seg.practice.length > 0 && (
        <div className="wb-practice">
          <h4>Practice — {seg.practice.length} item(s)</h4>
          <ul>
            {seg.practice.map(it => (
              <li key={it.id}>
                <span className="wb-kind">{it.id} · {it.kind}</span>
                <Rich text={it.prompt} />
              </li>
            ))}
          </ul>
          <p className="dim">reveals, hints and grading arrive with the practice engine (S3)</p>
        </div>
      )}
    </>
  );
}

export default function WorkbenchView({ open, vault, onClose }: {
  open: boolean; vault: string; onClose: () => void;
}) {
  const [list, setList] = useState<LessonList | null | undefined>(undefined);
  const [picked, setPicked] = useState<LessonListRow | null>(null);
  const [lesson, setLesson] = useState<Lesson | null | undefined>(undefined);
  const [st, dispatch] = useReducer(reduce, START);

  useEffect(() => {
    if (!open) return;
    dispatch({ t: "reset" });
    setList(undefined); setPicked(null); setLesson(undefined);
    get<LessonList>("lesson").then(l => {
      setList(l);
      setPicked(l.modules[0] ?? null);
    }).catch(() => setList(null));
  }, [open]);

  useEffect(() => {
    if (!open || !picked) return;
    setLesson(undefined);
    get<Lesson>(`lesson/${picked.course}/${picked.module}`)
      .then(setLesson).catch(() => setLesson(null));
  }, [open, picked]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.ctrlKey || e.altKey || e.metaKey) return;
      if (e.key === "f") {
        const tag = (e.target as HTMLElement | null)?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        e.preventDefault();
        dispatch({ t: "focus" });
      } else if (e.key === "Escape") {
        if (st.focus) dispatch({ t: "focus" });
        else if (st.dock) dispatch({ t: "dock", open: false });
        else return;                 // bare view — App's ladder closes it
        e.preventDefault();
        e.stopPropagation();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, st.focus, st.dock]);

  if (!open) return null;

  const seg = lesson?.segments[st.seg];
  const depth = st.depth[st.seg] ?? st.fallback;
  const held = (lesson?.problems.length ?? 0) > 0;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className={`workbench ${st.focus ? "focus" : ""}`}
           onClick={e => e.stopPropagation()} role="dialog" aria-label="Workbench">
        <header className="study-head">
          <span className="label">
            ◇ STUDY — WORKBENCH
            {lesson ? ` · ${lesson.course} M${String(lesson.module ?? "?").padStart(2, "0")} — ${lesson.title}` : ""}
            {lesson?.estimate ? <span className="dim"> · {lesson.estimate} min</span> : null}
          </span>
          <span>
            <button className="ghost" onClick={() => dispatch({ t: "focus" })}
                    title="Focus mode — collapse the dock (f)">
              {st.focus ? "◫ dock" : "▣ focus"}
            </button>
            <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
          </span>
        </header>

        {list === undefined && <p className="dim pad">reading…</p>}
        {list === null && <p className="err pad">backend unreachable</p>}
        {list && list.modules.length === 0 && (
          <p className="dim pad">no guide modules in the vault yet — a module is a
          note in <code>02-Areas/Academics/&lt;COURSE&gt;/guide/</code></p>
        )}

        {list && list.modules.length > 1 && (
          <div className="wb-picker">
            {list.modules.map(m => (
              <button key={m.file}
                      className={picked?.file === m.file ? "active" : ""}
                      onClick={() => setPicked(m)}>
                {m.course} M{String(m.module ?? "?").padStart(2, "0")}
                {m.problems.length > 0 ? " ⚠" : ""}
              </button>
            ))}
          </div>
        )}

        {picked && lesson === undefined && <p className="dim pad">parsing…</p>}
        {picked && lesson === null && <p className="err pad">backend unreachable</p>}

        {lesson && held && (
          <div className="wb-held">
            <h3>HELD — this module fails the grammar</h3>
            <p className="dim">A malformed module is a broken lesson that would read
            as shipped, so it is named instead of rendered:</p>
            <ul>{lesson.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
          </div>
        )}

        {lesson && !held && (
          <>
            <div className="wb-segs">
              {lesson.segments.map((s, i) => (
                <button key={s.n} className={i === st.seg ? "active" : ""}
                        title={`${s.title} — ⏱ ${s.minutes} min`}
                        onClick={() => dispatch({ t: "seg", i })}>
                  S{s.n} <span className="wb-min">{s.minutes}′</span>
                </button>
              ))}
            </div>
            <div className="wb-body">
              <div className="wb-read">
                {seg && (
                  <Segment seg={seg} depth={depth}
                           onDepth={d => dispatch({ t: "depth", i: st.seg, d })} />
                )}
                <div className="wb-nav">
                  <button className="ghost" disabled={st.seg === 0}
                          onClick={() => dispatch({ t: "seg", i: st.seg - 1 })}>← prev</button>
                  <span className="dim">{st.seg + 1} / {lesson.segments.length}</span>
                  <button className="ghost" disabled={st.seg >= lesson.segments.length - 1}
                          onClick={() => dispatch({ t: "seg", i: st.seg + 1 })}>next →</button>
                </div>
              </div>
              {!st.focus && st.dock && seg && (
                <aside className="wb-dock">
                  <h4>[?] Provenance</h4>
                  <p className="dim">where this segment comes from — the claim is
                  only as good as the note it cites</p>
                  <ul className="wb-prov">
                    {seg.sources.map(src => (
                      <li key={`${src.path}:${src.line}`}>
                        <a href={obsidianHref(vault, src.path)}
                           title={`source:: at line ${src.line} of the module`}>
                          {src.path.split("/").pop()}
                        </a>
                        <span className="dim"> · {src.path}</span>
                      </li>
                    ))}
                  </ul>
                  <h4>Module</h4>
                  <ul className="wb-prov">
                    <li>
                      <a href={obsidianHref(vault, lesson.file)}>{lesson.file.split("/").pop()}</a>
                      <span className="dim"> · segment at line {seg.line}</span>
                    </li>
                    {lesson.verified && <li className="dim">sources last verified {lesson.verified}</li>}
                  </ul>
                  <h4>All module sources</h4>
                  <ul className="wb-prov">
                    {lesson.sources.map(s => (
                      <li key={s}>
                        <a href={obsidianHref(vault, s)}>{s.split("/").pop()}</a>
                      </li>
                    ))}
                  </ul>
                </aside>
              )}
              {!st.focus && !st.dock && (
                <aside className="wb-dock wb-dock-closed">
                  <button className="ghost" onClick={() => dispatch({ t: "dock", open: true })}
                          title="Reopen the provenance slot">[?] provenance</button>
                </aside>
              )}
            </div>
            <footer className="wb-foot dim">
              <kbd>f</kbd> focus · <kbd>Esc</kbd> peels dock, focus, then closes
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
