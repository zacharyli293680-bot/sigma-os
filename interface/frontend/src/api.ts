// Served by the backend itself in production, so same-origin ("") is correct on
// any port. The absolute fallback is for `npm run dev`, where Vite serves the UI
// and the backend lives on its default port.
export const API = import.meta.env.DEV ? "http://127.0.0.1:8787" : "";

export type Finding = { level: string; what: string; fix: string | null };
export type Health = { vault: string; ok: boolean; findings: Finding[] };

export type Specialist = {
  key: string; title: string; cadence: string; model: string;
  last_ok: string | null; last_run: string | null;
  last_result: string | null; last_proposals: number | null; due: boolean;
};
export type Fleet = {
  last_run: string | null; stopped_early_at: string | null;
  task_installed: boolean; specialists: Specialist[];
};

export type VaultTask = {
  text: string; due: string; priority: number | null;
  overdue: boolean; file: string; line: number;
};
export type Tasks = { today: string; block: string | null; tasks: VaultTask[] };

export type ProposalRow = {
  file: string; title: string; kind: string | null; target: string | null;
  risk: string | null; date: string | null; status: string | null; staged?: string;
};
export type Proposals = {
  pending: ProposalRow[]; approved: ProposalRow[];
  staged: ProposalRow[]; applied_recent: ProposalRow[];
};

export type RepoState = {
  branch: string | null; dirty: number | null;
  last_commit: string | null; last_subject: string; unpushed: number | null;
};
export type Project = {
  name: string; status: string | null; area: string | null;
  started: string | null; due: string | null; repo: string | null;
  git: RepoState | null;
};
export type Projects = { projects: Project[] };

export type Window_ = {
  known: boolean; percent: number | null; reserved: number | null; note: string;
  vault: string;   // the vault's real name — obsidian:// links must not guess it
};

export type GraphNode = {
  id: string; label: string; bucket: string;
  inlinks: number; mtime: string | null;
};
export type Graph = {
  notes: number; edges: number;
  nodes: GraphNode[]; links: [number, number][];
};

export type CommandInfo = {
  verb: string; title: string; hint: string;
  writes: boolean; model: boolean; enabled: boolean;
};
export type Job = {
  verb: string; title: string;
  state: "running" | "done" | "failed";
  started: string; finished: string | null;
  exit: number | null; lines: string[];
};

export type ProgressResult = {
  ok: boolean; seconds: number; proposals: number; error: string | null;
};
export type Progress = {
  state: "running" | "done";
  note: string | null;
  run_started: string;
  queue: string[];
  current: string | null;
  current_started: string | null;
  current_model: string | null;
  results: Record<string, ProgressResult>;
  stopped_early: boolean;
  finished: string | null;
  updated: string;
};

export async function get<T>(path: string): Promise<T> {
  // A hung endpoint must fail, not stack: refresh() refires every 60s.
  const r = await fetch(`${API}/api/${path}`, { signal: AbortSignal.timeout(20_000) });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

/** "9h" / "3d" / "now" — panel rows want age, not timestamps. */
export function rel(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (isNaN(ms)) return iso;
  if (ms < 0) return "soon";     // clock skew must not read as "just happened"
  const m = Math.floor(ms / 60000);
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** Obsidian deep link — the click-through that makes every panel a door, not a copy. */
export function obsidianHref(vault: string, file: string): string {
  return `obsidian://open?vault=${encodeURIComponent(vault)}&file=${encodeURIComponent(file)}`;
}
