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
 * Esc peels in arrival order — focus, dock, module → chain view — in the
 * capture phase; the chain view lets Esc through to App's ladder to close.
 * A module whose parse reports problems is HELD and never rendered as if fine.
 */
import { useEffect, useReducer, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { API, get, obsidianHref, post, ApiError } from "./api";
import type {
  Courses, Guide, GuideProgress, GuideRow, Lesson, LessonList,
  LessonSegment, PracticeItem, Rollup,
} from "./api";
import { pythonReady, resetSql, runPython, runSql, warmPython } from "./sandbox";
import type { PyRun, SqlRun } from "./sandbox";

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

/** The dock's four slots — exactly one open at a time (§11). */
type Slot = "prov" | "work" | "code" | "tutor";
const SLOT_LABEL: [Slot, string][] = [
  ["prov", "[?] prov"], ["work", "✎ work"], ["code", "▸ code"], ["tutor", "✦ tutor"]];

type Lang = "python" | "sql";
const SCRATCH_MAX = 4000;

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
  seg: 0, depth: {}, fallback: "normal", focus: false, dock: "prov",
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

function Segment({ seg, depth, st, cp, onDepth, dispatch, onResolve, onSandbox }: {
  seg: LessonSegment; depth: Depth; st: WbState;
  /** Checkpoint rendering: practice only — no depth tabs, no example (§5.4). */
  cp: boolean;
  onDepth: (d: Depth) => void;
  dispatch: (a: WbAction) => void;
  onResolve: (it: PracticeItem, r: Result, given?: string) => void;
  onSandbox: (lang: Lang, code: string) => void;
}) {
  const resolved = seg.practice.filter(it => pr(st, it.id).result !== null).length;
  return (
    <>
      <div className="wb-seghead">
        <h3>S{seg.n} · {seg.title}</h3>
        <span className="wb-min">⏱ {seg.minutes} min</span>
      </div>
      {!cp && (
        <div className="wb-depths" role="tablist" aria-label="Depth">
          {DEPTH_LABEL.map(([d, label]) => (
            <button key={d} role="tab" aria-selected={depth === d}
                    className={depth === d ? "active" : ""}
                    onClick={() => onDepth(d)}>{label}</button>
          ))}
        </div>
      )}
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
  const [busy, setBusy] = useState(false);
  const [chainErr, setChainErr] = useState<string | null>(null);
  const [genNote, setGenNote] = useState<string | null>(null);
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
    if (open && course && guideProg?.course === course &&
        prevGenState.current === "running" && s !== "running") {
      setGenNote(null);            // "started —" must not outlive the run
      void fetchGuide(course);
      get<Courses>("courses").then(setCourses).catch(() => {});
      // The chain rows' openable-join reads the lesson list — without this
      // refetch the just-authored notes kept their "not written yet" chips
      // (found driving the first live run).
      get<LessonList>("lesson").then(setList).catch(() => {});
    }
    prevGenState.current = s;
  }, [open, course, guideProg]);

  // POST /api/guide/generate — the palette POST's own policy answers: a 409
  // is the window hold or the busy slot doing its job, shown as a notice.
  async function generate() {
    if (!course || busy) return;
    setGenNote(null);
    try {
      await post("guide/generate", { course });
      setGenNote("started — the reactor narrates; notes land as one commit each");
    } catch (err) {
      if (err instanceof ApiError) {
        const b = err.body as { reason?: string; running?: string } | undefined;
        setGenNote(err.code === "window" ? (b?.reason ?? "window held")
          : err.code === "busy" ? `busy — ${b?.running ?? "another job"} is running`
          : (err.detail || err.code));
      } else setGenNote("backend unreachable");
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
    if (!open || !picked) return;
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
      if (e.key === "f" && rendered) {
        e.preventDefault();
        dispatch({ t: "focus" });
      } else if (e.key === "Escape") {
        if (rendered && st.focus) dispatch({ t: "focus" });
        else if (rendered && st.dock) dispatch({ t: "dock", slot: null });
        else { setPicked(null); setLesson(undefined); }   // lesson → chain view
        e.preventDefault();
        e.stopPropagation();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, picked, rendered, st.focus, st.dock]);

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
  const rowOpen = (r: GuideRow): Openable | null =>
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
        const r = await post<Rollup>("lesson/session-end", { course: c });
        if (r.wrote) touched.current.delete(c);
        if (!shown || r.wrote) shown = r;
      }
      setRollup(shown);
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
  const row = courses?.courses.find(c => c.course === course) ?? null;
  const unitLabel = lesson
    ? (lesson.checkpoint != null
      ? `CP${lesson.checkpoint}`
      : `M${String(lesson.module ?? "?").padStart(2, "0")}`)
    : "";
  const pin = lesson
    ? `${lesson.course} ${unitLabel}${seg ? ` · S${seg.n} ${seg.title}` : ""}`
      + (st.lastQid ? ` · ${st.lastQid}` : "")
    : "";

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className={`workbench ${st.focus ? "focus" : ""}`}
           onClick={e => e.stopPropagation()} role="dialog" aria-label="Workbench">
        <header className="study-head">
          <span className="label">
            ◇ STUDY — {inLesson ? "WORKBENCH" : "COURSES"}
            {inLesson && lesson
              ? ` · ${lesson.course} ${unitLabel} — ${lesson.title}` : ""}
            {inLesson && lesson?.estimate
              ? <span className="dim"> · {lesson.estimate} min</span> : null}
            {inLesson && lesson && lesson.checkpoint != null && (lesson.covers?.length ?? 0) > 0
              ? <span className="dim"> · covers {lesson.covers!.map(m => `M${String(m).padStart(2, "0")}`).join(", ")}</span>
              : null}
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
                <p className="wb-gen">
                  <button className="ghost" disabled={busy}
                          onClick={() => void generate()}
                          title="one model call — plans modules from the course notes as a draft blueprint; nothing else runs until you approve it">
                    ✎ draft blueprint
                  </button>
                </p>
                {genNote && <p className="wb-gen dim">{genNote}</p>}
                {row && row.modules > 0 && list && (
                  <>
                    <p className="dim">authored modules, unchained:</p>
                    <div className="wb-picker">
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
                <p className="wb-chainhead">
                  <span className="dim">
                    {guide.done} done · {guide.skipped} skipped
                    · {guide.total - guide.done - guide.skipped} open
                    {guide.blueprint ? ` · blueprint ${guide.blueprint}` : ""}
                    {guide.planned != null
                      ? ` · ${guide.planned} planned · ${guide.missing} missing`
                      : ""}
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
                {genNote && <p className="wb-gen dim">{genNote}</p>}
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
                    const m = rowOpen(r);
                    const frontier = guide.frontier?.line === r.line;
                    const openable = m != null;
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
                          {m?.kind === "checkpoint" && (
                            <em className="wb-chip">checkpoint</em>
                          )}
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
                  <Segment seg={seg} depth={depth} st={st} cp={isCp}
                           onDepth={d => dispatch({ t: "depth", i: st.seg, d })}
                           dispatch={dispatch} onResolve={resolve}
                           onSandbox={toSandbox} />
                )}
                <div className="wb-nav">
                  <button className="ghost" disabled={st.seg === 0}
                          onClick={() => dispatch({ t: "seg", i: st.seg - 1 })}>← prev</button>
                  <span className="dim">{st.seg + 1} / {lesson.segments.length}</span>
                  <button className="ghost" disabled={st.seg >= lesson.segments.length - 1}
                          onClick={() => dispatch({ t: "seg", i: st.seg + 1 })}>next →</button>
                </div>
              </div>
              {!st.focus && st.dock && (
                <aside className="wb-dock">
                  <div className="wb-slots" role="tablist" aria-label="Dock slot">
                    {SLOT_LABEL.map(([s, label]) => (
                      <button key={s} role="tab" aria-selected={st.dock === s}
                              className={st.dock === s ? "active" : ""}
                              onClick={() => dispatch({ t: "dock", slot: s })}>
                        {label}
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
                </aside>
              )}
              {!st.focus && !st.dock && (
                <aside className="wb-dock wb-dock-closed">
                  {SLOT_LABEL.map(([s, label]) => (
                    <button key={s} className="ghost"
                            title={`Open the ${s} slot`}
                            onClick={() => dispatch({ t: "dock", slot: s })}>
                      {label}
                    </button>
                  ))}
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
