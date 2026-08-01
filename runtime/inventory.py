#!/usr/bin/env python3
"""
inventory.py — the vault's frontmatter, precomputed.

Built 2026-07-31, after the auditor failed three runs in a row on
`error_max_turns`. The third failure is the one that diagnosed it: with no drift
left to find, it still exhausted 40 turns. The *search* did not fit, so neither
a bigger budget nor a stop-early instruction addressed it — its brief asks Haiku
to sweep 175 notes across five classes of check by Grep, and grepping is what
ran out.

So the scan moves into script code. This is the same move study intake makes: a
deterministic step extracts the material, and the model spends its turns on the
part that actually needs judgement. Forty search turns become zero.

**This module reports facts and never judges them.** It does not encode which
`type` is legal, which fields are required, or what a status should be — that
lives in `CLAUDE.md`, which the auditor reads. Encoding the schema here would
create exactly the second declaration that drifts, and the vault has already
paid for that lesson once with its privacy list.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from sigma import frontmatter                    # noqa: E402

# The fields any schema in the contract turns on. Anything else in a note's
# frontmatter is reported too — an unexpected key is itself a finding.
_INTERESTING = ("type", "status", "course", "due", "date", "exam", "number",
                "area", "started", "repo", "kind", "risk", "applied", "club",
                "org", "role", "name", "term", "confidence", "source")

_SKIP_TOPS = {".obsidian", ".git", ".claude", ".trash", "99-Meta"}


def _rel(p: Path, vault: Path) -> str:
    return p.relative_to(vault).as_posix()


def scan_notes(vault: Path, sealed: set | None = None) -> list:
    """Every note's frontmatter, as (rel, dict). Sealed paths are excluded by
    the caller passing them in — this module does not re-implement that
    boundary."""
    out = []
    for p in sorted(vault.rglob("*.md")):
        rel = _rel(p, vault)
        top = rel.split("/", 1)[0]
        if top in _SKIP_TOPS or top.startswith("."):
            continue
        if sealed and rel in sealed:
            continue
        try:
            fm = frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        out.append((rel, fm or {}))
    return out


def manifest_coverage(vault: Path) -> list:
    """For each course index: which sibling notes it does and does not link.

    Precomputed because it is pure bookkeeping — the auditor's brief asks for
    "index/manifest notes that no longer list files that exist beside them",
    and answering that by Grep costs a turn per index. The drift that started
    all of this was exactly this check.
    """
    rows = []
    root = vault / "02-Areas" / "Academics"
    if not root.is_dir():
        return rows
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        index = folder / f"{folder.name.lower()}.md"
        if not index.exists():
            rows.append((folder.name, None, [], []))
            continue
        try:
            text = index.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        siblings = [q for q in sorted(folder.rglob("*.md")) if q != index]
        linked, missing = [], []
        for q in siblings:
            # A link may be a bare stem or a full vault path, so test the stem.
            (linked if f"[[{q.stem}" in text or f"/{q.stem}|" in text
             or f"/{q.stem}]]" in text else missing).append(q.stem)
        rows.append((folder.name, _rel(index, vault), linked, missing))
    return rows


def render(vault: Path, sealed: set | None = None) -> str:
    """The block handed to the auditor in place of its own scanning."""
    notes = scan_notes(vault, sealed)
    types: dict = {}
    for _, fm in notes:
        t = str(fm.get("type") or "(none)")
        types[t] = types.get(t, 0) + 1

    lines = [
        f"## Vault inventory — precomputed, complete, current ({len(notes)} notes)",
        "",
        "**Do not re-derive any of this with Grep, Glob or Read.** It was built by "
        "a deterministic scan immediately before this run and is not a sample. "
        "Earlier runs of this brief spent their whole turn budget rebuilding it "
        "and died before reporting anything. Spend your turns judging these facts "
        "against `CLAUDE.md` instead.",
        "",
        "### Types in use",
        "",
    ]
    for t, n in sorted(types.items(), key=lambda kv: -kv[1]):
        lines.append(f"- `{t}` × {n}")

    lines += ["", "### Frontmatter, per note", "",
              "Only keys actually present are shown; a missing key is a missing key.", ""]
    for rel, fm in notes:
        bits = [f"{k}={fm[k]}" for k in _INTERESTING
                if k in fm and str(fm.get(k)).strip() not in ("", "None")]
        extra = [k for k in fm if k not in _INTERESTING and k != "tags"]
        if extra:
            bits.append("other:" + ",".join(sorted(extra)))
        lines.append(f"- `{rel}` — " + ("; ".join(bits) if bits else "**no frontmatter fields**"))

    lines += ["", "### Course-index manifest coverage", "",
              "Which notes sit beside each course index, and which it links.", ""]
    for course, index, linked, missing in manifest_coverage(vault):
        if index is None:
            lines.append(f"- **{course}** — no course-index note at all")
            continue
        if missing:
            lines.append(f"- **{course}** (`{index}`) — {len(linked)} linked, "
                         f"**{len(missing)} NOT linked: {', '.join(missing)}**")
        else:
            lines.append(f"- **{course}** (`{index}`) — all {len(linked)} sibling "
                         f"note(s) linked")
    return "\n".join(lines)


def for_auditor() -> str:
    """Entry point wired to the auditor's `context` hook. Never raises: a broken
    inventory must degrade to "scan it yourself", not kill the run."""
    try:
        import reflect as rf
        vault = rf.VAULT
        sealed = None
        try:
            sys.path.insert(0, str(_HERE.parent / "interface" / "backend"))
            from privacy import gitignore_scan
            rels = [_rel(p, vault) for p in vault.rglob("*.md")]
            sealed = gitignore_scan(vault, rels)[0]
        except Exception:
            sealed = None            # worst case the auditor sees a sealed path
        return render(vault, sealed)
    except Exception as e:
        return (f"## Vault inventory\n\n_Could not be precomputed "
                f"({type(e).__name__}: {e}) — fall back to scanning, and say so._")


if __name__ == "__main__":
    import reflect as rf
    print(render(rf.VAULT))
