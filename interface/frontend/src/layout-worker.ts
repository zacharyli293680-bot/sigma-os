/**
 * The layout, off the main thread.
 *
 * At 476 notes the 220-iteration force layout is ~650ms of straight-line
 * arithmetic, and it is quadratic in notes: ~2.9s at 1000, ~11.5s at 2000. It
 * used to run on the main thread in 8ms slices, which kept any single frame
 * short but occupied the thread for roughly two thirds of a second at exactly
 * the moment the dashboard paints for the first time. A worker does not make
 * it faster; it makes it somebody else's thread, which is the only fix that
 * keeps working as the vault grows.
 *
 * The result is transferred rather than copied — the buffer is handed over and
 * this side loses it, which is correct since the worker is done with it.
 */
import { layout } from "./layout";
import type { LayoutRequest } from "./layout";

/* `self` is typed as a Window here: tsconfig.app.json's lib is ["ES2023",
   "DOM"] and adding "WebWorker" to it would conflict with DOM across the rest
   of the app. Narrowing to the two members actually used is cheaper than
   splitting the tsconfig for one file. */
const ctx = self as unknown as {
  onmessage: ((e: MessageEvent<LayoutRequest>) => void) | null;
  postMessage: (msg: unknown, transfer?: Transferable[]) => void;
};

ctx.onmessage = (e) => {
  const pos = layout(e.data);
  ctx.postMessage(pos, [pos.buffer]);
};
