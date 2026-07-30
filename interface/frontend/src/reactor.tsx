/**
 * The reactor (dashboard-plan §3, D1): the centrepiece that is both the agent
 * visual and the status bar. Four arc segments around a core, one per
 * specialist, with the five states:
 *
 *   idle      dim arc, the whole reactor breathing on a 4s pulse
 *   running   that arc bright with a linear sweep; elapsed counts up beneath
 *   held      amber — the specialist's last run raised proposals still waiting
 *             (an approximation until proposals carry their author, Phase 4)
 *   fault     red, matching the health glyph's turn
 *   degraded  hollow (dashed) — renderable now, but nothing can trigger it
 *             until Phase 4's model-degrade logic exists
 *
 * Motion rules from §2: no bounce, no spring. The sweep is linear, the idle
 * pulse is 4s, state changes cross-fade in 200ms via CSS transitions.
 */
import { useEffect, useState } from "react";
import type { Fleet, Progress } from "./api";
import { rel } from "./api";
import { Panel } from "./panels";

type ArcState = "idle" | "queued" | "running" | "ok" | "held" | "fault" | "degraded";

function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
  const rad = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
}

function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const [x0, y0] = polar(cx, cy, r, a0);
  const [x1, y1] = polar(cx, cy, r, a1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

export function useElapsed(sinceIso: string | null): number | null {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!sinceIso) return;
    const t = setInterval(() => tick(n => n + 1), 1000);
    return () => clearInterval(t);
  }, [sinceIso]);
  if (!sinceIso) return null;
  const s = Math.floor((Date.now() - new Date(sinceIso).getTime()) / 1000);
  return s >= 0 ? s : null;
}

/** A progress record is only believed while its heartbeat is fresh: a crashed
 *  run leaves state:"running" on disk forever, and the reactor must not sweep
 *  for a ghost. fleet.py rewrites `updated` at every transition. */
export function progressFresh(progress: Progress | null): boolean {
  if (!progress) return false;
  const age = Date.now() - Date.parse(progress.updated);
  return !isNaN(age) && age < 30 * 60_000;   // > the longest specialist timeout
}

export default function Reactor({ fleet, progress, waitingCount }: {
  fleet: Fleet | null;
  progress: Progress | null;
  waitingCount: number;
}) {
  const fresh = progressFresh(progress);
  const stalled = progress?.state === "running" && !fresh;
  const running = progress?.state === "running" && fresh;
  const paused = progress?.state === "paused";
  const degraded = !!progress?.degraded && (running || paused);
  const elapsed = useElapsed(running ? progress!.current_started : null);

  if (!fleet) return <Panel label="FLEET" className="center"><p className="dim">loading…</p></Panel>;
  if (fleet.specialists.length === 0) {
    return <Panel label="FLEET" className="center">
      <p className="warn-line">no specialists configured — check specialists.py</p>
    </Panel>;
  }

  const stateOf = (key: string): ArcState => {
    if (running) {
      if (progress!.current === key) return "running";
      const r = progress!.results[key];
      if (r) return r.ok ? "ok" : "fault";
      if (progress!.queue.includes(key)) return "queued";
    }
    const spec = fleet.specialists.find(s => s.key === key);
    if (spec?.last_result && spec.last_result !== "ok") return "fault";
    // Held is the loop-stalled-on-you state. Until proposals record which
    // specialist raised them, approximate: this one raised proposals last run
    // AND something is still PENDING (approved/staged no longer count — they
    // kept every arc amber long after Zach had acted).
    if ((spec?.last_proposals ?? 0) > 0 && waitingCount > 0) return "held";
    return "idle";
  };

  const keys = fleet.specialists.map(s => s.key);
  const span = 360 / keys.length;         // arc + gap per specialist
  const gap = 18;

  const doneCount = running ? Object.keys(progress!.results).length : 0;
  const anyFault = fleet.specialists.some(s => s.last_result && s.last_result !== "ok");

  const lastRun = fleet.last_run ? `${rel(fleet.last_run)} ago` : "never";
  const sub = running
    ? `running ${Math.min(doneCount + 1, progress!.queue.length)} of ${progress!.queue.length}` +
      (elapsed != null ? ` · ${elapsed}s` : "")
    : `${fleet.specialists.length} specialists · last run ${lastRun}` +
      (fleet.task_installed ? " · scheduled daily 09:00" : " · NOT SCHEDULED");

  const coreWord = running ? progress!.current ?? "…"
    : paused ? "PAUSED"
    : stalled ? "STALLED"
    : fleet.stopped_early_at ? "STOPPED"
    : anyFault ? "FAULT" : "IDLE";

  return (
    <Panel label="FLEET" className="center">
      <div className={`reactor ${running ? "running" : "idle"}`}>
        <svg viewBox="0 0 260 260" className="reactor-svg" role="img"
             aria-label={`fleet ${coreWord.toLowerCase()}`}>
          {keys.map((key, i) => {
            const st = stateOf(key);
            const a0 = i * span + gap / 2;
            const a1 = (i + 1) * span - gap / 2;
            const mid = (a0 + a1) / 2;
            const [lx, ly] = polar(130, 130, 116, mid);
            return (
              // `hollow` renders the §8 degrade visibly: still working, but on
              // Haiku — a quality drop you can see while it happens.
              <g key={key} className={`arc ${st} ${degraded ? "hollow" : ""}`}>
                <path d={arcPath(130, 130, 92, a0, a1)} className="arc-base" />
                {st === "running" && (
                  <path d={arcPath(130, 130, 92, a0, a1)} className="arc-sweep"
                        pathLength={100} />
                )}
                <text x={lx} y={ly} className="arc-label"
                      textAnchor="middle" dominantBaseline="middle">{key}</text>
              </g>
            );
          })}
          <circle cx={130} cy={130} r={40} className="core-ring" />
          <text x={130} y={126} className="core-word"
                textAnchor="middle" dominantBaseline="middle">{coreWord}</text>
          <text x={130} y={146} className="core-sub"
                textAnchor="middle" dominantBaseline="middle">
            {running && elapsed != null ? `${elapsed}s` : "Σ"}
          </text>
        </svg>
        <div className="center-sub">{sub}</div>
        {progress?.state === "done" && progress.note && !running && (
          <div className="dim center-note">last scheduled pass: {progress.note} ({rel(progress.finished)} ago)</div>
        )}
        {degraded && running && (
          <p className="warn-line">running degraded (haiku) — the window is tight</p>
        )}
        {paused && (
          <p className="warn-line">paused — window exhausted
            {progress?.resume_at ? ` · resumes ~${progress.resume_at.slice(11, 16)}` : ""};
            the rest stay due</p>
        )}
        {stalled && (
          <p className="warn-line">a run reported "running" but its heartbeat is stale —
            it likely crashed; check fleet.log</p>
        )}
        {!paused && fleet.stopped_early_at && (
          <p className="warn-line">last run stopped early on a rate limit ({rel(fleet.stopped_early_at)} ago)</p>
        )}
      </div>
    </Panel>
  );
}

/** The foot's live line — what is happening this second. */
export function activityLine(fleet: Fleet | null, progress: Progress | null,
                             elapsed: number | null): { text: string; live: boolean } {
  if (progress?.state === "running" && !progressFresh(progress)) {
    return { text: "⚠ a fleet run went quiet mid-flight — check fleet.log", live: false };
  }
  if (progress?.state === "running" && progress.current) {
    return {
      text: `⟳ ${progress.current} · running${elapsed != null ? ` · ${elapsed}s` : ""}` +
            ` (${progress.current_model ?? "?"})`,
      live: true,
    };
  }
  if (progress?.state === "paused") {
    return {
      text: `⏸ paused — window exhausted` +
            (progress.resume_at ? ` · resumes ~${progress.resume_at.slice(11, 16)}` : ""),
      live: false,
    };
  }
  if (progress?.state === "done") {
    const bits = Object.entries(progress.results)
      .map(([k, r]) => `${k} ${r.ok ? "✓" : "✗"}` +
                       (r.applied ? ` ✎${r.applied}` : r.proposals ? ` +${r.proposals}` : "") +
                       (r.held ? ` ⚠${r.held} held` : ""));
    return {
      text: `last run ${rel(progress.finished)} ago · ` +
            (bits.length ? bits.join(" · ") : progress.note ?? "done") +
            (progress.stopped_early ? " · stopped early" : ""),
      live: false,
    };
  }
  return { text: fleet
    ? `idle · last run ${fleet.last_run ? rel(fleet.last_run) + " ago" : "never"}`
    : "…", live: false };
}
