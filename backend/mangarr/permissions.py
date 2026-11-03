"""Opt-in permissions for Mangarr-created media, plus scoped preview/repair."""

import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from collections import OrderedDict
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)
_repair_lock = threading.Lock()


def resolve_id(raw: str, kind: str) -> int:
    raw = raw.strip()
    if not raw:
        return -1
    if raw.isdecimal() and int(raw) <= 4294967294:
        return int(raw)
    if raw == "nobody":
        raise ValueError("For Unraid's nobody user, enter UID 99; the container's nobody may be different.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", raw):
        raise ValueError(f"Permissions: enter a valid {kind} name or numeric ID.")
    try:
        if kind == "user":
            import pwd
            return pwd.getpwnam(raw).pw_uid
        import grp
        return grp.getgrnam(raw).gr_gid
    except (ImportError, KeyError):
        raise ValueError(f"Permissions: unknown {kind} {raw!r}; use its numeric ID.") from None


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    folder_mode: int = 0o775
    uid: int = 99
    gid: int = 100

    @property
    def file_mode(self) -> int:
        return self.folder_mode & 0o666


_policy = Policy()


def parse(values: dict[str, str]) -> Policy:
    mode = values.get("permissions_folder_mode", "775")
    if not re.fullmatch(r"7[0-7]{2}", mode):
        raise ValueError("Permissions: folder mode must be three octal digits starting with 7, such as 755 or 775.")
    return Policy(
        values.get("permissions_enabled") == "true", int(mode, 8),
        resolve_id(values.get("permissions_user", "99"), "user"),
        resolve_id(values.get("permissions_group", "100"), "group"),
    )


def configure(values: dict[str, str]) -> None:
    global _policy
    _policy = parse(values)


def _set(fd: int, mode: int, uid: int, gid: int) -> None:
    before = os.fstat(fd)
    if (uid >= 0 and before.st_uid != uid) or (gid >= 0 and before.st_gid != gid):
        os.fchown(fd, uid, gid)
    os.fchmod(fd, mode)
    after = os.fstat(fd)
    if stat.S_IMODE(after.st_mode) != mode or (uid >= 0 and after.st_uid != uid) or (gid >= 0 and after.st_gid != gid):
        raise PermissionError("The requested ownership or mode did not stick.")


def apply_created(path: Path, *, directory: bool = False) -> None:
    policy = _policy
    if not policy.enabled:
        return
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        if path.is_symlink():
            return
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode) != directory or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
                return  # chmod on a hardlink would also change the seeding file
            _set(fd, policy.folder_mode if directory else policy.file_mode, policy.uid, policy.gid)
        finally:
            os.close(fd)
    except (OSError, AttributeError) as exc:
        log.warning("Could not apply configured permissions to %s: %s", path, exc)


def mkdir_created(path: Path) -> None:
    """Apply policy only to directory components this call actually creates."""
    if path.exists() and not path.is_dir():
        raise FileExistsError(f"Not a directory: {path}")
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for folder in reversed(missing):
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        apply_created(folder, directory=True)
    if not path.is_dir():
        raise FileExistsError(f"Not a directory: {path}")


def _fingerprint(info) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def _open_managed(path: Path, root: Path, *, resolved_root: Path | None = None) -> int:
    # Reject symlinks at every component; repairs never traverse a linked
    # directory into another share, even if the final file is a regular CBZ.
    root = resolved_root if resolved_root is not None else root.resolve(strict=True)
    absolute = path.absolute()
    relative = absolute.relative_to(root)
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("Expected a media file inside its root")
    folder_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder_fd)
            os.close(folder_fd)
            folder_fd = next_fd
        fd = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(fd)
            raise ValueError("Skipped a symlink, shared hardlink, or non-file")
        return fd
    finally:
        os.close(folder_fd)


def _store_path(data_dir: Path, token: str, kind: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid permissions preview or undo ID")
    folder = data_dir / "logs" / "permissions"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{kind}-{token}.{'jsonl' if kind == 'undo' else 'json'}"


class _PreviewStats:
    """Read metadata without opening every media file or rewalking its folder.

    Pinned directory descriptors never follow replacement symlinks. This is
    preview-only; repairs still reopen and validate every path and identity.
    """
    def __init__(self):
        self.folders = OrderedDict()

    def close(self):
        for fd in self.folders.values():
            os.close(fd)
        self.folders.clear()

    def stat(self, path: Path, root: Path):
        relative = path.absolute().relative_to(root)
        if not relative.parts or any(part in {".", ".."} for part in relative.parts):
            raise ValueError("Expected a media file inside its root")
        key = (str(root), relative.parts[:-1])
        fd = self.folders.get(key)
        if fd is None:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in relative.parts[:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = child
                self.folders[key] = fd
            except BaseException:
                os.close(fd)
                raise
            if len(self.folders) > 128:
                os.close(self.folders.popitem(last=False)[1])
        else:
            self.folders.move_to_end(key)
        info = os.stat(relative.name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Skipped a symlink, shared hardlink, or non-file")
        return info


def preview(candidates, data_dir: Path, *, progress=None, policy=None) -> dict:
    policy = policy or _policy
    if not hasattr(os, "fchown"):
        raise ValueError("Media permission repair requires a Linux container.")
    token = uuid.uuid4().hex
    manifest = _store_path(data_dir, token, "preview")
    count, skipped, scanned, examples = 0, 0, 0, []
    roots: dict[str, Path] = {}
    stats = _PreviewStats()
    # Stream mismatches to disk; only the first 50 are sent to the browser.
    with manifest.with_suffix(".jsonl").open("w", encoding="utf-8") as output, closing(stats):
        for path, root in candidates:
            try:
                if Path(path).suffix.lower() != ".cbz":
                    continue
                if root not in roots:
                    roots[root] = Path(root).resolve(strict=True)
                info = stats.stat(Path(path), roots[root])
                before = [stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
                desired = [policy.file_mode, policy.uid if policy.uid >= 0 else info.st_uid, policy.gid if policy.gid >= 0 else info.st_gid]
                if before != desired:
                    entry = {"path": path, "root": root, "identity": _fingerprint(info), "before": before, "after": desired}
                    output.write(json.dumps(entry) + "\n")
                    count += 1
                    if len(examples) < 50:
                        examples.append(entry)
            except (OSError, ValueError):
                skipped += 1
            finally:
                scanned += 1
                if progress:
                    progress({"processed": scanned, "changed": count, "skipped": skipped})
    # A preview becomes usable only when its scan is complete. Expiry starts
    # here, so a long scan does not expire before the user can review it.
    manifest.write_text(json.dumps({"version": 2, "created": time.time(), "policy": asdict(policy), "count": count}), encoding="utf-8")
    return {"preview_id": token, "count": count, "skipped": skipped, "scanned": scanned, "examples": examples, "enabled": policy.enabled}


def repair(data_dir: Path, token: str, *, undo: bool = False, progress=None) -> dict:
    if not _repair_lock.acquire(blocking=False):
        raise ValueError("Another permissions repair is running")
    try:
        source = _store_path(data_dir, token, "undo" if undo else "preview")
        if undo:
            latest = {}
            for line in source.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                latest[entry["path"]] = entry
            record = {"entries": list(latest.values())}
        else:
            record = json.loads(source.read_text(encoding="utf-8"))
        if not undo and (not _policy.enabled or record["policy"] != asdict(_policy)):
            raise ValueError("Save and enable the permissions settings, then preview again before applying.")
        if not undo and time.time() - record["created"] > 3600:
            raise ValueError("This preview expired; preview again before applying.")
        changed, skipped, errors, error_count, processed = 0, 0, [], 0, 0
        journal_id = uuid.uuid4().hex
        journal_path = _store_path(data_dir, journal_id, "undo")
        def append_journal(entry):
            with journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        if not undo and record.get("version") == 2:
            def streamed_entries():
                with source.with_suffix(".jsonl").open(encoding="utf-8") as handle:
                    for line in handle:
                        yield json.loads(line)
            entries = streamed_entries()
            total = record["count"]
        else:
            entries = record["entries"]
            total = len(entries)
        if progress:
            progress({"total": total, "processed": 0, "undo_id": journal_id})
        for entry in entries:
            try:
                fd = _open_managed(Path(entry["path"]), Path(entry["root"]))
                try:
                    info = os.fstat(fd)
                    expected, desired = (entry["after"], entry["before"]) if undo else (entry["before"], entry["after"])
                    if _fingerprint(info) != entry["identity"] or [stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid] != expected:
                        skipped += 1
                        continue
                    # Persist the undo record before touching each file. A
                    # partially failed chmod/chown still has its original state.
                    event = {**entry, "before": expected, "after": desired}
                    append_journal(event)
                    try:
                        _set(fd, *desired)
                    finally:
                        actual = os.fstat(fd)
                        event["after"] = [stat.S_IMODE(actual.st_mode), actual.st_uid, actual.st_gid]
                        append_journal(event)
                    changed += 1
                finally:
                    os.close(fd)
            except (OSError, ValueError) as exc:
                error_count += 1
                if len(errors) < 50:
                    errors.append({"path": entry["path"], "error": str(exc)})
            finally:
                processed += 1
                if progress:
                    progress({"total": total, "processed": processed, "changed": changed, "skipped": skipped, "error_count": error_count, "undo_id": journal_id})
        return {"changed": changed, "skipped": skipped, "errors": errors[:50], "error_count": error_count, "undo_id": journal_id if journal_path.exists() else None}
    finally:
        _repair_lock.release()
