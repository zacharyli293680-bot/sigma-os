/*
 * cas.ts — the algebra half of the calculator: simplify, expand, factor,
 * differentiate, integrate, solve.
 *
 * Written rather than installed, for the reason the parser next door was:
 * everything in this app that reads a string reads it with a parser it owns.
 * A CAS is a larger thing to own than a tokeniser, so this one is deliberately
 * bounded — it does the algebra a first-year engineering course actually
 * asks for (polynomials, the standard function table, chain rule, linear-
 * substitution integrals, quadratics and rational roots) and **says so when a
 * problem is outside that** instead of returning something that looks like an
 * answer. `null` from `factor` or `integrate` is a real result here: it means
 * "not in this form, not by me", and the UI prints exactly that.
 *
 * ## Radians, always
 *
 * `evaluate()` bends the trig functions when the keypad is in degree mode.
 * Nothing here does, and nothing here may: d/dx sin(x) = cos(x) is *false* in
 * degrees (it is π/180 · cos x), and a CAS that quietly honoured a display
 * setting would return a wrong derivative with a straight face. The algebra
 * pane says radians on it for the same reason.
 *
 * ## On exactness
 *
 * Coefficients are JavaScript numbers, not a rational type. That is a real
 * limit and it shows up in exactly one place — a root like 1/3 arrives as
 * 0.3333333333333333 — so every rational that has to be *printed* goes through
 * `toFrac`, which recovers p/q by continued fractions and refuses (returns
 * null) when it cannot do so within a tolerance. Anything it refuses is
 * labelled numeric rather than dressed up as exact.
 */
import {
  type Node, num, sym, add, mul, pow, fn, neg, sub, div,
  ZERO, ONE, key, equal, isNum, has, splitCoef, splitPow,
  evaluate, latex, print, show, CONSTS, FNS,
} from "./expr";

// --------------------------------------------------------------------------
// rationals, only as far as printing needs them
// --------------------------------------------------------------------------

const EPS = 1e-10;
const near = (a: number, b: number) => Math.abs(a - b) <= EPS * Math.max(1, Math.abs(a), Math.abs(b));
const isInt = (v: number) => Number.isFinite(v) && Math.abs(v - Math.round(v)) < 1e-9;

/** p/q for a float, by continued fractions. `null` when the number is not a
 *  small rational — which is the honest answer for √2 and for 0.1 + 0.2 alike,
 *  and is what keeps "exact" meaning exact.
 *
 *  The two bounds are the whole design and they were both wrong once. A
 *  denominator cap of 100000 with a 1e-10 tolerance accepted **√8**: continued
 *  fractions converge like 1/q², so a five-digit denominator matches an
 *  irrational to about 1e-10, and `solve(x² = 2)` came back with a rational
 *  root and a straight face. A rational that a double can hold is exact to
 *  ~1e-16, so the tolerance is what separates the two, not the cap. */
export function toFrac(x: number, maxQ = 10000): [number, number] | null {
  if (!Number.isFinite(x)) return null;
  if (isInt(x)) return [Math.round(x), 1];
  const s = Math.sign(x);
  const target = Math.abs(x);
  const good = (n: number, d: number) =>
    Math.abs(n / d - target) <= 1e-13 * Math.max(1, target);
  let a = target;
  let [n0, d0, n1, d1] = [1, 0, Math.floor(a), 1];
  for (let i = 0; i < 40; i++) {
    const frac = a - Math.floor(a);
    if (frac < 1e-14) break;
    a = 1 / frac;
    const w = Math.floor(a);
    [n0, d0, n1, d1] = [n1, d1, w * n1 + n0, w * d1 + d0];
    if (d1 > maxQ) return null;
    if (good(n1, d1)) return [s * n1, d1];
  }
  return d1 <= maxQ && good(n1, d1) ? [s * n1, d1] : null;
}

const gcdI = (a: number, b: number): number => {
  a = Math.abs(Math.round(a)); b = Math.abs(Math.round(b));
  while (b) [a, b] = [b, a % b];
  return a;
};

/** A number as a node, written the way it should be read: 0.5 is a half, not
 *  a decimal, once it is standing in an algebraic answer. */
export function ratNode(x: number): Node {
  if (isInt(x)) return num(Math.round(x));
  const f = toFrac(x);
  return f ? div(num(f[0]), num(f[1])) : num(x);
}

// --------------------------------------------------------------------------
// simplify — the one function everything else is built on
// --------------------------------------------------------------------------

/** How far up an ordered sum a term sits. Highest degree first, which is how a
 *  polynomial is written everywhere outside a computer. Numbers sort last. */
function degOf(n: Node): number {
  switch (n.k) {
    case "num": return -Infinity;
    case "sym": return n.v in CONSTS ? -Infinity : 1;
    case "add": return Math.max(...n.xs.map(degOf));
    case "mul": return n.xs.reduce((s, x) => s + Math.max(0, degOf(x)), 0);
    case "pow": return isNum(n.b) ? degOf(n.a) * n.b.v : degOf(n.a);
    case "fn": return 0.5;      // above a constant, below anything polynomial
  }
}

function flat(k: "add" | "mul", xs: Node[]): Node[] {
  const out: Node[] = [];
  for (const x of xs) {
    if (x.k === k) out.push(...flat(k, x.xs));
    else out.push(x);
  }
  return out;
}

function sAdd(raw: Node[]): Node {
  const xs = flat("add", raw);
  let c = 0;
  // key of the non-numeric part → [coefficient, that part]
  const groups = new Map<string, [number, Node[]]>();
  for (const x of xs) {
    if (isNum(x)) { c += x.v; continue; }
    const [coef, rest] = splitCoef(x);
    const k = rest.map(key).join("*");
    const g = groups.get(k);
    if (g) g[0] += coef;
    else groups.set(k, [coef, rest]);
  }
  const terms: Node[] = [];
  for (const [coef, rest] of groups.values()) {
    if (near(coef, 0)) continue;
    const body = rest.length === 1 ? rest[0] : mul(...rest);
    terms.push(near(coef, 1) ? body : sMul([num(coef), body]));
  }
  terms.sort((a, b) => (degOf(b) - degOf(a)) || key(a).localeCompare(key(b)));
  if (!near(c, 0)) terms.push(num(c));
  if (!terms.length) return ZERO;
  return terms.length === 1 ? terms[0] : add(...terms);
}

function sMul(raw: Node[]): Node {
  const xs = flat("mul", raw);
  let c = 1;
  // key of the base → [summed exponent, that base]
  const groups = new Map<string, [Node[], Node]>();
  for (const x of xs) {
    if (isNum(x)) {
      if (x.v === 0) return ZERO;
      c *= x.v;
      continue;
    }
    const [b, e] = splitPow(x);
    const k = key(b);
    const g = groups.get(k);
    if (g) g[0].push(e);
    else groups.set(k, [[e], b]);
  }
  if (near(c, 0)) return ZERO;
  const factors: Node[] = [];
  for (const [es, b] of groups.values()) {
    const e = es.length === 1 ? es[0] : sAdd(es);
    if (isNum(e) && near(e.v, 0)) continue;
    if (isNum(e) && near(e.v, 1)) { factors.push(b); continue; }
    // (2x)^3 → 8x^3: pushing the constant out is what lets like terms meet.
    if (isNum(e) && isInt(e.v) && b.k === "mul") {
      const [bc, brest] = splitCoef(b);
      if (bc !== 1) {
        c *= Math.pow(bc, e.v);
        const inner = brest.length === 1 ? brest[0] : mul(...brest);
        factors.push(sPow(inner, e));
        continue;
      }
    }
    factors.push(pow(b, e));
  }
  // Symbols, then powers, then functions, then sums: `x(x + sin x)` rather
  // than `(x + sin x)x`. Sorting on the raw key put the bracket first, which
  // is nobody's handwriting.
  const rank = (n: Node) =>
    n.k === "sym" ? 0 : n.k === "pow" ? 1 : n.k === "fn" ? 2 : 3;
  factors.sort((a, b) => (rank(a) - rank(b)) || key(a).localeCompare(key(b)));
  if (!factors.length) return num(c);
  if (near(c, 1)) return factors.length === 1 ? factors[0] : mul(...factors);
  return mul(num(c), ...factors);
}

function sPow(a: Node, b: Node): Node {
  if (isNum(b)) {
    if (near(b.v, 0)) return ONE;
    if (near(b.v, 1)) return a;
  }
  if (isNum(a)) {
    if (near(a.v, 1)) return ONE;
    if (near(a.v, 0) && isNum(b) && b.v > 0) return ZERO;
  }
  if (isNum(a) && isNum(b)) {
    const r = Math.pow(a.v, b.v);
    // Fold only what stays exact: 2^10 and 4^0.5, never 2^0.5 — a CAS that
    // turns √2 into 1.4142135623730951 has thrown away the answer.
    if (Number.isFinite(r) && (isInt(b.v) ? Math.abs(r) < 1e15 : isInt(r))) {
      return num(isInt(r) ? Math.round(r) : r);
    }
    // √8 is 2√2. Pulling the square factor out is the one step between an
    // answer that is exact and an answer that is exact *and* readable.
    if (near(b.v, 0.5) && isInt(a.v) && a.v > 0 && a.v < 1e12) {
      const k = Math.round(a.v);
      for (let d = Math.floor(Math.sqrt(k)); d > 1; d--) {
        if (k % (d * d) === 0) return sMul([num(d), pow(num(k / (d * d)), b)]);
      }
    }
  }
  // (x^m)^n collapses only for integer n: (x²)^0.5 is |x|, not x.
  if (a.k === "pow" && isNum(b) && isInt(b.v)) return sPow(a.a, sMul([a.b, b]));
  if (a.k === "mul" && isNum(b) && isInt(b.v)) return sMul(a.xs.map(x => sPow(x, b)));
  return pow(a, b);
}

/** Functions the CAS may fold on numeric arguments even when the result is not
 *  an integer — they are arithmetic wearing a function's clothes. */
const ARITH = new Set(["abs", "floor", "ceil", "round", "sign", "mod", "gcd",
                       "lcm", "min", "max", "hypot", "fact", "ncr", "npr"]);

function sFn(f: string, xs: Node[]): Node {
  // sqrt and cbrt are powers wearing a name. Rewriting them here means the
  // power rule differentiates and integrates them for free, rather than every
  // rule below carrying its own √ case.
  if (f === "sqrt") return sPow(xs[0], num(0.5));
  if (f === "cbrt") return sPow(xs[0], div(ONE, num(3)));
  if (f === "root" && isNum(xs[0])) return sPow(xs[1], ratNode(1 / xs[0].v));
  if (f === "exp" && isNum(xs[0]) && near(xs[0].v, 0)) return ONE;
  if (f === "ln") {
    if (isNum(xs[0]) && near(xs[0].v, 1)) return ZERO;
    if (xs[0].k === "sym" && xs[0].v === "e") return ONE;
    if (xs[0].k === "fn" && xs[0].f === "exp") return xs[0].xs[0];
  }
  if (f === "exp" && xs[0].k === "fn" && xs[0].f === "ln") return xs[0].xs[0];
  if (xs.every(isNum)) {
    const spec = FNS[f];
    if (spec) {
      const r = spec.f(...xs.map(x => (x as { v: number }).v));
      if (Number.isFinite(r) && (isInt(r) || ARITH.has(f))) {
        return num(isInt(r) ? Math.round(r) : r);
      }
    }
  }
  return fn(f, ...xs);
}

export function simplify(n: Node): Node {
  switch (n.k) {
    case "num": case "sym": return n;
    case "add": return sAdd(n.xs.map(simplify));
    case "mul": return sMul(n.xs.map(simplify));
    case "pow": return sPow(simplify(n.a), simplify(n.b));
    case "fn": return sFn(n.f, n.xs.map(simplify));
  }
}

// --------------------------------------------------------------------------
// expand
// --------------------------------------------------------------------------

const summands = (n: Node): Node[] => (n.k === "add" ? n.xs : [n]);

export function expand(n: Node): Node {
  const go = (n: Node): Node => {
    switch (n.k) {
      case "num": case "sym": return n;
      case "add": return sAdd(n.xs.map(go));
      case "fn": return sFn(n.f, n.xs.map(go));
      case "mul": {
        // One running list of summands, multiplied by each factor in turn.
        let acc: Node[] = [ONE];
        for (const x of n.xs.map(go)) {
          const next: Node[] = [];
          for (const a of acc) for (const b of summands(x)) next.push(sMul([a, b]));
          acc = next;
          // A product of four trinomials is 81 terms and still fine; a product
          // of ten is not, and silently grinding is worse than stopping.
          if (acc.length > 4000) return sMul(n.xs.map(go));
        }
        return sAdd(acc);
      }
      case "pow": {
        const a = go(n.a), b = go(n.b);
        if (a.k === "add" && isNum(b) && isInt(b.v) && b.v > 1 && b.v <= 16) {
          let acc: Node[] = [ONE];
          for (let i = 0; i < b.v; i++) {
            const next: Node[] = [];
            for (const t of acc) for (const s of a.xs) next.push(sMul([t, s]));
            acc = next;
            if (acc.length > 4000) return sPow(a, b);
          }
          return sAdd(acc);
        }
        return sPow(a, b);
      }
    }
  };
  return go(simplify(n));
}

/** Substitute a value or another expression for a variable. */
export function subst(n: Node, v: string, by: Node): Node {
  const go = (n: Node): Node => {
    switch (n.k) {
      case "num": return n;
      case "sym": return n.v === v ? by : n;
      case "add": return add(...n.xs.map(go));
      case "mul": return mul(...n.xs.map(go));
      case "pow": return pow(go(n.a), go(n.b));
      case "fn": return fn(n.f, ...n.xs.map(go));
    }
  };
  return simplify(go(n));
}

// --------------------------------------------------------------------------
// polynomials
// --------------------------------------------------------------------------

const MAX_DEG = 16;

/** Coefficients low-order first, or `null` when the expression is not a
 *  polynomial in `v` with constant coefficients. `null` is the gate every
 *  exact method here is behind. */
export function polyOf(n: Node, v: string): number[] | null {
  const e = expand(n);
  const out: number[] = [];
  const put = (p: number, c: number) => {
    if (!isInt(p) || p < 0 || p > MAX_DEG) return false;
    const i = Math.round(p);
    while (out.length <= i) out.push(0);
    out[i] += c;
    return true;
  };
  for (const term of summands(e)) {
    const [coef, rest] = splitCoef(term);
    let p = 0, c = coef;
    for (const f of rest) {
      const [b, ex] = splitPow(f);
      if (b.k === "sym" && b.v === v) {
        if (!isNum(ex)) return null;
        p += ex.v;
      } else if (has(f, v)) {
        return null;                    // sin(x), x^y, 1/x — not a polynomial
      } else {
        // A constant coefficient may still be written as `pi` or `2/3`.
        try { c *= evaluate(f); } catch { return null; }
      }
    }
    if (!put(p, c)) return null;
  }
  while (out.length > 1 && near(out[out.length - 1], 0)) out.pop();
  return out.length ? out : [0];
}

const polyDeg = (c: number[]) => c.length - 1;

function polyNode(c: number[], v: string): Node {
  const terms: Node[] = [];
  for (let i = c.length - 1; i >= 0; i--) {
    if (near(c[i], 0)) continue;
    const p = i === 0 ? ONE : i === 1 ? sym(v) : pow(sym(v), num(i));
    terms.push(near(c[i], 1) && i > 0 ? p : sMul([ratNode(c[i]), p]));
  }
  return terms.length ? sAdd(terms) : ZERO;
}

/** Synthetic division by (v − r). Returns the quotient; the caller has already
 *  established that the remainder is zero. */
function deflate(c: number[], r: number): number[] {
  const out = new Array(c.length - 1).fill(0);
  let carry = 0;
  for (let i = c.length - 1; i >= 1; i--) {
    carry = c[i] + carry * r;
    out[i - 1] = carry;
  }
  return out;
}

const polyAt = (c: number[], x: number) =>
  c.reduce((s, k, i) => s + k * Math.pow(x, i), 0);

/** Rational roots, by the theorem of the same name. Only fires when the
 *  coefficients are (or scale to) integers — otherwise there is no p/q to
 *  enumerate and the numeric path is the honest one. */
function rationalRoots(c: number[]): number[] {
  const fr = c.map(x => toFrac(x));
  if (fr.some(f => f === null)) return [];
  let lcm = 1;
  for (const f of fr as [number, number][]) lcm = Math.abs(lcm * f[1]) / gcdI(lcm, f[1]);
  if (!Number.isFinite(lcm) || lcm > 1e6) return [];
  const ic = c.map(x => Math.round(x * lcm));
  const a0 = ic.findIndex(x => x !== 0);
  if (a0 < 0) return [];
  const p0 = Math.abs(ic[a0]), pn = Math.abs(ic[ic.length - 1]);
  if (p0 > 1e7 || pn > 1e7) return [];
  const divs = (x: number) => {
    const out: number[] = [];
    for (let i = 1; i * i <= x; i++) {
      if (x % i === 0) { out.push(i); if (i !== x / i) out.push(x / i); }
    }
    return out;
  };
  const roots: number[] = [];
  for (const p of divs(p0)) {
    for (const q of divs(pn)) {
      for (const s of [1, -1]) {
        const r = (s * p) / q;
        if (near(polyAt(c, r), 0) && !roots.some(x => near(x, r))) roots.push(r);
      }
    }
  }
  if (a0 > 0) roots.push(0);           // the theorem misses the root at zero
  return roots.sort((a, b) => a - b);
}

// --------------------------------------------------------------------------
// factor
// --------------------------------------------------------------------------

/** The common factor across a sum: the numeric gcd, and every base that
 *  appears in every term, at its lowest power. Works on things that are not
 *  polynomials at all — `x sin(x) + x²` factors to `x(sin(x) + x)`. */
function commonFactor(terms: Node[]): Node | null {
  if (terms.length < 2) return null;
  const parts = terms.map(t => {
    const [c, rest] = splitCoef(t);
    const m = new Map<string, [Node, number]>();
    for (const f of rest) {
      const [b, e] = splitPow(f);
      if (isNum(e)) m.set(key(b), [b, e.v]);
    }
    return { c, m };
  });
  // The numeric part: a gcd only when every coefficient is a whole number.
  let g = 0;
  let numeric = parts.every(p => isInt(p.c));
  if (numeric) { for (const p of parts) g = gcdI(g, p.c); }
  if (!numeric || g < 1) g = 1;
  if (parts.every(p => p.c < 0) && g > 0) g = -g;   // pull the sign out too

  const shared: Node[] = [];
  for (const [k, [b, e]] of parts[0].m) {
    let lo = e;
    for (const p of parts.slice(1)) {
      const hit = p.m.get(k);
      if (!hit) { lo = 0; break; }
      lo = Math.min(lo, hit[1]);
    }
    if (lo > 0) shared.push(sPow(b, num(lo)));
  }
  if (Math.abs(g) === 1 && !shared.length) return null;
  return sMul([num(g), ...shared]);
}

export type Factored = { node: Node; note?: string } | null;

/** Factor over the rationals. `null` means it does not factor that way — which
 *  is a fact about x² + 1, not a failure to try. */
export function factor(n: Node, v: string): Factored {
  const e = expand(n);
  if (e.k !== "add") {
    // Already a product; still worth folding (2x)(3x) → 6x².
    const s = simplify(e);
    return equal(s, simplify(n)) ? null : { node: s };
  }

  const c = polyOf(e, v);
  if (!c || polyDeg(c) < 1) {
    const g = commonFactor(e.xs);
    if (!g) return null;
    const inner = sAdd(e.xs.map(t => simplify(div(t, g))));
    return { node: sMul([g, inner]) };
  }

  // Content: the numeric gcd and the lowest power of v present.
  let low = 0;
  while (low < c.length && near(c[low], 0)) low++;
  const rest = c.slice(low);
  let content = 1;
  const fr = rest.map(x => toFrac(x));
  if (!fr.some(f => f === null)) {
    let g = 0;
    for (const f of fr as [number, number][]) {
      if (f[1] !== 1) { g = 0; break; }
      g = gcdI(g, f[0]);
    }
    if (g > 1) content = g;
  }
  if (rest[rest.length - 1] < 0) content = -content;
  const mono = rest.map(x => x / content);

  // Peel rational roots one at a time; what is left over stays as it is.
  const linear: Node[] = [];
  let cur = mono;
  for (let guard = 0; guard < MAX_DEG && polyDeg(cur) > 0; guard++) {
    const rs = rationalRoots(cur);
    if (!rs.length) break;
    const r = rs[0];
    const f = toFrac(r);
    if (!f) break;
    const [p, q] = f;
    // (v − p/q) written with integer coefficients: (qv − p), and the q comes
    // out into the leading constant so the product is still equal. Simplified
    // on the way in, or a negative root prints as `x + -1*-2`.
    linear.push(simplify(q === 1 ? sub(sym(v), num(p))
                                 : sub(sMul([num(q), sym(v)]), num(p))));
    cur = deflate(cur, r);
    if (q !== 1) cur = cur.map(x => x / q);
  }

  const pieces: Node[] = [];
  if (content !== 1) pieces.push(num(content));
  if (low > 0) pieces.push(low === 1 ? sym(v) : pow(sym(v), num(low)));
  pieces.push(...linear);
  const tail = polyNode(cur, v);
  if (!(isNum(tail) && near(tail.v, 1))) pieces.push(tail);
  const out = pieces.length === 1 ? pieces[0] : mul(...pieces);

  // "It factored" means the shape actually changed. x² + 1 comes back here
  // identical to what went in, and saying so is the whole point.
  if (equal(simplify(out), e) && !linear.length && low === 0 && content === 1) return null;
  if (!linear.length && low === 0 && content === 1) return null;
  return {
    node: out,
    note: polyDeg(cur) >= 2 ? "the remaining factor has no rational roots" : undefined,
  };
}

// --------------------------------------------------------------------------
// differentiate
// --------------------------------------------------------------------------

/** d/dv of the standard table, as a function of the argument. Radians. */
const DIFF: Record<string, (u: Node) => Node> = {
  sin: u => fn("cos", u),
  cos: u => neg(fn("sin", u)),
  tan: u => add(ONE, pow(fn("tan", u), num(2))),
  sec: u => mul(fn("sec", u), fn("tan", u)),
  csc: u => neg(mul(fn("csc", u), fn("cot", u))),
  cot: u => neg(add(ONE, pow(fn("cot", u), num(2)))),
  asin: u => pow(sub(ONE, pow(u, num(2))), num(-0.5)),
  acos: u => neg(pow(sub(ONE, pow(u, num(2))), num(-0.5))),
  atan: u => pow(add(ONE, pow(u, num(2))), num(-1)),
  sinh: u => fn("cosh", u),
  cosh: u => fn("sinh", u),
  tanh: u => sub(ONE, pow(fn("tanh", u), num(2))),
  asinh: u => pow(add(pow(u, num(2)), ONE), num(-0.5)),
  acosh: u => pow(sub(pow(u, num(2)), ONE), num(-0.5)),
  atanh: u => pow(sub(ONE, pow(u, num(2))), num(-1)),
  ln: u => pow(u, num(-1)),
  log: u => pow(mul(u, fn("ln", num(10))), num(-1)),
  log2: u => pow(mul(u, fn("ln", num(2))), num(-1)),
  exp: u => fn("exp", u),
  abs: u => fn("sign", u),
  sign: () => ZERO,
  floor: () => ZERO,
  ceil: () => ZERO,
  round: () => ZERO,
};

export class NoRule extends Error {}

export function diff(n: Node, v: string): Node {
  const go = (n: Node): Node => {
    if (!has(n, v)) return ZERO;
    switch (n.k) {
      case "num": return ZERO;
      case "sym": return n.v === v ? ONE : ZERO;
      case "add": return add(...n.xs.map(go));
      case "mul": {
        // Product rule, n-ary: one term per factor differentiated.
        const terms = n.xs.map((x, i) =>
          mul(go(x), ...n.xs.filter((_, j) => j !== i)));
        return add(...terms);
      }
      case "pow": {
        const { a, b } = n;
        if (!has(b, v)) {
          // The ordinary power rule, exponent constant.
          return mul(b, pow(a, sub(b, ONE)), go(a));
        }
        if (!has(a, v)) {
          // a^u — exponential in the exponent.
          return mul(pow(a, b), fn("ln", a), go(b));
        }
        // f^g, both moving: the logarithmic-derivative form.
        return mul(pow(a, b),
                   add(mul(go(b), fn("ln", a)), mul(b, go(a), pow(a, num(-1)))));
      }
      case "fn": {
        if (n.f === "mod" || n.f === "fact" || n.f === "ncr" || n.f === "npr"
            || n.f === "gcd" || n.f === "lcm") {
          throw new NoRule(`${n.f} has no derivative I know`);
        }
        if (n.f === "logb" && !has(n.xs[0], v)) {
          return mul(pow(mul(n.xs[1], fn("ln", n.xs[0])), num(-1)), go(n.xs[1]));
        }
        if (n.f === "hypot" || n.f === "min" || n.f === "max" || n.f === "atan2") {
          throw new NoRule(`${n.f} has no derivative I know`);
        }
        const rule = DIFF[n.f];
        if (!rule) throw new NoRule(`I don't know how to differentiate ${n.f}`);
        return mul(rule(n.xs[0]), go(n.xs[0]));   // chain rule, always
      }
    }
  };
  return simplify(go(simplify(n)));
}

// --------------------------------------------------------------------------
// integrate
// --------------------------------------------------------------------------

/** `a·v + b`, or null. Linear substitution is the only substitution this does,
 *  and it is the one that covers sin(2x+1), e^(-3t) and 1/(2x+5). */
function linearIn(n: Node, v: string): [number, number] | null {
  const c = polyOf(n, v);
  if (!c || polyDeg(c) !== 1) return null;
  return [c[1], c[0]];
}

/** ∫f dv over a linear argument u = av + b. Radians. */
const INT: Record<string, (u: Node) => Node> = {
  sin: u => neg(fn("cos", u)),
  cos: u => fn("sin", u),
  exp: u => fn("exp", u),
  sinh: u => fn("cosh", u),
  cosh: u => fn("sinh", u),
  tan: u => neg(fn("ln", fn("abs", fn("cos", u)))),
  cot: u => fn("ln", fn("abs", fn("sin", u))),
  ln: u => sub(mul(u, fn("ln", u)), u),
};

/** The indefinite integral, or `null` when it is outside the table. No
 *  constant of integration is attached — the UI writes the `+ C`, because it
 *  is a fact about the answer rather than a term in it. */
export function integrate(n: Node, v: string): Node | null {
  const go = (n: Node): Node | null => {
    if (!has(n, v)) return mul(n, sym(v));           // ∫c dv = cv
    switch (n.k) {
      case "sym": return mul(num(0.5), pow(sym(v), num(2)));
      case "add": {
        const parts = n.xs.map(go);
        return parts.some(p => p === null) ? null : add(...(parts as Node[]));
      }
      case "mul": {
        const moving = n.xs.filter(x => has(x, v));
        const still = n.xs.filter(x => !has(x, v));
        if (moving.length !== 1) return null;        // no parts, no by-parts
        const inner = go(moving[0]);
        return inner === null ? null : mul(...still, inner);
      }
      case "pow": {
        const { a, b } = n;
        if (has(b, v)) {
          // c^(av+b) — the one exponential-in-the-exponent case worth having.
          if (has(a, v)) return null;
          const lin = linearIn(b, v);
          if (!lin) return null;
          return div(pow(a, b), mul(num(lin[0]), fn("ln", a)));
        }
        if (!isNum(b)) return null;
        const lin = linearIn(a, v);
        if (!lin) return null;
        const [k] = lin;
        if (near(b.v, -1)) return div(fn("ln", fn("abs", a)), num(k));
        return div(pow(a, num(b.v + 1)), mul(num(k), num(b.v + 1)));
      }
      case "fn": {
        const rule = INT[n.f];
        if (!rule) return null;
        const lin = linearIn(n.xs[0], v);
        if (!lin) return null;
        return div(rule(n.xs[0]), num(lin[0]));
      }
      default: return null;
    }
  };
  // Unexpanded first, so ∫(2x+1)^5 dx comes back as (2x+1)^6/12 rather than as
  // the six terms of its expansion — both are right and only one is an answer.
  // Expanding is the fallback, which is what catches x(x+1) and its kind.
  const direct = go(simplify(n));
  const out = direct ?? go(expand(n));
  return out === null ? null : simplify(out);
}

/** ∫ from a to b, numerically — adaptive Simpson, so a definite integral is
 *  available even when the symbolic table has nothing. */
export function definite(n: Node, v: string, a: number, b: number): number {
  const f = (x: number) => {
    try { return evaluate(n, { env: { [v]: x } }); } catch { return NaN; }
  };
  const N = 2000;                     // even, so Simpson's pairing works out
  const h = (b - a) / N;
  let s = f(a) + f(b);
  for (let i = 1; i < N; i++) s += f(a + i * h) * (i % 2 ? 4 : 2);
  return (s * h) / 3;
}

// --------------------------------------------------------------------------
// solve
// --------------------------------------------------------------------------

export type Root = {
  /** How it prints — LaTeX, so an exact root stays a surd. */
  tex: string;
  /** The same root as a decimal, when it has one. Complex roots do not. */
  value: number | null;
};
export type Solution = {
  kind: "exact" | "numeric" | "identity" | "none";
  roots: Root[];
  note?: string;
};

const rootOf = (n: Node): Root => {
  const s = simplify(n);
  let value: number | null = null;
  try {
    const x = evaluate(s);
    value = Number.isFinite(x) ? x : null;
  } catch { value = null; }
  return { tex: latex(rationalize(s)), value };
};

/** Every distinct denominator in a sum — what has to be cleared before a
 *  rational equation can be read as a polynomial one. */
function denominators(n: Node, v: string): Node[] {
  const out = new Map<string, Node>();
  const scan = (t: Node) => {
    if (t.k === "mul") { t.xs.forEach(scan); return; }
    if (t.k === "add") { t.xs.forEach(scan); return; }
    const [b, e] = splitPow(t);
    if (isNum(e) && e.v < 0 && has(b, v)) out.set(key(b), b);
  };
  scan(n);
  return [...out.values()];
}

export function solve(lhs: Node, rhs: Node, v: string): Solution {
  let e = simplify(sub(lhs, rhs));
  if (!has(e, v)) {
    return isNum(simplify(e)) && near((simplify(e) as { v: number }).v, 0)
      ? { kind: "identity", roots: [], note: "true for every value of " + v }
      : { kind: "none", roots: [], note: `no ${v} appears, and the two sides differ` };
  }

  // Clear denominators, then remember to throw away any root that made one
  // zero — multiplying through is only valid away from its own poles.
  const dens = denominators(expand(e), v);
  if (dens.length) e = expand(mul(e, ...dens));

  const reject = (x: number) =>
    dens.some(d => {
      try { return Math.abs(evaluate(d, { env: { [v]: x } })) < 1e-9; }
      catch { return true; }
    });

  // A product is zero when a factor is: the cheapest exact win there is.
  const factored = simplify(expand(e));
  const parts = factored.k === "mul"
    ? factored.xs.filter(x => has(x, v))
    : [factored];
  if (parts.length > 1) {
    const all: Root[] = [];
    let kind: Solution["kind"] = "exact";
    for (const p of parts) {
      const s = solve(p, ZERO, v);
      if (s.kind === "numeric") kind = "numeric";
      all.push(...s.roots);
    }
    return dedupe(all, kind, reject);
  }

  const c = polyOf(e, v);
  if (c) {
    const d = polyDeg(c);
    if (d === 1) {
      return dedupe([rootOf(div(num(-c[0]), num(c[1])))], "exact", reject);
    }
    if (d === 2) {
      const [cc, b, a] = c;
      const disc = b * b - 4 * a * cc;
      if (near(disc, 0)) {
        return dedupe([rootOf(div(num(-b), num(2 * a)))], "exact", reject,
                      "a repeated root");
      }
      if (disc > 0) {
        const s = Math.sqrt(disc);
        const exact = isInt(s) || toFrac(s) !== null;
        const surd = exact ? ratNode(s) : pow(num(disc), num(0.5));
        return dedupe([
          rootOf(div(add(num(-b), surd), num(2 * a))),
          rootOf(div(sub(num(-b), surd), num(2 * a))),
        ], "exact", reject);
      }
      // Complex. Written out rather than dropped: "no real roots" is a
      // different statement from "no roots", and a quadratic always has two.
      const re = -b / (2 * a), im = Math.sqrt(-disc) / (2 * Math.abs(a));
      const fmt = (sign: string) => {
        const imag = near(im, 1) ? "i" : `${latex(ratNode(im))}i`;
        if (near(re, 0)) return sign === "+" ? imag : `-${imag}`;
        return `${latex(ratNode(re))} ${sign} ${imag}`;
      };
      return {
        kind: "exact",
        roots: [{ tex: fmt("+"), value: null }, { tex: fmt("-"), value: null }],
        note: "no real roots — the two below are complex",
      };
    }
    if (d >= 3) {
      // Peel what is rational, find the rest numerically.
      const exact = rationalRoots(c);
      let cur = c;
      for (const r of exact) if (polyDeg(cur) > 0 && near(polyAt(cur, r), 0)) cur = deflate(cur, r);
      const roots: Root[] = exact.map(r => rootOf(ratNode(r)));
      const nums = numericRoots(x => polyAt(cur, x)).filter(
        x => !exact.some(r => near(r, x)));
      roots.push(...nums.map(x => ({ tex: show(x, 10), value: x })));
      return dedupe(roots, nums.length ? "numeric" : "exact", reject,
                    nums.length ? "some roots found numerically" : undefined);
    }
  }

  // Not a polynomial: scan for sign changes and bisect. Honest about what that
  // is — roots in a window, not "the" roots.
  const f = (x: number) => {
    try { return evaluate(e, { env: { [v]: x } }); } catch { return NaN; }
  };
  const found = numericRoots(f);
  if (!found.length) {
    return { kind: "none", roots: [],
             note: `nothing crosses zero for ${v} between −50 and 50` };
  }
  return dedupe(found.map(x => ({ tex: show(x, 10), value: x })), "numeric",
                reject, `found numerically for ${v} between −50 and 50`);
}

function dedupe(roots: Root[], kind: Solution["kind"],
                reject: (x: number) => boolean, note?: string): Solution {
  const out: Root[] = [];
  let dropped = 0;
  for (const r of roots) {
    if (r.value !== null && reject(r.value)) { dropped++; continue; }
    if (out.some(o => o.tex === r.tex
                 || (o.value !== null && r.value !== null && near(o.value, r.value)))) continue;
    out.push(r);
  }
  out.sort((a, b) => (a.value ?? 0) - (b.value ?? 0));
  const notes = [note];
  if (dropped) notes.push(`${dropped} root(s) dropped — they make a denominator zero`);
  return {
    kind: out.length ? kind : "none",
    roots: out,
    note: notes.filter(Boolean).join(" · ") || undefined,
  };
}

/** Sign-change scan, then bisection, then a sanity check that the crossing is
 *  a root and not a pole — 1/x changes sign at zero and has no root there. */
export function numericRoots(f: (x: number) => number,
                             lo = -50, hi = 50, steps = 4000): number[] {
  const out: number[] = [];
  let px = lo, pv = f(lo);
  for (let i = 1; i <= steps; i++) {
    const x = lo + ((hi - lo) * i) / steps;
    const v = f(x);
    if (Number.isFinite(pv) && Number.isFinite(v)) {
      if (near(v, 0) && !out.some(r => Math.abs(r - x) < 1e-6)) out.push(x);
      else if (pv * v < 0) {
        let a = px, b = x, fa = pv;
        for (let j = 0; j < 80; j++) {
          const m = (a + b) / 2, fm = f(m);
          if (fa * fm <= 0) b = m; else { a = m; fa = fm; }
        }
        const r = (a + b) / 2;
        // A pole crosses too. |f| small at the crossing is what tells them apart.
        if (Math.abs(f(r)) < 1e-6 && !out.some(o => Math.abs(o - r) < 1e-6)) out.push(r);
      }
    }
    px = x; pv = v;
  }
  // cos(x) has thirty roots in this window and a list has room for a dozen.
  // Nearest zero first, because a periodic function's useful roots are the
  // ones by the origin — the alphabetical dozen would all be at x ≈ −48.
  if (out.length > 12) {
    out.sort((a, b) => Math.abs(a) - Math.abs(b));
    out.length = 12;
  }
  return out.sort((a, b) => a - b);
}

// --------------------------------------------------------------------------
// what the pane actually calls
// --------------------------------------------------------------------------

/** The last pass before anything is shown: every non-integer coefficient that
 *  *is* a rational becomes p/q, so `∫x² dx` reads `x³/3` rather than
 *  `0.333333333333x³`.
 *
 *  It runs at the printer's door rather than inside `simplify`, and that is the
 *  point: `sMul` would immediately fold `1·3⁻¹` back to 0.333…, so a tree that
 *  went through simplify again would lose the fraction. Nothing here is ever
 *  fed back into the algebra — it is a way of writing, not a way of thinking.
 *
 *  Exponents are left alone. `x^0.5` is √x to the printers and `x^(1/2)` is
 *  a fraction they would draw a bar under. */
export function rationalize(n: Node): Node {
  const go = (n: Node): Node => {
    switch (n.k) {
      case "num": {
        if (isInt(n.v)) return n;
        const f = toFrac(n.v);
        return f ? mul(num(f[0]), pow(num(f[1]), num(-1))) : n;
      }
      case "sym": return n;
      case "add": return add(...n.xs.map(go));
      case "mul": {
        // A rewritten coefficient is a product; splicing keeps the parent's
        // factor list flat, which is the shape the printers read for fractions.
        const xs: Node[] = [];
        for (const x of n.xs) {
          const r = go(x);
          if (r.k === "mul" && x.k !== "mul") xs.push(...r.xs);
          else xs.push(r);
        }
        return mul(...xs);
      }
      case "pow": return pow(go(n.a), n.b);
      case "fn": return fn(n.f, ...n.xs.map(go));
    }
  };
  return go(n);
}

export type Op = "simplify" | "expand" | "factor" | "diff" | "integrate" | "solve";

export type CasOut = {
  /** The heading above the result — "d/dx", "Factored", … */
  label: string;
  /** LaTeX for the result, when there is a single expression to show. */
  tex?: string;
  /** The same thing as text, for the tape and the work pad. */
  text?: string;
  /** Roots, when the operation was a solve. */
  roots?: Root[];
  /** Anything the reader has to know to trust the line above. */
  note?: string;
};

const RADIANS = "radians — the display's deg/rad setting never reaches the algebra";

/** One expression, written both ways. Every result goes out through here, so
 *  the rationalising pass can never be forgotten on one branch. */
const both = (label: string, n: Node, note?: string): CasOut => {
  const r = rationalize(n);
  return { label, tex: latex(r), text: print(r), note };
};

export function run(op: Op, lhs: Node, rhs: Node, v: string): CasOut {
  switch (op) {
    case "simplify": return both("Simplified", simplify(lhs));
    case "expand": return both("Expanded", expand(lhs));
    case "factor": {
      const r = factor(lhs, v);
      if (!r) {
        return both("Factored", expand(lhs),
                    `this doesn't factor over the rationals in ${v}`);
      }
      return both("Factored", r.node, r.note);
    }
    case "diff": return both(`d/d${v}`, diff(lhs, v), RADIANS);
    case "integrate": {
      const r = integrate(lhs, v);
      if (!r) {
        return { label: `∫ d${v}`, note:
          "not one I can do symbolically — the table here is powers, the "
          + "standard functions, and linear substitution inside them. A "
          + "definite value is still available by graphing it." };
      }
      const out = both(`∫ d${v}`, r, RADIANS);
      return { ...out, tex: `${out.tex} + C`, text: `${out.text} + C` };
    }
    case "solve": {
      const s = solve(lhs, rhs, v);
      return { label: `Solve for ${v}`, roots: s.roots, note: s.note
               ?? (s.kind === "numeric" ? "found numerically" : undefined) };
    }
  }
}
