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

/** The four priority queues (todo.py). Days are no longer the organising unit;
 *  each section shows a window of its highest-scoring *eligible* tasks. */
export type ScoreParts = {
  deadline: number; urgency: number; aging: number; total: number;
  /** null when the task has no deadline — not zero, which would read as "today". */
  days_until: number | null;
  age_days: number;
};
export type QueueTask = {
  id: string; file: string; line: number;
  raw: string;   // the staleness token, same contract as VaultTask
  text: string; heading: string | null; order: number;
  no_sync: boolean; deadline: string | null;
  section: string; parent: string | null;
  urgency: "high" | "medium" | "low";
  created: string; pinned: boolean; snoozed_until: string | null;
  raw_input: string | null;
  /** Set when this task sits behind an unfinished one in the same chain file. */
  blocked_by: string | null;
  /** True when it lives in a sequence document (a course timeline), where rank
   *  is meaningless and the expanded view must render order instead. */
  chain: boolean;
  parts: ScoreParts; score: number;
  overdue: boolean; archived: boolean; snoozed: boolean;
};
/** One course or project, with its whole chain in document order. */
export type QueueGroup = {
  parent: string; label: string; open: number; chain: QueueTask[];
};
export type QueueSection = {
  key: string; title: string;
  kind: "chain" | "flat";
  /** "course" / "project" when the window is one-per-parent, else null. */
  parent_noun: string | null;
  /** A maximum, never a quota: fewer eligible tasks means fewer rows, no filler. */
  window: number;
  visible: QueueTask[]; queue: QueueTask[]; blocked: QueueTask[];
  archived: QueueTask[]; snoozed: QueueTask[];
  groups: QueueGroup[];
  /** Every parent this section can take — empty for a flat section. Distinct
   *  from `groups`, which lists only parents that already have a chain: a move
   *  target has to include the course you have not filed anything against yet. */
  parents: { key: string; label: string }[];
};
export type Queue = {
  today: string; adopted: string | null;
  /** False when the sidecar index existed but did not parse — ages are stale
   *  and nothing was written. Shown, never swallowed. */
  index_ok: boolean;
  sections: Record<string, QueueSection>;
  counts: {
    visible: number; queued: number; blocked: number;
    archived: number; snoozed: number;
  };
};
export const QUEUE_ORDER = ["courses", "procertus", "projects", "misc"] as const;
/** What POST /api/queue/add answers with — where the line actually landed. */
export type QueueAdd = {
  ok: true; file: string; section: string; parent: string | null;
  raw: string; sha: string | null; created_note: boolean;
  /** filled in by the client: the line number the add landed on, once known */
  line?: number;
};
/** A reword suggestion. Every field is a proposal; nothing has been written. */
export type Reword = {
  title: string; section: string; parent: string | null;
  due: string | null; urgency: "high" | "medium" | "low";
};
/** null suggestion = the model answered with nothing usable. Not an error:
 *  the task is already filed and unchanged. */
export type RewordResp = { ok: true; suggestion: Reword | null };
/** One recorded 06:00 retrospective. `score` is null when nothing could be
 *  measured — not zero, which would claim the day was scored badly. */
export type ReviewRow = {
  date: string; score: number | null; weighted: number;
  by_section: Record<string, number>;
  components: { T?: number; A?: number; M?: number };
  deadlines_due: number; deadlines_met: number;
  advanced: number; courses: number;
  visible: number; queued: number;
};
export type ReviewResp = { latest: ReviewRow | null };

export type QueueEdit = {
  ok: true; file: string; section: string; parent: string | null;
  raw: string; id: string; moved: boolean; sha: string | null;
};

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

/** Exam mode (Phase 6). `date`/`days` are null when the source never stated
 *  one — blank is the honest answer, not zero. */
export type ExamUnit = {
  course: string; exam: string | null;
  date: string | null; days: number | null;
  status: string | null; file: string; title: string;
};
export type CourseCoverage = {
  course: string; sources: number; covered: number;
  by_folder: { folder: string; total: number; covered: number }[];
  uncovered_sample: string[]; uncovered_more: number;
};
export type Study = { exams: ExamUnit[]; coverage: CourseCoverage[] };

/** Repo awareness (Phase 6). `root` is derived from where hubs point. */
export type RepoRow = {
  name: string; path: string; branch: string | null;
  dirty: number | null; unpushed: number | null;
  last_commit: string | null; last_subject: string;
  idle_days: number | null;
  hub: string | null; hub_status: string | null; hub_stale_days: number | null;
};
export type Repos = {
  root: string | null; note?: string; repos: RepoRow[];
  orphans: string[]; stale_hubs: string[]; idle: string[]; stale_days: number;
};

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
  // The reword shells out to `claude -p`, a CLI cold start rather than an API
  // call — ~16s for a trivial prompt on this machine. Every other write is a
  // file and a commit and has no business taking 30s.
  const ms = path.startsWith("queue/reword") ? 140_000 : 30_000;
  const r = await fetch(`${API}/api/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(ms),
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
