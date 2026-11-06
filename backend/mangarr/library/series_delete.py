"""Removing a series can also delete its folders on disk (Remove → "Delete
files and folders on disk", off by default, like Sonarr's "Delete series
folder").

Only folders that belong to that series alone are deleted. A folder is
refused when it is a root folder itself or lies outside every root folder,
is a symlink, is a shared folder (a volume-offset extra folder), or another
series uses it, sits inside it, or contains it."""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .scanner import resolve_folders

log = logging.getLogger(__name__)


@dataclass
class FolderPlan:
    delete: list[Path] = field(default_factory=list)
    refused: list[tuple[Path, str]] = field(default_factory=list)


def series_folder_paths(series) -> list[tuple[Path, bool]]:
    """(folder, shared) for the series: its primary folder and extra folders;
    `shared` marks a volume-offset folder other series may also read."""
    if series.root_folder is None:
        return []
    root = Path(series.root_folder.path)
    extras = list(series.extra_folders)
    folders = resolve_folders(root, series, [f.path for f in extras])
    shared = {str(root / f.path) for f in extras if f.volume_offset is not None}
    return [(path, str(path) in shared) for path in folders]


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def plan_folder_deletion(series, others, roots: list[str]) -> FolderPlan:
    """Which of `series`' folders may be deleted. `others` are the series
    that stay in the library, with their root and extra folders loaded."""
    plan = FolderPlan()
    root_paths = [Path(r).resolve(strict=False) for r in roots]
    other_paths = [
        (other.title, path.resolve(strict=False))
        for other in others
        for path, _shared in series_folder_paths(other)
    ]
    for path, shared in series_folder_paths(series):
        if path.is_symlink():
            plan.refused.append((path, "it is a symbolic link"))
            continue
        if not path.exists():
            continue  # nothing on disk to delete
        if not path.is_dir():
            plan.refused.append((path, "it is not a folder"))
            continue
        real = path.resolve(strict=False)
        if any(real == root for root in root_paths):
            plan.refused.append((path, "it is a root folder"))
            continue
        if not any(root in real.parents for root in root_paths):
            plan.refused.append((path, "it is outside the root folders"))
            continue
        if shared:
            plan.refused.append((path, "it is a shared folder"))
            continue
        user = next((title for title, other in other_paths
                     if _inside(other, real) or _inside(real, other)), None)
        if user is not None:
            plan.refused.append((path, f"“{user}” also uses it"))
            continue
        plan.delete.append(path)
    return plan


def delete_folders(paths: list[Path]) -> tuple[list[str], list[str]]:
    """Delete the planned folders. Returns (deleted, problems)."""
    deleted, problems = [], []
    for path in paths:
        try:
            shutil.rmtree(path)
            deleted.append(str(path))
            log.info("Deleted series folder %s", path)
        except OSError as exc:
            log.warning("could not delete %s: %s", path, exc)
            problems.append(f"{path}: {exc.strerror or exc}")
    return deleted, problems
