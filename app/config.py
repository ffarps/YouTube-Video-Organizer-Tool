from functools import lru_cache
from pathlib import Path
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

    # Where on-demand downloads are written, one file per video id.
    media_path: str = "media"

    # Default ceiling for downloads: 2160 = 4K, 1440 = 2K, then 1080/720/...
    # Anything above 360 needs ffmpeg on PATH, because YouTube only serves
    # taller streams with the audio split into a separate file.
    download_max_height: int = 1440

    def media_dir(self) -> Path:
        """Absolute media folder. Resolved so the static mount and the
        delete path agree on what counts as 'inside' it."""
        return Path(self.media_path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
