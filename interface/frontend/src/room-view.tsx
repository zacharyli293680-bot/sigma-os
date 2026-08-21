/**
 * room-view.tsx — the layout picker (Ctrl+Shift+,).
 *
 * theme-view's contract, one row per room: moving the selection re-mounts the
 * other shell behind the overlay immediately, because a description cannot
 * tell you what a layout feels like — the actual furniture can. Enter keeps
 * it, Esc puts back the room you arrived in, and nothing is written to
 * storage until you keep it.
 *
 * Like the theme picker it owns its own Escape in the capture phase: App's
 * ladder just closes things, and this one has to revert first.
 */
import { useEffect, useRef, useState } from "react";
import { getRoom, ROOMS, setRoom } from "./room";
import type { Room } from "./room";

export default function RoomView({ open, onClose }: {
  open: boolean; onClose: () => void;
}) {
  const [sel, setSel] = useState(0);
  // What to put back if this is cancelled. A ref, not state: the key handler
  // below is registered once per open and would close over a stale value.
  const arrivedWith = useRef<Room>(getRoom());

  useEffect(() => {
    if (!open) return;
    arrivedWith.current = getRoom();
    setSel(Math.max(0, ROOMS.findIndex(r => r.id === getRoom().id)));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setSel(s => {
          const next = e.key === "ArrowDown"
            ? Math.min(s + 1, ROOMS.length - 1)
            : Math.max(s - 1, 0);
          setRoom(ROOMS[next], false);            // preview, not a decision
          return next;
        });
      } else if (e.key === "Enter") {
        e.preventDefault();
        setRoom(getRoom(), true);                 // keep whatever is mounted
        onClose();
      } else if (e.key === "Escape") {
        // Ahead of App's ladder, which would close without putting the room
        // back — hence the capture phase and the stop below.
        e.preventDefault();
        e.stopPropagation();
        setRoom(arrivedWith.current, false);
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  const keep = (r: Room) => { setRoom(r, true); onClose(); };
  const cancel = () => { setRoom(arrivedWith.current, false); onClose(); };

  return (
    <div className="palette-backdrop" onClick={cancel}>
      <div className="palette themes" onClick={e => e.stopPropagation()}>
        <div className="themes-head">
          <span className="label">LAYOUT</span>
          <span className="dim">the room, not the palette — the shell behind this re-mounts as you move</span>
        </div>
        <ul>
          {ROOMS.map((r, i) => (
            <li key={r.id}
                className={i === sel ? "sel" : ""}
                onMouseEnter={() => { setSel(i); setRoom(r, false); }}
                onClick={() => keep(r)}>
              <span className="th-main">
                <span className="p-title">{r.name}</span>
                <span className="th-note">{r.note}</span>
              </span>
              <span className="p-meta">
                {r.id === arrivedWith.current.id && <em className="p-badge model">current</em>}
                {/* One glyph each, not a swatch strip: a room has no colours of
                    its own to preview — the live re-mount behind is the preview. */}
                <span className="room-glyph">{r.id === "instrument" ? "◉" : "▦"}</span>
              </span>
            </li>
          ))}
        </ul>
        <div className="palette-foot dim">
          ↑↓ preview · Enter keep · Esc back to {arrivedWith.current.name} — the
          choice survives a reload
        </div>
      </div>
    </div>
  );
}
