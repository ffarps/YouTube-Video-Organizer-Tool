from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api.routes import router
from app.config import get_settings


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
    return app


app = create_app()
