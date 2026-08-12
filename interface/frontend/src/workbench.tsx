/**
 * workbench.tsx — study mode's course dashboard, lesson view, practice
 * engine, dock and tutor (S1–S6 of the study plan).
 *
 * Two levels inside one component tree. The **chain view** (S2) is the course
 * dashboard: every active course, its guide chain rendered as the sequence it
 * is — done rows checked, skipped rows dimmed with their date, the frontier
 * carrying the three actions (study, complete, skip). Completion goes through
 * the same POST /api/tasks/toggle as any checkbox — a human click, one commit,
 * one ledger row — and skip through POST /api/tasks/skip, so undo is the
 * ledger's revert either way. The **lesson view** renders one module (S1)
 * or one checkpoint (S6 — same grammar, practice only, no depth levels),
 * with the practice engine (S3): staged hints, numeric auto-check with
 * tolerance, MCQ letter checks, self-assessed reveal for short/proof/code,
 * per-question skip, every resolution appended to the attempt log.
 *
 * The dock holds exactly one slot at a time (§11): provenance, the work pad,
 * the code sandbox (S4 — Pyodide + sql.js, vendored, lazy-loaded, failures
 * reported as failures), or the tutor (S5 — the backend pins course/module/
 * segment/item context on every message; three modes; window-held exactly as
 * the palette's model verbs are; module corrections arrive as proposals).
 *
 * All view state lives in one reducer on purpose (the plan's §11/§15); the
 * reducer state mirrors to the machine-local sidecar (POST /api/lesson/state)
 * so a reopened module resumes, and the durable trace is the session rollup —
 * fired by the foot's button and by every close path via the open-effect's
 * cleanup, idempotent server-side through the watermark.
 *
 * Esc peels in arrival order — focus, dock, module → chain, chain → the course
 * grid — in the capture phase; only the grid lets Esc through to App's ladder
 * to close. A module whose parse reports problems is HELD, never rendered as if
 * fine.
 *
 * Three levels, one component, two files. `course === null` is the grid
 * (courses.tsx), `course` set is that course's chain, `picked` set is a lesson.
 * The grid is presentational plus one POST; everything stateful stays here,
 * because the reducer, the study clock's refs and the generation feed's
 * settle-refetch all have to survive switching between courses.
 */
import { useEffect, useReducer, useRef, useState } from "react";
import type { CSSProperties, Dispatch, ReactNode, SetStateAction } from "react";
import { API, get, obsidianHref, post, ApiError } from "./api";
import CoursesGrid from "./courses";
import { MathBlock, MathInline } from "./math";
import Figure from "./figure";
import type {
  CheckpointListRow, Courses, Guide, GuideProgress, GuideRow, Lesson,
  LessonList, LessonListRow, LessonSegment, PracticeItem, Rollup,
} from "./api";
import { pythonReady, resetSql, runPython, runSql, warmPython } from "./sandbox";
import Calculator from "./calc";
import { useTheme } from "./theme";
import type { PyRun, SqlRun } from "./sandbox";

type Depth = "summary" | "normal" | "in_depth";
/* Sentence case. These were shouted because the dashboard shouts — its labels
 * are 10.5px mono caps so that forty of them read as one texture. The room has
 * three of them, in a control the width of the rail, where caps cost a third
 * more space for no legibility at all. */
const DEPTH_LABEL: [Depth, string][] = [
  ["summary", "Summary"], ["normal", "Normal"], ["in_depth", "In depth"]];
const DEPTHS: Depth[] = ["summary", "normal", "in_depth"];

type Result = "correct" | "wrong" | "skipped";
type PracticeSt = {
  hints: number;            // how many hints are open
  revealed: boolean;        // the answer was shown before resolving
  result: Result | null;    // resolved state — null while still live
  given: string;            // what was typed or picked
};
const P0: PracticeSt = { hints: 0, revealed: false, result: null, given: "" };

/** The dock's slots — exactly one open at a time (§11). Each carries a glyph
 *  and a word: the rail has the room for both, and a row of glyphs alone is a
 *  puzzle every time you come back to it after a week. */
type Slot = "prov" | "work" | "code" | "tutor" | "calc";
const SLOT_LABEL: [Slot, string, string][] = [
  ["prov", "?", "Provenance"],
  ["work", "✎", "Work pad"],
  ["code", "▸", "Sandbox"],
  ["tutor", "✦", "Tutor"],
  ["calc", "∑", "Calculator"],
];

type Lang = "python" | "sql";
const SCRATCH_MAX = 4000;

// The study clock (S8). Five minutes of no pointer and no key is a coffee, not
// study; fifteen seconds is fine granularity for a number that gets rounded to
// minutes, and cheap enough to run for hours.
const IDLE_MS = 5 * 60_000;
const TICK_MS = 15_000;

type WbState = {
  seg: number;                       // index into segments
  depth: Record<number, Depth>;      // per-segment and sticky…
  fallback: Depth;                   // …over a course-level default
  focus: boolean;                    // dock collapsed, column centred
  dock: Slot | null;                 // which slot is open, if any
  practice: Record<string, PracticeSt>;
  scratch: string;                   // the work pad — sidecar-persisted
  lastQid: string | null;            // what the tutor pins — last touched item
  code: { lang: Lang; text: string };  // the sandbox editor — transient
};
type WbAction =
  | { t: "seg"; i: number }
  | { t: "depth"; i: number; d: Depth }
  | { t: "focus" }
  | { t: "dock"; slot: Slot | null }
  | { t: "reset" }
  | { t: "hydrate"; depth: Record<number, Depth>; fallback: Depth;
      practice: Record<string, PracticeSt>; scratch: string }
  | { t: "hint"; qid: string }
  | { t: "reveal"; qid: string }
  | { t: "given"; qid: string; text: string }
  | { t: "result"; qid: string; r: Result }
  | { t: "scratch"; text: string }
  | { t: "code"; lang?: Lang; text?: string };

const START: WbState = {
  // The dock starts CLOSED: reading-first means the reading column has the
  // width until you ask for something else. Nothing to migrate — `dock` was
  // never in the sidecar, and `hydrate` merges exactly four fields (depth,
  // fallback, practice, scratch), so it cannot come back from disk. Keep that
  // list closed, or a module would reopen someone's old dock over the page.
  seg: 0, depth: {}, fallback: "normal", focus: false, dock: null,
  practice: {}, scratch: "", lastQid: null,
  code: { lang: "python", text: "" },
};

function pr(st: WbState, qid: string): PracticeSt {
  return st.practice[qid] ?? P0;
}

function reduce(st: WbState, a: WbAction): WbState {
  switch (a.t) {
    case "seg": return { ...st, seg: a.i };
    case "depth": return { ...st, depth: { ...st.depth, [a.i]: a.d } };
    case "focus": return { ...st, focus: !st.focus };
    case "dock": return { ...st, dock: a.slot };
    case "reset": return START;
    case "hydrate":
      return { ...st, depth: a.depth, fallback: a.fallback,
               practice: a.practice, scratch: a.scratch };
    case "hint":
      return { ...st, lastQid: a.qid, practice: { ...st.practice,
        [a.qid]: { ...pr(st, a.qid), hints: pr(st, a.qid).hints + 1 } } };
    case "reveal":
      return { ...st, lastQid: a.qid, practice: { ...st.practice,
        [a.qid]: { ...pr(st, a.qid), revealed: true } } };
    case "given":
      // Capped so the mirrored state blob stays far under the server's 20k
      // guard — a pasted derivation belongs in the work pad, and a silent
      // 413 would drop ALL resume state, not just the long answer.
      return { ...st, lastQid: a.qid, practice: { ...st.practice,
        [a.qid]: { ...pr(st, a.qid), given: a.text.slice(0, 500) } } };
    case "result":
      return { ...st, lastQid: a.qid, practice: { ...st.practice,
        [a.qid]: { ...pr(st, a.qid), result: a.r } } };
    case "scratch": return { ...st, scratch: a.text.slice(0, SCRATCH_MAX) };
    case "code": return { ...st, code: { lang: a.lang ?? st.code.lang,
                                         text: a.text ?? st.code.text } };
  }
}

/** One option line of a multiple-choice prompt: `A) …`, `(a) …`, `B. …`.
 *
 *  The authoring prompt asks for "options A)–D)", so that is the shape the
 *  notes are written in and the shape both readers here must accept. It is
 *  anchored to the start of a line on purpose: an unanchored `(a)` matches
 *  mid-sentence prose ("…the couple (a) is free…") and would turn a
 *  parenthetical into an answer option.
 *
 *  Deliberately not global — `test()` on a `/g` regex advances `lastIndex`
 *  between calls, so a shared one answers differently on alternate lines. */
const OPTION_RE = /^\(?([A-Ha-h])[).]\s+\S/;

/** The content grammar is markdown-lite by construction — paragraphs and
 *  4-space-indented formula blocks, with bold, backticks and wikilinks
 *  inline. Rendering it needs no library, and adding one for this would be
 *  the first runtime dependency beyond react itself. */
type Block = { k: "p" | "pre" | "math" | "opt"; text: string };

function Rich({ text }: { text: string }) {
  const blocks: Block[] = [];
  let para: string[] = [], pre: string[] = [];
  // Display maths is a *mode*, not a line test: an `aligned` environment
  // routinely runs to a dozen lines, and treating `$$` as a one-liner would
  // render the first line as maths and the rest as prose.
  let math: string[] | null = null;
  const flush = () => {
    if (para.length) { blocks.push({ k: "p", text: para.join(" ") }); para = []; }
    if (pre.length) { blocks.push({ k: "pre", text: pre.join("\n") }); pre = []; }
  };
  const closeMath = () => {
    blocks.push({ k: "math", text: (math ?? []).join("\n").trim() });
    math = null;
  };

  for (const line of text.split("\n")) {
    if (math !== null) {
      if (line.trimEnd().endsWith("$$")) {
        math.push(line.replace(/\$\$\s*$/, ""));
        closeMath();
      } else math.push(line);
      continue;
    }
    const t = line.trim();
    if (t.startsWith("$$")) {
      flush();
      const rest = t.slice(2);
      if (rest.trimEnd().endsWith("$$")) blocks.push({ k: "math", text: rest.replace(/\$\$\s*$/, "").trim() });
      else math = [rest];
      continue;
    }
    // The pre path stays exactly as it was, so a module still written in the
    // old Unicode style renders today the way it rendered yesterday.
    if (/^\s{4,}\S/.test(line)) {
      if (para.length) flush();
      pre.push(line.slice(4));
    } else if (!t) flush();
    else if (OPTION_RE.test(t)) {
      // An option list is the one place where a line break carries meaning:
      // joining these into a paragraph the way prose is joined rendered a
      // four-option question as "…points along: A) A × B B) B × A C) A · B".
      flush();
      blocks.push({ k: "opt", text: t });
    } else {
      if (pre.length) flush();
      para.push(t);
    }
  }
  // An unterminated `$$` is a typo in one note, not a reason to swallow the
  // rest of the segment — close it and render what there is.
  if (math !== null) closeMath();
  flush();

  return (
    <>
      {blocks.map((b, i) =>
        b.k === "pre" ? <pre key={i}>{b.text}</pre>
        : b.k === "math" ? <MathBlock key={i} tex={b.text} />
        : b.k === "opt" ? <p key={i} className="wb-opt"><Inline text={b.text} /></p>
        : <p key={i}><Inline text={b.text} /></p>)}
    </>
  );
}

function Inline({ text }: { text: string }) {
  // The code-span alternative deliberately precedes the maths one: `split`
  // consumes left to right, so a `$` inside backticks is claimed as code and
  // never seen as maths. `$PATH` in a shell snippet stays a shell variable.
  // `**bold**` precedes `*italic*` so the greedier pair wins; both precede the
  // maths alternative, and the code span precedes everything. Italics were
  // missing entirely, which is why a sourced sentence rendered as
  // "a magnitude *and* a direction" with the asterisks showing.
  const parts = text.split(
    /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\[\[[^\]]+\]\]|\$[^$\n]+\$)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**")) return <b key={i}>{p.slice(2, -2)}</b>;
        if (p.startsWith("*") && p.endsWith("*") && p.length > 2) {
          return <i key={i}>{p.slice(1, -1)}</i>;
        }
        if (p.startsWith("`") && p.endsWith("`")) return <code key={i}>{p.slice(1, -1)}</code>;
        if (p.startsWith("$") && p.endsWith("$") && p.length > 2) {
          return <MathInline key={i} tex={p.slice(1, -1)} />;
        }
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

/** The option letters a multiple-choice prompt actually offers.
 *
 *  This read `/\(([a-h])\)/g` — parenthesised and lower case — while the
 *  authoring prompt has always asked for `A)–D)`. So every generated mcq
 *  found zero options and silently degraded to the reveal-the-answer path:
 *  the grader was never wrong, it was never reached. One shape, read the
 *  same way by `Rich` above and by the grader here. */
function mcqLetters(prompt: string): string[] {
  const found = prompt.split("\n")
    .map(l => l.trim().match(OPTION_RE))
    .filter((m): m is RegExpMatchArray => m !== null)
    .map(m => m[1].toLowerCase());
  return [...new Set(found)];
}

/** The mcq answer as a gradeable letter, or null when the answer:: text is
 *  not exactly one option letter. The grammar only requires answer:: to be
 *  non-empty, so a prose answer ("The moment doubles, so (b)") is legal —
 *  and taking its first character would auto-grade every click wrong. Null
 *  degrades the item to the self-assessed path instead of faking a grade. */
function mcqAnswer(answer: string): string | null {
  const m = (answer || "").trim().toLowerCase().match(/^\(?([a-h])\)?$/);
  return m ? m[1] : null;
}

/** The first fenced code block of a `code` practice item, for the sandbox.
 *  Only python and sql are runnable (the §2 decision) — any other fence tag
 *  gets a "not runnable" label instead of being fed to the wrong engine,
 *  which would manufacture a Python SyntaxError out of perfectly good Java.
 *  No fence → the prompt itself, commented, so context rides along. */
function fenced(prompt: string):
  | { runnable: true; lang: Lang; code: string }
  | { runnable: false; tag: string } {
  const m = prompt.match(/```(\w+)?\r?\n([\s\S]*?)```/);
  if (m) {
    const tag = (m[1] || "").toLowerCase();
    if (tag === "sql")
      return { runnable: true, lang: "sql", code: m[2].replace(/\s+$/, "") + "\n" };
    if (tag === "" || tag === "python" || tag === "py")
      return { runnable: true, lang: "python", code: m[2].replace(/\s+$/, "") + "\n" };
    return { runnable: false, tag };
  }
  const commented = prompt.split("\n").map(l => (l.trim() ? `# ${l}` : "#")).join("\n");
  return { runnable: true, lang: "python", code: `${commented}\n\n` };
}

/** One practice item, live. Auto-checked where the decision list allows
 *  (MCQ, numeric with tolerance), self-assessed everywhere else — a model
 *  never grades free response, and neither does a regex pretending to. */
function PracticeBox({ it, st, onHint, onReveal, onGiven, onResolve, onSandbox }: {
  it: PracticeItem; st: PracticeSt;
  onHint: () => void; onReveal: () => void;
  onGiven: (s: string) => void;
  onResolve: (r: Result, given?: string) => void;
  onSandbox?: (lang: Lang, code: string) => void;
}) {
  const expected = it.kind === "numeric" ? parseNumeric(it.answer ?? "") : null;
  const letters = it.kind === "mcq" ? mcqLetters(it.prompt) : [];
  const ans = it.kind === "mcq" ? mcqAnswer(it.answer ?? "") : null;
  const autoNumeric = it.kind === "numeric" && expected !== null;
  // Auto-grade only when the answer is one letter AND that letter is among
  // the prompt's own options — an answer of (e) against options (a)–(d)
  // would make every click wrong, which is a broken grader, not a grade.
  const autoMcq = it.kind === "mcq" && letters.length >= 2
    && ans !== null && letters.includes(ans);
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
                      onResolve(l === ans ? "correct" : "wrong", l);
                    }}>
              ({l})
            </button>
          ))}
          {it.kind === "code" && onSandbox && (() => {
            const f = fenced(it.prompt);
            return f.runnable ? (
              <button className="ghost" title="load this item into the code dock"
                      onClick={() => onSandbox(f.lang, f.code)}>
                ▸ open in sandbox
              </button>
            ) : (
              <em className="wb-chip dim"
                  title="only python and sql run in the dock — everything else renders, never runs">
                {f.tag} — not runnable
              </em>
            );
          })()}
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

/**
 * One segment's content — and only its content.
 *
 * The heading and the depth chips used to live here, between the title and the
 * first sentence. They are now the caller's: the title belongs to the lede that
 * replaced the rail, and the depth is a preference you set once and rarely
 * revisit, so it sits at the foot of the reading column with the other chrome
 * rather than interrupting the thing you came to read.
 */
function Segment({ seg, depth, st, cp, dispatch, onResolve, onSandbox }: {
  seg: LessonSegment; depth: Depth; st: WbState;
  /** Checkpoint rendering: practice only — no depth tabs, no example (§5.4). */
  cp: boolean;
  dispatch: (a: WbAction) => void;
  onResolve: (it: PracticeItem, r: Result, given?: string) => void;
  onSandbox: (lang: Lang, code: string) => void;
}) {
  const resolved = seg.practice.filter(it => pr(st, it.id).result !== null).length;
  return (
    <>
      {/* Above the prose at every depth: the diagram is what the segment is
          about, and a reader who scrolls past it to find the words has been
          shown the argument in the wrong order. */}
      {seg.figure && <Figure svg={seg.figure.svg} caption={seg.figure.caption} />}
      {!cp && <Rich text={seg[depth]} />}
      {!cp && seg.example && (
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
                onResolve={(r, given) => onResolve(it, r, given)}
                onSandbox={onSandbox} />
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ dock */

/** The sandbox's output state. It lives in WorkbenchView, not in CodeDock:
 *  the dock unmounts on every slot switch and focus toggle, and a traceback
 *  that vanished because the user glanced at the work pad would be a failure
 *  silently dropped — the exact thing S4's done-when forbids. */
type SandSt = { busy: boolean; cold: boolean; py: PyRun | null; sq: SqlRun | null };
const SAND0: SandSt = { busy: false, cold: false, py: null, sq: null };

/** The code sandbox (S4). Pyodide and sql.js, vendored and lazy — the first
 *  run pays the load, and a failure is a traceback on screen, never a shrug. */
function CodeDock({ code, sand, setSand, dispatch }: {
  code: { lang: Lang; text: string };
  sand: SandSt;
  setSand: Dispatch<SetStateAction<SandSt>>;
  dispatch: (a: WbAction) => void;
}) {
  // Opening the slot on python overlaps the ~14 MB load with typing.
  useEffect(() => {
    if (code.lang === "python") warmPython();
  }, [code.lang]);

  const run = async () => {
    if (sand.busy || !code.text.trim()) return;
    if (code.lang === "python") {
      setSand(s => ({ ...s, busy: true, cold: !pythonReady() }));
      const r = await runPython(code.text);
      setSand(s => ({ ...s, busy: false, cold: false, py: r }));
    } else {
      setSand(s => ({ ...s, busy: true }));
      const r = await runSql(code.text);
      setSand(s => ({ ...s, busy: false, sq: r }));
    }
  };

  const out = code.lang === "python" ? sand.py : sand.sq;
  return (
    <div className="wb-code">
      <div className="wb-codebar">
        {(["python", "sql"] as Lang[]).map(l => (
          <button key={l} className={`ghost ${code.lang === l ? "active" : ""}`}
                  onClick={() => dispatch({ t: "code", lang: l })}>{l}</button>
        ))}
        <span className="dim wb-codenote"
              title="pyodide + sql.js are vendored into the build — no CDN. Code runs in this tab with the page's own powers; run only code you trust.">
          local runtimes
        </span>
        {code.lang === "sql" && (
          <button className="ghost" title="drop the session database"
                  onClick={() => { resetSql(); setSand(s => ({ ...s, sq: null })); }}>
            reset db
          </button>
        )}
        <button className="ghost" disabled={sand.busy || !code.text.trim()} onClick={run}>
          {sand.busy ? "running…" : "▶ run"}
        </button>
      </div>
      <textarea className="wb-editor" value={code.text} spellCheck={false}
                aria-label="sandbox editor"
                placeholder={code.lang === "python"
                  ? "# python — e.g. compute a resultant, a moment, a unit vector…"
                  : "-- sql — the database persists between runs; reset db to start over"}
                onChange={e => dispatch({ t: "code", text: e.target.value })}
                onKeyDown={e => {
                  if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); void run(); }
                }} />
      <div className="wb-codeout">
        {sand.busy && sand.cold && (
          <p className="dim">loading the vendored Python runtime — first run
          only, ~14 MB from this machine…</p>
        )}
        {code.lang === "python" && sand.py && (
          <>
            {sand.py.stdout && <pre className="wb-out">{sand.py.stdout}</pre>}
            {sand.py.result !== null && <p className="wb-res">→ {sand.py.result}</p>}
            {sand.py.error && <pre className="wb-out wb-outerr">{sand.py.error}</pre>}
            <p className="dim">{sand.py.ok ? "ok" : "failed"} · {sand.py.ms} ms</p>
          </>
        )}
        {code.lang === "sql" && sand.sq && (
          <>
            {sand.sq.tables.map((t, i) => (
              <table key={i} className="wb-sqltab">
                <thead><tr>{t.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>
                  {t.values.map((row, j) => (
                    <tr key={j}>{row.map((v, k) => <td key={k}>{v}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            ))}
            {sand.sq.ok && sand.sq.tables.length === 0 && (
              <p className="dim">ok — no result set (statement ran)</p>
            )}
            {sand.sq.error && <pre className="wb-out wb-outerr">{sand.sq.error}</pre>}
            <p className="dim">{sand.sq.ok ? "ok" : "failed"} · {sand.sq.ms} ms</p>
          </>
        )}
        {!out && !sand.busy && (
          <p className="dim">python and sql run here; other languages render
          in lessons but are labelled not runnable. Ctrl+Enter runs.</p>
        )}
      </div>
    </div>
  );
}

type TutorMode = "nudge" | "explain" | "solve";
type TutorTurn = {
  q: string; a: string;
  tools: { name: string; detail: string }[];
  blocked: string[];
  proposals: string[];              // propose_change titles — the S5 done-when
  error?: string;
};

/** The tutor slot (S5). The pin line is what the backend will assemble the
 *  context from — shown so what the tutor knows is never a mystery. */
function TutorDock({ turns, busy, hold, mode, pin, onMode, onAsk }: {
  turns: TutorTurn[]; busy: boolean; hold: string | null;
  mode: TutorMode; pin: string;
  onMode: (m: TutorMode) => void;
  onAsk: (q: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); },
            [turns, busy]);
  return (
    <div className="wb-tutor">
      <div className="wb-modes" role="tablist" aria-label="Tutor mode">
        {(["nudge", "explain", "solve"] as TutorMode[]).map(m => (
          <button key={m} role="tab" aria-selected={mode === m}
                  className={mode === m ? "active" : ""}
                  title={m === "nudge" ? "one question back, never the answer"
                    : m === "explain" ? "re-teach it differently"
                    : "walk it through to the answer"}
                  onClick={() => onMode(m)}>{m}</button>
        ))}
      </div>
      <p className="dim wb-pin" title="the backend assembles this context onto every message">
        pinned: {pin}
      </p>
      {hold && <p className="wb-hold">⏸ {hold}</p>}
      <div className="wb-turns">
        {turns.length === 0 && !hold && (
          <p className="dim">Ask about the open segment or the pinned item.
          Nudge mode answers with a question, on purpose.</p>
        )}
        {turns.map((t, i) => (
          <div key={i} className="wb-turn">
            <p className="wb-turn-q">{t.q}</p>
            {t.tools.length > 0 && (
              <p className="dim wb-turn-trail">
                {t.tools.map(x => x.detail || x.name).join(" · ")}
              </p>
            )}
            {t.blocked.map((b, j) => (
              <p key={j} className="dim wb-turn-trail">🔒 {b}</p>
            ))}
            {t.proposals.map((p, j) => (
              <p key={j} className="wb-proposal">
                ✎ proposal raised: <b>{p}</b> — pending in WAITING ON YOU, nothing
                applied
              </p>
            ))}
            <div className="wb-turn-a">
              {t.a ? <Rich text={t.a} />
                : busy && i === turns.length - 1
                ? <span className="dim">thinking…</span> : null}
            </div>
            {t.error && <p className="err">{t.error}</p>}
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <form className="wb-tutorform" onSubmit={e => {
        e.preventDefault();
        const q = draft.trim();
        if (!q || busy || hold) return;
        setDraft("");
        onAsk(q);
      }}>
        <input value={draft} onChange={e => setDraft(e.target.value)}
               disabled={busy || !!hold}
               placeholder={hold ? "held — the window is reserved"
                 : busy ? "thinking…" : `ask (${mode})…`} />
        <button type="submit" className="ghost" disabled={busy || !!hold || !draft.trim()}>
          ask
        </button>
      </form>
    </div>
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

/** What a chain row can open: a module or a checkpoint, joined by wikilink
 *  target basename — basenames are vault-unique, so the join is exact. */
type Openable = {
  kind: "module" | "checkpoint";
  course: string; num: number; file: string; title: string;
};

/** What a checkpoint assesses, as a person would say it. `covers` is a list of
 *  module numbers, and it is contiguous in every blueprint written so far — but
 *  the schema allows a comma list, so a gap must print as one rather than
 *  quietly widening into a range that claims modules it does not test. */
function coversLabel(covers: number[]): string {
  if (!covers.length) return "nothing yet";
  const pad = (n: number) => `M${String(n).padStart(2, "0")}`;
  const runs: number[][] = [];
  for (const n of [...covers].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && n === last[last.length - 1] + 1) last.push(n);
    else runs.push([n]);
  }
  return runs
    .map(r => (r.length === 1 ? pad(r[0]) : `${pad(r[0])}–${pad(r[r.length - 1])}`))
    .join(", ");
}

/* --------------------------------------------------------------------------
 * The rail — one fixed place for everything that is navigation.
 *
 * Its whole promise is that it does not move. The same 250px column, the same
 * three bands (where you are · what is in here · what you can open with it) and
 * the same footer, whichever of the three views is on the right. Chrome that
 * relocates when the content changes is chrome you have to find again every
 * time, which is what the old foot strip did — it sat under the prose, so it
 * moved with every segment and scrolled away exactly when it was wanted.
 * ------------------------------------------------------------------------ */
type Scheme = "light" | "dark";
const SCHEME_KEY = "sigma.study.scheme";

/* --- making a borrowed accent legible ------------------------------------
 * The room takes the dashboard's accent, and the seven were chosen to glow on
 * a dark instrument — VOID's is `#22D3EE`, a cyan whose contrast against white
 * is 1.7:1. Painted as a filled button with white text it is illegible, and as
 * a word on paper it is worse.
 *
 * So neither of those colours is assumed. `--accent-fg` is whichever of black
 * or white actually contrasts against the fill, and `--accent-ink` is the
 * accent walked toward the ink — or toward the light, in dark mode — until it
 * clears 4.5:1 against the ground it will sit on. The hue survives, which is
 * the whole point of borrowing it; the luminance becomes the room's problem
 * rather than the palette's.
 *
 * CSS can express the mixing (`color-mix`) but not the *test*, which is why
 * this is arithmetic rather than a stylesheet rule.
 */
function rgbOf(h: string): [number, number, number] {
  const t = h.replace("#", "").trim();
  const full = t.length === 3 ? t.split("").map(c => c + c).join("") : t;
  return [0, 2, 4].map(i => parseInt(full.slice(i, i + 2), 16) || 0) as [number, number, number];
}
function relLum([r, g, b]: [number, number, number]): number {
  const f = (c: number) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
const ratio = (a: number, b: number) =>
  (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
const asHex = ([r, g, b]: [number, number, number]) =>
  "#" + [r, g, b].map(v => Math.round(v).toString(16).padStart(2, "0")).join("");

/** The two colours derived from a borrowed accent: what to write *on* it, and
 *  what it becomes when it is the text rather than the fill. */
function accentPair(accent: string, scheme: Scheme): { fg: string; ink: string } {
  const a = rgbOf(accent);
  const la = relLum(a);
  const fg = ratio(la, relLum([255, 255, 255])) >= ratio(la, relLum([16, 18, 26]))
    ? "#FFFFFF" : "#101018";
  const lg = relLum(rgbOf(scheme === "dark" ? "#1B1B1F" : "#FFFFFF"));
  const toward: [number, number, number] = scheme === "dark" ? [255, 255, 255] : [10, 10, 14];
  let ink = a;
  for (let t = 0; t <= 1.0001; t += 0.05) {
    ink = [0, 1, 2].map(i => a[i] + (toward[i] - a[i]) * t) as [number, number, number];
    if (ratio(relLum(ink), lg) >= 4.5) break;
  }
  return { fg, ink: asHex(ink) };
}

function Rail(
  { view, courses, course, lesson, st, depth, isCp, dispatch,
    minsDone, minsTotal, practiceDone, practiceTotal, scheme, onScheme,
    onCourses, onChain, onCourse, units }: {
    view: "catalog" | "chain" | "lesson";
    courses: Courses | null | undefined;
    course: string | null;
    lesson: Lesson | null | undefined;
    st: WbState; depth: Depth; isCp: boolean;
    dispatch: (a: WbAction) => void;
    minsDone: number; minsTotal: number;
    practiceDone: number; practiceTotal: number;
    scheme: Scheme; onScheme: (s: Scheme) => void;
    onCourses: () => void; onChain: () => void; onCourse: (c: string) => void;
    units: { n: number; modules: number; done: number; skipped: number;
             minutes: number; first: number | null; last: number | null }[];
  },
) {
  const pct = minsTotal ? Math.round((minsDone / minsTotal) * 100) : 0;
  return (
    <nav className="wb-rail" aria-label="Study navigation">
      <div className="wb-rail-top">
        <span className="wb-rail-mark">Study</span>
        {view !== "catalog" && (
          <button className="wb-rail-back" onClick={view === "lesson" ? onChain : onCourses}
                  title={view === "lesson" ? "Back to the chain (Esc)" : "All courses (Esc)"}>
            ‹ {view === "lesson" ? course ?? "chain" : "Courses"}
          </button>
        )}
      </div>

      {view === "lesson" && lesson && (
        <div className="wb-rail-now">
          <p className="wb-rail-title">{lesson.title}</p>
          <span className="wb-ring" role="img"
                aria-label={`${minsDone} of ${minsTotal} minutes`}
                style={{ "--pct": `${pct}` } as CSSProperties}>
            <span className="wb-ring-n">{pct}<i>%</i></span>
          </span>
          <p className="wb-rail-meta">
            {minsDone} of {minsTotal} min
            {practiceTotal > 0 && <> · {practiceDone}/{practiceTotal} practice</>}
          </p>
        </div>
      )}

      <div className="wb-rail-scroll">
        {view === "catalog" && (
          <RailBand label="Courses">
            {(courses?.courses ?? []).map(c => (
              <button key={c.course} className="wb-rail-item"
                      onClick={() => onCourse(c.course)}>
                <span className="wb-rail-item-k">{c.course}</span>
                <span className="wb-rail-item-v">{c.name}</span>
              </button>
            ))}
          </RailBand>
        )}

        {view === "lesson" && lesson && (
          <>
            <RailBand label={isCp ? "Checkpoint" : "Contents"}>
              {lesson.segments.map((s, i) => {
                const items = s.practice.length;
                const done = s.practice.filter(it => pr(st, it.id).result !== null).length;
                const state = items === 0 ? "none" : done === 0 ? "open"
                  : done < items ? "part" : "done";
                return (
                  <button key={s.n}
                          className={`wb-rail-item wb-seg-item s-${state} ${i === st.seg ? "on" : ""}`}
                          aria-current={i === st.seg ? "step" : undefined}
                          onClick={() => dispatch({ t: "seg", i })}>
                    <span className="wb-rail-item-k">{i + 1}</span>
                    <span className="wb-rail-item-v">{s.title}</span>
                    <span className="wb-rail-item-x">{s.minutes}m</span>
                  </button>
                );
              })}
            </RailBand>

            {!isCp && (
              <RailBand label="Depth">
                <div className="wb-seg" role="tablist" aria-label="Depth">
                  {DEPTH_LABEL.map(([d, label]) => (
                    <button key={d} role="tab" aria-selected={depth === d}
                            className={depth === d ? "on" : ""}
                            onClick={() => dispatch({ t: "depth", i: st.seg, d })}>
                      {label}
                    </button>
                  ))}
                </div>
              </RailBand>
            )}

            {!st.focus && (
              <RailBand label="Tools">
                {SLOT_LABEL.map(([s, glyph, label]) => (
                  <button key={s}
                          className={`wb-rail-item wb-tool ${st.dock === s ? "on" : ""}`}
                          aria-pressed={st.dock === s}
                          onClick={() => dispatch({ t: "dock", slot: st.dock === s ? null : s })}>
                    <span className="wb-rail-item-k" aria-hidden="true">{glyph}</span>
                    <span className="wb-rail-item-v">{label}</span>
                  </button>
                ))}
              </RailBand>
            )}
          </>
        )}

        {/* The same list shape the lesson view uses for segments, one level
            up: a course's units are to its chain what a module's segments are
            to its page, so navigating them should not be a different idea. */}
        {view === "chain" && units.length > 0 && (
          <RailBand label="Units">
            {units.map(u => {
              const state = u.modules === 0 ? "none"
                : u.done + u.skipped >= u.modules ? "done"
                : u.done > 0 ? "part" : "open";
              return (
                <button key={u.n} className={`wb-rail-item wb-seg-item s-${state}`}
                        /* No `behavior: "smooth"` here. It is dropped wherever
                           animations are throttled — measured: the scroller
                           stayed at 0 while the plain call moved it to 1111 —
                           and a jump that sometimes does nothing is worse than
                           one that is never animated. The easing is CSS's job
                           (`scroll-behavior` on `.wb-chain`), where failing to
                           apply costs smoothness rather than the scroll. */
                        onClick={() => document.getElementById(`wb-unit-${u.n}`)
                          ?.scrollIntoView({ block: "start" })}>
                  <span className="wb-rail-item-k">{u.n}</span>
                  <span className="wb-rail-item-v">
                    {u.first != null
                      ? `M${String(u.first).padStart(2, "0")}–M${String(u.last).padStart(2, "0")}`
                      : "unit"}
                  </span>
                  <span className="wb-rail-item-x">{u.done}/{u.modules}</span>
                </button>
              );
            })}
          </RailBand>
        )}
        {view === "chain" && units.length === 0 && (
          <RailBand label="Course">
            <p className="wb-rail-hint">
              Pick a module on the right. The chain is ordered — each one is
              genuinely blocked by the one before it.
            </p>
          </RailBand>
        )}
      </div>

      <div className="wb-rail-foot">
        <div className="wb-seg wb-scheme" role="group" aria-label="Appearance">
          <button className={scheme === "light" ? "on" : ""}
                  onClick={() => onScheme("light")} title="Light">☀</button>
          <button className={scheme === "dark" ? "on" : ""}
                  onClick={() => onScheme("dark")} title="Dark">☾</button>
        </div>
        <span className="wb-rail-esc">
          {view === "lesson" ? <><kbd>f</kbd> focus</> : <><kbd>Esc</kbd> back</>}
        </span>
      </div>
    </nav>
  );
}

function RailBand({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="wb-band">
      <h3 className="wb-band-h">{label}</h3>
      {children}
    </section>
  );
}

export default function WorkbenchView({ open, vault, onClose, guideProg }: {
  open: boolean; vault: string; onClose: () => void;
  guideProg?: GuideProgress | null;
}) {
  const [list, setList] = useState<LessonList | null | undefined>(undefined);
  const [courses, setCourses] = useState<Courses | null | undefined>(undefined);
  const [course, setCourse] = useState<string | null>(null);
  const [guide, setGuide] = useState<Guide | null | undefined>(undefined);
  const [picked, setPicked] = useState<Openable | null>(null);
  const [lesson, setLesson] = useState<Lesson | null | undefined>(undefined);
  const [st, dispatch] = useReducer(reduce, START);
  // Light or dark is a *reading* preference, not a theme: it belongs to the
  // person and the hour, not to the install, so it is remembered here rather
  // than derived from the dashboard's palette. Same store and same shape as
  // `sigma.brain.spin`, for the same reason — a view preference that a reload
  // forgets is a preference you set again every day.
  const [scheme, setSchemeRaw] = useState<Scheme>(() => {
    try { return localStorage.getItem(SCHEME_KEY) === "dark" ? "dark" : "light"; }
    catch { return "light"; }
  });
  const setScheme = (s: Scheme) => {
    setSchemeRaw(s);
    try { localStorage.setItem(SCHEME_KEY, s); } catch { /* private mode */ }
  };
  // The accent, and nothing else, comes from the instrument (see the inline
  // style on `.workbench` below).
  const theme = useTheme();
  const pair = accentPair(theme.tokens["--accent"] ?? "#2563EB", scheme);
  const [busy, setBusy] = useState(false);
  const [chainErr, setChainErr] = useState<string | null>(null);
  const [genNote, setGenNote] = useState<{ course: string; text: string } | null>(null);
  const [last, setLast] = useState<{ sha: string; what: string } | null>(null);
  const [rollup, setRollup] = useState<Rollup | "busy" | string | null>(null);
  // The tutor conversation lives up here, not in the slot component — switching
  // dock slots must not lose the chat, and the close path needs nothing from it.
  const [tutorTurns, setTutorTurns] = useState<TutorTurn[]>([]);
  const [tutorSession, setTutorSession] = useState<string | null>(null);
  const [tutorMode, setTutorMode] = useState<TutorMode>("nudge");
  const [tutorHold, setTutorHold] = useState<string | null>(null);
  const [tutorBusy, setTutorBusy] = useState(false);
  const tutorInFlight = useRef(false);
  // The in-flight tutor stream, aborted on module change: an orphaned stream
  // finishing after the reset would resurrect the OLD module's session id and
  // the next ask would resume the wrong conversation under the new pin.
  const tutorAbort = useRef<AbortController | null>(null);
  // The sandbox's output — up here so a slot switch or focus toggle cannot
  // unmount a traceback out of existence (S4: a failure is reported).
  const [sand, setSand] = useState<SandSt>(SAND0);
  // The latest un-posted state-mirror payload. The debounce alone would be
  // cancelled by leaving the lesson, silently dropping everything typed since
  // the last 600 ms pause — flushed on every exit path instead.
  const mirrorRef = useRef<unknown | null>(null);
  const flushMirror = () => {
    if (mirrorRef.current) {
      void post("lesson/state", mirrorRef.current).catch(() => {});
      mirrorRef.current = null;
    }
  };
  // Which courses have un-rolled-up attempts this session. A ref, not state:
  // nothing renders from it except the end-session button's presence, and the
  // close path reads it during cleanup when state is already torn down.
  const touched = useRef<Set<string>>(new Set());
  // ------------------------------------------------------------ the clock (S8)
  // Every `estimate:` in a guide is a guess until something measures it. This
  // is the something: active seconds per course, accrued while the workbench is
  // open on a lesson and *paused* when the tab is hidden or nothing has been
  // touched for IDLE_MS. Wall clock from open to close would measure how long
  // the tab was open, which is the assumed number wearing a stopwatch.
  const spent = useRef<Map<string, number>>(new Map());
  const lastAct = useRef<number>(Date.now());
  const clockCourse = useRef<string | null>(null);
  const secondsFor = (c: string) => Math.round(spent.current.get(c) ?? 0);

  useEffect(() => {
    if (!open) return;
    dispatch({ t: "reset" });
    setList(undefined); setCourses(undefined); setCourse(null);
    setGuide(undefined); setPicked(null); setLesson(undefined);
    setChainErr(null); setLast(null); setRollup(null);
    get<LessonList>("lesson").then(setList).catch(() => setList(null));
    // No course is selected on open: the grid is the landing view, and
    // auto-selecting one made four of five courses invisible behind a chip.
    get<Courses>("courses").then(setCourses).catch(() => setCourses(null));
    // Every close path — Esc through App's ladder included — rolls the
    // session up. Idempotent server-side, so racing the foot's button is fine.
    // The Set object itself is stable (only its contents change), so capturing
    // it here reads its contents as they are at close time.
    const pending = touched.current;
    const clock = spent.current;
    clock.clear();
    return () => {
      for (const c of pending) {
        void post("lesson/session-end",
                  { course: c, seconds: Math.round(clock.get(c) ?? 0) })
          .catch(() => {});
      }
      pending.clear();
      clock.clear();
    };
  }, [open]);

  // One interval for the whole open workbench, reading the current course from
  // a ref rather than keying on it: an interval that restarted on every module
  // switch would drop up to a tick each time, and the measurement would be
  // biased low exactly in the sessions that covered the most ground.
  useEffect(() => {
    if (!open) return;
    const bump = () => { lastAct.current = Date.now(); };
    const id = window.setInterval(() => {
      const c = clockCourse.current;
      if (!c || document.visibilityState !== "visible") return;
      if (Date.now() - lastAct.current > IDLE_MS) return;
      spent.current.set(c, (spent.current.get(c) ?? 0) + TICK_MS / 1000);
    }, TICK_MS);
    window.addEventListener("pointerdown", bump);
    window.addEventListener("keydown", bump);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("pointerdown", bump);
      window.removeEventListener("keydown", bump);
    };
  }, [open]);

  // Opening a module is itself activity, and it is what puts a course on the
  // clock — the chain view and the course picker are navigation, not study.
  useEffect(() => {
    clockCourse.current = lesson?.course ?? null;
    if (lesson) lastAct.current = Date.now();
  }, [lesson]);

  const fetchGuide = (c: string) =>
    get<Guide>(`guide/${c}`).then(setGuide).catch(() => setGuide(null));

  useEffect(() => {
    if (!open || !course) return;
    setGuide(undefined);
    setGenNote(null);
    void fetchGuide(course);
  }, [open, course]);

  // When a generation run for the open course settles, its notes just landed
  // (or were held) — refetch the chain rather than waiting for a reopen.
  // A null guideProg is a DEAD FEED, not a state: App nulls it on any SSE
  // error, and overwriting the 'running' evidence with null meant a run that
  // finished across a backend restart never triggered this refetch (S7
  // review) — the reconnect replays the terminal record, and prev must still
  // say 'running' when it does.
  const prevGenState = useRef<string | null>(null);
  useEffect(() => {
    const s = guideProg?.state ?? null;
    if (s === null) return;
    if (open && prevGenState.current === "running" && s !== "running") {
      setGenNote(null);            // "started —" must not outlive the run
      // The courses payload and the lesson list refresh whatever view is up:
      // a run started from a *card* settles while `course` is still null, and
      // keying the whole refetch on the drilled course left that card saying
      // "▣ generating" until the workbench was reopened.
      get<Courses>("courses").then(setCourses).catch(() => {});
      // The chain rows' openable-join reads the lesson list — without this
      // refetch the just-authored notes kept their "not written yet" chips
      // (found driving the first live run).
      get<LessonList>("lesson").then(setList).catch(() => {});
      if (course && guideProg?.course === course) void fetchGuide(course);
    }
    prevGenState.current = s;
  }, [open, course, guideProg]);

  // POST /api/guide/generate — the palette POST's own policy answers: a 409
  // is the window hold or the busy slot doing its job, shown as a notice.
  // Takes the course rather than reading the selection: the grid fires this
  // for a card that is not the one you are looking at.
  async function generate(code?: string | null) {
    const target = code ?? course;
    if (!target || busy) return;
    setGenNote(null);
    // The notice carries its course, because generation now fires from a grid:
    // an unscoped string would print one course's "window held" on every card.
    const say = (text: string) => setGenNote({ course: target, text });
    try {
      await post("guide/generate", { course: target });
      say("started — the reactor narrates; notes land as one commit each");
    } catch (err) {
      if (err instanceof ApiError) {
        const b = err.body as { reason?: string; running?: string } | undefined;
        say(err.code === "window" ? (b?.reason ?? "window held")
          : err.code === "busy" ? `busy — ${b?.running ?? "another job"} is running`
          : (err.detail || err.code));
      } else say("backend unreachable");
    }
  }

  useEffect(() => {
    if (!open || !picked) return;
    // Reset per-module view state: a surviving segment index from a longer
    // module would point past the end of a shorter one. The tutor resets too —
    // its pinned context is this module, and a conversation about the last one
    // continuing under a new pin would be quietly wrong — and its in-flight
    // stream is aborted so a late `done` cannot resurrect the old session.
    tutorAbort.current?.abort();
    tutorAbort.current = null;
    dispatch({ t: "reset" });
    setLesson(undefined);
    setTutorTurns([]); setTutorSession(null); setTutorHold(null);
    setSand(SAND0);
    const url = picked.kind === "checkpoint"
      ? `checkpoint/${picked.course}/${picked.num}`
      : `lesson/${picked.course}/${picked.num}`;
    get<Lesson>(url).then(l => {
      setLesson(l);
      // Resume where this module was left: the sidecar state rides on the
      // lesson payload. Validated field by field, inside its own try — it is
      // machine-local JSON a hand or an old build may have shaped
      // differently, and a bad entry must cost the resume, never report a
      // succeeded fetch as "backend unreachable".
      try {
        const s = l.state;
        if (!s) return;
        const depth: Record<number, Depth> = {};
        for (const [k, v] of Object.entries(s.depth ?? {})) {
          if (DEPTHS.includes(v as Depth)) depth[Number(k)] = v as Depth;
        }
        const practice: Record<string, PracticeSt> = {};
        for (const [qid, p] of Object.entries(s.practice ?? {})) {
          if (!p || typeof p !== "object") continue;   // a null entry is noise
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
                   practice,
                   scratch: typeof s.scratch === "string"
                     ? s.scratch.slice(0, SCRATCH_MAX) : "" });
      } catch { /* malformed sidecar state — render the module fresh */ }
    }).catch(() => setLesson(null));
    // Leaving the lesson (any path) flushes the pending state mirror — the
    // ref still holds this module's payload when the cleanup runs.
    return () => flushMirror();
  }, [open, picked]);

  // Mirror view state to the sidecar, debounced — resume is the feature,
  // never a commit. Depth keys become strings in JSON; hydrate converts back.
  const rendered = !!lesson && lesson.problems.length === 0;
  const isCp = picked?.kind === "checkpoint";
  useEffect(() => {
    if (!open || !rendered || !lesson) return;
    const body = {
      course: lesson.course,
      ...(lesson.checkpoint != null
        ? { checkpoint: lesson.checkpoint } : { module: lesson.module }),
      state: { depth: st.depth, fallback: st.fallback,
               practice: st.practice, scratch: st.scratch },
    };
    mirrorRef.current = body;           // what flushMirror posts on exit
    const id = window.setTimeout(() => {
      mirrorRef.current = null;
      void post("lesson/state", body).catch(() => {});
    }, 600);
    return () => window.clearTimeout(id);
  }, [open, rendered, lesson, st.depth, st.fallback, st.practice, st.scratch]);

  // The tutor's availability, checked when its slot opens — the same window
  // hold the palette's model verbs answer to, fetched, never guessed.
  useEffect(() => {
    if (!open || st.dock !== "tutor") return;
    get<{ hold: string | null }>("tutor/hold")
      .then(h => setTutorHold(h.hold)).catch(() => {});
  }, [open, st.dock]);

  // Keys live for the whole lesson level — held and loading views included,
  // so Esc always means "back toward the chain", which is what the footer
  // promises there too. The chain view owns no focus or dock, and its Esc
  // belongs to App's ladder.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.ctrlKey || e.altKey || e.metaKey) return;
      // An overlay stacked above the workbench (Capture autofocuses its
      // textarea) must get its own keys — a capture-phase listener fires
      // before the target's handlers, so check where focus actually is.
      const el = document.activeElement as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) {
        // Esc in an editor leaves the editor. Letting it fall through would
        // reach App's ladder, which closes the whole workbench — taking the
        // sandbox's transient code and the last debounce of scratch with it.
        if (e.key === "Escape") {
          el.blur();
          e.preventDefault();
          e.stopPropagation();
        }
        return;
      }
      if (e.key === "f" && picked && rendered) {
        e.preventDefault();
        dispatch({ t: "focus" });
      } else if (e.key === "Escape") {
        // The rungs, innermost first. The `return` at the end is the whole
        // safety of widening this listener past the lesson: an Esc with
        // nothing left to peel must reach App's ladder unmolested, or the
        // workbench stops closing.
        if (picked && rendered && st.focus) dispatch({ t: "focus" });
        else if (picked && rendered && st.dock) dispatch({ t: "dock", slot: null });
        else if (picked) { setPicked(null); setLesson(undefined); }  // lesson → chain
        else if (course) setCourse(null);                            // chain → grid
        else return;                                                 // grid → App
        e.preventDefault();
        e.stopPropagation();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, picked, course, rendered, st.focus, st.dock]);

  if (!open) return null;

  const seg = lesson?.segments[st.seg];
  const depth = st.depth[st.seg] ?? st.fallback;
  const held = (lesson?.problems.length ?? 0) > 0;

  // Chain rows address notes by wikilink target; the lesson list addresses
  // them by (course, number). Basenames are vault-unique, so they join there —
  // modules and checkpoints both (S6).
  const base = (f: string) => f.split("/").pop()!.replace(/\.md$/, "");
  const byBase = new Map<string, Openable>();
  for (const m of list?.modules ?? []) {
    if (m.module != null) byBase.set(base(m.file),
      { kind: "module", course: m.course, num: m.module, file: m.file, title: m.title });
  }
  for (const c of list?.checkpoints ?? []) {
    if (c.checkpoint != null) byBase.set(base(c.file),
      { kind: "checkpoint", course: c.course, num: c.checkpoint, file: c.file, title: c.title });
  }
  const row = courses?.courses.find(c => c.course === course) ?? null;
  const rowOpen = (r: GuideRow): Openable | null =>
    r.target ? byBase.get(r.target.split("/").pop()!) ?? null : null;

  /* --- the chain, as units rather than a list ----------------------------
   * The rows arrive flat and in teaching order, and were rendered that way:
   * twenty-nine identical lines, of which the only distinguishing mark was a
   * tick. Everything needed to do better is already on the wire and was simply
   * never read — the lesson list carries each module's `unit`, `estimate`,
   * `segments` and `practice`, and each checkpoint's `covers`.
   *
   * A checkpoint has no unit of its own, so it takes the unit of the last
   * module it assesses, which is what puts it at the foot of that unit rather
   * than adrift between two. A row whose note is not authored yet has no
   * metadata at all, so it inherits the unit of the row above it — the chain is
   * in teaching order, which makes carry-forward exactly right and not a guess.
   */
  const metaByBase = new Map<string, LessonListRow | CheckpointListRow>();
  for (const m of list?.modules ?? []) metaByBase.set(base(m.file), m);
  for (const c of list?.checkpoints ?? []) metaByBase.set(base(c.file), c);
  const unitOfModule = new Map<number, number>();
  for (const m of list?.modules ?? []) {
    if (m.course === course && m.module != null && m.unit != null) {
      unitOfModule.set(m.module, m.unit);
    }
  }

  type ChainItem = {
    row: GuideRow; open: Openable | null;
    mod: LessonListRow | null; cp: CheckpointListRow | null;
  };
  type ChainUnit = {
    n: number; items: ChainItem[];
    first: number | null; last: number | null;      // module numbers spanned
    modules: number; done: number; skipped: number;
    minutes: number; practice: number; unwritten: number;
  };

  const chainUnits: ChainUnit[] = (() => {
    if (!guide) return [];
    const out: ChainUnit[] = [];
    let carried = 1;
    for (const r of guide.rows) {
      const openable = rowOpen(r);
      const meta = r.target ? metaByBase.get(r.target.split("/").pop()!) ?? null : null;
      const mod = meta && "module" in meta ? meta : null;
      const cp = meta && "checkpoint" in meta ? meta : null;
      let unit = carried;
      if (mod?.unit != null) unit = mod.unit;
      else if (cp?.covers?.length) {
        unit = unitOfModule.get(cp.covers[cp.covers.length - 1]) ?? carried;
      }
      carried = unit;
      let u = out.find(x => x.n === unit);
      if (!u) {
        u = { n: unit, items: [], first: null, last: null, modules: 0,
              done: 0, skipped: 0, minutes: 0, practice: 0, unwritten: 0 };
        out.push(u);
      }
      u.items.push({ row: r, open: openable, mod, cp });
      if (cp) { u.practice += cp.practice; continue; }
      u.modules += 1;
      if (r.state === "done") u.done += 1;
      if (r.state === "skipped") u.skipped += 1;
      if (mod) {
        u.minutes += mod.estimate ?? 0;
        u.practice += mod.practice;
        if (mod.module != null) {
          u.first = u.first == null ? mod.module : Math.min(u.first, mod.module);
          u.last = u.last == null ? mod.module : Math.max(u.last, mod.module);
        }
      } else if (r.state === "open") u.unwritten += 1;
    }
    return out;
  })();

  /** The written estimate through the measured pace, when there is one. Null
   *  means unmeasured — shown as the estimate itself, never as a silent ×1.0
   *  (the rule the pace bar already followed). */
  const atPaceMin = (mins: number | null | undefined): number | null =>
    mins && row?.pace.multiplier ? Math.round(mins * row.pace.multiplier) : null;

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
      course: lesson.course,
      ...(lesson.checkpoint != null
        ? { checkpoint: lesson.checkpoint } : { module: lesson.module }),
      qid: it.id, result: r,
      hints: pr(st, it.id).hints, revealed: pr(st, it.id).revealed,
      answer: given ?? null,
    }).catch(() => {});
  };

  const toSandbox = (lang: Lang, code: string) => {
    dispatch({ t: "code", lang, text: code });
    // The dock only renders outside focus mode — leaving focus on makes the
    // click a silent no-op that loads code into an invisible slot.
    if (st.focus) dispatch({ t: "focus" });
    dispatch({ t: "dock", slot: "code" });
  };

  const endSession = async () => {
    // Roll up what was actually studied — every touched course — never the
    // picker's current selection: clicking end-session from another course's
    // chain must not post that course and report "nothing to roll up" while
    // the real attempts sit uncovered.
    const targets = [...touched.current];
    if (targets.length === 0) {
      const c = lesson?.course ?? course;
      if (!c) return;
      targets.push(c);
    }
    setRollup("busy");
    try {
      let shown: Rollup | null = null;
      for (const c of targets) {
        const r = await post<Rollup>("lesson/session-end",
                                     { course: c, seconds: secondsFor(c) });
        // The clock resets only with the row that recorded it. A rollup that
        // wrote nothing has not spent those minutes yet, and dropping them
        // would silently shorten the very session being measured.
        if (r.wrote) { touched.current.delete(c); spent.current.delete(c); }
        if (!shown || r.wrote) shown = r;
      }
      setRollup(shown);
      // Cards may have landed and the pace may have moved — both live in the
      // courses payload, which is otherwise fetched only when the workbench
      // opens.
      get<Courses>("courses").then(setCourses).catch(() => {});
    } catch (e) {
      setRollup(e instanceof ApiError
        ? `${e.code}${e.detail ? ` — ${e.detail}` : ""}` : "rollup failed");
    }
  };

  // The tutor's ask: stream over POST /api/tutor, the chat drawer's reader
  // with the workbench's pin riding in the body. A 409 is the window hold
  // doing its job — shown as the hold, not as a failure.
  const askTutor = async (question: string) => {
    if (tutorInFlight.current || !lesson) return;
    tutorInFlight.current = true;
    setTutorBusy(true);
    setTutorTurns(ts => [...ts, { q: question, a: "", tools: [], blocked: [], proposals: [] }]);
    const patch = (fn: (t: TutorTurn) => TutorTurn) =>
      setTutorTurns(ts => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));
    const qid = st.lastQid;
    const p = qid ? pr(st, qid) : null;
    const ac = new AbortController();
    tutorAbort.current = ac;
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    try {
      const res = await fetch(`${API}/api/tutor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: ac.signal,
        body: JSON.stringify({
          course: lesson.course,
          ...(lesson.checkpoint != null
            ? { checkpoint: lesson.checkpoint } : { module: lesson.module }),
          seg: seg?.n ?? null, qid,
          // `|| null` would drop a legitimate attempt of "0" — a plausible
          // numeric answer (a zero dot product IS this checkpoint's lesson).
          given: p && p.given.trim() !== "" ? p.given : null,
          hints: p?.hints ?? 0,
          revealed: p?.revealed ?? false,
          mode: tutorMode, question, session_id: tutorSession,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null) as
          { error?: string; reason?: string } | null;
        if (res.status === 409 && body?.reason) setTutorHold(body.reason);
        patch(t => ({ ...t, error: body?.reason ?? body?.error ?? `${res.status}` }));
        return;
      }
      if (!res.body) throw new Error("no response body");
      reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const chunks = buf.split(/\r?\n\r?\n/);
        buf = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const line = chunk.split(/\r?\n/).find(l => l.startsWith("data: "));
          if (!line) continue;
          let e;
          try { e = JSON.parse(line.slice(6)); } catch { continue; }
          if (e.type === "token") patch(t => ({ ...t, a: t.a + e.text }));
          else if (e.type === "tool") {
            if (String(e.name).endsWith("propose_change")) {
              patch(t => ({ ...t, proposals: [...t.proposals, e.detail || "a change"] }));
            } else {
              patch(t => ({ ...t, tools: [...t.tools, { name: e.name, detail: e.detail }] }));
            }
          }
          else if (e.type === "denied")
            patch(t => ({ ...t, blocked: [...t.blocked, e.message] }));
          else if (e.type === "error") {
            patch(t => ({ ...t, error: e.message }));
            setTutorSession(null);
          }
          // The abort guard matters here most: a stream orphaned by a module
          // switch must not hand the OLD conversation's session id to the
          // new module's tutor.
          else if (e.type === "done" && !ac.signal.aborted)
            setTutorSession(e.session_id);
        }
      }
    } catch (err) {
      if (!ac.signal.aborted) {
        patch(t => ({ ...t, error: String(err) }));
        setTutorSession(null);
      }
    } finally {
      try { await reader?.cancel(); } catch { /* already closed */ }
      if (tutorAbort.current === ac) tutorAbort.current = null;
      tutorInFlight.current = false;
      setTutorBusy(false);
    }
  };

  const inLesson = picked !== null;
  const unitLabel = lesson
    ? (lesson.checkpoint != null
      ? `CP${lesson.checkpoint}`
      : `M${String(lesson.module ?? "?").padStart(2, "0")}`)
    : "";
  const pin = lesson
    ? `${lesson.course} ${unitLabel}${seg ? ` · S${seg.n} ${seg.title}` : ""}`
      + (st.lastQid ? ` · ${st.lastQid}` : "")
    : "";
  // The estimate, corrected by what studying this course has actually cost.
  // Rendered only when `multiplier` is non-null: an unmeasured pace shows the
  // written estimate alone rather than an ×1.0 that looks like a measurement.
  const paceOf = courses?.courses.find(
    c => c.course === (lesson?.course ?? course))?.pace ?? null;
  const atPace = paceOf?.multiplier && lesson?.estimate
    ? Math.round(lesson.estimate * paceOf.multiplier) : null;
  // The rail's meter measures practice resolved across the whole module, and
  // is omitted when the module has none — a 0/0 bar would report a reading-only
  // module as untouched work, which is a different and false claim.
  const practiceItems = lesson?.segments.flatMap(s => s.practice) ?? [];
  const practiceTotal = practiceItems.length;
  const practiceDone = practiceItems.filter(
    it => pr(st, it.id).result !== null).length;
  // Progress through the module in the unit the module itself is measured in.
  // Summed from the segments rather than read from `estimate`, because the two
  // are only equal in a module that validates — and a held one still renders.
  const minsTotal = lesson?.segments.reduce((a, s) => a + s.minutes, 0) ?? 0;
  const minsDone = lesson?.segments.slice(0, st.seg)
    .reduce((a, s) => a + s.minutes, 0) ?? 0;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className={`workbench scheme-${scheme} ${st.focus ? "focus" : ""}`}
           /* The one value the room borrows from the instrument. Grounds and
              ink stay the room's own — that is what makes it a different place
              — but the accent is the dashboard's, so study mode reads as part
              of the same install rather than a white page someone bolted on.
              A CSS custom property set inline is the only way a *value* can
              cross from theme.ts into a region that otherwise declares its own. */
           style={{ "--accent": theme.tokens["--accent"],
                    "--accent-rgb": theme.tokens["--accent-rgb"],
                    "--accent-fg": pair.fg,
                    "--accent-ink": pair.ink } as CSSProperties}
           onClick={e => e.stopPropagation()} role="dialog" aria-label="Workbench">
        <Rail view={inLesson ? "lesson" : course ? "chain" : "catalog"}
              courses={courses} course={course} lesson={lesson} st={st}
              depth={depth} isCp={isCp} dispatch={dispatch}
              minsDone={minsDone} minsTotal={minsTotal}
              practiceDone={practiceDone} practiceTotal={practiceTotal}
              scheme={scheme} onScheme={setScheme}
              onCourses={() => { setCourse(null); setPicked(null); }}
              onChain={() => { setPicked(null); setLesson(undefined); }}
              onCourse={setCourse} units={chainUnits} />
        <div className="wb-main">
        <header className="study-head wb-head">
          {/* Two tiers, because one flat `·`-joined line had the course, the
              unit, the title, the estimate and the measured pace at equal
              weight. Line one is where you are; line two is what you are
              looking at. Every rule for this is scoped under `.workbench` —
              exam mode shares `.study-head`. */}
          <span className="wb-head-main">
            <span className="label">
              {/* No `◇ STUDY —` prefix in the room. The diamond is the
                  dashboard's mark for "a place in the instrument", and the one
                  thing this room is for is not being that. */}
              <button className="wb-crumb" onClick={() => { setCourse(null); setPicked(null); }}
                      disabled={!course && !inLesson}
                      title="all courses (Esc)">Courses</button>
              {course && (
                <>
                  <span className="wb-crumb-sep">⟩</span>
                  <button className="wb-crumb" disabled={!inLesson}
                          onClick={() => { setPicked(null); setLesson(undefined); }}
                          title="this course's chain (Esc)">{course}</button>
                </>
              )}
              {inLesson && lesson && (
                <>
                  <span className="wb-crumb-sep">⟩</span>
                  <span className="wb-crumb-here">{unitLabel}</span>
                </>
              )}
            </span>
            {/* The module's title and its meta used to sit here, and now sit in
                the lede at the top of the reading column — where the thing they
                describe actually is. A header that repeated them would be the
                same words twice, six pixels apart. */}
            {!inLesson && lesson && (
              <span className="wb-head-title">{lesson.title}</span>
            )}
          </span>
          <span className="wb-head-act">
            {inLesson && (
              <button className="ghost" title="Back to the course chain (Esc)"
                      onClick={() => { setPicked(null); setLesson(undefined); }}>
                ⟵ chain
              </button>
            )}
            {!inLesson && course && (
              <button className="ghost" title="Back to all courses (Esc)"
                      onClick={() => setCourse(null)}>
                ⟵ courses
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

        {/* ------------------------------------- the course grid (landing) */}
        {!inLesson && !course && (
          <CoursesGrid vault={vault} courses={courses} guideProg={guideProg}
                       busy={busy}
                       onOpen={setCourse}
                       onGenerate={c => void generate(c)}
                       onAdded={() => {
                         get<Courses>("courses").then(setCourses).catch(() => {});
                       }} />
        )}

        {/* ------------------------------------------------ chain view (S2) */}
        {!inLesson && course && (
          <div className="wb-chain">
            {guide === undefined && <p className="dim pad">reading the chain…</p>}
            {guide === null && (
              <div className="pad">
                <p className="dim">
                  no guide chain yet — a chain is
                  a <code>{course.toLowerCase()}-guide.md</code> note at the
                  course root, one checkbox row per module
                </p>
                <p className="wb-gen">
                  <button className="ghost" disabled={busy}
                          onClick={() => void generate()}
                          title="one model call — plans modules from the course notes as a draft blueprint; nothing else runs until you approve it">
                    ✎ draft blueprint
                  </button>
                </p>
                {genNote?.course === course && (
                  <p className="wb-gen dim">{genNote.text}</p>
                )}
                {row && row.modules > 0 && list && (
                  <>
                    <p className="sub dim">authored modules, unchained</p>
                    <div className="wb-chips">
                      {list.modules
                        .filter(m => m.course === course)
                        .map(m => (
                          <button key={m.file} disabled={m.module == null}
                                  onClick={() => m.module != null && setPicked({
                                    kind: "module", course: m.course,
                                    num: m.module, file: m.file, title: m.title })}>
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
                {/* The counts moved into the progress block below, which says
                    the same things in the units they matter in. What is left
                    here is what that block cannot say: which note this is, and
                    whether the plan behind it is approved. */}
                <p className="wb-chainhead">
                  <span className="dim">
                    {guide.blueprint && guide.blueprint !== "approved"
                      ? `blueprint ${guide.blueprint}` : ""}
                    {(guide.missing ?? 0) > 0 ? `${guide.blueprint && guide.blueprint !== "approved" ? " · " : ""}${guide.missing} not written` : ""}
                  </span>
                  {guide.file ? (
                    <a href={obsidianHref(vault, guide.file)}
                       title="the chain note — markdown is the truth">
                      {guide.file.split("/").pop()}
                    </a>
                  ) : (
                    <span className="dim"
                          title="the chain note is created the moment generation
 runs against the approved blueprint">no chain yet</span>
                  )}
                </p>
                {(() => {
                  const gen = guideProg && guideProg.course === course ? guideProg : null;
                  const age = gen?.updated ? Date.now() - Date.parse(gen.updated) : NaN;
                  const genRunning = gen?.state === "running" &&
                    !isNaN(age) && age < 30 * 60_000 ? gen : null;
                  if (genRunning) {
                    const total = genRunning.queue?.length ?? 0;
                    const done = Object.keys(genRunning.results ?? {}).length;
                    return <p className="wb-gen live">▣ generating —
                      {" "}{genRunning.current ?? "…"}
                      {total ? ` · ${Math.min(done + 1, total)} of ${total}` : ""}</p>;
                  }
                  if (guide.blueprint === "draft") {
                    return <p className="wb-gen dim">blueprint drafted — edit{" "}
                      <code>{course?.toLowerCase()}-guide-blueprint.md</code>, set{" "}
                      <code>status: approved</code>; generation only runs against
                      an approved plan</p>;
                  }
                  if (guide.blueprint === "approved" && (guide.missing ?? 0) > 0) {
                    return <p className="wb-gen">
                      <button className="ghost" disabled={busy}
                              onClick={() => void generate()}
                              title="authors the plan's missing notes — one revertible commit each; anything held surfaces in WAITING ON YOU">
                        ▶ generate {guide.missing} missing
                        {gen?.state === "paused" ? " (resume)" : ""}
                      </button>
                    </p>;
                  }
                  if (!guide.blueprint) {
                    return <p className="wb-gen">
                      <button className="ghost" disabled={busy}
                              onClick={() => void generate()}
                              title="one model call — plans modules from the course notes as a draft blueprint; nothing else runs until you approve it">
                        ✎ draft blueprint
                      </button>
                    </p>;
                  }
                  if (guide.blueprint !== "approved") {
                    // "unstated" or anything else hand-typed: say so rather
                    // than dead-ending — generation refuses this state too.
                    return <p className="wb-gen dim">blueprint status is{" "}
                      <code>{guide.blueprint}</code> — set{" "}
                      <code>status: draft</code> or <code>status: approved</code>{" "}
                      in <code>{course?.toLowerCase()}-guide-blueprint.md</code></p>;
                  }
                  return null;
                })()}
                {genNote?.course === course && (
                  <p className="wb-gen dim">{genNote.text}</p>
                )}
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
                {/* --- what the whole course costs, and how far in you are --
                    A count of done-versus-open answers "how many" and never
                    "how far", which on a 29-row chain is the only question
                    worth asking. The bar is segmented by unit, so its shape is
                    the course's shape rather than one undifferentiated ratio. */}
                {chainUnits.length > 0 && (
                  <div className="wb-cprog">
                    <div className="wb-cprog-bar" role="img"
                         aria-label={`${guide.done} of ${guide.total} rows done`}>
                      {chainUnits.map(u => (
                        <span key={u.n} className="wb-cprog-u"
                              style={{ flexGrow: Math.max(1, u.items.length) }}
                              title={`Unit ${u.n} — ${u.done}/${u.modules} modules`}>
                          <span className="wb-cprog-fill"
                                style={{ width: `${u.modules ? (u.done / u.modules) * 100 : 0}%` }} />
                        </span>
                      ))}
                    </div>
                    <p className="wb-cprog-meta">
                      <b>{guide.done}</b> of {guide.total} done
                      {guide.skipped ? ` · ${guide.skipped} skipped` : ""}
                      {row && row.projected.open > 0 && (
                        <span title={row.pace.multiplier
                          ? `${row.projected.estimate} min written × ${row.pace.multiplier} measured`
                            + ` over ${row.pace.n} finished module(s)`
                          : "estimated by the modules themselves — not enough finished"
                            + " modules to measure your pace yet"}>
                          {" · "}
                          {row.projected.minutes
                            ? `~${(row.projected.minutes / 60).toFixed(1)} h left at your pace`
                            : `${(row.projected.estimate / 60).toFixed(1)} h left`}
                          {row.projected.unestimated
                            /* Not "unwritten". `unestimated` counts open rows
                               whose target is not in the *module* scan, and a
                               checkpoint never is — it carries no ⏱ at all. So
                               a fully authored AA-210 read "+5 unwritten" when
                               nothing was missing, and "+6" when the only real
                               problem was one stale link in the chain. */
                            ? ` · ${row.projected.unestimated} untimed` : ""}
                        </span>
                      )}
                      {row && row.recall.open > 0 && (
                        <>
                          {" · "}
                          {row.recall.file ? (
                            <a href={obsidianHref(vault, row.recall.file)}
                               title="cards raised from what you missed — they queue under Courses, low priority, and retire themselves after two weeks">
                              {row.recall.open} recall card{row.recall.open === 1 ? "" : "s"}
                            </a>
                          ) : `${row.recall.open} recall cards`}
                          {row.recall.open >= row.recall.cap ? " (at cap)" : ""}
                        </>
                      )}
                    </p>
                  </div>
                )}

                {/* --- the one obvious next thing ---------------------------
                    The frontier used to be the row with a small triangle beside
                    it, somewhere in a list of twenty-nine. It is the only row
                    you can act on, so it is lifted out of the list entirely. */}
                {(() => {
                  if (!guide.frontier) return null;
                  const r = guide.frontier;
                  const m = rowOpen(r);
                  const meta = r.target
                    ? metaByBase.get(r.target.split("/").pop()!) ?? null : null;
                  const mod = meta && "module" in meta ? meta : null;
                  const cp = meta && "checkpoint" in meta ? meta : null;
                  const paced = atPaceMin(mod?.estimate);
                  return (
                    <section className="wb-resume">
                      <p className="wb-resume-eyebrow">
                        {guide.done > 0 ? "Continue" : "Start here"}
                      </p>
                      <h3 className="wb-resume-t">{rowText(r)}</h3>
                      <p className="wb-resume-meta">
                        {cp
                          ? <>Checkpoint · assesses {coversLabel(cp.covers)} · {cp.practice} questions</>
                          : mod
                            ? <>{mod.segments} segments · {mod.practice} practice
                                {mod.estimate ? <> · {paced
                                  ? `~${paced} min at your pace`
                                  : `${mod.estimate} min`}</> : null}</>
                            : <>not authored yet</>}
                      </p>
                      <div className="wb-resume-act">
                        {m && (
                          <button className="wb-btn wb-btn-primary" disabled={busy}
                                  onClick={() => setPicked(m)}>
                            {cp ? "Take it" : "Study"}<span aria-hidden="true"> →</span>
                          </button>
                        )}
                        <button className="wb-btn" disabled={busy}
                                title="tick the row — one commit, revertible from the ledger"
                                onClick={() => void act(`completed ${rowText(r)}`,
                                  "tasks/toggle",
                                  { file: guide.file, line: r.line, raw: r.raw, done: true })}>
                          ✓ Complete
                        </button>
                        <button className="wb-btn wb-btn-quiet" disabled={busy}
                                title="flip to [-] skipped::today — the frontier moves on, the row stays"
                                onClick={() => void act(`skipped ${rowText(r)}`,
                                  "tasks/skip",
                                  { file: guide.file, line: r.line, raw: r.raw })}>
                          → Skip
                        </button>
                      </div>
                    </section>
                  );
                })()}

                {/* --- the units ------------------------------------------- */}
                {chainUnits.map(u => {
                  const complete = u.modules > 0 && u.done + u.skipped === u.modules;
                  return (
                    <section key={u.n} className={`wb-unit ${complete ? "is-done" : ""}`}
                             id={`wb-unit-${u.n}`}>
                      <header className="wb-unit-h">
                        <h3 className="wb-unit-n">
                          Unit {u.n}
                          {u.first != null && (
                            <span className="wb-unit-span">
                              M{String(u.first).padStart(2, "0")}–M{String(u.last).padStart(2, "0")}
                            </span>
                          )}
                        </h3>
                        <span className="wb-unit-meta">
                          {u.minutes > 0 && <>{(u.minutes / 60).toFixed(1)} h · </>}
                          {u.practice} questions · {u.done}/{u.modules}
                          {u.unwritten > 0 && <> · {u.unwritten} unwritten</>}
                        </span>
                      </header>
                      <ul className="wb-rows">
                        {u.items.map(({ row: r, open: m, mod, cp }) => {
                          const frontier = guide.frontier?.line === r.line;
                          const paced = atPaceMin(mod?.estimate);
                          const body = (
                            <>
                              <span className="wb-row-k">
                                {cp ? "◈" : mod?.module != null
                                  ? String(mod.module).padStart(2, "0") : "··"}
                              </span>
                              <span className="wb-row-t">{rowText(r)}</span>
                              <span className="wb-row-facts">
                                {cp
                                  ? <>assesses {coversLabel(cp.covers)} · {cp.practice}q</>
                                  : mod
                                    ? <>
                                        {mod.estimate ? <em title={paced
                                          ? `~${paced} min at your measured pace`
                                          : "the module's own estimate"}>{mod.estimate}m</em> : null}
                                        <em>{mod.segments} seg</em>
                                        <em>{mod.practice}q</em>
                                      </>
                                    : <em className="wb-row-warn">not written yet</em>}
                                {mod?.problems.length
                                  ? <em className="wb-row-warn">held</em> : null}
                                {r.state === "skipped" && r.skipped
                                  ? <em>skipped {r.skipped}</em> : null}
                                {r.date ? <em>📅 {r.date}</em> : null}
                              </span>
                            </>
                          );
                          return (
                            <li key={r.line}
                                className={`wb-row wb-row-${r.state}`
                                  + (cp ? " wb-row-cp" : "")
                                  + (frontier ? " wb-frontier" : "")}>
                              <span className="wb-row-mark" aria-hidden="true">
                                {r.state === "done" ? "✓" : r.state === "skipped" ? "−"
                                  : frontier ? "▸" : ""}
                              </span>
                              {m
                                ? <button className="wb-row-open" onClick={() => setPicked(m)}
                                          title={`open ${m.file}`}>{body}</button>
                                : <span className="wb-row-open is-flat">{body}</span>}
                            </li>
                          );
                        })}
                      </ul>
                    </section>
                  );
                })}
                {!guide.frontier && guide.file && (
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
            <h3>HELD — this {isCp ? "checkpoint" : "module"} fails the grammar</h3>
            <p className="dim">A malformed lesson would read as shipped, so it is
            named instead of rendered:</p>
            <ul>{lesson.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
            <button className="ghost" onClick={() => { setPicked(null); setLesson(undefined); }}>
              ⟵ back to the chain
            </button>
          </div>
        )}

        {inLesson && lesson && !held && (
          <>
            {/* `has-dock` is back, but it buys the opposite of what it bought
                when the body was a grid: not a third track, but the offset that
                keeps the reading column standing still while a column opens
                beside it. */}
            <div className={`wb-body ${st.dock ? "has-dock" : ""}`}>
              <div className="wb-read">
                {/* The lede replaces the rail. A 176px spine spent permanent
                    width telling you which of five segments you were on; a line
                    of text says the same thing, and the room it gives back goes
                    to the reading measure. Where you are, how far in, and how
                    long is left — the three facts the rail actually carried. */}
                {seg && (
                  <div className="wb-lede">
                    <p className="wb-lede-mod">
                      {lesson.title}
                      <span className="wb-lede-pos">
                        {isCp ? "checkpoint" : `${st.seg + 1} of ${lesson.segments.length}`}
                      </span>
                    </p>
                    <h2 className="wb-lede-seg">{seg.title}</h2>
                    <p className="wb-lede-bar">
                      <span className="cov-bar" role="img"
                            aria-label={`${minsDone} of ${minsTotal} minutes`}>
                        <span className="cov-fill"
                              style={{ width: `${minsTotal ? Math.round((minsDone / minsTotal) * 100) : 0}%` }} />
                      </span>
                      <span className="dim">{minsDone} of {minsTotal} min</span>
                      {practiceTotal > 0 && (
                        <span className="dim">· {practiceDone}/{practiceTotal} practice</span>
                      )}
                      {atPace ? (
                        <span className="wb-pace"
                              title={`measured: ${paceOf!.actual} min actually spent `
                                     + `against ${paceOf!.estimate} min estimated over `
                                     + `${paceOf!.n} finished module(s), `
                                     + `${paceOf!.basis === "vault"
                                          ? "vault-wide" : "this course"}`}>
                          · ~{atPace} min at your pace
                        </span>
                      ) : null}
                      {lesson.checkpoint != null && (lesson.covers?.length ?? 0) > 0 && (
                        <span className="dim">
                          · covers {lesson.covers!.map(m => `M${String(m).padStart(2, "0")}`).join(", ")}
                        </span>
                      )}
                    </p>
                  </div>
                )}

                {seg && (
                  <Segment seg={seg} depth={depth} st={st} cp={isCp}
                           dispatch={dispatch} onResolve={resolve}
                           onSandbox={toSandbox} />
                )}

                {/* The one filled control on the screen (§1.2's single primary
                    action). On the last segment it stops pretending there is a
                    next one and offers the way out instead. */}
                <div className="wb-continue">
                  {st.seg > 0 && (
                    <button className="wb-back"
                            onClick={() => dispatch({ t: "seg", i: st.seg - 1 })}>
                      ← Back
                    </button>
                  )}
                  {st.seg < lesson.segments.length - 1 ? (
                    <button className="wb-go"
                            onClick={() => dispatch({ t: "seg", i: st.seg + 1 })}>
                      Continue →
                    </button>
                  ) : (
                    <button className="wb-go"
                            title="back to the chain — completing is still your click on the row"
                            onClick={() => { setPicked(null); setLesson(undefined); }}>
                      Done — back to the chain
                    </button>
                  )}
                </div>

                {/* The strip that used to live here — segments, depth, tools —
                    is the rail on the left now. It was chrome sitting under the
                    reading, which meant it scrolled away exactly when you
                    wanted it, and moved every time the prose changed length. */}
              </div>
              {!st.focus && st.dock && (
                <aside className="wb-dock">
                  <div className="wb-slots" role="tablist" aria-label="Dock slot">
                    {/* The dock's own tabs stay short — five full names do not
                        fit 420px and wrapped "Work pad" onto two lines. The
                        rail carries the long form; here the key is enough,
                        and the title attribute holds the rest. */}
                    {SLOT_LABEL.map(([s, glyph, label]) => (
                      <button key={s} role="tab" aria-selected={st.dock === s}
                              className={st.dock === s ? "active" : ""}
                              title={label}
                              onClick={() => dispatch({ t: "dock", slot: s })}>
                        <span aria-hidden="true">{glyph}</span> {s}
                      </button>
                    ))}
                    <button className="ghost wb-slot-x" title="Close the dock (Esc)"
                            onClick={() => dispatch({ t: "dock", slot: null })}>✕</button>
                  </div>

                  {st.dock === "prov" && seg && (
                    <div className="wb-slotbody">
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
                      <h4>{isCp ? "Checkpoint" : "Module"}</h4>
                      <ul className="wb-prov">
                        <li>
                          <a href={obsidianHref(vault, lesson.file)}>{lesson.file.split("/").pop()}</a>
                          <span className="dim"> · segment at line {seg.line}</span>
                        </li>
                        {lesson.verified && <li className="dim">sources last verified {lesson.verified}</li>}
                      </ul>
                      {lesson.sources.length > 0 && (
                        <>
                          <h4>All module sources</h4>
                          <ul className="wb-prov">
                            {lesson.sources.map(s => (
                              <li key={s}>
                                <a href={obsidianHref(vault, s)}>{s.split("/").pop()}</a>
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                    </div>
                  )}

                  {st.dock === "work" && (
                    <div className="wb-slotbody wb-work">
                      <h4>✎ Work</h4>
                      <textarea className="wb-editor" value={st.scratch}
                                aria-label="work pad" spellCheck={false}
                                placeholder="scratch space — resolve components, set up the FBD, keep the arithmetic honest…"
                                onChange={e => dispatch({ t: "scratch", text: e.target.value })} />
                      <p className="dim">persists on this machine (sidecar), per
                      {isCp ? " checkpoint" : " module"} — never a note</p>
                    </div>
                  )}

                  {st.dock === "code" && (
                    <div className="wb-slotbody">
                      <CodeDock code={st.code} sand={sand} setSand={setSand}
                                dispatch={dispatch} />
                    </div>
                  )}

                  {st.dock === "tutor" && (
                    <div className="wb-slotbody">
                      <TutorDock turns={tutorTurns} busy={tutorBusy} hold={tutorHold}
                                 mode={tutorMode} pin={pin}
                                 onMode={setTutorMode} onAsk={q => void askTutor(q)} />
                    </div>
                  )}

                  {/* The calculator writes into the work pad rather than owning
                      a record of its own: the pad is already sidecar-persisted
                      per module, and a second half-remembered history of your
                      arithmetic is a worse answer than one you can see. */}
                  {st.dock === "calc" && (
                    <div className="wb-slotbody">
                      <Calculator scratchAppend={line => dispatch({
                        t: "scratch",
                        text: (st.scratch ? st.scratch.replace(/\s*$/, "") + "\n" : "") + line,
                      })} />
                    </div>
                  )}
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
              {/* Cards are part of the same commit, so they are reported beside
                  it — including what the cap withheld, because a cap that
                  drops evidence silently is indistinguishable from a bug. */}
              {rollup.recall && rollup.recall.raised > 0 && (
                <> · {rollup.recall.raised} recall card
                  {rollup.recall.raised === 1 ? "" : "s"} raised
                  {rollup.recall.withheld
                    ? ` (${rollup.recall.withheld} withheld — at cap)` : ""}</>
              )}
            </span>
          )}
          {typeof rollup === "string" && rollup !== "busy" && (
            <span className="wb-roll err">{rollup}</span>
          )}
        </footer>
        </div>
      </div>
    </div>
  );
}
