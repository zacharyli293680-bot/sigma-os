/**
 * App.tsx — the dashboard shell (Phase 0 of the dashboard plan).
 *
 * The layout in dashboard-plan §3, populated with real data, static: top strip,
 * left rail, centre fleet summary, waiting-on-you + today, projects + fleet
 * detail, foot. The chat that used to *be* this app now lives in a Ctrl+/
 * drawer. No router and no state manager on purpose — one view, plain hooks;
 * rail destinations arrive with later phases.
 */
import { useCallback, useEffect, useState } from "react";
import "./App.css";
import { get } from "./api";
import type { Fleet, Health, Projects, Proposals, Tasks, Window_ } from "./api";
import ChatDrawer from "./chat";
import { FleetCenter, FleetDetail, Foot, Panel, ProjectsPanel, Rail, TodayPanel, TopStrip, WaitingPanel } from "./panels";

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
  const [chatOpen, setChatOpen] = useState(false);
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === "/") { e.preventDefault(); setChatOpen(o => !o); }
      else if (e.key === "Escape") setChatOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const vault = health?.vault ? health.vault.split(/[\\/]/).pop() || "" : "Obsidian Vault";

  return (
    <div className="shell">
      <TopStrip health={health} window={window_} block={tasks?.block ?? null}
                clock={clock} onHealthClick={checkHealth} />
      <Rail />
      <FleetCenter fleet={fleet} />
      <div className="right">
        <WaitingPanel proposals={proposals} vault={vault} />
        <TodayPanel tasks={tasks} vault={vault} />
      </div>
      <div className="lower">
        <ProjectsPanel projects={projects?.projects ?? null} vault={vault} />
        <FleetDetail fleet={fleet} />
      </div>
      <Foot />
      <ChatDrawer open={chatOpen} vault={vault} onClose={() => setChatOpen(false)} />
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
