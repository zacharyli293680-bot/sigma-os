/**
 * theme.ts — the seven palettes, and the one place a colour is written down.
 *
 * dashboard-plan §2 specified a single deliberate mode. What that bought was
 * coherence, and what it cost was that every hue lived inline in App.css: 64
 * literals, 35 of them the same cyan at a different alpha. Six palettes could
 * not have been added to that file; they can be added to this one, because the
 * stylesheet now names tokens and this module supplies their values.
 *
 * **A theme is a value swap, never a stylesheet fork.** There is no
 * `.theme-ember .panel` anywhere and there must never be one — the moment a
 * rule knows which theme is active, the other six stop being maintained.
 * Everything below is data, and `apply` writes it onto :root.
 *
 * Four rules the palettes are held to. The first three are inherited from §2
 * and the dataviz method rather than invented here; the fourth arrived with the
 * reference design language (`03-Projects/sigma-os/reference-ui-design-language`
 * in the vault) and is why a Spec now states three grounds instead of one:
 *
 *   1. **Bronze means one thing.** "Never leaves this machine", app-wide.
 *      Every theme keeps it in the copper family *and* clear of its own accent
 *      — EMBER is the hard case, which is why its accent is a red-orange and
 *      its bronze an olive-tinted one rather than two shades of the same hue.
 *   2. **Status is never colour alone.** Amber/red/add only ever sharpen a
 *      glyph or a word, so a theme may move them for contrast without moving
 *      what they mean.
 *   3. **The graph's bucket colours are data, not decoration.** They stay a
 *      full categorical wheel in all seven themes — nine hues that must be told
 *      apart — and are only re-tuned for the ground they sit on. A monochrome
 *      GRAPHITE brain would look consistent and say nothing.
 *   4. **Elevation is lightness, never a shadow** (the reference language's
 *      §1.1, adopted 2026-08-10). Every palette states three grounds — the
 *      page, the raised plane, and the inset — and a raised thing is the one
 *      that is *lighter than the page*. The rule inverts cleanly into the dark
 *      themes, where it had been doing nothing at all: a modal used to be
 *      `--void` on `--void`, separated only by a hairline and a bloom.
 */
import { useSyncExternalStore } from "react";

/** Canvas colours. The brain paints imperatively, so it cannot read CSS
 *  tokens; it takes this and applies its own fixed alphas. All hex, because
 *  the canvas parses hex once into channels (see `channels`). */
export interface BrainPalette {
  /** one per bucket, keyed by the graph's own bucket names */
  buckets: Record<string, string>;
  /** a node whose bucket is not in the map — new folder, old frontend */
  fallback: string;
  /** a node the fleet just touched, and the axon pulse crossing to it */
  fire: string;
  /** the no-sync ring. Bronze, and never a fill — see rule 1 above. */
  noSync: string;
  edgeDim: string;
  edgeFire: string;
  edgeLit: string;
  dust: string;
  labelInk: string;
  /** the halo stroked under a star's label so it survives a bright cloud */
  labelHalo: string;
}

export interface Theme {
  id: string;
  /** what the picker calls it */
  name: string;
  /** one line, in the picker — what the palette is *for*, not what it contains */
  note: string;
  /** CSS custom properties, keys including the leading `--` */
  tokens: Record<string, string>;
  brain: BrainPalette;
}

/** "#22D3EE" → "34 211 238", the space-separated form modern `rgb()` takes.
 *  One hex per colour in the specs below; every channel triple is derived, so
 *  a token and its -rgb twin can never drift apart. */
export function channels(hex: string): string {
  const h = hex.replace("#", "");
  const n = h.length === 3
    ? h.split("").map(c => c + c).join("")
    : h;
  return [0, 2, 4].map(i => parseInt(n.slice(i, i + 2), 16)).join(" ");
}

/** What a palette actually has to state. Everything else `make` derives. */
interface Spec {
  id: string;
  name: string;
  note: string;
  scheme: "dark" | "light";
  void_: string;
  /** the raised plane: modals, cards, popovers, floating controls. Lighter
   *  than `void_` in every theme — that is the whole rule (see 4 above). */
  surface: string;
  /** the inset: code blocks, formula boxes, wells. Between the two, so a well
   *  inside a card reads as *pressed into* it rather than as a second card. */
  inset: string;
  scrim: string;
  /** the circuit-trace substrate; defaults to the accent */
  grid?: string;
  accent: string;
  /** the accent's brighter twin, used for bloom only */
  glow: string;
  ink: string;
  ink2: string;
  muted: string;
  amber: string;
  red: string;
  add: string;
  bronze: string;
  /** panel-line and panel-line-dim alphas — a light ground needs more */
  line?: [number, number];
  /** the four drifting gradients behind the page */
  haze: [string, string, string, string];
  /** the four inside the sky; defaults to the haze */
  neb?: [string, string, string, string];
  /** Only a theme whose page is lighter than its instrument states these. The
   *  centre stage keeps a dark ground in every theme because the brain's canvas
   *  composites additively — see App.css's `.center`. */
  sky?: {
    bg: string;
    /** the sky's own raised plane. A light theme's `surface` is near-white and
     *  would be a hole punched in the star field, so the stage states its own. */
    surface: string;
    ink: string;
    ink2: string;
    muted: string;
    accent: string;
    glow: string;
    amber: string;
  };
  brain: BrainPalette;
}

function make(s: Spec): Theme {
  const [la, ld] = s.line ?? [0.28, 0.14];
  const sky = s.sky;
  const skyAccent = sky?.accent ?? s.accent;
  const tokens: Record<string, string> = {
    "--void": s.void_,
    "--void-rgb": channels(s.void_),
    "--surface": s.surface,
    "--surface-rgb": channels(s.surface),
    "--inset": s.inset,
    "--inset-rgb": channels(s.inset),
    "--scrim-rgb": channels(s.scrim),
    "--grid-rgb": channels(s.grid ?? s.accent),
    "--scheme": s.scheme,

    "--accent": s.accent,
    "--accent-rgb": channels(s.accent),
    "--glow-rgb": channels(s.glow),
    "--panel-line": `rgb(${channels(s.accent)} / ${la})`,
    "--panel-line-dim": `rgb(${channels(s.accent)} / ${ld})`,

    "--ink": s.ink,
    "--ink-2": s.ink2,
    "--muted": s.muted,
    "--muted-rgb": channels(s.muted),

    "--amber": s.amber,
    "--amber-rgb": channels(s.amber),
    "--red": s.red,
    "--red-rgb": channels(s.red),
    "--add": s.add,
    "--add-rgb": channels(s.add),
    "--bronze": s.bronze,
    "--bronze-rgb": channels(s.bronze),

    // the sky. Mirrors the base tokens unless the theme says otherwise, so the
    // five dark themes get `.center`'s remap as a no-op.
    "--sky": sky?.bg ?? s.void_,
    "--sky-rgb": channels(sky?.bg ?? s.void_),
    "--sky-bg": sky ? sky.bg : "transparent",
    "--sky-surface": sky?.surface ?? s.surface,
    "--sky-surface-rgb": channels(sky?.surface ?? s.surface),
    "--sky-ink": sky?.ink ?? s.ink,
    "--sky-ink-2": sky?.ink2 ?? s.ink2,
    "--sky-muted": sky?.muted ?? s.muted,
    "--sky-accent": skyAccent,
    "--sky-accent-rgb": channels(skyAccent),
    "--sky-glow-rgb": channels(sky?.glow ?? s.glow),
    "--sky-amber": sky?.amber ?? s.amber,
    // The sky always uses the dark-ground alphas: its ground is dark even when
    // the page is not, so borrowing a light theme's heavier hairline would
    // draw a cage around the stars.
    "--sky-line": `rgb(${channels(skyAccent)} / 0.28)`,
    "--sky-line-dim": `rgb(${channels(skyAccent)} / 0.14)`,
  };
  s.haze.forEach((c, i) => { tokens[`--haze-${i + 1}`] = channels(c); });
  (s.neb ?? s.haze).forEach((c, i) => { tokens[`--neb-${i + 1}`] = channels(c); });

  return { id: s.id, name: s.name, note: s.note, tokens, brain: s.brain };
}

/* ------------------------------------------------------------- the palettes */

export const THEMES: Theme[] = [
  make({
    id: "void",
    name: "VOID",
    note: "the original — cyan structure on near-black, the instrument as specified",
    scheme: "dark",
    void_: "#05070A", surface: "#0C1116", inset: "#080C11",
    scrim: "#020305",
    accent: "#22D3EE", glow: "#00E5FF",
    ink: "#D9E4EB", ink2: "#8299A6", muted: "#3A4A55",
    amber: "#FFB020", red: "#FF4D4D", add: "#4ADE80", bronze: "#C77D2E",
    haze: ["#00E5FF", "#C084FC", "#2DD4BF", "#60A5FA"],
    brain: {
      buckets: {
        root: "#FF6B6B", inbox: "#FFD166", daily: "#E8EAED",
        academics: "#4ADE80", areas: "#FB923C", projects: "#60A5FA",
        system: "#C084FC", archive: "#6B7280", meta: "#2DD4BF",
      },
      fallback: "#8299A6", fire: "#9BEFFC", noSync: "#C77D2E",
      edgeDim: "#7896AA", edgeFire: "#22D3EE", edgeLit: "#9BEFFC",
      dust: "#96B9D2", labelInk: "#D9E4EB", labelHalo: "#060A12",
    },
  }),

  make({
    id: "ember",
    name: "EMBER",
    note: "tungsten — a warm instrument for a dark room, red-orange on charred black",
    scheme: "dark",
    void_: "#0B0806", surface: "#150F0B", inset: "#0F0B08",
    scrim: "#050302",
    accent: "#FF6B35", glow: "#FF9A5C",
    ink: "#F2E6D8", ink2: "#A88E75", muted: "#574536",
    // Three warm hues that have to stay apart: accent (red-orange), amber
    // (gold) and bronze (olive copper). Separated by hue *and* lightness, and
    // each still arrives with a glyph — colour is never doing this alone.
    amber: "#FFC93D", red: "#FF4D6A", add: "#9BE04E", bronze: "#B08D57",
    haze: ["#FF6B35", "#E0457B", "#FFC93D", "#7C4DFF"],
    brain: {
      buckets: {
        root: "#FF5F56", inbox: "#FFC43D", daily: "#F5E9DA",
        academics: "#9CD35C", areas: "#FF9440", projects: "#5CB3E8",
        system: "#C58BF2", archive: "#97826B", meta: "#47CFB4",
      },
      fallback: "#A88E75", fire: "#FFD9A8", noSync: "#B08D57",
      edgeDim: "#A08A70", edgeFire: "#FF6B35", edgeLit: "#FFCFA0",
      dust: "#D2B58F", labelInk: "#F2E6D8", labelHalo: "#120A06",
    },
  }),

  make({
    id: "verdant",
    name: "VERDANT",
    note: "phosphor — the green terminal, for when the dashboard is a readout",
    scheme: "dark",
    void_: "#050A07", surface: "#0C1310", inset: "#080D0A",
    scrim: "#010402",
    accent: "#4ADE80", glow: "#7CF6A8",
    ink: "#DAEEDF", ink2: "#7FA189", muted: "#33503E",
    amber: "#FFC44D", red: "#FF5C5C", add: "#A3E635", bronze: "#C77D2E",
    haze: ["#4ADE80", "#2DD4BF", "#A3E635", "#38BDF8"],
    brain: {
      buckets: {
        root: "#FF6B6B", inbox: "#FFD166", daily: "#DDF0E2",
        academics: "#4ADE80", areas: "#FB923C", projects: "#56C8F5",
        system: "#B48CFB", archive: "#6E8177", meta: "#2DD4BF",
      },
      fallback: "#7FA189", fire: "#C6FFD9", noSync: "#C77D2E",
      edgeDim: "#7CA88C", edgeFire: "#4ADE80", edgeLit: "#A8F7C4",
      dust: "#9AC7A8", labelInk: "#DAEEDF", labelHalo: "#04120A",
    },
  }),

  make({
    id: "synapse",
    name: "SYNAPSE",
    note: "violet — the brain's own nebula colours, brought out to the whole shell",
    scheme: "dark",
    void_: "#08060D", surface: "#100D18", inset: "#0B0911",
    scrim: "#030208",
    accent: "#C084FC", glow: "#DDA9FF",
    ink: "#E6DEF2", ink2: "#9A8FB5", muted: "#453A5E",
    amber: "#FFB020", red: "#FF4D6A", add: "#4ADE80", bronze: "#C77D2E",
    haze: ["#C084FC", "#EC4899", "#60A5FA", "#2DD4BF"],
    brain: {
      buckets: {
        root: "#FF7597", inbox: "#FFD166", daily: "#EDE6F8",
        academics: "#5EE6A0", areas: "#FB923C", projects: "#74A9FF",
        system: "#C084FC", archive: "#7B7396", meta: "#3FD9CE",
      },
      fallback: "#9A8FB5", fire: "#E9CDFF", noSync: "#C77D2E",
      edgeDim: "#9689B0", edgeFire: "#C084FC", edgeLit: "#E9CDFF",
      dust: "#B3A6CC", labelInk: "#E6DEF2", labelHalo: "#0A0616",
    },
  }),

  make({
    id: "meridian",
    name: "MERIDIAN",
    note: "daylight — a paper dashboard around a dark instrument window",
    scheme: "light",
    // The one theme where the elevation rule was already native: paper is the
    // page, and a card is the whiter thing on it.
    void_: "#F2F5F7", surface: "#FFFFFF", inset: "#F7F9FA",
    scrim: "#253039",
    grid: "#23404E",
    accent: "#0E7C99", glow: "#0B6F8A",
    ink: "#16212B", ink2: "#4E606E", muted: "#A7B5BE",
    // Darkened for contrast against paper. Same meanings, same glyphs.
    amber: "#9A5B00", red: "#B92B2B", add: "#1B7A3D", bronze: "#8A5A1E",
    line: [0.34, 0.18],
    // Pastel over white — the haze is a 4% wash here, not a glow.
    haze: ["#7DD3FC", "#C4B5FD", "#99F6E4", "#BFDBFE"],
    neb: ["#00E5FF", "#C084FC", "#2DD4BF", "#60A5FA"],
    sky: {
      bg: "#060B12", surface: "#0D141D",
      ink: "#DDE7EF", ink2: "#90A5B5", muted: "#47596A",
      accent: "#38D6F0", glow: "#6FE9FF", amber: "#FFB020",
    },
    brain: {
      buckets: {
        root: "#FF7A7A", inbox: "#FFD873", daily: "#EEF3F7",
        academics: "#5CE894", areas: "#FFA24D", projects: "#6FB2FF",
        system: "#CB93FF", archive: "#7C8894", meta: "#3EE0CB",
      },
      fallback: "#90A5B5", fire: "#A8F0FF",
      // The one place a theme carries two bronzes: #8A5A1E is legible on paper
      // and invisible on the sky, so the ring gets the same hue lifted for its
      // own ground. Same family, same glyph, same meaning — a second *value*,
      // not a second colour.
      noSync: "#E0A46A",
      edgeDim: "#7896AA", edgeFire: "#38D6F0", edgeLit: "#9BEFFC",
      dust: "#96B9D2", labelInk: "#DDE7EF", labelHalo: "#050A11",
    },
  }),

  make({
    id: "graphite",
    name: "GRAPHITE",
    note: "neutral — structure in grey so only status and the graph carry hue",
    scheme: "dark",
    void_: "#0A0A0C", surface: "#131317", inset: "#0E0E11",
    scrim: "#030304",
    accent: "#9FB0BC", glow: "#D5E0E8",
    ink: "#E7E9EC", ink2: "#949AA1", muted: "#3E434A",
    amber: "#E0A33A", red: "#E0555F", add: "#6FBF73", bronze: "#B0885A",
    haze: ["#9FB0BC", "#7C8794", "#B8C2CC", "#63707C"],
    brain: {
      // Desaturated but still nine distinguishable hues — see rule 3. A grey
      // graph would match the shell and lose the one thing the picture is for.
      buckets: {
        root: "#D98C8C", inbox: "#D9C48C", daily: "#E8EAED",
        academics: "#8FC79A", areas: "#D9A87A", projects: "#8FAFD1",
        system: "#B3A3D1", archive: "#7A7F85", meta: "#8ACFC6",
      },
      fallback: "#949AA1", fire: "#E8F1F7", noSync: "#B0885A",
      edgeDim: "#8A939B", edgeFire: "#9FB0BC", edgeLit: "#D5E0E8",
      dust: "#A3ACB5", labelInk: "#E7E9EC", labelHalo: "#08080A",
    },
  }),

  make({
    id: "quartz",
    name: "QUARTZ",
    note: "the reference language — a tinted canvas under white cards, one accent kept for one thing",
    scheme: "light",
    // The three grounds the reference states outright, and the only palette
    // here whose lightness step is large enough to see from across the room.
    // MERIDIAN is also light, and this is not a second copy of it: MERIDIAN is
    // flat paper with a teal instrument accent, QUARTZ is tinted-canvas-under-
    // white with the faintest hairlines of any theme, because §3.2 leaves the
    // separating to space and to that step.
    void_: "#EEF0F8", surface: "#FFFFFF", inset: "#F5F6FA",
    scrim: "#1E2233",
    grid: "#3B3F63",
    // Indigo, and this is the one value in the file chosen rather than sampled
    // or inherited: the reference's own accent is a saturated magenta-pink, and
    // its closing line says not to take it. Indigo clears all six existing
    // accents — SYNAPSE's violet is lighter and pinker, MERIDIAN's is a dark
    // teal — and clears bronze by a mile, which rule 1 requires.
    accent: "#4F46E5", glow: "#6D63FF",
    ink: "#171A2B", ink2: "#5A6175", muted: "#A6ADC0",
    // Darkened for paper, exactly as MERIDIAN's are, and doing more work here:
    // these are now also the *text* colour inside a status chip, over a 12%
    // tint of themselves.
    amber: "#8A5000", red: "#B3243C", add: "#15703A", bronze: "#8A5A1E",
    // Fainter than any other theme's. The lightness step is the separator.
    line: [0.22, 0.11],
    haze: ["#C7BFFF", "#A5B4FC", "#DDD6FE", "#BFDBFE"],
    neb: ["#818CF8", "#C084FC", "#38BDF8", "#2DD4BF"],
    sky: {
      bg: "#0A0C18", surface: "#12162A",
      ink: "#DEE1F0", ink2: "#8E95AE", muted: "#464C66",
      accent: "#8B93FF", glow: "#ADB2FF", amber: "#FFB020",
    },
    brain: {
      // Bright, because the brain never sits on the canvas — it sits on the
      // sky above, which is dark in this theme for the same reason it is dark
      // in MERIDIAN: the canvas composites additively.
      buckets: {
        root: "#FF7A8F", inbox: "#FFD166", daily: "#E9EBF7",
        academics: "#5CE894", areas: "#FFA24D", projects: "#7C9DFF",
        system: "#C08BFF", archive: "#7C8399", meta: "#3EE0CB",
      },
      fallback: "#8E95AE", fire: "#B9BEFF",
      // The second bronze, for the same reason MERIDIAN carries one: #8A5A1E
      // is legible on the canvas and invisible on the sky.
      noSync: "#E0A46A",
      edgeDim: "#7F87A8", edgeFire: "#8B93FF", edgeLit: "#C3C7FF",
      dust: "#A2A9CC", labelInk: "#DEE1F0", labelHalo: "#070914",
    },
  }),
];

export const DEFAULT_THEME = THEMES[0];

/* --------------------------------------------------------------- the store */

const KEY = "sigma.theme";

let active: Theme = DEFAULT_THEME;
const listeners = new Set<() => void>();

/** Writes the palette onto :root. Custom properties inherit, so this one
 *  assignment re-paints every rule in App.css — including the ones inside
 *  overlays that are not mounted yet. */
function paint(t: Theme): void {
  const root = document.documentElement;
  for (const [k, v] of Object.entries(t.tokens)) root.style.setProperty(k, v);
  // Not used for styling — no rule may key on the theme (see the header). It is
  // here so `document.documentElement.dataset.theme` answers "which one is on"
  // in devtools and in a screenshot's DOM.
  root.dataset.theme = t.id;
}

export function getTheme(): Theme {
  return active;
}

export function themeById(id: string | null): Theme | undefined {
  return THEMES.find(t => t.id === id);
}

/**
 * Switch palettes.
 *
 * `remember: false` is what makes the picker's live preview safe — arrowing
 * through the list repaints without touching what survives a reload, so
 * backing out with Esc leaves nothing behind to undo.
 */
export function setTheme(t: Theme, remember = true): void {
  active = t;
  paint(t);
  if (remember) {
    try { localStorage.setItem(KEY, t.id); } catch { /* private mode — the
      palette still applies, it just will not survive the reload */ }
  }
  listeners.forEach(l => l());
}

/** Called once from main.tsx, before React mounts, so the first paint is
 *  already in the right palette rather than flashing VOID. */
export function bootTheme(): void {
  let saved: string | null = null;
  try { saved = localStorage.getItem(KEY); } catch { /* see above */ }
  setTheme(themeById(saved) ?? DEFAULT_THEME, false);
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

/** The active theme, as a hook. The canvas needs it as a value rather than as
 *  CSS, and so does the picker's own swatch row. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getTheme, getTheme);
}
