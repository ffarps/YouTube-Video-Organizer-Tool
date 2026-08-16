import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db, downloads, logs
from app.api.routes import router
from app.config import get_settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

log = logging.getLogger("watchlog")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.media_dir().mkdir(parents=True, exist_ok=True)
    logs.setup(settings.log_dir())
    log.info("starting: db=%s media=%s", settings.database_path, settings.media_dir())
    conn = db.connect(settings.database_path)
    db.init_db(conn)
    # Downloads can't survive a restart, so anything still marked in-flight is
    # stale and would otherwise spin forever in the UI.
    stale = downloads.reset_stale(conn)
    if stale:
        log.warning("marked %d interrupted download(s) as failed", stale)
    app.state.db = conn
    yield
    log.info("shutting down")
    conn.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Watchlog", version="2.1", lifespan=lifespan)
    app.include_router(router)

    # Which request died matters as much as the traceback: without the path
    # and method, a 500 in the log is a puzzle. Re-raised so the response and
    # uvicorn's own handling are unchanged.
    @app.exception_handler(Exception)
    async def log_unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        raise exc

    # Serve downloaded files through StaticFiles rather than a FileResponse
    # endpoint: it answers HTTP Range requests, which is what lets you scrub
    # the timeline of a local <video> instead of only playing it start to end.
    # check_dir=False because this module is imported (by tests, by uvicorn's
    # reloader) far more often than it is served — the folder is created in the
    # lifespan, when the app is actually starting up.
    app.mount(
        "/media",
        StaticFiles(directory=get_settings().media_dir(), check_dir=False),
        name="media",
    )

    index = STATIC_DIR / "index.html"
    if index.is_file():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def home():
            # no-cache, not no-store: keep caching it, but revalidate every
            # time — an unchanged page is a cheap 304. Without a freshness
            # directive (FileResponse sends only last-modified and etag) the
            # webview falls back to heuristic caching, a fraction of the file's
            # age, and serves the old page for hours after an edit. The desktop
            # window keeps its profile between runs, so that stale copy
            # outlives a restart: the app relaunches looking unchanged, and the
            # frontend is the whole UI.
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    favicon = STATIC_DIR / "favicon.ico"
    if favicon.is_file():

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon_file():
            return FileResponse(favicon)

    return app


app = create_app()
