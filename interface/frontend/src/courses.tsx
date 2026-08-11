/**
 * courses.tsx — the study dashboard's landing view: one card per active course.
 *
 * This was a `flex-wrap` row of chips inside a 1240px dialog, where a course
 * mid-chain and a course with nothing at all differed by a `0/3`. A course is
 * the unit of study-mode work, so it gets the unit of the dashboard's own visual
 * vocabulary: a `.panel` — an uppercase label with a hairline running off it,
 * unframed, because a frame here means *something is on you* and is spent on
 * the one card with a held module.
 *
 * **Every number on a card already existed in the payload.** `GET /api/courses`
 * has carried guide progress, the measured pace, the projection and the open
 * recall count since S8; the chain view spent them on three stacked `·`-joined
 * lines. Nothing new is fetched — it is the same facts, given a shape.
 *
 * **The add tile is the point of the grid.** A course has always been a folder
 * on disk, which made starting one the only thing in this system you had to
 * leave the interface for — and made "no active courses" a dead end. The tile
 * expands in place rather than opening a dialog: it is three fields, and a
 * modal over a modal is a worse answer than a card that grows.
 */
import { useEffect, useRef, useState } from "react";
import { ApiError, obsidianHref, post } from "./api";
import type { CourseAdded, CourseRow, Courses, GuideProgress } from "./api";

/** The contract's own naming rule (`CLAUDE.md` §Naming), checked here so the
 *  button can stay disabled rather than the server having to say no. The
 *  endpoint validates it again — this is a courtesy, not the guard. */
const CODE_RE = /^[A-Z]{2,4}-\d{3}$/;

/** A generation run counts as live for the same 30 minutes the chain view uses.
 *  A stale `running` record is a crashed run, not a running one. */
const GEN_FRESH_MS = 30 * 60_000;

function liveGen(gen: GuideProgress | null | undefined, code: string) {
  if (!gen || gen.course !== code || gen.state !== "running") return null;
  const age = gen.updated ? Date.now() - Date.parse(gen.updated) : NaN;
  return !isNaN(age) && age < GEN_FRESH_MS ? gen : null;
}

function hours(mins: number) {
  return `${(mins / 60).toFixed(1)} h`;
}

/* ------------------------------------------------------------- one course */

function CourseCard({ c, gen, busy, onOpen, onGenerate }: {
  c: CourseRow;
  gen: GuideProgress | null | undefined;
  busy: boolean;
  onOpen: () => void;
  onGenerate: () => void;
}) {
  const g = c.guide;
  const total = g?.total ?? 0;
  const open = total - (g?.done ?? 0) - (g?.skipped ?? 0);
  const pct = total ? Math.round(((g?.done ?? 0) / total) * 100) : 0;
  const running = liveGen(gen, c.course);

  // Fragments, omitted when empty rather than zeroed — the dashboard's rule
  // everywhere else, and the reason a quiet course reads as quiet.
  const meta: string[] = [];
  if (g?.frontier) meta.push(`next: ${g.frontier.label ?? "the frontier"}`);
  if (!g && c.modules > 0) meta.push(`${c.modules} module${c.modules === 1 ? "" : "s"} unchained`);
  if (!g && c.modules === 0 && c.timeline) meta.push("tracked by a timeline");
  if (c.projected.open > 0) {
    meta.push(c.projected.minutes
      ? `~${hours(c.projected.minutes)} left at your pace`
      : `${hours(c.projected.estimate)} estimated`);
  }
  if (c.recall.open > 0) meta.push(`${c.recall.open} recall`);

  // The one verb, chosen by state — the chain view's own five branches, moved
  // to where you decide which course to spend the evening on.
  let action: React.ReactNode = null;
  if (running) {
    const done = Object.keys(running.results ?? {}).length;
    const queued = running.queue?.length ?? 0;
    action = <span className="wb-gen live">▣ generating — {running.current ?? "…"}
      {queued ? ` · ${Math.min(done + 1, queued)} of ${queued}` : ""}</span>;
  } else if (!g || !g.blueprint) {
    action = (
      <button className="wb-btn" disabled={busy} onClick={onGenerate}
              title="one model call — plans modules from this course's notes as a draft blueprint; nothing else runs until you approve it">
        ✎ Draft a plan
      </button>
    );
  } else if (g.blueprint === "draft") {
    action = <span className="dim">plan drafted — approve it to generate</span>;
  } else if (g.blueprint === "approved" && (g.missing ?? 0) > 0) {
    action = (
      <button className="wb-btn" disabled={busy} onClick={onGenerate}
              title="authors the plan's missing notes — one revertible commit each; anything held surfaces in WAITING ON YOU">
        ▶ Generate {g.missing} missing
        {gen?.course === c.course && gen?.state === "paused" ? " (resume)" : ""}
      </button>
    );
  } else if (g.blueprint !== "approved") {
    action = <span className="dim">plan status is {g.blueprint}</span>;
  } else if (open > 0) {
    // The one filled control on the card, and the only one on most of this
    // screen: §1.2's single primary action, which is also what the accent is
    // for. Everything else here is a hairline button or a sentence.
    action = (
      <button className="wb-btn wb-btn-primary" onClick={onOpen}
              title="open the chain">
        Study<span aria-hidden="true"> →</span>
      </button>
    );
  } else if (total > 0) {
    action = <span className="dim">✓ chain complete</span>;
  }

  return (
    <section className={`panel wb-card ${c.held ? "attn" : ""}`}>
      <h2>
        {/* No `◇`. The diamond is the dashboard's mark for "a place in the
            instrument", and the study room is the one place that is not it. */}
        <button className="wb-card-open" onClick={onOpen}
                title={`open ${c.course}`}>{c.course}</button>
        <span className="wb-card-count">
          {total > 0 ? `${g!.done}/${total}` : g?.planned ? `${g.planned} planned` : ""}
        </span>
      </h2>
      {c.name && c.name !== c.course && <p className="wb-card-name">{c.name}</p>}

      {total > 0 && (
        <p className="wb-card-bar">
          <span className="cov-bar" role="img"
                aria-label={`${g!.done} of ${total} modules done`}>
            <span className="cov-fill" style={{ width: `${pct}%` }} />
          </span>
          <span className="dim">
            {open} open{g!.skipped ? ` · ${g!.skipped} skipped` : ""}
          </span>
        </p>
      )}

      {meta.length > 0 && <p className="wb-card-meta dim">{meta.join(" · ")}</p>}
      {/* A held module is the one thing on this screen that is genuinely on
          you, so it is the one thing that gets a frame and a word. */}
      {c.held > 0 && (
        <p className="wb-card-meta wb-card-warn">
          ⚠ {c.held} module{c.held === 1 ? "" : "s"} held — fails the grammar
        </p>
      )}
      {/* A folder with no course-index note still counts as active, so it can
          only be named, never hidden. Reachable by hand, and by undoing an add
          — git does not track the empty subfolders the create left behind. */}
      {!c.indexed && (
        <p className="wb-card-meta wb-card-warn">
          ⚠ no course-index note — the folder counts as active without one
        </p>
      )}
      {action && <p className="wb-card-act">{action}</p>}
    </section>
  );
}

/* ---------------------------------------------------------- the add tile */

function AddCourse({ vault, onAdded }: {
  vault: string; onAdded: (code: string) => void;
}) {
  const [openForm, setOpenForm] = useState(false);
  const [code, setCode] = useState("");
  // Required, unlike `term`: `active_courses` falls back to the folder name, so
  // a nameless course renders as its own code twice and has told you nothing.
  const [name, setName] = useState("");
  const [term, setTerm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [said, setSaid] = useState<CourseAdded | null>(null);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => { if (openForm) first.current?.focus(); }, [openForm]);

  const valid = CODE_RE.test(code) && name.trim().length > 0;

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (!valid || busy) return;
    setBusy(true); setErr(null); setSaid(null);
    try {
      const r = await post<CourseAdded>("courses/add", { code, name, term });
      setSaid(r);
      setCode(""); setName(""); setTerm("");
      onAdded(r.course);
      first.current?.focus();
    } catch (e2) {
      // The server's own word, never a generic message — a refusal here is
      // usually "that course already exists", which is actionable.
      setErr(e2 instanceof ApiError ? (e2.detail || e2.code) : "backend unreachable");
    } finally {
      setBusy(false);
    }
  }

  if (!openForm) {
    return (
      <button className="wb-card-new" onClick={() => setOpenForm(true)}
              title="create the folder and its course-index note — one commit, one ledger row, one click to undo">
        + add course
      </button>
    );
  }

  return (
    <section className="panel wb-card wb-card-adding">
      <h2>
        + NEW COURSE
        <button className="ghost wb-card-x" title="cancel"
                onClick={() => { setOpenForm(false); setErr(null); }}>✕</button>
      </h2>
      <form className="wb-addform" onSubmit={submit}>
        <input ref={first} className="wb-add-code" value={code} spellCheck={false}
               placeholder="CSE-421" aria-label="course code"
               onChange={e => { setCode(e.target.value.toUpperCase()); setErr(null); }} />
        <input className="wb-add-name" value={name} placeholder="course name"
               aria-label="course name" onChange={e => setName(e.target.value)} />
        <input className="wb-add-term" value={term} placeholder="term (optional)"
               aria-label="term" onChange={e => setTerm(e.target.value)} />
        <button className="q-add-go" disabled={busy || !valid}>
          {busy ? "…" : "Add"}
        </button>
      </form>
      {/* The shape is a hint, not an error: nothing has gone wrong yet, and a
          field that turns red while you are still typing into it is lying. */}
      {!CODE_RE.test(code) && code.length > 0 && !err && (
        <p className="wb-said dim">a course code is <code>&lt;DEPT&gt;-&lt;NUMBER&gt;</code> — CSE-421, AA-210</p>
      )}
      {err && <p className="wb-said err">{err}</p>}
      {said && !err && (
        <p className="wb-said">
          ✓ {said.course} ·{" "}
          <a href={obsidianHref(vault, said.file.replace(/\.md$/, ""))}
             title={said.file}>{said.file.split("/").pop()}</a>
          {" "}· {said.subfolders.join(", ")} created
          <br />
          <span className="dim">{said.groupings}</span>
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------- the grid */

export default function CoursesGrid({ vault, courses, guideProg, busy,
                                      onOpen, onGenerate, onAdded }: {
  vault: string;
  courses: Courses | null | undefined;
  guideProg?: GuideProgress | null;
  busy: boolean;
  onOpen: (code: string) => void;
  onGenerate: (code: string) => void;
  onAdded: (code: string) => void;
}) {
  const empty = courses !== null && courses !== undefined
    && courses.courses.length === 0;

  return (
    <div className="wb-chain">
      {courses === undefined && <p className="dim pad">reading…</p>}
      {courses === null && <p className="err pad">backend unreachable</p>}

      {/* §3.3 — a course grid with no courses in it is a first screen, and a
          first screen is the most designed one in the product. It used to be
          the add tile with a dim sentence trailing under it, which reads as an
          error state for something that is simply new. Only rendered at zero,
          so the grid you actually use cannot be affected by it. */}
      {empty && (
        <div className="wb-empty">
          <span className="empty-mark" aria-hidden="true">◇</span>
          <h2>No courses yet.</h2>
          <p>
            A course is a folder under <code>02-Areas/Academics/</code> with a
            course-index note in it. Starting one here writes both, as a single
            commit you can undo.
          </p>
          <AddCourse vault={vault} onAdded={onAdded} />
        </div>
      )}

      {courses && !empty && (
        <div className="wb-grid">
          {courses.courses.map(c => (
            <CourseCard key={c.course} c={c} gen={guideProg} busy={busy}
                        onOpen={() => onOpen(c.course)}
                        onGenerate={() => onGenerate(c.course)} />
          ))}
          {/* Last, and never the only thing here — the empty case above owns
              that, because a lone tile on a blank grid says nothing about what
              a course even is. */}
          <AddCourse vault={vault} onAdded={onAdded} />
        </div>
      )}
    </div>
  );
}
