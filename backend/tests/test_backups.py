import hashlib
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
