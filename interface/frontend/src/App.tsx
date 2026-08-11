/**
 * App.tsx — the dashboard shell (Phase 0 of the dashboard plan).
 *
 * The layout in dashboard-plan §3, populated with real data, static: top strip,
 * left rail, centre fleet summary, waiting-on-you + today, projects + fleet
 * detail, foot. The chat that used to *be* this app now lives in a Ctrl+/
 * drawer. No router and no state manager on purpose — one view, plain hooks;
 * rail destinations arrive with later phases.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import { API, get } from "./api";
import type { Agenda, Fire, Fleet, Graph, GuideProgress, Health, Job, NoSync, Progress, Projects, Proposals, Queue, Tasks, Window_ } from "./api";
import Brain, { VaultHud } from "./brain";
import ChatDrawer from "./chat";
import Ledger from "./ledger";
import NoSyncView from "./nosync";
import Palette from "./palette";
import Review from "./review";
import StudyView from "./study";
import BuildView from "./build";
import SoonView from "./soon";
import WorkbenchView from "./workbench";
import WorkView from "./work";
import AgendaRail from "./agenda-rail";
import AgendaView from "./agenda";
import Capture from "./capture";
import { Foot, Panel, ProjectsPanel, QueuePanel, Rail, TopStrip, WaitingPanel } from "./panels";
import Reactor, { activityLine, useElapsed } from "./reactor";
import ThemeView from "./theme-view";
import { useTheme } from "./theme";

const REFRESH_MS = 60_000;

/** Local dates, not UTC. `toISOString()` would roll the day over at 17:00
 *  Pacific and ask the calendar for tomorrow. */
function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(d.getDate() + n);
  return out;
}
/** The graph endpoint is cached server-side for 300s; polling it faster only
 *  ever returns the same thing. */
const GRAPH_MS = 300_000;

function useClock(): string {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);
  return now.toTimeString().slice(0, 5);
}

export default function App() {
  // undefined = not fetched yet, null = the fetch actually failed. The
  // OFFLINE panel keys on null — it used to key on the initial state and
  // flashed "backend unreachable" on every cold load.
  const [health, setHealth] = useState<Health | null | undefined>(undefined);
  const [fleet, setFleet] = useState<Fleet | null | undefined>(undefined);
  // Both, and they are not redundant: `tasks` is dated work anywhere in the
  // vault (the calendar strip, the top strip's study block), `queue` is the
  // four priority queues, which ignore 01-Daily entirely.
  const [tasks, setTasks] = useState<Tasks | null | undefined>(undefined);
  // The calendar, from the one resolver. Still distinct from `tasks`: this is
  // windowed and holds events, notes and expanded rules as well as checkboxes,
  // where `tasks` is every dated task in the vault with no horizon.
  const [agenda, setAgenda] = useState<Agenda | null | undefined>(undefined);
  const [queue, setQueue] = useState<Queue | null | undefined>(undefined);
  const [proposals, setProposals] = useState<Proposals | null | undefined>(undefined);
  const [projects, setProjects] = useState<Projects | null | undefined>(undefined);
  const [window_, setWindow] = useState<Window_ | null | undefined>(undefined);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  // The brain is the centre stage's background, with the reactor at its heart.
  // There is no second state and no expand: one stage, always the same size.
  const [graph, setGraph] = useState<Graph | null>(null);
  const [brainFilter, setBrainFilter] = useState<string | null>(null);
  // Whether the sky drifts. Persisted like the palette, and for the same
  // reason: a preference about ambient motion that resets on every reload is
  // one you have to re-express every morning. Defaults to on, so nothing
  // changes for anyone who never touches it.
  const [spin, setSpin] = useState(() => {
    try { return localStorage.getItem("sigma.brain.spin") !== "off"; }
    catch { return true; }        // private mode — drift, just do not remember
  });
  const setSpinSaved = (v: boolean) => {
    setSpin(v);
    try { localStorage.setItem("sigma.brain.spin", v ? "on" : "off"); }
    catch { /* see above */ }
  };
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [noSyncOpen, setNoSyncOpen] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [studyOpen, setStudyOpen] = useState(false);
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const [buildOpen, setBuildOpen] = useState(false);
  // Which unbuilt rail slot is showing its placeholder (§5.4). One piece of
  // state for all three: they are mutually exclusive, and a boolean each would
  // be three more rungs on the Esc ladder for one behaviour.
  const [soonSlot, setSoonSlot] = useState<string | null>(null);
  const [workOpen, setWorkOpen] = useState(false);
  const [agendaOpen, setAgendaOpen] = useState(false);
  const [captureOpen, setCaptureOpen] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);
  // Only for the strip's readout — the palette itself is applied to :root by
  // theme.ts, so nothing here re-renders to change a colour.
  const theme = useTheme();
  // Fetched once for the rail's count badge; the view refetches on open.
  const [noSync, setNoSync] = useState<NoSync | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [guideProg, setGuideProg] = useState<GuideProgress | null>(null);
  const fireRef = useRef<((detail: string) => void) | null>(null);
  const clock = useClock();

  const refresh = useCallback(() => {
    get<Fleet>("fleet").then(setFleet).catch(() => setFleet(null));
    get<Tasks>("tasks").then(setTasks).catch(() => setTasks(null));
    // Exactly the rail's horizon: today plus the six days its density bar
    // shows. A wider window would be payload nobody renders, on a 60s poll.
    get<Agenda>(`agenda?from=${iso(new Date())}&to=${iso(addDays(new Date(), 6))}`)
      .then(setAgenda).catch(() => setAgenda(null));
    get<Queue>("queue").then(setQueue).catch(() => setQueue(null));
    get<Proposals>("proposals").then(setProposals).catch(() => setProposals(null));
    get<Projects>("projects").then(setProjects).catch(() => setProjects(null));
    get<Window_>("window").then(setWindow).catch(() => setWindow(null));
    get<NoSync>("nosync").then(setNoSync).catch(() => setNoSync(null));
  }, []);

  // Health is separate and fetched once: doctor may probe auth with a real
  // model call (45s worst case), so it is never on the refresh interval —
  // clicking the glyph re-runs it deliberately.
  const checkHealth = useCallback(() => {
    setHealth(undefined);
    get<Health>("health").then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    refresh();
    checkHealth();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh, checkHealth]);

  // The graph on its own slower cadence: the backend caches it for 300s, and
  // re-fetching it every minute would only ever hand back the same object.
  // Kept off `refresh` deliberately — a new graph identity relays the sky, and
  // that is not something a routine poll should be able to do.
  useEffect(() => {
    const pull = () => get<Graph>("graph").then(setGraph).catch(() => {});
    pull();
    const t = setInterval(pull, GRAPH_MS);
    return () => clearInterval(t);
  }, []);

  // The reactor's live feed. EventSource reconnects on its own, and the
  // endpoint replays the current state on connect, so a mid-run page load
  // still shows the run.
  useEffect(() => {
    const es = new EventSource(`${API}/api/fleet/progress`);
    es.onmessage = e => {
      try { setProgress(JSON.parse(e.data)); } catch { /* torn event — keep last */ }
    };
    // A dead feed must not keep narrating: clear on error, and let the
    // server's replay-on-connect repopulate when the reconnect succeeds.
    es.onerror = () => setProgress(null);
    return () => es.close();
  }, []);

  // The generation pipeline's feed (study S7) — the same file-poll SSE shape
  // as the fleet's, and the same reason: a run started from the CLI must
  // render here all the same.
  useEffect(() => {
    const es = new EventSource(`${API}/api/guide/progress`);
    es.onmessage = e => {
      try { setGuideProg(JSON.parse(e.data)); } catch { /* torn event */ }
    };
    es.onerror = () => setGuideProg(null);
    return () => es.close();
  }, []);

  // The fleet's own tool calls, so an unattended run lights the notes it reads
  // instead of being represented by four arcs and nothing else. Separate from
  // the chat drawer's trail on purpose: that one rides the /api/ask stream and
  // only exists while a question is in flight, and the run that most needs to
  // be visible is the 09:00 one nobody is sitting in front of.
  useEffect(() => {
    const es = new EventSource(`${API}/api/fleet/fire`);
    es.onmessage = e => {
      try {
        const f: Fire = JSON.parse(e.data);
        if (f.detail) fireRef.current?.(f.detail);
      } catch { /* torn event — the next one arrives whole */ }
    };
    return () => es.close();
  }, []);

  // When a run finishes, the panels are stale the moment the reactor settles —
  // refetch immediately rather than waiting out the minute.
  const prevRunState = useRef<string | null>(null);
  useEffect(() => {
    if (prevRunState.current === "running" &&
        (progress?.state === "done" || progress?.state === "paused")) refresh();
    prevRunState.current = progress?.state ?? null;
  }, [progress, refresh]);

  // Same settle→refetch for a generation run: when it stops, new notes have
  // just landed (or been held) and the panels are stale.
  const prevGuideState = useRef<string | null>(null);
  useEffect(() => {
    if (prevGuideState.current === "running" && guideProg &&
        guideProg.state !== "running") refresh();
    prevGuideState.current = guideProg?.state ?? null;
  }, [guideProg, refresh]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.altKey) return;   // held keys flicker; AltGr fakes ctrl
      if (e.ctrlKey && e.key === "/") { e.preventDefault(); setChatOpen(o => !o); }
      else if (e.ctrlKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setPaletteOpen(o => !o);
      } else if (e.ctrlKey && (e.key === "j" || e.key === "J")) {
        e.preventDefault();
        setLedgerOpen(o => !o);
      } else if (e.ctrlKey && e.key === ".") {
        e.preventDefault();
        setNoSyncOpen(o => !o);
      } else if (e.ctrlKey && (e.key === "n" || e.key === "N")) {
        e.preventDefault();
        setCaptureOpen(o => !o);
      } else if (e.ctrlKey && e.key === ";") {
        // Add a task. Ctrl+N is capture (a note, into the inbox) and Ctrl+W
        // belongs to the browser; `;` is unclaimed and sits under the right
        // hand. Opens rather than toggles: hitting it while the view is open
        // means "I want to type a task", not "close this".
        e.preventDefault();
        setWorkOpen(true);
      } else if (e.ctrlKey && e.key === ",") {
        // The palette picker. `,` is the settings key everywhere else and is
        // unclaimed by Chrome here; it is also the only one of these that
        // changes how the dashboard looks rather than what it says.
        e.preventDefault();
        setThemeOpen(o => !o);
      } else if (e.ctrlKey && e.key === "\\") {
        // The study workbench. L is the address bar and G is find-next —
        // both eaten at the browser level like Ctrl+K — and `\` is unclaimed;
        // mnemonically it is the split between the workbench's two panes.
        e.preventDefault();
        setWorkbenchOpen(o => !o);
      } else if (e.ctrlKey && e.key === "'") {
        // The full agenda. `'` sits next to `;` (work) and `.` (no-sync), so
        // the three "what is on my plate" surfaces are three neighbouring keys.
        // Verified reaching the page in Chrome before the view was built —
        // Ctrl+K is eaten at the browser level here, and finding that out
        // after the fact would have meant rebuilding for it.
        e.preventDefault();
        setAgendaOpen(o => !o);
      } else if (e.key === "Escape") {
        // Esc peels one layer: review, palette, no-sync, ledger, drawer, then
        // an active filter. That last rung is what is left of the brain's own
        // Esc — the view it used to close no longer exists, but a filter still
        // needs a keyboard way out.
        // The theme picker is absent from this ladder on purpose: Esc there
        // has to *revert* the live preview before it closes, so theme-view.tsx
        // handles its own in the capture phase and stops the event here.
        if (captureOpen) setCaptureOpen(false);
        else if (reviewing) setReviewing(null);
        // The workbench peels before work: its own capture-phase handler has
        // already eaten Esc for the dock and focus rungs, so an event arriving
        // here means the view itself should close.
        else if (workbenchOpen) setWorkbenchOpen(false);
        else if (workOpen) setWorkOpen(false);
        // Agenda peels after work: it is normally entered from the work view or
        // from the today rail, so Esc unwinds in the order you arrived.
        else if (agendaOpen) setAgendaOpen(false);
        else if (studyOpen) setStudyOpen(false);
        else if (buildOpen) setBuildOpen(false);
        else if (soonSlot) setSoonSlot(null);
        else if (paletteOpen) setPaletteOpen(false);
        else if (noSyncOpen) setNoSyncOpen(false);
        else if (ledgerOpen) setLedgerOpen(false);
        else if (chatOpen) setChatOpen(false);
        else if (brainFilter) setBrainFilter(null);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // Every state the Esc ladder *reads* has to be in here. A missing one does
    // not fail loudly: the toggle keybind keeps working, because those use the
    // functional form of setState and never read the old value — so the view
    // opens and closes on Ctrl+' while Esc silently skips its rung. `agendaOpen`
    // was missing exactly that way and it took driving the view to notice.
  }, [paletteOpen, ledgerOpen, chatOpen, noSyncOpen, reviewing, studyOpen,
      workbenchOpen, buildOpen, workOpen, agendaOpen, captureOpen, brainFilter,
      soonSlot]);

  // Palette jobs stream here and take over the dock while they run; when one
  // finishes, the panels it may have changed refetch immediately.
  useEffect(() => {
    const es = new EventSource(`${API}/api/commands/events`);
    es.onmessage = e => {
      try { setJob(JSON.parse(e.data)); } catch { /* torn event */ }
    };
    es.onerror = () => setJob(null);   // a restarted server has no job to claim
    return () => es.close();
  }, []);
  const prevJobState = useRef<string | null>(null);
  useEffect(() => {
    if (prevJobState.current === "running" && job && job.state !== "running") {
      refresh();
      // Re-run the doctor only after verbs that can change what it measures —
      // re-probing after `doctor` itself ran it twice in a row.
      const v = job.verb;
      if (!v.endsWith("-status") && v !== "doctor" && v !== "status" && v !== "reflect-diff") {
        checkHealth();
      }
    }
    prevJobState.current = job?.state ?? null;
  }, [job, refresh, checkHealth]);

  // The vault's real name arrives with the instant /api/window fetch — the
  // old source was the 45s health probe, and a hardcoded guess filled the gap.
  const vault = window_?.vault
    ?? (health?.vault ? health.vault.split(/[\\/]/).pop() || "" : "");
  // Held = a loop stalled on Zach. Only *pending* proposals qualify —
  // counting approved/staged kept every arc amber long after he had acted.
  const waitingCount = proposals?.pending.length ?? 0;
  const dockElapsed = useElapsed(progress?.state === "running" ? progress.current_started : null);

  // Dock priority: a palette job running now, a job finished in the last
  // minute-and-a-half, then the fleet's own line.
  const jobRecent = job?.finished
    && Date.now() - new Date(job.finished).getTime() < 90_000;
  const dock = job && job.state === "running"
    ? { text: `⌘ ${job.verb} · ${job.lines[job.lines.length - 1] ?? "starting…"}`, live: true }
    : job && jobRecent
    ? { text: `⌘ ${job.verb} ${job.state === "done" ? "✓" : "✗ failed"} · ${job.lines[job.lines.length - 1] ?? ""}`, live: false }
    : activityLine(fleet ?? null, progress, dockElapsed);

  return (
    <div className="shell">
      {/* the same drifting haze the sky sits in — one material, one view */}
      <div className="haze" aria-hidden="true" />
      <TopStrip health={health} window={window_ ?? null} block={tasks?.block ?? null}
                clock={clock} onHealthClick={checkHealth}
                theme={theme.name} onTheme={() => setThemeOpen(o => !o)} />
      <Rail noSyncOpen={noSyncOpen} onNoSync={() => setNoSyncOpen(o => !o)}
            noSyncCount={noSync?.ok ? noSync.total : null}
            studyOpen={studyOpen} onStudy={() => setStudyOpen(o => !o)}
            buildOpen={buildOpen} onBuild={() => setBuildOpen(o => !o)}
            workOpen={workOpen} onWork={() => setWorkOpen(o => !o)}
            agendaOpen={agendaOpen} onAgenda={() => setAgendaOpen(o => !o)}
            soonSlot={soonSlot}
            onSoon={k => setSoonSlot(s => (s === k ? null : k))} />
      {/* The centre stage: one scene, not a panel with a picture in it. The sky
          fills this cell and nothing else — it briefly spanned the whole shell
          behind every panel, and what that cost was the alignment, because the
          reactor is the thing the vault is supposed to be firing *around*. The
          sky is bounded here, the reactor sits at its heart in a pool of
          darkened sky, and the vault's numbers run down the left margin. */}
      <section className="panel center">
        <h2>FLEET</h2>
        <Brain graph={graph} vault={vault} fireRef={fireRef} filter={brainFilter}
               spin={spin} />
        <div className="core-scrim" aria-hidden="true" />
        <Reactor fleet={fleet ?? null} progress={progress} waitingCount={waitingCount}
                 guide={guideProg} />
        <VaultHud graph={graph} filter={brainFilter} onFilter={setBrainFilter}
                  spin={spin} onSpin={setSpinSaved} />
      </section>
      <div className="right">
        <WaitingPanel proposals={proposals ?? null} onReview={setReviewing} />
        <QueuePanel queue={queue ?? null} vault={vault} onMutate={refresh}
                    onOpen={() => setWorkOpen(true)} />
      </div>
      <div className="lower">
        <AgendaRail agenda={agenda ?? null} vault={vault}
                    onOpen={() => setStudyOpen(true)} />
        <ProjectsPanel projects={projects?.projects ?? null} vault={vault} />
      </div>
      <Foot activity={dock}
            onChat={() => setChatOpen(o => !o)}
            onPalette={() => setPaletteOpen(o => !o)}
            onLedger={() => setLedgerOpen(o => !o)}
            onNoSync={() => setNoSyncOpen(o => !o)}
            onCapture={() => setCaptureOpen(o => !o)}
            onWork={() => setWorkOpen(true)}
            onAgenda={() => setAgendaOpen(o => !o)}
            onWorkbench={() => setWorkbenchOpen(o => !o)} />
      <Ledger open={ledgerOpen} vault={vault} onClose={() => setLedgerOpen(false)}
              onMutate={refresh} />
      <NoSyncView open={noSyncOpen} vault={vault} onClose={() => setNoSyncOpen(false)} />
      <Review name={reviewing} vault={vault} onClose={() => setReviewing(null)}
              onMutate={refresh} />
      <StudyView open={studyOpen} vault={vault} onClose={() => setStudyOpen(false)}
                 onWorkbench={() => { setStudyOpen(false); setWorkbenchOpen(true); }} />
      <WorkbenchView open={workbenchOpen} vault={vault}
                     onClose={() => setWorkbenchOpen(false)}
                     guideProg={guideProg} />
      <BuildView open={buildOpen} vault={vault} onClose={() => setBuildOpen(false)} />
      <SoonView slot={soonSlot} vault={vault} onClose={() => setSoonSlot(null)} />
      <WorkView open={workOpen} vault={vault} onClose={() => setWorkOpen(false)}
                onMutate={refresh} />
      <AgendaView open={agendaOpen} vault={vault} onClose={() => setAgendaOpen(false)} />
      <Capture open={captureOpen} onClose={() => setCaptureOpen(false)} onDone={refresh} />
      <Palette open={paletteOpen} onClose={() => setPaletteOpen(false)}
               onLaunched={() => {}} />
      <ThemeView open={themeOpen} onClose={() => setThemeOpen(false)} />
      <ChatDrawer open={chatOpen} vault={vault} onClose={() => setChatOpen(false)}
                  onTool={d => fireRef.current?.(d)} />
      {!chatOpen && (
        <button className="chat-fab" onClick={() => setChatOpen(true)} title="Ask Sigma (Ctrl+/)">
          ⌕ ask
        </button>
      )}
      {fleet === null && tasks === null && proposals === null && (
        // All three null means all three fetches *failed* — undefined (still
        // loading) never triggers this.
        <Panel label="OFFLINE" className="offline">
          <p>backend unreachable — start it with <code>sigma ui</code></p>
        </Panel>
      )}
    </div>
  );
}
