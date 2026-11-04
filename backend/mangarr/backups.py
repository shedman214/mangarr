"""Online SQLite snapshots and validated, restart-only restoration."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile

from . import __version__

MAX_BYTES = 2 * 1024 ** 3
_lock = threading.Lock()


def folder(data_dir):
    path = Path(data_dir) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def archive_path(data_dir, token):
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid backup ID")
    return folder(data_dir) / f"{token}.zip"


def _validate_db(path, check_schema=True):
    from .models import Base
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
            raise ValueError("Backup database contains unsupported triggers or views")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database failed its integrity check")
        if not check_schema:  # pre-migration snapshots hold the OLD layout by definition
            return
        # Match all mapped columns; restoring an incompatible schema is unsafe.
        for table in Base.metadata.sorted_tables:
            columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table.name}")')}
            if not set(table.columns.keys()) <= columns:
                raise ValueError("Backup database schema is incompatible with this Mangarr version")


def create(data_dir, reason="manual"):
    with _lock:
        root = Path(data_dir).resolve()
        with tempfile.TemporaryDirectory(dir=folder(root)) as temp:
            snapshot = Path(temp) / "mangarr.db"
            with closing(sqlite3.connect(f"{(root / 'mangarr.db').as_uri()}?mode=ro", uri=True)) as src, closing(sqlite3.connect(snapshot)) as dest:
                src.backup(dest, pages=256, sleep=0.05)
            _validate_db(snapshot, check_schema=reason != "pre-migration")
            with snapshot.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            manifest = {"format": 1, "version": __version__, "created": time.time(),
                        "database_sha256": digest, "reason": reason}
            token = uuid.uuid4().hex
            target = archive_path(root, token)
            temporary = target.with_suffix(".tmp")
            try:
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.write(snapshot, "mangarr.db")
                    archive.writestr("manifest.json", json.dumps(manifest))
                    if (root / "api_key").is_file():
                        archive.write(root / "api_key", "api_key")
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    return {"id": token, "created": manifest["created"], "bytes": target.stat().st_size, "reason": reason}


KEEP_PREMIGRATION = 5


def pending_schema_changes(db_path):
    """Tables/columns this version needs that the database file lacks (empty = nothing to migrate)."""
    from .models import Base
    path = Path(db_path)
    if not path.is_file():
        return []
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return []
        changes = []
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                changes.append(table.name)
                continue
            columns = {r[1] for r in db.execute(f'PRAGMA table_info("{table.name}")')}
            changes += [f"{table.name}.{c}" for c in table.columns.keys() if c not in columns]
        return changes


def pre_migration_backup(data_dir):
    """Back up an existing database before init_db changes its layout. Returns the backup or None.

    Raises if the backup cannot be made, so the caller can refuse to migrate."""
    root = Path(data_dir).resolve()
    changes = pending_schema_changes(root / "mangarr.db")
    if not changes:
        return None
    result = create(root, reason="pre-migration")
    result["changes"] = changes
    _prune_premigration(root)
    return result


def list_backups(data_dir):
    """Server-side backups, newest first. Only metadata is read; archives are not extracted."""
    out = []
    for path in folder(data_dir).glob("*.zip"):
        if not re.fullmatch(r"[0-9a-f]{32}", path.stem):
            continue
        try:
            with zipfile.ZipFile(path) as z:
                if z.getinfo("manifest.json").file_size > 4096:
                    continue
                manifest = json.loads(z.read("manifest.json"))
                if not isinstance(manifest, dict) or not isinstance(manifest.get("created"), (int, float)):
                    continue
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
        out.append({"id": path.stem, "created": manifest.get("created"), "version": manifest.get("version"),
                    "reason": manifest.get("reason", "manual"), "bytes": path.stat().st_size})
    return sorted(out, key=lambda b: b["created"] or 0, reverse=True)


def _prune_premigration(root):
    automatic = [b for b in list_backups(root) if b["reason"] == "pre-migration"]
    for old in automatic[KEEP_PREMIGRATION:]:
        archive_path(root, old["id"]).unlink(missing_ok=True)


def validate(archive, destination):
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or set(names) - {"manifest.json", "mangarr.db", "api_key"} or not {"manifest.json", "mangarr.db"} <= set(names):
            raise ValueError("Unexpected or missing backup entries")
        if any(i.file_size > MAX_BYTES or i.flag_bits & 1 for i in z.infolist()) or sum(i.file_size for i in z.infolist()) > MAX_BYTES:
            raise ValueError("Backup is too large or encrypted")
        if z.getinfo("manifest.json").file_size > 4096 or ("api_key" in names and z.getinfo("api_key").file_size > 4096):
            raise ValueError("Invalid backup metadata")
        manifest = json.loads(z.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("version") != __version__:
            raise ValueError("Restore requires a backup from this Mangarr version")
        db = Path(destination) / "mangarr.db"
        with z.open("mangarr.db") as src, db.open("wb") as dest:
            shutil.copyfileobj(src, dest)
        with db.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != manifest.get("database_sha256"):
            raise ValueError("Backup checksum does not match")
        # A pre-migration copy holds the previous layout; startup migrates it forward after restoring.
        _validate_db(db, check_schema=manifest.get("reason") != "pre-migration")
        if "api_key" in names:
            key = z.read("api_key").decode().strip()
            if not re.fullmatch(r"[a-zA-Z0-9_-]{16,256}", key):
                raise ValueError("Invalid backup API key")
            (Path(destination) / "api_key").write_text(key)
        return manifest


def stage(data_dir, uploaded):
    with _lock:
        root = folder(data_dir)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            manifest = validate(uploaded, temp)
            pending = root / "pending-restore.zip"
            if pending.exists():
                raise ValueError("A restore is already scheduled; cancel it first")
            os.chmod(uploaded, 0o600)
            os.replace(uploaded, pending)
        return {"pending": True, "created": manifest["created"], "restart_required": True}


def status(data_dir):
    return {"pending": (folder(data_dir) / "pending-restore.zip").is_file()}


def restore_pending(data_dir):
    """Only call before database initialization and scheduler startup."""
    root = Path(data_dir).resolve()
    pending = folder(root) / "pending-restore.zip"
    if not pending.exists():
        return
    with tempfile.TemporaryDirectory(dir=folder(root)) as temp:
        validate(pending, temp)
        safety = create(root) if (root / "mangarr.db").exists() else None
        try:
            # SQLite backup handles WAL safely; never copy a live DB file.
            with closing(sqlite3.connect(Path(temp) / "mangarr.db")) as src, closing(sqlite3.connect(root / "mangarr.db")) as dest:
                src.backup(dest)
            # Preserve the installation's bootstrap key so existing clients keep access.
        except Exception:
            if safety:
                with tempfile.TemporaryDirectory(dir=folder(root)) as rollback:
                    validate(archive_path(root, safety["id"]), rollback)
                    with closing(sqlite3.connect(Path(rollback) / "mangarr.db")) as src, closing(sqlite3.connect(root / "mangarr.db")) as dest:
                        src.backup(dest)
            raise
    os.replace(pending, folder(root) / f"restored-{uuid.uuid4().hex}.zip")
