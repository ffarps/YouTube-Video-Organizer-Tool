from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db, downloads
from app.api.routes import router
from app.config import get_settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.media_dir().mkdir(parents=True, exist_ok=True)
    conn = db.connect(settings.database_path)
    db.init_db(conn)
    # Downloads can't survive a restart, so anything still marked in-flight is
    # stale and would otherwise spin forever in the UI.
    downloads.reset_stale(conn)
    app.state.db = conn
    yield
    conn.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Watchlog", version="2.1", lifespan=lifespan)
    app.include_router(router)

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
            return FileResponse(index)

    favicon = STATIC_DIR / "favicon.ico"
    if favicon.is_file():

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon_file():
            return FileResponse(favicon)

    return app


app = create_app()
