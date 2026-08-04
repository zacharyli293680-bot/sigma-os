/**
 * The brain — live-firing nebula (dashboard-plan §5, D2).
 *
 * Every note is a point of light. Position comes from a small 3D force layout
 * run ONCE when the graph loads, then frozen — ambient motion is camera drift
 * and mouse parallax, never re-simulation. Colour is the note's bucket, using
 * the exact colours from .obsidian/graph.json so this and Obsidian's graph are
 * one picture of one vault. Brightness is inbound links: hubs read as stars.
 *
 * The firing is the part that has to be true: chat tool events (a Read's
 * basename) light their node and pulse its edges, so you can watch where an
 * answer came from. Layout is aesthetic; the firing is factual.
 *
 * That split governs every effect here. Ambient beauty — the cortical breathing
 * wave, the bloom, the depth fog, the dust — is deliberately *continuous and
 * slow*, so it can never be mistaken for a spike. Firing stays the only thing
 * that flashes white-cyan and the only thing that sends light down an edge. If
 * you see something travel, it happened.
 *
 * §11's render-budget risk is handled directly: if the average frame runs
 * long, the loop degrades to a static render that repaints only on
 * interaction. prefers-reduced-motion gets the static sky from the start.
 * Glows are pre-rendered sprites rather than ctx.shadowBlur — same look at a
 * fraction of the cost, which is what buys the bloom inside the budget.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { obsidianHref } from "./api";
import type { Graph } from "./api";

const COLORS: Record<string, string> = {
  root: "#FF6B6B", inbox: "#FFD166", daily: "#E8EAED",
  academics: "#4ADE80", areas: "#FB923C", projects: "#60A5FA",
  system: "#C084FC", archive: "#6B7280", meta: "#2DD4BF",
};
const BUCKETS: [string, string][] = [
  ["root", "root hubs"], ["inbox", "inbox"], ["daily", "daily"],
  ["academics", "academics"], ["areas", "areas"], ["projects", "projects"],
  ["system", "system"], ["archive", "archive"], ["meta", "meta & ref"],
];
const FIRE_MS = 1900;
const FIRE_COLOR = "#9BEFFC";
/** Phase 5. A *ring*, never a fill: colour in this view belongs to the eight
 *  bucket groups, and repainting a node bronze would make the brain disagree
 *  with Obsidian's own graph (dashboard-plan §2). */
const NOSYNC_COLOR = "#C77D2E";
/** Fraction of FIRE_MS an axon pulse takes to cross its edge. */
const AXON = 0.42;

/** Ambient frame rate. The camera makes one revolution per 140 seconds, so at
 *  60fps a star at the cloud's edge moves ~1.4px per frame — fifteen is
 *  visually identical and costs a quarter as much. That is what makes a
 *  permanently-mounted brain affordable to leave on all day. */
const AMBIENT_FPS = 15;
/** How long a fire promotes the sky to full rate: the pulse's crossing plus the
 *  tail of the node's own glow. "If it moves, something happened" applied to
 *  the frame budget — the only thing that buys 60fps is something real. */
const HOT_MS = FIRE_MS + 300;
/** Quality ladder. 0 full · 1 dust off and a shorter glow reach · 2 the far
 *  filaments dropped · 3 static, repainting only on demand. This replaces a
 *  one-way boolean: the old rule condemned the sky permanently once any 45
 *  frames exceeded 26ms, so a single GC pause could do it and nothing could
 *  ever recover. Measured on a rolling median instead, and re-armed whenever
 *  the canvas changes size class.
 *
 *  Rung 2 arrived with the full-shell sky. Dust and glow reach are both spent
 *  by rung 1 and neither touches edges — which are the frame's dominant cost —
 *  so the ladder had nothing between "pretty" and "frozen", and one slow moment
 *  dropped it the whole way. */
const SLOW_MS = 26;
const WINDOW = 45;
const STATIC = 3;
/** Must match the grid transition in App.css (`--dive-ms`). The sky renders at
 *  full rate for this long whenever the stage changes size. */
const DIVE_MS = 340;
/** Backing-store granularity, in device pixels. The canvas box changes on every
 *  frame of the expand, and reallocating a ~10MB buffer twenty times across one
 *  animation is ~200MB of churn — a driver stall right where smoothness matters
 *  most. Rounding up to a step means ~3 reallocations instead of ~20; the buffer
 *  is then slightly larger than the box and the transform below stretches the
 *  scene to fill it exactly, so the picture is very slightly supersampled rather
 *  than distorted. */
const STEP_PX = 64;

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** The layout's inputs, hashed — node ids (which seed every start position) and
 *  the edge list (which springs pull on). Nothing else can move a star.
 *
 *  This is what decides whether to relay. The guard used to be object identity,
 *  and `/api/graph` is polled every five minutes with `.json()` handing back a
 *  fresh object every time — so the full 220-iteration O(n²) layout re-ran
 *  twice an hour to arrive at *bit-identical* positions, `seed` and `relax`
 *  both being deterministic. Invisible at this vault's size and not at four
 *  times it. A poll that changed only `mtime` now swaps the data in and leaves
 *  the sky exactly where it was. */
function signature(g: Graph): number {
  let h = 2166136261;
  for (const n of g.nodes) {
    for (let i = 0; i < n.id.length; i++) { h ^= n.id.charCodeAt(i); h = Math.imul(h, 16777619); }
    h ^= 10; h = Math.imul(h, 16777619);          // separator: ids are variable-length
  }
  for (const [a, b] of g.links) {
    h ^= a; h = Math.imul(h, 16777619);
    h ^= b; h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/* --------------------------------------------------------------- glow sprites */

/** Pre-rendered radial glow, one per colour. ctx.shadowBlur re-blurs on every
 *  fill and was the single most expensive call in the old frame; a cached
 *  sprite blitted with drawImage costs a texture copy and looks better. */
const SPRITES = new Map<string, HTMLCanvasElement>();
const SPRITE_PX = 64;

function glow(color: string): HTMLCanvasElement {
  const hit = SPRITES.get(color);
  if (hit) return hit;
  const c = document.createElement("canvas");
  c.width = c.height = SPRITE_PX;
  const x = c.getContext("2d")!;
  const h = SPRITE_PX / 2;
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  const grd = x.createRadialGradient(h, h, 0, h, h, h);
  grd.addColorStop(0.00, "rgba(255,255,255,0.95)");
  grd.addColorStop(0.13, `rgba(${r},${g},${b},0.90)`);
  grd.addColorStop(0.34, `rgba(${r},${g},${b},0.30)`);
  grd.addColorStop(0.62, `rgba(${r},${g},${b},0.07)`);
  grd.addColorStop(1.00, `rgba(${r},${g},${b},0)`);
  x.fillStyle = grd;
  x.fillRect(0, 0, SPRITE_PX, SPRITE_PX);
  SPRITES.set(color, c);
  return c;
}

/* ----------------------------------------------------------- edge strokes */

/** Quantised filament colours, hoisted out of the frame.
 *
 *  The edge pass used to build an `rgba(…)` template literal with `.toFixed(3)`
 *  for *every* edge on *every* frame and assign it to `ctx.strokeStyle` — a
 *  string allocation and a CSS colour parse per edge, plus its own
 *  `beginPath`/`stroke` pair. Edges are the dominant per-frame cost and that
 *  was most of it, spent on alpha differences no eye can resolve.
 *
 *  Depth is bucketed into eight steps instead. Each bucket collects one Path2D
 *  during the loop and is stroked once, so the whole pass costs eight strokes
 *  and allocates nothing per edge. */
const EDGE_STEPS = 8;
/** The range the projection factor `p` actually occupies: z after rotation
 *  stays inside the relaxed cloud's radius, so p lands between roughly 0.7 and
 *  1.7. Anything outside is clamped into an end bucket rather than dropped. */
const EDGE_FAR = 0.5, EDGE_NEAR = 1.8;

const EDGE_DIM: string[] = [];
const EDGE_FIRE: string[] = [];
for (let i = 0; i < EDGE_STEPS; i++) {
  const u = (i + 0.5) / EDGE_STEPS;
  EDGE_DIM.push(`rgba(120, 150, 170, ${(0.035 * (EDGE_FAR + (EDGE_NEAR - EDGE_FAR) * u)).toFixed(3)})`);
  EDGE_FIRE.push(`rgba(34, 211, 238, ${(0.06 + 0.5 * u).toFixed(3)})`);
}
const EDGE_LIT = "rgba(155, 239, 252, 0.34)";

/** value → bucket index, clamped into the end buckets. */
function bucket(v: number, lo: number, hi: number): number {
  const u = (v - lo) / (hi - lo);
  return u <= 0 ? 0 : u >= 1 ? EDGE_STEPS - 1 : (u * EDGE_STEPS) | 0;
}

/** Quality rung 2 drops ambient filaments behind this depth. Fired and hovered
 *  edges are never thinned: they are the two that carry information, and
 *  keeping those affordable is the entire reason to degrade the rest. */
const THIN_DEPTH = 1.0;

/** 3D force layout: repulsion, springs, centre gravity. Seeded from note ids,
 *  so the sky looks the same on every visit.
 *
 *  Split into seed + relax so the 220 iterations can be spread across frames.
 *  In one go it is ~4.4M pair evaluations — a 100–200ms main-thread block that
 *  used to hide behind a fullscreen overlay's fade and now lands squarely in
 *  the dashboard's first paint, which is exactly where a hitch is most visible.
 */
const ITERS = 220;
/** Layout work per tick, as a time budget rather than a fixed iteration count.
 *  A fixed count is a fixed *fraction* of an O(n²) cost, so the freeze it buys
 *  grows with the vault — 22 iterations is a few ms at this size and would be
 *  hundreds at a thousand notes, i.e. the same total work delivered in hostile
 *  lumps. A budget keeps each tick short and simply uses more of them. */
const CHUNK_MS = 8;

function seed(g: Graph): { pos: Float32Array; vel: Float32Array } {
  const n = g.nodes.length;
  const pos = new Float32Array(n * 3);
  const vel = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const rnd = mulberry32(hash(g.nodes[i].id));
    pos[i * 3] = (rnd() - 0.5) * 420;
    pos[i * 3 + 1] = (rnd() - 0.5) * 420;
    pos[i * 3 + 2] = (rnd() - 0.5) * 420;
  }
  return { pos, vel };
}

function relax(g: Graph, pos: Float32Array, vel: Float32Array, iters: number) {
  const n = g.nodes.length;
  for (let iter = 0; iter < iters; iter++) {
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i * 3] - pos[j * 3];
        const dy = pos[i * 3 + 1] - pos[j * 3 + 1];
        const dz = pos[i * 3 + 2] - pos[j * 3 + 2];
        const d2 = dx * dx + dy * dy + dz * dz + 1;
        if (d2 > 62500) continue;
        const g = 620 / (d2 * Math.sqrt(d2));   // one sqrt, not six
        vel[i * 3] += dx * g; vel[j * 3] -= dx * g;
        vel[i * 3 + 1] += dy * g; vel[j * 3 + 1] -= dy * g;
        vel[i * 3 + 2] += dz * g; vel[j * 3 + 2] -= dz * g;
      }
    }
    for (const [a, b] of g.links) {
      const dx = pos[b * 3] - pos[a * 3];
      const dy = pos[b * 3 + 1] - pos[a * 3 + 1];
      const dz = pos[b * 3 + 2] - pos[a * 3 + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01;
      const f = (d - 72) * 0.012;
      vel[a * 3] += dx / d * f; vel[b * 3] -= dx / d * f;
      vel[a * 3 + 1] += dy / d * f; vel[b * 3 + 1] -= dy / d * f;
      vel[a * 3 + 2] += dz / d * f; vel[b * 3 + 2] -= dz / d * f;
    }
    for (let i = 0; i < n; i++) {
      vel[i * 3] -= pos[i * 3] * 0.004;
      vel[i * 3 + 1] -= pos[i * 3 + 1] * 0.004;
      vel[i * 3 + 2] -= pos[i * 3 + 2] * 0.004;
      pos[i * 3] += (vel[i * 3] *= 0.82);
      pos[i * 3 + 1] += (vel[i * 3 + 1] *= 0.82);
      pos[i * 3 + 2] += (vel[i * 3 + 2] *= 0.82);
    }
  }
}

/** Interstellar dust: parallax depth cues, so camera drift reads as motion
 *  through a volume rather than a flat picture rotating. */
function dust(seed: number, count = 260): Float32Array {
  const rnd = mulberry32(seed);
  const d = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    d[i * 3] = (rnd() - 0.5) * 1500;
    d[i * 3 + 1] = (rnd() - 0.5) * 1500;
    d[i * 3 + 2] = (rnd() - 0.5) * 1500;
  }
  return d;
}

type World = {
  g: Graph;
  pos: Float32Array;
  phase: Float32Array;                 // per-node breathing phase (spatial wave)
  motes: Float32Array;
  byId: Map<string, number>;           // full vault-relative path (no .md, lower)
  byBase: Map<string, number>;         // basename fallback — ambiguous, last wins
  edgesOf: Map<number, [number, number][]>;
  fires: Map<number, number>;          // node idx -> performance.now() of firing
};

export default function Brain({ graph, vault, fireRef, filter, onStatic,
                                mode = "focus" }: {
  /** Fetched by App, like every other panel's data. The brain is a renderer. */
  graph: Graph | null;
  vault: string;
  fireRef: React.MutableRefObject<((detail: string) => void) | null>;
  /** Lifted to App so the HUD can live outside this component — the chips sit
   *  on the stage in ambient and become the V.A.U.L.T. column when expanded. */
  filter: string | null;
  /** Reports the bottom of the quality ladder upward, so the HUD can say
   *  "static sky — frame budget" while living outside this component. A
   *  degradation nobody is told about is the kind of silent failure this whole
   *  OS is built to avoid. */
  onStatic?: (v: boolean) => void;
  /** "focus" renders at the display's rate; "ambient" is capped at AMBIENT_FPS.
   *  That cap is now the *only* difference between them. The dust layer is
   *  gated on the quality ladder and always was, whatever the old comment here
   *  claimed; and the camera fill stopped varying when the sky moved out of the
   *  centre cell to cover the whole shell, where there is no box to over-fill.
   *  The sky is never unmounted between the two — that is the point of a mode
   *  rather than a second view. */
  mode?: "ambient" | "focus";
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [quality, setQuality] = useState(0);
  // `ready` flips when the deferred layout has produced a world to draw. It is
  // state rather than a ref because the draw effect has to re-run when it lands.
  const [ready, setReady] = useState(false);
  useEffect(() => { onStatic?.(quality >= STATIC); }, [quality, onStatic]);
  // Camera angle accumulates rather than being derived from the frame
  // timestamp: the loop stops while the tab is hidden, and `t * k` would
  // teleport the sky by however long you were away. A ref, so changing quality
  // or mode restarts the loop without snapping the camera back to zero.
  const driftRef = useRef(0);
  const hotUntil = useRef(0);
  const modeRef = useRef(mode);
  modeRef.current = mode;
  // Filters *dim*, they do not remove. The layout is computed once and cached
  // (dashboard-plan §5), so hiding nodes would either relayout — throwing away
  // the picture you had just learned to read — or leave holes. Dimming keeps
  // the constellation recognisable and costs nothing.
  // A ref, not state, inside the draw loop: filtering must not restart the
  // animation, and the loop needs the current value each frame.
  const filterRef = useRef<string | null>(null);
  filterRef.current = filter;
  // Set by the draw effect; lets anything outside the loop ask for one repaint
  // without restarting it. Needed because in the degraded static mode nothing
  // repaints on its own, so a filter change would simply not show up.
  const redrawRef = useRef<(() => void) | null>(null);
  useEffect(() => { redrawRef.current?.(); }, [filter]);
  // Re-arm the ladder when the mode changes: a degrade measured against an
  // ambient cell says nothing about the expanded view, and vice versa. Without
  // this, one slow moment in a corner of the dashboard would leave the full
  // view permanently static.
  //
  // And run flat out for the length of the expand/collapse. Going *in* was
  // already smooth because focus means 60fps; coming *out* dropped to the
  // ambient 15 the instant the class flipped, so the sky stuttered through
  // roughly five frames of a 340ms animation while the box around it moved
  // smoothly. The transition is the one moment ambient must not apply.
  useEffect(() => {
    setQuality(0);
    hotUntil.current = performance.now() + DIVE_MS + 120;
  }, [mode]);
  const world = useRef<World | null>(null);
  const mouse = useRef({ x: 0, y: 0 });
  const hover = useRef<number | null>(null);
  /** Reused projection arrays. Three `Float32Array(n)` were allocated *inside*
   *  the frame, so the sky produced steady garbage at the display's rate and
   *  the amount grew with the vault — feeding exactly the GC pauses the rolling
   *  median below exists to tolerate. Reallocated only when the count changes. */
  const proj = useRef<{ px: Float32Array; py: Float32Array; pp: Float32Array } | null>(null);

  // Build the world from whatever graph App last handed us. Keyed on graph
  // identity, so a refetch that returns the same object does nothing and a
  // genuinely new one relays.
  const built = useRef<number | null>(null);
  useEffect(() => {
    const g = graph;
    if (!g) return;
    const sig = signature(g);
    if (built.current === sig && world.current) {
      // Same shape, fresher facts. `mtime` and `no_sync` both change without
      // moving a star, and they drive the week filter and the bronze ring — so
      // swap the data in and keep the positions. This is the common path on the
      // five-minute poll; relaying here would reshuffle a sky the reader has
      // already learned in order to arrive at the same picture.
      world.current.g = g;
      redrawRef.current?.();
      return;
    }
    built.current = sig;

    const byId = new Map<string, number>();
    const byBase = new Map<string, number>();
    g.nodes.forEach((n, i) => {
      byId.set(n.id.toLowerCase().replace(/\.md$/, ""), i);
      byBase.set(n.label.toLowerCase(), i);
    });
    const edgesOf = new Map<number, [number, number][]>();
    for (const [a, b] of g.links) {
      (edgesOf.get(a) ?? edgesOf.set(a, []).get(a)!).push([a, b]);
      (edgesOf.get(b) ?? edgesOf.set(b, []).get(b)!).push([a, b]);
    }

    // The layout runs in chunks across frames rather than in one block. Done
    // in one go it is a 100–200ms freeze at exactly the moment the dashboard
    // is painting for the first time — the single most visible hitch the boot
    // had. Nothing is drawn until it finishes: a sky that visibly settles
    // would contradict this view's one promise, that motion means something
    // happened.
    const { pos, vel } = seed(g);
    let iter = 0;
    let cancelled = false;
    let timer = 0;

    const finish = () => {
      // Phase from position, not from index: neighbours in space breathe
      // nearly in step, so the sky shows slow travelling swells the way
      // cortex does, instead of 200 independently twinkling stars.
      const phase = new Float32Array(g.nodes.length);
      for (let i = 0; i < g.nodes.length; i++) {
        phase[i] = (pos[i * 3] + pos[i * 3 + 1] * 0.6 + pos[i * 3 + 2] * 0.3) * 0.006;
      }
      world.current = {
        g, pos, phase, motes: dust(hash(vault || "sigma")),
        byId, byBase, edgesOf, fires: new Map(),
      };
      setReady(true);
    };

    const step = () => {
      if (cancelled) return;
      // Spend a budget, not a count — see CHUNK_MS. One iteration at a time so
      // the check is exact rather than a guess at how long a batch will take.
      const t0 = performance.now();
      do {
        relax(g, pos, vel, 1);
        iter++;
      } while (iter < ITERS && performance.now() - t0 < CHUNK_MS);
      if (iter >= ITERS) { finish(); return; }
      // setTimeout, not rAF: a background tab suspends rAF entirely, and this
      // dashboard is exactly the kind of thing you open in one and switch to
      // later. Slower there, but it finishes.
      timer = setTimeout(step, 0);
    };
    timer = setTimeout(step, 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [graph, vault]);

  // The firing hook the chat drawer calls through App. Reads now arrive as
  // vault-relative paths (describe() in app.py), so the exact node fires even
  // when basenames collide; bare basenames remain a fallback. Grep/Glob
  // details don't name a note and simply miss.
  useEffect(() => {
    fireRef.current = (detail: string) => {
      const w = world.current;
      if (!w) return;
      const key = detail.replace(/\\/g, "/").replace(/\.md$/i, "").toLowerCase().trim();
      const idx = w.byId.get(key)
        ?? w.byBase.get(key.split("/").pop() ?? key);
      if (idx === undefined) return;
      w.fires.set(idx, performance.now());
      // A fire is the one thing that earns full frame rate: promote the sky
      // for exactly as long as the pulse is actually travelling, then let it
      // fall back. This is also what makes firing visible at all in the
      // degraded static mode, where nothing else would repaint.
      hotUntil.current = performance.now() + HOT_MS;
    };
    return () => { fireRef.current = null; };
  }, [fireRef]);

  useEffect(() => {
    if (!ready || !world.current) return;
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const live = !reduced && quality < STATIC;
    let raf = 0, prev = 0, lastDraw = 0, dirty = true;
    let awake = !document.hidden;
    const times: number[] = [];
    /** Scratch for `project` below — one triple for the whole loop. */
    const P3 = new Float32Array(3);

    const draw = (t: number, animating: boolean) => {
      const w = world.current!;
      const W = canvas.clientWidth, H = canvas.clientHeight;
      const dpr = window.devicePixelRatio || 1;
      // Round before comparing: canvas.width truncates its setter, so at
      // fractional DPR (Windows 125%/150%) an un-rounded comparison is true
      // every frame — the backing store reallocates 60×/s, frame time blows
      // the budget, and the sky degrades to static "randomly".
      // Quantised, so a smooth resize does not reallocate every frame. The
      // transform then maps the scene onto whatever buffer we actually have
      // rather than assuming it is exactly W*dpr — which is what keeps a
      // slightly-too-large buffer looking supersampled instead of stretched.
      const cw = Math.ceil((W * dpr) / STEP_PX) * STEP_PX;
      const ch = Math.ceil((H * dpr) / STEP_PX) * STEP_PX;
      if (canvas.width !== cw || canvas.height !== ch) {
        canvas.width = cw; canvas.height = ch;
      }
      ctx.setTransform(cw / W, 0, 0, ch / H, 0, 0);
      ctx.clearRect(0, 0, W, H);

      const rotY = driftRef.current + mouse.current.x * 0.35;
      const rotX = mouse.current.y * 0.22;
      const cy = Math.cos(rotY), sy = Math.sin(rotY);
      const cx = Math.cos(rotX), sx = Math.sin(rotX);
      const scale = Math.min(W, H) / 560;

      // Project once into flat arrays; everything below reads these. The result
      // lands in the scratch triple rather than a fresh tuple — this used to
      // return a new array per node *and* per dust mote on every frame, which
      // was the largest single source of per-frame garbage in the scene.
      const project = (src: Float32Array, i: number) => {
        const x0 = src[i * 3], y0 = src[i * 3 + 1], z0 = src[i * 3 + 2];
        const x1 = x0 * cy + z0 * sy, z1 = -x0 * sy + z0 * cy;
        const y2 = y0 * cx - z1 * sx, z2 = y0 * sx + z1 * cx;
        const p = 620 / (620 + z2);
        P3[0] = W / 2 + x1 * p * scale; P3[1] = H / 2 + y2 * p * scale; P3[2] = p;
      };

      const n = w.g.nodes.length;
      let PR = proj.current;
      if (!PR || PR.px.length !== n) {
        PR = proj.current = { px: new Float32Array(n), py: new Float32Array(n), pp: new Float32Array(n) };
      }
      const { px, py, pp } = PR;
      for (let i = 0; i < n; i++) {
        project(w.pos, i);
        px[i] = P3[0]; py[i] = P3[1]; pp[i] = P3[2];
      }

      const now = performance.now();
      const fireAge = (i: number) => {
        const f = w.fires.get(i);
        if (f === undefined) return 0;
        const a = 1 - (now - f) / FIRE_MS;
        if (a <= 0) { w.fires.delete(i); return 0; }
        return a;
      };

      ctx.globalCompositeOperation = "lighter";

      /* ---- dust ----------------------------------------------------------
         First thing dropped when the frame budget slips: 260 motes are pure
         depth cue, and losing them costs parallax rather than information. */
      if (quality < 1) {
        ctx.fillStyle = "rgba(150,185,210,0.30)";
        for (let i = 0; i < w.motes.length / 3; i++) {
          project(w.motes, i);
          const x = P3[0], y = P3[1], p = P3[2];
          if (p <= 0 || x < 0 || y < 0 || x > W || y > H) continue;
          ctx.globalAlpha = Math.min(0.5, (p - 0.45) * 0.7);
          if (ctx.globalAlpha <= 0) continue;
          ctx.fillRect(x, y, p * 1.4, p * 1.4);
        }
        ctx.globalAlpha = 1;
      }

      /* ---- edges ----------------------------------------------------------
         Collected into one path per colour bucket and stroked once each, rather
         than a path and a parsed colour string per edge. Compositing is
         additive here, so the order buckets are drawn in cannot change the
         result — nothing is lost by leaving the link list's own order behind. */
      const hi = hover.current;
      const hotEdges = hi !== null ? w.edgesOf.get(hi) : undefined;
      ctx.lineWidth = 1;
      const dimPaths: (Path2D | undefined)[] = new Array(EDGE_STEPS);
      const firePaths: (Path2D | undefined)[] = new Array(EDGE_STEPS);
      let litPath: Path2D | undefined;
      for (const [a, b] of w.g.links) {
        const boost = Math.max(fireAge(a), fireAge(b));
        const lit = hi !== null && (a === hi || b === hi);
        // Depth-fade edges too, or the far side of the volume reads as a
        // flat wire cage sitting on top of the near stars.
        const depth = Math.min(pp[a], pp[b]);
        let path: Path2D;
        if (boost > 0) {
          const s = bucket(boost, 0, 1);
          path = firePaths[s] ?? (firePaths[s] = new Path2D());
        } else if (lit) {
          path = litPath ?? (litPath = new Path2D());
        } else {
          // Rung 2: the far filaments go. Never the fired or hovered ones —
          // those are the two that mean something.
          if (quality >= 2 && depth < THIN_DEPTH) continue;
          const s = bucket(depth, EDGE_FAR, EDGE_NEAR);
          path = dimPaths[s] ?? (dimPaths[s] = new Path2D());
        }
        path.moveTo(px[a], py[a]);
        path.lineTo(px[b], py[b]);
      }
      for (let s = 0; s < EDGE_STEPS; s++) {
        const p = dimPaths[s];
        if (p) { ctx.strokeStyle = EDGE_DIM[s]; ctx.stroke(p); }
      }
      if (litPath) { ctx.strokeStyle = EDGE_LIT; ctx.stroke(litPath); }
      for (let s = 0; s < EDGE_STEPS; s++) {
        const p = firePaths[s];
        if (p) { ctx.strokeStyle = EDGE_FIRE[s]; ctx.stroke(p); }
      }

      /* ---- axon pulses ---------------------------------------------------
         Only fired nodes emit these. Nothing else in the scene travels, so a
         moving light always means a tool actually touched that note.        */
      const axon = glow(FIRE_COLOR);
      for (const [idx] of w.fires) {
        const a = fireAge(idx);
        if (a <= 0) continue;
        const u = (1 - a) / AXON;
        if (u > 1) continue;
        const edges = w.edgesOf.get(idx);
        if (!edges) continue;
        const fade = 1 - u;
        for (const [ea, eb] of edges) {
          const far = ea === idx ? eb : ea;
          const x = px[idx] + (px[far] - px[idx]) * u;
          const y = py[idx] + (py[far] - py[idx]) * u;
          const s = 9 * pp[far] * fade;
          ctx.globalAlpha = fade;
          ctx.drawImage(axon, x - s, y - s, s * 2, s * 2);
        }
      }
      ctx.globalAlpha = 1;

      /* ---- stars --------------------------------------------------------- */
      for (let i = 0; i < n; i++) {
        const node = w.g.nodes[i];
        const boost = fireAge(i);
        // The ambient pulse: a slow spatial swell, deliberately gentle and
        // never white — the eye reads it as breathing, not as an event.
        const swell = animating
          ? 0.80 + 0.20 * Math.sin(t / 3400 * Math.PI * 2 + w.phase[i])
          : 0.88;
        const depth = Math.max(0, Math.min(1, (pp[i] - 0.42) / 0.9));
        const f = filterRef.current;
        const passes = !f
          || (f === "week" ? (node.mtime ? Date.now() - new Date(node.mtime).getTime() < 7 * 864e5 : false)
              : node.bucket === f);
        const dim = (hi !== null && i !== hi && !hotEdges?.some(([a, b]) => a === i || b === i)
          ? 0.30 : 1) * (passes ? 1 : 0.12);

        const r = (1.7 + Math.sqrt(node.inlinks) * 1.05) * pp[i] + boost * 6;
        const spr = glow(boost > 0 ? FIRE_COLOR : COLORS[node.bucket] ?? "#8299A6");
        // A smaller sprite at reduced quality: the blit is fill-rate bound, so
        // shrinking the reach is the cheapest thing that buys back a frame
        // without changing what is on screen.
        const reach = Math.max(r, 1) * ((quality < 1 ? 3.9 : 2.9) + boost * 3.2);

        ctx.globalAlpha = Math.min(1, (0.36 + 0.62 * depth) * swell * dim + boost);
        ctx.drawImage(spr, px[i] - reach, py[i] - reach, reach * 2, reach * 2);
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";

      // The no-sync ring, drawn after the additive pass so it outlines a star
      // rather than adding to its bloom. Same fact as the ▦ in Today and
      // Projects, rendered in the only way this view has room for.
      ctx.lineWidth = 1;
      ctx.strokeStyle = NOSYNC_COLOR;
      for (let i = 0; i < n; i++) {
        if (!w.g.nodes[i].no_sync) continue;
        const depth = Math.max(0, Math.min(1, (pp[i] - 0.42) / 0.9));
        // The ring has to obey the filter too. It did not at first, so a
        // filtered-out ProCertus node vanished while its bronze ring stayed at
        // full brightness — a marker floating with nothing under it.
        const fr = filterRef.current;
        const rp = !fr
          || (fr === "week"
              ? (w.g.nodes[i].mtime ? Date.now() - new Date(w.g.nodes[i].mtime!).getTime() < 7 * 864e5 : false)
              : w.g.nodes[i].bucket === fr);
        const dim = (hi !== null && i !== hi ? 0.35 : 1) * (rp ? 1 : 0.12);
        const r = (1.7 + Math.sqrt(w.g.nodes[i].inlinks) * 1.05) * pp[i];
        ctx.globalAlpha = Math.min(0.85, (0.30 + 0.55 * depth) * dim);
        ctx.beginPath();
        ctx.arc(px[i], py[i], Math.max(r, 1.2) + 2.8, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      if (hi !== null) {
        ctx.font = "12px 'JetBrains Mono', Consolas, monospace";
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(6,10,18,0.9)";
        ctx.strokeText(w.g.nodes[hi].label, px[hi] + 10, py[hi] - 8);
        ctx.fillStyle = "#D9E4EB";
        ctx.fillText(w.g.nodes[hi].label, px[hi] + 10, py[hi] - 8);
      }

      // hit-test bookkeeping for hover/click, reused by the handlers below
      (canvas as any)._proj = PR;
    };

    /* The frame governor. The RAF runs continuously — it is nearly free when
       it decides not to draw — and everything about *whether* to paint is
       decided here rather than by starting and stopping the loop. That is what
       lets a fire animate even in the static mode, where nothing else would
       ever repaint. */
    const loop = (t: number) => {
      raf = requestAnimationFrame(loop);
      if (!awake) return;                       // hidden tab: cost nothing
      const dt = prev ? Math.min(t - prev, 100) : 16;
      prev = t;

      const hot = t < hotUntil.current;
      const animating = live || hot;
      if (!animating && !dirty) return;         // static and nothing changed

      // Ambient is capped; focus and anything hot run at the display's rate.
      const interval = (modeRef.current === "focus" || hot) ? 0 : 1000 / AMBIENT_FPS;
      if (animating && !dirty && t - lastDraw < interval) return;
      lastDraw = t;
      dirty = false;
      if (animating) driftRef.current += dt * 0.000045;

      const t0 = performance.now();
      draw(t, animating);
      const ms = performance.now() - t0;

      // Median of a window, not "any frame over budget, counted". One GC pause
      // must not be able to condemn the sky, and the old rule let it.
      if (!animating || quality >= STATIC) return;
      times.push(ms);
      if (times.length < WINDOW) return;
      const median = [...times].sort((a, b) => a - b)[times.length >> 1];
      times.length = 0;
      if (median > SLOW_MS) setQuality(q => Math.min(STATIC, q + 1));
    };
    raf = requestAnimationFrame(loop);

    /* Waking and sleeping. A dashboard is left open all day; painting a
       starfield nobody can see is the one cost that would make a permanent
       brain indefensible.

       Visibility only — deliberately NOT window blur. This dashboard's whole
       job is ambient awareness, and the case it is for is having it open on a
       second screen while you work in another window. Blur would freeze it in
       exactly that case, which is the one that matters most. `document.hidden`
       is the only signal that actually means nobody can see this. */
    const onVisibility = () => {
      awake = !document.hidden;
      if (awake) { prev = 0; dirty = true; }    // repaint once, do not jump
    };
    document.addEventListener("visibilitychange", onVisibility);

    // The per-frame clientWidth read only adapts while the loop is drawing, so
    // a resize in static mode left a stretched picture until the mouse moved.
    const ro = new ResizeObserver(() => { dirty = true; });
    ro.observe(canvas);

    // A filter change has to repaint too, for the same reason. filterRef is
    // read inside draw, so this is the only thing that has to happen.
    redrawRef.current = () => { dirty = true; };

    const pick = (e: MouseEvent): number | null => {
      const proj = (canvas as any)._proj;
      if (!proj) return null;
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      let best: number | null = null, bd = 144;
      for (let i = 0; i < proj.px.length; i++) {
        const d = (proj.px[i] - mx) ** 2 + (proj.py[i] - my) ** 2;
        if (d < bd) { bd = d; best = i; }
      }
      return best;
    };
    const onMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      mouse.current.x = (e.clientX - rect.left) / rect.width - 0.5;
      mouse.current.y = (e.clientY - rect.top) / rect.height - 0.5;
      hover.current = pick(e);
      canvas.style.cursor = hover.current !== null ? "pointer" : "default";
      dirty = true;
    };
    // Without this, moving the cursor off a star and onto something layered
    // over the canvas leaves that star's label painted, pointing at nothing.
    const onLeave = () => {
      hover.current = null;
      mouse.current.x = mouse.current.y = 0;
      dirty = true;
    };
    const onClick = (e: MouseEvent) => {
      const i = pick(e);
      if (i !== null && world.current) {
        // A graph you can only look at is a screensaver — click opens the note.
        const id = world.current.g.nodes[i].id.replace(/\.md$/i, "");
        window.location.href = obsidianHref(vault, id);
      }
    };
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    canvas.addEventListener("click", onClick);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      redrawRef.current = null;
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      canvas.removeEventListener("click", onClick);
    };
  }, [ready, quality, vault]);

  // Just the sky. The numbers and the filters are VaultHud, below, because in
  // the ambient state they live on the stage's edges rather than over the
  // canvas — and the two need to be positioned independently.
  return (
    <div className={`brain-field ${mode} ${ready ? "lit" : ""}`}>
      <div className="brain-nebula" aria-hidden="true" />
      <canvas ref={canvasRef} className="brain-canvas" />
    </div>
  );
}

/* ------------------------------------------------------------------ the HUD */

/** The vault's numbers and the bucket filters — the V.A.U.L.T. column.
 *
 *  One arrangement, always on. There used to be two variants showing exactly
 *  the same facts and driving exactly the same filter: a `chips` row for when
 *  the sky shared its cell, and this column for when it had the whole shell.
 *  The sky has the whole shell at all times now, so the row it was fitting
 *  around no longer exists and keeping both would mean maintaining two
 *  renderings of one set of numbers.
 */
export function VaultHud({ graph, filter, onFilter, staticSky = false }: {
  graph: Graph | null;
  filter: string | null;
  onFilter: (f: string | null) => void;
  staticSky?: boolean;
}) {
  // Memoised: App re-renders on every fleet-progress SSE message, and these
  // walked all 200 nodes three times on each of them.
  const { counts, noSyncCount, weekCount, lastTouched } = useMemo(() => {
    const c: Record<string, number> = {};
    let ns = 0, wk = 0;
    let last: { label: string; mtime: string } | null = null;
    const weekAgo = Date.now() - 7 * 864e5;
    graph?.nodes.forEach(n => {
      c[n.bucket] = (c[n.bucket] ?? 0) + 1;
      if (n.no_sync) ns++;
      if (n.mtime && new Date(n.mtime).getTime() > weekAgo) wk++;
      // Folded into the pass that was already walking every node. The brain
      // used to compute this into its `World` — by building a full sorted copy
      // of the node list to read one element off the front — and then never
      // render it anywhere, which is why the column has been missing the row
      // dashboard-plan §5 promised it since the day it shipped.
      if (n.mtime && (last === null || n.mtime > last.mtime)) {
        last = { label: n.label, mtime: n.mtime };
      }
    });
    return { counts: c, noSyncCount: ns, weekCount: wk,
             lastTouched: last as { label: string; mtime: string } | null };
  }, [graph]);

  const shown = filter ? (filter === "week" ? weekCount : (counts[filter] ?? 0)) : 0;

  return (
    <aside className="brain-stats">
      <div className="hero">{graph?.notes ?? "…"}</div>
      <div className="hero-label">NOTES</div>
      <div className="stat-row"><span>edges</span><b>{graph?.edges ?? "…"}</b></div>
      <div className="stat-row last-touched"
           title={lastTouched ? `${lastTouched.label} — ${lastTouched.mtime}` : ""}>
        <span>last touched</span><b>{lastTouched?.label ?? "—"}</b>
      </div>
      <div className="legend">
        {BUCKETS.map(([k, label]) => (
          <button key={k}
                  className={`legend-row ${filter === k ? "on" : ""}`}
                  onClick={() => onFilter(filter === k ? null : k)}
                  title={`show only ${label}`}>
            <i style={{ background: COLORS[k] }} />
            <span>{label}</span><b>{counts[k] ?? 0}</b>
          </button>
        ))}
        {noSyncCount > 0 && (
          // Listed apart from the buckets because it is not one: a node has
          // exactly one bucket and may *also* be no-sync.
          // `nosync-key`, not `nosync`: the overlay in nosync.tsx owns the
          // bare class and sizes itself to 760px, which this row inherited.
          <div className="legend-row nosync-key" title="never leaves this machine (Ctrl+.)">
            <i style={{ background: "transparent", boxShadow: `inset 0 0 0 1px ${NOSYNC_COLOR}` }} />
            <span>no-sync</span><b>{noSyncCount}</b>
          </div>
        )}
      </div>
      <div className="brain-filters">
        <button className={filter === "week" ? "on" : ""}
                onClick={() => onFilter(filter === "week" ? null : "week")}
                title="notes touched in the last 7 days">changed this week</button>
        <button onClick={() => onFilter(null)} disabled={!filter}>all</button>
      </div>
      {filter && (
        <p className="dim brain-filter-note">
          showing {shown} of {graph?.notes ?? 0} · the rest are dimmed, not hidden
        </p>
      )}
      <p className="dim brain-hint">
        click a star to open the note · <kbd>Esc</kbd> back
        {staticSky && <><br />static sky — frame budget</>}
      </p>
    </aside>
  );
}
