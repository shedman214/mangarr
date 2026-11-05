import hashlib
import os
import json
import sqlite3
import zipfile

import pytest
from sqlalchemy import create_engine

from mangarr import backups
from mangarr.models import Base


def library(root):
    root.mkdir(exist_ok=True)
    engine = create_engine(f"sqlite:///{root / 'mangarr.db'}")
    Base.metadata.create_all(engine)
    engine.dispose()
    (root / "api_key").write_text("a" * 32)
    db = sqlite3.connect(root / "mangarr.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("INSERT INTO settings (key,value) VALUES ('test','original')")
    db.commit()
    return db


def test_online_wal_snapshot_restore_and_safety_copy(tmp_path):
    db = library(tmp_path)
    result = backups.create(tmp_path)
    path = backups.archive_path(tmp_path, result["id"])
    upload = tmp_path / "upload.zip"
    upload.write_bytes(path.read_bytes())
    with zipfile.ZipFile(path) as z:
        assert set(z.namelist()) == {"manifest.json", "mangarr.db", "api_key"}
    db.execute("UPDATE settings SET value='new'")
    db.commit()
    backups.stage(tmp_path, upload)
    assert db.execute("SELECT value FROM settings WHERE key='test'").fetchone()[0] == "new"
    db.close()
    (tmp_path / "api_key").write_text("b" * 32)
    backups.restore_pending(tmp_path)
    assert not backups.status(tmp_path)["pending"]
    with sqlite3.connect(tmp_path / "mangarr.db") as restored:
        assert restored.execute("SELECT value FROM settings WHERE key='test'").fetchone()[0] == "original"
    assert (tmp_path / "api_key").read_text() == "b" * 32
    assert len(list(backups.folder(tmp_path).glob("*.zip"))) == 3
    # Repeated startup does not apply the same restore again.
    backups.restore_pending(tmp_path)


@pytest.mark.parametrize("kind", ["traversal", "duplicate", "checksum", "version", "schema"])
def test_reject_invalid_restore_without_touching_database(tmp_path, kind):
    db = library(tmp_path)
    result = backups.create(tmp_path)
    with zipfile.ZipFile(backups.archive_path(tmp_path, result["id"])) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    if kind == "traversal":
        entries["../escape"] = b"bad"
    elif kind in {"checksum", "version"}:
        manifest = json.loads(entries["manifest.json"])
        manifest["database_sha256" if kind == "checksum" else "version"] = "wrong"
        entries["manifest.json"] = json.dumps(manifest).encode()
    elif kind == "schema":
        empty = tmp_path / "empty.db"
        with sqlite3.connect(empty) as connection:
            connection.execute("CREATE TABLE dummy (id INTEGER)")
        entries["mangarr.db"] = empty.read_bytes()
        manifest = json.loads(entries["manifest.json"])
        manifest["database_sha256"] = hashlib.sha256(entries["mangarr.db"]).hexdigest()
        entries["manifest.json"] = json.dumps(manifest).encode()
    upload = tmp_path / "upload.zip"
    with zipfile.ZipFile(upload, "w") as z:
        for name, value in entries.items():
            z.writestr(name, value)
        if kind == "duplicate":
            z.writestr("manifest.json", entries["manifest.json"])
    with pytest.raises(ValueError):
        backups.stage(tmp_path, upload)
    assert not backups.status(tmp_path)["pending"]
    assert db.execute("SELECT value FROM settings WHERE key='test'").fetchone()[0] == "original"
    db.close()


def test_pending_restore_is_not_overwritten(tmp_path):
    db = library(tmp_path)
    result = backups.create(tmp_path)
    for n in range(2):
        upload = tmp_path / f"upload{n}.zip"
        upload.write_bytes(backups.archive_path(tmp_path, result["id"]).read_bytes())
        if n:
            with pytest.raises(ValueError, match="already scheduled"):
                backups.stage(tmp_path, upload)
        else:
            backups.stage(tmp_path, upload)
    db.close()


def old_layout(root):
    """A database from before a release added a column and a table."""
    db = library(root)
    db.execute("ALTER TABLE series DROP COLUMN metadata_refreshed_at")  # a column db.py adds on upgrade
    table = next(t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in {"settings", "series"})
    db.execute(f'DROP TABLE "{table}"')
    db.commit()
    return db, table


def test_no_premigration_backup_when_layout_is_current_or_database_is_new(tmp_path):
    assert backups.pre_migration_backup(tmp_path) is None  # fresh install: nothing to protect
    library(tmp_path).close()
    assert backups.pending_schema_changes(tmp_path / "mangarr.db") == []
    assert backups.pre_migration_backup(tmp_path) is None
    assert not list(backups.folder(tmp_path).glob("*.zip"))


def test_premigration_backup_keeps_old_layout_and_can_be_restored(tmp_path):
    db, table = old_layout(tmp_path)
    db.close()
    before = (tmp_path / "mangarr.db").read_bytes()
    result = backups.pre_migration_backup(tmp_path)
    assert (tmp_path / "mangarr.db").read_bytes() == before  # read-only: nothing migrated
    assert table in result["changes"] and "series.metadata_refreshed_at" in result["changes"]
    archive = backups.archive_path(tmp_path, result["id"])
    with zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["reason"] == "pre-migration"
        assert hashlib.sha256(z.read("mangarr.db")).hexdigest() == manifest["database_sha256"]
    assert archive.stat().st_mode & 0o777 == 0o600
    listed = backups.list_backups(tmp_path)
    assert [(b["id"], b["reason"]) for b in listed] == [(result["id"], "pre-migration")]
    # The pre-migration copy passes restore validation even though its layout is the old one.
    upload = tmp_path / "upload.zip"
    upload.write_bytes(archive.read_bytes())
    assert backups.stage(tmp_path, upload)["pending"]


def test_manual_backup_still_requires_current_layout(tmp_path):
    old_layout(tmp_path)[0].close()
    with pytest.raises(ValueError):
        backups.create(tmp_path)


def test_premigration_backups_wait_for_age_policy_instead_of_a_five_backup_cap(tmp_path, monkeypatch):
    library(tmp_path / "current").close()
    manual = backups.create(tmp_path / "current")["id"]
    old_layout(tmp_path)[0].close()
    backups.folder(tmp_path).mkdir(parents=True, exist_ok=True)
    os.replace(backups.archive_path(tmp_path / "current", manual), backups.archive_path(tmp_path, manual))
    clock = iter(range(1_800_000_000, 1_800_001_000))
    monkeypatch.setattr(backups.time, "time", lambda: next(clock))
    made = [backups.pre_migration_backup(tmp_path)["id"] for _ in range(7)]
    ids = {b["id"] for b in backups.list_backups(tmp_path)}
    assert manual in ids
    assert ids - {manual} == set(made)


def test_premigration_backup_failure_is_raised(tmp_path, monkeypatch):
    old_layout(tmp_path)[0].close()
    monkeypatch.setattr(backups, "_validate_db", lambda *a, **k: (_ for _ in ()).throw(ValueError("corrupt")))
    with pytest.raises(ValueError):
        backups.pre_migration_backup(tmp_path)
    assert not list(backups.folder(tmp_path).glob("*.zip"))
