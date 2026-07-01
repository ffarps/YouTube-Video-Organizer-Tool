from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.api.routes import router
from app.config import get_settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    conn = db.connect(settings.database_path)
    db.init_db(conn)
    app.state.db = conn
    yield
    conn.close()


def create_app() -> FastAPI:
    app = FastAPI(title="YouTube Video Organizer", version="2.0", lifespan=lifespan)
    app.include_router(router)

    index = STATIC_DIR / "index.html"
    if index.is_file():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def home():
            return FileResponse(index)

    return app


app = create_app()
