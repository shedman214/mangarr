import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import (
    auth as auth_routes,
    logs as log_routes,
    backups,
    apikeys,
    calendar,
    import_lists,
    library,
    library_import,
    queue,
    search,
    series,
    settings,
    system,
)
from .api.deps import get_api_key, require_api_key
from .config import config
from .db import init_db, session_scope
from .jobs import scheduler

from . import logs

# Console only at import; the log file is added at startup (see lifespan).
logs.setup(None, logs.environment_level(config.log_level))
log = logging.getLogger("mangarr")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from .backups import pre_migration_backup, restore_pending
    logs.setup(config.data_dir, logs.environment_level(config.log_level))
    await asyncio.to_thread(restore_pending, config.data_dir)
    if config.premigration_backup:
        # Refuses to start (and so to migrate) if the safety copy fails; MANGARR_PREMIGRATION_BACKUP=false skips it.
        saved = await asyncio.to_thread(pre_migration_backup, config.data_dir)
        if saved:
            log.info("Saved pre-migration backup %s before updating the database (%d change(s))", saved["id"], len(saved["changes"]))
    await init_db()
    get_api_key()  # load (or create) the browser key so logs can mask it
    from . import settings_service
    async with session_scope() as session:
        await settings_service.get_all(session)
        from .api.logs import apply_saved_level
        await apply_saved_level(session)
    await scheduler.start()
    log.info("Mangarr %s ready on %s:%d", __version__, config.host, config.port)
    yield
    scheduler.shutdown()


app = FastAPI(title="Mangarr", version=__version__, lifespan=lifespan)

api = FastAPI(dependencies=[Depends(require_api_key)])
api.include_router(series.router)
api.include_router(library_import.router)
api.include_router(library.router)
api.include_router(calendar.router)
api.include_router(import_lists.router)
api.include_router(search.router)
api.include_router(queue.router)
api.include_router(settings.router)
api.include_router(backups.router)
api.include_router(auth_routes.router)
api.include_router(log_routes.router)
api.include_router(apikeys.router)
api.include_router(system.router)
app.mount("/api/v1", api)
app.include_router(auth_routes.public)


@app.get("/initialize.json")
async def initialize(request: Request):
    """Bootstrap info for the web UI, mirroring how the *arr apps hand their
    UI the API key. Without the optional login (Settings → Login) this is
    readable by anyone who can reach the port; with it, only by a browser
    with a valid session."""
    from . import auth
    if auth.enabled() and not auth.valid_session(request.cookies.get(auth.COOKIE)):
        # With login on, the key (and so the API) is only handed to a signed-in browser.
        return JSONResponse({"detail": "Login required", "loginRequired": True, "version": __version__}, status_code=401, headers={"Cache-Control": "no-store"})
    return JSONResponse({"apiKey": get_api_key(), "version": __version__, "urlBase": "", "login": auth.enabled()}, headers={"Cache-Control": "no-store"})


@app.get("/ping")
async def ping():
    """Liveness probe for the Docker HEALTHCHECK and uptime monitors, like
    the *arr apps' /ping. Public on purpose (it sits outside /api/v1, so no
    X-Api-Key) and deliberately cheap: no database, no sources, no secrets."""
    return {"status": "OK"}


# Serve the built frontend if present (production/Docker)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class _HashedAssets(StaticFiles):
    """Built assets carry a content hash in their name, so they never change."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# index.html names the current assets: browsers must revalidate it, or they keep
# showing the previous version's interface after an update.
_REVALIDATE = {"Cache-Control": "no-cache"}


def mount_frontend(target: FastAPI, static_dir: Path) -> None:
    target.mount("/assets", _HashedAssets(directory=static_dir / "assets"), name="assets")

    @target.get("/{full_path:path}")
    async def spa(full_path: str):
        if full_path:
            # resolve + containment check: the route param is percent-decoded,
            # so "..%2f" sequences would otherwise escape the static dir
            candidate = (static_dir / full_path).resolve()
            if candidate.is_relative_to(static_dir) and candidate.is_file():
                return FileResponse(candidate, headers=_REVALIDATE)
        return FileResponse(static_dir / "index.html", headers=_REVALIDATE)


if STATIC_DIR.is_dir():
    mount_frontend(app, STATIC_DIR)
else:

    @app.get("/")
    async def root():
        return JSONResponse({"app": "mangarr", "version": __version__, "ui": "not built"})


def run() -> None:
    import uvicorn

    # log_config=None: uvicorn's loggers use Mangarr's redacting handlers and levels.
    uvicorn.run("mangarr.main:app", host=config.host, port=config.port, log_config=None)


if __name__ == "__main__":
    run()
