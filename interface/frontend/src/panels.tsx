/**
 * The Phase 0 panels — static, populated with real data (dashboard-plan §10).
 *
 * Status is never colour alone: every state pairs a glyph or word with its
 * colour, and text wears ink tokens rather than accent colours. The window
 * meter renders its own ignorance honestly (§8 — no data source yet).
 */
import { obsidianHref, rel } from "./api";
import type { Fleet, Health, Project, Proposals, Tasks, Window_ } from "./api";

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
  const alerts = health?.findings.filter(f => f.level !== "ok") ?? [];
  const state = health === undefined ? ["◌", "checking…", "down"]
    : health === null ? ["○", "offline", "down"]
    : health.ok ? ["◉", "all clear", "ok"]
    : ["◬", `${alerts.length} to review`, "warn"];
  return (
    <header className="strip">
      <span className="brand"><span className="sigma">Σ</span> SIGMA</span>
      <button className={`health ${state[2]}`} onClick={onHealthClick}
              title={health ? alerts.map(a => `${a.what}${a.fix ? ` — ${a.fix}` : ""}`).join("\n") || "all six checks pass" : "backend unreachable"}>
        <span className="glyph">{state[0]}</span> {state[1]}
      </button>
      <span className="meter" title={win?.note ?? "window meter"}>
        <span className="meter-bar unknown" aria-hidden="true" />
        window {win?.known && win.percent != null ? `${win.percent}%` : "— unknown"}
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
  ["BR", "Brain — Phase 2", false],
  ["AG", "Agents — Phase 1", false],
  ["WK", "Work — Phase 6", false],
  ["ST", "Study — Phase 6", false],
  ["BD", "Build — Phase 6", false],
  ["CR", "Career — Phase 6", false],
  ["SY", "System — Phase 6", false],
];

export function Rail() {
  return (
    <nav className="rail">
      {RAIL.map(([k, title, live]) => (
        <button key={k} className={live ? "active" : ""} disabled={!live} title={title}>
          ◇ {k}
        </button>
      ))}
      <div className="rail-gap" />
      <button className="seal" disabled title="Sealed lane — Phase 5">▦ SEAL</button>
    </nav>
  );
}

// ---------------------------------------------------------------- centre

export function FleetCenter({ fleet }: { fleet: Fleet | null }) {
  if (!fleet) return <Panel label="FLEET" className="center"><p className="dim">loading…</p></Panel>;
  const due = fleet.specialists.filter(s => s.due);
  const broken = fleet.specialists.filter(s => s.last_result && s.last_result !== "ok");
  const word = fleet.stopped_early_at ? "STOPPED EARLY"
    : broken.length ? "FAULT" : due.length ? "DUE" : "IDLE";
  const tone = fleet.stopped_early_at || broken.length ? "warn" : due.length ? "due" : "idle";
  return (
    <Panel label="FLEET" className="center">
      <div className="center-body">
        <div className={`center-word ${tone}`}>{word}</div>
        <div className="center-sub">
          {fleet.specialists.length} specialists · last run {rel(fleet.last_run)} ago
          {fleet.task_installed ? " · scheduled daily 09:00" : " · NOT SCHEDULED"}
        </div>
        <div className="center-row">
          {fleet.specialists.map(s => (
            <span key={s.key} className="spec" title={`${s.title} — ${s.model}, ${s.cadence}`}>
              {s.key} {s.last_result === "ok" ? "✓" : s.last_result ? "✗" : "·"} {rel(s.last_ok)}
              {s.due && <em className="due-tag"> due</em>}
            </span>
          ))}
        </div>
        {fleet.stopped_early_at && (
          <p className="warn-line">last run stopped early on a rate limit ({rel(fleet.stopped_early_at)} ago)</p>
        )}
        <p className="dim center-note">reactor visual lands in Phase 1 — this is the plain summary</p>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- right column

export function WaitingPanel({ proposals, vault }: { proposals: Proposals | null; vault: string }) {
  if (!proposals) return <Panel label="WAITING ON YOU"><p className="dim">loading…</p></Panel>;
  const rows = [
    ...proposals.pending.map(p => ({ ...p, badge: "pending", hint: "review, then set status: approved" })),
    ...proposals.approved.map(p => ({ ...p, badge: "approved", hint: "sigma reflect apply" })),
    ...proposals.staged.map(p => ({ ...p, badge: "staged", hint: "sigma reflect diff → merge" })),
  ];
  return (
    <Panel label="WAITING ON YOU">
      {rows.length === 0 ? (
        <p className="allclear">✓ nothing is waiting on you</p>
      ) : (
        <ul className="rows">
          {rows.map(p => (
            <li key={p.file}>
              <a href={obsidianHref(vault, `06-System/proposals/${p.file.replace(/\.md$/, "")}`)}
                 title={`${p.kind ?? "?"} → ${p.target ?? "?"}\n${p.hint}`}>
                <em className={`badge ${p.badge}`}>{p.badge}</em> {p.title}
              </a>
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

export function TodayPanel({ tasks, vault }: { tasks: Tasks | null; vault: string }) {
  if (!tasks) return <Panel label="TODAY"><p className="dim">loading…</p></Panel>;
  const soon = tasks.tasks.filter(t => t.overdue || t.due <= tasks.today);
  const upcoming = tasks.tasks.filter(t => !soon.includes(t)).slice(0, 6);
  const hidden = tasks.tasks.length - soon.length - upcoming.length;
  const row = (t: Tasks["tasks"][number]) => (
    <li key={`${t.file}:${t.line}`} className={t.overdue ? "overdue" : ""}>
      <a href={obsidianHref(vault, t.file.replace(/\.md$/, ""))}
         title={`${t.file}:${t.line} — checkbox ticking arrives in Phase 4`}>
        <span className="box">☐</span>
        <span className="due-date">{t.due.slice(5)}{t.overdue ? " !" : ""}</span> {t.text}
      </a>
    </li>
  );
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
              <span className="name">{p.name}</span>
              {p.git ? (
                <span className={`repo ${p.git.dirty ? "dirty" : ""}`}>
                  {p.git.dirty ? `● ${p.git.dirty} dirty` : "○ clean"}
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

export function FleetDetail({ fleet }: { fleet: Fleet | null }) {
  if (!fleet) return <Panel label="SPECIALISTS"><p className="dim">loading…</p></Panel>;
  return (
    <Panel label="SPECIALISTS">
      <table className="fleet-table">
        <thead>
          <tr><th>who</th><th>cadence</th><th>model</th><th>last ok</th><th>raised</th><th></th></tr>
        </thead>
        <tbody>
          {fleet.specialists.map(s => (
            <tr key={s.key} className={s.last_result && s.last_result !== "ok" ? "broken" : ""}>
              <td>{s.key}</td>
              <td>{s.cadence}</td>
              <td>{s.model}</td>
              <td title={s.last_ok ?? "never"}>{rel(s.last_ok)}</td>
              <td>{s.last_proposals ?? "—"}</td>
              <td>{s.due ? <em className="due-tag">due</em>
                : s.last_result === "ok" ? "✓" : s.last_result ? `✗ ${s.last_result}` : "·"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

// ---------------------------------------------------------------- foot

export function Foot() {
  return (
    <footer className="foot">
      <span><kbd>Ctrl</kbd>+<kbd>/</kbd> chat · <kbd>Esc</kbd> overview</span>
      <span className="dim">activity dock — Phase 1</span>
    </footer>
  );
}
