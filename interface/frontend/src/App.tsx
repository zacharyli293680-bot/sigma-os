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
import type { Fleet, Health, Job, Progress, Projects, Proposals, Tasks, Window_ } from "./api";
import Brain from "./brain";
import ChatDrawer from "./chat";
import Palette from "./palette";
import { FleetDetail, Foot, Panel, ProjectsPanel, Rail, TodayPanel, TopStrip, WaitingPanel } from "./panels";
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
  const [health, setHealth] = useState<Health | null | undefined>(undefined);
  const [fleet, setFleet] = useState<Fleet | null>(null);
  const [tasks, setTasks] = useState<Tasks | null>(null);
  const [proposals, setProposals] = useState<Proposals | null>(null);
  const [projects, setProjects] = useState<Projects | null>(null);
  const [window_, setWindow] = useState<Window_ | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [brainOpen, setBrainOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const fireRef = useRef<((detail: string) => void) | null>(null);
  const clock = useClock();

  const refresh = useCallback(() => {
    get<Fleet>("fleet").then(setFleet).catch(() => setFleet(null));
    get<Tasks>("tasks").then(setTasks).catch(() => setTasks(null));
    get<Proposals>("proposals").then(setProposals).catch(() => setProposals(null));
    get<Projects>("projects").then(setProjects).catch(() => setProjects(null));
    get<Window_>("window").then(setWindow).catch(() => setWindow(null));
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
    return () => es.close();
  }, []);

  // When a run finishes, the panels are stale the moment the reactor settles —
  // refetch immediately rather than waiting out the minute.
  const prevRunState = useRef<string | null>(null);
  useEffect(() => {
    if (prevRunState.current === "running" && progress?.state === "done") refresh();
    prevRunState.current = progress?.state ?? null;
  }, [progress, refresh]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === "/") { e.preventDefault(); setChatOpen(o => !o); }
      else if (e.ctrlKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setPaletteOpen(o => !o);
      } else if (e.ctrlKey && (e.key === "g" || e.key === "G")) {
        e.preventDefault();
        setBrainOpen(o => !o);
      } else if (e.key === "Escape") {
        // Esc peels one layer: palette, then the drawer, then the brain.
        if (paletteOpen) setPaletteOpen(false);
        else if (chatOpen) setChatOpen(false);
        else setBrainOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [paletteOpen, chatOpen]);

  // Palette jobs stream here and take over the dock while they run; when one
  // finishes, the panels it may have changed refetch immediately.
  useEffect(() => {
    const es = new EventSource(`${API}/api/commands/events`);
    es.onmessage = e => {
      try { setJob(JSON.parse(e.data)); } catch { /* torn event */ }
    };
    return () => es.close();
  }, []);
  const prevJobState = useRef<string | null>(null);
  useEffect(() => {
    if (prevJobState.current === "running" && job && job.state !== "running") {
      refresh();
      checkHealth();
    }
    prevJobState.current = job?.state ?? null;
  }, [job, refresh, checkHealth]);

  const vault = health?.vault ? health.vault.split(/[\\/]/).pop() || "" : "Obsidian Vault";
  const waitingCount = proposals
    ? proposals.pending.length + proposals.approved.length + proposals.staged.length
    : 0;
  const dockElapsed = useElapsed(progress?.state === "running" ? progress.current_started : null);

  // Dock priority: a palette job running now, a job finished in the last
  // minute-and-a-half, then the fleet's own line.
  const jobRecent = job?.finished
    && Date.now() - new Date(job.finished).getTime() < 90_000;
  const dock = job && job.state === "running"
    ? { text: `⌘ ${job.verb} · ${job.lines[job.lines.length - 1] ?? "starting…"}`, live: true }
    : job && jobRecent
    ? { text: `⌘ ${job.verb} ${job.state === "done" ? "✓" : "✗ failed"} · ${job.lines[job.lines.length - 1] ?? ""}`, live: false }
    : activityLine(fleet, progress, dockElapsed);

  return (
    <div className="shell">
      <TopStrip health={health} window={window_} block={tasks?.block ?? null}
                clock={clock} onHealthClick={checkHealth} />
      <Rail brainOpen={brainOpen} onBrain={() => setBrainOpen(o => !o)} />
      <Reactor fleet={fleet} progress={progress} waitingCount={waitingCount} />
      <div className="right">
        <WaitingPanel proposals={proposals} vault={vault} />
        <TodayPanel tasks={tasks} vault={vault} />
      </div>
      <div className="lower">
        <ProjectsPanel projects={projects?.projects ?? null} vault={vault} />
        <FleetDetail fleet={fleet} />
      </div>
      <Foot activity={dock}
            onChat={() => setChatOpen(o => !o)}
            onBrain={() => setBrainOpen(o => !o)}
            onPalette={() => setPaletteOpen(o => !o)} />
      <Brain open={brainOpen} vault={vault} fireRef={fireRef} />
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
        <Panel label="OFFLINE" className="offline">
          <p>backend unreachable — start it with <code>sigma ui</code></p>
        </Panel>
      )}
    </div>
  );
}
