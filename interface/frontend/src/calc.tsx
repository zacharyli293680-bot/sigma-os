/*
 * calc.tsx — the workbench's calculator slot: a keypad, a grapher and an
 * algebra pane.
 *
 * **No `eval`, and no library.** The app's standing rule is that a string
 * typed here is data to a parser and never something the browser executes —
 * `chat.tsx` states it for HTML, `math.tsx` for TeX, `figure.tsx` for SVG. The
 * parser and the algebra live next door in `expr.ts` and `cas.ts`; this file
 * is only the room they are used in.
 *
 * ## Why three panes and not one
 *
 * The first version had no modes at all: one field, and an expression that
 * mentioned `x` grew a plot under its answer. That is a lovely trick and it
 * quietly capped what the thing could be. A grapher wants *several* functions
 * and a window you can move; a CAS wants an operation and a variable to
 * perform it in. Neither fits under a single field, and pretending otherwise
 * cost the two features that were actually missing.
 *
 * So: **Calc** is a calculator, keys and all — the arithmetic you do beside a
 * problem, with a tape and a memory. **Graph** is a plot of up to six curves
 * with a window you drag, zoom and read off. **Algebra** is `cas.ts` with a
 * verb attached: simplify, expand, factor, differentiate, integrate, solve.
 *
 * The three share one expression language, so an answer moves between them —
 * solve for x here, graph it there, and any of them appends its line to the
 * work pad, which is the module's own sidecar-persisted record. The calculator
 * still keeps no durable history of its own for that reason.
 *
 * ## Degrees
 *
 * The keypad and the grapher honour deg/rad, because a bearing in a statics
 * problem is written in degrees and always will be. The algebra pane does not
 * and says so on screen: d/dx sin(x) = cos(x) is false in degrees, and a CAS
 * that silently followed a display toggle would hand back a wrong derivative.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  type Node, parse, parseEquation, evaluate, sampler, show, varsOf, sub, ZERO,
} from "./expr";
import { type CasOut, type Op, run as casRun } from "./cas";
import { MathBlock } from "./math";

type Mode = "calc" | "graph" | "algebra";
const MODE_KEY = "sigma.study.calcmode";
const DEG_KEY = "sigma.study.calcdeg";

const CURVES = 6;    // as many as six distinguishable colours, and no more

// --------------------------------------------------------------------------
// the keypad
// --------------------------------------------------------------------------

type Key = {
  /** What the key says. */
  t: string;
  /** What it puts in the field. A `#` in the string is where the caret lands. */
  ins?: string;
  /** The second-function label and insert, revealed by `2nd`. */
  t2?: string;
  ins2?: string;
  /** An action key rather than an insert. */
  act?: "clear" | "back" | "equals" | "sign" | "deg" | "inv"
      | "mc" | "mr" | "mplus" | "mminus";
  /** Which family it belongs to, for colour. */
  cls?: "fn" | "op" | "num" | "go" | "warn";
  title?: string;
};

/** Six columns, and the order a hand expects: digits in a block on the left of
 *  the lower half, operators down the right edge, functions above. A keypad is
 *  muscle memory or it is nothing, so this is the layout every scientific
 *  calculator has shipped since 1985 rather than a nicer one. */
const KEYS: Key[] = [
  { t: "2nd", act: "inv", cls: "fn", title: "the second function on each key" },
  { t: "deg", act: "deg", cls: "fn", title: "degrees or radians" },
  { t: "(", ins: "(", cls: "op" }, { t: ")", ins: ")", cls: "op" },
  { t: "⌫", act: "back", cls: "warn", title: "delete the last character" },
  { t: "AC", act: "clear", cls: "warn", title: "clear the field" },

  { t: "sin", ins: "sin(#)", t2: "sin⁻¹", ins2: "asin(#)", cls: "fn" },
  { t: "cos", ins: "cos(#)", t2: "cos⁻¹", ins2: "acos(#)", cls: "fn" },
  { t: "tan", ins: "tan(#)", t2: "tan⁻¹", ins2: "atan(#)", cls: "fn" },
  { t: "ln", ins: "ln(#)", t2: "eˣ", ins2: "exp(#)", cls: "fn" },
  { t: "log", ins: "log(#)", t2: "10ˣ", ins2: "10^(#)", cls: "fn" },
  { t: "÷", ins: "/", cls: "op" },

  { t: "x²", ins: "^2", t2: "x³", ins2: "^3", cls: "fn" },
  { t: "xʸ", ins: "^", t2: "ʸ√x", ins2: "root(#, )", cls: "fn" },
  { t: "√", ins: "sqrt(#)", t2: "∛", ins2: "cbrt(#)", cls: "fn" },
  { t: "1/x", ins: "^-1", t2: "|x|", ins2: "abs(#)", cls: "fn" },
  { t: "n!", ins: "!", t2: "nCr", ins2: "ncr(#, )", cls: "fn" },
  { t: "×", ins: "*", cls: "op" },

  { t: "7", ins: "7", cls: "num" }, { t: "8", ins: "8", cls: "num" },
  { t: "9", ins: "9", cls: "num" },
  { t: "π", ins: "pi", t2: "τ", ins2: "tau", cls: "fn" },
  { t: "e", ins: "e", t2: "g", ins2: "g", cls: "fn", title: "e — or g, 9.81 m/s²" },
  { t: "−", ins: "-", cls: "op" },

  { t: "4", ins: "4", cls: "num" }, { t: "5", ins: "5", cls: "num" },
  { t: "6", ins: "6", cls: "num" },
  { t: "ans", ins: "ans", cls: "fn", title: "the previous result" },
  { t: "EXP", ins: "e", t2: "mod", ins2: "mod(#, )", cls: "fn",
    title: "×10ⁿ — 2e5 is 200000" },
  { t: "+", ins: "+", cls: "op" },

  { t: "1", ins: "1", cls: "num" }, { t: "2", ins: "2", cls: "num" },
  { t: "3", ins: "3", cls: "num" },
  { t: "x", ins: "x", cls: "fn", title: "a variable — graph it or solve for it" },
  { t: "%", ins: "%", cls: "fn", title: "percent after a value, modulo between two" },
  { t: "=", act: "equals", cls: "go" },

  { t: "0", ins: "0", cls: "num" }, { t: ".", ins: ".", cls: "num" },
  { t: "±", act: "sign", cls: "num", title: "negate what is in the field" },
  { t: "MR", act: "mr", cls: "fn", title: "recall the memory" },
  { t: "M+", act: "mplus", cls: "fn", title: "add the answer to memory" },
  { t: "M−", act: "mminus", cls: "fn", title: "subtract the answer from memory" },
];

// --------------------------------------------------------------------------
// the plot
// --------------------------------------------------------------------------

const SAMPLES = 700;

type Win = { x0: number; x1: number; y0: number; y1: number };

/** Sample `f` across the window and return SVG path segments, split wherever
 *  the curve leaves the world — a vertical asymptote must be a gap, not a
 *  stroke drawn straight down through the plot. */
function paths(f: (x: number) => number, w: Win, pw: number, ph: number): string[] {
  const out: string[] = [];
  let cur: string[] = [];
  const px = (x: number) => ((x - w.x0) / (w.x1 - w.x0)) * pw;
  const py = (y: number) => ph - ((y - w.y0) / (w.y1 - w.y0)) * ph;
  const span = w.y1 - w.y0;
  let prev: number | null = null;
  for (let i = 0; i <= SAMPLES; i++) {
    const x = w.x0 + ((w.x1 - w.x0) * i) / SAMPLES;
    const y = f(x);
    const inWorld = Number.isFinite(y) && y >= w.y0 - span && y <= w.y1 + span;
    // A jump larger than twice the visible height between two adjacent samples
    // is a pole, not a line. Break rather than draw the join.
    const jumped = prev !== null && Number.isFinite(y) && Math.abs(y - prev) > span * 2;
    if (!inWorld || jumped) {
      if (cur.length > 1) out.push(cur.join(" "));
      cur = [];
      prev = Number.isFinite(y) ? y : null;
      continue;
    }
    cur.push(`${cur.length ? "L" : "M"}${px(x).toFixed(2)},${py(y).toFixed(2)}`);
    prev = y;
  }
  if (cur.length > 1) out.push(cur.join(" "));
  return out;
}

/** A y-window that shows the functions rather than their worst sample. The
 *  2nd–98th percentile sets the scale, so one spike near an asymptote cannot
 *  flatten everything else into a horizontal line. */
function autoY(fs: ((x: number) => number)[], x0: number, x1: number): [number, number] {
  const vals: number[] = [];
  for (const f of fs) {
    for (let i = 0; i <= 240; i++) {
      const v = f(x0 + ((x1 - x0) * i) / 240);
      if (Number.isFinite(v)) vals.push(v);
    }
  }
  if (!vals.length) return [-10, 10];
  const sorted = [...vals].sort((a, b) => a - b);
  const lo = sorted[Math.floor(sorted.length * 0.02)];
  const hi = sorted[Math.ceil(sorted.length * 0.98) - 1];
  let a = Math.min(lo, 0), b = Math.max(hi, 0);
  if (b - a < 1e-9) { a -= 1; b += 1; }
  const pad = (b - a) * 0.08;
  return [a - pad, b + pad];
}

/** Round grid steps a person recognises: 1, 2, 5 and their decades. */
function step(span: number): number {
  const raw = span / 7;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / mag;
  return (n >= 5 ? 5 : n >= 2 ? 2 : 1) * mag;
}

/** A tick label, short. An axis has no room for `6.283185307179586`, and the
 *  point of the number is the scale rather than the value. */
function tick(v: number): string {
  if (Math.abs(v) < 1e-12) return "0";
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(0);
  return String(Number(v.toPrecision(4)));
}

type Curve = { id: number; src: string; on: boolean };

function Plot({ curves, nodes, win, setWin, deg }: {
  curves: Curve[];
  nodes: (Node | null)[];
  win: Win;
  setWin: (w: Win) => void;
  deg: boolean;
}) {
  // Margins, because a plot without numbered axes is a picture of a curve
  // rather than a reading of one. `sin(x)/x` in degree mode over ±10 is a flat
  // line at y ≈ 0.019 — correct, and indistinguishable from a broken plot
  // until the axis says 0.019.
  const W = 520, H = 340, L = 46, B = 24, T = 10, R = 10;
  const pw = W - L - R, ph = H - T - B;
  const svg = useRef<SVGSVGElement>(null);
  const [trace, setTrace] = useState<number | null>(null);
  const drag = useRef<{ x: number; y: number; win: Win } | null>(null);

  const fs = useMemo(
    () => nodes.map(n => (n ? sampler(n, "x", { deg }) : () => NaN)),
    [nodes, deg]);

  const segs = useMemo(
    () => fs.map((f, i) => (curves[i]?.on && nodes[i] ? paths(f, win, pw, ph) : [])),
    [fs, curves, nodes, win, pw, ph]);

  const px = (x: number) => L + ((x - win.x0) / (win.x1 - win.x0)) * pw;
  const py = (y: number) => T + ph - ((y - win.y0) / (win.y1 - win.y0)) * ph;
  const sx = step(win.x1 - win.x0), sy = step(win.y1 - win.y0);
  const xs: number[] = [], ys: number[] = [];
  for (let v = Math.ceil(win.x0 / sx) * sx; v <= win.x1 + 1e-9; v += sx)
    xs.push(Number(v.toFixed(10)));
  for (let v = Math.ceil(win.y0 / sy) * sy; v <= win.y1 + 1e-9; v += sy)
    ys.push(Number(v.toFixed(10)));

  /** Viewport pixels → world units. The SVG scales to its box, so the ratio
   *  has to come from the live rect rather than from W and H. */
  const world = (ev: { clientX: number; clientY: number }) => {
    const r = svg.current?.getBoundingClientRect();
    if (!r) return null;
    const vx = ((ev.clientX - r.left) / r.width) * W;
    const vy = ((ev.clientY - r.top) / r.height) * H;
    return {
      x: win.x0 + ((vx - L) / pw) * (win.x1 - win.x0),
      y: win.y0 + ((T + ph - vy) / ph) * (win.y1 - win.y0),
      vx, vy,
    };
  };

  const onMove = (ev: React.PointerEvent) => {
    const w = world(ev);
    if (!w) return;
    if (drag.current) {
      // Pan: the point under the pointer stays under the pointer.
      const dx = ((ev.clientX - drag.current.x) / (svg.current!.getBoundingClientRect().width))
                 * W / pw * (drag.current.win.x1 - drag.current.win.x0);
      const dy = ((ev.clientY - drag.current.y) / (svg.current!.getBoundingClientRect().height))
                 * H / ph * (drag.current.win.y1 - drag.current.win.y0);
      const b = drag.current.win;
      setWin({ x0: b.x0 - dx, x1: b.x1 - dx, y0: b.y0 + dy, y1: b.y1 + dy });
      return;
    }
    setTrace(w.vx >= L && w.vx <= L + pw ? w.x : null);
  };

  const zoom = (k: number, at?: { x: number; y: number }) => {
    const cx = at?.x ?? (win.x0 + win.x1) / 2;
    const cy = at?.y ?? (win.y0 + win.y1) / 2;
    setWin({
      x0: cx + (win.x0 - cx) * k, x1: cx + (win.x1 - cx) * k,
      y0: cy + (win.y0 - cy) * k, y1: cy + (win.y1 - cy) * k,
    });
  };

  // Wheel-zoom is bound by hand rather than with `onWheel`, because React
  // registers wheel at the root as a *passive* listener: the zoom would work
  // and the dock would scroll underneath it at the same time. A plot that
  // slides away while you zoom into it is worse than one that does not zoom.
  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const w = world(e);
      zoom(e.deltaY > 0 ? 1.15 : 1 / 1.15, w ?? undefined);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  return (
    <div className="calc-plotwrap">
      <svg ref={svg} className="calc-plot" viewBox={`0 0 ${W} ${H}`}
           role="img" aria-label={`plot, x from ${show(win.x0)} to ${show(win.x1)}, `
                                  + `y from ${show(win.y0)} to ${show(win.y1)}`}
           onPointerDown={e => {
             drag.current = { x: e.clientX, y: e.clientY, win };
             (e.target as Element).setPointerCapture?.(e.pointerId);
           }}
           onPointerUp={e => {
             drag.current = null;
             (e.target as Element).releasePointerCapture?.(e.pointerId);
           }}
           onPointerLeave={() => { drag.current = null; setTrace(null); }}
           onPointerMove={onMove}>
        {xs.map(v => (
          <g key={`x${v}`}>
            <line className="calc-grid" x1={px(v)} y1={T} x2={px(v)} y2={T + ph} />
            <text className="calc-tick" x={px(v)} y={H - 8} textAnchor="middle">{tick(v)}</text>
          </g>
        ))}
        {ys.map(v => (
          <g key={`y${v}`}>
            <line className="calc-grid" x1={L} y1={py(v)} x2={L + pw} y2={py(v)} />
            <text className="calc-tick" x={L - 6} y={py(v) + 4} textAnchor="end">{tick(v)}</text>
          </g>
        ))}
        {win.y0 <= 0 && win.y1 >= 0 && (
          <line className="calc-axis" x1={L} y1={py(0)} x2={L + pw} y2={py(0)} />
        )}
        {win.x0 <= 0 && win.x1 >= 0 && (
          <line className="calc-axis" x1={px(0)} y1={T} x2={px(0)} y2={T + ph} />
        )}
        <g transform={`translate(${L} ${T})`}>
          {segs.map((ss, i) => ss.map((d, j) => (
            <path key={`${i}-${j}`} className="calc-curve" d={d}
                  style={{ stroke: `var(--curve-${(i % CURVES) + 1})` }} />
          )))}
        </g>
        {trace !== null && (
          <g className="calc-trace">
            <line x1={px(trace)} y1={T} x2={px(trace)} y2={T + ph} />
            {fs.map((f, i) => {
              if (!curves[i]?.on || !nodes[i]) return null;
              const y = f(trace);
              if (!Number.isFinite(y) || y < win.y0 || y > win.y1) return null;
              return <circle key={i} cx={px(trace)} cy={py(y)} r={3.5}
                             style={{ fill: `var(--curve-${(i % CURVES) + 1})` }} />;
            })}
          </g>
        )}
      </svg>

      <div className="calc-readout">
        {trace === null ? (
          <span className="dim">drag to pan · scroll to zoom · hover to read a value</span>
        ) : (
          <>
            <span className="calc-at">x = {show(trace, 6)}</span>
            {fs.map((f, i) => {
              if (!curves[i]?.on || !nodes[i]) return null;
              const y = f(trace);
              return (
                <span key={i} className="calc-yv">
                  <i style={{ background: `var(--curve-${(i % CURVES) + 1})` }} />
                  {Number.isFinite(y) ? show(y, 6) : "—"}
                </span>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// the slot
// --------------------------------------------------------------------------

type Tape = { src: string; out: string; ok: boolean };

export default function Calculator(
  { scratchAppend }: { scratchAppend?: (line: string) => void },
) {
  const [mode, setMode] = useState<Mode>(() => {
    try {
      const m = localStorage.getItem(MODE_KEY);
      return m === "graph" || m === "algebra" ? m : "calc";
    } catch { return "calc"; }
  });
  const [deg, setDeg] = useState(() => {
    try { return localStorage.getItem(DEG_KEY) !== "rad"; } catch { return true; }
  });
  useEffect(() => {
    try { localStorage.setItem(MODE_KEY, mode); } catch { /* private mode */ }
  }, [mode]);
  useEffect(() => {
    try { localStorage.setItem(DEG_KEY, deg ? "deg" : "rad"); } catch { /* private mode */ }
  }, [deg]);

  // ---- calc pane
  const [src, setSrc] = useState("");
  const [tape, setTape] = useState<Tape[]>([]);
  const [ans, setAns] = useState(0);
  const [mem, setMem] = useState(0);
  const [inv, setInv] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  // ---- graph pane
  const [curves, setCurves] = useState<Curve[]>([{ id: 1, src: "", on: true }]);
  const [win, setWin] = useState<Win>({ x0: -10, x1: 10, y0: -10, y1: 10 });
  const [autoWin, setAutoWin] = useState(true);

  // ---- algebra pane
  const [aSrc, setASrc] = useState("");
  const [aVar, setAVar] = useState("x");
  const [out, setOut] = useState<CasOut | null>(null);
  const [aErr, setAErr] = useState<string | null>(null);

  /* ----------------------------------------------------------- calc pane */

  // Compiled on every keystroke so the answer is live, and so an error names
  // itself while you are still looking at the thing that caused it.
  const live = useMemo(() => {
    const s = src.trim();
    if (!s) return null;
    try {
      const node = parse(s);
      return { node, err: null as string | null };
    } catch (e) {
      return { node: null, err: e instanceof Error ? e.message : String(e) };
    }
  }, [src]);

  const freeVars = live?.node ? varsOf(live.node).filter(v => v !== "ans") : [];
  const value = useMemo(() => {
    if (!live?.node || freeVars.length) return null;
    try {
      const v = evaluate(live.node, { deg, env: { ans } });
      return Number.isNaN(v) ? null : v;
    } catch { return null; }
  }, [live, deg, ans, freeVars.length]);

  const insert = (frag: string) => {
    const el = input.current;
    const caret = frag.indexOf("#");
    const text = frag.replace("#", "");
    if (!el) { setSrc(s => s + text); return; }
    const a = el.selectionStart ?? src.length, b = el.selectionEnd ?? src.length;
    const next = src.slice(0, a) + text + src.slice(b);
    setSrc(next);
    // The caret lands where the `#` was — inside `sin(|)`, not after it.
    const at = a + (caret >= 0 ? caret : text.length);
    requestAnimationFrame(() => { el.focus(); el.setSelectionRange(at, at); });
  };

  const commit = () => {
    if (!live) return;
    if (live.err) {
      setTape(t => [...t, { src, out: live.err!, ok: false }].slice(-40));
      return;
    }
    if (freeVars.length) {
      // A field with an `x` in it is a function, and the answer to a function
      // is a graph. Hand it to the pane that draws one rather than refusing.
      toGraph(src.trim());
      return;
    }
    const v = evaluate(live.node!, { deg, env: { ans } });
    setAns(v);
    setTape(t => [...t, { src, out: show(v), ok: true }].slice(-40));
    setSrc("");
  };

  const act = (a: NonNullable<Key["act"]>) => {
    switch (a) {
      case "clear": setSrc(""); input.current?.focus(); break;
      case "back": {
        const el = input.current;
        const at = el?.selectionStart ?? src.length;
        if (at > 0) {
          setSrc(src.slice(0, at - 1) + src.slice(el?.selectionEnd ?? at));
          requestAnimationFrame(() => {
            el?.focus(); el?.setSelectionRange(at - 1, at - 1);
          });
        }
        break;
      }
      case "equals": commit(); break;
      case "sign": setSrc(s => (s.startsWith("-(") && s.endsWith(")")
        ? s.slice(2, -1) : s.trim() ? `-(${s})` : "-")); break;
      case "deg": setDeg(d => !d); break;
      case "inv": setInv(i => !i); break;
      case "mc": setMem(0); break;
      case "mr": insert(show(mem)); break;
      case "mplus": setMem(m => m + (value ?? ans)); break;
      case "mminus": setMem(m => m - (value ?? ans)); break;
    }
  };

  /* ---------------------------------------------------------- graph pane */

  const nodes = useMemo(
    () => curves.map(c => {
      const s = c.src.trim();
      if (!s) return null;
      try { return parse(s); } catch { return null; }
    }), [curves]);

  const errs = useMemo(
    () => curves.map(c => {
      const s = c.src.trim();
      if (!s) return null;
      try { parse(s); return null; }
      catch (e) { return e instanceof Error ? e.message : String(e); }
    }), [curves]);

  // The y-window follows the curves while it is on auto, and stops the moment
  // you touch a number or drag the plot — an auto-fit that keeps snapping back
  // over your own pan is a plot you cannot steer.
  useEffect(() => {
    if (!autoWin) return;
    const fs = nodes
      .map((n, i) => (n && curves[i].on ? sampler(n, "x", { deg }) : null))
      .filter((f): f is (x: number) => number => f !== null);
    if (!fs.length) return;
    const [y0, y1] = autoY(fs, win.x0, win.x1);
    setWin(w => (Math.abs(w.y0 - y0) < 1e-9 && Math.abs(w.y1 - y1) < 1e-9
      ? w : { ...w, y0, y1 }));
  }, [nodes, curves, deg, autoWin, win.x0, win.x1]);

  const toGraph = (s: string) => {
    setCurves(cs => {
      const empty = cs.findIndex(c => !c.src.trim());
      if (empty >= 0) {
        const next = [...cs];
        next[empty] = { ...next[empty], src: s, on: true };
        return next;
      }
      if (cs.length >= CURVES) return [...cs.slice(0, -1), { ...cs[cs.length - 1], src: s, on: true }];
      return [...cs, { id: Math.max(0, ...cs.map(c => c.id)) + 1, src: s, on: true }];
    });
    setMode("graph");
  };

  /* -------------------------------------------------------- algebra pane */

  const aVars = useMemo(() => {
    try {
      const { lhs, rhs } = parseEquation(aSrc);
      const vs = [...varsOf(lhs), ...varsOf(rhs)].filter((v, i, a) => a.indexOf(v) === i);
      return vs.length ? vs : ["x"];
    } catch { return ["x"]; }
  }, [aSrc]);
  useEffect(() => {
    if (!aVars.includes(aVar)) setAVar(aVars[0]);
  }, [aVars, aVar]);

  const doOp = (op: Op) => {
    setAErr(null);
    try {
      const { lhs, rhs } = parseEquation(aSrc);
      if (op === "solve" || !aSrc.includes("=")) {
        setOut(casRun(op, lhs, rhs, aVar));
        return;
      }
      // An equation handed to simplify/expand/factor/d/dx is two expressions,
      // and doing the operation to the left side alone would silently drop
      // half the input. Move it all to one side first, and say so.
      setOut({
        ...casRun(op, sub(lhs, rhs), ZERO, aVar),
        note: "applied to (left − right), since you gave an equation",
      });
    } catch (e) {
      setOut(null);
      setAErr(e instanceof Error ? e.message : String(e));
    }
  };

  /* ------------------------------------------------------------- render */

  return (
    <div className="calc">
      <div className="calc-modes wb-seg" role="tablist" aria-label="Calculator mode">
        {(["calc", "graph", "algebra"] as Mode[]).map(m => (
          <button key={m} role="tab" aria-selected={mode === m}
                  className={mode === m ? "on" : ""}
                  onClick={() => setMode(m)}>
            {m === "calc" ? "Calculator" : m === "graph" ? "Graph" : "Algebra"}
          </button>
        ))}
      </div>

      {mode === "calc" && (
        <>
          <div className="calc-head">
            <div className="wb-seg" role="group" aria-label="Angle unit">
              <button className={deg ? "on" : ""} onClick={() => setDeg(true)}>deg</button>
              <button className={!deg ? "on" : ""} onClick={() => setDeg(false)}>rad</button>
            </div>
            {mem !== 0 && (
              <button className="wb-btn wb-btn-quiet" onClick={() => act("mc")}
                      title="clear the memory">M = {show(mem, 6)} ✕</button>
            )}
            {tape.length > 0 && (
              <button className="wb-btn wb-btn-quiet" onClick={() => setTape([])}>
                clear tape
              </button>
            )}
          </div>

          {tape.length > 0 && (
            <ol className="calc-tape">
              {tape.map((t, i) => (
                <li key={i} className={t.ok ? "" : "bad"}>
                  <button className="calc-recall" title="put this back in the field"
                          onClick={() => insert(t.src)}>{t.src}</button>
                  <span className="calc-out">{t.ok ? `= ${t.out}` : t.out}</span>
                </li>
              ))}
            </ol>
          )}

          <div className="calc-display">
            <input ref={input} className="calc-in" value={src} spellCheck={false}
                   placeholder="40 * 0.3   ·   sin(30)   ·   2^10"
                   aria-label="Expression"
                   onChange={e => setSrc(e.target.value)}
                   onKeyDown={e => {
                     if (e.key === "Enter") { e.preventDefault(); commit(); }
                     // The workbench binds single letters; a calculator field
                     // must keep its own keystrokes.
                     e.stopPropagation();
                   }} />
            <p className="calc-now">
              <span className="calc-eq">=</span>
              <span className="calc-val">
                {live?.err ? <span className="calc-err">{live.err}</span>
                  : freeVars.length ? <span className="dim">
                      a function of {freeVars.join(", ")} — Enter graphs it
                    </span>
                  : value !== null ? show(value)
                  : <span className="dim">{show(ans)}</span>}
              </span>
              {scratchAppend && value !== null && (
                <button className="wb-btn wb-btn-quiet"
                        title="append this line to the work pad"
                        onClick={() => scratchAppend(`${src.trim()} = ${show(value)}`)}>
                  → work
                </button>
              )}
            </p>
          </div>

          <div className={`calc-pad ${inv ? "is-inv" : ""}`}>
            {KEYS.map((k, i) => {
              const second = inv && k.ins2 !== undefined;
              const label = second ? k.t2! : k.t;
              // Only `2nd` lights up. The angle key *shows* the current unit
              // rather than lighting when it is one of the two — a key reading
              // "rad" and highlighted is two signals for one fact, and the
              // segmented control above already says which is selected.
              const on = k.act === "inv" && inv;
              return (
                <button key={i} type="button"
                        className={`calc-key ${k.cls ? `k-${k.cls}` : ""} ${on ? "on" : ""}`}
                        title={k.title ?? (k.t2 ? `2nd: ${k.t2}` : undefined)}
                        onClick={() => {
                          if (k.act) act(k.act);
                          else insert(second ? k.ins2! : k.ins!);
                          if (second) setInv(false);   // 2nd is one key deep
                        }}>
                  {k.act === "deg" ? (deg ? "deg" : "rad") : label}
                </button>
              );
            })}
          </div>

          <p className="calc-note dim">
            Enter keeps the line and stores <code>ans</code>. Implicit products
            (<code>2x</code>, <code>3(x+1)</code>) and <code>!</code> are read the
            way you write them.
          </p>
        </>
      )}

      {mode === "graph" && (
        <>
          <div className="calc-fns">
            {curves.map((c, i) => (
              <div key={c.id} className="calc-fn">
                <button className={`calc-swatch ${c.on ? "" : "off"}`}
                        style={{ background: `var(--curve-${(i % CURVES) + 1})` }}
                        title={c.on ? "hide this curve" : "show this curve"}
                        aria-label={c.on ? "hide this curve" : "show this curve"}
                        onClick={() => setCurves(cs => cs.map(
                          (x, j) => (j === i ? { ...x, on: !x.on } : x)))} />
                <span className="calc-fn-y">y =</span>
                <input className="calc-fn-in" value={c.src} spellCheck={false}
                       placeholder={i === 0 ? "x^2 - 4" : "another curve"}
                       aria-label={`function ${i + 1}`}
                       onChange={e => setCurves(cs => cs.map(
                         (x, j) => (j === i ? { ...x, src: e.target.value } : x)))}
                       onKeyDown={e => e.stopPropagation()} />
                {curves.length > 1 && (
                  <button className="calc-fn-x" title="remove this curve"
                          aria-label="remove this curve"
                          onClick={() => setCurves(cs => cs.filter((_, j) => j !== i))}>
                    ✕
                  </button>
                )}
              </div>
            ))}
            {errs.map((e, i) => e && (
              <p key={i} className="calc-err">y{i + 1}: {e}</p>
            ))}
            {curves.length < CURVES && (
              <button className="wb-btn wb-btn-quiet calc-add"
                      onClick={() => setCurves(cs => [...cs,
                        { id: Math.max(0, ...cs.map(c => c.id)) + 1, src: "", on: true }])}>
                + another curve
              </button>
            )}
          </div>

          <Plot curves={curves} nodes={nodes} win={win} deg={deg}
                setWin={w => { setAutoWin(false); setWin(w); }} />

          <div className="calc-win">
            <label>x <input type="number" value={win.x0} aria-label="x minimum"
                            onChange={e => setWin(w => ({ ...w, x0: Number(e.target.value) }))}
                            onKeyDown={e => e.stopPropagation()} /></label>
            <label>to <input type="number" value={win.x1} aria-label="x maximum"
                             onChange={e => setWin(w => ({ ...w, x1: Number(e.target.value) }))}
                             onKeyDown={e => e.stopPropagation()} /></label>
            <label>y <input type="number" value={Number(win.y0.toPrecision(6))}
                            aria-label="y minimum" disabled={autoWin}
                            onChange={e => setWin(w => ({ ...w, y0: Number(e.target.value) }))}
                            onKeyDown={e => e.stopPropagation()} /></label>
            <label>to <input type="number" value={Number(win.y1.toPrecision(6))}
                             aria-label="y maximum" disabled={autoWin}
                             onChange={e => setWin(w => ({ ...w, y1: Number(e.target.value) }))}
                             onKeyDown={e => e.stopPropagation()} /></label>
          </div>
          <div className="calc-winbtns">
            <div className="wb-seg" role="group" aria-label="y fit">
              <button className={autoWin ? "on" : ""} onClick={() => setAutoWin(true)}>
                fit y
              </button>
              <button className={!autoWin ? "on" : ""} onClick={() => setAutoWin(false)}>
                manual
              </button>
            </div>
            <button className="wb-btn wb-btn-quiet" onClick={() => {
              setAutoWin(false);
              setWin(w => {
                const cx = (w.x0 + w.x1) / 2, cy = (w.y0 + w.y1) / 2;
                return { x0: cx + (w.x0 - cx) / 2, x1: cx + (w.x1 - cx) / 2,
                         y0: cy + (w.y0 - cy) / 2, y1: cy + (w.y1 - cy) / 2 };
              });
            }}>zoom in</button>
            <button className="wb-btn wb-btn-quiet" onClick={() => {
              setAutoWin(false);
              setWin(w => {
                const cx = (w.x0 + w.x1) / 2, cy = (w.y0 + w.y1) / 2;
                return { x0: cx + (w.x0 - cx) * 2, x1: cx + (w.x1 - cx) * 2,
                         y0: cy + (w.y0 - cy) * 2, y1: cy + (w.y1 - cy) * 2 };
              });
            }}>zoom out</button>
            <button className="wb-btn wb-btn-quiet" onClick={() => {
              setAutoWin(true);
              setWin({ x0: -10, x1: 10, y0: -10, y1: 10 });
            }}>reset</button>
            <div className="wb-seg" role="group" aria-label="Angle unit">
              <button className={deg ? "on" : ""} onClick={() => setDeg(true)}>deg</button>
              <button className={!deg ? "on" : ""} onClick={() => setDeg(false)}>rad</button>
            </div>
          </div>
        </>
      )}

      {mode === "algebra" && (
        <>
          <input className="calc-in" value={aSrc} spellCheck={false}
                 placeholder="x^2 - 5x + 6 = 0   ·   sin(x)/x   ·   (x+1)^3"
                 aria-label="Expression or equation"
                 onChange={e => setASrc(e.target.value)}
                 onKeyDown={e => {
                   if (e.key === "Enter") { e.preventDefault(); doOp("simplify"); }
                   e.stopPropagation();
                 }} />

          <div className="calc-ops">
            <button className="wb-btn" onClick={() => doOp("simplify")}>Simplify</button>
            <button className="wb-btn" onClick={() => doOp("expand")}>Expand</button>
            <button className="wb-btn" onClick={() => doOp("factor")}>Factor</button>
            <button className="wb-btn" onClick={() => doOp("diff")}>d/d{aVar}</button>
            <button className="wb-btn" onClick={() => doOp("integrate")}>∫ d{aVar}</button>
            <button className="wb-btn wb-btn-primary" onClick={() => doOp("solve")}>
              Solve
            </button>
            {aVars.length > 1 && (
              <label className="calc-var">
                in
                <select value={aVar} onChange={e => setAVar(e.target.value)}
                        onKeyDown={e => e.stopPropagation()}>
                  {aVars.map(v => <option key={v} value={v}>{v}</option>)}
                </select>
              </label>
            )}
          </div>

          {aErr && <p className="calc-err">{aErr}</p>}

          {out && (
            <div className="calc-res">
              <p className="calc-res-h">{out.label}</p>
              {out.tex && <MathBlock tex={out.tex} />}
              {out.roots && (out.roots.length ? (
                <ul className="calc-roots">
                  {out.roots.map((r, i) => (
                    <li key={i}>
                      <MathBlock tex={`${aVar} = ${r.tex}`} />
                      {r.value !== null && !/^[-\d.]+$/.test(r.tex) && (
                        <span className="dim">≈ {show(r.value, 8)}</span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : <p className="dim">no solution found</p>)}
              {out.note && <p className="calc-res-n dim">{out.note}</p>}
              <div className="calc-res-act">
                {scratchAppend && (out.text || out.roots) && (
                  <button className="wb-btn wb-btn-quiet"
                          title="append this to the work pad"
                          onClick={() => scratchAppend(out.text
                            ? `${aSrc.trim()}  →  ${out.text}`
                            : `${aSrc.trim()}  →  ${aVar} = ${out.roots!
                                .map(r => (r.value !== null ? show(r.value, 8) : r.tex))
                                .join(", ")}`)}>
                    → work
                  </button>
                )}
                {out.text && (
                  <button className="wb-btn wb-btn-quiet"
                          title="draw this in the graph pane"
                          onClick={() => toGraph(out.text!)}>
                    → graph
                  </button>
                )}
              </div>
            </div>
          )}

          <p className="calc-note dim">
            Write an equation with <code>=</code> to solve it, or an expression on
            its own. The algebra works in <b>radians</b> and over the rationals —
            when something does not factor or integrate in that world it says so
            rather than guessing.
          </p>
        </>
      )}
    </div>
  );
}
