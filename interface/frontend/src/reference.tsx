/*
 * reference.tsx — the workbench's reference slot: what you look *up*.
 *
 * The other four slots are about the module in front of you. This one is about
 * the course: its equations, definitions, constants, tables and procedures,
 * with the conditions that make each valid.
 *
 * **Two tiers, one document.** `Detailed` is the whole sheet; `Simplified` is
 * the `tier:: exam` subset — what fits on one piece of paper, both sides, of
 * the kind a closed-book exam allows. The second is a *filter*, never a
 * rewrite: an entry says the same thing in both views, so there is exactly one
 * place to correct it and no way for the two to drift. That is also why the
 * toggle costs no request — both tiers arrive in the same payload.
 *
 * The simplified view carries a fill gauge, because "does this fit on the
 * sheet" is the only constraint it has and it is invisible on screen: a
 * scrolling column looks the same at one page and at four.
 */
import { useEffect, useMemo, useState } from "react";
import { get, obsidianHref } from "./api";
import type { Reference, ReferenceEntry } from "./api";
import { Rich } from "./rich";

type Tier = "full" | "exam";
const TIER_KEY = "sigma.study.reftier";

/** The glyph for a kind. Small, and never the only signal — the kind is also
 *  written out beside it, because a glyph alone is a puzzle you re-solve every
 *  time you come back after a week. */
const KIND_GLYPH: Record<string, string> = {
  equation: "∑", definition: "≡", constant: "#", table: "▦", procedure: "→",
};

function Entry({ e }: { e: ReferenceEntry }) {
  return (
    <article className="ref-e">
      <h4 className="ref-e-h">
        <span className="ref-e-t">{e.title}</span>
        <span className="ref-e-k" title={e.kind}>
          <span aria-hidden="true">{KIND_GLYPH[e.kind] ?? "·"}</span> {e.kind}
        </span>
      </h4>
      <div className="ref-e-b"><Rich text={e.body} /></div>
    </article>
  );
}

export default function ReferenceDock(
  { course, vault }: { course: string | null; vault: string },
) {
  const [d, setD] = useState<Reference | null | undefined>(undefined);
  const [err, setErr] = useState<string | null>(null);
  const [tier, setTierRaw] = useState<Tier>(() => {
    try { return localStorage.getItem(TIER_KEY) === "exam" ? "exam" : "full"; }
    catch { return "full"; }
  });
  const setTier = (t: Tier) => {
    setTierRaw(t);
    try { localStorage.setItem(TIER_KEY, t); } catch { /* private mode */ }
  };

  useEffect(() => {
    if (!course) return;
    setD(undefined); setErr(null);
    get<Reference>(`reference/${encodeURIComponent(course)}`)
      .then(setD)
      .catch((e: unknown) => {
        setD(null);
        // `get()` throws a plain Error whose message ends in the status —
        // `ApiError` belongs to the mutation layer and never reaches here, so
        // the first version's `e.code === "no reference"` could not fire and
        // every course without a sheet read as a dead backend.
        setErr(/404$/.test(e instanceof Error ? e.message : "")
          ? null : "backend unreachable");
      });
  }, [course]);

  // Filtering here rather than at the API keeps the toggle instant and keeps
  // the two views provably the same entries.
  const shown = useMemo(() => {
    if (!d) return [];
    return d.sections
      .map(s => ({
        title: s.title,
        entries: s.entries.filter(e => tier === "full" || e.tier === "exam"),
      }))
      .filter(s => s.entries.length > 0);
  }, [d, tier]);

  if (!course) return <p className="dim">open a course first</p>;
  if (d === undefined) return <p className="dim">reading the sheet…</p>;
  if (err) return <p className="err">{err}</p>;
  if (d === null) {
    return (
      <div className="ref-none">
        <p className="dim">
          {course} has no reference sheet yet. One is written from the course's
          own modules and assessments:
        </p>
        <p><code>sigma guide reference {course}</code></p>
        <p className="dim">
          One model call. It lands as a note in the course folder — a commit you
          can revert — and shows up here.
        </p>
      </div>
    );
  }

  const pct = Math.min(100, Math.round((d.exam_chars / d.exam_budget) * 100));
  const over = d.exam_chars > d.exam_budget;

  return (
    <div className="ref">
      <div className="ref-head">
        <div className="wb-seg" role="group" aria-label="Reference detail">
          <button className={tier === "full" ? "on" : ""}
                  onClick={() => setTier("full")}
                  title="everything on the sheet">Detailed</button>
          <button className={tier === "exam" ? "on" : ""}
                  onClick={() => setTier("exam")}
                  title="only what fits on one double-sided page">Simplified</button>
        </div>
        <a className="ref-file" href={obsidianHref(vault, d.file)}
           title="the note — markdown is the truth">
          {d.file.split("/").pop()}
        </a>
      </div>

      {tier === "exam" ? (
        <div className={`ref-gauge ${over ? "over" : ""}`}>
          <span className="ref-gauge-bar" role="img"
                aria-label={`${d.exam_chars} of ${d.exam_budget} characters`}>
            <span className="ref-gauge-fill" style={{ width: `${pct}%` }} />
          </span>
          <span className="dim">
            {d.counts.exam} of {d.counts.all} entries · {pct}% of one
            double-sided sheet{over ? " — over" : ""}
          </span>
        </div>
      ) : (
        <p className="ref-meta dim">
          {d.counts.all} entries · {d.counts.exam} of them carry onto the exam sheet
        </p>
      )}

      {d.problems.length > 0 && (
        <div className="ref-held">
          <p><b>This sheet fails the grammar</b> — showing it anyway, because a
          reference is still readable when one entry is malformed:</p>
          <ul>{d.problems.slice(0, 6).map((p, i) => <li key={i}>{p}</li>)}</ul>
        </div>
      )}

      {shown.length === 0 && (
        <p className="dim">nothing on this tier</p>
      )}
      {shown.map(s => (
        <section key={s.title} className="ref-s">
          <h3 className="ref-s-h">{s.title}</h3>
          {s.entries.map(e => <Entry key={e.line} e={e} />)}
        </section>
      ))}
    </div>
  );
}
