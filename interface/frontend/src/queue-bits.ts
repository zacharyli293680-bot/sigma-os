/**
 * queue-bits.ts — what the work view and the QUEUE digest both need.
 *
 * Two views render the same queue at two sizes: the WK view's four cards, and
 * the right column's digest. The chips, the score breakdown and the completion
 * flow must be identical between them — a task that reads `due Fri` in one
 * place and `08-07` in the other is two systems wearing one name.
 *
 * Its own module rather than exports hanging off work.tsx: oxlint's
 * `only-export-components` rule is right that a file mixing components and
 * shared helpers breaks fast refresh, and reactor.tsx already carries three
 * warnings for doing it the other way.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, post } from "./api";
import type { QueueSection, QueueTask } from "./api";

export type Chip = { label: string; tone: string };

const WEEKDAY = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** How long a completed row spends leaving before the refetch swaps the list.
 *  Matches the shell's own --dive-ms so the whole dashboard moves at one speed. */
export const LEAVE_MS = 240;

/**
 * Deadline, aging and urgency, as words that carry their own meaning.
 *
 * Status is never colour alone in this dashboard: `overdue 3d` says what it is
 * with the stylesheet switched off, and the tone only sharpens it.
 */
export function chipsFor(t: QueueTask): Chip[] {
  const out: Chip[] = [];
  const d = t.parts.days_until;
  if (t.deadline && d !== null) {
    if (d < 0) out.push({ label: `overdue ${-d}d`, tone: "danger" });
    else if (d === 0) out.push({ label: "due today", tone: "danger" });
    else if (d === 1) out.push({ label: "due tomorrow", tone: "warn" });
    else if (d <= 6) {
      // Date-only parsing, so this is a local calendar day rather than UTC —
      // `new Date("2026-08-07")` is midnight UTC and lands on the 6th in any
      // negative offset, which would print the wrong weekday half the year.
      const [y, m, day] = t.deadline.split("-").map(Number);
      out.push({ label: `due ${WEEKDAY[new Date(y, m - 1, day).getDay()]}`, tone: "warn" });
    } else out.push({ label: `due ${t.deadline.slice(5)}`, tone: "dim" });
  }
  // The anti-starvation term, surfaced: this is *why* a long-ignored task with
  // no deadline eventually climbs into the visible window.
  if (t.parts.aging >= 1) {
    out.push({ label: `aging ${t.parts.age_days}d`, tone: "age" });
  }
  if (t.urgency === "high") out.push({ label: "high", tone: "urg" });
  else if (t.urgency === "low") out.push({ label: "low", tone: "dim" });
  return out;
}

/** The tooltip that answers "why is this here?". */
export function breakdown(t: QueueTask): string {
  const p = t.parts;
  const bits: string[] = [];
  if (p.deadline) {
    const d = p.days_until;
    bits.push(`${d !== null && d < 0 ? "overdue" : "due"} +${Math.round(p.deadline)}`);
  }
  bits.push(`${t.urgency} +${Math.round(p.urgency)}`);
  if (p.aging) bits.push(`age +${Math.round(p.aging)}`);
  const math = `${bits.join(" · ")} = ${Math.round(p.total)}`;
  return t.pinned ? `pinned — shown regardless of score\n(${math})` : math;
}

/** "Block 3 of 10 · next: strong induction notes" for a course or project head. */
export function progressOf(s: QueueSection, t: QueueTask): string | null {
  // File-aware since study S2: a course can hold several groups (timeline
  // chain, guide chain, flat tasks), and the old first-by-parent match handed
  // the guide head the timeline's "next:" line. A head whose own file is in
  // no group has no sequence to report progress in.
  const g = s.groups.find(x => x.parent === t.parent
    && x.chain.some(c => c.file === t.file));
  if (!g) return null;
  if (s.key === "projects") {
    const behind = g.open - 1;
    return behind > 0 ? `${behind} queued behind` : null;
  }
  const headings: string[] = [];
  for (const c of g.chain) {
    if (c.heading && headings[headings.length - 1] !== c.heading) headings.push(c.heading);
  }
  const at = t.heading ? headings.indexOf(t.heading) + 1 : 0;
  // The heading is a full block title ("Block 3 — Distributed Loads"); the
  // leading label alone is what fits on a sub-line.
  const label = (t.heading ?? "").split("—")[0].trim() || `${at}`;
  const next = g.chain[1];
  const where = at && headings.length ? `${label} of ${headings.length}` : label;
  if (!next) return where || null;
  // Elide rather than cut: "…examples; packet" reads as a finished phrase and
  // silently misstates what the next task is.
  const peek = next.text.length > 46 ? `${next.text.slice(0, 45).trimEnd()}…` : next.text;
  return `${where} · next: ${peek}`;
}

export type RowState = Record<string, "busy" | "leaving">;

/**
 * Completing a task, as a queue rather than a list.
 *
 * Optimistic with rollback, the same contract the Today panel already used:
 * `raw` is the staleness token, and a 409 `stale` means the note moved
 * underneath us — so
 * the list is what must change, not the note. The only addition is the pause
 * between the write landing and the refetch, which is what makes the row
 * *leave* rather than blink out of existence.
 */
export function useQueueTick(onMutate: () => void) {
  const [rows, setRows] = useState<RowState>({});
  const [errs, setErrs] = useState<Record<string, string>>({});
  // A refetch that lands after unmount would set state on a dead component,
  // and the 60s poll makes that a routine race rather than a rare one.
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const tick = useCallback(async (t: QueueTask) => {
    if (rows[t.id]) return;
    setRows(s => ({ ...s, [t.id]: "busy" }));
    setErrs(({ [t.id]: _drop, ...rest }) => rest);
    try {
      await post("tasks/toggle", { file: t.file, line: t.line, raw: t.raw, done: true });
      if (!alive.current) return;
      setRows(s => ({ ...s, [t.id]: "leaving" }));
      setTimeout(() => {
        if (!alive.current) return;
        onMutate();
        // Cleared after the refetch is asked for, not before: dropping the
        // class first would snap the row back to full opacity for a frame.
        setRows(({ [t.id]: _gone, ...rest }) => rest);
      }, LEAVE_MS);
    } catch (e) {
      if (!alive.current) return;
      setRows(({ [t.id]: _drop, ...rest }) => rest);
      const msg = e instanceof ApiError
        ? (e.code === "stale" ? "the note changed — list refreshed" : e.detail || e.code)
        : "backend unreachable";
      setErrs(prev => ({ ...prev, [t.id]: msg }));
      if (e instanceof ApiError && e.code === "stale") onMutate();
    }
  }, [rows, onMutate]);

  return { rows, errs, tick };
}

/**
 * Which ids appeared since the last payload — the tasks that were *promoted*.
 *
 * Animating every row on arrival would mean the whole dashboard twitches every
 * 60 seconds when the poll returns the same list. Only a genuine promotion
 * should move, so the first payload primes the set and animates nothing.
 *
 * Tracked in an effect rather than during render because StrictMode invokes
 * render twice: the first pass would mark everything as seen and the second
 * would find nothing new.
 */
export function useFreshIds(ids: string[]): Set<string> {
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const key = ids.join("|");

  useEffect(() => {
    const now = new Set(ids);
    if (seen.current === null) {          // first payload primes, never animates
      seen.current = now;
      return;
    }
    const added = ids.filter(i => !seen.current!.has(i));
    seen.current = now;
    if (!added.length) {
      setFresh(f => (f.size ? new Set() : f));
      return;
    }
    setFresh(new Set(added));
    const t = setTimeout(() => setFresh(new Set()), 600);
    return () => clearTimeout(t);
    // `key` is the payload identity; `ids` is rebuilt every render and would
    // re-run this forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return fresh;
}
