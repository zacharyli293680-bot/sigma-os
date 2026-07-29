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
 * §11's render-budget risk is handled directly: if the average frame runs
 * long, the loop degrades to a static render that repaints only on
 * interaction. prefers-reduced-motion gets the static sky from the start.
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
        const f = 620 / d2;
        vel[i * 3] += dx * f / Math.sqrt(d2); vel[j * 3] -= dx * f / Math.sqrt(d2);
        vel[i * 3 + 1] += dy * f / Math.sqrt(d2); vel[j * 3 + 1] -= dy * f / Math.sqrt(d2);
        vel[i * 3 + 2] += dz * f / Math.sqrt(d2); vel[j * 3 + 2] -= dz * f / Math.sqrt(d2);
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

type World = {
  g: Graph;
  pos: Float32Array;
  byBase: Map<string, number>;
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
  const world = useRef<World | null>(null);
  const mouse = useRef({ x: 0, y: 0 });
  const hover = useRef<number | null>(null);

  useEffect(() => {
    if (!open || graph) return;
    get<Graph>("graph").then(g => {
      const byBase = new Map<string, number>();
      g.nodes.forEach((n, i) => byBase.set(n.label.toLowerCase(), i));
      const edgesOf = new Map<number, [number, number][]>();
      for (const [a, b] of g.links) {
        (edgesOf.get(a) ?? edgesOf.set(a, []).get(a)!).push([a, b]);
        (edgesOf.get(b) ?? edgesOf.set(b, []).get(b)!).push([a, b]);
      }
      const touched = [...g.nodes].sort((a, b) =>
        (b.mtime ?? "").localeCompare(a.mtime ?? ""))[0];
      world.current = {
        g, pos: layout(g), byBase, edgesOf, fires: new Map(),
        lastTouched: touched?.label ?? "—",
      };
      setGraph(g);
    }).catch(() => setGraph(null));
  }, [open, graph]);

  // The firing hook the chat drawer calls through App: a Read's basename
  // lights its node. Grep/Glob details don't name a note and simply miss.
  useEffect(() => {
    fireRef.current = (detail: string) => {
      const w = world.current;
      if (!w) return;
      const key = detail.replace(/\.md$/i, "").toLowerCase().trim();
      const idx = w.byBase.get(key);
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
      if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);

      const drift = live ? t * 0.000045 : 0.6;
      const rotY = drift + mouse.current.x * 0.35;
      const rotX = mouse.current.y * 0.22;
      const cy = Math.cos(rotY), sy = Math.sin(rotY);
      const cx = Math.cos(rotX), sx = Math.sin(rotX);
      const scale = Math.min(W, H) / 720;
      const breathe = live ? 0.72 + 0.22 * Math.sin((t / 4000) * Math.PI * 2) : 0.85;

      const n = w.g.nodes.length;
      const px = new Float32Array(n), py = new Float32Array(n), pp = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        const x0 = w.pos[i * 3], y0 = w.pos[i * 3 + 1], z0 = w.pos[i * 3 + 2];
        const x1 = x0 * cy + z0 * sy, z1 = -x0 * sy + z0 * cy;
        const y2 = y0 * cx - z1 * sx, z2 = y0 * sx + z1 * cx;
        const p = 620 / (620 + z2);
        px[i] = W / 2 + x1 * p * scale;
        py[i] = H / 2 + y2 * p * scale;
        pp[i] = p;
      }

      const now = performance.now();
      const fireAge = (i: number) => {
        const f = w.fires.get(i);
        if (f === undefined) return 0;
        const a = 1 - (now - f) / FIRE_MS;
        if (a <= 0) { w.fires.delete(i); return 0; }
        return a;
      };

      ctx.lineWidth = 1;
      for (const [a, b] of w.g.links) {
        const boost = Math.max(fireAge(a), fireAge(b));
        ctx.strokeStyle = boost > 0
          ? `rgba(34, 211, 238, ${(0.06 + 0.5 * boost).toFixed(3)})`
          : "rgba(120, 150, 170, 0.05)";
        ctx.beginPath();
        ctx.moveTo(px[a], py[a]);
        ctx.lineTo(px[b], py[b]);
        ctx.stroke();
      }

      for (let i = 0; i < n; i++) {
        const node = w.g.nodes[i];
        const boost = fireAge(i);
        const r = (1.5 + Math.sqrt(node.inlinks) * 0.95) * pp[i] + boost * 6;
        ctx.globalAlpha = Math.min(1, breathe + boost);
        ctx.fillStyle = boost > 0 ? "#9BEFFC" : COLORS[node.bucket] ?? "#8299A6";
        ctx.shadowColor = ctx.fillStyle;
        ctx.shadowBlur = boost > 0 ? 20 : r > 3.4 ? 9 : 0;
        ctx.beginPath();
        ctx.arc(px[i], py[i], Math.max(r, 1), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.shadowBlur = 0;
      ctx.globalAlpha = 1;

      if (hover.current !== null) {
        const i = hover.current;
        ctx.font = "12px 'JetBrains Mono', Consolas, monospace";
        ctx.fillStyle = "#D9E4EB";
        ctx.fillText(w.g.nodes[i].label, px[i] + 10, py[i] - 8);
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

  return (
    <div className="brain-overlay">
      <canvas ref={canvasRef} className="brain-canvas" />
      <aside className="brain-stats">
        <div className="hero">{graph?.notes ?? "…"}</div>
        <div className="hero-label">NOTES</div>
        <div className="stat-row"><span>edges</span><b>{graph?.edges ?? "…"}</b></div>
        <div className="stat-row"><span>last touched</span><b>{world.current?.lastTouched ?? "…"}</b></div>
        <div className="legend">
          {BUCKETS.map(([k, label]) => (
            <div key={k} className="legend-row">
              <i style={{ background: COLORS[k] }} />
              <span>{label}</span><b>{counts[k] ?? 0}</b>
            </div>
          ))}
        </div>
        <p className="dim brain-hint">
          click a star to open the note · <kbd>Esc</kbd> back
          {staticSky && <><br />static sky — frame budget</>}
        </p>
      </aside>
    </div>
  );
}
