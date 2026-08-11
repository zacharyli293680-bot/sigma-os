/*
 * calc.tsx — the workbench's calculator slot: arithmetic, and a plot.
 *
 * **No `eval`, and no library.** The app's standing rule is that a vault note
 * is data to a parser and never something a browser executes — `chat.tsx`
 * states it for HTML, `math.tsx` for TeX, `figure.tsx` for SVG. An expression
 * typed here is the same kind of thing, so it gets the same treatment: a
 * tokeniser and a recursive-descent parser, and the only things it can name are
 * the constants and functions on the table below. `eval` would have been three
 * lines and would have handed the page's whole scope to a text field.
 *
 * The parse produces a closure over `x`, which is what makes one implementation
 * serve both halves: `2 + 2 * 3` is that closure sampled once, and `sin(x)/x`
 * is the same closure sampled six hundred times across a range. There is no
 * separate "graphing mode" — an expression mentioning `x` simply gets a plot
 * under the answer.
 *
 * Everything is deliberately kept to what a statics course actually needs at
 * the desk: degrees as well as radians (bearings and angles of incline are
 * written in degrees in every AA-210 note), a running tape, and `ans` for the
 * previous result.
 */
import { useMemo, useRef, useState } from "react";

// --------------------------------------------------------------------------
// the language
// --------------------------------------------------------------------------

/** Everything a name is allowed to mean. A name that is not here is an error
 *  naming itself, never a silent zero — `sni(30)` should say what is wrong,
 *  not quietly plot the x-axis. */
const CONSTS: Record<string, number> = {
  pi: Math.PI, e: Math.E, tau: Math.PI * 2,
  g: 9.81,          // the one piece of course furniture: m/s²
};

type Fn1 = (v: number) => number;
const FNS: Record<string, Fn1 | ((...a: number[]) => number)> = {
  sin: Math.sin, cos: Math.cos, tan: Math.tan,
  asin: Math.asin, acos: Math.acos, atan: Math.atan,
  sinh: Math.sinh, cosh: Math.cosh, tanh: Math.tanh,
  sqrt: Math.sqrt, cbrt: Math.cbrt, abs: Math.abs,
  ln: Math.log, log: Math.log10, log2: Math.log2, exp: Math.exp,
  floor: Math.floor, ceil: Math.ceil, round: Math.round, sign: Math.sign,
  atan2: Math.atan2, min: Math.min, max: Math.max, hypot: Math.hypot,
  mod: (a: number, b: number) => a % b,
};
/** Functions whose argument is an angle, and those whose *result* is one — the
 *  two lists degree mode has to bend, in opposite directions. */
const TAKES_ANGLE = new Set(["sin", "cos", "tan"]);
const GIVES_ANGLE = new Set(["asin", "acos", "atan", "atan2"]);

type Tok =
  | { k: "num"; v: number }
  | { k: "name"; v: string }
  | { k: "op"; v: string };

function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === " " || c === "\t" || c === "_" || c === ",") {
      // `_` and `,` are digit separators a person types in 12_000 or 12,000.
      if (c === ",") out.push({ k: "op", v: "," });
      i++;
      continue;
    }
    if (/[0-9.]/.test(c)) {
      const m = /^\d*\.?\d+(?:[eE][+-]?\d+)?/.exec(src.slice(i));
      if (!m) throw new Error(`can't read a number at "${src.slice(i, i + 6)}"`);
      out.push({ k: "num", v: Number(m[0]) });
      i += m[0].length;
      continue;
    }
    if (/[a-zA-Z]/.test(c)) {
      const m = /^[a-zA-Z][a-zA-Z0-9]*/.exec(src.slice(i))!;
      out.push({ k: "name", v: m[0] });
      i += m[0].length;
      continue;
    }
    if ("+-*/^%()".includes(c)) {
      // `**` is how a keyboard-minded person writes a power.
      if (c === "*" && src[i + 1] === "*") { out.push({ k: "op", v: "^" }); i += 2; continue; }
      out.push({ k: "op", v: c });
      i++;
      continue;
    }
    if (c === "×") { out.push({ k: "op", v: "*" }); i++; continue; }
    if (c === "÷") { out.push({ k: "op", v: "/" }); i++; continue; }
    if (c === "−") { out.push({ k: "op", v: "-" }); i++; continue; }
    throw new Error(`"${c}" is not something I can read`);
  }
  return out;
}

type Compiled = {
  /** Evaluate at an x. Expressions with no `x` ignore it. */
  at: (x: number) => number;
  /** Whether `x` appears — the one thing that decides plot or no plot. */
  usesX: boolean;
};

/** Parse to a closure. Throws with a human sentence; the caller shows it. */
function compile(src: string, deg: boolean, ans: number): Compiled {
  const toks = tokenize(src);
  let p = 0;
  let usesX = false;
  const peek = () => toks[p];
  const eat = (v: string) => {
    const t = toks[p];
    if (t && t.k === "op" && t.v === v) { p++; return true; }
    return false;
  };
  const toRad = (v: number) => (deg ? (v * Math.PI) / 180 : v);
  const fromRad = (v: number) => (deg ? (v * 180) / Math.PI : v);

  type Node = (x: number) => number;

  function expr(): Node {
    let left = term();
    for (;;) {
      if (eat("+")) { const r = term(), l = left; left = x => l(x) + r(x); }
      else if (eat("-")) { const r = term(), l = left; left = x => l(x) - r(x); }
      else return left;
    }
  }
  function term(): Node {
    let left = unary();
    for (;;) {
      if (eat("*")) { const r = unary(), l = left; left = x => l(x) * r(x); }
      else if (eat("/")) { const r = unary(), l = left; left = x => l(x) / r(x); }
      else if (eat("%")) { const r = unary(), l = left; left = x => l(x) % r(x); }
      else if (implicitFollows()) {
        // `2x`, `3(x+1)`, `2sin(x)` — written constantly on paper, and a
        // calculator that refuses them is a calculator you stop using.
        const r = unary(), l = left; left = x => l(x) * r(x);
      } else return left;
    }
  }
  /** Is the next token one that may begin a factor with no operator before it?
   *  Deliberately not a name directly after a name: `x pi` is a typo, not a
   *  product, and reading it as one hides the typo. */
  function implicitFollows(): boolean {
    const t = peek();
    if (!t) return false;
    const prev = toks[p - 1];
    if (t.k === "num") return prev?.k === "name" || (prev?.k === "op" && prev.v === ")");
    if (t.k === "name") return prev?.k === "num" || (prev?.k === "op" && prev.v === ")");
    if (t.k === "op" && t.v === "(") return prev?.k === "num" || (prev?.k === "op" && prev.v === ")");
    return false;
  }
  function unary(): Node {
    if (eat("-")) { const r = unary(); return x => -r(x); }
    if (eat("+")) return unary();
    return power();
  }
  function power(): Node {
    const base = atom();
    if (eat("^")) { const ex = unary(); return x => Math.pow(base(x), ex(x)); }
    return base;
  }
  function atom(): Node {
    const t = peek();
    if (!t) throw new Error("the expression stops early");
    if (t.k === "num") { p++; return () => t.v; }
    if (t.k === "op" && t.v === "(") {
      p++;
      const inner = expr();
      if (!eat(")")) throw new Error("a ( is never closed");
      return inner;
    }
    if (t.k === "name") {
      p++;
      const name = t.v;
      const lower = name.toLowerCase();
      if (peek()?.k === "op" && (peek() as { v: string }).v === "(") {
        p++;
        const args: Node[] = [];
        if (!eat(")")) {
          do { args.push(expr()); } while (eat(","));
          if (!eat(")")) throw new Error(`${name}( is never closed`);
        }
        const fn = FNS[lower];
        if (!fn) throw new Error(`I don't know a function called "${name}"`);
        const takes = TAKES_ANGLE.has(lower), gives = GIVES_ANGLE.has(lower);
        return x => {
          const vs = args.map(a => a(x));
          const call = takes ? [toRad(vs[0]), ...vs.slice(1)] : vs;
          const out = (fn as (...a: number[]) => number)(...call);
          return gives ? fromRad(out) : out;
        };
      }
      if (lower === "x") { usesX = true; return x => x; }
      if (lower === "ans") return () => ans;
      if (lower in CONSTS) return () => CONSTS[lower];
      throw new Error(`I don't know what "${name}" is`);
    }
    throw new Error(`"${(t as { v: string | number }).v}" can't start a value`);
  }

  const root = expr();
  if (p < toks.length) {
    const t = toks[p] as { v: string | number };
    throw new Error(`there is a stray "${t.v}" at the end`);
  }
  return { at: root, usesX };
}

/** A number as a person would write it: enough precision to be useful, no
 *  floating-point lint (`0.30000000000000004`), and exponent form only when
 *  the plain form would be unreadable. */
function show(v: number): string {
  if (!Number.isFinite(v)) return Number.isNaN(v) ? "undefined" : (v > 0 ? "∞" : "−∞");
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e10 || a < 1e-6) return v.toExponential(6).replace(/e([+-])/, "e$1");
  const r = Number(v.toPrecision(12));
  return String(r);
}

// --------------------------------------------------------------------------
// the plot
// --------------------------------------------------------------------------

const SAMPLES = 600;

/** Sample `f` across [x0,x1] and return SVG path segments, split wherever the
 *  curve leaves the world — a vertical asymptote must be a gap, not a stroke
 *  drawn straight down through the plot. */
function paths(f: (x: number) => number, x0: number, x1: number,
               y0: number, y1: number, w: number, h: number): string[] {
  const out: string[] = [];
  let cur: string[] = [];
  const px = (x: number) => ((x - x0) / (x1 - x0)) * w;
  const py = (y: number) => h - ((y - y0) / (y1 - y0)) * h;
  let prev: number | null = null;
  for (let i = 0; i <= SAMPLES; i++) {
    const x = x0 + ((x1 - x0) * i) / SAMPLES;
    let y: number;
    try { y = f(x); } catch { y = NaN; }
    const inWorld = Number.isFinite(y) && y >= y0 - (y1 - y0) && y <= y1 + (y1 - y0);
    // A jump larger than the whole visible height between two adjacent samples
    // is a pole, not a line. Break rather than draw the join.
    const jumped = prev !== null && Number.isFinite(y) && Math.abs(y - prev) > (y1 - y0) * 2;
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

/** A y-window that shows the function rather than its worst sample. The median
 *  absolute value sets the scale, so one spike near an asymptote cannot flatten
 *  everything else into a horizontal line. */
function yWindow(f: (x: number) => number, x0: number, x1: number): [number, number] {
  const vals: number[] = [];
  for (let i = 0; i <= 200; i++) {
    let v: number;
    try { v = f(x0 + ((x1 - x0) * i) / 200); } catch { continue; }
    if (Number.isFinite(v)) vals.push(v);
  }
  if (!vals.length) return [-1, 1];
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
  const raw = span / 6;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / mag;
  return (n >= 5 ? 5 : n >= 2 ? 2 : 1) * mag;
}

/** A tick label, short. A plot axis has no room for `6.283185307179586`, and
 *  the point of the number is the scale rather than the value. */
function tick(v: number): string {
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(0);
  return String(Number(v.toPrecision(4)));
}

function Plot({ f, x0, x1 }: { f: (x: number) => number; x0: number; x1: number }) {
  // Margins, because a plot without numbered axes is a picture of a curve
  // rather than a reading of one. `sin(x)/x` in degree mode over ±10 is a flat
  // line at y ≈ 0.019 — correct, and indistinguishable from a broken plot until
  // the axis says 0.019.
  const W = 380, H = 232, L = 42, B = 20, T = 8, R = 8;
  const pw = W - L - R, ph = H - T - B;
  const [y0, y1] = useMemo(() => yWindow(f, x0, x1), [f, x0, x1]);
  const segs = useMemo(() => paths(f, x0, x1, y0, y1, pw, ph), [f, x0, x1, y0, y1, pw, ph]);
  const px = (x: number) => L + ((x - x0) / (x1 - x0)) * pw;
  const py = (y: number) => T + ph - ((y - y0) / (y1 - y0)) * ph;
  const sx = step(x1 - x0), sy = step(y1 - y0);
  const xs: number[] = [], ys: number[] = [];
  for (let v = Math.ceil(x0 / sx) * sx; v <= x1 + 1e-9; v += sx) xs.push(Number(v.toFixed(10)));
  for (let v = Math.ceil(y0 / sy) * sy; v <= y1 + 1e-9; v += sy) ys.push(Number(v.toFixed(10)));

  return (
    <svg className="calc-plot" viewBox={`0 0 ${W} ${H}`} role="img"
         aria-label={`plot from x = ${show(x0)} to x = ${show(x1)}, `
                     + `y from ${show(y0)} to ${show(y1)}`}>
      {xs.map(v => (
        <g key={`x${v}`}>
          <line className="calc-grid" x1={px(v)} y1={T} x2={px(v)} y2={T + ph} />
          <text className="calc-tick" x={px(v)} y={H - 6} textAnchor="middle">{tick(v)}</text>
        </g>
      ))}
      {ys.map(v => (
        <g key={`y${v}`}>
          <line className="calc-grid" x1={L} y1={py(v)} x2={L + pw} y2={py(v)} />
          <text className="calc-tick" x={L - 5} y={py(v) + 3} textAnchor="end">{tick(v)}</text>
        </g>
      ))}
      {y0 <= 0 && y1 >= 0 && (
        <line className="calc-axis" x1={L} y1={py(0)} x2={L + pw} y2={py(0)} />
      )}
      {x0 <= 0 && x1 >= 0 && (
        <line className="calc-axis" x1={px(0)} y1={T} x2={px(0)} y2={T + ph} />
      )}
      <g transform={`translate(${L} ${T})`}>
        {segs.map((d, i) => <path key={i} className="calc-curve" d={d} />)}
      </g>
    </svg>
  );
}

// --------------------------------------------------------------------------
// the slot
// --------------------------------------------------------------------------

type Tape = { src: string; out: string; ok: boolean };

/** The keypad. Only the keys that are faster to hit than to type — a full
 *  0–9 grid next to a focused text field is furniture, not a feature. */
const KEYS: [string, string][] = [
  ["(", "("], [")", ")"], ["^", "^"], ["√", "sqrt("],
  ["π", "pi"], ["sin", "sin("], ["cos", "cos("], ["tan", "tan("],
];

export default function Calculator(
  { scratchAppend }: { scratchAppend?: (line: string) => void },
) {
  const [src, setSrc] = useState("");
  const [deg, setDeg] = useState(true);
  const [tape, setTape] = useState<Tape[]>([]);
  const [ans, setAns] = useState(0);
  const [range, setRange] = useState<[number, number]>([-10, 10]);
  const input = useRef<HTMLInputElement>(null);

  // Compiled on every keystroke so the answer is live, and so an error names
  // itself while you are still looking at the thing that caused it.
  const live = useMemo(() => {
    const s = src.trim();
    if (!s) return null;
    try {
      const c = compile(s, deg, ans);
      return { c, err: null as string | null };
    } catch (e) {
      return { c: null, err: e instanceof Error ? e.message : String(e) };
    }
  }, [src, deg, ans]);

  const value = live?.c && !live.c.usesX ? live.c.at(0) : null;

  const commit = () => {
    if (!live) return;
    if (live.err) { setTape(t => [...t, { src, out: live.err!, ok: false }].slice(-40)); return; }
    const c = live.c!;
    const out = c.usesX ? "plotted" : show(c.at(0));
    if (!c.usesX) setAns(c.at(0));
    setTape(t => [...t, { src, out, ok: true }].slice(-40));
    setSrc("");
  };

  const insert = (frag: string) => {
    setSrc(s => s + frag);
    input.current?.focus();
  };

  return (
    <div className="calc">
      <div className="calc-head">
        <div className="wb-seg" role="group" aria-label="Angle unit">
          <button className={deg ? "on" : ""} onClick={() => setDeg(true)}>deg</button>
          <button className={!deg ? "on" : ""} onClick={() => setDeg(false)}>rad</button>
        </div>
        {tape.length > 0 && (
          <button className="wb-btn wb-btn-quiet" onClick={() => setTape([])}>clear tape</button>
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

      <input ref={input} className="calc-in" value={src} spellCheck={false}
             placeholder="40 * 0.3   ·   sin(30)   ·   x^2 - 4"
             aria-label="Expression"
             onChange={e => setSrc(e.target.value)}
             onKeyDown={e => {
               if (e.key === "Enter") { e.preventDefault(); commit(); }
               // The workbench binds single letters; a calculator field must
               // keep its own keystrokes.
               e.stopPropagation();
             }} />

      <div className="calc-keys">
        {KEYS.map(([label, frag]) => (
          <button key={label} className="wb-btn" onClick={() => insert(frag)}>{label}</button>
        ))}
      </div>

      {live?.err && src.trim() && <p className="calc-err">{live.err}</p>}

      {value !== null && (
        <p className="calc-now">
          <span className="calc-eq">=</span>
          <span className="calc-val">{show(value)}</span>
          {scratchAppend && (
            <button className="wb-btn wb-btn-quiet"
                    title="append this line to the work pad"
                    onClick={() => scratchAppend(`${src.trim()} = ${show(value)}`)}>
              → work
            </button>
          )}
        </p>
      )}

      {live?.c?.usesX && (
        <>
          <Plot f={live.c.at} x0={range[0]} x1={range[1]} />
          <div className="calc-range">
            <span className="dim">x from</span>
            <input type="number" value={range[0]} aria-label="x minimum"
                   onChange={e => setRange(([, b]) => [Number(e.target.value), b])}
                   onKeyDown={e => e.stopPropagation()} />
            <span className="dim">to</span>
            <input type="number" value={range[1]} aria-label="x maximum"
                   onChange={e => setRange(([a]) => [a, Number(e.target.value)])}
                   onKeyDown={e => e.stopPropagation()} />
            <button className="wb-btn wb-btn-quiet"
                    onClick={() => setRange(([a, b]) => {
                      const m = (a + b) / 2, half = (b - a) / 4;
                      return [m - half, m + half];
                    })}>in</button>
            <button className="wb-btn wb-btn-quiet"
                    onClick={() => setRange(([a, b]) => {
                      const m = (a + b) / 2, half = b - a;
                      return [m - half, m + half];
                    })}>out</button>
          </div>
        </>
      )}

      <p className="calc-note dim">
        Enter evaluates and keeps the line. <code>ans</code> is the last result;
        write <code>x</code> anywhere to get a plot instead of a number.
      </p>
    </div>
  );
}
