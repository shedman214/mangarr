"""Background permission operations with small, restart-aware progress records."""
import asyncio
import json
import os
import re
import threading
import time
import uuid

from sqlalchemy import select

from . import permissions, settings_service
from .config import config
from .db import session_scope
from .models import Chapter, RootFolder, Series

_start_lock = asyncio.Lock()
_state_lock = threading.Lock()
_tasks: set[asyncio.Task] = set()
_active: dict | None = None


def _folder():
    path = config.data_dir / "logs" / "permissions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def get_job(token: str | None = None):
    if token is None:
        pointer = _folder() / "latest-job.json"
        if not pointer.exists():
            return None
        token = json.loads(pointer.read_text(encoding="utf-8"))["id"]
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid permissions job ID")
    with _state_lock:
        if _active and _active["id"] == token:
            return dict(_active)
    path = _folder() / f"job-{token}.json"
    if not path.exists():
        raise FileNotFoundError("Permissions job not found")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["state"] == "running":
        state.update(state="interrupted", error="Mangarr restarted during this operation. Preview again before applying changes. Any partial repair can be undone.")
    return state


async def start(operation: str, token: str | None = None):
    global _active
    async with _start_lock:
        with _state_lock:
            if _active and _active["state"] == "running":
                raise ValueError("A permissions operation is already running. Its progress is shown below.")
        async with session_scope() as session:
            policy = permissions.parse(await settings_service.get_all(session))
        if not hasattr(os, "fchown"):
            raise ValueError("Media permission repair requires a Linux container.")
        state = {"id": uuid.uuid4().hex, "operation": operation, "state": "running", "phase": "Starting", "processed": 0, "total": None, "changed": 0, "skipped": 0, "started": time.time()}
        with _state_lock:
            _active = state
        _save(_folder() / f"job-{state['id']}.json", state)
        _save(_folder() / "latest-job.json", {"id": state["id"]})
        task = asyncio.create_task(_run(state, policy, token))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return dict(state)


async def _run(state, policy, token):
    last_saved = 0.0
    job_path = _folder() / f"job-{state['id']}.json"
    candidates_path = _folder() / f"candidates-{state['id']}.jsonl"

    def update(values, force=False):
        nonlocal last_saved
        with _state_lock:
            force = force or bool(values.get("undo_id") and values["undo_id"] != state.get("undo_id"))
            state.update(values)
            now = time.monotonic()
            if force or now - last_saved >= 1:
                _save(job_path, state)
                last_saved = now

    try:
        if state["operation"] == "preview":
            update({"phase": "Listing library"}, True)
            # Spool distinct paths in batches, then close the DB transaction
            # before filesystem work. No file-count cap or whole-library list.
            async with session_scope() as session:
                result = await session.stream(select(Chapter.file_path, RootFolder.path)
                    .join(Series, Chapter.series_id == Series.id)
                    .join(RootFolder, Series.root_folder_id == RootFolder.id)
                    .where(Chapter.downloaded == True, Chapter.excluded == False,
                           Chapter.file_source != "", Chapter.file_path != "").distinct())
                total = 0
                def append(rows):
                    with candidates_path.open("a", encoding="utf-8") as handle:
                        for path, root in rows:
                            handle.write(json.dumps([path, root]) + "\n")
                async for batch in result.partitions(1000):
                    await asyncio.to_thread(append, batch)
                    total += len(batch)
                    update({"listed": total})
                await result.close()
            update({"phase": "Checking files", "total": total}, True)
            def candidates():
                if candidates_path.exists():
                    with candidates_path.open(encoding="utf-8") as handle:
                        for line in handle:
                            yield tuple(json.loads(line))
            result = await asyncio.to_thread(permissions.preview, candidates(), config.data_dir, progress=update, policy=policy)
        else:
            update({"phase": "Restoring permissions" if state["operation"] == "undo" else "Fixing permissions"}, True)
            result = await asyncio.to_thread(permissions.repair, config.data_dir, token, undo=state["operation"] == "undo", progress=update)
        update({"state": "complete", "phase": "Complete", "result": result, "finished": time.time()}, True)
    except Exception as exc:
        update({"state": "failed", "phase": "Failed", "error": str(exc), "finished": time.time()}, True)
    finally:
        candidates_path.unlink(missing_ok=True)
