/**
 * agenda-rail.tsx — the today rail (agenda subsystem, P3).
 *
 * Replaces `calendar.tsx`, the 14-day strip. Not a calendar: an instrument
 * strip, per the brief's §7 — a NOW marker with the next commitment and a
 * countdown, at most the next few items, a seven-cell density bar for the rest
 * of the week, and one number.
 *
 * **It reads /api/agenda, and nothing else does the arithmetic twice.** Every
 * occurrence here arrives from the one resolver with its provenance attached,
 * so the strip and the queue and the eventual full view cannot disagree about
 * what is due — which is the failure the old strip avoided by reading a single
 * endpoint, kept now by there being a single *resolver* instead.
 *
 * **The number feeds nothing.** Free-hours-left is displayed from P3 and acts
 * on nothing until P7, deliberately: three phases of checking it against a real
 * day before the queue is allowed to believe it.
 *
 * Clicking anything opens its note in Obsidian. In P4 that becomes the full
 * agenda view; until it exists, a click that opens the source beats a click
 * that does nothing.
 */
import type { Agenda, Occurrence } from "./api";
import { obsidianHref } from "./api";

/** The waking day. One constant, and the only policy in this file: "free" is
 *  measured against these bounds, not against 24h. */
const DAY_START = 8;
const DAY_END = 22;

const AHEAD = 3;            // how many upcoming items the strip will show
const WEEK = 7;

/** Glyphs separate what happens *to* you from what you *do* — the distinction
 *  the brief asks the rail to carry, and status is never colour alone here.
 *  Exported since the enterprise room: its Today card carries the same
 *  vocabulary, because two glyph sets for one calendar teaches two calendars. */
export const GLYPH: Record<Occurrence["kind"], string> = {
  event: "◆", rule: "▣", note: "▦", task: "☐", practice: "◎",
};

export function minutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function hhmm(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** "in 1h 18m" / "in 12m" / "now" — a countdown reads as a countdown. */
export function until(mins: number): string {
  if (mins <= 0) return "now";
  const h = Math.floor(mins / 60), m = mins % 60;
  return h ? `in ${h}h ${m}m` : `in ${m}m`;
}

/** Committed hours per day, from timed occurrences only.
 *
 *  Mirrors `agenda.committed_hours` deliberately rather than importing a number
 *  the endpoint does not send: an untimed occurrence contributes nothing rather
 *  than a guessed default, because a deadline is not an hour of work and
 *  inventing one would put a fabricated number under a real decision. */
function hoursByDay(occ: Occurrence[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const o of occ) {
    if (!o.start || !o.end) continue;
    const mins = minutes(o.end) - minutes(o.start);
    if (mins > 0) out[o.date] = (out[o.date] ?? 0) + mins / 60;
  }
  return out;
}

/** Everything the rail computes, computed once. Exported since the enterprise
 *  room: its Today card shows the same day, and the promise this file opens
 *  with — nothing else does the arithmetic twice — has to survive a second
 *  consumer. `limit` is the caller's window; the rail shows AHEAD, the
 *  enterprise card a row or two more. */
export function todayModel(agenda: Agenda, limit = AHEAD) {
  const now = new Date();
  const today = iso(now);
  const nowMin = now.getHours() * 60 + now.getMinutes();

  const todays = agenda.occurrences.filter(o => o.date === today);

  // Timed things still ahead, then untimed ones — an all-day item is not "next"
  // in the way a 15:30 appointment is, so it never wins the NOW slot.
  const ahead = todays
    .filter(o => o.start && minutes(o.start) >= nowMin)
    .sort((a, b) => minutes(a.start!) - minutes(b.start!));
  // A habit already met today drops out of "what is left". It stays on the
  // agenda view — the record of a day that was kept is worth seeing — but the
  // rail answers "what is still ahead", and a satisfied obligation is not.
  const untimed = todays.filter(o => !o.start && o.done !== true);
  // What already happened, for a view with room to show the day whole.
  const past = todays
    .filter(o => o.start && minutes(o.start) < nowMin)
    .sort((a, b) => minutes(a.start!) - minutes(b.start!));
  const next = ahead[0] ?? null;
  const upcoming = [...ahead, ...untimed].slice(0, limit);

  // Free hours *left*: the rest of the waking day, minus what is still ahead in
  // it. Past commitments are already spent and are not subtracted twice.
  const endMin = DAY_END * 60;
  const startMin = Math.max(nowMin, DAY_START * 60);
  const remaining = Math.max(0, endMin - startMin);
  const committedAhead = ahead.reduce((sum, o) => {
    if (!o.start || !o.end) return sum;
    const s = Math.max(minutes(o.start), startMin);
    const e = Math.min(minutes(o.end), endMin);
    return e > s ? sum + (e - s) : sum;
  }, 0);
  const freeLeft = Math.max(0, (remaining - committedAhead) / 60);

  // The week bar: height is committed hours, so an exam week looks heavy.
  const byDay = hoursByDay(agenda.occurrences);
  const week = Array.from({ length: WEEK }, (_, i) => {
    const d = new Date(now);
    d.setDate(now.getDate() + i);
    const key = iso(d);
    return { key, d, hours: byDay[key] ?? 0, count: agenda.occurrences.filter(o => o.date === key).length };
  });
  const peak = Math.max(1, ...week.map(w => w.hours));

  return { now, today, nowMin, past, ahead, untimed, next, upcoming,
           committedAhead, freeLeft, week, peak,
           dayStart: DAY_START, dayEnd: DAY_END };
}

export default function AgendaRail({ agenda, vault, onOpen }: {
  agenda: Agenda | null; vault: string; onOpen: () => void;
}) {
  if (!agenda) return null;

  const { now, today, nowMin, next, upcoming, committedAhead, freeLeft,
          week, peak } = todayModel(agenda);

  return (
    <section className="agrail">
      <h2>
        TODAY
        <span className="ag-now">NOW {hhmm(now)}</span>
        {/* No overdue chip, deliberately. §7 specifies NOW + the next few + the
            density bar + one number, and overdue work is what the queue panel
            exists to surface. Adding it here would also mean fetching a wide
            lookback window on a 60s poll to count things this strip is not
            about. If it turns out to belong here, it belongs in P4. */}
        {/* A line that failed to parse is surfaced, never swallowed. */}
        {agenda.problems.length > 0 && (
          <span className="ag-problem" title={agenda.problems
            .map(p => `${p.path}:${p.line} — ${p.why}\n  ${p.raw}`).join("\n")}>
            ⚠ {agenda.problems.length} unreadable
          </span>
        )}
      </h2>

      <div className="ag-body">
        <ol className="ag-next">
          {upcoming.length === 0 && (
            <li className="ag-empty dim">nothing left today</li>
          )}
          {upcoming.map(o => {
            const mins = o.start ? minutes(o.start) - nowMin : null;
            return (
              <li key={o.id} className={`ag-item ${o === next ? "is-next" : ""}`}>
                <a href={obsidianHref(vault, o.source.path)}
                   title={`${o.kind} · ${o.source.path}${o.source.line ? `:${o.source.line}` : ""}${
                     o.conflict ? `\n⚠ ${o.conflict.why}` : ""}`}>
                  <span className={`ag-glyph k-${o.kind}`}>{GLYPH[o.kind]}</span>
                  <span className="ag-when">
                    {o.start ? `${o.start}${o.end ? `–${o.end}` : "–"}` : "all day"}
                  </span>
                  <span className="ag-title">
                    {o.no_sync && <span className="ag-seal" title="never leaves this machine">⊘</span>}
                    {o.conflict && <span className="ag-conflict" title={o.conflict.why}>⚠</span>}
                    {o.title}
                  </span>
                  {/* A standing daily goal has no countdown — it is due by the
                      end of the day, not at a time — so the slot carries the
                      thing that is actually running out instead. */}
                  {o.kind === "practice" && o.done === false && (
                    <span className="ag-count dim">not yet</span>
                  )}
                  {mins !== null && <span className="ag-count">{until(mins)}</span>}
                </a>
              </li>
            );
          })}
        </ol>

        <div className="ag-right">
          <button className="ag-free" onClick={onOpen}
                  title={`the waking day is ${DAY_START}:00–${DAY_END}:00; `
                       + `${committedAhead / 60 >= 0.05 ? (committedAhead / 60).toFixed(1) : "0"}h `
                       + `of it is still committed`}>
            <span className="ag-free-n">{freeLeft.toFixed(1)}<em>h</em></span>
            <span className="ag-free-l">FREE LEFT</span>
          </button>
          <div className="ag-week" role="img"
               aria-label={`committed hours for the next ${WEEK} days`}>
            {week.map(w => (
              <button key={w.key} className={`ag-bar ${w.key === today ? "today" : ""}`}
                      onClick={onOpen}
                      title={`${w.key} — ${w.hours ? `${w.hours.toFixed(1)}h committed` : "nothing timed"}`
                           + `${w.count ? `, ${w.count} item(s)` : ""}`}>
                <span className="ag-bar-fill" style={{ height: `${Math.round((w.hours / peak) * 100)}%` }} />
                <span className="ag-bar-d">{w.d.toLocaleDateString(undefined, { weekday: "narrow" })}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
