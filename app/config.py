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

    # yt-dlp player client(s) for downloads, comma-separated, e.g. "android"
    # or "android,web". None leaves yt-dlp's own default. YouTube now gates
    # the default client's stream URLs behind a proof-of-origin token: without
    # cookies (or a PO-token plugin) the metadata still resolves but every
    # stream 403s. `android` needs neither and downloads fine, at the cost of
    # only being offered up to 360p on most videos — which is why this is a
    # knob and not a silent fallback. Set it when YouTube changes its mind
    # again; the working client rotates.
    ytdlp_player_client: Optional[str] = None

    # Where on-demand downloads are written, one file per video id.
    media_path: str = "media"

    # Where the rotating log file goes. Kept out of the media folder so a
    # crash report is never mistaken for something you downloaded.
    log_path: str = "logs"

    # Default ceiling for downloads: 2160 = 4K, 1440 = 2K, then 1080/720/...
    # Anything above 360 needs ffmpeg on PATH, because YouTube only serves
    # taller streams with the audio split into a separate file.
    download_max_height: int = 1440

    def media_dir(self) -> Path:
        """Absolute media folder. Resolved so the static mount and the
        delete path agree on what counts as 'inside' it."""
        return Path(self.media_path).resolve()

    def log_dir(self) -> Path:
        """Absolute log folder."""
        return Path(self.log_path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
