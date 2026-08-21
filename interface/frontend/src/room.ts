/**
 * room.ts — the two rooms, and the one place a layout is written down.
 *
 * The user-facing word is *layout* (the footer prints `layout: enterprise`,
 * the picker says LAYOUT); the code word is *room*, partly because layout.ts
 * is already the brain's force layout, and mostly because the workbench's
 * stylesheet coined it first: a room and a palette are orthogonal
 * (App.css §workbench), stored under separate keys, every combination valid.
 * QUARTZ in the cockpit and VOID in the office are both real.
 *
 * The store mirrors theme.ts's shape — paint/get/set/boot/useRoom — so the
 * picker inherits the same contract: preview live, keep on Enter, revert on
 * Esc. `paint` stamps `data-layout` on :root, and unlike `data-theme` — which
 * is diagnostic only, because no CSS rule may key on a palette — this
 * attribute is *made* to be styled against. Palette forks are banned; layout
 * forks are the point: enterprise.css scopes every rule under
 * [data-layout="enterprise"], so the Instrument's rules never even match.
 */
import { useSyncExternalStore } from "react";

export interface Room {
  id: string;
  name: string;
  note: string;
}

export const ROOMS: Room[] = [
  {
    id: "instrument",
    name: "INSTRUMENT",
    note: "the cockpit — cyan structure on the void, the brain at centre",
  },
  {
    id: "enterprise",
    name: "ENTERPRISE",
    note: "the office — cards on a tinted canvas, the day's work front and centre",
  },
];

export const DEFAULT_ROOM = ROOMS[0];

/* --------------------------------------------------------------- the store */

const KEY = "sigma.layout";

let active: Room = DEFAULT_ROOM;
const listeners = new Set<() => void>();

function paint(r: Room): void {
  document.documentElement.dataset.layout = r.id;
}

export function getRoom(): Room {
  return active;
}

export function roomById(id: string | null): Room | undefined {
  return ROOMS.find(r => r.id === id);
}

/**
 * Switch rooms. `remember: false` is the picker's live preview — arrowing
 * through the list re-mounts the other shell without touching what survives
 * a reload, so backing out with Esc leaves nothing behind to undo.
 */
export function setRoom(r: Room, remember = true): void {
  active = r;
  paint(r);
  if (remember) {
    try { localStorage.setItem(KEY, r.id); } catch { /* private mode — the
      room still applies, it just will not survive the reload */ }
  }
  listeners.forEach(fn => fn());
}

/** Called once from main.tsx beside bootTheme(), before React mounts, so the
 *  first paint is already the right room rather than flashing the cockpit. */
export function bootRoom(): void {
  let saved: string | null = null;
  try { saved = localStorage.getItem(KEY); } catch { /* see above */ }
  setRoom(roomById(saved) ?? DEFAULT_ROOM, false);
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

/** Unlike useTheme — which almost nothing consumes, because palettes repaint
 *  through :root — this one *is* the render branch: App.tsx reads it to decide
 *  which shell to mount. */
export function useRoom(): Room {
  return useSyncExternalStore(subscribe, getRoom, getRoom);
}
