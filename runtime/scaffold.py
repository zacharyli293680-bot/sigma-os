#!/usr/bin/env python3
"""
scaffold.py — a new project from one line (dashboard-plan Phase 6).

`dashboard-vision`: "Scaffold a new project — repo, hub note, gitignore, the
boring parts — from a one-line description."

    sigma new "a CLI that watches a folder and thumbnails any image dropped in it"

      C:\\Users\\tusha\\Documents\\CS Projects\\thumbwatch\\   git init · .gitignore · README.md
      03-Projects/thumbwatch.md                              the hub note, as a revertible commit

**Not a framework generator.** It does not run `npm create vite` or `mvn
archetype:generate` and should not: those tools are better at their own job and
change under you. What it makes is the part nobody enjoys and everybody skips —
a folder that is already a git repo, a `.gitignore` that is right for the stack,
a README that says what the thing is, and a hub note the rest of Sigma can
already see. `sigma devlog` works on it from the first commit, because the hub is
written with the `## Dev log` section devlog splices into.

**The model chooses from a list; script code writes the files.** The `.gitignore`
is not model-written — a hallucinated one that omits `.env` is a credential leak
with a plausible explanation. The model picks a stack *name*, that name is a key
into `STACKS` below, and the contents come from this file. Same doctrine as the
palette's verb table, for the same reason.

**Everything outside the vault is refused unless it is new.** The one thing this
does that nothing else in Sigma does is write outside the vault, so the guards
are about that: the name must be a plain kebab slug, the target must resolve
inside the code root, and the directory must not already exist with anything in
it. No remote is created and nothing is pushed — that is Zach's to do.
"""
import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import devlog                                          # noqa: E402  (hub records)
import reflect as rf                                   # noqa: E402
from sigma import (call_model, kebab, make_logger,     # noqa: E402
                   parse_model_json, write_note)
from sigma import gitops                               # noqa: E402

VAULT = rf.VAULT
PROJECTS = VAULT / "03-Projects"

# A slug that is safe as a directory name, a filename and a wikilink target.
# Anchored and character-classed rather than "does not contain ..": a rule about
# what IS allowed cannot be walked around by an encoding nobody thought of.
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")

MODEL_TIMEOUT = 240

log = make_logger(_HERE / "scaffold.log", "scaffold", stream=sys.stdout)


# --------------------------------------------------------------------------
# stacks — the model picks a key, this file supplies the contents
# --------------------------------------------------------------------------

_COMMON = """
# OS / editor
.DS_Store
Thumbs.db
.idea/
.vscode/
*.swp

# secrets — never commit these
.env
.env.*
!.env.example
*.pem
*.key
"""

STACKS: dict = {
    "python": {
        "label": "Python",
        "ignore": """
__pycache__/
*.py[cod]
.venv/
venv/
env/
build/
dist/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
""",
    },
    "node": {
        "label": "Node / TypeScript",
        "ignore": """
node_modules/
dist/
build/
coverage/
*.tsbuildinfo
npm-debug.log*
yarn-error.log*
pnpm-debug.log*
""",
    },
    "react-vite": {
        "label": "React + Vite",
        "ignore": """
node_modules/
dist/
dist-ssr/
coverage/
*.local
*.tsbuildinfo
npm-debug.log*
""",
    },
    "next": {
        "label": "Next.js",
        "ignore": """
node_modules/
.next/
out/
build/
coverage/
next-env.d.ts
npm-debug.log*
""",
    },
    "java-maven": {
        "label": "Java / Maven",
        "ignore": """
target/
*.class
*.jar
!.mvn/wrapper/maven-wrapper.jar
.mvn/timing.properties
""",
    },
    "go": {
        "label": "Go",
        "ignore": """
bin/
*.exe
*.test
*.out
vendor/
""",
    },
    "rust": {
        "label": "Rust",
        "ignore": """
/target/
**/*.rs.bk
*.pdb
""",
    },
    "static": {
        "label": "Static site / plain HTML",
        "ignore": """
node_modules/
dist/
_site/
.cache/
""",
    },
    "other": {
        "label": "Something else",
        "ignore": "",
    },
}


def gitignore_for(stack: str) -> str:
    body = STACKS.get(stack, STACKS["other"])["ignore"].strip()
    head = f"# {STACKS.get(stack, STACKS['other'])['label']}\n{body}\n\n" if body else ""
    return head + _COMMON.strip() + "\n"


# --------------------------------------------------------------------------
# where code lives
# --------------------------------------------------------------------------

def code_root() -> Path | None:
    """The folder the existing project hubs point into.

    Derived rather than hardcoded, for the same reason `panels._code_root` does
    it: the runtime has already moved once, and a constant would have to be found
    and repointed by hand when it moves again. Computed here rather than imported
    because that one lives in a FastAPI module and this is a CLI script.
    """
    counts: dict = {}
    for rec in devlog.hub_records():
        repo = rec.get("repo") or ""
        if not repo:
            continue
        try:
            parent = Path(repo).parent
        except (OSError, ValueError):
            continue
        counts[parent] = counts.get(parent, 0) + 1
    if not counts:
        return None
    # Most-claimed wins. ProCertus lives under its own root and is a minority of
    # one, so this lands on `CS Projects` without needing to know its name.
    return max(counts.items(), key=lambda kv: (kv[1], str(kv[0])))[0]


# --------------------------------------------------------------------------
# asking
# --------------------------------------------------------------------------

PROMPT = """You are Sigma's project scaffolder. Zach described a project in one line and
you are turning it into the starting shape: a name, a stack, a README and a hub note summary.

His description:

    {description}

Projects that already exist (do NOT reuse one of these names):
{existing}

Answer with ONE JSON object and nothing else — no prose, no code fence:

{{
  "name": "kebab-case-slug",
  "title": "Human Readable Title",
  "stack": "one of: {stacks}",
  "summary": "one sentence, what the project is — goes under the hub note's title as a blockquote",
  "goal": "one or two sentences: what 'done' looks like for a first version",
  "readme": "the full body of README.md as markdown, starting with a # heading",
  "tasks": ["a concrete first task", "a second", "a third"]
}}

Rules:

- **`name`** is lowercase kebab, 3–50 characters, letters/digits/hyphens only. It becomes a
  folder name, a filename and a wikilink, so no spaces, dots, slashes or accents. Prefer
  something short and memorable over something descriptive: `thumbwatch`, not
  `image-thumbnail-folder-watcher`.
- **`stack`** must be exactly one of the listed keys. Pick from the description; choose
  `other` if it genuinely does not say and you would be guessing.
- **`readme`** is what a stranger reads first: what it is, why it exists, how to run it once
  it exists. Two or three short sections. Do not invent features he did not describe, and do
  not write installation steps for code that is not written yet — say what the intended
  entry point is instead.
- **`tasks`** are the real first moves for this specific project, in order. Not "set up the
  repo" — that is already done by the time he reads them.
- Everything you write is his to edit. Be concrete and brief; a scaffold that reads like
  filler gets deleted rather than filled in."""


def ask(description: str, model: str = "sonnet") -> tuple:
    """(plan, raw). `plan` is None if the model did not answer with usable JSON."""
    existing = sorted(rec["name"] for rec in devlog.hub_records())
    prompt = PROMPT.format(
        description=description.strip(),
        existing="\n".join(f"    - {n}" for n in existing) or "    (none)",
        stacks=", ".join(STACKS))
    raw = call_model(prompt, model, timeout=MODEL_TIMEOUT, actor="scaffold")
    return parse_model_json(raw), raw


# --------------------------------------------------------------------------
# checking — every rule here is about writing outside the vault
# --------------------------------------------------------------------------

def vet(plan: dict, root: Path) -> tuple:
    """(name, stack, problem). `problem` is None when it is safe to create.

    The model chose the name, so the name is untrusted input that becomes a
    filesystem path. Everything below treats it that way.
    """
    raw = str((plan or {}).get("name") or "").strip()

    # Path syntax is refused, never normalised. `kebab("../escape")` is
    # "escape" — a perfectly safe slug, and creating a folder called `escape`
    # is not what anyone meant. Salvage is for a model that wrote "Thumb Watch"
    # when it meant a slug; a name carrying separators or a `..` segment means
    # something is wrong upstream, and quietly turning it into a plausible
    # project is how a wrong thing gets built and looks fine.
    if any(ch in raw for ch in "/\\:") or ".." in raw:
        return "", "", (f"the model's name {raw!r} contains path syntax — "
                        f"refusing rather than normalising it into something "
                        f"that looks deliberate")

    name = raw.lower()
    if not SAFE_NAME.match(name):
        # kebab() is the same normaliser the rest of the OS uses for a title.
        name = kebab(name or str((plan or {}).get("title") or ""), "")
        if not SAFE_NAME.match(name):
            return "", "", f"the model's name {raw!r} is not a usable slug"

    stack = str((plan or {}).get("stack") or "other").strip().lower()
    if stack not in STACKS:
        stack = "other"

    dest = (root / name).resolve()
    try:
        # Belt and braces with SAFE_NAME: a slug that cannot contain a separator
        # cannot escape, and this proves it rather than assuming it.
        dest.relative_to(root.resolve())
    except ValueError:
        return name, stack, f"{name!r} would resolve outside {root}"

    if dest.exists() and any(dest.iterdir()):
        return name, stack, (f"{dest} already exists and is not empty — "
                             f"scaffolding would write into someone else's project")
    hub = PROJECTS / f"{name}.md"
    if hub.exists():
        return name, stack, (f"03-Projects/{name}.md already exists — pick another "
                             f"name, or open the hub you already have")
    return name, stack, None


# --------------------------------------------------------------------------
# making
# --------------------------------------------------------------------------

def hub_note(name: str, plan: dict, dest: Path, stack: str) -> str:
    """The hub note, composed here rather than by the model.

    Shaped like the hubs this vault already has rather than like
    `99-Meta/Templates/project.md`, which predates them — `## Dev log` and
    `## Decisions` are the sections `devlog.py` and `mapper.py` write into, so a
    hub scaffolded from the older template would be invisible to both.
    """
    url = str(dest).replace("\\", "/").replace(" ", "%20")
    tasks = [str(t).strip() for t in (plan.get("tasks") or []) if str(t).strip()][:5]
    today = datetime.date.today().isoformat()
    return (
        f"---\n"
        f"type: project\n"
        f"area: personal\n"
        f"status: active\n"
        f"started: {today}\n"
        f"due:\n"
        f"repo: {dest}\n"
        f"tags: [project]\n"
        f"---\n\n"
        f"# {plan.get('title') or name}\n\n"
        f"> {plan.get('summary') or ''}\n\n"
        f"**Code:** [Open repo folder](file:///{url}) · `{dest}`\n"
        f"**Stack:** {STACKS[stack]['label']}\n\n"
        f"## Goal\n"
        f"{plan.get('goal') or '-'}\n\n"
        f"## Current focus\n-\n\n"
        f"## Tasks\n"
        + ("\n".join(f"- [ ] {t}" for t in tasks) if tasks else "- ")
        + f"\n\n## Dev log\n-\n\n"
        f"## Decisions\n-\n\n"
        f"## Links\n-\n\n"
        f"## Related\n"
        f"- All projects: [[projects|Projects MOC]]\n")


def create_repo(dest: Path, name: str, plan: dict, stack: str) -> list:
    """Make the folder a git repo with its boring files. Returns what it wrote.

    Deliberately does NOT create a remote or push: that is the one step that
    leaves the machine, and it is Zach's to take.
    """
    made = []
    dest.mkdir(parents=True, exist_ok=True)

    readme = str(plan.get("readme") or "").strip()
    if not readme.startswith("#"):
        readme = f"# {plan.get('title') or name}\n\n{readme}".strip()
    write_note(dest / "README.md", readme + "\n")
    made.append("README.md")

    write_note(dest / ".gitignore", gitignore_for(stack))
    made.append(".gitignore")

    if not (dest / ".git").exists():
        r = gitops._git(dest, "init", "-b", "main", timeout=60)
        if r.returncode != 0:
            log(f"   git init failed: {(r.stderr or '').strip()[:160]}")
            return made
        made.append("git init (branch main)")
        gitops._git(dest, "add", "-A", timeout=60)
        c = gitops._git(dest, "commit", "-m",
                        f"Scaffold {name}: README and .gitignore", timeout=60)
        if c.returncode == 0:
            made.append("first commit")
        else:
            # A missing user.name/user.email is the usual cause, and it is worth
            # naming: the files are there, they are just not committed yet.
            log(f"   files written but not committed: "
                f"{(c.stderr or c.stdout or '').strip()[:160]}")
    return made


def propose_hub(name: str, content: str, description: str) -> Path:
    return rf.write_proposal({
        "title": f"Project hub: {name}",
        "kind": "note", "target": f"03-Projects/{name}.md", "content": content,
        "rationale": (f"Scaffolded from the one-line description: "
                      f"{description.strip()[:200]!r}. The repo, its .gitignore "
                      f"and its first commit were made by `scaffold.py`; this is "
                      f"the hub note that makes it visible to the rest of Sigma."),
        "risk": "low", "scope": "vault", "insight": "",
    }, datetime.date.today().isoformat())


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run(description: str, dry_run: bool = False, model: str = "sonnet") -> int:
    description = (description or "").strip()
    if len(description) < 12:
        log("say a bit more — one line describing what the project is")
        return 2

    root = code_root()
    if root is None or not root.is_dir():
        log("no project hub declares a `repo:` path, so there is nowhere to put "
            "this — create one project by hand first")
        return 1

    log(f"asking for a plan ({model})…")
    plan, raw = ask(description, model=model)
    if not plan:
        log(f"the model did not answer with usable JSON: "
            f"{' '.join((raw or '(nothing)').split())[:200]}")
        return 1

    name, stack, problem = vet(plan, root)
    if problem:
        log(f"refusing — {problem}")
        return 1

    dest = root / name
    log(f"-> {name}  ({STACKS[stack]['label']})")
    log(f"   {plan.get('summary') or ''}"[:200])
    log(f"   repo: {dest}")
    if dry_run:
        log(f"   would create: README.md, .gitignore, git init, first commit")
        log(f"   would propose: 03-Projects/{name}.md")
        return 0

    made = create_repo(dest, name, plan, stack)
    for m in made:
        log(f"   {m}")

    try:
        prop = propose_hub(name, hub_note(name, plan, dest, stack), description)
        import applier
        done = applier.apply_one(prop, actor="scaffold")
    except Exception as e:
        # The repo exists and that is the irreversible half; a hub note that did
        # not land is one command away, and saying so beats implying failure.
        log(f"   repo created, but the hub note failed: {type(e).__name__}: {e}")
        log(f"   the proposal is in 06-System/proposals/ — `sigma reflect apply`")
        return 1

    if done.get("action") in ("create", "update"):
        log(f"   hub → {done.get('target')} ({(done.get('sha') or '')[:10] or 'no commit'})")
    else:
        log(f"   HELD {done.get('target')} — {done.get('reason')}")
        return 1

    log(f"scaffolded {name}")
    log(f"   next: cd \"{dest}\" — and `sigma devlog` will write up your commits")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Turn a one-line description into a repo and a project hub.")
    ap.add_argument("description", nargs="*",
                    help="one line: what the project is")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan it and print what would be made; create nothing")
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    if not a.description:
        ap.print_help()
        return 2
    return run(" ".join(a.description), dry_run=a.dry_run, model=a.model)


if __name__ == "__main__":
    raise SystemExit(main())
