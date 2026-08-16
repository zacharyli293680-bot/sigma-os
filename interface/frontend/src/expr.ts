/*
 * expr.ts — one expression language, three consumers.
 *
 * **Still no `eval`, and still no library.** The rule `calc.tsx` was written
 * under has not moved: a string typed into this app is data to a parser and
 * never something the browser executes — `chat.tsx` states it for HTML,
 * `math.tsx` for TeX, `figure.tsx` for SVG. What changed is what the parse
 * produces.
 *
 * The calculator used to compile straight to a closure over `x`. That is
 * exactly enough for a number and a plot, and exactly nothing for algebra: you
 * cannot differentiate a function you can only *call*. So the parse now builds
 * a tree, and the three consumers each take what they need from it —
 * `evaluate()` walks it for the keypad, the grapher samples it, and `cas.ts`
 * rewrites it.
 *
 * **Two operators, not four.** Subtraction is `+ (-1)·x` and division is
 * `· x^-1` the moment it is parsed, so `add` and `mul` are n-ary and every
 * simplification rule downstream is written once instead of four times. The
 * printers put the minus signs and the fraction bars back, so nothing about
 * this reaches the screen.
 */

// --------------------------------------------------------------------------
// the tree
// --------------------------------------------------------------------------

export type Node =
  | { k: "num"; v: number }
  | { k: "sym"; v: string }
  | { k: "add"; xs: Node[] }
  | { k: "mul"; xs: Node[] }
  | { k: "pow"; a: Node; b: Node }
  | { k: "fn"; f: string; xs: Node[] };

export const num = (v: number): Node => ({ k: "num", v });
export const sym = (v: string): Node => ({ k: "sym", v });
export const add = (...xs: Node[]): Node => ({ k: "add", xs });
export const mul = (...xs: Node[]): Node => ({ k: "mul", xs });
export const pow = (a: Node, b: Node): Node => ({ k: "pow", a, b });
export const fn = (f: string, ...xs: Node[]): Node => ({ k: "fn", f, xs });
export const neg = (a: Node): Node => mul(num(-1), a);
export const sub = (a: Node, b: Node): Node => add(a, neg(b));
export const div = (a: Node, b: Node): Node => mul(a, pow(b, num(-1)));

export const ZERO = num(0);
export const ONE = num(1);

/** Structural identity, as a string. Used for grouping like terms, for sorting
 *  into a canonical order, and for `equal()` — one definition, so two nodes
 *  that group together always also sort and compare together. */
export function key(n: Node): string {
  switch (n.k) {
    case "num": return `n${n.v}`;
    case "sym": return `s${n.v}`;
    case "add": return `+(${n.xs.map(key).join(",")})`;
    case "mul": return `*(${n.xs.map(key).join(",")})`;
    case "pow": return `^(${key(n.a)},${key(n.b)})`;
    case "fn": return `${n.f}(${n.xs.map(key).join(",")})`;
  }
}
export const equal = (a: Node, b: Node) => key(a) === key(b);
export const isNum = (n: Node): n is { k: "num"; v: number } => n.k === "num";
export const numOr = (n: Node, d: number) => (n.k === "num" ? n.v : d);

/** Every symbol in a tree, in first-seen order, minus the named constants —
 *  `pi` is spelled like a variable and is not one. */
export function varsOf(n: Node, out: string[] = []): string[] {
  if (n.k === "sym") {
    if (!(n.v in CONSTS) && !out.includes(n.v)) out.push(n.v);
  } else if (n.k === "add" || n.k === "mul") n.xs.forEach(x => varsOf(x, out));
  else if (n.k === "pow") { varsOf(n.a, out); varsOf(n.b, out); }
  else if (n.k === "fn") n.xs.forEach(x => varsOf(x, out));
  return out;
}

/** Whether `v` occurs anywhere — the question every differentiation and
 *  integration rule asks first. */
export function has(n: Node, v: string): boolean {
  switch (n.k) {
    case "num": return false;
    case "sym": return n.v === v;
    case "add": case "mul": return n.xs.some(x => has(x, v));
    case "pow": return has(n.a, v) || has(n.b, v);
    case "fn": return n.xs.some(x => has(x, v));
  }
}

// --------------------------------------------------------------------------
// what a name is allowed to mean
// --------------------------------------------------------------------------

/** A name that is not here is an error naming itself, never a silent zero —
 *  `sni(30)` should say what is wrong, not quietly plot the x-axis. */
export const CONSTS: Record<string, number> = {
  pi: Math.PI, e: Math.E, tau: Math.PI * 2,
  g: 9.81,          // the one piece of course furniture: m/s²
};

const gammaG = 7;
const gammaC = [
  0.99999999999980993, 676.5203681218851, -1259.1392167224028,
  771.32342877765313, -176.61502916214059, 12.507343278686905,
  -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
];
/** Γ(z) by Lanczos, so `0.5!` is √π/2 rather than an error. Factorial on a
 *  calculator is asked of integers 99% of the time and of a half-integer the
 *  other 1%, and refusing the second is a worse answer than approximating it. */
function gamma(z: number): number {
  if (z < 0.5) return Math.PI / (Math.sin(Math.PI * z) * gamma(1 - z));
  z -= 1;
  let x = gammaC[0];
  for (let i = 1; i < gammaG + 2; i++) x += gammaC[i] / (z + i);
  const t = z + gammaG + 0.5;
  return Math.sqrt(2 * Math.PI) * Math.pow(t, z + 0.5) * Math.exp(-t) * x;
}
export function factorial(n: number): number {
  if (!Number.isFinite(n)) return NaN;
  if (Number.isInteger(n) && n < 0) return NaN;   // −1! is a pole, not a number
  if (Number.isInteger(n) && n <= 170) {
    let r = 1;
    for (let i = 2; i <= n; i++) r *= i;
    return r;
  }
  return gamma(n + 1);
}

const nCr = (n: number, r: number) => factorial(n) / (factorial(r) * factorial(n - r));
const nPr = (n: number, r: number) => factorial(n) / factorial(n - r);
function gcd2(a: number, b: number): number {
  a = Math.abs(Math.round(a)); b = Math.abs(Math.round(b));
  while (b) { [a, b] = [b, a % b]; }
  return a;
}

type Impl = (...a: number[]) => number;
/** The scientific set. Arity is checked at parse time from this table, so
 *  `sin(1, 2)` is a sentence about arity rather than a silently dropped
 *  argument. `-1` means variadic. */
export const FNS: Record<string, { f: Impl; n: number; tex?: string }> = {
  sin: { f: Math.sin, n: 1 }, cos: { f: Math.cos, n: 1 }, tan: { f: Math.tan, n: 1 },
  sec: { f: x => 1 / Math.cos(x), n: 1 }, csc: { f: x => 1 / Math.sin(x), n: 1 },
  cot: { f: x => 1 / Math.tan(x), n: 1 },
  asin: { f: Math.asin, n: 1 }, acos: { f: Math.acos, n: 1 }, atan: { f: Math.atan, n: 1 },
  sinh: { f: Math.sinh, n: 1 }, cosh: { f: Math.cosh, n: 1 }, tanh: { f: Math.tanh, n: 1 },
  asinh: { f: Math.asinh, n: 1 }, acosh: { f: Math.acosh, n: 1 },
  atanh: { f: Math.atanh, n: 1 },
  sqrt: { f: Math.sqrt, n: 1 }, cbrt: { f: Math.cbrt, n: 1 },
  abs: { f: Math.abs, n: 1, tex: "\\left|#1\\right|" },
  ln: { f: Math.log, n: 1 }, log: { f: Math.log10, n: 1 }, log2: { f: Math.log2, n: 1 },
  exp: { f: Math.exp, n: 1 },
  floor: { f: Math.floor, n: 1 }, ceil: { f: Math.ceil, n: 1 },
  round: { f: Math.round, n: 1 }, sign: { f: Math.sign, n: 1 },
  fact: { f: factorial, n: 1 },
  atan2: { f: Math.atan2, n: 2 }, mod: { f: (a, b) => a % b, n: 2 },
  logb: { f: (b, x) => Math.log(x) / Math.log(b), n: 2 },
  root: { f: (n, x) => (n % 2 === 1 && x < 0 ? -Math.pow(-x, 1 / n) : Math.pow(x, 1 / n)), n: 2 },
  ncr: { f: nCr, n: 2 }, npr: { f: nPr, n: 2 },
  gcd: { f: (...a) => a.reduce(gcd2), n: -1 },
  lcm: { f: (...a) => a.reduce((x, y) => Math.abs(x * y) / (gcd2(x, y) || 1)), n: -1 },
  min: { f: Math.min, n: -1 }, max: { f: Math.max, n: -1 },
  hypot: { f: Math.hypot, n: -1 },
};

/** Functions whose argument is an angle, and those whose *result* is one — the
 *  two lists degree mode has to bend, in opposite directions. */
export const TAKES_ANGLE = new Set(["sin", "cos", "tan", "sec", "csc", "cot"]);
export const GIVES_ANGLE = new Set(["asin", "acos", "atan", "atan2"]);

// --------------------------------------------------------------------------
// reading
// --------------------------------------------------------------------------

type Tok =
  | { k: "num"; v: number }
  | { k: "name"; v: string }
  | { k: "op"; v: string };

/** The unicode a person actually types or pastes, mapped to the ASCII the
 *  parser knows. The keypad emits ASCII; a formula pasted out of a note does
 *  not. */
const UNI: Record<string, string> = {
  "×": "*", "·": "*", "∗": "*", "÷": "/", "−": "-", "–": "-", "—": "-",
  "^": "^", "π": "pi", "τ": "tau", "√": "sqrt", "∛": "cbrt", "≤": "<=", "≥": ">=",
};

export function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === " " || c === "\t" || c === "\n" || c === "_") {
      // `_` is the digit separator a person types in 12_000.
      i++;
      continue;
    }
    if (c === ",") { out.push({ k: "op", v: "," }); i++; continue; }
    if (/[0-9.]/.test(c)) {
      const m = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(src.slice(i));
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
    if (c in UNI) {
      const t = UNI[c];
      if (/^[a-z]+$/.test(t)) out.push({ k: "name", v: t });
      else out.push({ k: "op", v: t });
      i++;
      continue;
    }
    if ("+-*/^%()!=".includes(c)) {
      // `**` is how a keyboard-minded person writes a power.
      if (c === "*" && src[i + 1] === "*") { out.push({ k: "op", v: "^" }); i += 2; continue; }
      out.push({ k: "op", v: c });
      i++;
      continue;
    }
    throw new Error(`"${c}" is not something I can read`);
  }
  return out;
}

/** Parse one expression. Throws with a human sentence; every caller shows it
 *  verbatim, because an error that names the character it choked on is worth
 *  more than a red border. */
export function parse(src: string): Node {
  const toks = tokenize(src);
  if (!toks.length) throw new Error("there is nothing to read");
  let p = 0;
  const peek = () => toks[p];
  const eat = (v: string) => {
    const t = toks[p];
    if (t && t.k === "op" && t.v === v) { p++; return true; }
    return false;
  };

  function expr(): Node {
    let left = term();
    for (;;) {
      if (eat("+")) left = add(left, term());
      else if (eat("-")) left = sub(left, term());
      else return left;
    }
  }
  function term(): Node {
    let left = unary();
    for (;;) {
      if (eat("*")) left = mul(left, unary());
      else if (eat("/")) left = div(left, unary());
      else if (eat("%")) left = fn("mod", left, unary());
      else if (implicitFollows()) {
        // `2x`, `3(x+1)`, `2sin(x)` — written constantly on paper, and a
        // calculator that refuses them is a calculator you stop using.
        left = mul(left, unary());
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
    const afterValue = prev?.k === "num"
      || (prev?.k === "op" && (prev.v === ")" || prev.v === "!"));
    if (t.k === "num") return prev?.k === "name" || afterValue;
    if (t.k === "name") return afterValue;
    if (t.k === "op" && t.v === "(") return afterValue;
    return false;
  }
  function unary(): Node {
    if (eat("-")) return neg(unary());
    if (eat("+")) return unary();
    return power();
  }
  function power(): Node {
    const base = postfix();
    // Right-associative, and the exponent may itself be signed: `2^-1`.
    if (eat("^")) return pow(base, unary());
    return base;
  }
  /** `!` is factorial, and `%` is two operators wearing one glyph.
   *
   *  Every calculator ever made reads a trailing `%` as "divide by a hundred",
   *  and every programming language reads `a % b` as a remainder. Both are
   *  worth having and the two never collide, because what tells them apart is
   *  whether anything follows: `50%` is a percentage and `7 % 3` is a
   *  remainder. So this consumes the `%` only when nothing that could start a
   *  value comes after it, and leaves it for `term()` otherwise.
   *
   *  A leading sign is deliberately *not* counted as starting a value, which
   *  decides the one genuinely ambiguous case in favour of percent: `50% + 10`
   *  is what a hand on the keypad types and `100 % -5` is not. `mod(a, b)`
   *  spells the remainder out unambiguously for the case this gives up. */
  function postfix(): Node {
    let a = atom();
    for (;;) {
      if (eat("!")) { a = fn("fact", a); continue; }
      const t = peek();
      if (t && t.k === "op" && t.v === "%") {
        const nxt = toks[p + 1];
        const startsValue = nxt && (nxt.k === "num" || nxt.k === "name"
          || (nxt.k === "op" && nxt.v === "("));
        if (startsValue) return a;         // modulo — `term()` takes it
        p++;
        a = div(a, num(100));
        continue;
      }
      return a;
    }
  }
  function atom(): Node {
    const t = peek();
    if (!t) throw new Error("the expression stops early");
    if (t.k === "num") { p++; return num(t.v); }
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
        const spec = FNS[lower];
        if (!spec) throw new Error(`I don't know a function called "${name}"`);
        if (spec.n >= 0 && args.length !== spec.n) {
          throw new Error(`${lower} takes ${spec.n} argument`
                          + `${spec.n === 1 ? "" : "s"}, not ${args.length}`);
        }
        if (spec.n < 0 && args.length === 0) {
          throw new Error(`${lower} needs at least one argument`);
        }
        return fn(lower, ...args);
      }
      // A bare name is a variable or a constant. Which of the two it is gets
      // decided by whoever evaluates it — the CAS wants `pi` to stay `pi`, and
      // `evaluate` wants it to be 3.14159.
      return sym(lower);
    }
    throw new Error(`"${(t as { v: string | number }).v}" can't start a value`);
  }

  const root = expr();
  if (p < toks.length) {
    const t = toks[p] as { v: string | number };
    throw new Error(`there is a stray "${t.v}" at the end`);
  }
  return root;
}

/** `lhs = rhs`, for the solver. One `=` exactly: `a = b = c` is a chain nobody
 *  types on purpose, and reading it as two equations would be a guess. An
 *  expression with no `=` is read as `expr = 0`, which is what "solve x^2 - 4"
 *  means to everyone who types it. */
export function parseEquation(src: string): { lhs: Node; rhs: Node } {
  const halves = src.split("=");
  if (halves.length === 1) return { lhs: parse(src), rhs: ZERO };
  if (halves.length !== 2) throw new Error("one = per equation, please");
  if (!halves[0].trim() || !halves[1].trim())
    throw new Error("both sides of the = need something on them");
  return { lhs: parse(halves[0]), rhs: parse(halves[1]) };
}

// --------------------------------------------------------------------------
// arithmetic
// --------------------------------------------------------------------------

export type EvalOpts = {
  /** Degree mode. Bends the angle-taking functions on the way in and the
   *  angle-giving ones on the way out — never the CAS, which is radians only
   *  because d/dx sin(x) = cos(x) is false in degrees. */
  deg?: boolean;
  /** Variable bindings. Falls back to the constants table, then throws. */
  env?: Record<string, number>;
};

export function evaluate(n: Node, o: EvalOpts = {}): number {
  const deg = !!o.deg;
  const env = o.env ?? {};
  const walk = (n: Node): number => {
    switch (n.k) {
      case "num": return n.v;
      case "sym": {
        if (n.v in env) return env[n.v];
        if (n.v in CONSTS) return CONSTS[n.v];
        throw new Error(`I don't know what "${n.v}" is`);
      }
      case "add": return n.xs.reduce((s, x) => s + walk(x), 0);
      case "mul": return n.xs.reduce((s, x) => s * walk(x), 1);
      case "pow": {
        const b = walk(n.a), e = walk(n.b);
        // A negative base to a fractional power is NaN in IEEE, which is right
        // for 0.5 and wrong for 1/3 — a cube root of −8 is −2. The odd-integer
        // reciprocal is the case a calculator user actually hits.
        if (b < 0 && !Number.isInteger(e)) {
          const inv = 1 / e;
          if (Math.abs(inv - Math.round(inv)) < 1e-12 && Math.round(inv) % 2 !== 0)
            return -Math.pow(-b, e);
        }
        return Math.pow(b, e);
      }
      case "fn": {
        const spec = FNS[n.f];
        if (!spec) throw new Error(`I don't know a function called "${n.f}"`);
        let vs = n.xs.map(walk);
        if (deg && TAKES_ANGLE.has(n.f)) vs = [(vs[0] * Math.PI) / 180, ...vs.slice(1)];
        const out = spec.f(...vs);
        return deg && GIVES_ANGLE.has(n.f) ? (out * 180) / Math.PI : out;
      }
    }
  };
  return walk(n);
}

/** A one-variable sampler, which is all the grapher ever wants. Built once per
 *  expression rather than per sample: 600 points × a fresh closure per point
 *  was the one hot loop in here. */
export function sampler(n: Node, v: string, o: EvalOpts = {}): (x: number) => number {
  const env = { ...(o.env ?? {}) };
  return (x: number) => {
    env[v] = x;
    try { return evaluate(n, { deg: o.deg, env }); } catch { return NaN; }
  };
}

/** A number as a person would write it: enough precision to be useful, no
 *  floating-point lint (`0.30000000000000004`), and exponent form only when
 *  the plain form would be unreadable. */
export function show(v: number, sig = 12): string {
  if (!Number.isFinite(v)) return Number.isNaN(v) ? "undefined" : (v > 0 ? "∞" : "−∞");
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e12 || a < 1e-6) return v.toExponential(6).replace(/e([+-])/, "e$1");
  return String(Number(v.toPrecision(sig)));
}

// --------------------------------------------------------------------------
// writing it back out
// --------------------------------------------------------------------------

/** Binding power, for deciding brackets. The printers ask this rather than
 *  bracketing everything: `x + 1` inside a product needs them and `x` never
 *  does, and a CAS that writes `((x))` reads like a machine. */
function prec(n: Node): number {
  switch (n.k) {
    case "add": return 1;
    case "mul": return 2;
    case "pow": return 3;
    default: return 4;
  }
}

/** Split a product into its numeric coefficient and the rest — the shape both
 *  printers need for signs, and the CAS needs for collecting like terms. */
export function splitCoef(n: Node): [number, Node[]] {
  if (n.k !== "mul") return isNum(n) ? [n.v, []] : [1, [n]];
  let c = 1;
  const rest: Node[] = [];
  for (const x of n.xs) {
    if (isNum(x)) c *= x.v;
    else rest.push(x);
  }
  return [c, rest];
}

/** A factor's base and exponent. `x` is `x^1`; the CAS collects on the base. */
export function splitPow(n: Node): [Node, Node] {
  return n.k === "pow" ? [n.a, n.b] : [n, ONE];
}

/** Factors with a negative exponent — the denominator, once the tree stopped
 *  having one. */
function overUnder(xs: Node[]): { over: Node[]; under: Node[] } {
  const over: Node[] = [], under: Node[] = [];
  for (const x of xs) {
    const [b, e] = splitPow(x);
    if (isNum(e) && e.v < 0) under.push(e.v === -1 ? b : pow(b, num(-e.v)));
    else over.push(x);
  }
  return { over, under };
}

/** The numeric part of a product, pulled to the front where it is read, with
 *  its sign taken off. `-1·ln(x)` is written `-ln(x)` by every human being who
 *  has ever written it, and `1·x` is written `x`. */
function headCoef(xs: Node[]): { sign: string; parts: Node[] } {
  let c = 1;
  const rest: Node[] = [];
  for (const x of xs) {
    if (isNum(x)) c *= x.v;
    else rest.push(x);
  }
  const sign = c < 0 ? "-" : "";
  c = Math.abs(c);
  const parts = c === 1 && rest.length ? rest : [num(c), ...rest];
  return { sign, parts };
}

/** Plain text, the form that goes into the tape, the work pad and back into
 *  the input field — so it must re-parse to the same tree. */
export function print(n: Node): string {
  const wrap = (x: Node, min: number) => (prec(x) < min ? `(${print(x)})` : print(x));
  switch (n.k) {
    case "num": return show(n.v);
    case "sym": return n.v;
    case "add": {
      let s = "";
      n.xs.forEach((x, i) => {
        const [c] = splitCoef(x);
        const negTerm = c < 0;
        const body = negTerm ? print(negate(x)) : print(x);
        if (i === 0) s += negTerm ? `-${body}` : body;
        else s += negTerm ? ` - ${body}` : ` + ${body}`;
      });
      return s;
    }
    case "mul": {
      const { over, under } = overUnder(n.xs);
      const { sign, parts } = headCoef(over);
      const top = parts.length ? parts.map(x => wrap(x, 2)).join("*") : "1";
      if (!under.length) return sign + top;
      const bot = under.map(x => wrap(x, 3)).join("*");
      return `${sign}${top}/${under.length > 1 ? `(${bot})` : bot}`;
    }
    case "pow": {
      // `sqrt` and `cbrt` were rewritten to powers on the way in (cas.ts says
      // why). Writing them back out that way keeps the text form re-parseable
      // *and* readable — `x^0.5` is neither.
      if (isNum(n.b)) {
        if (n.b.v === 0.5) return `sqrt(${print(n.a)})`;
        if (Math.abs(n.b.v - 1 / 3) < 1e-12) return `cbrt(${print(n.a)})`;
        // A negative exponent is a denominator wherever it appears, not only
        // when it happens to have siblings in a product.
        if (n.b.v === -1) return `1/${wrap(n.a, 3)}`;
        if (n.b.v < 0) return `1/${wrap(pow(n.a, num(-n.b.v)), 3)}`;
      }
      return `${wrap(n.a, 4)}^${wrap(n.b, 4)}`;
    }
    case "fn": return `${n.f}(${n.xs.map(print).join(", ")})`;
  }
}

/** Flip the sign without simplifying — printing only. */
function negate(n: Node): Node {
  if (isNum(n)) return num(-n.v);
  if (n.k === "mul") {
    const xs = [...n.xs];
    const i = xs.findIndex(isNum);
    if (i >= 0) {
      const c = -(xs[i] as { v: number }).v;
      if (c === 1) { xs.splice(i, 1); return xs.length === 1 ? xs[0] : mul(...xs); }
      xs[i] = num(c);
      return mul(...xs);
    }
  }
  return mul(num(-1), n);
}

const TEX_FN: Record<string, string> = {
  sin: "\\sin", cos: "\\cos", tan: "\\tan", sec: "\\sec", csc: "\\csc", cot: "\\cot",
  asin: "\\arcsin", acos: "\\arccos", atan: "\\arctan",
  sinh: "\\sinh", cosh: "\\cosh", tanh: "\\tanh",
  ln: "\\ln", log: "\\log", exp: "\\exp",
  min: "\\min", max: "\\max", gcd: "\\gcd",
};

/** LaTeX, for the answer. The vault's own convention (`$…$`, `$$…$$`, rendered
 *  by KaTeX in `math.tsx`) — the same maths the modules are written in, so a
 *  CAS result and a lesson equation look like the same language. */
export function latex(n: Node): string {
  const wrap = (x: Node, min: number) => (prec(x) < min ? `\\left(${latex(x)}\\right)` : latex(x));
  switch (n.k) {
    case "num": {
      const s = show(n.v);
      const m = /^(-?[\d.]+)e([+-]\d+)$/.exec(s);
      return m ? `${m[1]} \\times 10^{${Number(m[2])}}` : s;
    }
    case "sym": {
      if (n.v === "pi") return "\\pi";
      if (n.v === "tau") return "\\tau";
      if (n.v === "theta") return "\\theta";
      // `x1` reads as a subscript to everyone who writes maths.
      const m = /^([a-z]+)(\d+)$/.exec(n.v);
      return m ? `${m[1]}_{${m[2]}}` : n.v;
    }
    case "add": {
      let s = "";
      n.xs.forEach((x, i) => {
        const [c] = splitCoef(x);
        const negTerm = c < 0;
        const body = negTerm ? latex(negate(x)) : latex(x);
        if (i === 0) s += negTerm ? `-${body}` : body;
        else s += negTerm ? ` - ${body}` : ` + ${body}`;
      });
      return s;
    }
    case "mul": {
      const { over, under } = overUnder(n.xs);
      const { sign, parts } = headCoef(over);
      const join = (xs: Node[]) => {
        let s = "";
        xs.forEach((x, i) => {
          const t = wrap(x, 2);
          // A digit meeting a digit needs the dot; everything else juxtaposes.
          if (i && /[\d.]$/.test(s) && /^[\d.]/.test(t)) s += " \\cdot ";
          else if (i) s += " ";
          s += t;
        });
        return s || "1";
      };
      if (!under.length) return sign + join(parts);
      return `${sign}\\frac{${join(parts)}}{${join(under)}}`;
    }
    case "pow": {
      const [b, e] = [n.a, n.b];
      if (isNum(e) && e.v === 0.5) return `\\sqrt{${latex(b)}}`;
      if (isNum(e) && e.v === -0.5) return `\\frac{1}{\\sqrt{${latex(b)}}}`;
      if (isNum(e) && Math.abs(e.v - 1 / 3) < 1e-12) return `\\sqrt[3]{${latex(b)}}`;
      if (isNum(e) && e.v === -1) return `\\frac{1}{${latex(b)}}`;
      if (isNum(e) && e.v < 0) return `\\frac{1}{${latex(pow(b, num(-e.v)))}}`;
      return `${wrap(b, 4)}^{${latex(e)}}`;
    }
    case "fn": {
      if (n.f === "sqrt") return `\\sqrt{${latex(n.xs[0])}}`;
      if (n.f === "cbrt") return `\\sqrt[3]{${latex(n.xs[0])}}`;
      if (n.f === "root") return `\\sqrt[${latex(n.xs[0])}]{${latex(n.xs[1])}}`;
      if (n.f === "abs") return `\\left|${latex(n.xs[0])}\\right|`;
      if (n.f === "fact") return `${wrap(n.xs[0], 4)}!`;
      if (n.f === "logb") return `\\log_{${latex(n.xs[0])}}\\left(${latex(n.xs[1])}\\right)`;
      if (n.f === "log2") return `\\log_{2}\\left(${latex(n.xs[0])}\\right)`;
      const name = TEX_FN[n.f] ?? `\\operatorname{${n.f}}`;
      return `${name}\\left(${n.xs.map(latex).join(",\\, ")}\\right)`;
    }
  }
}
