/**
 * answer.tsx — §4's answer grammar, as one component.
 *
 * The reference design language calls this its most transferable structural
 * idea, and the reason is that the order never varies: eyebrow, claim,
 * elaboration, confidence, provenance. A reader who has seen one answer knows
 * where to look in every other one.
 *
 * Two of its rules are the point, and both were being broken here:
 *
 * **The sources are *in* the answer, not behind a footnote.** Chat put them
 * above it, collapsed, behind a summary that counted *tool calls* — "3 lookups"
 * for a turn that read seven notes and grepped twice. The information was
 * already on the wire: `agent.describe()` returns the vault-relative path of
 * every note read, precisely so the brain can fire that node. This spends it on
 * the reader instead of only on the picture.
 *
 * **Confidence is never colour alone** — three dots, a word, and a sentence
 * saying what that word means *here*. That one needs the model's cooperation,
 * so `agent.py`'s orientation asks for a closing `confidence:: <level> - <why>`
 * line in the vault's own `key::value` grammar (the fifth use of that shape,
 * after `until::`, `cancelled::`, `skipped::` and `expired::`). It degrades
 * quietly: no line, no element. Nothing here fabricates a level.
 */
import { obsidianHref } from "./api";

export type Tool = { name: string; detail: string };

type Confidence = { level: "high" | "medium" | "low"; why: string };

/** Accepts the hyphen the orientation shows and the dashes a model reaches for
 *  anyway. The level is the load-bearing part; the reason may be empty. */
const CONF_RE = /^confidence::\s*(high|medium|low)\b\s*[-–—:]?\s*([\s\S]*)$/im;

/** Pulls the confidence line out of the prose. It is asked for as the last
 *  line, and taken from anywhere so a model that puts it one paragraph early
 *  does not leak `confidence::` into the rendered body. */
function splitConfidence(text: string): { body: string; conf: Confidence | null } {
  const m = CONF_RE.exec(text);
  if (!m) return { body: text, conf: null };
  return {
    body: (text.slice(0, m.index) + text.slice(m.index + m[0].length)).trim(),
    conf: { level: m[1].toLowerCase() as Confidence["level"], why: m[2].trim() },
  };
}

/** The first paragraph, promoted to a headline.
 *
 *  Split on the first newline, not the first *blank* line: `Markdown` above
 *  gives every non-empty line its own `<p>`, so a line IS a paragraph here.
 *  Splitting on `\n\n` looked more careful and promoted nothing at all, because
 *  a model writing ordinary prose separates paragraphs with one newline and the
 *  blank line never arrives.
 *
 *  The caller suppresses this while tokens are streaming — otherwise the whole
 *  half-written answer is "the first line" and the headline shrinks as you read
 *  it. */
/** Length as the reader sees it. `**bold**`, backticks and `[[a|b]]` all cost
 *  characters that never reach the screen, and measuring the raw line rejected
 *  a 251-character lead as if it were 300. */
function visibleLength(md: string): number {
  return md
    .replace(/\[\[([^\]|]+)\|([^\]]*)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\*\*|`/g, "")
    .length;
}

function splitClaim(text: string): { claim: string | null; rest: string } {
  const i = text.indexOf("\n");
  if (i <= 0) return { claim: null, rest: text };
  const first = text.slice(0, i).trim();
  // A heading, bullet, quote or table row is not a claim, and neither is a lead
  // long enough that setting it above body size would be shouting rather than
  // leading. The orientation asks for one sentence; models write two or three,
  // and a 300-character lead at 15.5px still reads as a lead.
  if (/^[#\-*>|]/.test(first) || visibleLength(first) > 300) {
    return { claim: null, rest: text };
  }
  return { claim: first, rest: text.slice(i + 1) };
}

function inline(text: string, vault: string, key: string) {
  const parts = text.split(/(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.filter(Boolean).map((p, i) => {
    const k = `${key}-${i}`;
    if (p.startsWith("[[") && p.endsWith("]]")) {
      const [target, label] = p.slice(2, -2).split("|");
      return (
        <a key={k} className="wikilink" href={obsidianHref(vault, target)}
           title={`Open ${target} in Obsidian`}>
          {label || target}
        </a>
      );
    }
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={k}>{p.slice(2, -2)}</strong>;
    if (p.startsWith("`") && p.endsWith("`")) return <code key={k}>{p.slice(1, -1)}</code>;
    return <span key={k}>{p}</span>;
  });
}

/** The React-nodes-only Markdown subset, moved here from chat.tsx with its
 *  guarantee intact: no `dangerouslySetInnerHTML` anywhere, so a vault note
 *  containing a stray `<script>` renders as text and never runs. */
export function Markdown({ text, vault }: { text: string; vault: string }) {
  const blocks: React.ReactNode[] = [];
  let list: React.ReactNode[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(<ul key={`ul-${blocks.length}`}>{list}</ul>);
      list = [];
    }
  };

  text.split("\n").forEach((line, i) => {
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (heading) {
      flush();
      blocks.push(<h3 key={i}>{inline(heading[2], vault, `h${i}`)}</h3>);
    } else if (bullet) {
      list.push(<li key={i}>{inline(bullet[1], vault, `li${i}`)}</li>);
    } else if (line.trim() === "") {
      flush();
    } else {
      flush();
      blocks.push(<p key={i}>{inline(line, vault, `p${i}`)}</p>);
    }
  });
  flush();
  return <>{blocks}</>;
}

const DOTS = { high: "●●●", medium: "●●○", low: "●○○" } as const;

/** Provenance. Reads are the sources; searches are how they were found, which
 *  is a different claim and gets a different word. Deduped, because a model
 *  re-reading a note to check something did not read two notes. */
function Provenance({ tools, blocked, vault }: {
  tools: Tool[]; blocked: string[]; vault: string;
}) {
  const reads = [...new Set(tools.filter(t => t.name === "Read").map(t => t.detail))];
  const searches = tools.filter(t => t.name === "Grep" || t.name === "Glob").length;
  if (!reads.length && !searches && !blocked.length) return null;

  return (
    <section className="prov">
      <h4>
        Sources behind this answer
        <span className="prov-count">
          {reads.length} note{reads.length === 1 ? "" : "s"}
          {searches > 0 && ` · ${searches} search${searches === 1 ? "" : "es"}`}
        </span>
      </h4>
      {reads.length > 0 && (
        <ul className="prov-list">
          {reads.map(p => (
            <li key={p}>
              <a href={obsidianHref(vault, p.replace(/\.md$/, ""))} title={p}>
                {p.split("/").pop()?.replace(/\.md$/, "")}
              </a>
              <span className="prov-where">{p.replace(/\/[^/]+$/, "")}</span>
            </li>
          ))}
        </ul>
      )}
      {/* A refusal belongs here rather than floating above the answer: it is a
          statement about what fed this, and the honest half of provenance is
          what could not be read. */}
      {blocked.map(b => (
        <p key={b} className="prov-sealed" title="gitignored — never sent to the model">
          ⊘ {b} — sealed, never sent
        </p>
      ))}
      {reads.length === 0 && searches > 0 && (
        <p className="prov-none">
          searched but opened nothing — this answer is not grounded in a note
        </p>
      )}
    </section>
  );
}

export default function Answer({ text, answerAt, tools, blocked, vault, streaming }: {
  text: string;
  /** Offset in `text` where the answer proper starts — everything before it is
   *  the model narrating its own lookups. See chat.tsx's `answerAt`. */
  answerAt?: number;
  tools: Tool[];
  blocked: string[];
  vault: string;
  /** while true the claim stays unpromoted — see splitClaim */
  streaming?: boolean;
}) {
  // Only trust the boundary if the model actually said something after its last
  // lookup. A turn that ends on a tool call has no post-lookup text, and
  // slicing there would render an empty answer under a full trail.
  const after = answerAt != null ? text.slice(answerAt).trim() : "";
  const said = after.length > 0 && !streaming ? after : text;

  const { body, conf } = splitConfidence(said);
  const { claim, rest } = streaming ? { claim: null, rest: body } : splitClaim(body);
  // The one eyebrow that varies: a turn that called propose_change did not
  // answer a question, it drafted a change, and saying so before the prose is
  // the difference between reading it as information and reading it as a thing
  // now waiting on you.
  const proposed = tools.some(t => t.name.endsWith("propose_change"));

  return (
    <div className="ans">
      <span className="ans-eyebrow">{proposed ? "Proposal drafted" : "Answer"}</span>
      {claim && <p className="ans-claim">{inline(claim, vault, "claim")}</p>}
      <div className="ans-body"><Markdown text={rest} vault={vault} /></div>

      {conf && (
        <p className="ans-conf">
          <span className={`ans-dots c-${conf.level}`} aria-hidden="true">
            {DOTS[conf.level]}
          </span>
          <b>{conf.level} confidence</b>
          {conf.why && <span className="ans-why"> — {conf.why}</span>}
        </p>
      )}

      {/* Not while streaming: the trail is showing the same lookups live, and
          a half-written source list under a half-written answer is the "3
          lookups" problem again with extra steps. */}
      {!streaming && <Provenance tools={tools} blocked={blocked} vault={vault} />}
    </div>
  );
}
