"""Archive one video's actual media file — the only place the app writes
video bytes to disk.

Everything else under `ingest` is metadata-only (`skip_download: True`); this
is opt-in, one video at a time, for watching offline. Deliberately no bulk
path: pulling a whole backlog is both a terms-of-service problem and a disk
problem, so the caller can only ever name a single id.

YouTube serves anything above 360p as *separate* video and audio streams that
have to be muxed back together, which needs ffmpeg. Without it the format
selector degrades to the progressive (single-file) stream rather than failing,
so the feature still works on a bare machine — just at 360p.
"""
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Callable, List, Optional

import yt_dlp


class DownloadError(Exception):
    pass


# Offered by the UI, highest first. 2160/1440 are the reason ffmpeg matters.
QUALITY_CHOICES: List[int] = [2160, 1440, 1080, 720, 480, 360]

# The tallest progressive (muxed) stream YouTube still publishes. Everything
# above this is adaptive-only, hence unreachable without a merger.
NO_FFMPEG_MAX_HEIGHT = 360


def cookie_spec(spec: Optional[str]) -> Optional[tuple]:
    """yt-dlp's `cookiesfrombrowser` tuple from a `browser[:profile]` string.

    The profile half matters for Firefox forks: yt-dlp only knows a fixed list
    of browser names, so Zen (and LibreWolf, and the rest) are unreachable by
    name — but they are Firefox underneath, with the same unencrypted
    `cookies.sqlite`, so naming `firefox` and pointing at the profile
    directory reads them correctly. Split once from the left, because a
    Windows profile path carries its own colon after the drive letter.
    """
    if not spec:
        return None
    name, _, profile = spec.partition(":")
    name = name.strip()
    profile = profile.strip()
    return (name, profile) if profile else (name,)


@lru_cache(maxsize=1)
def js_runtime() -> Optional[str]:
    """A JavaScript engine for yt-dlp, or None if the machine has none.

    YouTube's stream URLs carry a signature that has to be put back through
    their own player JS before the URL is valid, so yt-dlp needs an engine to
    run it. Without one it falls back to a path it now calls deprecated and
    hands back URLs the CDN refuses — a 403 on every adaptive stream, which
    looks exactly like the proof-of-origin refusal and is not the same thing.
    Only `deno` is enabled upstream by default, so an installed Node sits
    unused unless it is named. Detected like ffmpeg, and cached for the same
    reason: a runtime does not appear mid-session. The solver script yt-dlp
    runs in it comes from the `yt-dlp-ejs` dependency.
    """
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return name
    return None


@lru_cache(maxsize=1)
def ffmpeg_available() -> bool:
    """Cached: a missing ffmpeg won't appear mid-session, and this is checked
    on every status poll."""
    return shutil.which("ffmpeg") is not None


# Resolution decides first; H.264 only breaks ties *at the same height*.
# Expressing the codec preference in the format selector instead made "4K"
# quietly resolve to 1080p: a 1080p H.264 stream matched the preferred branch
# before any taller VP9/AV1 stream was ever considered.
FORMAT_SORT = ["res", "vcodec:h264", "acodec:m4a"]


# Clients YouTube only offers the progressive stream to. `android` is the way
# past the proof-of-origin gate without cookies, and it pays for that in
# resolution: every video comes back with a 360p ceiling. Named here so the UI
# can say so up front rather than accepting a request for 1080p and handing
# back 360p — the same promise `ffmpeg_available` exists to keep.
CAPPED_PLAYER_CLIENTS = {"android"}


def client_ceiling(player_client: Optional[str]) -> Optional[int]:
    """The ceiling the configured player client imposes, if any."""
    if not player_client:
        return None
    first = next((c.strip() for c in player_client.split(",") if c.strip()), None)
    return NO_FFMPEG_MAX_HEIGHT if first in CAPPED_PLAYER_CLIENTS else None


def effective_height(
    max_height: Optional[int], player_client: Optional[str] = None
) -> Optional[int]:
    """The height actually deliverable here, so the UI can warn before the
    user picks 4K and silently gets 360p.

    `max_height=None` means "whatever this video has"; it stays None unless
    something caps it — a missing ffmpeg, or a player client that is only
    offered the progressive stream.
    """
    caps = [c for c in (
        None if ffmpeg_available() else NO_FFMPEG_MAX_HEIGHT,
        client_ceiling(player_client),
    ) if c is not None]
    if not caps:
        return max_height
    if max_height is None:
        return min(caps)
    return min(max_height, *caps)


def _format_selector(max_height: Optional[int], audio_only: bool) -> str:
    """Which streams to take. `max_height=None` puts no ceiling on it.

    Only resolution is constrained here — codec preference belongs in
    FORMAT_SORT, where it can rank alternatives instead of excluding them.
    """
    if audio_only:
        # m4a lands as-is. Converting to mp3 would need ffmpeg and re-encode
        # something already lossy, for a format nothing here requires.
        return "bestaudio[ext=m4a]/bestaudio"
    if not ffmpeg_available():
        return (
            f"b[height<={NO_FFMPEG_MAX_HEIGHT}][vcodec!=none][acodec!=none]"
            f"/b[vcodec!=none][acodec!=none]"
        )
    if max_height is None:
        return "bv*+ba/b"
    return f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"


class _ProgressBridge:
    """Turns yt-dlp's per-stream byte counts into one forward-only figure.

    yt-dlp reports the video stream and the audio stream as two separate
    downloads that each run 0->100%. A single bar is what the UI wants, so
    finished streams are banked and added underneath the one in flight. The
    total is an estimate that grows as streams complete — approximate, but it
    never goes backwards, which is the property a progress bar needs.
    """

    def __init__(self, report: Callable[[dict], None]):
        self.report = report
        self.banked = 0

    def __call__(self, event: dict) -> None:
        status = event.get("status")
        if status == "downloading":
            done = event.get("downloaded_bytes") or 0
            total = (
                event.get("total_bytes") or event.get("total_bytes_estimate") or 0
            )
            self.report({
                "stage": "downloading",
                "done": self.banked + done,
                "total": (self.banked + total) if total else 0,
                "speed": event.get("speed"),
                "eta": event.get("eta"),
            })
        elif status == "finished":
            self.banked += (
                event.get("total_bytes") or event.get("downloaded_bytes") or 0
            )
            # Either the audio stream is next or ffmpeg is about to mux; both
            # are silent stretches, so say so instead of parking at 99%.
            self.report({"stage": "processing", "done": self.banked, "total": 0})


def _explain(message: str, cookies_browser, player_client) -> str:
    """Say what to do about a 403, because the raw message doesn't.

    A 403 on the stream URL is no longer the occasional bad luck that
    `extractor_retries` was there to ride out: YouTube gates its default
    client's streams behind a proof-of-origin token, so metadata resolves,
    every stream is refused, and retrying re-resolves URLs that are refused
    the same way. Nothing in the app can fix that by itself — the two ways
    through are a signed-in cookie jar or a client that isn't gated — so the
    error has to name them rather than surfacing "HTTP Error 403: Forbidden"
    to someone who has no reason to connect it to a setting.
    """
    if "403" not in message:
        return message
    if cookies_browser or player_client:
        return (
            f"{message}\n\nYouTube refused the stream even with "
            f"cookies_browser={cookies_browser!r} player_client={player_client!r}. "
            "The working player client rotates — try another (android, web, tv) "
            "in YTDLP_PLAYER_CLIENT, or point YTDLP_COOKIES_BROWSER at a browser "
            "signed in to YouTube."
        )
    return (
        f"{message}\n\nYouTube refused the stream. It now wants a "
        "proof-of-origin token for the default player client, so metadata "
        "resolves but no stream downloads. Either set YTDLP_COOKIES_BROWSER "
        "in .env to a browser signed in to YouTube (keeps full quality), or "
        "set YTDLP_PLAYER_CLIENT=android (no cookies, usually 360p only)."
    )


def download_video(
    video_id: str,
    media_dir: Path,
    max_height: Optional[int] = 1440,
    audio_only: bool = False,
    cookies_browser: Optional[str] = None,
    player_client: Optional[str] = None,
    progress: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Pull one video into `media_dir`; returns {filename, size_bytes, height}.

    `max_height=None` takes the best the video actually offers, which is the
    only way to ask for "4K if it exists, 1080p if that's all there is".

    `progress` receives {"stage", "done", "total", ...} events in the same
    shape sync.py already reports for metadata fetches, so the UI's existing
    progress handling applies unchanged.
    """
    media_dir.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda event: None)

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Name by video id, not title: titles collide, contain characters
        # Windows rejects, and change when a creator edits them.
        "outtmpl": str(media_dir / f"{video_id}.%(ext)s"),
        "format": _format_selector(max_height, audio_only),
        "format_sort": FORMAT_SORT,
        "progress_hooks": [_ProgressBridge(report)],
        # A half-written file from an interrupted run should be replaced.
        # Resuming into it risks a corrupt container that plays for 20s.
        "continuedl": False,
        "overwrites": True,
        # YouTube hands out 403s on the adaptive (above-360p) streams fairly
        # often. `extractor_retries` is the one that helps: it re-resolves
        # fresh stream URLs, where a plain retry just replays the dead one.
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 3,
    }
    runtime = js_runtime()
    if runtime:
        opts["js_runtimes"] = {runtime: {}}
    if not audio_only and ffmpeg_available():
        opts["merge_output_format"] = "mp4"
    if cookies_browser:
        opts["cookiesfrombrowser"] = cookie_spec(cookies_browser)
    if player_client:
        opts["extractor_args"] = {
            "youtube": {"player_client": [c.strip() for c in player_client.split(",") if c.strip()]}
        }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=True
            )
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_explain(str(e), cookies_browser, player_client)) from e

    requested = (info or {}).get("requested_downloads") or []
    filepath = requested[0].get("filepath") if requested else None
    if not filepath:
        raise DownloadError(f"yt-dlp reported no output file for {video_id}")

    path = Path(filepath)
    if not path.is_file():
        raise DownloadError(f"Expected {path.name} on disk but it is missing")

    return {
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        # None for audio-only; the UI uses it to show what quality landed,
        # which may be below what was asked for if YouTube had nothing taller.
        "height": None if audio_only else info.get("height"),
    }
