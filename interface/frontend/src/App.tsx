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
import type { Fleet, Health, Job, NoSync, Progress, Projects, Proposals, Tasks, Window_ } from "./api";
import Brain from "./brain";
import ChatDrawer from "./chat";
import Ledger from "./ledger";
import NoSyncView from "./nosync";
import Palette from "./palette";
import Review from "./review";
import { Foot, Panel, ProjectsPanel, Rail, TodayPanel, TopStrip, WaitingPanel } from "./panels";
import Reactor, { activityLine, useElapsed } from "./reactor";

const REFRESH_MS = 60_000;

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
  const [tasks, setTasks] = useState<Tasks | null | undefined>(undefined);
  const [proposals, setProposals] = useState<Proposals | null | undefined>(undefined);
  const [projects, setProjects] = useState<Projects | null | undefined>(undefined);
  const [window_, setWindow] = useState<Window_ | null | undefined>(undefined);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [noSyncOpen, setNoSyncOpen] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  // Fetched once for the rail's count badge; the view refetches on open.
  const [noSync, setNoSync] = useState<NoSync | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const fireRef = useRef<((detail: string) => void) | null>(null);
  const clock = useClock();

  const refresh = useCallback(() => {
    get<Fleet>("fleet").then(setFleet).catch(() => setFleet(null));
    get<Tasks>("tasks").then(setTasks).catch(() => setTasks(null));
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

  // When a run finishes, the panels are stale the moment the reactor settles —
  // refetch immediately rather than waiting out the minute.
  const prevRunState = useRef<string | null>(null);
  useEffect(() => {
    if (prevRunState.current === "running" &&
        (progress?.state === "done" || progress?.state === "paused")) refresh();
    prevRunState.current = progress?.state ?? null;
  }, [progress, refresh]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.altKey) return;   // held keys flicker; AltGr fakes ctrl
      if (e.ctrlKey && e.key === "/") { e.preventDefault(); setChatOpen(o => !o); }
      else if (e.ctrlKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setPaletteOpen(o => !o);
      } else if (e.ctrlKey && (e.key === "g" || e.key === "G")) {
        e.preventDefault();
        setBrainOpen(o => !o);
      } else if (e.ctrlKey && (e.key === "j" || e.key === "J")) {
        e.preventDefault();
        setLedgerOpen(o => !o);
      } else if (e.ctrlKey && e.key === ".") {
        e.preventDefault();
        setNoSyncOpen(o => !o);
      } else if (e.key === "Escape") {
        // Esc peels one layer: review, palette, no-sync, ledger, drawer, brain.
        if (reviewing) setReviewing(null);
        else if (paletteOpen) setPaletteOpen(false);
        else if (noSyncOpen) setNoSyncOpen(false);
        else if (ledgerOpen) setLedgerOpen(false);
        else if (chatOpen) setChatOpen(false);
        else setBrainOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [paletteOpen, ledgerOpen, chatOpen, noSyncOpen, reviewing]);

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
      {/* the same drifting haze the brain view sits in — one material, two views */}
      <div className="haze" aria-hidden="true" />
      <TopStrip health={health} window={window_ ?? null} block={tasks?.block ?? null}
                clock={clock} onHealthClick={checkHealth} />
      <Rail brainOpen={brainOpen} onBrain={() => setBrainOpen(o => !o)}
            noSyncOpen={noSyncOpen} onNoSync={() => setNoSyncOpen(o => !o)}
            noSyncCount={noSync?.ok ? noSync.total : null} />
      <Reactor fleet={fleet ?? null} progress={progress} waitingCount={waitingCount} />
      <div className="right">
        <WaitingPanel proposals={proposals ?? null} onReview={setReviewing} />
        <TodayPanel tasks={tasks ?? null} vault={vault} onMutate={refresh} />
      </div>
      <div className="lower">
        <ProjectsPanel projects={projects?.projects ?? null} vault={vault} />
      </div>
      <Foot activity={dock}
            onChat={() => setChatOpen(o => !o)}
            onBrain={() => setBrainOpen(o => !o)}
            onPalette={() => setPaletteOpen(o => !o)}
            onLedger={() => setLedgerOpen(o => !o)}
            onNoSync={() => setNoSyncOpen(o => !o)} />
      <Brain open={brainOpen} vault={vault} fireRef={fireRef} />
      <Ledger open={ledgerOpen} vault={vault} onClose={() => setLedgerOpen(false)}
              onMutate={refresh} />
      <NoSyncView open={noSyncOpen} vault={vault} onClose={() => setNoSyncOpen(false)} />
      <Review name={reviewing} vault={vault} onClose={() => setReviewing(null)}
              onMutate={refresh} />
      <Palette open={paletteOpen} onClose={() => setPaletteOpen(false)}
               onLaunched={() => {}} />
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
