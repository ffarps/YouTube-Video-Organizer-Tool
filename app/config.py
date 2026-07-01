from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration, read from environment / .env (never committed)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Google Cloud API key for the YouTube Data API. Optional: without it,
    # ingestion falls back to yt-dlp (slower, keyless).
    youtube_api_key: Optional[str] = None

    # Path to the SQLite database file.
    database_path: str = "organizer.db"

    # Browser to read cookies from for yt-dlp, e.g. "firefox" or "chrome".
    # Required only for private playlists such as Watch Later.
    ytdlp_cookies_browser: Optional[str] = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
