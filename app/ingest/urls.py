"""Canonicalize YouTube URLs: extract video ids, playlist ids, and source kind."""
import re
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Playlist ids: WL (Watch Later), LL (Liked), or PL/UU/OL/FL/RD + suffix
PLAYLIST_ID_RE = re.compile(r"^(WL|LL|(?:PL|UU|OL|FL|RD)[A-Za-z0-9_-]+)$")


def video_id_from_url(url: str) -> Optional[str]:
    """Extract the canonical 11-char video id from any YouTube URL form.

    Handles: watch?v=, youtu.be/, /shorts/, /embed/, /live/, and bare ids.
    Returns None if no video id is present.
    """
    url = url.strip()
    if VIDEO_ID_RE.match(url):
        return url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host not in ("youtube.com", "youtu.be", "youtube-nocookie.com", "music.youtube.com"):
        return None

    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if VIDEO_ID_RE.match(candidate) else None

    qs = parse_qs(parsed.query)
    if "v" in qs and qs["v"] and VIDEO_ID_RE.match(qs["v"][0]):
        return qs["v"][0]

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
        return parts[1] if VIDEO_ID_RE.match(parts[1]) else None
    return None


# URLs inside free-form text; stops at whitespace and markdown/punctuation
# delimiters so "[url](url)" and "url, url" both split cleanly.
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\]\)\}>\"',;]+")


def extract_video_ids(text: str) -> Tuple[List[str], List[str]]:
    """Pull every recognizable video id out of pasted free-form text.

    Accepts any separator style (newlines, commas, spaces) and URLs wrapped
    in markdown brackets; bare 11-char ids also work. Returns
    (unique ids in first-seen order, unrecognized url-like entries)."""
    ids: List[str] = []
    seen = set()
    invalid: List[str] = []

    for url in _URL_IN_TEXT_RE.findall(text):
        vid = video_id_from_url(url)
        if vid is None:
            invalid.append(url)
        elif vid not in seen:
            seen.add(vid)
            ids.append(vid)

    # whatever remains after removing URLs: bare ids and junk
    for token in re.split(r"[\s,;]+", _URL_IN_TEXT_RE.sub(" ", text)):
        if VIDEO_ID_RE.match(token):
            if token not in seen:
                seen.add(token)
                ids.append(token)
        elif "youtu" in token.lower():  # a mangled link; anything else is noise
            invalid.append(token)
    return ids, invalid


def playlist_id_from_url(url: str) -> Optional[str]:
    """Extract a playlist id from a URL (or accept a bare playlist id)."""
    url = url.strip()
    if PLAYLIST_ID_RE.match(url):
        return url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "list" in qs and qs["list"]:
        candidate = qs["list"][0]
        return candidate if PLAYLIST_ID_RE.match(candidate) else None
    return None


def channel_ref_from_url(url: str) -> Optional[dict]:
    """Detect a channel URL. Returns {'channel_id': ...} or {'handle': ...}."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host not in ("youtube.com", "music.youtube.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None
    if parts[0] == "channel" and len(parts) >= 2:
        return {"channel_id": parts[1]}
    if parts[0].startswith("@"):
        return {"handle": parts[0]}
    return None


def classify_url(url: str) -> dict:
    """Classify a URL into {'kind': ..., ...ref fields}.

    kind is one of: watch_later, playlist, channel, video, unknown.
    A watch URL that also carries a list= param is treated as its playlist.
    """
    playlist_id = playlist_id_from_url(url)
    if playlist_id == "WL":
        return {"kind": "watch_later", "playlist_id": "WL"}
    if playlist_id:
        return {"kind": "playlist", "playlist_id": playlist_id}
    channel = channel_ref_from_url(url)
    if channel:
        return {"kind": "channel", **channel}
    video_id = video_id_from_url(url)
    if video_id:
        return {"kind": "video", "video_id": video_id}
    return {"kind": "unknown"}
