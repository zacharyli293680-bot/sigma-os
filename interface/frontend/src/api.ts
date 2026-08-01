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
  no_sync: boolean;   // gitignored but model-exempt — never leaves this machine
  raw: string;   // the exact line — handed back to toggle as the staleness check
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

/** One proposal, opened for review (Phase 6). */
export type ProposalDetail = {
  name: string; title: string;
  kind: string | null; status: string | null; risk: string | null; date: string;
  target: string;
  target_exists: boolean;
  /** True when applying stages rather than writes — applier.py's rule, surfaced. */
  would_stage: boolean;
  proposed: string; diff: string; body: string;
};

export type RepoState = {
  branch: string | null; dirty: number | null;
  last_commit: string | null; last_subject: string; unpushed: number | null;
};
export type Project = {
  name: string; status: string | null; area: string | null;
  started: string | null; due: string | null; repo: string | null;
  no_sync: boolean;
  git: RepoState | null;
};
export type Projects = { projects: Project[] };

/** The Phase 5 audit view: everything that never leaves this machine. */
export type NoSyncGroup = {
  prefix: string; count: number; bytes: number; newest: string | null;
};
export type NoSyncFile = { path: string; mtime: string | null; bytes: number | null };
export type NoSync = {
  ok: boolean;          // false = git could not answer; the boundary is unverified
  total: number; bytes: number;
  groups: NoSyncGroup[]; files: NoSyncFile[];
  truncated: number;    // rows beyond the cap — never silently dropped
};

export type Window_ = {
  // "proxy" since Phase 4: observed spend + the last rate-limit event. Never
  // true — real headroom is not exposed by anything, and the UI must not
  // render a proxy as a percentage.
  known: boolean | "proxy"; percent: number | null;
  calls?: number; cost_usd?: number | null;
  last_rate_limit?: string | null;
  paused?: boolean; resume_at?: string | null;
  reserved: string | number | null; note: string;
  vault: string;   // the vault's real name — obsidian:// links must not guess it
};

export type LedgerEntry = {
  ts: string; actor: string;
  action: "create" | "update" | "toggle" | "revert" | "append";
  target: string; sha: string | null; summary: string;
  reverted: boolean;
  extra?: { proposal?: string; reverts?: string; line?: number; absorbed?: boolean };
};
export type Activity = { entries: LedgerEntry[] };

export type GraphNode = {
  id: string; label: string; bucket: string;
  no_sync: boolean;   // drawn as a bronze ring, never as a colour — bucket owns colour
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
  applied?: number; held?: number;
};
export type Progress = {
  state: "running" | "done" | "paused";
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
  degraded?: boolean;       // running on Haiku — the arcs render hollow
  resume_at?: string | null;
};

export async function get<T>(path: string): Promise<T> {
  // A hung endpoint must fail, not stack: refresh() refires every 60s.
  const r = await fetch(`${API}/api/${path}`, { signal: AbortSignal.timeout(20_000) });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

/** The mutation layer (Phase 4). A failed write carries the server's error
 *  word — "stale", "busy", "conflict", "sealed path" — so a row can react
 *  precisely instead of showing one generic failure. */
export class ApiError extends Error {
  status: number; code: string; detail?: string;
  constructor(status: number, code: string, detail?: string) {
    super(detail || code);
    this.status = status; this.code = code; this.detail = detail;
  }
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API}/api/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30_000),
  });
  let data: { error?: string; detail?: string } | null = null;
  try { data = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw new ApiError(r.status, data?.error ?? String(r.status), data?.detail);
  return data as T;
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
