#!/usr/bin/env python3
"""
install_hooks.py — put the pre-push privacy guard into every repo that needs it.

Git hooks live in .git/hooks/, which git does not track, so the canonical copy
lives here (versioned) and this script copies it into each working repo. Re-run
it after cloning to a new machine, or after editing hooks/pre-push.

  python install_hooks.py             install into the known repos
  python install_hooks.py --status    report which repos have it, without writing
"""
import argparse, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sigma import DEFAULT_VAULT

HOOK_SRC = Path(__file__).resolve().parent / "hooks" / "pre-push"

# The vault is the one that matters — it holds the carved-out material and
# auto-pushes unattended. This repo is included so the guard travels with it.
TARGETS = [DEFAULT_VAULT, Path(__file__).resolve().parent.parent]


def hook_path(repo: Path) -> Path | None:
    git = repo / ".git"
    if git.is_file():                      # worktree/submodule: .git is a pointer
        try:
            git = Path(git.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip())
        except (OSError, IndexError):
            return None
    return git / "hooks" / "pre-push" if git.is_dir() else None


def main():
    ap = argparse.ArgumentParser(description="Install the pre-push privacy guard.")
    ap.add_argument("--status", action="store_true", help="report only, write nothing")
    a = ap.parse_args()

    if not HOOK_SRC.exists():
        print(f"!! missing source hook: {HOOK_SRC}", file=sys.stderr)
        return 1

    want = HOOK_SRC.read_text(encoding="utf-8")
    rc = 0
    for repo in TARGETS:
        dst = hook_path(repo)
        if dst is None:
            print(f"!! {repo}: not a git repository")
            rc = 1
            continue
        current = dst.read_text(encoding="utf-8") if dst.exists() else None
        if current == want:
            print(f"OK {repo.name}: pre-push installed and current")
            continue
        if a.status:
            print(f"!! {repo.name}: pre-push {'stale' if current else 'MISSING'}")
            rc = 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HOOK_SRC, dst)
        dst.chmod(0o755)
        print(f"-> {repo.name}: pre-push {'updated' if current else 'installed'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
