/*
 * rich.tsx — the study room's markdown-lite renderer.
 *
 * Lifted out of workbench.tsx when the reference sheet needed the same thing:
 * paragraphs, 4-space formula blocks, `$$` display maths and inline bold /
 * italic / code / wikilink / `$…$`. Two views rendering note bodies had to
 * share one renderer or drift, and importing it back out of workbench.tsx
 * would have made the import cycle workbench → reference → workbench.
 *
 * `OPTION_RE` lives here rather than beside the grader because both readers of
 * an option list are here: this file decides that the line break survives, and
 * `mcqLetters` in workbench.tsx decides which letters are on offer. They must
 * agree, and `tests/test_mcq_options.py` pins that they do.
 */
import { MathBlock, MathInline } from "./math";

/** One option line of a multiple-choice prompt: `A) …`, `(a) …`, `B. …`.
 *
 *  The authoring prompt asks for "options A)–D)", so that is the shape the
 *  notes are written in and the shape both readers here must accept. It is
 *  anchored to the start of a line on purpose: an unanchored `(a)` matches
 *  mid-sentence prose ("…the couple (a) is free…") and would turn a
 *  parenthetical into an answer option.
 *
 *  Deliberately not global — `test()` on a `/g` regex advances `lastIndex`
 *  between calls, so a shared one answers differently on alternate lines. */
export const OPTION_RE = /^\(?([A-Ha-h])[).]\s+\S/;

/** The content grammar is markdown-lite by construction — paragraphs and
 *  4-space-indented formula blocks, with bold, backticks and wikilinks
 *  inline. Rendering it needs no library, and adding one for this would be
 *  the first runtime dependency beyond react itself. */
type Block =
  | { k: "p" | "pre" | "math" | "opt"; text: string }
  | { k: "table"; rows: string[][] };

/** A markdown table row → its cells. The outer pipes are optional, which is
 *  what a person types; an escaped `\|` stays a character, because a cell can
 *  legitimately contain one. */
function cells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, "")
    .split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, "|"));
}
/** `|---|:--:|` — the row that turns two lines of pipes into a table. Without
 *  one, a line containing pipes is prose that contains pipes. */
const RULE_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

export function Rich({ text }: { text: string }) {
  const blocks: Block[] = [];
  let para: string[] = [], pre: string[] = [];
  // Display maths is a *mode*, not a line test: an `aligned` environment
  // routinely runs to a dozen lines, and treating `$$` as a one-liner would
  // render the first line as maths and the rest as prose.
  let math: string[] | null = null;
  const flush = () => {
    if (para.length) { blocks.push({ k: "p", text: para.join(" ") }); para = []; }
    if (pre.length) { blocks.push({ k: "pre", text: pre.join("\n") }); pre = []; }
  };
  const closeMath = () => {
    blocks.push({ k: "math", text: (math ?? []).join("\n").trim() });
    math = null;
  };

  // Indexed rather than for-of: a table consumes the lines below it, so the
  // loop has to be able to move its own cursor.
  const lines = text.split("\n");
  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];
    if (math !== null) {
      if (line.trimEnd().endsWith("$$")) {
        math.push(line.replace(/\$\$\s*$/, ""));
        closeMath();
      } else math.push(line);
      continue;
    }
    const t = line.trim();
    // A table is two lines before it is one: a header, then a rule. Requiring
    // the rule is what keeps `a | b` in a sentence from becoming a table.
    if (t.includes("|") && RULE_RE.test(lines[li + 1] ?? "")) {
      flush();
      const rows: string[][] = [cells(t)];
      li += 2;
      while (li < lines.length && lines[li].includes("|") && lines[li].trim()) {
        rows.push(cells(lines[li]));
        li += 1;
      }
      li -= 1;
      blocks.push({ k: "table", rows });
      continue;
    }
    if (t.startsWith("$$")) {
      flush();
      const rest = t.slice(2);
      if (rest.trimEnd().endsWith("$$")) blocks.push({ k: "math", text: rest.replace(/\$\$\s*$/, "").trim() });
      else math = [rest];
      continue;
    }
    // The pre path stays exactly as it was, so a module still written in the
    // old Unicode style renders today the way it rendered yesterday.
    if (/^\s{4,}\S/.test(line)) {
      if (para.length) flush();
      pre.push(line.slice(4));
    } else if (!t) flush();
    else if (OPTION_RE.test(t)) {
      // An option list is the one place where a line break carries meaning:
      // joining these into a paragraph the way prose is joined rendered a
      // four-option question as "…points along: A) A × B B) B × A C) A · B".
      flush();
      blocks.push({ k: "opt", text: t });
    } else {
      if (pre.length) flush();
      para.push(t);
    }
  }
  // An unterminated `$$` is a typo in one note, not a reason to swallow the
  // rest of the segment — close it and render what there is.
  if (math !== null) closeMath();
  flush();

  return (
    <>
      {blocks.map((b, i) =>
        b.k === "pre" ? <pre key={i}>{b.text}</pre>
        : b.k === "math" ? <MathBlock key={i} tex={b.text} />
        : b.k === "opt" ? <p key={i} className="wb-opt"><Inline text={b.text} /></p>
        : b.k === "table" ? (
          <table key={i}>
            <thead>
              <tr>{b.rows[0].map((c, j) => <th key={j}><Inline text={c} /></th>)}</tr>
            </thead>
            <tbody>
              {b.rows.slice(1).map((r, ri) => (
                <tr key={ri}>{r.map((c, j) => <td key={j}><Inline text={c} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        )
        : <p key={i}><Inline text={b.text} /></p>)}
    </>
  );
}

export function Inline({ text }: { text: string }) {
  // The code-span alternative deliberately precedes the maths one: `split`
  // consumes left to right, so a `$` inside backticks is claimed as code and
  // never seen as maths. `$PATH` in a shell snippet stays a shell variable.
  // `**bold**` precedes `*italic*` so the greedier pair wins; both precede the
  // maths alternative, and the code span precedes everything. Italics were
  // missing entirely, which is why a sourced sentence rendered as
  // "a magnitude *and* a direction" with the asterisks showing.
  const parts = text.split(
    /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\[\[[^\]]+\]\]|\$[^$\n]+\$)/g);
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**")) return <b key={i}>{p.slice(2, -2)}</b>;
        if (p.startsWith("*") && p.endsWith("*") && p.length > 2) {
          return <i key={i}>{p.slice(1, -1)}</i>;
        }
        if (p.startsWith("`") && p.endsWith("`")) return <code key={i}>{p.slice(1, -1)}</code>;
        if (p.startsWith("$") && p.endsWith("$") && p.length > 2) {
          return <MathInline key={i} tex={p.slice(1, -1)} />;
        }
        if (p.startsWith("[[") && p.endsWith("]]")) {
          const inner = p.slice(2, -2);
          const bar = inner.indexOf("|");
          const label = bar >= 0 ? inner.slice(bar + 1) : inner.split("/").pop() ?? inner;
          return <span key={i} className="wb-link">{label}</span>;
        }
        return p;
      })}
    </>
  );
}

