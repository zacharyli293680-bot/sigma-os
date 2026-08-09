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

/** One thing on the calendar, from the one resolver (agenda.py).
 *
 *  `source` is never empty — provenance is an invariant, which is what lets any
 *  occurrence answer "why is this here" the way a queue row answers "why is
 *  this ranked here". `raw` is the exact line, and it is the staleness token
 *  the write path will hand back in P5. */
export type OccurrenceKind = "task" | "note" | "event" | "rule" | "practice";
export type Occurrence = {
  kind: OccurrenceKind;
  id: string;
  date: string;                  // YYYY-MM-DD
  /** Whether this day's obligation is met. Only a `practice` occurrence sets
   *  it; null everywhere else means "not that kind of thing" rather than "not
   *  done", which a bare false would have claimed of every event. */
  done: boolean | null;
  /** null when the thing has no clock time — it belongs in the all-day gutter. */
  start: string | null;          // HH:MM
  end: string | null;
  all_day: boolean;
  title: string;
  owner: string;
  raw: string | null;
  priority: number | null;
  source: {
    path: string; line: number | null;
    block_id: string | null; rule_id: string | null;
    /** "date" | "due" on a dated note — which key put it on this day. */
    field: string | null;
  };
  /** Set on every day of a multi-day event, so a bar knows which end it is. */
  span: { start: string; end: string; index: number; length: number } | null;
  section: string; parent: string | null;
  no_sync: boolean;              // gitignored but model-exempt — bronze, never hidden
  /** Two sources disagreeing about the same subject. Shown, never resolved. */
  conflict: { with: string[]; why: string } | null;
  /** The date it was cancelled, or null. A cancelled event keeps its line and
   *  keeps being emitted — it renders struck and stops counting toward
   *  committed hours. Nothing here deletes a record of something once true. */
  cancelled: string | null;
  /** One occurrence of a recurrence rule, removed by `except::` (agenda P6).
   *  Its own field rather than a value in `cancelled`: an event's `cancelled`
   *  is *the day you called it off*, and `except::` only records the day the
   *  thing would have happened. Both render struck; neither counts hours. */
  skipped: boolean;
  /** Client-only: an optimistic move is in flight for this occurrence. Never
   *  sent by the server; set by the view so a moved row can render as unsettled
   *  until its commit lands. */
  pending?: boolean;
};
/** A line that was meant to be an event and is not. Reported rather than
 *  dropped: an occurrence that silently stopped existing is this subsystem's
 *  worst failure, because nothing about the calendar looks wrong. */
export type AgendaProblem = { path: string; line: number; raw: string; why: string };
export type Agenda = {
  from: string; to: string; today: string;
  occurrences: Occurrence[];
  conflicts: number;
  problems: AgendaProblem[];
  /** The vault's single declaration, from schedule.md. null until one exists. */
  timezone: string | null;
};

/** What POST /api/agenda/add and /edit answer with — where the line landed and
 *  what it now reads, so the client can verify rather than assume. */
export type AgendaWrite = {
  ok: true; file: string; raw: string; sha: string | null;
  created_note?: boolean; moved?: boolean;
  /** Carries a failed pull: "committed locally but never reached the remote". */
  note?: string | null;
};

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
  /** Which kind of sequence document. A course can run both, one head each —
   *  the guide head takes its own window slot and renders marked (study S2). */
  chain_kind: "timeline" | "guide" | null;
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
/** One solved problem, as the log note records it. */
export type Rep = {
  n: number; title: string; difficulty: string; topics: string[];
  date: string; revisit: number;
};
/** The daily habit that is deliberately *not* one of the four queues: it has no
 *  checkbox to tick, so it has none of their affordances. A sibling of
 *  `sections`, never a fifth member of it. Null when the vault has no log note. */
export type Practice = {
  habit: string; title: string; file: string; goal: string;
  started: string | null;
  done: boolean;
  today: Rep[];
  streak: { current: number; longest: number; last: string | null; at_risk: boolean };
  total: number;
  by_difficulty: Record<string, number>;
  /** The last few solves *before* today, newest first — `today` above already
   *  holds today's, and overlapping them would render the same solve twice.
   *  Capped by the backend because this rides on the queue poll: the log note
   *  is the full record, this is the glance at it. */
  recent: Rep[];
  /** Exactly the number the 06:00 review will use for today — read from the
   *  same function, never recomputed, so the card cannot preview a score the
   *  review will disagree with. */
  component: number | null;
};
export type Queue = {
  today: string; adopted: string | null;
  /** False when the sidecar index existed but did not parse — ages are stale
   *  and nothing was written. Shown, never swallowed. */
  index_ok: boolean;
  sections: Record<string, QueueSection>;
  practice: Practice | null;
  counts: {
    visible: number; queued: number; blocked: number;
    archived: number; snoozed: number;
  };
};
/** What POST /api/practice/log answers with. `duplicate` rides on the 409. */
export type PracticeLog = {
  ok: true; n: number; date: string; line: string; file: string;
  title: string; difficulty: string; topics: string[];
  sha: string | null; revisit: number; practice: Practice | null;
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
  components: { T?: number; A?: number; M?: number; P?: number };
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

/** Study mode S1: a guide module, parsed by runtime/lesson.py. The workbench
 *  renders exactly what the one grammar owner hands over — problems non-empty
 *  means the module is held, and the view says so rather than rendering it. */
export type LessonSource = { path: string; line: number };
export type PracticeItem = {
  id: string; kind: string; line: number; prompt: string;
  hints: string[]; answer: string | null; solution: string | null;
  source: string | null;
};
export type LessonSegment = {
  n: number; title: string; minutes: number; line: number;
  sources: LessonSource[];
  summary: string; normal: string; in_depth: string; example: string | null;
  practice: PracticeItem[];
};
export type Lesson = {
  type: string; course: string; module: number | null; unit: number | null;
  title: string; estimate: number | null; verified: string | null; tags: string;
  sources: string[]; preamble: string; segments: LessonSegment[];
  problems: string[]; file: string;
  /** The sidecar's resume state, riding along so a reopened module lands where
   *  it was. Loosely typed on purpose — the workbench validates on hydrate. */
  state: LessonState | null;
};
export type LessonListRow = {
  course: string; module: number | null; unit: number | null; title: string;
  estimate: number | null; file: string; segments: number; practice: number;
  problems: string[];
};
export type LessonList = { modules: LessonListRow[] };

/** What the workbench posts to /api/lesson/state — machine-local view state,
 *  never a commit. JSON object keys are strings, so the depth map is keyed by
 *  the segment index's string form. */
export type LessonState = {
  depth?: Record<string, string>;
  fallback?: string;
  practice?: Record<string, {
    hints?: number; revealed?: boolean;
    result?: "correct" | "wrong" | "skipped" | null; given?: string;
  }>;
};

/** Study S2: one row of a course's guide chain — `[-]` included, which the
 *  queue's scanner deliberately cannot see (that is what advances the
 *  frontier past a skip; the renderer shows the row dimmed instead). */
export type GuideRow = {
  line: number; raw: string;
  state: "open" | "done" | "skipped";
  text: string;
  target: string | null; label: string | null;
  skipped: string | null; date: string | null;
};
export type Guide = {
  course: string; file: string; rows: GuideRow[];
  total: number; done: number; skipped: number;
  frontier: GuideRow | null;
  /** The blueprint note's `status:` — draft | approved — or null without one. */
  blueprint: string | null;
};
/** GET /api/courses — every active course's study surface at a glance. */
export type CourseRow = {
  course: string; name: string; timeline: boolean;
  modules: number; held: number;
  guide: Omit<Guide, "rows"> | null;
};
export type Courses = { courses: CourseRow[] };
/** What POST /api/lesson/session-end answers with. `wrote: false` is the
 *  idempotent no-op — nothing uncovered since the last rollup. */
export type Rollup = {
  ok: true; rows: number; wrote: boolean;
  file?: string; raw?: string; sha?: string; digest?: string;
};

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

/** One tool call a fleet specialist made, tailed out of fleet.fire.jsonl by
 *  /api/fleet/fire. The brain fires on `detail`, which for a Read is the
 *  vault-relative path of the note — the same string the chat drawer's trail
 *  carries, produced by the same `describe()`. */
export type Fire = { specialist: string; tool: string; detail: string };
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
  /** The whole error payload. Some refusals carry structure the caller can act
   *  on rather than only print — a duplicate practice log returns the entry it
   *  collided with, which is what turns "already solved" from an error into
   *  "you did this on 2026-07-14, log it again?". */
  body?: Record<string, unknown>;
  constructor(status: number, code: string, detail?: string,
              body?: Record<string, unknown>) {
    super(detail || code);
    this.status = status; this.code = code; this.detail = detail;
    this.body = body;
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
  if (!r.ok) {
    throw new ApiError(r.status, data?.error ?? String(r.status), data?.detail,
                       data as Record<string, unknown> | undefined);
  }
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
