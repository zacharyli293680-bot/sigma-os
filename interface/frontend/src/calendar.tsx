/**
 * calendar.tsx — the calendar strip (Phase 6).
 *
 * `dashboard-vision`: "exams, due dates, milestones, and internship commitments
 * on one line."
 *
 * No new endpoint. Everything dated in this vault is already a task with a
 * `📅` date, and `/api/tasks` has been serving those since Phase 0 — exam
 * milestones, ProCertus commitments and assignment deadlines all arrive through
 * the same door. Adding a second source would have meant two things that could
 * disagree about what is due.
 *
 * Fourteen days, because the strip is for "what is coming", not a planner. What
 * falls off the end is counted rather than silently dropped.
 */
import type { Tasks, VaultTask } from "./api";

const DAYS = 14;

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function CalendarStrip({ tasks, onOpen }: {
  tasks: Tasks | null; onOpen: () => void;
}) {
  if (!tasks) return null;

  const today = new Date();
  const days: { key: string; d: Date; items: VaultTask[] }[] = [];
  for (let i = 0; i < DAYS; i++) {
    const d = new Date(today);
    d.setDate(today.getDate() + i);
    const key = iso(d);
    days.push({ key, d, items: tasks.tasks.filter(t => t.due === key) });
  }
  const horizon = days[days.length - 1].key;
  const overdue = tasks.tasks.filter(t => t.overdue);
  const beyond = tasks.tasks.filter(t => !t.overdue && t.due > horizon).length;

  return (
    <section className="calstrip">
      <h2>NEXT {DAYS} DAYS</h2>
      <div className="cal-days">
        {overdue.length > 0 && (
          <button className="cal-day overdue" onClick={onOpen}
                  title={overdue.map(t => `${t.due} ${t.text}`).join("\n")}>
            <span className="cal-dow">past</span>
            <span className="cal-num">!</span>
            <span className="cal-dots">{overdue.length}</span>
          </button>
        )}
        {days.map(({ key, d, items }) => {
          const weekend = d.getDay() === 0 || d.getDay() === 6;
          const isToday = key === iso(today);
          return (
            <button key={key}
                    className={`cal-day ${isToday ? "today" : ""} ${weekend ? "weekend" : ""} ${items.length ? "has" : ""}`}
                    onClick={onOpen}
                    title={items.length
                      ? items.map(t => `${t.no_sync ? "⊘ " : ""}${t.text}`).join("\n")
                      : `${key} — nothing due`}>
              <span className="cal-dow">{d.toLocaleDateString(undefined, { weekday: "narrow" })}</span>
              <span className="cal-num">{d.getDate()}</span>
              {/* One dot per item, capped — the count is in the tooltip and
                  the dots are only a density cue. */}
              <span className="cal-dots">
                {items.length === 0 ? "" : items.length > 3 ? `${items.length}` : "•".repeat(items.length)}
              </span>
            </button>
          );
        })}
      </div>
      {beyond > 0 && <span className="cal-beyond dim">+{beyond} beyond</span>}
    </section>
  );
}
