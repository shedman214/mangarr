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


def folder_specs(series_list) -> list[tuple[str, list[tuple[Path, bool]]]]:
    """(title, folders) for each series — plain data, so the disk checks in
    plan_folder_deletion can run in a worker thread."""
    return [(s.title, series_folder_paths(s)) for s in series_list]


def plan_folder_deletion(doomed, others, roots: list[str]) -> FolderPlan:
    """Which folders of the series being removed (`doomed`) may be deleted.
    `doomed` and `others` (the series that stay) come from folder_specs.
    Touches the disk; run it off the event loop.

    Across the whole request: a folder refused for one series is never
    deleted for another, nor through a parent folder; a folder inside
    another planned folder is dropped (deleting the parent covers it)."""
    plan = FolderPlan()
    root_paths = [Path(r).resolve(strict=False) for r in roots]
    other_paths = [
        (title, path.resolve(strict=False))
        for title, folders in others
        for path, _shared in folders
    ]
    candidates: list[tuple[Path, Path]] = []  # (path, resolved)
    refused_real: list[Path] = []
    seen_refused: set[str] = set()

    def refuse(path: Path, reason: str) -> None:
        refused_real.append(path.resolve(strict=False))
        if str(path) not in seen_refused:
            seen_refused.add(str(path))
            plan.refused.append((path, reason))

    for _title, folders in doomed:
        for path, shared in folders:
            if path.is_symlink():
                refuse(path, "it is a symbolic link")
                continue
            if not path.exists():
                continue  # nothing on disk to delete
            if not path.is_dir():
                refuse(path, "it is not a folder")
                continue
            real = path.resolve(strict=False)
            if any(real == root for root in root_paths):
                refuse(path, "it is a root folder")
                continue
            if not any(root in real.parents for root in root_paths):
                refuse(path, "it is outside the root folders")
                continue
            if shared:
                refuse(path, "it is a shared folder")
                continue
            user = next((t for t, other in other_paths
                         if _inside(other, real) or _inside(real, other)), None)
            if user is not None:
                refuse(path, f"“{user}” also uses it")
                continue
            candidates.append((path, real))

    kept: list[tuple[Path, Path]] = []
    for path, real in candidates:
        # a refused folder at, inside or above this one protects it
        guard = next((r for r in refused_real if _inside(r, real) or _inside(real, r)), None)
        if guard is not None:
            refuse(path, "another folder in this removal is kept and overlaps it")
            continue
        kept.append((path, real))
    planned: set[Path] = set()
    for path, real in kept:
        if any(real != other and _inside(real, other) for _p, other in kept):
            continue  # deleted with its parent folder
        if real not in planned:  # two spellings of one folder: delete it once
            planned.add(real)
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
