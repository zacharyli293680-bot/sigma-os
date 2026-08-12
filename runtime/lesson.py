#!/usr/bin/env python3
"""
lesson.py — the study-guide module grammar (study mode, S1).

One grammar, one owner, three consumers. The workbench renders a module through
this parser, `sigma doctor` runs `validate()` over every `type: module` note
(modules are co-writable by hand, and a hand-edit that breaks the grammar must
surface as a doctor finding rather than a silently unrenderable lesson), and the
applier's structural hold will call the same `validate()` on proposal content in
S7. Two modules parsing the same grammar with two regexes is precisely the drift
the vault's one-implementation rule exists to stop — the same reason the task
grammar lives only in todo.py.

**Why the format is a contract.** Generated modules auto-apply through
applier.py, so a parser must be able to reject a bad module mechanically —
"looks fine" is not a check. Every hard rule here mirrors the CLAUDE.md
"Study guides" section and the vault's sigma-os-study-plan §4/§5: segment count,
time-budget sum, three depths, required answers, per-segment provenance.

**Why this file is not called study.py.** The backend inserts runtime/ at
sys.path[0] while the tests insert interface/backend on top of it; a runtime
module named after a natural backend name resolves to two different files
depending on who imported first. This codebase has paid for that three times
(todo/queue, retro/review, agenda/calendar). `lesson` shadows nothing — not
stdlib, not the venv, not the backend.

Nothing here writes to the vault — parsing and validating is all it does. S3
added the practice sidecars (`study.state.json`, `study.jsonl`), and they are
machine-local files under runtime/, the same split todo.py made for its index:
what a checkbox cannot express lives beside the code, gitignored, and the one
durable trace (the session rollup's digest) goes through writes.py's git path
into the study log, never from here.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from sigma import DEFAULT_VAULT, frontmatter  # noqa: E402

# --------------------------------------------------------------------------
# the module grammar — the canonical copy
# --------------------------------------------------------------------------
# `## S<n> · <title> ⏱ <minutes>` opens a segment. The separator is U+00B7 and
# the clock is U+23F1, exactly as the contract's example writes them — a parser
# that also accepted lookalikes would train authors into a grammar the applier
# hold then refuses.
SEG_RE = re.compile(r"^##\s+S(\d+)\s+·\s+(.+?)\s+⏱\s+(\d+)\s*$")
H2_RE = re.compile(r"^##\s+\S")
DEPTH_RE = re.compile(r"^###\s+(Summary|Normal|In depth|Example)\s*$")
H3_RE = re.compile(r"^###\s+\S")
PRACTICE_RE = re.compile(r"^\?\?\s+(\S+)\s+·\s+(\S+)\s*$")
SOURCE_RE = re.compile(r"^source::\s*(.+?)\s*$")
# `figure:: <caption>` and then a raw, UNFENCED `<svg>…</svg>`. Unfenced is the
# whole point: Obsidian renders inline HTML in reading view, so the diagram
# draws in the vault as well as in the workbench, and a ```svg fence would show
# the source in both. Same `key:: value` shape as `source::`, `until::`,
# `cancelled::`, `skipped::` and `expired::` — one grammar, sixth use.
FIGURE_RE = re.compile(r"^figure::\s*(.*?)\s*$")
ITEM_KEY_RE = re.compile(r"^-\s+(hint|answer|solution|source)::\s*(.*?)\s*$")
QID_RE = re.compile(r"^q-(\d+)-(\d+)$")

KINDS = {"mcq", "numeric", "short", "code", "proof"}

# --------------------------------------------------------------------------
# what a figure may contain
# --------------------------------------------------------------------------
# An allow-list, not a block-list: anything not named here is refused, so a
# construct nobody thought of fails closed. The frontend walks the same two sets
# to build React elements (`interface/frontend/src/figure.tsx`), and
# `tests/test_figure_safety.py` fails if the two copies drift apart.
SVG_TAGS = {
    "svg", "g", "title", "desc", "defs", "marker",
    "line", "polyline", "polygon", "path", "rect", "circle", "ellipse",
    "text", "tspan",
}
SVG_ATTRS = {
    # geometry
    "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "width", "height", "d", "points", "dx", "dy", "transform",
    "viewBox", "preserveAspectRatio",
    # paint. `currentColor` is what the authoring prompt asks for, so a figure
    # inherits the room's ink instead of hardcoding a black that disappears the
    # day the ground changes.
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "opacity",
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-opacity",
    # text
    "text-anchor", "dominant-baseline", "font-size", "font-family",
    "font-weight", "font-style", "letter-spacing",
    # arrowheads
    "marker-end", "marker-start", "marker-mid",
    "markerWidth", "markerHeight", "refX", "refY", "orient", "markerUnits",
    # identity and accessibility
    "id", "class", "role", "aria-label", "aria-hidden", "xmlns",
}
# Named separately only so the refusal can say *why* rather than "not allowed":
# these are the ones someone would actually try.
SVG_DANGEROUS = {"script", "foreignObject", "image", "use", "a", "animate",
                 "set", "handler", "style"}

# The hard checks (study plan §4) — enforced, not advised.
ESTIMATE_MIN, ESTIMATE_MAX = 30, 60
SEGMENTS_MIN, SEGMENTS_MAX = 3, 7
PRACTICE_MIN, PRACTICE_MAX = 8, 12

ACADEMICS = ("02-Areas", "Academics")


# --------------------------------------------------------------------------
# frontmatter — sigma.frontmatter() reads flat fields only, so the one block
# list a module carries (sources:) is pulled here rather than teaching the
# shared helper YAML it doesn't otherwise need
# --------------------------------------------------------------------------
def _fm_block(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:i]
    return []


def _fm_sources(text: str) -> list[str]:
    out, in_sources = [], False
    for line in _fm_block(text):
        if re.match(r"^sources:\s*$", line):
            in_sources = True
            continue
        if in_sources:
            m = re.match(r"^\s+-\s+(.+?)\s*$", line)
            if m:
                out.append(m.group(1))
                continue
            in_sources = False
    return out


# --------------------------------------------------------------------------
# parsing — every structural element keeps its 1-based line number, because
# the provenance slot's whole answer is "this file, this line"
# --------------------------------------------------------------------------
def parse(text: str) -> dict:
    """Parse one module note. Never raises on bad structure — structural
    faults land in the returned dict's `problems` list, so validate() and the
    renderer see the same story."""
    fm = frontmatter(text) or {}
    problems: list[str] = []
    lines = text.splitlines()

    body_start = 0
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body_start = i + 1
                break

    # Slice the body into preamble + segments at top-level ## headings,
    # ignoring anything inside a ``` fence.
    seg_starts: list[int] = []
    in_fence = False
    for i in range(body_start, len(lines)):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if SEG_RE.match(line):
            seg_starts.append(i)
        elif H2_RE.match(line) and not line.startswith("# "):
            problems.append(f"line {i + 1}: unexpected H2 (only '## S<n> · "
                            f"<title> ⏱ <min>' segments are allowed): {line.strip()!r}")

    preamble_end = seg_starts[0] if seg_starts else len(lines)
    preamble = "\n".join(lines[body_start:preamble_end])

    segments = []
    for idx, start in enumerate(seg_starts):
        end = seg_starts[idx + 1] if idx + 1 < len(seg_starts) else len(lines)
        segments.append(_parse_segment(lines, start, end, problems))

    return {
        "type": str(fm.get("type") or "").strip(),
        "course": str(fm.get("course") or "").strip(),
        "module": _int_or_none(fm.get("module")),
        "unit": _int_or_none(fm.get("unit")),
        "title": str(fm.get("title") or "").strip(),
        "estimate": _int_or_none(fm.get("estimate")),
        "verified": str(fm.get("verified") or "").strip() or None,
        "tags": str(fm.get("tags") or "").strip(),
        "sources": _fm_sources(text),
        "preamble": preamble,
        "segments": segments,
        "problems": problems,
    }


def _int_or_none(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _trim_block(lines: list[str]) -> str:
    """Drop leading/trailing blank lines but keep indentation — a bare
    .strip() would eat the 4-space indent of a formula block that opens a
    section, and the renderer would show the formula as prose."""
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _parse_segment(lines: list[str], start: int, end: int, problems: list[str]) -> dict:
    m = SEG_RE.match(lines[start])
    seg = {
        "n": int(m.group(1)), "title": m.group(2).strip(),
        "minutes": int(m.group(3)), "line": start + 1,
        "sources": [], "summary": "", "normal": "", "in_depth": "",
        "example": None, "practice": [], "figure": None,
    }
    key_of = {"Summary": "summary", "Normal": "normal",
              "In depth": "in_depth", "Example": "example"}
    bucket = None          # which depth/example section content flows into
    buckets: dict[str, list[str]] = {}
    item = None            # the practice item currently being read
    in_fence = False

    def close_item():
        nonlocal item
        if item is None:
            return
        item["prompt"] = _trim_block(item.pop("_prompt"))
        if not item["prompt"]:
            problems.append(f"line {item['line']}: practice item "
                            f"{item['id']} has no question text")
        seg["practice"].append(item)
        item = None

    i = start + 1
    while i < end:
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if in_fence or line.lstrip().startswith("```"):
            if item is not None and item["_keys_seen"]:
                problems.append(f"line {i + 1}: content after a practice "
                                f"item's keyed lines")
            elif item is not None:
                item["_prompt"].append(line)
            elif bucket:
                buckets.setdefault(bucket, []).append(line)
            i += 1
            continue

        sm = SOURCE_RE.match(line)
        if sm and bucket is None and item is None:
            seg["sources"].append({"path": sm.group(1), "line": i + 1})
            i += 1
            continue

        gm = FIGURE_RE.match(line)
        if gm and bucket is None and item is None:
            caption = gm.group(1)
            svg: list[str] = []
            j = i + 1
            while j < end and "</svg>" not in lines[j]:
                svg.append(lines[j])
                j += 1
            if j < end:
                svg.append(lines[j])
                j += 1
            else:
                problems.append(f"line {i + 1}: figure:: is never closed — a "
                                f"figure runs from the key to its `</svg>`")
            if seg["figure"] is not None:
                # One per segment. A segment teaches one idea; two diagrams for
                # it means the split is wrong, and the alternative is a rule
                # about ordering that nothing else in this grammar needs.
                problems.append(f"line {i + 1}: a second figure:: in segment "
                                f"S{seg['n']} — one figure per segment")
            elif not caption:
                problems.append(f"line {i + 1}: figure:: has no caption — a "
                                f"diagram nobody can describe is not evidence")
            else:
                seg["figure"] = {"caption": caption,
                                 "svg": "\n".join(svg).strip(),
                                 "line": i + 1}
            i = j
            continue

        dm = DEPTH_RE.match(line)
        if dm:
            close_item()
            bucket = key_of[dm.group(1)]
            if bucket in buckets:
                problems.append(f"line {i + 1}: duplicate '### {dm.group(1)}' "
                                f"in segment S{seg['n']}")
            buckets.setdefault(bucket, [])
            i += 1
            continue
        if H3_RE.match(line):
            problems.append(f"line {i + 1}: unknown H3 (only Summary, Normal, "
                            f"In depth, Example): {line.strip()!r}")
            close_item()
            bucket = None
            i += 1
            continue

        pm = PRACTICE_RE.match(line)
        if pm:
            close_item()
            bucket = None
            item = {"id": pm.group(1), "kind": pm.group(2), "line": i + 1,
                    "hints": [], "answer": None, "solution": None,
                    "source": None, "_prompt": [], "_keys_seen": False}
            i += 1
            continue
        if line.startswith("??"):
            # A near-miss opener silently absorbed as prose would leak the
            # question and its answer into a depth section while the module
            # still validated clean — name it instead.
            problems.append(f"line {i + 1}: malformed practice opener "
                            f"(expected '?? <q-id> · <kind>'): {line.strip()!r}")
            close_item()
            bucket = None
            i += 1
            continue

        km = ITEM_KEY_RE.match(line)
        if km and item is not None:
            key, val = km.group(1), km.group(2)
            item["_keys_seen"] = True
            if key == "hint":
                item["hints"].append(val)
            elif item[key] is not None:
                problems.append(f"line {i + 1}: duplicate {key}:: in "
                                f"practice item {item['id']}")
            else:
                item[key] = val
            i += 1
            continue

        if item is not None:
            if line.strip() and item["_keys_seen"]:
                problems.append(f"line {i + 1}: content after a practice "
                                f"item's keyed lines")
            elif line.strip() or item["_prompt"]:
                item["_prompt"].append(line)
        elif bucket is not None:
            buckets.setdefault(bucket, []).append(line)
        elif line.strip():
            problems.append(f"line {i + 1}: content before the first "
                            f"'###' section of segment S{seg['n']}")
        i += 1

    close_item()
    for key, collected in buckets.items():
        seg[key] = _trim_block(collected)
    for it in seg["practice"]:
        it.pop("_keys_seen", None)
    return seg


# --------------------------------------------------------------------------
# serialisation — the canonical form. The S7 generator composes through this
# and re-parses its own output before a proposal is written; on canonical
# text, serialize(parse(s)) == s, byte for byte. Hand-written modules keep
# their own paragraph wrapping — this is not a formatter for them, and the
# guarantee there is structural: parse(serialize(parse(text))) sees the same
# module.
# --------------------------------------------------------------------------
def serialize(d: dict) -> str:
    out = ["---",
           f"type: {d['type']}",
           f"course: {d['course']}",
           f"module: {d['module'] if d['module'] is not None else ''}",
           f"unit: {d['unit'] if d['unit'] is not None else ''}",
           f"title: {d['title']}",
           f"estimate: {d['estimate'] if d['estimate'] is not None else ''}",
           "sources:"]
    out += [f"  - {s}" for s in d["sources"]]
    out.append(f"verified: {d['verified'] or ''}")
    out.append(f"tags: {d['tags']}")
    out.append("---")
    out.append("")
    if d["preamble"].strip():
        out.append(d["preamble"].strip())
        out.append("")
    for s in d["segments"]:
        out.append(f"## S{s['n']} · {s['title']} ⏱ {s['minutes']}")
        out += [f"source:: {src['path']}" for src in s["sources"]]
        # Beside `source::`, and before the depth headings, because that is
        # where it was parsed from. Canonicalisation runs through here on every
        # generated module — a figure this dropped would be authored, validated
        # and then quietly deleted on the way to disk.
        if s.get("figure"):
            out.append(f"figure:: {s['figure']['caption']}")
            out.append(s["figure"]["svg"])
        for key, label in (("summary", "Summary"), ("normal", "Normal"),
                           ("in_depth", "In depth")):
            out += ["", f"### {label}", "", s[key]]
        if s["example"] is not None:
            out += ["", "### Example", "", s["example"]]
        for it in s["practice"]:
            out += ["", f"?? {it['id']} · {it['kind']}", it["prompt"]]
            out += [f"- hint:: {h}" for h in it["hints"]]
            out.append(f"- answer:: {it['answer'] or ''}")
            out.append(f"- solution:: {it['solution'] or ''}")
            if it["source"]:
                out.append(f"- source:: {it['source']}")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# --------------------------------------------------------------------------
# validation — the checks are the contract; a module that fails any of them
# is held, never rendered as if fine
# --------------------------------------------------------------------------
def validate_svg(svg: str, tag: str = "figure") -> list[str]:
    """Every way this figure violates the allow-list; [] means it conforms.

    Well-formed XML is required rather than merely preferred. SVG inside HTML
    does not have to be, but a parser that guesses at unclosed tags is a parser
    whose idea of the document can differ from the browser's — and the whole
    point of this function is that what it approved is what gets rendered.
    """
    import xml.etree.ElementTree as ET

    out: list[str] = []
    if not svg.strip():
        return [f"{tag}: no SVG follows the caption"]
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        return [f"{tag}: not well-formed XML ({e}) — a figure is checked before "
                f"it is rendered, so it has to be parseable the same way twice"]

    def bare(name: str) -> str:
        return name.split("}", 1)[1] if "}" in name else name

    if bare(root.tag) != "svg":
        out.append(f"{tag}: the outer element is <{bare(root.tag)}>, expected <svg>")
    if not (root.get("viewBox") or "").strip():
        out.append(f"{tag}: <svg> has no viewBox — without one it cannot scale "
                   f"to the reading column")

    for el in root.iter():
        name = bare(el.tag)
        if name in SVG_DANGEROUS:
            out.append(f"{tag}: <{name}> is refused — a figure draws, it does "
                       f"not script, fetch or embed")
            continue
        if name not in SVG_TAGS:
            out.append(f"{tag}: <{name}> is not on the allow-list")
            continue
        for attr, val in el.attrib.items():
            a = bare(attr)
            low = a.lower()
            if low.startswith("on"):
                out.append(f"{tag}: <{name} {a}=…> — event handlers are refused")
            elif low in ("href", "xlink:href") or low.endswith(":href"):
                out.append(f"{tag}: <{name} {a}=…> — a figure never links out")
            elif a not in SVG_ATTRS:
                out.append(f"{tag}: <{name} {a}=…> is not on the allow-list")
            elif "url(" in val.lower() or "javascript:" in val.lower():
                out.append(f"{tag}: <{name} {a}=…> references an external "
                           f"resource — a figure is self-contained")
    return out


def validate(text: str, vault: Path | None = None) -> list[str]:
    """Return every way this module note violates the grammar; [] means it
    conforms. `vault` enables source-resolution checks (invariant 7)."""
    d = parse(text)
    out = list(d["problems"])

    if d["type"] != "module":
        out.append(f"frontmatter: type is {d['type']!r}, expected 'module'")
    if not d["course"]:
        out.append("frontmatter: course is blank")
    if d["module"] is None:
        out.append("frontmatter: module number is missing or not an integer")
    if not d["title"]:
        out.append("frontmatter: title is blank")
    if not d["sources"]:
        out.append("frontmatter: sources list is empty — a module authored "
                   "from nothing is unverified content")

    est = d["estimate"]
    if est is None:
        out.append("frontmatter: estimate is missing or not an integer")
    elif not ESTIMATE_MIN <= est <= ESTIMATE_MAX:
        out.append(f"frontmatter: estimate {est} outside "
                   f"{ESTIMATE_MIN}–{ESTIMATE_MAX} minutes — split or merge")

    segs = d["segments"]
    if not SEGMENTS_MIN <= len(segs) <= SEGMENTS_MAX:
        out.append(f"{len(segs)} segment(s) — the rule is "
                   f"{SEGMENTS_MIN}–{SEGMENTS_MAX} per module")
    if est is not None and segs:
        total = sum(s["minutes"] for s in segs)
        if total != est:
            out.append(f"segment ⏱ values sum to {total}, estimate says {est}")

    seen_numbers = set()
    for s in segs:
        tag = f"segment S{s['n']} (line {s['line']})"
        if s["n"] in seen_numbers:
            out.append(f"{tag}: duplicate segment number")
        seen_numbers.add(s["n"])
        if not s["sources"]:
            out.append(f"{tag}: no source:: line — unverified content is held")
        for depth, label in (("summary", "Summary"), ("normal", "Normal"),
                             ("in_depth", "In depth")):
            if not s[depth]:
                out.append(f"{tag}: '### {label}' is missing or empty")
        if s["example"] is not None and not s["example"]:
            out.append(f"{tag}: '### Example' is present but empty")
        if s.get("figure"):
            out += validate_svg(s["figure"]["svg"],
                                f"{tag} figure (line {s['figure']['line']})")

    if segs and not any(s["example"] for s in segs):
        out.append("no segment has an '### Example' — at least one per module")

    items = [it for s in segs for it in s["practice"]]
    if segs and not PRACTICE_MIN <= len(items) <= PRACTICE_MAX:
        out.append(f"{len(items)} practice item(s) — the rule is "
                   f"{PRACTICE_MIN}–{PRACTICE_MAX} per module")
    seen_ids = set()
    for it in items:
        tag = f"practice item {it['id']} (line {it['line']})"
        if it["kind"] not in KINDS:
            out.append(f"{tag}: unknown kind {it['kind']!r} "
                       f"(mcq | numeric | short | code | proof)")
        qm = QID_RE.match(it["id"])
        if not qm:
            out.append(f"{tag}: id does not match q-<module>-<n>")
        elif d["module"] is not None and int(qm.group(1)) != d["module"]:
            out.append(f"{tag}: id names module {qm.group(1)} but this is "
                       f"module {d['module']}")
        if it["id"] in seen_ids:
            out.append(f"{tag}: duplicate question id")
        seen_ids.add(it["id"])
        if not it["answer"]:
            out.append(f"{tag}: answer:: is missing — this holds the module")
        if not it["solution"]:
            out.append(f"{tag}: solution:: is missing — this holds the module")

    if vault is not None:
        for rel in d["sources"]:
            if not (vault / rel).is_file():
                out.append(f"frontmatter source does not resolve: {rel}")
        for s in segs:
            for src in s["sources"]:
                if not (vault / src["path"]).is_file():
                    out.append(f"segment S{s['n']} source does not resolve "
                               f"(line {src['line']}): {src['path']}")
            for it in s["practice"]:
                if it["source"] and not (vault / it["source"]).is_file():
                    out.append(f"practice item {it['id']} source does not "
                               f"resolve: {it['source']}")
    return out


# --------------------------------------------------------------------------
# the vault walk — where modules live and how the API finds one
# --------------------------------------------------------------------------
def _default_split(vault: Path, rels: list[str]):
    """todo.py's pattern, kept identical on purpose: fail *closed*. If the
    privacy module cannot answer, everything reads as sealed — the failure
    direction is an empty module list, which is loud, rather than sealed
    material on screen, which is not recoverable."""
    backend = str(HERE.parent / "interface" / "backend")
    if backend not in sys.path:
        sys.path.append(backend)
    try:
        from privacy import gitignore_scan
    except Exception:
        return set(rels), set()
    sealed, no_sync, _ = gitignore_scan(Path(vault), rels)
    return sealed, no_sync


def scan(vault: Path, split=None) -> list[dict]:
    """Every `type: module` note under 02-Areas/Academics/*/guide/, with its
    validation problems. Re-derived on every call and never cached here — the
    same rule as the queue: editing a note in Obsidian can never disagree."""
    root = vault.joinpath(*ACADEMICS)
    if not root.is_dir():
        return []
    paths = sorted(root.glob("*/guide/*.md"))
    rels = [p.relative_to(vault).as_posix() for p in paths]
    sealed, _ = (split or _default_split)(vault, rels)

    out = []
    for p, rel in zip(paths, rels):
        if rel in sealed:
            continue
        try:
            # utf-8-sig: a Windows editor's BOM would otherwise hide the
            # frontmatter from the type check and the module would vanish
            # from scan, the API and doctor without a word — the exact
            # silent failure this module exists to prevent.
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        fm = frontmatter(text) or {}
        if str(fm.get("type") or "").strip() != "module":
            continue
        d = parse(text)
        problems = validate(text, vault=vault)
        # The contract's naming rule is one string: the `course:` field equals
        # the folder name (CLAUDE.md §Naming). A drifted field would key this
        # module's attempts under a course the rollup can never find — the
        # digest would quietly never land — so the drift is a held problem
        # here, where doctor and the renderer both see it.
        folder = p.parents[1].name
        if d["course"] and d["course"] != folder:
            problems.append(f"course field says {d['course']!r} but the note "
                            f"lives in {folder}/ — the naming rule is one "
                            f"string, and attempts are keyed by the field")
        out.append({
            "course": d["course"], "module": d["module"], "unit": d["unit"],
            "title": d["title"], "estimate": d["estimate"], "file": rel,
            "segments": len(d["segments"]),
            "practice": sum(len(s["practice"]) for s in d["segments"]),
            "problems": problems,
        })
    out.sort(key=lambda r: (r["course"], r["module"] if r["module"] is not None else 0))

    # Two files claiming the same (course, module) would be silently aliased —
    # load() serves whichever sorts first. Flag both instead.
    seen_key: dict = {}
    for r in out:
        key = (r["course"].lower(), r["module"])
        if key in seen_key and r["module"] is not None:
            msg = (f"duplicate module number: {seen_key[key]} and {r['file']} "
                   f"both claim {r['course']} M{r['module']}")
            r["problems"].append(msg)
            for prev in out:
                if prev["file"] == seen_key[key]:
                    prev["problems"].append(msg)
        else:
            seen_key.setdefault(key, r["file"])
    return out


def load(vault: Path, course: str, module_no: int, split=None) -> dict | None:
    """One parsed module for the API, or None if no such module exists.
    The full parse plus file identity and validation problems."""
    for row in scan(vault, split=split):
        if row["course"].lower() == course.lower() and row["module"] == module_no:
            p = vault / row["file"]
            try:
                text = p.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                return None
            d = parse(text)
            d["file"] = row["file"]
            # Single-file validation, plus what only the whole-course scan can
            # see (a duplicate module number, a course field that drifted from
            # its folder) — held either way.
            d["problems"] = validate(text, vault=vault) + [
                p_ for p_ in row["problems"]
                if p_.startswith(("duplicate module", "course field"))]
            return d
    return None


# --------------------------------------------------------------------------
# the chain grammar — one checkbox row per module in <code>-guide.md (§5.2)
# --------------------------------------------------------------------------
# The chain and the module are the guide family's two grammars, and this file
# owns both for the same one-implementation reason. `[-]` is in the row regex
# on purpose: todo.py's TASK_RE deliberately cannot see a skipped row (that is
# what advances the frontier past a skip), but the renderer must show it
# dimmed rather than gone, so the chain reads its own note here.

CHAIN_ROW_RE = re.compile(r"^\s*[-*]\s+\[( |x|X|-)\]\s+(.*\S)\s*$")
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
SKIPPED_KEY_RE = re.compile(r"skipped::(\d{4}-\d{2}-\d{2})")
DATED_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")


def parse_chain(text: str) -> list[dict]:
    """Every checkbox row of a chain note, skipped rows included, in document
    order — which is the frontier's order (todo.py sorts (file, order), no
    date consulted). Fence-guarded like every other scanner."""
    rows = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = CHAIN_ROW_RE.match(line)
        if not m:
            continue
        box, body = m.group(1), m.group(2)
        lm = WIKILINK_RE.search(body)
        sm = SKIPPED_KEY_RE.search(body)
        dm = DATED_RE.search(body)
        rows.append({
            "line": i, "raw": line,
            "state": ("done" if box in "xX"
                      else "skipped" if box == "-" else "open"),
            "text": body,
            "target": lm.group(1).strip() if lm else None,
            "label": ((lm.group(2) or lm.group(1).split("/")[-1]).strip()
                      if lm else None),
            "skipped": sm.group(1) if sm else None,
            "date": dm.group(1) if dm else None,
        })
    return rows


def course_folder(vault: Path, course: str) -> Path | None:
    """The course's folder under Academics, matched case-insensitively —
    course codes are uppercase folder names, chain basenames are lowercase."""
    root = vault.joinpath(*ACADEMICS)
    if not root.is_dir():
        return None
    for d in sorted(root.iterdir()):
        if d.is_dir() and d.name.lower() == course.lower():
            return d
    return None


def guide(vault: Path, course: str, split=None) -> dict | None:
    """One course's chain, progress and blueprint status — the payload behind
    GET /api/guide/{course}. The names are fixed by the contract, so nothing
    is searched for. A course with a blueprint but no chain yet (drafted,
    awaiting approval — S7's gate) returns a chain-less payload with
    `file: None` rather than 404ing: the generate affordance lives exactly
    there. None only when the course has neither note."""
    folder = course_folder(vault, course)
    if folder is None:
        return None
    code = folder.name.lower()
    p = folder / f"{code}-guide.md"
    bp = folder / f"{code}-guide-blueprint.md"
    rel = p.relative_to(vault).as_posix() if p.is_file() else None
    bp_rel = bp.relative_to(vault).as_posix() if bp.is_file() else None
    sealed, _ = (split or _default_split)(vault, [r for r in (rel, bp_rel) if r])
    if rel in sealed:
        return None
    if bp_rel in sealed:
        bp_rel = None                       # a sealed blueprint reads as absent
    rows = []
    if rel is not None:
        try:
            rows = parse_chain(p.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            return None

    # `status: draft | approved`, read mechanically — the same field the S7
    # applier hold checks. None when no blueprint note exists yet. `planned`/
    # `missing` are the generate affordance's arithmetic: how many rows the
    # plan holds, and how many have no note on disk yet — computed fresh from
    # the blueprint and the guide/ folder, never cached, like the rows.
    blueprint = None
    planned = missing = None
    if bp_rel is not None:
        try:
            bp_text = bp.read_text(encoding="utf-8-sig", errors="replace")
            # A blank status is a malformed blueprint, not an absent one —
            # doctor flags it, and the payload must still render so the
            # workbench can SAY so instead of falling into the no-guide
            # dead end and offering to draft a plan that already exists.
            blueprint = (str((frontmatter(bp_text) or {}).get("status")
                             or "").strip() or "unstated")
            plan = parse_blueprint(bp_text)
            have_m, have_c = existing_units(vault, course)
            planned = len(plan["rows"])
            missing = sum(1 for r in plan["rows"]
                          if (r["n"] not in have_m if r["kind"] == "module"
                              else r["n"] not in have_c))
        except OSError:
            bp_rel = None                   # unreadable reads as absent
    if rel is None and bp_rel is None:
        return None

    return {
        "course": folder.name, "file": rel, "rows": rows,
        "total": len(rows),
        "done": sum(1 for r in rows if r["state"] == "done"),
        "skipped": sum(1 for r in rows if r["state"] == "skipped"),
        "frontier": next((r for r in rows if r["state"] == "open"), None),
        "blueprint": blueprint,
        "planned": planned,
        "missing": missing,
    }


# --------------------------------------------------------------------------
# the checkpoint grammar — a unit's assessment (§5.4, study S6)
# --------------------------------------------------------------------------
# A checkpoint is the module grammar minus the teaching: the same parser, the
# same segment headings and practice openers, but practice items only — no
# depth levels, no example, and ids carry the checkpoint's own number
# (`q-cp1-3`) so they can never collide with a module's `q-<module>-<n>`.
# One parser for both is the point: a second grammar would be a second owner.

CP_QID_RE = re.compile(r"^q-cp(\d+)-(\d+)$")


def _fm_covers(text: str) -> list[int]:
    """The `covers:` list, in either YAML spelling. Inline `[2, 3]` is what
    the schema writes, but Obsidian's Properties panel rewrites inline lists
    into block form the moment it touches the note — `sources:` has
    _fm_sources for exactly this, and a covers reader that only knew the
    inline form held a conforming note every time Obsidian saved it. Digits
    are the grammar; anything else in a value is prose, not this parser's
    business. `covers` deliberately is NOT cross-checked against authored
    modules: it may name planned ones (the pilot's CP1 covers M03 before the
    note exists); checking it against the approved blueprint is S7's job."""
    fm = frontmatter(text) or {}
    inline = [int(m) for m in re.findall(r"\d+", str(fm.get("covers") or ""))]
    if inline:
        return inline
    out, in_covers = [], False
    for line in _fm_block(text):
        if re.match(r"^covers:\s*$", line):
            in_covers = True
            continue
        if in_covers:
            m = re.match(r"^\s+-\s+(\d+)\s*$", line)
            if m:
                out.append(int(m.group(1)))
                continue
            in_covers = False
    return out


def validate_checkpoint(text: str, vault: Path | None = None) -> list[str]:
    """Every way this checkpoint note violates its contract; [] conforms.

    Deliberately not a flag on validate(): the two contracts share a parser
    but disagree about what must and must not exist (a module is held
    *without* depth levels, a checkpoint is held *with* them), and one
    function serving both would be a function whose rules depend on who is
    asking."""
    d = parse(text)
    fm = frontmatter(text) or {}
    out = list(d["problems"])

    if d["type"] != "checkpoint":
        out.append(f"frontmatter: type is {d['type']!r}, expected 'checkpoint'")
    if not d["course"]:
        out.append("frontmatter: course is blank")
    cp = _int_or_none(fm.get("checkpoint"))
    if cp is None:
        out.append("frontmatter: checkpoint number is missing or not an integer")
    covers = _fm_covers(text)
    if not covers:
        out.append("frontmatter: covers is empty — a checkpoint must name "
                   "the modules it assesses")
    date = str(fm.get("date") or "").strip()
    if date:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            out.append(f"frontmatter: date {date!r} is not YYYY-MM-DD")

    segs = d["segments"]
    if not segs:
        out.append("no segments — a checkpoint groups its items under "
                   "'## S<n> · <topic> ⏱ <min>' headings like a module")
    seen_numbers = set()
    for s in segs:
        tag = f"segment S{s['n']} (line {s['line']})"
        if s["n"] in seen_numbers:
            out.append(f"{tag}: duplicate segment number")
        seen_numbers.add(s["n"])
        if not s["sources"]:
            out.append(f"{tag}: no source:: line — unverified content is held")
        if s["example"] is not None:
            out.append(f"{tag}: has '### Example' — a checkpoint carries "
                       f"practice only")
        # A checkpoint may carry a figure: a question about a bracket needs the
        # bracket. It is held to the same allow-list, because the difference
        # between a module and a checkpoint is what they teach, not what they
        # are allowed to render.
        if s.get("figure"):
            out += validate_svg(s["figure"]["svg"],
                                f"{tag} figure (line {s['figure']['line']})")

    # Depth headings are found by scanning the text, not by truthiness on the
    # parsed buckets: parse() renders a present-but-EMPTY '### Summary' as ""
    # — indistinguishable from absent — so a module stripped down to bare
    # depth headings would slip a content check. The heading itself is the
    # violation (§5.4), with or without prose under it.
    in_fence = False
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        dm = re.match(r"^###\s+(Summary|Normal|In depth)\s*$", line)
        if dm:
            out.append(f"line {i}: has '### {dm.group(1)}' — a checkpoint "
                       f"carries practice only, never depth levels (§5.4)")

    items = [it for s in segs for it in s["practice"]]
    if segs and not items:
        out.append("no practice items — a checkpoint with nothing to answer "
                   "assesses nothing")
    seen_ids = set()
    for it in items:
        tag = f"practice item {it['id']} (line {it['line']})"
        if it["kind"] not in KINDS:
            out.append(f"{tag}: unknown kind {it['kind']!r} "
                       f"(mcq | numeric | short | code | proof)")
        qm = CP_QID_RE.match(it["id"])
        if not qm:
            out.append(f"{tag}: id does not match q-cp<checkpoint>-<n>")
        elif cp is not None and int(qm.group(1)) != cp:
            out.append(f"{tag}: id names checkpoint {qm.group(1)} but this "
                       f"is checkpoint {cp}")
        if it["id"] in seen_ids:
            out.append(f"{tag}: duplicate question id")
        seen_ids.add(it["id"])
        if not it["answer"]:
            out.append(f"{tag}: answer:: is missing — this holds the checkpoint")
        if not it["solution"]:
            out.append(f"{tag}: solution:: is missing — this holds the checkpoint")

    if vault is not None:
        for s in segs:
            for src in s["sources"]:
                if not (vault / src["path"]).is_file():
                    out.append(f"segment S{s['n']} source does not resolve "
                               f"(line {src['line']}): {src['path']}")
            for it in s["practice"]:
                if it["source"] and not (vault / it["source"]).is_file():
                    out.append(f"practice item {it['id']} source does not "
                               f"resolve: {it['source']}")
    return out


def _h1(text: str) -> str | None:
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    return m.group(1) if m else None


def validate_any(text: str, vault: Path | None = None) -> list[str]:
    """Dispatch on the note's own `type:` — what the CLI and any generic
    caller should use. Running the module validator over a checkpoint
    reports ~20 wrong-grammar complaints (missing depths, segment counts)
    that bury the one real problem; the note says which contract it claims,
    so judge it by that one."""
    fm = frontmatter(text) or {}
    kind = str(fm.get("type") or "").strip()
    if kind == "checkpoint":
        return validate_checkpoint(text, vault=vault)
    if kind == "guide-blueprint":
        return validate_blueprint(text, vault=vault)
    if kind == "reference":
        return validate_reference(text, vault=vault)
    return validate(text, vault=vault)


def scan_checkpoints(vault: Path, split=None) -> list[dict]:
    """Every `type: checkpoint` note under guide/, with its problems — the
    same walk as scan(), same fail-closed sealing, same drift checks."""
    root = vault.joinpath(*ACADEMICS)
    if not root.is_dir():
        return []
    paths = sorted(root.glob("*/guide/*.md"))
    rels = [p.relative_to(vault).as_posix() for p in paths]
    sealed, _ = (split or _default_split)(vault, rels)

    out = []
    for p, rel in zip(paths, rels):
        if rel in sealed:
            continue
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        fm = frontmatter(text) or {}
        if str(fm.get("type") or "").strip() != "checkpoint":
            continue
        d = parse(text)
        problems = validate_checkpoint(text, vault=vault)
        folder = p.parents[1].name
        if d["course"] and d["course"] != folder:
            problems.append(f"course field says {d['course']!r} but the note "
                            f"lives in {folder}/ — the naming rule is one "
                            f"string, and attempts are keyed by the field")
        out.append({
            "course": d["course"],
            "checkpoint": _int_or_none(fm.get("checkpoint")),
            "covers": _fm_covers(text),
            "date": str(fm.get("date") or "").strip() or None,
            "title": _h1(text) or (f"Checkpoint {_int_or_none(fm.get('checkpoint'))}"
                                   if _int_or_none(fm.get("checkpoint")) else p.stem),
            "file": rel,
            "segments": len(d["segments"]),
            "practice": sum(len(s["practice"]) for s in d["segments"]),
            "problems": problems,
        })
    out.sort(key=lambda r: (r["course"],
                            r["checkpoint"] if r["checkpoint"] is not None else 0))

    seen_key: dict = {}
    for r in out:
        key = (r["course"].lower(), r["checkpoint"])
        if key in seen_key and r["checkpoint"] is not None:
            msg = (f"duplicate checkpoint number: {seen_key[key]} and "
                   f"{r['file']} both claim {r['course']} CP{r['checkpoint']}")
            r["problems"].append(msg)
            for prev in out:
                if prev["file"] == seen_key[key]:
                    prev["problems"].append(msg)
        else:
            seen_key.setdefault(key, r["file"])
    return out


def load_checkpoint(vault: Path, course: str, cp_no: int, split=None) -> dict | None:
    """One parsed checkpoint for the API, or None. load()'s shape — the full
    parse plus identity, `covers`, `date` and every validation problem."""
    for row in scan_checkpoints(vault, split=split):
        if row["course"].lower() == course.lower() and row["checkpoint"] == cp_no:
            p = vault / row["file"]
            try:
                text = p.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                return None
            d = parse(text)
            d["file"] = row["file"]
            d["checkpoint"] = row["checkpoint"]
            d["covers"] = row["covers"]
            d["date"] = row["date"]
            d["title"] = row["title"]
            d["problems"] = validate_checkpoint(text, vault=vault) + [
                p_ for p_ in row["problems"]
                if p_.startswith(("duplicate checkpoint", "course field"))]
            return d
    return None


# --------------------------------------------------------------------------
# the blueprint grammar — the approved plan a guide is generated from (§5.3)
# --------------------------------------------------------------------------
# One row per planned module or checkpoint, in teaching order — the row order
# IS the chain order, so approval of the note is approval of the sequence. The
# grammar reuses the family's own shapes on purpose: the module row carries the
# segment heading's `·` and `⏱`, sources are indented `- source::` children,
# and a checkpoint row names what it covers. Zach edits rows by hand before
# flipping `status: approved`, so a parser must reject a bad row mechanically —
# the same doctrine as the module body, and the same single owner.

BP_MODULE_RE = re.compile(r"^\s*-\s+M(\d+)\s+·\s+(.+?)\s+⏱\s+(\d+)\s*$")
BP_CP_RE = re.compile(r"^\s*-\s+CP(\d+)\s+·\s+(.*\S)\s*$")
BP_UNIT_RE = re.compile(r"^##\s+Unit\s+(\d+)(?:\s+·\s+(.+?))?\s*$")
BP_SRC_RE = re.compile(r"^\s+-\s+source::\s*(.+?)\s*$")
BP_COVERS_RE = re.compile(r"^(?:(.*?)\s+·\s+)?covers\s+(.+?)$")

# --------------------------------------------------------------------------
# the reference sheet (study S9)
# --------------------------------------------------------------------------
# One note per course holding the things you look *up* rather than learn: the
# equations, definitions, constants, tables and procedures a question needs to
# hand. Two tiers live in the same note rather than two notes, for the reason
# the module's three depths live in one segment — the second tier is a *view*
# of the material, not a second body of it, and two files would drift the
# moment one was edited.
#
#   ## <section>
#
#   ### <entry title>
#   kind:: equation | definition | constant | table | procedure
#   tier:: exam | full
#   <body: LaTeX, prose, a markdown table>
#
# `exam` is the subset that survives onto the one double-sided sheet an exam
# allows; `full` is everything else the detailed view adds. So the simplified
# view is a *filter*, never a rewrite: an entry says the same thing in both,
# and there is only one place to correct it.
REF_SECTION_RE = re.compile(r"^##\s+(?!#)(.+?)\s*$")
REF_ENTRY_RE = re.compile(r"^###\s+(.+?)\s*$")
REF_KEY_RE = re.compile(r"^(kind|tier)::\s*(.*?)\s*$")
REF_KINDS = {"equation", "definition", "constant", "table", "procedure"}
REF_TIERS = {"exam", "full"}

# What fits on one sheet of paper, both sides, at a density a person can still
# read under time pressure. Measured against the real thing rather than guessed
# at: ~55 lines a side at 10pt, ~62 characters a line, two sides — call it 6800
# and round up for the fact that a display equation buys back the words it
# replaces. It is a *problem* rather than a warning when the exam tier exceeds
# it, because a simplified sheet that does not fit is the one thing this tier
# exists to guarantee, and the generator's repair pass can act on a problem.
EXAM_BUDGET = 7000


def parse_reference(text: str) -> dict:
    """A reference note → {course, sections:[{title, entries:[…]}], problems}.

    Entries carry `kind`, `tier`, `title` and `body`. Unknown keys are left in
    the body rather than dropped: a reference is prose a person also reads in
    Obsidian, and silently eating a line there would be worse than showing it.
    """
    fm = frontmatter(text) or {}
    out = {
        "type": str(fm.get("type") or "").strip(),
        "course": str(fm.get("course") or "").strip(),
        "sections": [],
        "problems": [],
    }
    problems = out["problems"]
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break

    section = None
    entry = None
    body: list[str] = []
    in_fence = False

    def close_entry():
        nonlocal entry, body
        if entry is not None:
            entry["body"] = _trim_block(body)
            if not entry["body"]:
                problems.append(f"line {entry['line']}: entry "
                                f"{entry['title']!r} has no body")
            section["entries"].append(entry)
        entry, body = None, []

    for i in range(start, len(lines)):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if entry is not None:
                body.append(line)
            continue
        if in_fence:
            if entry is not None:
                body.append(line)
            continue

        m = REF_ENTRY_RE.match(line)
        if m:
            if section is None:
                problems.append(f"line {i + 1}: entry {m.group(1)!r} before "
                                f"any '## ' section")
                section = {"title": "(unsectioned)", "entries": []}
                out["sections"].append(section)
            close_entry()
            entry = {"title": m.group(1), "kind": None, "tier": None,
                     "line": i + 1, "body": ""}
            continue

        m = REF_SECTION_RE.match(line)
        if m:
            close_entry()
            section = {"title": m.group(1), "entries": []}
            out["sections"].append(section)
            continue

        if entry is not None:
            km = REF_KEY_RE.match(line)
            # A key only counts before the body starts. `tier:: exam` written
            # halfway down a paragraph is prose about tiers, not a header.
            if km and not body:
                key, val = km.group(1), km.group(2)
                if entry[key] is not None:
                    problems.append(f"line {i + 1}: duplicate {key}:: in "
                                    f"{entry['title']!r}")
                else:
                    entry[key] = val
                continue
            if line.strip() or body:
                body.append(line)
            continue

        if line.strip() and section is not None:
            problems.append(f"line {i + 1}: content before the first '### ' "
                            f"entry of section {section['title']!r}")

    close_entry()
    return out


def reference_entries(d: dict, tier: str | None = None) -> list[dict]:
    """Every entry, flat, optionally filtered to a tier. `exam` returns the
    exam subset; `full` returns everything, because the detailed view is the
    whole sheet rather than the complement of the simplified one."""
    out = []
    for s in d.get("sections") or []:
        for e in s.get("entries") or []:
            if tier is None or tier == "full" or e.get("tier") == tier:
                out.append({**e, "section": s["title"]})
    return out


def reference_size(d: dict) -> int:
    """Characters on the exam sheet — titles and bodies, which is what a
    person's eye and a printer both actually spend."""
    return sum(len(e["title"]) + len(e["body"]) + 2
               for e in reference_entries(d, "exam"))


def validate_reference(text: str, vault: Path | None = None) -> list[str]:
    """Structure only, like every other validator here: it never judges whether
    an equation is *right*, which is Zach's job at review, and it never lets a
    malformed sheet render as if it were fine."""
    d = parse_reference(text)
    problems = list(d["problems"])
    fm = frontmatter(text) or {}

    if d["type"] != "reference":
        problems.append(f"frontmatter type is {d['type']!r}, expected 'reference'")
    if not d["course"]:
        problems.append("frontmatter has no course")
    # `frontmatter()` here is deliberately flat — it returns `tags` as the raw
    # string `[guide]`, never a list — so the membership test has to unwrap the
    # brackets itself rather than assume a YAML parser ran.
    tags = [t.strip().strip("'\"")
            for t in str(fm.get("tags") or "").strip("[]").split(",")]
    if "guide" not in tags:
        problems.append("frontmatter tags must include 'guide'")

    if not d["sections"]:
        problems.append("no '## ' sections — a reference is grouped or it is a list")
    seen: dict[str, int] = {}
    entries = reference_entries(d)
    if not entries:
        problems.append("no entries")
    for s in d["sections"]:
        if not s["entries"]:
            problems.append(f"section {s['title']!r} has no entries")
    for e in entries:
        tag = f"entry {e['title']!r} (line {e['line']})"
        if e["kind"] not in REF_KINDS:
            problems.append(f"{tag}: kind:: is {e['kind']!r}, expected one of "
                            f"{', '.join(sorted(REF_KINDS))}")
        if e["tier"] not in REF_TIERS:
            problems.append(f"{tag}: tier:: is {e['tier']!r}, expected "
                            f"'exam' or 'full'")
        key = e["title"].strip().casefold()
        if key in seen:
            problems.append(f"{tag}: duplicate of the entry at line {seen[key]}")
        else:
            seen[key] = e["line"]

    if not any(e["tier"] == "exam" for e in entries):
        problems.append("no exam-tier entries — the simplified sheet would be "
                        "empty, and it is the tier with the hard constraint")
    size = reference_size(d)
    if size > EXAM_BUDGET:
        problems.append(f"the exam tier is {size} characters, over the "
                        f"{EXAM_BUDGET} that fits one double-sided sheet — "
                        f"move the least-needed entries to tier:: full")
    return problems


def load_reference(vault: Path, course: str, split=None) -> dict | None:
    """The course's reference note, parsed, with its problems and its measured
    exam-sheet size. None when the course has none."""
    folder = course_folder(vault, course)
    if folder is None:
        return None
    p = folder / f"{folder.name.lower()}-reference.md"
    if not p.is_file():
        return None
    rel = p.relative_to(vault).as_posix()
    # `scan()`'s exact two lines, and they are easy to get inverted: the split
    # returns what is SEALED first, not what is allowed. Read the other way, an
    # empty first element — the normal case, nothing sealed — looks like
    # "nothing is permitted" and the sheet silently 404s. Defaulting to
    # `_default_split` keeps the fail-closed direction when no split is given.
    sealed, _ = (split or _default_split)(vault, [rel])
    if rel in sealed:
        return None
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    d = parse_reference(text)
    d["file"] = rel
    d["problems"] = validate_reference(text, vault=vault)
    d["exam_chars"] = reference_size(d)
    d["exam_budget"] = EXAM_BUDGET
    d["counts"] = {
        "all": len(reference_entries(d)),
        "exam": len(reference_entries(d, "exam")),
    }
    return d


# Units are earned by count, mechanically (§4): ≤8 modules → flat, no unit
# sections; 9–30 → units of 4–6 drawn at the course's own seams.
FLAT_MAX = 8
UNIT_MIN, UNIT_MAX = 4, 6


def _covers_numbers(spec: str) -> list[int]:
    """`M2, M3` / `M01–M04` / `2-4` → sorted module numbers. Ranges accept
    the en dash the titles use and the hyphen a keyboard produces — with or
    without spaces around it (`M1 – M4` is natural typography in a grammar
    whose own separator is a spaced `·`; tokenising on whitespace first made
    the regex's space allowance dead code and rejected exactly that)."""
    out: set = set()
    spec = re.sub(r"\s*([–-])\s*", r"\1", spec.strip())
    for tok in re.split(r"[,\s]+", spec):
        if not tok:
            continue
        m = re.match(r"^M?0*(\d+)(?:\s*[–-]\s*M?0*(\d+))?$", tok)
        if not m:
            return []
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a:
            return []
        out.update(range(a, b + 1))
    return sorted(out)


def parse_blueprint(text: str) -> dict:
    """The whole blueprint → dict; never raises, faults land in `problems`.

    `rows` is the ordered plan: each entry is a module
    ({kind, n, title, unit, est, sources, line}) or a checkpoint
    ({kind, n, title, unit, covers, line}), in document order."""
    fm = frontmatter(text) or {}
    d = {
        "type": str(fm.get("type") or "").strip(),
        "course": str(fm.get("course") or "").strip(),
        "status": str(fm.get("status") or "").strip(),
        "rows": [],
        "units": [],
        "problems": [],
    }
    unit: int | None = None
    in_rows = in_fence = False
    ended_by = "the preamble — no plan section is open yet"
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        um = BP_UNIT_RE.match(line)
        if um:
            unit = int(um.group(1))
            d["units"].append({"n": unit, "title": (um.group(2) or "").strip() or None,
                               "line": i})
            in_rows = True
            continue
        if re.match(r"^##\s+Modules\s*$", line):
            unit, in_rows = None, True
            continue
        if H2_RE.match(line):
            in_rows = False                  # any other section ends the plan
            ended_by = line.strip()
            continue
        if not in_rows:
            # A plan-shaped row outside the plan is a LOST row, not prose —
            # the exact way a near-miss unit heading ('## Unit 2 addendum',
            # '## unit 3') silently swallowed everything after it. Flag it;
            # never silently drop what looks like a planned module.
            if BP_MODULE_RE.match(line) or BP_CP_RE.match(line):
                d["problems"].append(
                    f"line {i}: plan row after {ended_by!r} ended the plan "
                    f"section — a unit heading is `## Unit <n> · <name>`")
            continue
        mm = BP_MODULE_RE.match(line)
        if mm:
            d["rows"].append({"kind": "module", "n": int(mm.group(1)),
                              "title": mm.group(2).strip(), "unit": unit,
                              "est": int(mm.group(3)), "sources": [], "line": i})
            continue
        cm = BP_CP_RE.match(line)
        if cm:
            body = cm.group(2)
            cv = BP_COVERS_RE.match(body)
            covers = _covers_numbers(cv.group(2)) if cv else []
            title = (cv.group(1) or "").strip() if cv else body.strip()
            if not cv or not covers:
                d["problems"].append(
                    f"line {i}: checkpoint row must name what it covers — "
                    f"`- CP{cm.group(1)} · <title> · covers M<a>–M<b>`")
            d["rows"].append({"kind": "checkpoint", "n": int(cm.group(1)),
                              "title": title or None, "unit": unit,
                              "covers": covers, "line": i})
            continue
        sm = BP_SRC_RE.match(line)
        if sm:
            if d["rows"] and d["rows"][-1]["kind"] == "module":
                d["rows"][-1]["sources"].append(sm.group(1))
            else:
                d["problems"].append(f"line {i}: source:: line belongs under "
                                     f"a module row, and none is open")
            continue
        if re.match(r"^\s*-\s+\S", line):
            d["problems"].append(f"line {i}: unrecognised row — a plan row is "
                                 f"`- M<n> · <title> ⏱ <min>`, "
                                 f"`- CP<n> · … covers …`, or an indented "
                                 f"`- source:: <path>`")
    return d


def validate_blueprint(text: str, vault: Path | None = None) -> list[str]:
    """Every way this blueprint violates its contract; [] conforms. The
    checks are §4's grouping arithmetic made mechanical — approval is only
    load-bearing if what was approved is itself well-formed."""
    d = parse_blueprint(text)
    out = list(d["problems"])

    if d["type"] != "guide-blueprint":
        out.append(f"frontmatter: type is {d['type']!r}, expected 'guide-blueprint'")
    if not d["course"]:
        out.append("frontmatter: course is blank")
    if d["status"] not in ("draft", "approved"):
        out.append(f"frontmatter: status is {d['status']!r}, expected "
                   f"'draft' or 'approved'")

    mods = [r for r in d["rows"] if r["kind"] == "module"]
    cps = [r for r in d["rows"] if r["kind"] == "checkpoint"]
    if not mods:
        out.append("no module rows — a blueprint with no plan approves nothing")

    seen: dict = {}
    for r in mods:
        tag = f"module row M{r['n']:02} (line {r['line']})"
        if r["n"] in seen:
            out.append(f"{tag}: duplicate module number (also line {seen[r['n']]})")
        seen.setdefault(r["n"], r["line"])
        if not r["title"]:
            out.append(f"{tag}: title is blank")
        if not ESTIMATE_MIN <= r["est"] <= ESTIMATE_MAX:
            out.append(f"{tag}: estimate {r['est']} outside "
                       f"{ESTIMATE_MIN}–{ESTIMATE_MAX} minutes — split or merge")
        if not r["sources"]:
            out.append(f"{tag}: no source:: line — a module planned from "
                       f"nothing is unverified content")
    seen_cp: dict = {}
    ahead: set = set()
    for r in d["rows"]:
        if r["kind"] == "module":
            ahead.add(r["n"])
            continue
        tag = f"checkpoint row CP{r['n']} (line {r['line']})"
        if r["n"] in seen_cp:
            out.append(f"{tag}: duplicate checkpoint number "
                       f"(also line {seen_cp[r['n']]})")
        seen_cp.setdefault(r["n"], r["line"])
        planned = {m["n"] for m in mods}
        for n in r["covers"]:
            if n not in planned:
                out.append(f"{tag}: covers M{n:02}, which no module row plans")
            elif n not in ahead:
                out.append(f"{tag}: covers M{n:02}, which is planned after it "
                           f"— an assessment cannot precede its material")

    # §4's grouping rule, mechanically. The last unit may run short — a course's
    # tail rarely divides evenly — but an oversized unit is always a wrong seam.
    units = [u["n"] for u in d["units"]]
    if units:
        if units != sorted(set(units)):
            out.append("unit sections must be unique and in order")
        for idx, u in enumerate(units):
            size = sum(1 for r in mods if r["unit"] == u)
            tag = f"unit {u}"
            if size > UNIT_MAX:
                out.append(f"{tag}: {size} modules — units are "
                           f"{UNIT_MIN}–{UNIT_MAX} (§4)")
            if size < UNIT_MIN and idx < len(units) - 1:
                out.append(f"{tag}: {size} module(s) — units are "
                           f"{UNIT_MIN}–{UNIT_MAX} (§4)")
            if not any(r["kind"] == "checkpoint" and r["unit"] == u
                       for r in d["rows"]):
                out.append(f"{tag}: no checkpoint row — one per unit, "
                           f"never optional (§4)")
        for r in mods:
            if r["unit"] is None:
                out.append(f"module row M{r['n']:02} (line {r['line']}): "
                           f"outside every unit section")
    else:
        if len(mods) > FLAT_MAX:
            out.append(f"{len(mods)} modules with no unit sections — a course "
                       f"over {FLAT_MAX} modules is grouped into units (§4)")
        if len(mods) >= 4 and len(cps) < len(mods) // 4:
            out.append(f"{len(cps)} checkpoint row(s) for {len(mods)} modules "
                       f"— flat courses carry one every 4 modules (§4)")

    if vault is not None and d["course"]:
        for r in mods:
            for src in r["sources"]:
                if not (vault / src).is_file():
                    out.append(f"module row M{r['n']:02} source does not "
                               f"resolve: {src}")
    return out


def serialize_blueprint(d: dict) -> str:
    """dict → canonical blueprint text; parse_blueprint(serialize_blueprint(d))
    sees the same plan. The preamble is the approval instruction itself,
    because the person reading this note next is Zach deciding whether to
    flip `status:` — the note must say what flipping it authorises."""
    code = d["course"].lower()
    out = ["---",
           "type: guide-blueprint",
           f"course: {d['course']}",
           f"status: {d.get('status') or 'draft'}",
           "tags: [guide]",
           "---",
           "",
           f"# {d['course']} — study-guide blueprint",
           "",
           "The plan the study guide generates from ([[sigma-os-study-plan]] "
           "§5.3): one row per module, in teaching order, each with its "
           "sources; checkpoint rows name what they assess. **Edit the rows, "
           "then set `status: approved`** — generation only runs against an "
           "approved blueprint, a module absent from it is held, and the row "
           f"order becomes `{code}-guide.md`'s chain order.",
           ""]
    units = {u["n"]: u for u in d.get("units", [])}
    if not units:
        out.append("## Modules")
        out.append("")
    # `section` is what heading is currently open: None = nothing yet,
    # "flat" = a leading `## Modules`, an int = that unit. A row whose unit
    # is None NEVER opens a heading of its own — `## Unit None` is a heading
    # this file's own parser reads as end-of-plan, which silently dropped
    # every row beneath it (found by the S7 review, verified by execution).
    # Leading unit-less rows get `## Modules`; later ones stay in the open
    # unit, which the reparse then records as that unit — no row is ever lost.
    section: object = None
    for r in d["rows"]:
        if units:
            ru = r["unit"]
            if ru is not None and ru != section:
                section = ru
                u = units.get(ru) or {}
                title = f" · {u['title']}" if u.get("title") else ""
                if out[-1] != "":
                    out.append("")
                out.append(f"## Unit {ru}{title}")
                out.append("")
            elif section is None:
                section = "flat"
                out.append("## Modules")
                out.append("")
        if r["kind"] == "module":
            out.append(f"- M{r['n']:02} · {r['title']} ⏱ {r['est']}")
            out += [f"    - source:: {s}" for s in r["sources"]]
        else:
            covers = ", ".join(f"M{n}" for n in r["covers"])
            title = f"{r['title']} · " if r.get("title") else ""
            out.append(f"- CP{r['n']} · {title}covers {covers}")
    return "\n".join(out).rstrip("\n") + "\n"


def load_blueprint(vault: Path, course: str, split=None) -> dict | None:
    """One course's parsed blueprint plus file identity and problems, or None
    when no blueprint note exists. The fixed name rule, like guide()."""
    folder = course_folder(vault, course)
    if folder is None:
        return None
    p = folder / f"{folder.name.lower()}-guide-blueprint.md"
    if not p.is_file():
        return None
    rel = p.relative_to(vault).as_posix()
    sealed, _ = (split or _default_split)(vault, [rel])
    if rel in sealed:
        return None
    try:
        text = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    d = parse_blueprint(text)
    d["file"] = rel
    d["problems"] = validate_blueprint(text, vault=vault)
    return d


def scan_blueprints(vault: Path, split=None) -> list[dict]:
    """Every `type: guide-blueprint` note at a course root, with its problems
    — doctor's sweep. Blueprints are hand-edited by design (approval IS an
    edit), so a broken row must surface as a finding, not as a generation
    run that silently plans nothing."""
    root = vault.joinpath(*ACADEMICS)
    if not root.is_dir():
        return []
    paths = sorted(root.glob("*/*-guide-blueprint.md"))
    rels = [p.relative_to(vault).as_posix() for p in paths]
    sealed, _ = (split or _default_split)(vault, rels)
    out = []
    for p, rel in zip(paths, rels):
        if rel in sealed:
            continue
        try:
            text = p.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        fm = frontmatter(text) or {}
        if str(fm.get("type") or "").strip() != "guide-blueprint":
            continue
        d = parse_blueprint(text)
        problems = validate_blueprint(text, vault=vault)
        folder = p.parent.name
        if d["course"] and d["course"].lower() != folder.lower():
            problems.append(f"course field says {d['course']!r} but the note "
                            f"lives in {folder}/")
        out.append({"course": d["course"] or folder, "file": rel,
                    "status": d["status"] or None,
                    "rows": len(d["rows"]), "problems": problems})
    return out


def existing_units(vault: Path, course: str) -> tuple[set, set]:
    """(module numbers, checkpoint numbers) that already exist on disk under
    the course's guide/ folder — the resume test. No state file, on purpose:
    the notes on disk are the record of which generation jobs completed
    (§13.3), so a wiped sidecar or a hand-authored module both read true."""
    folder = course_folder(vault, course)
    mods: set = set()
    cps: set = set()
    if folder is None or not (folder / "guide").is_dir():
        return mods, cps
    for p in sorted((folder / "guide").glob("*.md")):
        try:
            fm = frontmatter(p.read_text(encoding="utf-8-sig", errors="replace")) or {}
        except OSError:
            continue
        kind = str(fm.get("type") or "").strip()
        if kind == "module":
            n = _int_or_none(fm.get("module"))
            if n is not None:
                mods.add(n)
        elif kind == "checkpoint":
            n = _int_or_none(fm.get("checkpoint"))
            if n is not None:
                cps.add(n)
    return mods, cps


# --------------------------------------------------------------------------
# the practice sidecars — machine-local state, never the vault (§8)
# --------------------------------------------------------------------------
# `.state.json` and `.jsonl` are both load-bearing suffixes: .gitignore already
# excludes `runtime/*.state.json` and `runtime/*.jsonl` as one machine's
# operating state, so neither file can reach GitHub under an existing
# documented rule rather than a new special case — todo.state.json's reasoning,
# inherited whole.

STATE_PATH = HERE / "study.state.json"
ATTEMPTS_PATH = HERE / "study.jsonl"


def qhash(prompt: str) -> str:
    """A stable handle for a practice question's *text*.

    Attempt rows carry it beside the q-id because regeneration renumbers ids
    (§5.1): the id says where the question sits today, the hash says what was
    actually asked, and history survives a regenerated module through the
    second. Same normalisation as todo.task_id, for the same reason."""
    return hashlib.sha1(" ".join(prompt.split()).casefold()
                        .encode("utf-8")).hexdigest()[:12]


def load_state(path=None) -> dict:
    """The fine-grained view state: depth chosen, reveals opened, per-question
    results, and the rollup watermarks. Losable by design — everything durable
    is in the note (completion) or the study log (the digest)."""
    p = Path(path or STATE_PATH)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "modules": {}, "rollup": {}}
    if not isinstance(data, dict) or not isinstance(data.get("modules"), dict):
        return {"version": 1, "modules": {}, "rollup": {}}
    data.setdefault("version", 1)
    data.setdefault("rollup", {})
    return data


def save_state(state: dict, path=None) -> bool:
    """Atomic replace, todo.save_index's shape: losing view state costs a few
    reveals, losing the request serving the dashboard costs the dashboard."""
    p = Path(path or STATE_PATH)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, p)
        return True
    except (OSError, TypeError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def record_attempt(row: dict, path=None) -> bool:
    """Append one attempt to the log. Append-only on purpose: an attempt is a
    fact about what happened, and facts do not get edited."""
    p = Path(path or ATTEMPTS_PATH)
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def attempts_for(course: str, after: str | None = None, path=None) -> list[dict]:
    """This course's attempts, oldest first, optionally only those after an
    ISO timestamp — which is how the rollup watermark makes session-end
    idempotent. A torn or hand-mangled line is skipped, never fatal."""
    p = Path(path or ATTEMPTS_PATH)
    if not p.is_file():
        return []
    out = []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("course") or "").lower() != course.lower():
            continue
        if after and str(row.get("ts") or "") <= after:
            continue
        out.append(row)
    out.sort(key=lambda r: str(r.get("ts") or ""))
    return out


def digest(wrong: list[dict]) -> str:
    """`the dot product ×2, unit vectors ×1` — the compact wrong-answer trace
    a rollup row carries (§8). Grouped by segment title because that is the
    unit a re-study decision is made at; sorted worst-first, then A–Z so the
    same misses always produce the same digest."""
    counts: dict[str, int] = {}
    for r in wrong:
        key = str(r.get("seg_title") or r.get("qid") or "?").strip()
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{k} ×{n}" for k, n in ordered)


def find_item(d: dict, qid: str) -> tuple[dict, dict] | None:
    """(segment, practice item) for one q-id in a parsed module — what the
    attempt endpoint derives kind, qhash and the digest's segment title from."""
    for seg in d["segments"]:
        for it in seg["practice"]:
            if it["id"] == qid:
                return seg, it
    return None


def now_iso() -> str:
    """Local wall time — the vault stores local wall time everywhere (the
    schedule note owns the timezone). Microseconds are load-bearing, not
    cosmetic: the rollup watermark is a strictly-greater-than comparison on
    this string, and at seconds precision an attempt logged in the same second
    as the last covered row would be silently swallowed by the next rollup."""
    return datetime.datetime.now().isoformat(timespec="microseconds")


# --------------------------------------------------------------------------
# CLI — validate one file, or scan the vault
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Study-guide module grammar.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate one module note")
    v.add_argument("path")
    v.add_argument("--vault", default=str(DEFAULT_VAULT))
    s = sub.add_parser("scan", help="list every module note, with problems")
    s.add_argument("--vault", default=str(DEFAULT_VAULT))
    a = ap.parse_args()

    if a.cmd == "validate":
        text = Path(a.path).read_text(encoding="utf-8-sig", errors="replace")
        # Dispatch on the note's type: doctor's fix hint points here for
        # checkpoints too, and the module validator misdiagnoses those.
        problems = validate_any(text, vault=Path(a.vault))
        for p in problems:
            print(f"  !! {p}")
        print("valid" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if a.cmd == "scan":
        print(json.dumps(scan(Path(a.vault)), indent=2, ensure_ascii=False))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
