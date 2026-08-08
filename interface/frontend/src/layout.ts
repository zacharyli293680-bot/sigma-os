/**
 * The brain's 3D force layout, extracted from brain.tsx.
 *
 * It lives on its own for one reason: it now runs in a Web Worker, and the
 * main thread still needs the identical code for the fallback path. Two copies
 * of a seeded deterministic algorithm is the one duplication this project
 * cannot afford — the whole layout cache below rests on the claim that the
 * same inputs give bit-identical positions, and a drifted second copy would
 * break that silently, by moving stars rather than by throwing.
 *
 * Nothing here touches the DOM, so it imports cleanly into a worker.
 *
 * The algorithm is unchanged from the version that ran on the main thread:
 * repulsion with a distance cutoff, springs along links, centre gravity,
 * seeded from note ids so the sky looks the same on every visit. It was
 * deliberately NOT replaced with a spatial index. The relaxed cloud's radius
 * (~333 units) is larger than the repulsion cutoff (250), so 35% of all pairs
 * are genuinely in range — a hard 2.9x ceiling on what any partitioning can
 * prune, and a measured integer-binned grid came out *slower* than the plain
 * O(n^2) loop at this vault's size (0.33x at 476 notes, 1.4x at 2000). The
 * cost was moved off the main thread and cached instead of approximated,
 * which keeps the positions exact.
 */

/** Iterations to run. The layout is frozen after this — ambient motion in the
 *  view is camera drift, never re-simulation. */
export const ITERS = 220;

/** What the worker is handed. Ids and links only: the node objects carry
 *  `mtime`, `bucket` and `no_sync`, none of which can move a star, and
 *  structured-cloning them would be pure copying cost. */
export type LayoutRequest = { ids: string[]; links: [number, number][] };

export function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

export function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Start positions, one seeded RNG per note id. */
export function seedPositions(ids: string[]): { pos: Float32Array; vel: Float32Array } {
  const n = ids.length;
  const pos = new Float32Array(n * 3);
  const vel = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const rnd = mulberry32(hash(ids[i]));
    pos[i * 3] = (rnd() - 0.5) * 420;
    pos[i * 3 + 1] = (rnd() - 0.5) * 420;
    pos[i * 3 + 2] = (rnd() - 0.5) * 420;
  }
  return { pos, vel };
}

export function relax(
  links: [number, number][], n: number,
  pos: Float32Array, vel: Float32Array, iters: number,
) {
  for (let iter = 0; iter < iters; iter++) {
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = pos[i * 3] - pos[j * 3];
        const dy = pos[i * 3 + 1] - pos[j * 3 + 1];
        const dz = pos[i * 3 + 2] - pos[j * 3 + 2];
        const d2 = dx * dx + dy * dy + dz * dz + 1;
        if (d2 > 62500) continue;
        const f = 620 / (d2 * Math.sqrt(d2));   // one sqrt, not six
        vel[i * 3] += dx * f; vel[j * 3] -= dx * f;
        vel[i * 3 + 1] += dy * f; vel[j * 3 + 1] -= dy * f;
        vel[i * 3 + 2] += dz * f; vel[j * 3 + 2] -= dz * f;
      }
    }
    for (const [a, b] of links) {
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

/** The whole layout, start to frozen. What the worker runs. */
export function layout(req: LayoutRequest): Float32Array {
  const { pos, vel } = seedPositions(req.ids);
  relax(req.links, req.ids.length, pos, vel, ITERS);
  return pos;
}

/** Radius of the relaxed cloud — the renderer needs it to decide how far the
 *  camera may drift before a cached edge layer is stale by more than a pixel. */
export function cloudRadius(pos: Float32Array, n: number): number {
  let m = 0;
  for (let i = 0; i < n; i++) {
    const r = Math.hypot(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]);
    if (r > m) m = r;
  }
  return m || 1;
}
