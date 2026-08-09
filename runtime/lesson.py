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
ITEM_KEY_RE = re.compile(r"^-\s+(hint|answer|solution|source)::\s*(.*?)\s*$")
QID_RE = re.compile(r"^q-(\d+)-(\d+)$")

KINDS = {"mcq", "numeric", "short", "code", "proof"}

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
        "example": None, "practice": [],
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
        out.append({
            "course": d["course"], "module": d["module"], "unit": d["unit"],
            "title": d["title"], "estimate": d["estimate"], "file": rel,
            "segments": len(d["segments"]),
            "practice": sum(len(s["practice"]) for s in d["segments"]),
            "problems": validate(text, vault=vault),
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
            # see (a duplicate module number) — held either way.
            d["problems"] = validate(text, vault=vault) + [
                p_ for p_ in row["problems"] if p_.startswith("duplicate module")]
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
    GET /api/guide/{course}. None when the course has no `<code>-guide.md`;
    the name is fixed by the contract, so nothing is searched for."""
    folder = course_folder(vault, course)
    if folder is None:
        return None
    code = folder.name.lower()
    p = folder / f"{code}-guide.md"
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
    rows = parse_chain(text)

    # `status: draft | approved`, read mechanically — the same field the S7
    # applier hold will check. None when no blueprint note exists yet.
    blueprint = None
    bp = folder / f"{code}-guide-blueprint.md"
    if bp.is_file():
        try:
            fm = frontmatter(bp.read_text(encoding="utf-8-sig", errors="replace"))
            blueprint = str(fm.get("status") or "").strip() or None
        except OSError:
            blueprint = None

    return {
        "course": folder.name, "file": rel, "rows": rows,
        "total": len(rows),
        "done": sum(1 for r in rows if r["state"] == "done"),
        "skipped": sum(1 for r in rows if r["state"] == "skipped"),
        "frontier": next((r for r in rows if r["state"] == "open"), None),
        "blueprint": blueprint,
    }


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
        problems = validate(text, vault=Path(a.vault))
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
