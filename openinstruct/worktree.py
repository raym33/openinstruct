from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .config import sanitize_session_name


class WorktreeError(RuntimeError):
    pass


@dataclass
class GitWorktree:
    repo_root: Path
    worktree_root: Path
    workspace_root: Path


def detect_git_repo(path: Path) -> Optional[Path]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    root = completed.stdout.strip()
    return Path(root).expanduser().resolve() if root else None


def _iter_workspace_entries(root: Path, ignored_roots: Iterable[Path]) -> List[Path]:
    ignored = [path.expanduser().resolve() for path in ignored_roots]
    entries: List[Path] = []
    for entry in sorted(root.rglob("*")):
        if any(part in {".git", "node_modules", "__pycache__"} for part in entry.parts):
            continue
        if entry.name == ".DS_Store":
            continue
        if any(entry == item or item in entry.parents for item in ignored):
            continue
        entries.append(entry)
    return entries


def mirror_workspace(source_root: Path, target_root: Path, ignored_roots: Optional[List[Path]] = None) -> None:
    ignored = ignored_roots or []
    target_root.mkdir(parents=True, exist_ok=True)

    source_entries = _iter_workspace_entries(source_root, ignored)
    source_rel = {entry.relative_to(source_root) for entry in source_entries}
    target_entries = _iter_workspace_entries(target_root, [])

    for entry in sorted(target_entries, key=lambda item: len(item.parts), reverse=True):
        rel = entry.relative_to(target_root)
        if rel not in source_rel:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()

    for entry in source_entries:
        rel = entry.relative_to(source_root)
        destination = target_root / rel
        if entry.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, destination)


def create_isolated_worktree(
    source_root: Path,
    home: Path,
    label: str,
    ignored_roots: Optional[List[Path]] = None,
) -> Optional[GitWorktree]:
    repo_root = detect_git_repo(source_root)
    if repo_root is None:
        return None

    try:
        workspace_rel = source_root.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise WorktreeError(f"Workspace root is not inside repo root: {source_root}") from exc

    worktrees_dir = home.expanduser().resolve() / "worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    worktree_root = worktrees_dir / sanitize_session_name(label)
    if worktree_root.exists():
        shutil.rmtree(worktree_root, ignore_errors=True)

    completed = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", "--detach", str(worktree_root), "HEAD"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise WorktreeError(completed.stderr.strip() or "git worktree add failed")

    workspace_root = worktree_root / workspace_rel if str(workspace_rel) != "." else worktree_root
    mirror_workspace(source_root, workspace_root, ignored_roots=ignored_roots)
    return GitWorktree(repo_root=repo_root, worktree_root=worktree_root, workspace_root=workspace_root)


def remove_isolated_worktree(worktree: GitWorktree) -> None:
    completed = subprocess.run(
        ["git", "-C", str(worktree.repo_root), "worktree", "remove", "--force", str(worktree.worktree_root)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0 and worktree.worktree_root.exists():
        shutil.rmtree(worktree.worktree_root, ignore_errors=True)
