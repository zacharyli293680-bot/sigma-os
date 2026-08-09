/**
 * workbench.tsx — study mode's course dashboard, lesson view and practice
 * engine (S1–S3 of the study plan).
 *
 * Two levels inside one component tree. The **chain view** (S2) is the course
 * dashboard: every active course, its guide chain rendered as the sequence it
 * is — done rows checked, skipped rows dimmed with their date, the frontier
 * carrying the three actions (study, complete, skip). Completion goes through
 * the same POST /api/tasks/toggle as any checkbox — a human click, one commit,
 * one ledger row — and skip through POST /api/tasks/skip, so undo is the
 * ledger's revert either way. The **lesson view** (S1) renders one module,
 * now with the practice engine (S3): staged hints, numeric auto-check with
 * tolerance, MCQ letter checks, self-assessed reveal for short/proof/code,
 * per-question skip, every resolution appended to the attempt log.
 *
 * All view state lives in one reducer on purpose (the plan's §11/§15); the
 * reducer state mirrors to the machine-local sidecar (POST /api/lesson/state)
 * so a reopened module resumes, and the durable trace is the session rollup —
 * fired by the foot's button and by every close path via the open-effect's
 * cleanup, idempotent server-side through the watermark.
 *
 * Esc peels in arrival order — focus, dock, module → chain view — in the
 * capture phase; the chain view lets Esc through to App's ladder to close.
 * A module whose parse reports problems is HELD and never rendered as if fine.
 */
import { useEffect, useReducer, useRef, useState } from "react";
import { get, obsidianHref, post, ApiError } from "./api";
import type {
  Courses, Guide, GuideRow, Lesson, LessonList, LessonListRow,
  LessonSegment, PracticeItem, Rollup,
} from "./api";

type Depth = "summary" | "normal" | "in_depth";
const DEPTH_LABEL: [Depth, string][] = [
  ["summary", "SUMMARY"], ["normal", "NORMAL"], ["in_depth", "IN DEPTH"]];
const DEPTHS: Depth[] = ["summary", "normal", "in_depth"];

type Result = "correct" | "wrong" | "skipped";
type PracticeSt = {
  hints: number;            // how many hints are open
  revealed: boolean;        // the answer was shown before resolving
  result: Result | null;    // resolved state — null while still live
  given: string;            // what was typed or picked
};
const P0: PracticeSt = { hints: 0, revealed: false, result: null, given: "" };

type WbState = {
  seg: number;                       // index into segments
  depth: Record<number, Depth>;      // per-segment and sticky…
  fallback: Depth;                   // …over a course-level default
  focus: boolean;                    // dock collapsed, column centred
  dock: boolean;                     // the provenance slot
  practice: Record<string, PracticeSt>;
};
type WbAction =
  | { t: "seg"; i: number }
  | { t: "depth"; i: number; d: Depth }
  | { t: "focus" }
  | { t: "dock"; open: boolean }
  | { t: "reset" }
  | { t: "hydrate"; depth: Record<number, Depth>; fallback: Depth;
      practice: Record<string, PracticeSt> }
  | { t: "hint"; qid: string }
  | { t: "reveal"; qid: string }
  | { t: "given"; qid: string; text: string }
  | { t: "result"; qid: string; r: Result };

const START: WbState = {
  seg: 0, depth: {}, fallback: "normal", focus: false, dock: true, practice: {},
};

function pr(st: WbState, qid: string): PracticeSt {
  return st.practice[qid] ?? P0;
}

function reduce(st: WbState, a: WbAction): WbState {
  switch (a.t) {
    case "seg": return { ...st, seg: a.i };
    case "depth": return { ...st, depth: { ...st.depth, [a.i]: a.d } };
    case "focus": return { ...st, focus: !st.focus };
    case "dock": return { ...st, dock: a.open };
    case "reset": return START;
    case "hydrate":
      return { ...st, depth: a.depth, fallback: a.fallback, practice: a.practice };
    case "hint":
      return { ...st, practice: { ...st.practice,
        [a.qid]: { ...pr(st, a.qid), hints: pr(st, a.qid).hints + 1 } } };
    case "reveal":
      return { ...st, practice: { ...st.practice,
        [a.qid]: { ...pr(st, a.qid), revealed: true } } };
    case "given":
      return { ...st, practice: { ...st.practice,
        [a.qid]: { ...pr(st, a.qid), given: a.text } } };
    case "result":
      return { ...st, practice: { ...st.practice,
        [a.qid]: { ...pr(st, a.qid), result: a.r } } };
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

/** A numeric answer's expected value. The grammar stores answers as prose
 *  ("6/7 ≈ 0.857", "−120 N", "90°"), so this prefers the decimal after ≈,
 *  evaluates a leading fraction, normalises the unicode minus, and takes the
 *  first number otherwise. null means "not machine-checkable" and the item
 *  degrades to the self-assessed path rather than faking a grade. */
function parseNumeric(s: string): number | null {
  const tail = (s || "").replace(/−/g, "-").split("≈").pop() ?? "";
  const frac = tail.match(/^\s*(-?\d+(?:\.\d+)?)\s*\/\s*(-?\d+(?:\.\d+)?)/);
  if (frac && Number(frac[2]) !== 0) return Number(frac[1]) / Number(frac[2]);
  const m = tail.match(/-?\d+(?:\.\d+)?/);
  return m ? Number(m[0]) : null;
}

function mcqLetters(prompt: string): string[] {
  return [...new Set([...prompt.matchAll(/\(([a-h])\)/g)].map(m => m[1]))];
}

function mcqAnswer(answer: string): string {
  return (answer || "").trim().toLowerCase().replace(/[()]/g, "").slice(0, 1);
}

/** One practice item, live. Auto-checked where the decision list allows
 *  (MCQ, numeric with tolerance), self-assessed everywhere else — a model
 *  never grades free response, and neither does a regex pretending to. */
function PracticeBox({ it, st, onHint, onReveal, onGiven, onResolve }: {
  it: PracticeItem; st: PracticeSt;
  onHint: () => void; onReveal: () => void;
  onGiven: (s: string) => void;
  onResolve: (r: Result, given?: string) => void;
}) {
  const expected = it.kind === "numeric" ? parseNumeric(it.answer ?? "") : null;
  const letters = it.kind === "mcq" ? mcqLetters(it.prompt) : [];
  const autoNumeric = it.kind === "numeric" && expected !== null;
  const autoMcq = it.kind === "mcq" && letters.length >= 2 && !!mcqAnswer(it.answer ?? "");
  const auto = autoNumeric || autoMcq;
  const done = st.result !== null;

  const check = () => {
    const u = parseNumeric(st.given);
    if (u === null || expected === null) return;
    const ok = Math.abs(u - expected) <= Math.max(0.01, Math.abs(expected) * 0.015);
    onResolve(ok ? "correct" : "wrong", st.given);
  };

  return (
    <li className={`wb-q ${done ? `wb-q-${st.result}` : ""}`}>
      <div className="wb-q-head">
        <span className="wb-kind">{it.id} · {it.kind}</span>
        {done && (
          <span className={`wb-q-mark ${st.result}`}>
            {st.result === "correct" ? "✓ correct"
              : st.result === "wrong" ? "✗ missed" : "− skipped"}
          </span>
        )}
      </div>
      <Rich text={it.prompt} />

      {st.hints > 0 && (
        <ul className="wb-hints">
          {it.hints.slice(0, st.hints).map((h, i) => (
            <li key={i}><Inline text={h} /></li>
          ))}
        </ul>
      )}

      {!done && (
        <div className="wb-q-act">
          {it.hints.length > st.hints && (
            <button className="ghost" onClick={onHint}
                    title="one hint at a time — each opened hint is recorded with the attempt">
              hint {st.hints + 1}/{it.hints.length}
            </button>
          )}
          {autoNumeric && (
            <>
              <input className="wb-num" value={st.given} placeholder="answer…"
                     aria-label={`answer for ${it.id}`}
                     onChange={e => onGiven(e.target.value)}
                     onKeyDown={e => { if (e.key === "Enter") check(); }} />
              <button className="ghost" disabled={!st.given.trim()} onClick={check}>
                check
              </button>
            </>
          )}
          {autoMcq && letters.map(l => (
            <button key={l} className="ghost wb-mcq"
                    onClick={() => {
                      onGiven(l);
                      onResolve(l === mcqAnswer(it.answer ?? "") ? "correct" : "wrong", l);
                    }}>
              ({l})
            </button>
          ))}
          {!auto && !st.revealed && (
            <button className="ghost" onClick={onReveal}>reveal answer</button>
          )}
          {!auto && st.revealed && (
            <>
              <button className="ghost" onClick={() => onResolve("correct")}>✓ I had it</button>
              <button className="ghost" onClick={() => onResolve("wrong")}>✗ I missed it</button>
            </>
          )}
          <button className="ghost wb-q-skip" onClick={() => onResolve("skipped")}
                  title="recorded as skipped — visible in the attempt log, never counted as missed">
            skip
          </button>
        </div>
      )}

      {(st.revealed || (done && st.result !== "skipped")) && (
        <div className="wb-solution">
          <p><b>Answer:</b> <Inline text={it.answer ?? ""} /></p>
          {it.solution && <Rich text={it.solution} />}
        </div>
      )}
    </li>
  );
}

function Segment({ seg, depth, st, onDepth, dispatch, onResolve }: {
  seg: LessonSegment; depth: Depth; st: WbState;
  onDepth: (d: Depth) => void;
  dispatch: (a: WbAction) => void;
  onResolve: (it: PracticeItem, r: Result, given?: string) => void;
}) {
  const resolved = seg.practice.filter(it => pr(st, it.id).result !== null).length;
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
          <h4>Practice — {resolved}/{seg.practice.length}</h4>
          <ul>
            {seg.practice.map(it => (
              <PracticeBox key={it.id} it={it} st={pr(st, it.id)}
                onHint={() => dispatch({ t: "hint", qid: it.id })}
                onReveal={() => dispatch({ t: "reveal", qid: it.id })}
                onGiven={s => dispatch({ t: "given", qid: it.id, text: s })}
                onResolve={(r, given) => onResolve(it, r, given)} />
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

/** A chain row's text, cleaned for display: the wikilink reduced to its
 *  label, the skipped:: marker lifted out (it renders as a chip instead). */
function rowText(r: GuideRow): string {
  return r.text
    .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, t, l) => l ?? t.split("/").pop())
    .replace(/skipped::\d{4}-\d{2}-\d{2}/, "")
    .replace(/\s+/g, " ").trim();
}

export default function WorkbenchView({ open, vault, onClose }: {
  open: boolean; vault: string; onClose: () => void;
}) {
  const [list, setList] = useState<LessonList | null | undefined>(undefined);
  const [courses, setCourses] = useState<Courses | null | undefined>(undefined);
  const [course, setCourse] = useState<string | null>(null);
  const [guide, setGuide] = useState<Guide | null | undefined>(undefined);
  const [picked, setPicked] = useState<LessonListRow | null>(null);
  const [lesson, setLesson] = useState<Lesson | null | undefined>(undefined);
  const [st, dispatch] = useReducer(reduce, START);
  const [busy, setBusy] = useState(false);
  const [chainErr, setChainErr] = useState<string | null>(null);
  const [last, setLast] = useState<{ sha: string; what: string } | null>(null);
  const [rollup, setRollup] = useState<Rollup | "busy" | string | null>(null);
  // Which courses have un-rolled-up attempts this session. A ref, not state:
  // nothing renders from it except the end-session button's presence, and the
  // close path reads it during cleanup when state is already torn down.
  const touched = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!open) return;
    dispatch({ t: "reset" });
    setList(undefined); setCourses(undefined); setCourse(null);
    setGuide(undefined); setPicked(null); setLesson(undefined);
    setChainErr(null); setLast(null); setRollup(null);
    get<LessonList>("lesson").then(setList).catch(() => setList(null));
    get<Courses>("courses").then(c => {
      setCourses(c);
      // Default to the first course that has a chain, then the first with
      // any authored module — the pilot case either way.
      const first = c.courses.find(x => x.guide) ?? c.courses.find(x => x.modules > 0);
      setCourse(first ? first.course : null);
    }).catch(() => setCourses(null));
    // Every close path — Esc through App's ladder included — rolls the
    // session up. Idempotent server-side, so racing the foot's button is fine.
    // The Set object itself is stable (only its contents change), so capturing
    // it here reads its contents as they are at close time.
    const pending = touched.current;
    return () => {
      for (const c of pending) {
        void post("lesson/session-end", { course: c }).catch(() => {});
      }
      pending.clear();
    };
  }, [open]);

  const fetchGuide = (c: string) =>
    get<Guide>(`guide/${c}`).then(setGuide).catch(() => setGuide(null));

  useEffect(() => {
    if (!open || !course) return;
    setGuide(undefined);
    void fetchGuide(course);
  }, [open, course]);

  useEffect(() => {
    if (!open || !picked) return;
    // Reset per-module view state: a surviving segment index from a longer
    // module would point past the end of a shorter one.
    dispatch({ t: "reset" });
    setLesson(undefined);
    get<Lesson>(`lesson/${picked.course}/${picked.module}`).then(l => {
      setLesson(l);
      // Resume where this module was left: the sidecar state rides on the
      // lesson payload. Validated field by field — it is machine-local JSON
      // a hand or an old build may have shaped differently.
      const s = l.state;
      if (!s) return;
      const depth: Record<number, Depth> = {};
      for (const [k, v] of Object.entries(s.depth ?? {})) {
        if (DEPTHS.includes(v as Depth)) depth[Number(k)] = v as Depth;
      }
      const practice: Record<string, PracticeSt> = {};
      for (const [qid, p] of Object.entries(s.practice ?? {})) {
        practice[qid] = {
          hints: typeof p.hints === "number" ? p.hints : 0,
          revealed: !!p.revealed,
          result: p.result === "correct" || p.result === "wrong"
            || p.result === "skipped" ? p.result : null,
          given: typeof p.given === "string" ? p.given : "",
        };
      }
      dispatch({ t: "hydrate", depth,
                 fallback: DEPTHS.includes(s.fallback as Depth)
                   ? s.fallback as Depth : "normal",
                 practice });
    }).catch(() => setLesson(null));
  }, [open, picked]);

  // Mirror view state to the sidecar, debounced — resume is the feature,
  // never a commit. Depth keys become strings in JSON; hydrate converts back.
  const rendered = !!lesson && lesson.problems.length === 0;
  useEffect(() => {
    if (!open || !rendered || !lesson) return;
    const id = window.setTimeout(() => {
      void post("lesson/state", {
        course: lesson.course, module: lesson.module,
        state: { depth: st.depth, fallback: st.fallback, practice: st.practice },
      }).catch(() => {});
    }, 600);
    return () => window.clearTimeout(id);
  }, [open, rendered, lesson, st.depth, st.fallback, st.practice]);

  // Only intercept keys while a lesson body is actually rendered — the chain
  // view owns no focus or dock, and its Esc belongs to App's ladder.
  useEffect(() => {
    if (!open || !rendered) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.ctrlKey || e.altKey || e.metaKey) return;
      // An overlay stacked above the workbench (Capture autofocuses its
      // textarea) must get its own keys — a capture-phase listener fires
      // before the target's handlers, so check where focus actually is.
      const el = document.activeElement as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
      if (e.key === "f") {
        e.preventDefault();
        dispatch({ t: "focus" });
      } else if (e.key === "Escape") {
        if (st.focus) dispatch({ t: "focus" });
        else if (st.dock) dispatch({ t: "dock", open: false });
        else { setPicked(null); setLesson(undefined); }   // module → chain view
        e.preventDefault();
        e.stopPropagation();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, rendered, st.focus, st.dock]);

  if (!open) return null;

  const seg = lesson?.segments[st.seg];
  const depth = st.depth[st.seg] ?? st.fallback;
  const held = (lesson?.problems.length ?? 0) > 0;

  // Chain rows address modules by wikilink target; the lesson list addresses
  // them by (course, module). Basenames are vault-unique, so they join there.
  const byBase = new Map<string, LessonListRow>(
    (list?.modules ?? []).map(m =>
      [m.file.split("/").pop()!.replace(/\.md$/, ""), m]));
  const rowModule = (r: GuideRow): LessonListRow | null =>
    r.target ? byBase.get(r.target.split("/").pop()!) ?? null : null;

  const act = async (what: string, path: string, body: unknown) => {
    setBusy(true); setChainErr(null);
    try {
      const r = await post<{ sha: string }>(path, body);
      setLast({ sha: r.sha, what });
      if (course) await fetchGuide(course);
      get<Courses>("courses").then(setCourses).catch(() => {});
    } catch (e) {
      setChainErr(e instanceof ApiError
        ? `${e.code}${e.detail ? ` — ${e.detail}` : ""}` : "write failed");
    } finally {
      setBusy(false);
    }
  };

  const undo = async () => {
    if (!last) return;
    setBusy(true); setChainErr(null);
    try {
      await post("activity/revert", { sha: last.sha });
      setLast(null);
      if (course) await fetchGuide(course);
      get<Courses>("courses").then(setCourses).catch(() => {});
    } catch (e) {
      setChainErr(e instanceof ApiError
        ? `${e.code}${e.detail ? ` — ${e.detail}` : ""}` : "undo failed");
    } finally {
      setBusy(false);
    }
  };

  const resolve = (it: PracticeItem, r: Result, given?: string) => {
    if (!lesson || pr(st, it.id).result !== null) return;
    dispatch({ t: "result", qid: it.id, r });
    touched.current.add(lesson.course);
    void post("lesson/attempt", {
      course: lesson.course, module: lesson.module, qid: it.id, result: r,
      hints: pr(st, it.id).hints, revealed: pr(st, it.id).revealed,
      answer: given ?? null,
    }).catch(() => {});
  };

  const endSession = async () => {
    const c = lesson?.course ?? course;
    if (!c) return;
    setRollup("busy");
    try {
      const r = await post<Rollup>("lesson/session-end", { course: c });
      setRollup(r);
      if (r.wrote) touched.current.delete(c);
    } catch (e) {
      setRollup(e instanceof ApiError
        ? `${e.code}${e.detail ? ` — ${e.detail}` : ""}` : "rollup failed");
    }
  };

  const inLesson = picked !== null;
  const row = courses?.courses.find(c => c.course === course) ?? null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className={`workbench ${st.focus ? "focus" : ""}`}
           onClick={e => e.stopPropagation()} role="dialog" aria-label="Workbench">
        <header className="study-head">
          <span className="label">
            ◇ STUDY — {inLesson ? "WORKBENCH" : "COURSES"}
            {inLesson && lesson
              ? ` · ${lesson.course} M${String(lesson.module ?? "?").padStart(2, "0")} — ${lesson.title}`
              : ""}
            {inLesson && lesson?.estimate
              ? <span className="dim"> · {lesson.estimate} min</span> : null}
          </span>
          <span>
            {inLesson && (
              <button className="ghost" title="Back to the course chain (Esc)"
                      onClick={() => { setPicked(null); setLesson(undefined); }}>
                ⟵ chain
              </button>
            )}
            {touched.current.size > 0 && (
              <button className="ghost" onClick={endSession}
                      title="Roll this session up into the study log — one commit, digest included">
                ▣ end session
              </button>
            )}
            {inLesson && (
              <button className="ghost" onClick={() => dispatch({ t: "focus" })}
                      title="Focus mode — collapse the dock (f)">
                {st.focus ? "◫ dock" : "▣ focus"}
              </button>
            )}
            <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
          </span>
        </header>

        {/* ------------------------------------------------ chain view (S2) */}
        {!inLesson && (
          <div className="wb-chain">
            {courses === undefined && <p className="dim pad">reading…</p>}
            {courses === null && <p className="err pad">backend unreachable</p>}
            {courses && courses.courses.length === 0 && (
              <p className="dim pad">no active courses</p>
            )}
            {courses && courses.courses.length > 0 && (
              <div className="wb-picker">
                {courses.courses.map(c => (
                  <button key={c.course}
                          className={c.course === course ? "active" : ""}
                          onClick={() => setCourse(c.course)}
                          title={c.name !== c.course ? c.name : undefined}>
                    {c.course}
                    {c.guide
                      ? <span className="wb-min"> {c.guide.done}/{c.guide.total}</span>
                      : null}
                  </button>
                ))}
              </div>
            )}

            {course && guide === undefined && <p className="dim pad">reading the chain…</p>}
            {course && guide === null && (
              <div className="pad">
                <p className="dim">
                  no guide chain yet — a chain is
                  a <code>{course.toLowerCase()}-guide.md</code> note at the
                  course root, one checkbox row per module
                </p>
                {row && row.modules > 0 && list && (
                  <>
                    <p className="dim">authored modules, unchained:</p>
                    <div className="wb-picker">
                      {list.modules
                        .filter(m => m.course === course)
                        .map(m => (
                          <button key={m.file} disabled={m.module == null}
                                  onClick={() => setPicked(m)}>
                            M{String(m.module ?? "?").padStart(2, "0")} · {m.title}
                          </button>
                        ))}
                    </div>
                  </>
                )}
              </div>
            )}

            {guide && (
              <div className="wb-chainbody">
                <p className="wb-chainhead">
                  <span className="dim">
                    {guide.done} done · {guide.skipped} skipped
                    · {guide.total - guide.done - guide.skipped} open
                    {guide.blueprint ? ` · blueprint ${guide.blueprint}` : ""}
                  </span>
                  <a href={obsidianHref(vault, guide.file)}
                     title="the chain note — markdown is the truth">
                    {guide.file.split("/").pop()}
                  </a>
                </p>
                {last && (
                  <p className="wb-last">
                    ✓ {last.what} · <code>{last.sha.slice(0, 7)}</code>
                    <button className="ghost" onClick={undo} disabled={busy}
                            title="git revert of that one commit — the ledger's own undo">
                      undo
                    </button>
                  </p>
                )}
                {chainErr && <p className="err">{chainErr}</p>}
                <ul className="wb-rows">
                  {guide.rows.map(r => {
                    const m = rowModule(r);
                    const frontier = guide.frontier?.line === r.line;
                    const openable = m != null && m.module != null;
                    return (
                      <li key={r.line}
                          className={`wb-row wb-row-${r.state} ${frontier ? "wb-frontier" : ""}`}>
                        <span className="wb-row-glyph">
                          {r.state === "done" ? "✓" : r.state === "skipped" ? "−"
                            : frontier ? "▸" : "·"}
                        </span>
                        <span className="wb-row-text">
                          {openable
                            ? <button className="wb-row-open" onClick={() => setPicked(m)}
                                      title={`open ${m.file}`}>{rowText(r)}</button>
                            : <span title="module not authored yet">{rowText(r)}</span>}
                          {r.state === "skipped" && r.skipped && (
                            <em className="wb-chip">skipped {r.skipped}</em>
                          )}
                          {r.date && <em className="wb-chip">📅 {r.date}</em>}
                          {!openable && r.state === "open" && (
                            <em className="wb-chip dim">not written yet</em>
                          )}
                        </span>
                        {frontier && (
                          <span className="wb-row-act">
                            {openable && (
                              <button className="ghost" disabled={busy}
                                      onClick={() => setPicked(m)}>study</button>
                            )}
                            <button className="ghost" disabled={busy}
                                    title="tick the row — one commit, revertible from the ledger"
                                    onClick={() => void act(`completed ${rowText(r)}`,
                                      "tasks/toggle",
                                      { file: guide.file, line: r.line, raw: r.raw, done: true })}>
                              ✓ complete
                            </button>
                            <button className="ghost" disabled={busy}
                                    title="flip to [-] skipped::today — the frontier moves on, the row stays"
                                    onClick={() => void act(`skipped ${rowText(r)}`,
                                      "tasks/skip",
                                      { file: guide.file, line: r.line, raw: r.raw })}>
                              → skip
                            </button>
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
                {!guide.frontier && (
                  <p className="dim pad">no open module — the chain is complete</p>
                )}
              </div>
            )}
          </div>
        )}

        {/* ----------------------------------------------- lesson view (S1) */}
        {inLesson && lesson === undefined && <p className="dim pad">parsing…</p>}
        {inLesson && lesson === null && <p className="err pad">backend unreachable</p>}

        {inLesson && lesson && held && (
          <div className="wb-held">
            <h3>HELD — this module fails the grammar</h3>
            <p className="dim">A malformed module is a broken lesson that would read
            as shipped, so it is named instead of rendered:</p>
            <ul>{lesson.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
            <button className="ghost" onClick={() => { setPicked(null); setLesson(undefined); }}>
              ⟵ back to the chain
            </button>
          </div>
        )}

        {inLesson && lesson && !held && (
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
                  <Segment seg={seg} depth={depth} st={st}
                           onDepth={d => dispatch({ t: "depth", i: st.seg, d })}
                           dispatch={dispatch} onResolve={resolve} />
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
          </>
        )}

        <footer className="wb-foot dim">
          {inLesson
            ? <span><kbd>f</kbd> focus · <kbd>Esc</kbd> peels dock, focus, then the chain</span>
            : <span><kbd>Esc</kbd> closes · completing goes through the same toggle as any task</span>}
          {rollup === "busy" && <span className="wb-roll">rolling up…</span>}
          {rollup && rollup !== "busy" && typeof rollup !== "string" && (
            <span className="wb-roll">
              {rollup.wrote
                ? <>rolled up · {rollup.raw?.replace(/^- /, "")} · <code>{rollup.sha?.slice(0, 7)}</code></>
                : "nothing to roll up"}
            </span>
          )}
          {typeof rollup === "string" && rollup !== "busy" && (
            <span className="wb-roll err">{rollup}</span>
          )}
        </footer>
      </div>
    </div>
  );
}
