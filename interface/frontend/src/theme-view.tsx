/**
 * theme-view.tsx — the palette picker (Ctrl+,).
 *
 * The command palette's shell, one row per theme. Two decisions worth stating:
 *
 * **It previews live.** Moving the selection repaints the dashboard behind the
 * overlay immediately, because a swatch row cannot tell you what a palette
 * feels like across a hundred rules — the sky, the reactor's bloom and the
 * queue's chips are the actual product, and they are all visible through the
 * backdrop. Enter keeps it, Esc puts back the one you arrived with. Nothing is
 * written to storage until you keep it, so backing out leaves nothing to undo.
 *
 * **It owns its own Escape.** Every other overlay defers to App's Esc ladder,
 * which just closes things. This one has to revert first, so it listens in the
 * capture phase and stops the event before the ladder sees it.
 */
import { useEffect, useRef, useState } from "react";
import { getTheme, setTheme, THEMES } from "./theme";
import type { Theme } from "./theme";

/** The dots, in the order they carry meaning: structure, then the three status
 *  colours, then the seal. Ground and ink come from the chip itself. */
function Swatches({ t }: { t: Theme }) {
  const dot = (token: string, label: string) => (
    <i key={token} style={{ background: t.tokens[token] }} title={label} />
  );
  return (
    <span className="th-swatch" style={{
      background: t.tokens["--void"],
      borderColor: t.tokens["--panel-line"],
      color: t.tokens["--ink"],
    }}>
      <b style={{ color: t.tokens["--accent"] }}>Σ</b>
      {dot("--accent", "accent — structure")}
      {dot("--amber", "amber — attention")}
      {dot("--red", "red — fault")}
      {dot("--add", "green — a diff's inserted line")}
      {dot("--bronze", "bronze — never leaves this machine")}
    </span>
  );
}

export default function ThemeView({ open, onClose }: {
  open: boolean; onClose: () => void;
}) {
  const [sel, setSel] = useState(0);
  // What to put back if this is cancelled. A ref, not state: the key handler
  // below is registered once per open and would close over a stale value.
  const arrivedWith = useRef<Theme>(getTheme());

  useEffect(() => {
    if (!open) return;
    arrivedWith.current = getTheme();
    setSel(Math.max(0, THEMES.findIndex(t => t.id === getTheme().id)));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setSel(s => {
          const next = e.key === "ArrowDown"
            ? Math.min(s + 1, THEMES.length - 1)
            : Math.max(s - 1, 0);
          setTheme(THEMES[next], false);          // preview, not a decision
          return next;
        });
      } else if (e.key === "Enter") {
        e.preventDefault();
        setTheme(getTheme(), true);               // keep whatever is on screen
        onClose();
      } else if (e.key === "Escape") {
        // Ahead of App's ladder, which would close without putting anything
        // back — hence the capture phase and the stop below.
        e.preventDefault();
        e.stopPropagation();
        setTheme(arrivedWith.current, false);
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;

  const keep = (t: Theme) => { setTheme(t, true); onClose(); };
  const cancel = () => { setTheme(arrivedWith.current, false); onClose(); };

  return (
    <div className="palette-backdrop" onClick={cancel}>
      <div className="palette themes" onClick={e => e.stopPropagation()}>
        <div className="themes-head">
          <span className="label">PALETTE</span>
          <span className="dim">the dashboard behind this repaints as you move</span>
        </div>
        <ul>
          {THEMES.map((t, i) => (
            <li key={t.id}
                className={i === sel ? "sel" : ""}
                onMouseEnter={() => { setSel(i); setTheme(t, false); }}
                onClick={() => keep(t)}>
              <span className="th-main">
                <span className="p-title">{t.name}</span>
                <span className="th-note">{t.note}</span>
              </span>
              <span className="p-meta">
                {t.id === arrivedWith.current.id && <em className="p-badge model">current</em>}
                <Swatches t={t} />
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
