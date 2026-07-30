"""
applier — the deterministic half of auto-apply (dashboard-plan Phase 4).

The specialists still cannot write: `propose_change` remains their only tool
that touches disk, and it writes a proposal note. What changed is what happens
next — after a specialist finishes, the fleet hands this module the proposals
that run raised, and *script code* applies each `kind: note` proposal as its
own git commit, recorded in the ledger, revertible in one click.

The model never executes here. Every decision below is a rule, not a judgment
call, which is what makes the flip of "propose, don't self-apply" safe to run
unattended:

  held, never applied — anything that is not a fresh `kind: note` proposal ·
  a target that escapes the vault, is gitignored, or lands in the proposals
  machinery itself · anything touching CLAUDE.md (the single held category) ·
  a change that ticks a checkbox (completion is a human signal) · a change
  that would hollow out an existing note.

A held proposal stays `pending` — exactly what every proposal used to be — and
is stamped with why, so waiting-on-you explains itself.
"""
import re
from pathlib import Path

from sigma import gitops, ledger, make_logger

HERE = Path(__file__).resolve().parent
log = make_logger(HERE / "fleet.log", "sigma applier")

# Any increase in ticked boxes is held, including a brand-new "- [x] done"
# line: an agent must never mark work finished on Zach's behalf.
_CHECKED = re.compile(r"^\s*[-*]\s+\[[xX]\]", re.M)

# The machinery must not write to itself: a proposal whose target is another
# proposal (or the staging area) would let one auto-applied note approve the
# next, which reopens the gate this module is supposed to keep shut.
_SELF_PATHS = ("06-System/proposals/", "06-System/proposed/")

HOLLOW_RATIO = 0.4


def _title_of(text: str, fallback: str) -> str:
    m = re.search(r"^# (.+)$", text, re.M)
    return (m.group(1).strip() if m else fallback)[:80]


def _stamp(path: Path, block: str):
    try:
        text = path.read_text(encoding="utf-8")
        path.write_text(text.rstrip() + "\n\n---\n" + block + "\n", encoding="utf-8")
    except OSError as e:
        log(f"could not stamp {path.name}: {e}")


def _mark_auto_applied(path: Path, today: str, rel: str, sha: str | None):
    try:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"^status: pending\s*$", "status: applied", text, count=1, flags=re.M)
        text = re.sub(r"^applied:\s*$", f"applied: {today}", text, count=1, flags=re.M)
        text = text.replace("status **pending**", "status **applied**", 1)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        log(f"could not mark {path.name} applied: {e}")
        return
    commit_line = (f"commit `{sha[:10]}` — revert it from the dashboard's activity ledger"
                   if sha else "no commit (untracked path), so no undo commit exists")
    _stamp(path, f"**Auto-applied {today}** → `{rel}` · {commit_line}.")


def _mark_held(path: Path, today: str, reason: str):
    try:
        if "**Held " in path.read_text(encoding="utf-8"):
            return
    except OSError:
        return
    _stamp(path, f"**Held {today}** — {reason}. It stays pending for your review.")


def apply_one(prop_path: Path, actor: str) -> dict:
    """Apply or hold one proposal. Never raises; never deletes anything."""
    import datetime

    import reflect as rf                        # read at call time: tests re-point rf.VAULT

    name = prop_path.stem
    today = datetime.date.today().isoformat()
    out = {"proposal": name, "action": "held", "target": None, "sha": None,
           "reason": None, "absorbed": False}

    def held(reason: str) -> dict:
        out["reason"] = reason
        _mark_held(prop_path, today, reason)
        log(f"HELD {name}: {reason}")
        return out

    try:
        text = prop_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        out["reason"] = f"unreadable proposal ({e})"
        return out
    fm = rf.frontmatter(text)
    if fm.get("type") != "proposal" or fm.get("status") != "pending":
        return held("not a pending proposal")
    kind = fm.get("kind", "")
    if kind != "note":
        return held(f"kind '{kind or '?'}' keeps the approval gate — only notes auto-apply")

    target = fm.get("target", "")
    dest = rf.safe_target(target, "vault")
    if dest is None:
        return held(f"target '{target}' does not resolve inside the vault")
    vault = Path(rf.VAULT).resolve()
    rel = dest.relative_to(vault).as_posix()
    out["target"] = rel
    if dest == Path(rf.CONTRACT).resolve() or dest.name.lower() == "claude.md":
        return held("the contract is the single held category")
    if any(rel.startswith(p) for p in _SELF_PATHS):
        return held("targets the proposal machinery itself")

    content = rf.proposal_content(text)
    if not content.strip():
        return held("empty content block")
    title = _title_of(text, name)

    try:
        with gitops.vault_write(vault) as w:
            # everything stateful happens inside the mutex, after the pull:
            # the guards must judge the file as it is NOW, not as it was
            # before obsidian-git's last sync landed.
            ign = gitops._git(vault, "check-ignore", "-q", rel)
            if ign.returncode == 0:
                return held("gitignored path — sealed or machine-local, never auto-written")
            if ign.returncode not in (0, 1):
                return held("could not verify the privacy boundary — failing closed")

            existed = dest.exists()
            if existed:
                old = dest.read_text(encoding="utf-8", errors="replace")
                if len(_CHECKED.findall(content)) > len(_CHECKED.findall(old)):
                    return held("ticks a checkbox — completion is a human signal")
                if len(content.strip()) < HOLLOW_RATIO * len(old.strip()):
                    return held(f"would shrink the note to under "
                                f"{int(HOLLOW_RATIO * 100)}% of its current size")

            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content.rstrip() + "\n", encoding="utf-8")
            verb = "update" if existed else "create"
            res = w.commit(rel, f"sigma({actor}): {verb} {rel} - {title[:60]}")
    except gitops.GitBusy as e:
        # written nothing yet: the proposal simply stays pending for next time
        return held(f"the git mutex is busy ({e}) — left pending, nothing written")

    out.update({"action": verb, "sha": res["sha"], "absorbed": res["absorbed"],
                "reason": res["note"]})
    ledger.record(actor, verb, rel, res["sha"], title,
                  extra={"proposal": name, **({"absorbed": True} if res["absorbed"] else {})})
    _mark_auto_applied(prop_path, today, rel, res["sha"])
    log(f"applied {name} -> {rel} "
        f"({res['sha'][:10] if res['sha'] else 'no commit'}"
        f"{', absorbed' if res['absorbed'] else ''})")
    return out


def apply_run(files: list[str], actor: str) -> list[dict]:
    """Apply every proposal a specialist's run just raised, one commit each."""
    import reflect as rf

    out = []
    for fname in files or []:
        p = Path(rf.PROPOSALS) / fname
        if not p.exists():
            log(f"skip {fname}: vanished before apply")
            continue
        out.append(apply_one(p, actor))
    return out
