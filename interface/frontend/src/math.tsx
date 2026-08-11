/**
 * math.tsx — TeX in a lesson, rendered.
 *
 * Generated modules used to write maths as Unicode art inside a 4-space block:
 * `‖V‖ = √(V_x² + V_y² + V_z²)`, and a division rendered as a hand-aligned bar
 * across two lines. It reads as clunky because it *is* clunky, and it has no
 * answer at all for a fraction, an integral or a matrix.
 *
 * The vault had already solved this without the guide noticing. Seventy-three
 * notes under `02-Areas/` write maths as `$…$` and `$$…$$`, which Obsidian
 * renders natively — and exactly one file under AA-210 contains a `$`. The
 * intake-written notes were right and the generator was the odd one out, so
 * this is adopting the vault's own convention rather than inventing one.
 *
 * ## On `dangerouslySetInnerHTML`
 *
 * `chat.tsx` states the rule this project holds itself to: a vault note
 * containing a stray `<script>` must render as text and never run. KaTeX has no
 * React-element API, so this file is the one place that injects HTML, and the
 * reason it is allowed here and *not* for figures is worth being exact about:
 *
 *   · Here the note supplies **TeX**, which is data to a parser. The markup is
 *     KaTeX's own output; no byte of the note reaches the DOM as markup. With
 *     `trust: false` (the default, restated below because it is load-bearing)
 *     KaTeX refuses `\href`, `\url`, `\includegraphics` and the `\html*`
 *     family, so a note cannot smuggle a URL or an attribute through either.
 *   · A figure supplies **markup itself**. There is no parser in between, so
 *     nothing can vouch for it — which is why `figure.tsx` walks the SVG and
 *     emits React elements from a whitelist instead of taking this shortcut.
 *
 * Different inputs, different guarantees, different mechanisms.
 */
import katex from "katex";
import "katex/dist/katex.min.css";

const OPTS = {
  // Never throw: an unsupported macro must cost one expression, not the whole
  // segment. KaTeX renders the offending source in red instead, which is also
  // the honest signal — Obsidian renders with MathJax and KaTeX is a subset, so
  // this is precisely the seam where the two disagree and it should be visible
  // in the one place it happened.
  throwOnError: false,
  // Restated rather than relied upon. This is the whole safety argument above.
  trust: false,
  strict: false as const,
};

function html(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex, { ...OPTS, displayMode });
  } catch {
    // renderToString can still throw on a malformed *option* combination
    // rather than on the TeX. Degrade to the source, never to nothing.
    return "";
  }
}

/** `$$…$$` — its own block, centred, with room to breathe. */
export function MathBlock({ tex }: { tex: string }) {
  const out = html(tex, true);
  if (!out) return <pre className="math-raw">{tex}</pre>;
  return <div className="math-block" dangerouslySetInnerHTML={{ __html: out }} />;
}

/** `$…$` — inside a sentence, on the sentence's own baseline. */
export function MathInline({ tex }: { tex: string }) {
  const out = html(tex, false);
  if (!out) return <code className="math-raw">{tex}</code>;
  return <span className="math-inline" dangerouslySetInnerHTML={{ __html: out }} />;
}
