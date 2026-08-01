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
import { useEffect, useRef, useState } from "react";
import { get, obsidianHref } from "./api";
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

/** One-shot 3D force layout: repulsion, springs, centre gravity. Seeded from
 *  note ids, so the sky looks the same on every visit. */
function layout(g: Graph): Float32Array {
  const n = g.nodes.length;
  const pos = new Float32Array(n * 3);
  const vel = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const rnd = mulberry32(hash(g.nodes[i].id));
    pos[i * 3] = (rnd() - 0.5) * 420;
    pos[i * 3 + 1] = (rnd() - 0.5) * 420;
    pos[i * 3 + 2] = (rnd() - 0.5) * 420;
  }
  for (let iter = 0; iter < 220; iter++) {
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
  return pos;
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
  lastTouched: string;
};

export default function Brain({ open, vault, fireRef }: {
  open: boolean;
  vault: string;
  fireRef: React.MutableRefObject<((detail: string) => void) | null>;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [staticSky, setStaticSky] = useState(false);
  // Filters *dim*, they do not remove. The layout is computed once and cached
  // (dashboard-plan §5), so hiding nodes would either relayout — throwing away
  // the picture you had just learned to read — or leave holes. Dimming keeps
  // the constellation recognisable and costs nothing.
  // A ref, not state, inside the draw loop: filtering must not restart the
  // animation, and the loop needs the current value each frame.
  const [filter, setFilter] = useState<string | null>(null);
  const filterRef = useRef<string | null>(null);
  filterRef.current = filter;
  const world = useRef<World | null>(null);
  const mouse = useRef({ x: 0, y: 0 });
  const hover = useRef<number | null>(null);

  const fetching = useRef(false);      // StrictMode double-invokes effects
  useEffect(() => {
    if (!open || graph || fetching.current) return;
    fetching.current = true;
    get<Graph>("graph").then(g => {
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
      const touched = [...g.nodes].sort((a, b) =>
        (b.mtime ?? "").localeCompare(a.mtime ?? ""))[0];

      const pos = layout(g);
      // Phase from position, not from index: neighbours in space breathe nearly
      // in step, so the sky shows slow travelling swells the way cortex does,
      // instead of 164 independently twinkling stars.
      const phase = new Float32Array(g.nodes.length);
      for (let i = 0; i < g.nodes.length; i++) {
        phase[i] = (pos[i * 3] + pos[i * 3 + 1] * 0.6 + pos[i * 3 + 2] * 0.3) * 0.006;
      }

      world.current = {
        g, pos, phase, motes: dust(hash(vault || "sigma")),
        byId, byBase, edgesOf, fires: new Map(),
        lastTouched: touched?.label ?? "—",
      };
      setGraph(g);
    }).catch(() => setGraph(null))
      .finally(() => { fetching.current = false; });
  }, [open, graph, vault]);

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
      if (idx !== undefined) w.fires.set(idx, performance.now());
    };
    return () => { fireRef.current = null; };
  }, [fireRef]);

  useEffect(() => {
    if (!open || !graph) return;
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0, frames = 0, slow = 0, live = !reduced && !staticSky;

    const draw = (t: number) => {
      const w = world.current!;
      const W = canvas.clientWidth, H = canvas.clientHeight;
      const dpr = window.devicePixelRatio || 1;
      // Round before comparing: canvas.width truncates its setter, so at
      // fractional DPR (Windows 125%/150%) an un-rounded comparison is true
      // every frame — the backing store reallocates 60×/s, frame time blows
      // the budget, and the sky degrades to static "randomly".
      const cw = Math.round(W * dpr), ch = Math.round(H * dpr);
      if (canvas.width !== cw) { canvas.width = cw; canvas.height = ch; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      const drift = live ? t * 0.000045 : 0.6;
      const rotY = drift + mouse.current.x * 0.35;
      const rotX = mouse.current.y * 0.22;
      const cy = Math.cos(rotY), sy = Math.sin(rotY);
      const cx = Math.cos(rotX), sx = Math.sin(rotX);
      const scale = Math.min(W, H) / 560;

      // Project once into flat arrays; everything below reads these.
      const project = (src: Float32Array, i: number): [number, number, number] => {
        const x0 = src[i * 3], y0 = src[i * 3 + 1], z0 = src[i * 3 + 2];
        const x1 = x0 * cy + z0 * sy, z1 = -x0 * sy + z0 * cy;
        const y2 = y0 * cx - z1 * sx, z2 = y0 * sx + z1 * cx;
        const p = 620 / (620 + z2);
        return [W / 2 + x1 * p * scale, H / 2 + y2 * p * scale, p];
      };

      const n = w.g.nodes.length;
      const px = new Float32Array(n), py = new Float32Array(n), pp = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        const [x, y, p] = project(w.pos, i);
        px[i] = x; py[i] = y; pp[i] = p;
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

      /* ---- dust ---------------------------------------------------------- */
      ctx.fillStyle = "rgba(150,185,210,0.30)";
      for (let i = 0; i < w.motes.length / 3; i++) {
        const [x, y, p] = project(w.motes, i);
        if (p <= 0 || x < 0 || y < 0 || x > W || y > H) continue;
        ctx.globalAlpha = Math.min(0.5, (p - 0.45) * 0.7);
        if (ctx.globalAlpha <= 0) continue;
        ctx.fillRect(x, y, p * 1.4, p * 1.4);
      }
      ctx.globalAlpha = 1;

      /* ---- edges --------------------------------------------------------- */
      const hi = hover.current;
      const hotEdges = hi !== null ? w.edgesOf.get(hi) : undefined;
      ctx.lineWidth = 1;
      for (const [a, b] of w.g.links) {
        const boost = Math.max(fireAge(a), fireAge(b));
        const lit = hi !== null && (a === hi || b === hi);
        // Depth-fade edges too, or the far side of the volume reads as a
        // flat wire cage sitting on top of the near stars.
        const depth = Math.min(pp[a], pp[b]);
        ctx.strokeStyle = boost > 0
          ? `rgba(34, 211, 238, ${(0.06 + 0.5 * boost).toFixed(3)})`
          : lit
            ? "rgba(155, 239, 252, 0.34)"
            : `rgba(120, 150, 170, ${(0.035 * depth).toFixed(3)})`;
        ctx.beginPath();
        ctx.moveTo(px[a], py[a]);
        ctx.lineTo(px[b], py[b]);
        ctx.stroke();
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
        const swell = live
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
        const reach = Math.max(r, 1) * (3.9 + boost * 3.2);

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
      (canvas as any)._proj = { px, py };
    };

    const loop = (t: number) => {
      const t0 = performance.now();
      draw(t);
      const dt = performance.now() - t0;
      if (++frames > 30 && dt > 26 && ++slow > 45) {
        // §11: cache the layout, cap the cost, degrade to static if the frame
        // budget slips — a stuttering sky is worse than a still one.
        setStaticSky(true);
        live = false;
        return;
      }
      if (live) raf = requestAnimationFrame(loop);
    };

    if (live) raf = requestAnimationFrame(loop);
    else draw(performance.now());

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
      if (!live) draw(performance.now());
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
    canvas.addEventListener("click", onClick);
    return () => {
      cancelAnimationFrame(raf);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("click", onClick);
    };
  }, [open, graph, staticSky, vault]);

  if (!open) return null;

  const counts: Record<string, number> = {};
  graph?.nodes.forEach(n => { counts[n.bucket] = (counts[n.bucket] ?? 0) + 1; });
  const noSyncCount = graph?.nodes.filter(n => n.no_sync).length ?? 0;
  const weekCount = graph?.nodes.filter(n =>
    n.mtime ? Date.now() - new Date(n.mtime).getTime() < 7 * 864e5 : false).length ?? 0;

  return (
    <div className="brain-overlay">
      <div className="brain-nebula" aria-hidden="true" />
      <canvas ref={canvasRef} className="brain-canvas" />
      <div className="brain-vignette" aria-hidden="true" />
      <aside className="brain-stats">
        <div className="hero">{graph?.notes ?? "…"}</div>
        <div className="hero-label">NOTES</div>
        <div className="stat-row"><span>edges</span><b>{graph?.edges ?? "…"}</b></div>
        <div className="stat-row"><span>last touched</span><b>{world.current?.lastTouched ?? "…"}</b></div>
        <div className="legend">
          {BUCKETS.map(([k, label]) => (
            <button key={k}
                    className={`legend-row ${filter === k ? "on" : ""}`}
                    onClick={() => setFilter(filter === k ? null : k)}
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
                  onClick={() => setFilter(filter === "week" ? null : "week")}
                  title="notes touched in the last 7 days">changed this week</button>
          <button onClick={() => setFilter(null)} disabled={!filter}>all</button>
        </div>
        {filter && (
          <p className="dim brain-filter-note">
            showing {filter === "week" ? weekCount : (counts[filter] ?? 0)} of {graph?.notes ?? 0}
            {" "}· the rest are dimmed, not hidden
          </p>
        )}
        <p className="dim brain-hint">
          click a star to open the note · <kbd>Esc</kbd> back
          {staticSky && <><br />static sky — frame budget</>}
        </p>
      </aside>
    </div>
  );
}
