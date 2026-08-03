/**
 * agenda.tsx — the full calendar view (agenda subsystem, P4). Ctrl+' or rail CA.
 *
 * Read-only. Writing is P5, and shipping the view first is deliberate: a month
 * you can look at for a few days is what tells you whether the resolver is
 * right, and it costs nothing to find that out before anything can move a date.
 *
 * Three modes, because they answer different questions:
 *   **Week**   the workhorse — an hour grid, with an all-day gutter on top for
 *              everything dated but untimed (which is every task, since tasks
 *              carry no time).
 *   **Month**  density and horizon. An exam week should *look* heavy.
 *   **Agenda** the 14-day list — still the best scan of what is coming, and the
 *              shape the old strip was reaching for.
 *
 * **Every occurrence can say why it is here.** The `[?]` opens a provenance
 * strip naming the file, the line, and which key or rule put it on that day —
 * the same affordance the queue's score breakdown is, applied to dates. It sits
 * at the foot of the view rather than in a popover so there is one place to
 * look and nothing to position.
 *
 * Filtering reuses `section` from the resolver, which comes from
 * `todo.section_of`. A second classifier here would be a second answer to
 * "whose work is this".
 */
import { useEffect, useMemo, useState } from "react";
import { get, obsidianHref } from "./api";
import type { Agenda, Occurrence } from "./api";

type Mode = "week" | "month" | "agenda";

/** Weeks start Monday: the academic grammar this vault is full of is MWF and
 *  TR, and a Monday start keeps the weekend together instead of splitting it
 *  across both edges. One constant. */
const WEEK_START = 1;
/** The hour grid's default bounds. Anything outside them widens the grid rather
 *  than being clipped — an event at 06:00 must not simply vanish. */
const GRID_FROM = 8;
const GRID_TO = 22;
const AGENDA_DAYS = 14;
const MONTH_CELL_MAX = 3;      // items drawn per month cell before "+n"

const GLYPH: Record<Occurrence["kind"], string> = {
  event: "◆", rule: "▣", note: "▦", task: "☐",
};
const SECTIONS: [string, string][] = [
  ["all", "All"], ["courses", "Courses"], ["procertus", "ProCertus"],
  ["projects", "Projects"], ["misc", "Misc"],
];

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function addDays(d: Date, n: number): Date {
  const out = new Date(d); out.setDate(d.getDate() + n); return out;
}
function startOfWeek(d: Date): Date {
  const out = new Date(d);
  out.setDate(d.getDate() - ((d.getDay() - WEEK_START + 7) % 7));
  return out;
}
function minutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

/** The window each mode needs. Month is padded to whole weeks so the grid has
 *  no ragged first and last row. */
function range(mode: Mode, anchor: Date): { from: Date; to: Date } {
  if (mode === "week") {
    const from = startOfWeek(anchor);
    return { from, to: addDays(from, 6) };
  }
  if (mode === "agenda") return { from: anchor, to: addDays(anchor, AGENDA_DAYS - 1) };
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
  const from = startOfWeek(first);
  const to = addDays(startOfWeek(last), 6);
  return { from, to };
}

function Marks({ o }: { o: Occurrence }) {
  return (
    <>
      {o.no_sync && <span className="ag-seal" title="never leaves this machine">⊘</span>}
      {o.conflict && <span className="ag-conflict" title={o.conflict.why}>⚠</span>}
    </>
  );
}

export default function AgendaView({ open, vault, onClose }: {
  open: boolean; vault: string; onClose: () => void;
}) {
  const [mode, setMode] = useState<Mode>("week");
  const [anchor, setAnchor] = useState(() => new Date());
  const [section, setSection] = useState("all");
  const [probe, setProbe] = useState<Occurrence | null>(null);
  const [d, setD] = useState<Agenda | null | undefined>(undefined);

  const { from, to } = useMemo(() => range(mode, anchor), [mode, anchor]);
  const fromISO = iso(from), toISO = iso(to);

  useEffect(() => {
    if (!open) return;
    setD(undefined);
    get<Agenda>(`agenda?from=${fromISO}&to=${toISO}`).then(setD).catch(() => setD(null));
  }, [open, fromISO, toISO]);

  // Reopening should land on today rather than wherever it was left weeks ago.
  useEffect(() => { if (open) { setAnchor(new Date()); setProbe(null); } }, [open]);

  const shown = useMemo(
    () => (d?.occurrences ?? []).filter(o => section === "all" || o.section === section),
    [d, section]);

  if (!open) return null;

  const step = (n: number) => setAnchor(a =>
    mode === "month" ? new Date(a.getFullYear(), a.getMonth() + n, 1)
      : addDays(a, n * (mode === "week" ? 7 : AGENDA_DAYS)));

  const today = iso(new Date());
  const label = mode === "month"
    ? anchor.toLocaleDateString(undefined, { month: "long", year: "numeric" })
    : `${fromISO} → ${toISO}`;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="agview" onClick={e => e.stopPropagation()}
           role="dialog" aria-label="Agenda">
        <header className="agview-head">
          <span className="label">◇ AGENDA</span>
          <div className="agview-modes">
            {(["week", "month", "agenda"] as Mode[]).map(m => (
              <button key={m} className={m === mode ? "on" : ""} onClick={() => setMode(m)}>
                {m.toUpperCase()}
              </button>
            ))}
          </div>
          <div className="agview-nav">
            <button onClick={() => step(-1)} title="Previous">‹</button>
            <button onClick={() => setAnchor(new Date())} title="Back to today">TODAY</button>
            <button onClick={() => step(1)} title="Next">›</button>
          </div>
          <span className="agview-range">{label}</span>
          <div className="agview-filter">
            {SECTIONS.map(([k, t]) => (
              <button key={k} className={k === section ? "on" : ""}
                      onClick={() => setSection(k)}>{t}</button>
            ))}
          </div>
          <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
        </header>

        {d === undefined && <p className="dim pad">reading…</p>}
        {d === null && <p className="err pad">backend unreachable</p>}

        {d && (
          <>
            {/* Unreadable rows are named here too, not only on the rail: this is
                the view you are in when you are wondering where something went. */}
            {d.problems.length > 0 && (
              <p className="agview-problems" title={d.problems
                .map(p => `${p.path}:${p.line} — ${p.why}\n  ${p.raw}`).join("\n")}>
                ⚠ {d.problems.length} line(s) in the calendar notes do not parse —
                they are not shown anywhere
              </p>
            )}

            <div className="agview-body">
              {mode === "week" && <Week occ={shown} from={from} today={today}
                                       vault={vault} onProbe={setProbe} />}
              {mode === "month" && <Month occ={shown} from={from} to={to} anchor={anchor}
                                          today={today} onProbe={setProbe} />}
              {mode === "agenda" && <List occ={shown} from={from} today={today}
                                          vault={vault} onProbe={setProbe} />}
            </div>

            <footer className={`agview-foot ${probe ? "has" : ""}`}>
              {probe ? (
                <>
                  <span className="ag-glyph">{GLYPH[probe.kind]}</span>
                  <b>{probe.title}</b>
                  <span className="dim">
                    {probe.kind}
                    {probe.source.field ? ` · frontmatter ${probe.source.field}:` : ""}
                    {probe.source.rule_id ? ` · rule ${probe.source.rule_id}` : ""}
                    {probe.source.block_id ? ` · ${probe.source.block_id}` : ""}
                  </span>
                  <a href={obsidianHref(vault, probe.source.path)} className="agview-src">
                    {probe.source.path}{probe.source.line ? `:${probe.source.line}` : ""}
                  </a>
                  {probe.conflict && <span className="agview-why">⚠ {probe.conflict.why}</span>}
                  <button className="ghost" onClick={() => setProbe(null)}>✕</button>
                </>
              ) : (
                <span className="dim">
                  {shown.length} occurrence{shown.length === 1 ? "" : "s"}
                  {d.conflicts > 0 && <> · <span className="warn">{d.conflicts} disagreeing</span></>}
                  {d.timezone ? ` · ${d.timezone}` : " · no timezone declared"}
                  {" — [?] on anything to see where it comes from"}
                </span>
              )}
            </footer>
          </>
        )}
      </div>
    </div>
  );
}

/** One row in a list or a gutter. */
function Row({ o, vault, onProbe, showTime = true }: {
  o: Occurrence; vault: string; onProbe: (o: Occurrence) => void; showTime?: boolean;
}) {
  return (
    <li className={`agrow k-${o.kind}`}>
      <button className="agrow-why" onClick={() => onProbe(o)}
              title="where does this come from?">[?]</button>
      <span className={`ag-glyph k-${o.kind}`}>{GLYPH[o.kind]}</span>
      {showTime && (
        <span className="agrow-when">
          {o.start ? `${o.start}${o.end ? `–${o.end}` : "–"}` : "all day"}
        </span>
      )}
      <a href={obsidianHref(vault, o.source.path)} className="agrow-title" title={o.title}>
        <Marks o={o} />{o.title}
      </a>
    </li>
  );
}

function Week({ occ, from, today, vault, onProbe }: {
  occ: Occurrence[]; from: Date; today: string; vault: string;
  onProbe: (o: Occurrence) => void;
}) {
  const days = Array.from({ length: 7 }, (_, i) => addDays(from, i));
  const timed = occ.filter(o => o.start);
  // Widen rather than clip: a 06:00 flight is not an event this grid may hide.
  const lo = Math.min(GRID_FROM * 60, ...timed.map(o => minutes(o.start!)));
  const hi = Math.max(GRID_TO * 60, ...timed.map(o => minutes(o.end ?? o.start!) + 30));
  const span = Math.max(60, hi - lo);
  const hours: number[] = [];
  for (let h = Math.floor(lo / 60); h <= Math.ceil(hi / 60); h++) hours.push(h);

  return (
    <div className="agweek">
      <div className="agweek-gutter">
        <span className="agweek-corner">all day</span>
        {days.map(dd => {
          const key = iso(dd);
          const items = occ.filter(o => o.date === key && !o.start);
          return (
            <div key={key} className={`agweek-allday ${key === today ? "today" : ""}`}>
              <span className="agweek-dow">
                {dd.toLocaleDateString(undefined, { weekday: "short" })} {dd.getDate()}
              </span>
              <ul>{items.map(o => (
                <Row key={o.id} o={o} vault={vault} onProbe={onProbe} showTime={false} />
              ))}</ul>
            </div>
          );
        })}
      </div>

      <div className="agweek-grid">
        <div className="agweek-hours">
          {hours.map(h => (
            <span key={h} style={{ top: `${((h * 60 - lo) / span) * 100}%` }}>
              {String(h).padStart(2, "0")}:00
            </span>
          ))}
        </div>
        {days.map(dd => {
          const key = iso(dd);
          return (
            <div key={key} className={`agweek-col ${key === today ? "today" : ""}`}>
              {hours.map(h => (
                <span key={h} className="agweek-line"
                      style={{ top: `${((h * 60 - lo) / span) * 100}%` }} />
              ))}
              {occ.filter(o => o.date === key && o.start).map(o => {
                const s = minutes(o.start!);
                const e = o.end ? minutes(o.end) : s + 30;
                return (
                  <button key={o.id} className={`agblock k-${o.kind}`}
                          onClick={() => onProbe(o)}
                          title={`${o.start}${o.end ? `–${o.end}` : ""} ${o.title}\n${o.source.path}`}
                          style={{ top: `${((s - lo) / span) * 100}%`,
                                   height: `${Math.max(2.2, ((e - s) / span) * 100)}%` }}>
                    <span className="agblock-t">{o.start}</span>
                    <span className="agblock-n"><Marks o={o} />{o.title}</span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** No `vault` here on purpose: a month cell is too small to be a link as well
 *  as a target, so every item opens the provenance strip instead, and that
 *  strip carries the Obsidian link. One way in, from a cell this size. */
function Month({ occ, from, to, anchor, today, onProbe }: {
  occ: Occurrence[]; from: Date; to: Date; anchor: Date; today: string;
  onProbe: (o: Occurrence) => void;
}) {
  const cells: Date[] = [];
  for (let d = new Date(from); iso(d) <= iso(to); d = addDays(d, 1)) cells.push(new Date(d));
  const month = anchor.getMonth();

  return (
    <div className="agmonth">
      {Array.from({ length: 7 }, (_, i) => (
        <span key={i} className="agmonth-dow">
          {addDays(from, i).toLocaleDateString(undefined, { weekday: "short" })}
        </span>
      ))}
      {cells.map(dd => {
        const key = iso(dd);
        const items = occ.filter(o => o.date === key);
        return (
          <div key={key} className={`agmonth-cell ${key === today ? "today" : ""} `
                                  + `${dd.getMonth() === month ? "" : "outside"}`}>
            <span className="agmonth-n">{dd.getDate()}</span>
            <ul>
              {items.slice(0, MONTH_CELL_MAX).map(o => (
                <li key={o.id} className={`agmonth-item k-${o.kind}`}>
                  <button onClick={() => onProbe(o)}
                          title={`${o.start ?? "all day"} ${o.title}\n${o.source.path}`}>
                    <span className={`ag-glyph k-${o.kind}`}>{GLYPH[o.kind]}</span>
                    <Marks o={o} />{o.title}
                  </button>
                </li>
              ))}
            </ul>
            {/* Never silently truncated: what is not drawn is counted. */}
            {items.length > MONTH_CELL_MAX && (
              <span className="agmonth-more"
                    title={items.slice(MONTH_CELL_MAX).map(o => o.title).join("\n")}>
                +{items.length - MONTH_CELL_MAX}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function List({ occ, from, today, vault, onProbe }: {
  occ: Occurrence[]; from: Date; today: string; vault: string;
  onProbe: (o: Occurrence) => void;
}) {
  const days = Array.from({ length: AGENDA_DAYS }, (_, i) => addDays(from, i));
  return (
    <div className="aglist">
      {days.map(dd => {
        const key = iso(dd);
        const items = occ.filter(o => o.date === key);
        return (
          <section key={key} className={key === today ? "today" : ""}>
            <h4>
              {dd.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
              {key === today && <em>today</em>}
            </h4>
            {items.length === 0
              ? <p className="dim">—</p>
              : <ul>{items.map(o => (
                  <Row key={o.id} o={o} vault={vault} onProbe={onProbe} />
                ))}</ul>}
          </section>
        );
      })}
    </div>
  );
}
