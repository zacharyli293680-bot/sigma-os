/**
 * build.tsx — repo awareness (Phase 6), on the rail's BD slot.
 *
 * The Projects panel already shows git state for hubs that *declare* a `repo:`.
 * What it structurally cannot show is the other direction — a repo with no hub
 * at all — and that is the more useful half, because a project you never wrote
 * a hub for is precisely the one you forget. `team20` was sitting in the code
 * folder untouched for 56 days and appeared nowhere in the vault.
 *
 * Three signals, each paired with a word rather than left to colour alone:
 * no hub, hub stale against its code, and idle.
 */
import { useEffect, useState } from "react";
import { get, obsidianHref, rel } from "./api";
import type { Repos } from "./api";

export default function BuildView({ open, vault, onClose }: {
  open: boolean; vault: string; onClose: () => void;
}) {
  const [d, setD] = useState<Repos | null | undefined>(undefined);

  useEffect(() => {
    if (!open) return;
    setD(undefined);
    get<Repos>("repos").then(setD).catch(() => setD(null));
  }, [open]);

  if (!open) return null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="study" onClick={e => e.stopPropagation()}
           role="dialog" aria-label="Build">
        <header className="study-head">
          <span className="label">◇ BUILD — REPO AWARENESS</span>
          <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
        </header>

        {d === undefined && <p className="dim pad">reading…</p>}
        {d === null && <p className="err pad">backend unreachable</p>}
        {d && !d.root && <p className="dim pad">{d.note ?? "no code folder found"}</p>}

        {d && d.root && (
          <div className="study-body">
            <p className="dim study-note">
              <code>{d.root}</code> — found by looking at where your project hubs
              point, not from a setting.
            </p>

            <h3>Repositories</h3>
            <ul className="repo-list">
              {d.repos.map(r => (
                <li key={r.path}>
                  <span className="repo-name">{r.name}</span>
                  <span className="repo-branch" title={r.branch ?? ""}>{r.branch ?? "?"}</span>
                  <span className="repo-git">
                    {r.dirty == null ? "? unreadable"
                      : r.dirty ? <b className="warn-line">● {r.dirty} dirty</b>
                      : "○ clean"}
                    {r.unpushed ? <b className="warn-line"> · ↑{r.unpushed}</b> : ""}
                  </span>
                  <span className={`repo-idle ${r.idle_days != null && r.idle_days >= d.stale_days ? "warn-line" : "dim"}`}>
                    {r.last_commit ? `${rel(r.last_commit)} ago` : "no commits"}
                  </span>
                  <span className="repo-hub">
                    {r.hub === null
                      ? <b className="warn-line" title="this repo has no hub note in the vault">no hub</b>
                      : (
                        <a href={obsidianHref(vault, `03-Projects/${r.hub}`)}>
                          {r.hub}
                          {(r.hub_stale_days ?? 0) > 0 && (
                            <b className="warn-line"
                               title="the code has commits newer than this hub note">
                              {" "}· stale {r.hub_stale_days}d
                            </b>
                          )}
                        </a>
                      )}
                  </span>
                </li>
              ))}
            </ul>

            <h3>What needs attention</h3>
            <ul className="repo-flags">
              <li>
                <b>{d.orphans.length}</b> repo(s) with no hub note
                {d.orphans.length > 0 && <span className="dim"> — {d.orphans.join(", ")}</span>}
              </li>
              <li>
                <b>{d.stale_hubs.length}</b> hub(s) whose code has moved on since the note did
                {d.stale_hubs.length > 0 && <span className="dim"> — {d.stale_hubs.join(", ")}</span>}
              </li>
              <li>
                <b>{d.idle.length}</b> repo(s) untouched for {d.stale_days}+ days
                {d.idle.length > 0 && <span className="dim"> — {d.idle.join(", ")}</span>}
              </li>
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
