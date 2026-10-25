"""Shared Git repository operations: clone, fetch, checkout, and worktree cleanup.

Common logic extracted from eval/run_review.py and eval/run_review_claude.py
for reuse by the OCR and Claude reviewers.
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional


class RepoError(Exception):
    """Raised for a recoverable Git error while processing a single review case."""


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def run_command(
    command: List[str],
    cwd: Optional[Path] = None,
    timeout_seconds: Optional[int] = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    """Run a subprocess without raising on nonzero exit; the caller checks returncode."""
    log(f"$ {' '.join(command)}" + (f"  (cwd={cwd})" if cwd else ""))
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        timeout=timeout_seconds,
        capture_output=capture_output,
        text=True,
        check=False,
    )


def local_repo_path(repo_dir: Path, repo_full_name: str) -> Path:
    """Map 'owner/name' to a stable local directory, repo/owner__name."""
    safe_name = repo_full_name.replace("/", "__")
    return Path(repo_dir) / safe_name


def clone_or_update_repo(clone_url: str, target_dir: Path) -> None:
    """Clone a missing repository, or fetch an existing one to make later commits available."""
    target_dir = Path(target_dir)
    if target_dir.exists() and (target_dir / ".git").exists():
        log(f"Repo already cloned, fetching latest: {target_dir}")
        result = run_command(
            ["git", "fetch", "--all", "--tags"], cwd=target_dir, timeout_seconds=1800
        )
        if result.returncode != 0:
            log(f"git fetch warning (continuing): {result.stderr.strip()}")
        return

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    log(f"Cloning {clone_url} -> {target_dir}")
    result = run_command(["git", "clone", clone_url, str(target_dir)], timeout_seconds=3600)
    if result.returncode != 0:
        raise RepoError(f"git clone failed: {result.stderr.strip()}")


def ensure_commit_present(repo_path: Path, commit: str) -> None:
    """Ensure a commit exists locally, fetching it directly if needed."""
    check = run_command(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=repo_path)
    if check.returncode == 0:
        return
    log(f"Commit {commit[:12]} not found locally, fetching directly...")
    fetch = run_command(["git", "fetch", "origin", commit], cwd=repo_path, timeout_seconds=1800)
    if fetch.returncode != 0:
        raise RepoError(f"commit {commit} unavailable after fetch: {fetch.stderr.strip()}")


def clean_worktree(repo_path: Path) -> None:
    """Restore a clean worktree, discarding all local changes and untracked files.

    Reviewers (especially Claude Code's acceptEdits mode) may leave temporary files
    or modify tracked files. `git checkout -f` only overwrites tracked files;
    untracked files accumulate and can block later checkouts or affect the next
    review. Run this cleanup before and after each review:

      git reset --hard   -> revert changes to tracked files
      git clean -fdx     -> remove untracked files/directories, including ignored ones
    """
    repo_path = Path(repo_path)
    if not (repo_path / ".git").exists():
        return
    reset = run_command(["git", "reset", "--hard"], cwd=repo_path, timeout_seconds=300)
    if reset.returncode != 0:
        log(f"git reset --hard warning (continuing): {reset.stderr.strip()}")
    clean = run_command(["git", "clean", "-fdx"], cwd=repo_path, timeout_seconds=300)
    if clean.returncode != 0:
        log(f"git clean -fdx warning (continuing): {clean.stderr.strip()}")


def checkout_commit(repo_path: Path, commit: str) -> None:
    """Check out a commit after cleaning the worktree to prevent leftover changes from blocking it."""
    ensure_commit_present(repo_path, commit)
    clean_worktree(repo_path)
    result = run_command(["git", "checkout", "-f", commit], cwd=repo_path)
    if result.returncode != 0:
        raise RepoError(f"git checkout {commit} failed: {result.stderr.strip()}")


def diff_stat(repo_path: Path, base_commit: str, head_commit: str) -> str:
    """Lightweight diff summary for logging only; reviewers explore the repository themselves."""
    result = run_command(
        ["git", "diff", "--stat", base_commit, head_commit],
        cwd=repo_path,
        timeout_seconds=120,
    )
    if result.returncode != 0:
        return "(diff --stat unavailable)"
    lines = result.stdout.strip().splitlines()
    return lines[-1].strip() if lines else "(no changes)"


def prepare_repo(
    repo_dir: Path,
    clone_url: str,
    repo_full_name: str,
    base_commit: str,
    head_commit: str,
) -> Path:
    """Prepare a repository for a review case: clone -> ensure commit -> checkout head.

    Return the local repository path.
    """
    repo_path = local_repo_path(repo_dir, repo_full_name)
    clone_or_update_repo(clone_url, repo_path)
    ensure_commit_present(repo_path, base_commit)
    checkout_commit(repo_path, head_commit)
    return repo_path
