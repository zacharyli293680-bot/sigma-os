/**
 * figure.tsx — a module's diagram, rendered without ever injecting markup.
 *
 * `chat.tsx` states the rule this project holds itself to: a vault note
 * containing a stray `<script>` must render as text and never run. A figure is
 * the hardest case for that rule, because unlike every other thing a note
 * carries, a figure *is* markup — there is no parser in between to vouch for
 * it the way KaTeX vouches for TeX (see `math.tsx`, which explains why it is
 * allowed the shortcut this file is not).
 *
 * So nothing here is injected. The SVG is parsed, walked, and rebuilt as React
 * elements from an allow-list: an element not named below is dropped, an
 * attribute not named below is dropped, and a `<script>` arrives as an unknown
 * element and leaves as nothing. There is no path from the note's bytes to the
 * DOM that does not pass through `createElement`.
 *
 * `runtime/lesson.py` holds the same two sets and refuses a figure that breaks
 * them at *validation* time, so a bad figure is held rather than silently
 * stripped — you find out, instead of quietly getting a diagram with a piece
 * missing. `tests/test_figure_safety.py` fails if the two copies drift.
 */
import { createElement, useMemo } from "react";
import type { ReactNode } from "react";

const TAGS = new Set([
  "svg", "g", "title", "desc", "defs", "marker",
  "line", "polyline", "polygon", "path", "rect", "circle", "ellipse",
  "text", "tspan",
]);

const ATTRS = new Set([
  "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
  "width", "height", "d", "points", "dx", "dy", "transform",
  "viewBox", "preserveAspectRatio",
  "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "opacity",
  "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-opacity",
  "text-anchor", "dominant-baseline", "font-size", "font-family",
  "font-weight", "font-style", "letter-spacing",
  "marker-end", "marker-start", "marker-mid",
  "markerWidth", "markerHeight", "refX", "refY", "orient", "markerUnits",
  "id", "class", "role", "aria-label", "aria-hidden", "xmlns",
]);

/** SVG attribute name → React prop name. `aria-*` and `data-*` stay hyphenated
 *  because React wants them that way; everything else camelCases, which is what
 *  keeps `stroke-width` from arriving as an unknown-prop warning. */
function prop(name: string): string {
  if (name.startsWith("aria-") || name.startsWith("data-")) return name;
  if (name === "class") return "className";
  if (name === "xmlns") return "xmlns";
  return name.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
}

function convert(node: Element, key: string): ReactNode {
  // `localName`, not `tagName`: SVG is case-sensitive, and this is what makes
  // `foreignObject` arrive spelled the way the allow-list spells it rather than
  // as `foreignobject`, which would then miss and be dropped for the right
  // answer by accident.
  const name = node.localName;
  if (!TAGS.has(name)) return null;

  const props: Record<string, string> = { key };
  for (const at of Array.from(node.attributes)) {
    const a = at.name;
    // Belt and braces. The allow-list already excludes both, but these two are
    // the ones worth failing loudly about if the list is ever edited carelessly.
    if (a.toLowerCase().startsWith("on")) continue;
    if (a.toLowerCase().includes("href")) continue;
    if (!ATTRS.has(a)) continue;
    if (/url\(|javascript:/i.test(at.value)) continue;
    props[prop(a)] = at.value;
  }

  const kids: ReactNode[] = [];
  node.childNodes.forEach((c, i) => {
    if (c.nodeType === 3) {
      const t = c.nodeValue ?? "";
      if (t.trim()) kids.push(t);
    } else if (c.nodeType === 1) {
      const el = convert(c as Element, `${key}-${i}`);
      if (el) kids.push(el);
    }
  });

  return createElement(name, props, kids.length ? kids : undefined);
}

export default function Figure({ svg, caption }: { svg: string; caption: string }) {
  const tree = useMemo(() => {
    try {
      const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
      if (doc.getElementsByTagName("parsererror").length) return null;
      const root = doc.documentElement;
      if (!root || root.localName !== "svg") return null;
      return convert(root, "f");
    } catch {
      return null;
    }
  }, [svg]);

  if (!tree) {
    // The note said there was a diagram and there is not. Saying so is the
    // honest failure; rendering the caption alone would describe a picture that
    // never arrived.
    return (
      <figure className="wb-figure">
        <p className="wb-figure-bad">⚠ this figure could not be drawn — {caption}</p>
      </figure>
    );
  }
  return (
    <figure className="wb-figure">
      <div className="wb-figure-art">{tree}</div>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}
