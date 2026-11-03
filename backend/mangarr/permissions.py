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


def _open_managed(path: Path, root: Path) -> int:
    # Reject symlinks at every component; repairs never traverse a linked
    # directory into another share, even if the final file is a regular CBZ.
    root = root.resolve(strict=True)
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


def preview(candidates: list[tuple[str, str]], data_dir: Path) -> dict:
    policy = _policy
    if not hasattr(os, "fchown"):
        raise ValueError("Media permission repair requires a Linux container.")
    entries, skipped = [], 0
    for path, root in sorted(set(candidates)):
        try:
            if Path(path).suffix.lower() != ".cbz":
                continue
            fd = _open_managed(Path(path), Path(root))
            try:
                info = os.fstat(fd)
            finally:
                os.close(fd)
            before = [stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
            desired = [policy.file_mode, policy.uid if policy.uid >= 0 else info.st_uid, policy.gid if policy.gid >= 0 else info.st_gid]
            if before != desired:
                entries.append({"path": path, "root": root, "identity": _fingerprint(info), "before": before, "after": desired})
        except (OSError, ValueError):
            skipped += 1
    token = uuid.uuid4().hex
    record = {"created": time.time(), "policy": asdict(policy), "entries": entries}
    _store_path(data_dir, token, "preview").write_text(json.dumps(record), encoding="utf-8")
    return {"preview_id": token, "count": len(entries), "skipped": skipped, "examples": entries[:50], "enabled": policy.enabled}


def repair(data_dir: Path, token: str, *, undo: bool = False) -> dict:
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
        changed, skipped, errors = 0, 0, []
        journal_id = uuid.uuid4().hex
        journal_path = _store_path(data_dir, journal_id, "undo")
        def append_journal(entry):
            with journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        for entry in record["entries"]:
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
                errors.append({"path": entry["path"], "error": str(exc)})
        return {"changed": changed, "skipped": skipped, "errors": errors[:50], "error_count": len(errors), "undo_id": journal_id if journal_path.exists() else None}
    finally:
        _repair_lock.release()
