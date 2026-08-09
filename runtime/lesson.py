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

Nothing here writes to the vault — parsing and validating is all it does.
"""
import argparse
import json
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
