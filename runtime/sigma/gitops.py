"""
gitops — the one place Sigma writes to git.

Phase 4 of the dashboard gave Sigma hands: the fleet applies note proposals,
the dashboard toggles checkboxes, the ledger reverts. All of them commit, and
all of them commit HERE — one mutex, path-scoped `git add`, the resulting SHA
read back and returned — because "every autonomous change is one revertible
commit" is only true if nothing can interleave with the sequence that makes
the commit.

obsidian-git is the reason for most of this file's paranoia. It pulls every
15 minutes and commits/pushes every 30, from inside Obsidian, honouring no
lock of ours. So: pull before write, stage only the named paths, and if the
staged diff comes back empty, find the commit that already absorbed the
change instead of pretending one was made.

Everything here degrades rather than raises where a failure should cost a
missing commit, not a lost run — except the mutex, which raises GitBusy,
because writing without it is the one thing this module exists to prevent.
"""
import datetime
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
MUTEX_PATH = RUNTIME / "git.lock"
STALE_MINUTES = 10          # a crashed writer must not wedge every writer after it


class GitBusy(RuntimeError):
    """Another Sigma writer holds the git mutex and did not release in time."""


def _git(vault, *args, timeout=90):
    return subprocess.run(["git", "-C", str(vault), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


class _Mutex:
    """Exclusive-create lock file, same shape as the fleet's run lock.

    Distinct from that lock on purpose: the fleet holds its lock for a whole
    multi-minute run, while this one brackets a single pull-write-commit
    sequence measured in seconds. One mutex for git operations, held briefly,
    is what lets a dashboard toggle land during a fleet run without either
    corrupting the other's commit.
    """

    def __init__(self, path=None, timeout_s=30.0):
        # MUTEX_PATH is read at call time, not def time, so tests can re-point it
        self.path, self.timeout_s, self.held = Path(path or MUTEX_PATH), timeout_s, False

    def __enter__(self):
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                with self.path.open("x", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "pid": os.getpid(),
                        "at": datetime.datetime.now().isoformat(timespec="seconds")}))
                self.held = True
                return self
            except FileExistsError:
                if self._stale():
                    try:
                        self.path.unlink()
                    except OSError:
                        pass                     # loser of the takeover race defers
                    continue
                if time.monotonic() >= deadline:
                    raise GitBusy("another Sigma writer holds the git mutex")
                time.sleep(0.25)
            except OSError as e:
                raise GitBusy(f"could not take the git mutex: {e}") from e

    def _stale(self):
        try:
            at = json.loads(self.path.read_text(encoding="utf-8")).get("at")
            age = (datetime.datetime.now()
                   - datetime.datetime.fromisoformat(str(at))).total_seconds()
            return age > STALE_MINUTES * 60
        except Exception:
            return True                          # unreadable lock = stale lock

    def __exit__(self, *exc):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


def _pull(vault) -> dict:
    """Best-effort pull. obsidian-git pulls every 15 minutes underneath us, so a
    write that skips this can land on a 14-minute-old HEAD and lose the race.

    Best-effort because the failure modes are not ours to solve here: offline
    means the merge simply happens later; a conflict is aborted and left for
    obsidian-git's own conflict handling. Either way the local write is still
    safe — it becomes a fresh commit on local HEAD.
    """
    r = _git(vault, "remote")
    if r.returncode != 0 or not (r.stdout or "").strip():
        return {"ok": True, "note": "no remote"}
    r = _git(vault, "pull", "--no-rebase", "--no-edit")
    if r.returncode == 0:
        return {"ok": True, "note": None}
    _git(vault, "merge", "--abort")              # harmless if there is no merge
    detail = " ".join(((r.stderr or r.stdout) or "").split())[:200]
    return {"ok": False, "note": f"pull failed ({detail}) - writing on local HEAD"}


def head(vault) -> str | None:
    r = _git(vault, "rev-parse", "HEAD")
    return (r.stdout or "").strip() or None if r.returncode == 0 else None


class Writer:
    """Yielded by vault_write(); the only object with a commit method."""

    def __init__(self, vault, pulled):
        self.vault = Path(vault)
        self.pulled = pulled

    def commit(self, paths, message) -> dict:
        """Stage exactly `paths`, commit, return what actually happened.

        {"sha": str|None, "absorbed": bool, "note": str|None}

        sha None + note means the change produced no commit (an ignored path,
        or nothing differed). absorbed=True means an interleaved commit —
        obsidian-git's 30-minute backup is the expected culprit — already
        carries the change; its SHA is returned so the ledger can still point
        at a real, revertible commit rather than at nothing.
        """
        rels = [str(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
        r = _git(self.vault, "add", "--", *rels)
        if r.returncode != 0:
            blob = (r.stderr or r.stdout or "").lower()
            if "ignored" in blob:
                return {"sha": None, "absorbed": False,
                        "note": "gitignored path - written but never committed, no undo commit"}
            return {"sha": None, "absorbed": False,
                    "note": f"git add failed: {' '.join(blob.split())[:200]}"}
        staged = _git(self.vault, "diff", "--cached", "--quiet", "--", *rels)
        if staged.returncode == 0:               # nothing staged: absorbed or no-op
            r = _git(self.vault, "log", "-1", "--format=%H", "--", rels[0])
            sha = (r.stdout or "").strip() or None
            return {"sha": sha, "absorbed": bool(sha),
                    "note": "change was already committed (interleaved backup commit)"
                            if sha else "nothing to commit"}
        r = _git(self.vault, "commit", "-m", message, "--", *rels)
        if r.returncode != 0:
            detail = " ".join(((r.stderr or r.stdout) or "").split())[:200]
            _git(self.vault, "reset", "--", *rels)
            return {"sha": None, "absorbed": False, "note": f"commit failed: {detail}"}
        return {"sha": head(self.vault), "absorbed": False, "note": None}


@contextmanager
def vault_write(vault, timeout_s=30.0):
    """Mutex + pull, then a Writer. The whole write belongs inside this block:
    pull, re-verify whatever the write depends on, write, commit."""
    with _Mutex(timeout_s=timeout_s):
        yield Writer(vault, _pull(vault))


def revert(vault, sha, timeout_s=30.0) -> dict:
    """git revert one ledger commit. Conflicts are surfaced, never forced:
    a revert that no longer applies cleanly aborts and reports, because
    guessing at a resolution unattended is how an undo button loses data."""
    with _Mutex(timeout_s=timeout_s):
        _pull(vault)
        r = _git(vault, "revert", "--no-edit", str(sha))
        if r.returncode == 0:
            return {"ok": True, "sha": head(vault), "note": None}
        _git(vault, "revert", "--abort")
        detail = " ".join(((r.stderr or r.stdout) or "").split())[:300]
        return {"ok": False, "sha": None,
                "note": f"revert conflicts with later changes - resolve by hand ({detail})"}
