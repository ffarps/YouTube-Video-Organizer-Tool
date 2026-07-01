import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Set

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id            TEXT PRIMARY KEY,   -- 11-char YouTube video id
    title         TEXT NOT NULL,
    description   TEXT,
    channel_id    TEXT,
    channel_title TEXT,
    duration_sec  INTEGER,
    published_at  TEXT,               -- ISO 8601
    thumbnail_url TEXT,
    tags          TEXT,               -- JSON array
    view_count    INTEGER,
    source        TEXT NOT NULL DEFAULT 'api',
    added_at      TEXT NOT NULL,
    embedding     BLOB                -- filled in Phase 2
);

CREATE TABLE IF NOT EXISTS themes (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'manual'   -- manual | discovered
);

CREATE TABLE IF NOT EXISTS video_themes (
    video_id   TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    theme_id   INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    confidence REAL NOT NULL DEFAULT 1.0,
    source     TEXT NOT NULL DEFAULT 'manual',  -- rule | embedding | manual
    PRIMARY KEY (video_id, theme_id)
);

CREATE TABLE IF NOT EXISTS watch_state (
    video_id   TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'unwatched',  -- unwatched | watched | skipped
    rating     INTEGER,
    watched_at TEXT
);

CREATE TABLE IF NOT EXISTS playlists (
    id             TEXT PRIMARY KEY,
    title          TEXT,
    kind           TEXT NOT NULL DEFAULT 'playlist',  -- playlist | channel | watch_later
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    video_id    TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    position    INTEGER,
    PRIMARY KEY (playlist_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_video_themes_theme ON video_themes(theme_id);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --- videos ---------------------------------------------------------------

def upsert_video(conn: sqlite3.Connection, video: dict) -> bool:
    """Insert or update a video. Returns True if the video was new."""
    existed = conn.execute(
        "SELECT 1 FROM videos WHERE id = ?", (video["id"],)
    ).fetchone()
    conn.execute(
        """
        INSERT INTO videos (id, title, description, channel_id, channel_title,
                            duration_sec, published_at, thumbnail_url, tags,
                            view_count, source, added_at)
        VALUES (:id, :title, :description, :channel_id, :channel_title,
                :duration_sec, :published_at, :thumbnail_url, :tags,
                :view_count, :source, :added_at)
        ON CONFLICT(id) DO UPDATE SET
            title         = excluded.title,
            description   = COALESCE(excluded.description, videos.description),
            channel_id    = COALESCE(excluded.channel_id, videos.channel_id),
            channel_title = COALESCE(excluded.channel_title, videos.channel_title),
            duration_sec  = COALESCE(excluded.duration_sec, videos.duration_sec),
            published_at  = COALESCE(excluded.published_at, videos.published_at),
            thumbnail_url = COALESCE(excluded.thumbnail_url, videos.thumbnail_url),
            tags          = COALESCE(excluded.tags, videos.tags),
            view_count    = COALESCE(excluded.view_count, videos.view_count)
        """,
        {
            "id": video["id"],
            "title": video.get("title") or "",
            "description": video.get("description"),
            "channel_id": video.get("channel_id"),
            "channel_title": video.get("channel_title"),
            "duration_sec": video.get("duration_sec"),
            "published_at": video.get("published_at"),
            "thumbnail_url": video.get("thumbnail_url"),
            "tags": json.dumps(video.get("tags") or []),
            "view_count": video.get("view_count"),
            "source": video.get("source", "api"),
            "added_at": video.get("added_at") or now_iso(),
        },
    )
    conn.execute(
        "INSERT OR IGNORE INTO watch_state (video_id) VALUES (?)", (video["id"],)
    )
    return existed is None


def get_video(conn: sqlite3.Connection, video_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT v.*, w.status AS watch_status, w.rating
        FROM videos v LEFT JOIN watch_state w ON w.video_id = v.id
        WHERE v.id = ?
        """,
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    video = _row_to_video(row)
    video["themes"] = [
        r["name"]
        for r in conn.execute(
            """
            SELECT t.name FROM themes t
            JOIN video_themes vt ON vt.theme_id = t.id
            WHERE vt.video_id = ? ORDER BY vt.confidence DESC
            """,
            (video_id,),
        )
    ]
    return video


def delete_video(conn: sqlite3.Connection, video_id: str) -> bool:
    cur = conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    return cur.rowcount > 0


def existing_video_ids(conn: sqlite3.Connection, ids: Iterable[str]) -> Set[str]:
    ids = list(ids)
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id FROM videos WHERE id IN ({placeholders})", ids
    ).fetchall()
    return {r["id"] for r in rows}


def _row_to_video(row: sqlite3.Row) -> dict:
    video = dict(row)
    video["tags"] = json.loads(video.get("tags") or "[]")
    video.pop("embedding", None)
    video["watch_status"] = video.get("watch_status") or "unwatched"
    return video


# --- themes ---------------------------------------------------------------

def get_or_create_theme(
    conn: sqlite3.Connection, name: str, kind: str = "manual"
) -> int:
    row = conn.execute("SELECT id FROM themes WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO themes (name, kind) VALUES (?, ?)", (name, kind)
    )
    return cur.lastrowid


def list_themes(conn: sqlite3.Connection) -> List[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT t.id, t.name, t.kind, COUNT(vt.video_id) AS video_count
            FROM themes t LEFT JOIN video_themes vt ON vt.theme_id = t.id
            GROUP BY t.id ORDER BY t.name
            """
        )
    ]


def assign_theme(
    conn: sqlite3.Connection,
    video_id: str,
    theme_id: int,
    confidence: float = 1.0,
    source: str = "manual",
) -> None:
    conn.execute(
        """
        INSERT INTO video_themes (video_id, theme_id, confidence, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(video_id, theme_id) DO UPDATE SET
            confidence = excluded.confidence, source = excluded.source
        """,
        (video_id, theme_id, confidence, source),
    )


def videos_by_theme(
    conn: sqlite3.Connection,
    theme_name: str,
    watched: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> Optional[List[dict]]:
    theme = conn.execute(
        "SELECT id FROM themes WHERE name = ?", (theme_name,)
    ).fetchone()
    if theme is None:
        return None
    query = """
        SELECT v.*, w.status AS watch_status, w.rating
        FROM videos v
        JOIN video_themes vt ON vt.video_id = v.id AND vt.theme_id = ?
        LEFT JOIN watch_state w ON w.video_id = v.id
    """
    params: list = [theme["id"]]
    if watched is True:
        query += " WHERE w.status = 'watched'"
    elif watched is False:
        query += " WHERE COALESCE(w.status, 'unwatched') != 'watched'"
    query += " ORDER BY v.published_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row_to_video(r) for r in conn.execute(query, params)]


def remove_theme_assignment(
    conn: sqlite3.Connection, video_id: str, theme_name: str
) -> bool:
    cur = conn.execute(
        """
        DELETE FROM video_themes
        WHERE video_id = ? AND theme_id = (SELECT id FROM themes WHERE name = ?)
        """,
        (video_id, theme_name),
    )
    return cur.rowcount > 0


# --- embeddings -------------------------------------------------------------

def videos_missing_embedding(conn: sqlite3.Connection, limit: int = 500) -> List[dict]:
    return [
        _row_to_video(r)
        for r in conn.execute(
            """
            SELECT v.*, NULL AS watch_status, NULL AS rating FROM videos v
            WHERE v.embedding IS NULL LIMIT ?
            """,
            (limit,),
        )
    ]


def save_embedding(conn: sqlite3.Connection, video_id: str, blob: bytes) -> None:
    conn.execute("UPDATE videos SET embedding = ? WHERE id = ?", (blob, video_id))


def embedding_counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded
        FROM videos
        """
    ).fetchone()
    return {"total": row["total"], "embedded": row["embedded"] or 0}


def theme_member_embeddings(conn: sqlite3.Connection) -> List[dict]:
    """Embeddings of confirmed theme members (manual + rule assignments),
    the raw material for theme prototypes."""
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT t.name AS theme, v.embedding
            FROM video_themes vt
            JOIN themes t ON t.id = vt.theme_id
            JOIN videos v ON v.id = vt.video_id
            WHERE vt.source IN ('manual', 'rule') AND v.embedding IS NOT NULL
            """
        )
    ]


def videos_without_themes(
    conn: sqlite3.Connection, with_embedding_only: bool = False, limit: int = 200
) -> List[dict]:
    query = """
        SELECT v.*, w.status AS watch_status, w.rating
        FROM videos v
        LEFT JOIN watch_state w ON w.video_id = v.id
        WHERE NOT EXISTS (SELECT 1 FROM video_themes vt WHERE vt.video_id = v.id)
    """
    if with_embedding_only:
        query += " AND v.embedding IS NOT NULL"
    query += " ORDER BY v.added_at DESC LIMIT ?"
    rows = conn.execute(query, (limit,)).fetchall()
    videos = []
    for row in rows:
        video = dict(row)
        video["tags"] = json.loads(video.get("tags") or "[]")
        video["watch_status"] = video.get("watch_status") or "unwatched"
        videos.append(video)  # keeps the embedding blob for suggestion scoring
    return videos


def watched_with_embeddings(conn: sqlite3.Connection) -> List[dict]:
    """Videos with an embedding and a non-default watch state — the
    recommender's training signal."""
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT v.id, v.embedding, w.status, w.rating, w.watched_at
            FROM videos v
            JOIN watch_state w ON w.video_id = v.id
            WHERE v.embedding IS NOT NULL AND w.status != 'unwatched'
            """
        )
    ]


def unwatched_candidates(
    conn: sqlite3.Connection,
    theme: Optional[str] = None,
    max_duration_sec: Optional[int] = None,
) -> List[dict]:
    query = """
        SELECT v.*, w.status AS watch_status, w.rating
        FROM videos v
        LEFT JOIN watch_state w ON w.video_id = v.id
        WHERE COALESCE(w.status, 'unwatched') = 'unwatched'
    """
    params: list = []
    if theme:
        query += """
            AND EXISTS (
                SELECT 1 FROM video_themes vt JOIN themes t ON t.id = vt.theme_id
                WHERE vt.video_id = v.id AND t.name = ?
            )
        """
        params.append(theme)
    if max_duration_sec:
        query += " AND v.duration_sec IS NOT NULL AND v.duration_sec <= ?"
        params.append(max_duration_sec)
    rows = conn.execute(query, params).fetchall()
    videos = []
    for row in rows:
        video = dict(row)
        video["tags"] = json.loads(video.get("tags") or "[]")
        video["watch_status"] = video.get("watch_status") or "unwatched"
        videos.append(video)  # embedding blob kept for scoring
    return videos


def theme_affinity(conn: sqlite3.Connection) -> dict:
    """theme name -> count of watched videos in it (cold-start signal)."""
    return {
        r["name"]: r["n"]
        for r in conn.execute(
            """
            SELECT t.name, COUNT(*) AS n
            FROM watch_state w
            JOIN video_themes vt ON vt.video_id = w.video_id
            JOIN themes t ON t.id = vt.theme_id
            WHERE w.status = 'watched'
            GROUP BY t.name
            """
        )
    }


def themes_for_videos(conn: sqlite3.Connection, video_ids: List[str]) -> dict:
    """video_id -> [theme names]; batch lookup for listings."""
    if not video_ids:
        return {}
    placeholders = ",".join("?" * len(video_ids))
    mapping: dict = {vid: [] for vid in video_ids}
    for r in conn.execute(
        f"""
        SELECT vt.video_id, t.name FROM video_themes vt
        JOIN themes t ON t.id = vt.theme_id
        WHERE vt.video_id IN ({placeholders})
        ORDER BY vt.confidence DESC
        """,
        video_ids,
    ):
        mapping[r["video_id"]].append(r["name"])
    return mapping


# --- watch state ----------------------------------------------------------

def set_watch_state(
    conn: sqlite3.Connection,
    video_id: str,
    status: Optional[str] = None,
    rating: Optional[int] = None,
) -> bool:
    if conn.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
        return False
    conn.execute(
        "INSERT OR IGNORE INTO watch_state (video_id) VALUES (?)", (video_id,)
    )
    if status is not None:
        watched_at = now_iso() if status == "watched" else None
        conn.execute(
            "UPDATE watch_state SET status = ?, watched_at = ? WHERE video_id = ?",
            (status, watched_at, video_id),
        )
    if rating is not None:
        conn.execute(
            "UPDATE watch_state SET rating = ? WHERE video_id = ?",
            (rating, video_id),
        )
    return True


# --- playlists ------------------------------------------------------------

def upsert_playlist(
    conn: sqlite3.Connection, playlist_id: str, title: Optional[str], kind: str
) -> None:
    conn.execute(
        """
        INSERT INTO playlists (id, title, kind, last_synced_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = COALESCE(excluded.title, playlists.title),
            last_synced_at = excluded.last_synced_at
        """,
        (playlist_id, title, kind, now_iso()),
    )


def upsert_playlist_item(
    conn: sqlite3.Connection, playlist_id: str, video_id: str, position: Optional[int]
) -> None:
    conn.execute(
        """
        INSERT INTO playlist_items (playlist_id, video_id, position)
        VALUES (?, ?, ?)
        ON CONFLICT(playlist_id, video_id) DO UPDATE SET position = excluded.position
        """,
        (playlist_id, video_id, position),
    )
