/**
 * soon.tsx — the rail slots that are designed and not built.
 *
 * `AG`, `CR` and `SY` have been rendered disabled since the shell shipped, on
 * the doctrine that **disabled beats hidden**: a slot you can see is a promise
 * you can hold the project to, and demolishing the room to tidy the corridor
 * loses that. The reference design language's §5.4 keeps the doctrine and
 * raises the bar — *"nothing is a dead click, and nothing pretends to exist"*.
 * A disabled button clears the second half and fails the first: clicking it
 * does nothing at all, and "nothing at all" is indistinguishable from broken.
 *
 * So the slots are live now, and what they open is a placeholder that says two
 * things: **what will go here**, and **what to do instead today**. The second
 * half is the one that earns the screen — every pointer below is a working
 * link, a real CLI verb or another rail slot, so the view is useful rather than
 * apologetic.
 *
 * The copy is lifted from `CONTEXT.md` §16 "Designed and not built" rather than
 * invented here, because a placeholder that drifts from the roadmap is worse
 * than no placeholder — it becomes a second, stale roadmap that nobody edits.
 */
import { obsidianHref } from "./api";

type Slot = {
  /** the eyebrow's noun — the rail's two letters spelled out */
  name: string;
  /** §2.3: a headline is the finding, as a sentence, with a full stop */
  claim: string;
  will: React.ReactNode[];
  instead: React.ReactNode[];
};

/** Written as a function of `vault` so the "instead" pointers can be real
 *  Obsidian links rather than paths printed as text. */
function slots(vault: string): Record<string, Slot> {
  const note = (path: string, label: string) => (
    <a href={obsidianHref(vault, path)} title={`${path}.md`}>{label}</a>
  );
  return {
    AG: {
      name: "AGENTS",
      claim: "Sigma's specialists run on a schedule, and you cannot yet point one at something.",
      will: [
        <>every specialist, the window it runs in, and what it last did</>,
        <>assign work to one directly, instead of waiting for the 09:00 pass</>,
        <>break a goal into specialist work and get one answer back</>,
        <>watch, steer and <b>stop</b> a run — a run you can interrupt is a run
           you can trust to start</>,
      ],
      instead: [
        <>the <b>FLEET</b> panel on the stage names the last run and what is scheduled</>,
        <><code>sigma fleet</code> runs one now, from the CLI or the <kbd>Ctrl+K</kbd> palette</>,
        <>their proposals land in <b>WAITING ON YOU</b>, which is where you approve one</>,
      ],
    },
    CR: {
      name: "CAREER",
      claim: "The pipeline is empty, which is the reason there is no panel rather than an oversight.",
      will: [
        <>paste a posting, get a tracked application note, its deadlines on the
           calendar, and a nudge before it goes stale</>,
        <>tailor a résumé or a cover letter against a posting, grounded in what
           you actually built rather than in adjectives</>,
        <>interview prep driven by the gap between a posting and the vault</>,
      ],
      instead: [
        <><code>02-Areas/Career/Applications/</code> is empty — the tracker has
           reported "pipeline is clear" every week since it started</>,
        <>{note("02-Areas/Career/job-search", "job-search")} is the area index</>,
        <>career tasks queue under <b>MISC</b> in <b>WORK</b> like anything else</>,
      ],
    },
    SY: {
      name: "SYSTEM",
      claim: "Sigma's memory of its own work lives in the vault, and has never had a screen.",
      will: [
        <>session logs, insights and proposals as one browsable history</>,
        <>what Sigma has learned about itself, and which lesson superseded which</>,
        <>the health check as a live panel rather than a line at session start</>,
      ],
      instead: [
        <>{note("06-System/system", "system.md")} holds Dataview dashboards over
           all three, which is the same data this panel would read</>,
        <><b>WAITING ON YOU</b> carries every pending proposal</>,
        <><kbd>Ctrl+J</kbd> is the activity ledger — every write Sigma made, each
           with one click to undo it</>,
        <><code>sigma doctor</code> for health</>,
      ],
    },
  };
}

export default function SoonView({ slot, vault, onClose }: {
  slot: string | null; vault: string; onClose: () => void;
}) {
  if (!slot) return null;
  const s = slots(vault)[slot];
  if (!s) return null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="study soon" onClick={e => e.stopPropagation()}
           role="dialog" aria-label={`${s.name} — not built yet`}>
        <header className="study-head">
          <span className="label">◇ {s.name}</span>
          <span className="p-badge soon-badge">soon</span>
          <button className="ghost" onClick={onClose} title="Close (Esc)">✕</button>
        </header>

        <div className="study-body">
          <p className="soon-claim">{s.claim}</p>

          <h3>What will go here</h3>
          <ul className="soon-list">
            {s.will.map((w, i) => <li key={i}>{w}</li>)}
          </ul>

          <h3>What to do instead</h3>
          <ul className="soon-list soon-now">
            {s.instead.map((w, i) => <li key={i}>{w}</li>)}
          </ul>

          <p className="dim study-note">
            This list is <code>CONTEXT.md</code> §16, not a second roadmap — if it
            is out of date, that file is where it is wrong.
          </p>
        </div>
      </div>
    </div>
  );
}
