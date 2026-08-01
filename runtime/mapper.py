#!/usr/bin/env python3
"""
mapper.py — turn an unfamiliar codebase into linked architecture notes (Phase 6).

`dashboard-vision`: "Map an unfamiliar codebase into linked architecture notes."
The contract already specifies the shape, under **project** in `CLAUDE.md`:

    A project with enough architecture to be worth mapping may also have a
    `03-Projects/<project>/` folder of atomic context notes — one per concept
    (a component, a data model, a subsystem), cross-linked to each other and
    indexed from an *Architecture map* section in the hub. […] If the repo has a
    generated knowledge graph (e.g. `graphify-out/`), use its communities and
    most-connected "god nodes" to decide what deserves a note.

So this does not invent a format; it fills in one the vault was already waiting
for. `Focus Log` has exactly that graph sitting in `backend/graphify-out/`, and
its report names 13 communities and 10 god nodes — which is a better answer to
"what deserves a note" than any amount of file-tree guessing.

**Script code reads the repo, not the model.** `VaultPrivacy.verdict` refuses
every path outside the vault — "the agent's world is the vault and nothing above
it" — and that boundary is not one a convenience feature gets to widen. So the
survey below is built by this module, exactly as study intake extracts a PDF, and
handed over as *material* rather than as instructions. Nothing about the privacy
model changes: the model still cannot open a file it was not given.

**The survey rides the conversation, not the system prompt.** `fleet.run_one`'s
`material` argument exists for this: a brief goes on the command line, where
Windows caps it at 32,767 characters, and a codebase survey does not fit. The
conversation goes over stdin and does not have that ceiling.

**The hub's Architecture map is written by script code**, from the notes that
actually landed — not by the model, which would be claiming links it cannot
verify. Same splice discipline as `devlog.py`, and the same reason.

**Writes.** None, directly: `propose_change` → `applier.py`, one revertible
commit per note.
"""
import argparse
import asyncio
import datetime
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import devlog                                       # noqa: E402  (hub scan + splice)
import reflect as rf                                # noqa: E402
import specialists as sp                            # noqa: E402
from sigma import frontmatter, kebab, make_logger   # noqa: E402
from sigma import gitops                            # noqa: E402

VAULT = rf.VAULT
PROJECTS = VAULT / "03-Projects"

# The survey rides stdin, so this is about the model's attention rather than any
# hard limit — a 200,000-character dump buries the graph report that is the most
# useful thing in it.
MATERIAL_BUDGET = 90_000
TREE_CAP = 400
README_CAP = 6_000
MANIFEST_CAP = 3_000
REPORT_CAP = 14_000
SIGNATURE_FILES = 24
SIGNATURE_LINES = 30

TIMEOUT_S = 1200

# Files that say what a project *is* before any source does.
READMES = ("README.md", "README.rst", "README.txt", "readme.md")
MANIFESTS = ("package.json", "pom.xml", "requirements.txt", "pyproject.toml",
             "Cargo.toml", "go.mod", "build.gradle", "build.gradle.kts",
             "Gemfile", "composer.json", "CMakeLists.txt")
SOURCE_SUFFIXES = {".py", ".java", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
                   ".rb", ".cs", ".kt", ".swift", ".c", ".cc", ".cpp", ".h",
                   ".hpp", ".php", ".scala", ".m", ".mm"}

log = make_logger(_HERE / "mapper.log", "mapper", stream=sys.stdout)

ARCH_HEAD = "## Architecture map"


# --------------------------------------------------------------------------
# what is worth mapping
# --------------------------------------------------------------------------

def notes_dir(name: str) -> Path:
    return PROJECTS / name


def existing_notes(name: str) -> list:
    d = notes_dir(name)
    return sorted(d.glob("*.md")) if d.is_dir() else []


def candidates(only: str | None = None) -> tuple:
    """(jobs, problems, quiet). Reuses devlog's hub scan — one implementation of
    "what is a project hub and what repo does it claim"."""
    jobs, problems, quiet = [], [], []
    try:
        guard = devlog._privacy()
    except Exception as e:
        return [], [("mapper", f"privacy guard unavailable ({e}) — "
                               f"refusing to read anything")], []

    for rec in devlog.hub_records():
        name = rec["name"]
        if only and name.lower() != only.lower():
            continue
        rel = rec["path"].relative_to(VAULT).as_posix()

        if rec["archived"]:
            quiet.append((name, "archived — map it only if you go back to it"))
            continue
        if not rec["repo"]:
            (problems if only else quiet).append(
                (name, "no `repo:` field — nothing to map"))
            continue
        if guard.verdict(str(rec["path"])):
            problems.append((name, "its hub note is refused at the model boundary"))
            continue

        repo = Path(rec["repo"])
        if not repo.is_dir():
            problems.append((name, f"repo folder is missing: {rec['repo']}"))
            continue

        # The applier refuses a gitignored target, so notes for this project
        # could never be committed. Say so instead of spending a model call.
        ign = gitops._git(VAULT, "check-ignore", "-q", rel)
        if ign.returncode == 0:
            problems.append((name, "its hub note is gitignored — architecture "
                                   "notes could not be committed, so they could "
                                   "not be undone; this one stays hand-written"))
            continue
        if ign.returncode not in (0, 1):
            problems.append((name, "could not verify the privacy boundary — failing closed"))
            continue

        have = existing_notes(name)
        if have and not only:
            # Mapping is not incremental: a second pass would re-propose notes
            # that already exist and the applier would hold every one of them.
            # Naming the project is the way to say "map it again".
            quiet.append((name, f"already mapped — {len(have)} note(s) in "
                                f"03-Projects/{name}/"))
            continue
        jobs.append({**rec, "repo": repo, "have": have})
    return jobs, problems, quiet


# --------------------------------------------------------------------------
# the survey — deterministic, and the expensive part done once
# --------------------------------------------------------------------------

def tracked_files(repo: Path) -> list:
    """`git ls-files`, so the survey inherits the repo's own .gitignore.

    Walking the tree instead would mean maintaining a list of directories to
    skip — node_modules, target, dist, .venv, __pycache__ — that is wrong for
    the next language encountered. git already knows.
    """
    r = gitops._git(repo, "ls-files", timeout=60)
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def _read(path: Path, cap: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:cap]
    except OSError:
        return ""


def _tree(files: list) -> str:
    """A directory census rather than a full listing: for a 4,000-file repo the
    shape is the information and the filenames are noise."""
    if len(files) <= TREE_CAP:
        return "\n".join(files)
    by_dir: dict = {}
    for f in files:
        by_dir.setdefault(f.rsplit("/", 1)[0] if "/" in f else ".", []).append(f)
    out = [f"_{len(files)} tracked files; listing by directory._", ""]
    for d, fs in sorted(by_dir.items()):
        out.append(f"**{d}/** — {len(fs)} file(s)")
        out.extend(f"  {f.rsplit('/', 1)[-1]}" for f in sorted(fs)[:12])
        if len(fs) > 12:
            out.append(f"  … and {len(fs) - 12} more")
    return "\n".join(out)


def _signatures(repo: Path, files: list) -> str:
    """The head of the largest source files — imports and the top declaration.

    Not the bodies. The point is to learn what the pieces are called and what
    they depend on, which is the top of a file; a mapper that read whole bodies
    would spend its whole budget on three files.
    """
    src = [f for f in files if Path(f).suffix.lower() in SOURCE_SUFFIXES]
    sized = []
    for f in src:
        try:
            sized.append(((repo / f).stat().st_size, f))
        except OSError:
            continue
    out = []
    for _, f in sorted(sized, reverse=True)[:SIGNATURE_FILES]:
        head = _read(repo / f, 4_000).splitlines()[:SIGNATURE_LINES]
        # Blank lines and one-line comments carry nothing at this zoom level.
        head = [ln for ln in head if ln.strip() and not ln.strip().startswith(("//", "#"))]
        if head:
            out.append(f"### `{f}`\n```\n" + "\n".join(head) + "\n```")
    return "\n\n".join(out)


def _graph_report(repo: Path, files: list) -> str:
    """A generated knowledge graph, if one was committed. The contract points at
    `graphify-out/` by name, and its report is the single most useful input here:
    communities and god nodes are precisely the "what deserves a note" question,
    already answered by something that read the whole corpus."""
    for f in files:
        if f.lower().endswith("graph_report.md"):
            return f"_From `{f}`._\n\n" + _read(repo / f, REPORT_CAP)
    # Not committed but present on disk — graphify output is often gitignored.
    for p in sorted(repo.rglob("GRAPH_REPORT.md")):
        try:
            rel = p.relative_to(repo).as_posix()
        except ValueError:
            continue
        return f"_From `{rel}` (present on disk, not tracked)._\n\n" + _read(p, REPORT_CAP)
    return ""


def survey(repo: Path) -> dict:
    files = tracked_files(repo)
    if not files:
        return {"error": "git ls-files returned nothing — not a git repo, or empty"}

    readme = ""
    for name in READMES:
        readme = _read(repo / name, README_CAP)
        if readme:
            readme = f"_From `{name}`._\n\n" + readme
            break

    manifests = []
    for f in files:
        base = f.rsplit("/", 1)[-1]
        if base in MANIFESTS and f.count("/") <= 2:
            body = _read(repo / f, MANIFEST_CAP)
            if body:
                manifests.append(f"### `{f}`\n```\n{body}\n```")

    return {"error": None, "n": len(files), "tree": _tree(files),
            "readme": readme, "manifests": "\n\n".join(manifests),
            "report": _graph_report(repo, files),
            "signatures": _signatures(repo, files)}


def build_material(job: dict, s: dict) -> str:
    """The survey, as the conversation's opening material.

    Ordered by information density, because a model reading a long message
    weights the start: the graph report first when there is one, then what the
    project says about itself, then its dependencies, then its shape.
    """
    blocks = [("A generated knowledge graph of this repo", s["report"]),
              ("The project's README", s["readme"]),
              ("Build manifests and dependencies", s["manifests"]),
              (f"The file tree ({s['n']} tracked files)", s["tree"]),
              ("The head of the largest source files", s["signatures"])]
    out = [f"# Survey of `{job['repo']}`", "",
           "This is everything you get. It was gathered by script code — you "
           "cannot open the repository yourself, because it lives outside the "
           "vault and the model boundary stops at the vault's edge. Work from "
           "what is here and say plainly if something is not derivable from it."]
    used = sum(len(b) for _, b in blocks)
    for title, body in blocks:
        if not body.strip():
            continue
        if used > MATERIAL_BUDGET and title.startswith("The head"):
            out.append(f"## {title}\n\n_(omitted to keep this readable.)_")
            continue
        out.append(f"## {title}\n\n{body}")
    text = "\n\n".join(out)
    return text if len(text) <= MATERIAL_BUDGET else text[:MATERIAL_BUDGET] + "\n\n_(truncated.)_"


# --------------------------------------------------------------------------
# the brief — small, because the material is elsewhere
# --------------------------------------------------------------------------

RULES = """
You are Sigma's codebase mapper. Zach triggered this run himself and is watching
the output land.

Three rules bound everything you do:

1. **You do not write files.** `propose_change` is the only tool you have that
   touches disk. Each call drafts one note; the runner applies it afterwards as
   its own revertible commit. Never claim you created a file.
2. **Never invent architecture.** Everything in a note must be derivable from the
   survey you were given. You cannot open the repository — it lives outside the
   vault, and the model boundary stops there. If the survey does not show you how
   something works, say what it is and leave the mechanism out rather than
   guessing at it plausibly.
3. **Never copy code into the vault.** The contract is explicit: code lives in
   its repo and the vault points at it. Name a class or a file, quote a signature
   if it earns its place, and link to the folder — do not paste implementations.
""".strip()

BRIEF = """
## The project

- **Hub note:** `{rel}` (`[[{name}]]`)
- **Repo:** `{repo}`
- **Notes go in:** `03-Projects/{name}/`
{havenote}
## Your job

Read the survey in the message that follows and write the **architecture notes**
this project has been missing — the notes that would let someone (Zach in six
months, or an agent with no context) understand the codebase without reading it.

1. **One note per concept**, named for the concept: a component, a data model, a
   subsystem, a cross-cutting mechanism. `authentication-and-jwt.md`,
   `task-model.md` — not `backend.md` or `part-2.md`. Between **4 and 9 notes**;
   fewer if the project genuinely has fewer distinct ideas.
2. **If the survey includes a generated knowledge graph, let it decide.** Its
   communities are the natural note boundaries and its god nodes are the core
   abstractions — that report read the whole corpus, which you cannot.
3. **Frontmatter**, the `resource` schema plus the project key. `source:` is the
   folder within the repo the note is about, which is how the notes this vault
   already has do it:
   ```
   ---
   type: resource
   course:
   source: {repo}
   project: {name}
   tags: [resource]
   ---
   ```
4. **Point at the code, never copy it.** Each note carries a line like
   `**Code:** [Open the folder](file:///{fileurl}) · `path/within/repo``
   Name the classes and files that implement the concept. Do not paste bodies.
5. **Link.** Every note links back to the hub `[[{name}]]`, and to the sibling
   notes it genuinely relates to — a note about JWT auth links the user model it
   authenticates. Only wikilink notes that exist or that you are creating in this
   same run; a bracketed non-note is a permanent dangling node in the graph.
6. **Say the one thing clearly.** Title names the idea, body explains just that,
   self-contained enough to read alone. What it is, how it fits, what it depends
   on, and where the code is.

Call `propose_change` once per note, `kind: note`, `target` set to
`03-Projects/{name}/<kebab-name>.md`. Do **not** propose a change to the hub note
— the runner writes its Architecture map itself, from the notes that actually
land. Finish with one sentence naming what you mapped.
""".strip()


def build_brief(job: dict) -> str:
    have = ""
    if job["have"]:
        have = ("- **Careful:** this project already has notes in that folder:\n"
                + "\n".join(f"  - `{p.name}`" for p in job["have"])
                + "\n  Add what is missing and leave those alone — a proposal "
                  "targeting an existing note is held, not applied.\n")
    return BRIEF.format(
        rel=job["path"].relative_to(VAULT).as_posix(), name=job["name"],
        repo=job["repo"], havenote=have,
        fileurl=str(job["repo"]).replace("\\", "/").replace(" ", "%20"))


# --------------------------------------------------------------------------
# the hub's Architecture map — script code, from what landed
# --------------------------------------------------------------------------

def _bullet(p: Path) -> str:
    title = ""
    try:
        m = re.search(r"^#\s+(.+)$", p.read_text(encoding="utf-8",
                                                 errors="replace"), re.M)
        title = m.group(1).strip() if m else ""
    except OSError:
        pass
    # Alias unless it would be identical to the filename. `[[file-uploads]]`
    # renders as "file-uploads"; the notes this vault already has write
    # `[[file-uploads|File uploads]]`, and a map is something you read.
    if title and title != p.stem:
        return f"- [[{p.stem}|{title}]]"
    return f"- [[{p.stem}]]"


def link_hub(job: dict) -> str | None:
    """Index the notes that exist NOW from the hub's Architecture map.

    Written by script code rather than the model for the same reason `devlog`
    splices rather than re-transcribes: this is a list of links, every one of
    which must resolve, and the only thing that knows which proposals actually
    landed is the code that just applied them. A model listing them would be
    reporting its intentions.

    **Additive only.** `focus-log`'s map is hand-grouped prose — "Domain & data",
    "Cross-cutting", each link annotated — and a generated flat list is strictly
    worse than that. So an existing section is never replaced: notes it already
    links are left alone, and anything new is appended beneath it. Creating the
    section from nothing is the only case where this module writes the whole
    thing.
    """
    notes = existing_notes(job["name"])
    if not notes:
        return None
    hub = job["path"].read_text(encoding="utf-8", errors="replace")

    m = re.search(rf"^{re.escape(ARCH_HEAD)}\s*$", hub, re.M | re.I)
    if m:
        nxt = re.search(r"^##\s+", hub[m.end():], re.M)
        end = m.end() + nxt.start() if nxt else len(hub)
        body = hub[m.end():end]
        # A note is "already indexed" if its basename appears in any wikilink in
        # the section — `[[tasks]]` and `[[tasks|Tasks]]` are the same link.
        linked = {t.split("|")[0].split("/")[-1].strip()
                  for t in re.findall(r"\[\[([^\]]+)\]\]", body)}
        fresh = [p for p in notes if p.stem not in linked]
        if not fresh:
            return None
        added = "\n".join(_bullet(p) for p in fresh)
        updated = (hub[:end].rstrip() + "\n" + added + "\n\n"
                   + hub[end:].lstrip("\n"))
    else:
        section = "\n".join(
            [ARCH_HEAD, "", f"> Mapped {datetime.date.today().isoformat()} by "
                            f"`sigma map` from the code in this project's repo.", ""]
            + [_bullet(p) for p in notes])
        # Before Related if there is one, else at the end — the map belongs with
        # the project's own material, not after its outbound links.
        rel_h = re.search(r"^##\s+Related\s*$", hub, re.M | re.I)
        if rel_h:
            updated = hub[:rel_h.start()] + section + "\n\n" + hub[rel_h.start():]
        else:
            updated = hub.rstrip() + "\n\n" + section + "\n"
    if updated.strip() == hub.strip():
        return None

    prop = rf.write_proposal({
        "title": f"Architecture map: {job['name']}",
        "kind": "note",
        "target": job["path"].relative_to(VAULT).as_posix(),
        "content": updated,
        "rationale": (f"Indexes the {len(notes)} architecture note(s) now in "
                      f"03-Projects/{job['name']}/. The list is built by "
                      f"`mapper.py` from the notes that exist on disk, so every "
                      f"link resolves."),
        "risk": "low", "scope": "vault", "insight": "",
    }, datetime.date.today().isoformat())

    import applier
    done = applier.apply_one(prop, actor="mapper")
    return done.get("target") if done.get("action") in ("create", "update") else None


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

async def map_one(job: dict, s: dict, model: str = "sonnet") -> dict:
    import fleet as fl

    spec = sp.Specialist(
        key="mapper", title="Codebase mapper", cadence="manual",
        model=model, effort="medium", brief=build_brief(job),
        # Up to 9 notes, each a propose_change call, plus the vault reads it
        # makes to check what it is linking to.
        max_turns=40)
    return await fl.run_one(spec, timeout_s=TIMEOUT_S, rules=RULES,
                            material=build_material(job, s))


def run(only: str | None = None, dry_run: bool = False, limit: int = 0,
        model: str = "sonnet") -> int:
    jobs, problems, _quiet = candidates(only)
    for name, why in problems:
        log(f"  skipped {name} — {why}")
    if not jobs:
        log("nothing to map — run `sigma map --project <name>` to re-map one")
        return 1 if problems else 0
    if limit and len(jobs) > limit:
        log(f"  {len(jobs)} project(s) unmapped; taking the first {limit}")
        jobs = jobs[:limit]

    log(f"{len(jobs)} project(s) to map")
    made = failed = 0
    for j in jobs:
        s = survey(j["repo"])
        if s.get("error"):
            failed += 1
            log(f"  {j['name']}: {s['error']}")
            continue
        kind = "with its knowledge graph" if s["report"] else "from its tree and sources"
        log(f"-> {j['name']} ({s['n']} tracked files, {kind})")
        if dry_run:
            log(f"   would send {len(build_material(j, s)):,} characters of survey")
            continue

        try:
            r = asyncio.run(map_one(j, s, model=model))
        except Exception as e:
            r = {"ok": False, "proposals": 0, "files": [],
                 "error": f"{type(e).__name__}: {e}"}
        if not r.get("ok"):
            failed += 1
            log(f"   nothing written — {r.get('error') or 'the run failed'}")
            continue
        if not r.get("proposals"):
            failed += 1
            log(f"   no notes proposed"
                + (f" ({r.get('summary', '')[:160]})" if r.get("summary") else ""))
            continue

        try:
            import applier
            applied = applier.apply_run(r["files"], actor="mapper")
        except Exception as e:
            failed += 1
            log(f"   proposals written but applying failed: {type(e).__name__}: {e}")
            continue

        landed = [a for a in applied if a.get("action") in ("create", "update")]
        for a in landed:
            log(f"   {a.get('target')}")
        for a in applied:
            if a.get("action") == "held":
                log(f"   HELD {a.get('target')} — {a.get('reason')}")
        log(f"   {r['proposals']} proposed, {len(landed)} applied")

        if landed:
            try:
                where = link_hub(j)
                log(f"   architecture map → {where}" if where else
                    "   the hub already listed these notes")
            except Exception as e:
                # The notes are the deliverable and they landed; an unindexed
                # map is a missing link, not lost work.
                log(f"   notes landed but the hub index failed: "
                    f"{type(e).__name__}: {e} — add the map by hand")
            made += 1
        else:
            failed += 1
        if r.get("summary"):
            log(f"   {r['summary'][:300]}")

    log(f"map finished: {made} mapped" + (f", {failed} failed" if failed else ""))
    return 1 if failed else 0


def status(only: str | None = None) -> int:
    jobs, problems, quiet = candidates(only)
    if jobs:
        log(f"{len(jobs)} project(s) with no architecture notes:")
        for j in jobs:
            log(f"   {j['name']:<22} {j['repo']}")
    else:
        log("every active project with a repo has architecture notes")
    for name, why in quiet:
        log(f"   {name:<22} {why}")
    for name, why in problems:
        log(f"   ! {name} — {why}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Turn a project's codebase into linked architecture notes.")
    ap.add_argument("--status", action="store_true",
                    help="which projects have no architecture notes; write nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="build the survey and report its size; call no model")
    ap.add_argument("--project", default="", help="only this hub note's name")
    ap.add_argument("--max", type=int, default=0, help="stop after N projects")
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    if not (a.status or a.dry_run):
        import fleet as fl
        fl._quiet_proactor_shutdown()
    if a.status:
        return status(a.project or None)
    return run(only=a.project or None, dry_run=a.dry_run,
               limit=a.max, model=a.model)


if __name__ == "__main__":
    raise SystemExit(main())
