/**
 * The Phase 0 panels — static, populated with real data (dashboard-plan §10).
 *
 * Status is never colour alone: every state pairs a glyph or word with its
 * colour, and text wears ink tokens rather than accent colours. The window
 * meter renders its own ignorance honestly (§8 — no data source yet).
 */
import { useState } from "react";
import { ApiError, obsidianHref, post, rel } from "./api";
import type { Health, Project, Proposals, Tasks, VaultTask, Window_ } from "./api";

export function Panel({ label, children, className = "" }: {
  label: string; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <h2>{label}</h2>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- top strip

export function TopStrip({ health, window: win, block, clock, onHealthClick }: {
  health: Health | null | undefined; window: Window_ | null; block: string | null;
  clock: string; onHealthClick: () => void;
}) {
  // undefined = the doctor is still running (it can take ~30s when the auth
  // probe fires); null = the fetch actually failed. Different words for
  // different facts — "offline" while merely checking would be crying wolf.
  // "info" findings (standing facts like the model-boundary exemptions) are
  // shown in the tooltip but never counted as needing review.
  const alerts = health?.findings.filter(f => f.level === "alert" || f.level === "todo") ?? [];
  const state = health === undefined ? ["◌", "checking…", "down"]
    : health === null ? ["○", "offline", "down"]
    : health.ok ? ["◉", "all clear", "ok"]
    : ["◬", `${alerts.length} to review`, "warn"];
  const tooltip = health === undefined ? "running the health checks…"
    : health === null ? "backend unreachable"
    : health.findings.filter(f => f.level !== "ok")
        .map(a => `${a.what}${a.fix ? ` — ${a.fix}` : ""}`).join("\n") || "all checks pass";
  return (
    <header className="strip">
      <span className="brand"><span className="sigma">Σ</span> SIGMA</span>
      <button className={`health ${state[2]}`} onClick={onHealthClick}
              title={tooltip}>
        <span className="glyph">{state[0]}</span> {state[1]}
      </button>
      <span className={`meter ${win?.paused || win?.last_rate_limit ? "warn" : ""}`}
            title={[win?.note,
                    win?.cost_usd != null ? `~$${win.cost_usd} notional this window` : null,
                    win?.reserved ? `reserved: ${win.reserved}` : null]
                   .filter(Boolean).join("\n") || "window meter"}>
        {!win ? "window —"
          : win.paused ? `window PAUSED${win.resume_at ? ` · resumes ~${win.resume_at.slice(11, 16)}` : ""}`
          : win.last_rate_limit ? (rel(win.last_rate_limit) === "now"
              ? "window limited · just hit"
              : `window limited · hit ${rel(win.last_rate_limit)} ago`)
          : win.known === "proxy" ? `window ok · ${win.calls ?? 0} calls/5h`
          : "window — unknown"}
      </span>
      <span className="block" title="from today's daily note">
        {block ?? "no daily note yet"}
      </span>
      <span className="clock">{clock}</span>
    </header>
  );
}

// ---------------------------------------------------------------- left rail

const RAIL: [string, string, boolean][] = [
  ["OV", "Overview", true],
  ["BR", "Brain (Ctrl+G)", true],
  ["AG", "Agents — Phase 1", false],
  ["WK", "Work — Phase 6", false],
  ["ST", "Study — exam mode", true],
  ["BD", "Build — repo awareness", true],
  ["CR", "Career — Phase 6", false],
  ["SY", "System — Phase 6", false],
];

export function Rail({ brainOpen, onBrain, noSyncOpen, onNoSync, noSyncCount,
                      studyOpen, onStudy, buildOpen, onBuild }: {
  brainOpen: boolean; onBrain: () => void;
  noSyncOpen: boolean; onNoSync: () => void; noSyncCount: number | null;
  studyOpen: boolean; onStudy: () => void;
  buildOpen: boolean; onBuild: () => void;
}) {
  return (
    <nav className="rail">
      {RAIL.map(([k, title, live]) => {
        const active = k === "BR" ? brainOpen : k === "ST" ? studyOpen
          : k === "BD" ? buildOpen
          : k === "OV" ? !brainOpen && !studyOpen && !buildOpen : false;
        const go = k === "BR" ? onBrain : k === "ST" ? onStudy : k === "BD" ? onBuild
          : k === "OV" && brainOpen ? onBrain : undefined;
        return (
          <button key={k} className={active ? "active" : ""} disabled={!live}
                  title={title} onClick={go}>
            ◇ {k}
          </button>
        );
      })}
      <div className="rail-gap" />
      {/* Phase 5. Live now, and a lens rather than a room: everything it lists
          also appears elsewhere, marked. What it adds is the total. */}
      <button className={`seal ${noSyncOpen ? "active" : ""}`} onClick={onNoSync}
              title={`No-sync — everything that never leaves this machine (Ctrl+.)${
                noSyncCount ? `\n${noSyncCount} files, on one disk only` : ""}`}>
        ⊘ SEAL{noSyncCount ? <span className="seal-n">{noSyncCount}</span> : null}
      </button>
    </nav>
  );
}

/** The mark itself. One glyph, one colour, one meaning — and the same tooltip
 *  everywhere it appears, because a marker that explains itself differently in
 *  two panels teaches two different boundaries.
 *
 *  **⊘, not ▦.** The first draft used ▦, which live rendering immediately
 *  disproved: `🏁` (U+1F3C1, on every milestone task in the study timelines)
 *  has no glyph in this font stack and falls back to a hatched box almost
 *  identical to it. A confidentiality marker that random tofu can imitate is
 *  worse than none, and telling them apart by colour alone would break the
 *  dashboard's own rule that status is never colour alone. ⊘ also rhymes with
 *  the bronze ring the brain draws for the same fact. */
export function NoSyncMark() {
  return (
    <span className="nosync-mark"
          title="never leaves this machine — gitignored, so it has no off-machine backup">
      ⊘
    </span>
  );
}

// The centre panel is the reactor — see reactor.tsx (D1 replaced the Phase 0
// plain summary that used to live here).

// ---------------------------------------------------------------- right column

// No `vault` any more: the rows stopped being Obsidian links when they became
// the door to the diff, and the review overlay takes the vault name itself.
export function WaitingPanel({ proposals, onReview }: {
  proposals: Proposals | null; onReview: (name: string) => void;
}) {
  if (!proposals) return <Panel label="WAITING ON YOU"><p className="dim">loading…</p></Panel>;
  const rows = [
    ...proposals.pending.map(p => ({ ...p, badge: "pending", hint: "review, then set status: approved" })),
    ...proposals.approved.map(p => ({ ...p, badge: "approved", hint: "sigma reflect apply" })),
    ...proposals.staged.map(p => ({ ...p, badge: "staged", hint: "sigma reflect diff → merge" })),
  ];
  // The one panel that keeps a frame: a bordered box means something is on you.
  return (
    <Panel label="WAITING ON YOU" className={rows.length ? "attn" : ""}>
      {rows.length === 0 ? (
        <p className="allclear">✓ nothing is waiting on you</p>
      ) : (
        <ul className="rows">
          {rows.map(p => (
            // The row is now the door to the diff, not to Obsidian. Reviewing
            // a proposal was the one loop that still required a terminal.
            <li key={p.file}>
              <button className="waiting-row"
                      onClick={() => onReview(p.file.replace(/\.md$/, ""))}
                      title={`${p.kind ?? "?"} → ${p.target ?? "?"}\nreview the diff and decide`}>
                <em className={`badge ${p.badge}`}>{p.badge}</em> {p.title}
              </button>
            </li>
          ))}
        </ul>
      )}
      {proposals.applied_recent.length > 0 && rows.length === 0 && (
        <p className="dim">last applied: {proposals.applied_recent[0].title}</p>
      )}
    </Panel>
  );
}

export function TodayPanel({ tasks, vault, onMutate }: {
  tasks: Tasks | null; vault: string; onMutate: () => void;
}) {
  // Optimistic, with rollback: ☐ → ◌ while the write is in flight, ☑ on
  // success (the refetch then drops the row — done tasks live in the notes,
  // not here), back to ☐ with the reason on failure. "stale" also refetches:
  // the note moved underneath the panel, so the panel is what must change.
  const [state, setState] = useState<Record<string, "busy" | "done">>({});
  const [errs, setErrs] = useState<Record<string, string>>({});
  if (!tasks) return <Panel label="TODAY"><p className="dim">loading…</p></Panel>;
  const keyOf = (t: VaultTask) => `${t.file}:${t.line}`;

  async function tick(t: VaultTask) {
    const k = keyOf(t);
    if (state[k]) return;
    setState(s => ({ ...s, [k]: "busy" }));
    setErrs(({ [k]: _drop, ...rest }) => rest);
    try {
      await post("tasks/toggle", { file: t.file, line: t.line, raw: t.raw, done: true });
      setState(s => ({ ...s, [k]: "done" }));
      onMutate();
    } catch (e) {
      setState(({ [k]: _drop, ...rest }) => rest);
      const msg = e instanceof ApiError
        ? (e.code === "stale" ? "the note changed — list refreshed" : e.detail || e.code)
        : "backend unreachable";
      setErrs(prev => ({ ...prev, [k]: msg }));
      if (e instanceof ApiError && e.code === "stale") onMutate();
    }
  }

  const soon = tasks.tasks.filter(t => t.overdue || t.due <= tasks.today);
  const upcoming = tasks.tasks.filter(t => !soon.includes(t)).slice(0, 6);
  const hidden = tasks.tasks.length - soon.length - upcoming.length;
  const row = (t: VaultTask) => {
    const k = keyOf(t);
    const st = state[k];
    return (
      <li key={k} className={`task-row ${t.overdue ? "overdue" : ""}`}>
        <button className={`box ${st ?? ""}`} disabled={!!st} onClick={() => tick(t)}
                title={st === "done"
                  ? "ticked — one commit of its own, revertible in the ledger (Ctrl+J)"
                  : "tick it — writes to the note as its own revertible commit"}>
          {st === "done" ? "☑" : st === "busy" ? "◌" : "☐"}
        </button>
        <a href={obsidianHref(vault, t.file.replace(/\.md$/, ""))}
           title={`${t.file}:${t.line}`}>
          <span className="due-date">{t.due.slice(5)}{t.overdue ? " !" : ""}</span>
          {t.no_sync && <NoSyncMark />} {t.text}
        </a>
        {errs[k] && <span className="row-err">{errs[k]}</span>}
      </li>
    );
  };
  return (
    <Panel label="TODAY" className="today">
      {soon.length === 0 && <p className="allclear">✓ nothing due today</p>}
      <ul className="rows">{soon.map(row)}</ul>
      {upcoming.length > 0 && <p className="dim sub">next up</p>}
      <ul className="rows dim-rows">{upcoming.map(row)}</ul>
      {hidden > 0 && <p className="dim">+{hidden} more dated tasks in the vault</p>}
    </Panel>
  );
}

// ---------------------------------------------------------------- lower row

export function ProjectsPanel({ projects, vault }: { projects: Project[] | null; vault: string }) {
  if (!projects) return <Panel label="PROJECTS"><p className="dim">loading…</p></Panel>;
  return (
    <Panel label="PROJECTS">
      <ul className="rows">
        {projects.map(p => (
          <li key={p.name} className="project">
            <a href={obsidianHref(vault, `03-Projects/${p.name}`)} title={p.repo ?? "no repo"}>
              <span className="name">{p.no_sync && <NoSyncMark />}{p.name}</span>
              {p.git ? (
                <span className={`repo ${p.git.dirty ? "dirty" : ""}`}>
                  {p.git.dirty == null ? "? unreadable"      /* git failed ≠ clean */
                    : p.git.dirty ? `● ${p.git.dirty} dirty` : "○ clean"}
                  {p.git.unpushed ? ` · ↑${p.git.unpushed}` : ""}
                  {" · "}{rel(p.git.last_commit)}
                </span>
              ) : (
                <span className="repo dim">no repo</span>
              )}
            </a>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

// The SPECIALISTS table lived here until it was folded into the reactor: the
// arcs already said who and how-healthy, so the panel restated half its own
// subject. Hovering or focusing an arc now shows that specialist's full row.

// ---------------------------------------------------------------- foot

export function Foot({ activity, onChat, onBrain, onPalette, onLedger, onNoSync,
                      onCapture }: {
  activity: { text: string; live: boolean };
  onChat: () => void; onBrain: () => void; onPalette: () => void;
  onLedger: () => void; onNoSync: () => void; onCapture: () => void;
}) {
  // The hints are also the buttons — Chrome sometimes eats Ctrl+G/Ctrl+K, so
  // every keystroke has a clickable twin. The dock is a door too: clicking
  // what-is-happening opens the full ledger of what happened.
  return (
    <footer className="foot">
      <span className="foot-keys">
        <button onClick={onPalette}><kbd>Ctrl</kbd>+<kbd>K</kbd> palette</button> ·{" "}
        <button onClick={onChat}><kbd>Ctrl</kbd>+<kbd>/</kbd> chat</button> ·{" "}
        <button onClick={onBrain}><kbd>Ctrl</kbd>+<kbd>G</kbd> brain</button> ·{" "}
        <button onClick={onLedger}><kbd>Ctrl</kbd>+<kbd>J</kbd> ledger</button> ·{" "}
        <button onClick={onNoSync}><kbd>Ctrl</kbd>+<kbd>.</kbd> no-sync</button> ·{" "}
        <button onClick={onCapture}><kbd>Ctrl</kbd>+<kbd>N</kbd> capture</button> ·{" "}
        <kbd>Esc</kbd> back
      </span>
      <button className={`dock ${activity.live ? "live" : "dim"}`} onClick={onLedger}
              title="open the activity ledger (Ctrl+J)">{activity.text}</button>
    </footer>
  );
}
