/**
 * enterprise.tsx — the office (the ENTERPRISE room).
 *
 * The design the approved mockup froze (interface/mockups/enterprise-layout
 * .html): glass chrome over a tinted canvas, flat cards, the day's work front
 * and centre, no brain — the graph lives in the Instrument, and the office
 * gets the machine's *facts* instead (the SYSTEM strip, the footer dock).
 *
 * Nothing here fetches. App.tsx keeps every byte of data and state and hands
 * them down, so switching rooms mid-run costs nothing and the two shells can
 * never disagree about what is true. Every overlay is shared with the
 * Instrument — same components, same Esc ladder — restyled by enterprise.css
 * where the notch would clash with the office.
 *
 * Rules carried over whole from the design: status is never colour alone;
 * the accent is spent five ways (brand Σ, the Ask capsule, the active-nav
 * dot, soon badges, the focus ring) and no more; active fills are ink-on-void
 * so they invert with the palette; sans is what a human wrote, mono is what
 * a machine produced.
 */
import { useState } from "react";
import { GLYPH, todayModel, until, minutes } from "./agenda-rail";
import { obsidianHref, QUEUE_ORDER, rel } from "./api";
import type { Agenda, Fleet, Health, Progress, Proposals, Queue,
              Tasks, Window_ } from "./api";
import { NoSyncMark } from "./panels";
import { breakdown, chipsFor, useFreshIds, useQueueTick } from "./queue-bits";
import { useElapsed } from "./reactor";

/** The sidebar, in words. The rail's two-letter codes are cockpit shorthand;
 *  an office labels its doors. Same slots, same soon set, same SEAL. */
const NAV: [key: string, label: string, state: "live" | "soon"][] = [
  ["OV", "Overview", "live"],
  ["WK", "Work", "live"],
  ["CA", "Calendar", "live"],
  ["ST", "Study", "live"],
  ["BD", "Build", "live"],
  ["AG", "Agents", "soon"],
  ["CR", "Career", "soon"],
  ["SY", "System", "soon"],
];

function greeting(now: Date): string {
  const h = now.getHours();
  return h < 5 ? "Up late, Zach." : h < 12 ? "Good morning, Zach."
    : h < 18 ? "Good afternoon, Zach." : "Good evening, Zach.";
}

export interface EnterpriseProps {
  health: Health | null | undefined;
  window: Window_ | null;
  tasks: Tasks | null;
  agenda: Agenda | null;
  queue: Queue | null;
  proposals: Proposals | null;
  fleet: Fleet | null;
  progress: Progress | null;
  noSyncCount: number | null;
  vault: string;
  clock: string;
  activity: { text: string; live: boolean };
  themeName: string;
  roomName: string;
  open: { work: boolean; agenda: boolean; study: boolean; build: boolean;
          nosync: boolean; soon: string | null };
  onHealth: () => void;
  onTheme: () => void;
  onRoom: () => void;
  onPalette: () => void;
  onCapture: () => void;
  onChat: () => void;
  onLedger: () => void;
  onWork: () => void;
  onAgenda: () => void;
  onStudy: () => void;
  onBuild: () => void;
  onSoon: (slot: string) => void;
  onNoSync: () => void;
  onReview: (name: string) => void;
  onMutate: () => void;
}

export default function EnterpriseShell(p: EnterpriseProps) {
  const nothingOver = !p.open.work && !p.open.agenda && !p.open.study
    && !p.open.build && !p.open.nosync && p.open.soon === null;

  // The same health vocabulary as the strip — different dress, same words,
  // because two shells disagreeing about "all clear" would be the worst bug
  // this split could ship.
  const alerts = p.health?.findings.filter(f => f.level === "alert" || f.level === "todo") ?? [];
  const hState = p.health === undefined ? ["◌", "checking…", "down"]
    : p.health === null ? ["○", "offline", "down"]
    : p.health.ok ? ["◉", "all clear", "ok"]
    : ["◬", `${alerts.length} to review`, "warn"];
  const hTip = p.health === undefined ? "running the health checks…"
    : p.health === null ? "backend unreachable"
    : p.health.findings.filter(f => f.level !== "ok")
        .map(a => `${a.what}${a.fix ? ` — ${a.fix}` : ""}`).join("\n") || "all checks pass";

  const day = todayModel(p.agenda ?? { occurrences: [], problems: [] } as unknown as Agenda, 5);
  const today = p.tasks?.today ?? day.today;

  // KPI arithmetic. Every number arrives with its noun, and none is invented:
  // due/overdue from /api/tasks, waiting from /api/proposals, free hours from
  // the rail's own model, the window verbatim from /api/window.
  const dueToday = p.tasks ? p.tasks.tasks.filter(t => t.due === today && !t.overdue).length : null;
  const overdue = p.tasks ? p.tasks.tasks.filter(t => t.overdue).length : null;
  const lateDays = p.tasks
    ? Math.max(0, ...p.tasks.tasks.filter(t => t.overdue)
        .map(t => Math.round((Date.parse(today) - Date.parse(t.due)) / 86_400_000)))
    : 0;
  const waitingRows = p.proposals
    ? [...p.proposals.pending.map(x => ({ ...x, badge: "pending" })),
       ...p.proposals.approved.map(x => ({ ...x, badge: "approved" })),
       ...p.proposals.staged.map(x => ({ ...x, badge: "staged" }))]
    : null;
  const win = p.window;
  const winWord = !win ? "—"
    : win.paused ? "paused"
    : win.last_rate_limit ? "limited"
    : win.known === "proxy" ? "ok" : "—";

  const runElapsed = useElapsed(p.progress?.state === "running" ? p.progress.current_started : null);

  const dateLine = new Date().toLocaleDateString(undefined,
    { weekday: "long", month: "long", day: "numeric" });

  return (
    <div className="ent">
      <header className="ent-topbar">
        <span className="ent-brand"><span className="ent-sigil">Σ</span> Sigma</span>
        <button className="ent-search" onClick={p.onPalette}
                title="search or run a command (Ctrl+K)">
          <span className="ent-search-glyph">⌕</span> Search or command…
          <kbd className="ent-kbd">Ctrl K</kbd>
        </button>
        <span className="ent-spacer" />
        <button className={`ent-status ${hState[2]}`} onClick={p.onHealth} title={hTip}>
          <span className="ent-status-dot">{hState[0]}</span> {hState[1]}
        </button>
        <button className="ent-tb" onClick={p.onCapture} title="capture to the inbox (Ctrl+N)">
          ＋ Capture
        </button>
        <button className="ent-tb" onClick={p.onTheme} title="switch the palette (Ctrl+,)">
          ◐ <span className="ent-readout">{p.themeName}</span>
        </button>
        <button className="ent-tb" onClick={p.onRoom} title="switch the layout (Ctrl+Shift+,)">
          ▦ <span className="ent-readout">{p.roomName}</span>
        </button>
        <span className="ent-clock">{p.clock}</span>
      </header>

      <nav className="ent-side">
        <div className="ent-nav">
          {NAV.map(([k, label, state]) => {
            const soon = state === "soon";
            const active = k === "OV" ? nothingOver
              : k === "WK" ? p.open.work : k === "CA" ? p.open.agenda
              : k === "ST" ? p.open.study : k === "BD" ? p.open.build
              : p.open.soon === k;
            const go = k === "OV" ? undefined
              : k === "WK" ? p.onWork : k === "CA" ? p.onAgenda
              : k === "ST" ? p.onStudy : k === "BD" ? p.onBuild
              : () => p.onSoon(k);
            return (
              <button key={k} onClick={go}
                      className={`ent-nav-item ${active ? "active" : ""} ${soon ? "soon" : ""}`}>
                {label}
                {k === "WK" && p.queue && (
                  <span className="ent-nav-n">{p.queue.counts.visible}</span>
                )}
                {soon && <span className="ent-soon">soon</span>}
              </button>
            );
          })}
        </div>
        <div className="ent-side-push" />
        <button className={`ent-sealed ${p.open.nosync ? "active" : ""}`} onClick={p.onNoSync}
                title="No-sync — everything that never leaves this machine (Ctrl+.)">
          ⊘ Sealed{p.noSyncCount ? <span className="ent-nav-n">{p.noSyncCount}</span> : null}
        </button>
        <div className="ent-side-foot">
          vault: {p.vault || "—"}<br />layout: enterprise
        </div>
      </nav>

      <main className="ent-main">
        <div className="ent-col">

          <section className="ent-greeting">
            <h1>{greeting(day.now)}</h1>
            <div className="ent-sub">
              {dateLine}
              {p.tasks?.block && <> · <span className="ent-block" title="from today's daily note">{p.tasks.block}</span></>}
            </div>
          </section>

          <section className="ent-kpis" aria-label="today at a glance">
            <div className="ent-kpi">
              <div className="ent-eyebrow">Due today</div>
              <div className="ent-num">{dueToday ?? "—"}</div>
              <div className="ent-qual">tasks</div>
            </div>
            <div className="ent-kpi">
              <div className="ent-eyebrow">Overdue</div>
              <div className="ent-num">{overdue ?? "—"}</div>
              <div className={`ent-qual ${overdue ? "alert" : ""}`}>
                {overdue == null ? "…" : overdue === 0 ? "none late"
                  : `⚠ up to ${lateDays}d late`}
              </div>
            </div>
            <div className="ent-kpi">
              <div className="ent-eyebrow">Waiting on you</div>
              <div className="ent-num">{waitingRows ? waitingRows.length : "—"}</div>
              <div className="ent-qual">proposals</div>
            </div>
            <div className="ent-kpi">
              <div className="ent-eyebrow">Free today</div>
              <div className="ent-num">{p.agenda ? `${day.freeLeft.toFixed(1)}h` : "—"}</div>
              <div className="ent-qual">of the {day.dayStart}:00–{day.dayEnd}:00 day</div>
            </div>
            <div className="ent-kpi">
              <div className="ent-eyebrow">Window</div>
              <div className="ent-num">{winWord}</div>
              <div className="ent-qual" title={win?.note ?? undefined}>
                {!win ? "…"
                  : win.paused ? <span className="m">resumes {win.resume_at?.slice(11, 16) ?? "—"}</span>
                  : win.last_rate_limit ? <span className="m">hit {rel(win.last_rate_limit)} ago</span>
                  : win.known === "proxy" ? <span className="m">{win.calls ?? 0} calls/5h</span>
                  : "no data source"}
              </div>
            </div>
          </section>

          {/* The one flexing region: everything above and below it is fixed
              height, so the overview always fits the viewport — a card that
              outgrows its cell clamps behind an expander instead of pushing
              the page into scroll. Projects has no card here: the git facts
              live one click away behind Build, and the overview's job is the
              day, not the repos. */}
          <section className="ent-grid">
            <EntQueueCard queue={p.queue} vault={p.vault}
                          onMutate={p.onMutate} onOpen={p.onWork} />
            <div className="ent-rightcol">
              <EntTodayCard agenda={p.agenda} vault={p.vault}
                            onOpen={p.onAgenda} onStudy={p.onStudy} />
              <EntWaitingCard rows={waitingRows} onReview={p.onReview} />
            </div>
          </section>

          <section className="ent-system" aria-label="the fleet">
            <span className="ent-sys-lbl">SYSTEM</span>
            {!p.fleet ? <span className="ent-sys-cell">loading…</span>
              : p.fleet.specialists.map((s, i) => (
                <span key={s.key} className="ent-sys-cell">
                  {i > 0 && <span className="ent-sys-sep">▪ </span>}
                  <b>{s.key}</b> · {s.due ? <span className="due">due</span>
                    : s.last_ok ? `ok ${rel(s.last_ok)} ago` : "never run"} · {s.cadence}
                </span>
              ))}
            {p.progress?.state === "running" && (
              <span className="ent-sys-cell live">
                <span className="ent-sys-sep">▪ </span>
                running · {p.progress.current ?? "…"} · {runElapsed}
              </span>
            )}
            <button className="ent-sys-ledger" onClick={p.onLedger}
                    title="open the activity ledger (Ctrl+J)">ledger ▸</button>
          </section>

        </div>
      </main>

      <button className="ent-ask" onClick={p.onChat} title="Ask Sigma (Ctrl+/)">
        ⌕ Ask Sigma
      </button>

      <footer className="ent-foot">
        <span className="ent-mode">layout: enterprise · palette: {p.themeName.toLowerCase()}</span>
        <span className="ent-hints">
          <button onClick={p.onPalette}>Ctrl+K command</button> ·{" "}
          <button onClick={p.onChat}>Ctrl+/ ask</button> ·{" "}
          <button onClick={p.onLedger}>Ctrl+J ledger</button> ·{" "}
          <button onClick={p.onNoSync}>Ctrl+. sealed</button> ·{" "}
          <button onClick={p.onCapture}>Ctrl+N capture</button> ·{" "}
          <button onClick={p.onWork}>Ctrl+; task</button> ·{" "}
          <button onClick={p.onAgenda}>Ctrl+' agenda</button> ·{" "}
          <button onClick={p.onStudy}>Ctrl+\ study</button> · Esc back
        </span>
        <button className={`ent-dock ${p.activity.live ? "live" : ""}`} onClick={p.onLedger}
                title="open the activity ledger (Ctrl+J)">⌁ {p.activity.text}</button>
      </footer>
    </div>
  );
}

/* ------------------------------------------------------------- work queue */

/** Collapsed, the card spends a fixed row budget — every section keeps at
 *  least one row, the remainder goes to the earliest sections — so the
 *  overview fits the viewport whatever the queues hold. Expanding shows the
 *  full window inside the card's own scroll; the page never grows. */
const QROWS = 6;

function EntQueueCard({ queue, vault, onMutate, onOpen }: {
  queue: Queue | null; vault: string; onMutate: () => void; onOpen: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  // The same tick machinery as the Instrument's digest — one write path,
  // one revertible commit per tick, whichever room it came from.
  const { rows, errs, tick } = useQueueTick(onMutate);
  const ids = queue
    ? QUEUE_ORDER.flatMap(k => queue.sections[k].visible.map(t => t.id))
    : [];
  const fresh = useFreshIds(ids);

  const filled = queue
    ? QUEUE_ORDER.map(k => queue.sections[k]).filter(s => s.visible.length)
    : [];

  // One row per filled section first, then the leftovers in queue order.
  const take = filled.map(s => Math.min(1, s.visible.length));
  let left = Math.max(0, QROWS - take.reduce((a, b) => a + b, 0));
  filled.forEach((s, i) => {
    const extra = Math.min(left, s.visible.length - take[i]);
    take[i] += extra; left -= extra;
  });
  const hidden = filled.reduce((n, s, i) => n + s.visible.length - take[i], 0);

  return (
    <div className="ent-card ent-qcard" aria-label="work queue">
      <div className="ent-card-head">
        <h2>Work queue</h2>
        <button className="ent-meta" onClick={onOpen}
                title="open the full queues (Ctrl+;)">
          {queue ? `${queue.counts.visible} visible · ${queue.counts.queued} queued ▸` : "…"}
        </button>
      </div>
      <div className={`ent-qbody ${expanded ? "expanded" : ""}`}>
        {!queue && <p className="ent-dim">loading…</p>}
        {queue && filled.length === 0 && (
          <p className="ent-dim">✓ nothing eligible — every queue is empty or blocked</p>
        )}
        {filled.map((s, i) => (
          <div key={s.key} className="ent-qsec">
            <div className="ent-eyebrow">{s.title}</div>
            {(expanded ? s.visible : s.visible.slice(0, take[i])).map(t => {
              const st = rows[t.id];
              return (
                <div key={t.id}
                     className={`ent-qrow ${st ?? ""}${fresh.has(t.id) ? " promoted" : ""}`}>
                  <button className={`ent-check ${st ?? ""}`} disabled={!!st}
                          onClick={() => tick(t)}
                          title={st === "leaving"
                            ? "ticked — one commit of its own, revertible in the ledger (Ctrl+J)"
                            : "tick it — writes to the note as its own revertible commit"}>
                    {st === "leaving" ? "✓" : ""}
                  </button>
                  <a className="ent-qtitle" href={obsidianHref(vault, t.file.replace(/\.md$/, ""))}
                     title={`${t.file}:${t.line}\n${breakdown(t)}`}>
                    {t.parent && <b className="ent-qparent">{t.parent}</b>}
                    {t.no_sync && <NoSyncMark />} {t.text}
                  </a>
                  {chipsFor(t).slice(0, 1).map(c => (
                    <em key={c.label} className={`ent-chip ${c.tone}`}>{c.label}</em>
                  ))}
                  {errs[t.id] && <span className="ent-err">{errs[t.id]}</span>}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      {(hidden > 0 || expanded) && (
        <button className="ent-more" onClick={() => setExpanded(e => !e)}
                title={expanded ? "back to the short view"
                  : "show every visible row — the card scrolls, the page does not"}>
          {expanded ? "▴ show less" : `▾ ${hidden} more`}
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ today */

function EntTodayCard({ agenda, vault, onOpen, onStudy }: {
  agenda: Agenda | null; vault: string; onOpen: () => void; onStudy: () => void;
}) {
  if (!agenda) {
    return (
      <div className="ent-card" aria-label="today">
        <div className="ent-card-head"><h2>Today</h2></div>
        <p className="ent-dim">loading…</p>
      </div>
    );
  }
  const day = todayModel(agenda, 3);
  const shown = [...day.past.slice(-1), ...day.upcoming];
  const nowLabel = `${String(day.now.getHours()).padStart(2, "0")}:${String(day.now.getMinutes()).padStart(2, "0")}`;
  const dateLabel = day.now.toLocaleDateString(undefined, { month: "short", day: "numeric" });

  return (
    <div className="ent-card" aria-label="today">
      <div className="ent-card-head">
        <h2>Today</h2>
        {agenda.problems.length > 0 && (
          <span className="ent-problem" title={agenda.problems
            .map(pr => `${pr.path}:${pr.line} — ${pr.why}`).join("\n")}>
            ⚠ {agenda.problems.length} unreadable
          </span>
        )}
        <button className="ent-meta" onClick={onOpen} title="open the calendar (Ctrl+')">
          {dateLabel} ▸
        </button>
      </div>

      {shown.length === 0 && <p className="ent-dim">nothing left today</p>}
      {day.past.slice(-1).map(o => (
        <a key={o.id} className="ent-trow done" href={obsidianHref(vault, o.source.path)}
           title={`${o.kind} · ${o.source.path}`}>
          <span className="ent-twhen">{o.start}</span>
          <span className="ent-tglyph">{GLYPH[o.kind]}</span>
          <span className="ent-ttitle">{o.no_sync && <NoSyncMark />}{o.title}</span>
          <span className="ent-ttick">✓</span>
        </a>
      ))}
      <div className="ent-nowrule"><span className="t">now · {nowLabel}</span><span className="line" /></div>
      {day.upcoming.map(o => {
        const mins = o.start ? minutes(o.start) - day.nowMin : null;
        return (
          <a key={o.id} className="ent-trow" href={obsidianHref(vault, o.source.path)}
             title={`${o.kind} · ${o.source.path}${o.conflict ? `\n⚠ ${o.conflict.why}` : ""}`}>
            <span className="ent-twhen">{o.start ?? "all day"}</span>
            <span className="ent-tglyph">{GLYPH[o.kind]}</span>
            <span className="ent-ttitle">
              {o.no_sync && <NoSyncMark />}
              {o.conflict && <span className="ent-conflict" title={o.conflict.why}>⚠ </span>}
              {o.title}
            </span>
            {o.kind === "practice" && o.done === false
              ? <span className="ent-tuntil">not yet</span>
              : mins !== null && <span className="ent-tuntil">{until(mins)}</span>}
          </a>
        );
      })}

      <div className="ent-week">
        <div className="ent-bars" role="img" aria-label="committed hours, next 7 days">
          {day.week.map(w => (
            <span key={w.key} className={`ent-bar ${w.key === day.today ? "today" : ""}`}
                  title={`${w.key} — ${w.hours ? `${w.hours.toFixed(1)}h committed` : "nothing timed"}`}>
              <span className="ent-bar-fill"
                    style={{ height: `${Math.max(8, Math.round((w.hours / day.peak) * 100))}%` }} />
            </span>
          ))}
        </div>
        <button className="ent-free" onClick={onStudy}
                title={`the waking day is ${day.dayStart}:00–${day.dayEnd}:00 — click to open the workbench`}>
          {day.freeLeft.toFixed(1)}h free ▸
        </button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- waiting */

/** The same clamp as the queue's: three rows on the overview, the rest behind
 *  the expander. Proposals are rarely many, but the one week they are is the
 *  week this card must not push the page into scroll. */
const WROWS = 3;

function EntWaitingCard({ rows, onReview }: {
  rows: ({ file: string; title: string; kind: string | null;
           target: string | null; badge: string })[] | null;
  onReview: (name: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const shown = rows && !expanded ? rows.slice(0, WROWS) : rows;
  const hidden = rows ? rows.length - (shown?.length ?? 0) : 0;
  return (
    <div className={`ent-card ${rows?.length ? "attn" : ""}`} aria-label="waiting on you">
      <div className="ent-card-head">
        <h2>Waiting on you</h2>
        {rows && rows.length > 0 && <span className="ent-meta">review ▸</span>}
      </div>
      {!rows && <p className="ent-dim">loading…</p>}
      {rows && rows.length === 0 && <p className="ent-dim">✓ nothing is waiting on you</p>}
      {shown?.map(r => (
        <button key={r.file} className="ent-wrow"
                onClick={() => onReview(r.file.replace(/\.md$/, ""))}
                title={`${r.kind ?? "?"} → ${r.target ?? "?"}\nreview the diff and decide`}>
          <em className={`ent-chip ${r.badge === "pending" ? "warn" : "ok"}`}>{r.badge}</em>
          <span>{r.title}</span>
        </button>
      ))}
      {(hidden > 0 || expanded) && (
        <button className="ent-more" onClick={() => setExpanded(e => !e)}>
          {expanded ? "▴ show less" : `▾ ${hidden} more`}
        </button>
      )}
    </div>
  );
}
