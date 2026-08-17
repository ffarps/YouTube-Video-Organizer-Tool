import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Set

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

CREATE TABLE IF NOT EXISTS theme_rules (
    id         INTEGER PRIMARY KEY,
    theme_name TEXT NOT NULL,
    pattern    TEXT NOT NULL,       -- literal expression, word-boundary matched
    exclusive  INTEGER NOT NULL DEFAULT 0,  -- 1: matching video gets ONLY this theme
    created_at TEXT NOT NULL
);

-- Remembers that a built-in keyword theme (rules.THEME_KEYWORDS key) was
-- renamed/merged, so the rule engine feeds its videos into the current name
-- instead of recreating the original name on the next ingest/reapply.
CREATE TABLE IF NOT EXISTS theme_aliases (
    rule_key TEXT PRIMARY KEY,      -- the built-in theme key that was renamed
    name     TEXT NOT NULL          -- the display name it now maps to
);

CREATE TABLE IF NOT EXISTS watch_state (
    video_id   TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    status     TEXT NOT NULL DEFAULT 'unwatched',  -- unwatched | watched | skipped
    rating     INTEGER,                            -- -1 thumbs down | +1 thumbs up
                                                   -- (NULL on a watched video = "it was okay")
    watched_at TEXT,
    -- Rewatches. `status`/`rating` say what you thought of a video once;
    -- these say how often you come back to it, which is the thing that
    -- actually identifies a favourite.
    play_count     INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT
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

-- Locally archived copies. Separate from `videos` so a failed or deleted
-- download never disturbs the metadata row, and so the file can come and go
-- while the library entry stays put. `filename` is a basename inside
-- MEDIA_PATH, not a full path — moving the media folder must not orphan
-- every row.
CREATE TABLE IF NOT EXISTS downloads (
    video_id     TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'queued',  -- queued | downloading | done | error
    filename     TEXT,        -- NULL until the download finishes
    size_bytes   INTEGER,
    height       INTEGER,     -- what actually came down, NULL for audio-only
    audio_only   INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    requested_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_video_themes_theme ON video_themes(theme_id);
CREATE INDEX IF NOT EXISTS idx_videos_published ON videos(published_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SEARCH_URL_RE = re.compile(r"https?://\S+")


@lru_cache(maxsize=256)
def _term_re(term: str) -> re.Pattern:
    # Anchored at a word start, so "tv" finds "TV shows" but not "natgeotv".
    # Words of 3+ chars match as a prefix (typing "assassin" finds
    # "Assassin's" mid-word); shorter ones must be whole words — plural
    # allowed, so "tv" still finds "TVs" — or "ai" would drag in "airplane".
    tail = "" if len(term) > 2 else r"s?(?![0-9A-Za-z])"
    return re.compile(
        r"(?<![0-9A-Za-z])" + re.escape(term) + tail, re.IGNORECASE
    )


def search_hit(term: str, text: Optional[str], strip_urls: int = 0) -> int:
    """SQL helper: does `term` start a word in `text`?

    Registered on every connection so list_videos can search without LIKE.
    `strip_urls` drops links first — descriptions are mostly link soup, and
    "youtube.com/tv" should not count as a hit for "tv".
    """
    if not text:
        return 0
    if strip_urls:
        text = _SEARCH_URL_RE.sub(" ", text)
    return 1 if _term_re(term).search(text) else 0


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.create_function("search_hit", 3, search_hit, deterministic=True)
    return conn


SCHEMA_VERSION = 1  # 1: 5-star ratings collapsed to thumbs


def _migrate_ratings_to_thumbs(conn: sqlite3.Connection) -> None:
    """5 stars -> thumbs: 4-5 up, 1-2 down, 3 becomes no vote at all.

    A 3-star "it was okay" needs no storage now — a watched video with no
    thumb already says exactly that. Guarded by user_version because the
    mapping is NOT idempotent: a stored 1 means one star before this runs and
    thumbs up after.
    """
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return
    conn.execute(
        """
        UPDATE watch_state
           SET rating = CASE WHEN rating >= 4 THEN 1
                             WHEN rating <= 2 THEN -1
                             ELSE NULL END
         WHERE rating IS NOT NULL
        """
    )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _migrate_play_counters(conn: sqlite3.Connection) -> None:
    """Add the rewatch counters to a watch_state that predates them.

    Guarded on the column list rather than on `user_version`, because a fresh
    database already gets them from SCHEMA and would have nothing to add — and
    because tying it to SCHEMA_VERSION would re-run the ratings migration on
    an already-migrated database, flipping every thumbs up into a thumbs down.
    """
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(watch_state)")}
    if "play_count" not in columns:
        conn.execute(
            "ALTER TABLE watch_state ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0"
        )
    if "last_played_at" not in columns:
        conn.execute("ALTER TABLE watch_state ADD COLUMN last_played_at TEXT")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_ratings_to_thumbs(conn)
    _migrate_play_counters(conn)
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
        SELECT v.*, w.status AS watch_status, w.rating,
               w.play_count, w.last_played_at,
               d.status AS download_status, d.filename AS download_file
        FROM videos v
        LEFT JOIN watch_state w ON w.video_id = v.id
        LEFT JOIN downloads  d ON d.video_id = v.id
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


def delete_videos(conn: sqlite3.Connection, video_ids: Iterable[str]) -> int:
    """Delete many videos at once; cascades to themes/watch_state/playlists.
    Returns the number actually removed."""
    ids = list(video_ids)
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(f"DELETE FROM videos WHERE id IN ({placeholders})", ids)
    return cur.rowcount


def _row_to_video(row: sqlite3.Row) -> dict:
    video = dict(row)
    video["tags"] = json.loads(video.get("tags") or "[]")
    video.pop("embedding", None)
    video["watch_status"] = video.get("watch_status") or "unwatched"
    # Not every query joins `downloads` (recommendations and exports don't
    # need it), but the card renderer reads these unconditionally.
    video.setdefault("download_status", None)
    video.setdefault("download_file", None)
    # A video nobody has opened has no watch_state row at all, so NULL here
    # means zero plays rather than unknown.
    video["play_count"] = video.get("play_count") or 0
    video.setdefault("last_played_at", None)
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


def list_themes(
    conn: sqlite3.Connection, watched: Optional[bool] = None
) -> List[dict]:
    """List themes with a video count. When ``watched`` is given, the count
    reflects only videos in that watch state (e.g. ``watched=False`` counts a
    theme's unwatched videos), matching the Browse "unwatched only" filter."""
    if watched is None:
        count_expr = "COUNT(vt.video_id)"
    elif watched is False:
        count_expr = (
            "SUM(CASE WHEN vt.video_id IS NOT NULL "
            "AND COALESCE(w.status, 'unwatched') != 'watched' THEN 1 ELSE 0 END)"
        )
    else:
        count_expr = "SUM(CASE WHEN w.status = 'watched' THEN 1 ELSE 0 END)"
    return [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT t.id, t.name, t.kind, {count_expr} AS video_count
            FROM themes t
            LEFT JOIN video_themes vt ON vt.theme_id = t.id
            LEFT JOIN watch_state w ON w.video_id = vt.video_id
            GROUP BY t.id ORDER BY t.name
            """
        )
    ]


def delete_theme(conn: sqlite3.Connection, name: str) -> bool:
    """Delete a theme and all its assignments (cascade)."""
    cur = conn.execute("DELETE FROM themes WHERE name = ?", (name,))
    return cur.rowcount > 0


def rename_theme(
    conn: sqlite3.Connection,
    old_name: str,
    new_name: str,
    builtin_keys: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """Rename a theme; if the target name already exists, merge into it.
    Returns 'renamed', 'merged', or None when old_name doesn't exist.

    ``builtin_keys`` is the set of rule-engine keyword theme keys
    (rules.THEME_KEYWORDS). Renaming one of them records an alias so the
    rule engine keeps feeding its videos into the new name instead of
    recreating the original on the next ingest/reapply."""
    old = conn.execute("SELECT id FROM themes WHERE name = ?", (old_name,)).fetchone()
    if old is None:
        return None
    target = conn.execute(
        "SELECT id FROM themes WHERE name = ?", (new_name,)
    ).fetchone()
    _record_theme_alias(conn, old_name, new_name, builtin_keys)
    if target is None or target["id"] == old["id"]:
        conn.execute("UPDATE themes SET name = ? WHERE id = ?", (new_name, old["id"]))
        return "renamed"
    conn.execute(
        """
        INSERT OR IGNORE INTO video_themes (video_id, theme_id, confidence, source)
        SELECT video_id, ?, confidence, source FROM video_themes WHERE theme_id = ?
        """,
        (target["id"], old["id"]),
    )
    conn.execute("DELETE FROM themes WHERE id = ?", (old["id"],))
    return "merged"


def _record_theme_alias(
    conn: sqlite3.Connection,
    old_name: str,
    new_name: str,
    builtin_keys: Optional[Iterable[str]],
) -> None:
    """Keep the built-in-key -> display-name map in sync across a rename.

    - Any alias that pointed at ``old_name`` now points at ``new_name`` (so a
      built-in theme renamed twice, A -> B -> C, still resolves to C).
    - If ``old_name`` is itself a built-in key, start tracking it."""
    conn.execute(
        "UPDATE theme_aliases SET name = ? WHERE name = ?", (new_name, old_name)
    )
    if old_name in set(builtin_keys or ()):
        conn.execute(
            """
            INSERT INTO theme_aliases (rule_key, name) VALUES (?, ?)
            ON CONFLICT(rule_key) DO UPDATE SET name = excluded.name
            """,
            (old_name, new_name),
        )


def builtin_theme_overrides(conn: sqlite3.Connection) -> Dict[str, str]:
    """Map each renamed built-in theme key to its current display name.
    Passed to rules.evaluate so renamed built-in themes aren't recreated."""
    return {
        r["rule_key"]: r["name"]
        for r in conn.execute("SELECT rule_key, name FROM theme_aliases")
    }


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
    sort: str = "newest",
    downloaded: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> Optional[List[dict]]:
    theme = conn.execute(
        "SELECT id FROM themes WHERE name = ?", (theme_name,)
    ).fetchone()
    if theme is None:
        return None
    query = """
        SELECT v.*, w.status AS watch_status, w.rating,
               w.play_count, w.last_played_at,
               d.status AS download_status, d.filename AS download_file
        FROM videos v
        JOIN video_themes vt ON vt.video_id = v.id AND vt.theme_id = ?
        LEFT JOIN watch_state w ON w.video_id = v.id
        LEFT JOIN downloads  d ON d.video_id = v.id
    """
    params: list = [theme["id"]]
    clauses: list = []
    if watched is True:
        clauses.append("w.status = 'watched'")
    elif watched is False:
        clauses.append("COALESCE(w.status, 'unwatched') != 'watched'")
    if downloaded is True:
        clauses.append("d.status = 'done'")
    elif downloaded is False:
        clauses.append("COALESCE(d.status, '') != 'done'")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += f" ORDER BY {_VIDEO_SORTS.get(sort, _VIDEO_SORTS['newest'])}"
    query += " LIMIT ? OFFSET ?"
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


def remove_other_themes(
    conn: sqlite3.Connection, video_id: str, keep_names: Iterable[str]
) -> int:
    """Drop every theme assignment for a video except keep_names.
    Returns the number of assignments removed."""
    keep = list(keep_names)
    placeholders = ",".join("?" * len(keep))
    cur = conn.execute(
        f"""
        DELETE FROM video_themes
        WHERE video_id = ?
          AND theme_id NOT IN (SELECT id FROM themes WHERE name IN ({placeholders}))
        """,
        [video_id, *keep],
    )
    return cur.rowcount


def remove_stale_rule_themes(
    conn: sqlite3.Connection, video_id: str, keep_names: Iterable[str]
) -> int:
    """Drop rule-sourced assignments not in keep_names. Manual and embedding
    assignments are preserved. Returns the number removed."""
    keep = list(keep_names)
    query = "DELETE FROM video_themes WHERE video_id = ? AND source = 'rule'"
    params: list = [video_id]
    if keep:
        placeholders = ",".join("?" * len(keep))
        query += (
            f" AND theme_id NOT IN (SELECT id FROM themes WHERE name IN ({placeholders}))"
        )
        params += keep
    cur = conn.execute(query, params)
    return cur.rowcount


# --- theme rules ------------------------------------------------------------

def add_theme_rule(
    conn: sqlite3.Connection, theme_name: str, pattern: str, exclusive: bool = False
) -> dict:
    cur = conn.execute(
        """
        INSERT INTO theme_rules (theme_name, pattern, exclusive, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (theme_name, pattern, int(exclusive), now_iso()),
    )
    return {
        "id": cur.lastrowid,
        "theme_name": theme_name,
        "pattern": pattern,
        "exclusive": exclusive,
    }


def list_theme_rules(conn: sqlite3.Connection) -> List[dict]:
    return [
        {**dict(r), "exclusive": bool(r["exclusive"])}
        for r in conn.execute("SELECT * FROM theme_rules ORDER BY id")
    ]


def delete_theme_rule(conn: sqlite3.Connection, rule_id: int) -> bool:
    cur = conn.execute("DELETE FROM theme_rules WHERE id = ?", (rule_id,))
    return cur.rowcount > 0


def channel_theme_counts(conn: sqlite3.Connection) -> List[dict]:
    """Per (channel, theme): how many of the channel's videos carry that theme.
    Feeds channel -> theme rule suggestions (rules.suggest_rules)."""
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT v.channel_title AS channel, t.name AS theme,
                   COUNT(DISTINCT v.id) AS n
            FROM videos v
            JOIN video_themes vt ON vt.video_id = v.id
            JOIN themes t ON t.id = vt.theme_id
            WHERE v.channel_title IS NOT NULL AND TRIM(v.channel_title) != ''
            GROUP BY v.channel_title, t.name
            """
        )
    ]


def channel_tagged_totals(conn: sqlite3.Connection) -> Dict[str, int]:
    """channel -> number of its videos that have at least one theme.
    A video with several themes counts once (COUNT DISTINCT)."""
    return {
        r["channel"]: r["total"]
        for r in conn.execute(
            """
            SELECT v.channel_title AS channel, COUNT(DISTINCT v.id) AS total
            FROM videos v
            JOIN video_themes vt ON vt.video_id = v.id
            WHERE v.channel_title IS NOT NULL AND TRIM(v.channel_title) != ''
            GROUP BY v.channel_title
            """
        )
    }


def all_videos(conn: sqlite3.Connection) -> List[dict]:
    return [
        _row_to_video(r)
        for r in conn.execute(
            """
            SELECT v.*, w.status AS watch_status, w.rating
            FROM videos v LEFT JOIN watch_state w ON w.video_id = v.id
            """
        )
    ]


_VIDEO_SORTS = {
    "newest": "v.published_at DESC",
    "oldest": "v.published_at ASC",
    "longest": "v.duration_sec DESC",
    "shortest": "v.duration_sec ASC",
    "channel": "v.channel_title COLLATE NOCASE ASC",
    "added": "v.added_at DESC",
    # Never-played videos have no watch_state row, so COALESCE keeps them at
    # the bottom instead of letting NULL sort above a real count.
    "played": "COALESCE(w.play_count, 0) DESC, w.last_played_at DESC",
    "replayed": "w.last_played_at DESC",
}


def list_videos(
    conn: sqlite3.Connection,
    search: Optional[str] = None,
    sort: str = "newest",
    watched: Optional[bool] = None,
    unthemed: Optional[bool] = None,
    downloaded: Optional[bool] = None,
    channel: Optional[str] = None,
    channel_id: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[dict]:
    query = """
        SELECT v.*, w.status AS watch_status, w.rating,
               w.play_count, w.last_played_at,
               d.status AS download_status, d.filename AS download_file
        FROM videos v
        LEFT JOIN watch_state w ON w.video_id = v.id
        LEFT JOIN downloads  d ON d.video_id = v.id
    """
    clauses: list = []
    params: list = []
    # Every word typed must hit somewhere (title, channel or description).
    terms = (search or "").split()
    for term in terms:
        clauses.append(
            "(search_hit(?, v.title, 0) OR search_hit(?, v.channel_title, 0)"
            " OR search_hit(?, v.description, 1))"
        )
        params += [term, term, term]
    if watched is True:
        clauses.append("w.status = 'watched'")
    elif watched is False:
        clauses.append("COALESCE(w.status, 'unwatched') != 'watched'")
    if unthemed is True:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM video_themes vt WHERE vt.video_id = v.id)"
        )
    elif unthemed is False:
        clauses.append(
            "EXISTS (SELECT 1 FROM video_themes vt WHERE vt.video_id = v.id)"
        )
    # Only a finished copy counts as offline — a queued or failed one has no
    # file behind it, so it would be a broken entry in an "offline" view.
    if downloaded is True:
        clauses.append("d.status = 'done'")
    elif downloaded is False:
        clauses.append("COALESCE(d.status, '') != 'done'")
    # One channel, matched by id *and* by name: a yt-dlp row can carry the name
    # with no id, and a channel that renamed leaves its old name on everything
    # ingested before the rename. Either test alone shows only half the channel.
    channel_match = []
    if channel_id:
        channel_match.append("v.channel_id = ?")
        params.append(channel_id)
    if channel:
        channel_match.append("v.channel_title = ? COLLATE NOCASE")
        params.append(channel)
    if channel_match:
        clauses.append("(" + " OR ".join(channel_match) + ")")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY "
    # What you searched for is almost always in the title or the channel;
    # description-only hits are sponsor blurbs and link lists, so they rank
    # last instead of burying the real matches.
    for field in ("v.title", "v.channel_title") if terms else ():
        hits = " AND ".join(["search_hit(?, %s, 0)" % field] * len(terms))
        query += f"CASE WHEN {hits} THEN 0 ELSE 1 END, "
        params += terms
    query += _VIDEO_SORTS.get(sort, _VIDEO_SORTS["newest"])
    query += " LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row_to_video(r) for r in conn.execute(query, params)]


def count_videos(conn: sqlite3.Connection, watched: Optional[bool] = None) -> int:
    query = "SELECT COUNT(*) AS n FROM videos v LEFT JOIN watch_state w ON w.video_id = v.id"
    if watched is True:
        query += " WHERE w.status = 'watched'"
    elif watched is False:
        query += " WHERE COALESCE(w.status, 'unwatched') != 'watched'"
    return conn.execute(query).fetchone()["n"]


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


def existing_video_ids(
    conn: sqlite3.Connection, video_ids: Iterable[str]
) -> Set[str]:
    """Subset of video_ids that actually exist in the library."""
    ids = list(video_ids)
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    return {
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM videos WHERE id IN ({placeholders})", ids
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
        # 0 clears the vote — tapping the thumb you already gave takes it back
        conn.execute(
            "UPDATE watch_state SET rating = ? WHERE video_id = ?",
            (rating or None, video_id),
        )
    return True


def record_play(conn: sqlite3.Connection, video_id: str) -> Optional[int]:
    """Count one play. Returns the new total, or None for an unknown video.

    Deliberately independent of `status`: opening something again is what
    makes it a favourite, and that says nothing about whether you consider it
    watched. Rewatching a video you already thumbed up must not reset either.
    """
    if conn.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
        return None
    conn.execute(
        "INSERT OR IGNORE INTO watch_state (video_id) VALUES (?)", (video_id,)
    )
    conn.execute(
        """
        UPDATE watch_state
           SET play_count = COALESCE(play_count, 0) + 1, last_played_at = ?
         WHERE video_id = ?
        """,
        (now_iso(), video_id),
    )
    conn.commit()
    return conn.execute(
        "SELECT play_count FROM watch_state WHERE video_id = ?", (video_id,)
    ).fetchone()["play_count"]


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


# --- local playlists --------------------------------------------------------
# kind='local' playlists are user-made inside the tool; sync never touches
# them ("local-" ids can't collide with YouTube playlist ids).

def create_playlist(conn: sqlite3.Connection, title: str) -> dict:
    playlist_id = "local-" + uuid.uuid4().hex[:10]
    conn.execute(
        "INSERT INTO playlists (id, title, kind) VALUES (?, ?, 'local')",
        (playlist_id, title),
    )
    return {"id": playlist_id, "title": title, "kind": "local", "video_count": 0}


def get_playlist(conn: sqlite3.Connection, playlist_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM playlists WHERE id = ?", (playlist_id,)
    ).fetchone()
    return dict(row) if row else None


def list_playlists(conn: sqlite3.Connection) -> List[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT p.id, p.title, p.kind, COUNT(pi.video_id) AS video_count
            FROM playlists p LEFT JOIN playlist_items pi ON pi.playlist_id = p.id
            GROUP BY p.id
            ORDER BY (p.kind = 'local') DESC, p.title COLLATE NOCASE
            """
        )
    ]


def delete_playlist(conn: sqlite3.Connection, playlist_id: str) -> bool:
    cur = conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
    return cur.rowcount > 0


def playlist_videos(conn: sqlite3.Connection, playlist_id: str) -> Optional[List[dict]]:
    if get_playlist(conn, playlist_id) is None:
        return None
    return [
        _row_to_video(r)
        for r in conn.execute(
            """
            SELECT v.*, w.status AS watch_status, w.rating,
               w.play_count, w.last_played_at, pi.position,
                   d.status AS download_status, d.filename AS download_file
            FROM playlist_items pi
            JOIN videos v ON v.id = pi.video_id
            LEFT JOIN watch_state w ON w.video_id = v.id
            LEFT JOIN downloads  d ON d.video_id = v.id
            WHERE pi.playlist_id = ?
            ORDER BY pi.position
            """,
            (playlist_id,),
        )
    ]


def add_playlist_video(
    conn: sqlite3.Connection, playlist_id: str, video_id: str
) -> None:
    """Append a video at the end of a playlist (no-op if already in it)."""
    row = conn.execute(
        "SELECT COALESCE(MAX(position) + 1, 0) AS next FROM playlist_items WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO playlist_items (playlist_id, video_id, position) VALUES (?, ?, ?)",
        (playlist_id, video_id, row["next"]),
    )


def remove_playlist_video(
    conn: sqlite3.Connection, playlist_id: str, video_id: str
) -> bool:
    cur = conn.execute(
        "DELETE FROM playlist_items WHERE playlist_id = ? AND video_id = ?",
        (playlist_id, video_id),
    )
    return cur.rowcount > 0


# --- downloads --------------------------------------------------------------

def mark_download_queued(
    conn: sqlite3.Connection, video_id: str, audio_only: bool = False
) -> None:
    """Claim a download slot for a video, clearing any previous failure.

    Re-downloading at a different quality replaces the row rather than adding
    one — a video has at most one local copy.
    """
    conn.execute(
        """
        INSERT INTO downloads (video_id, status, audio_only, requested_at)
        VALUES (?, 'queued', ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            status = 'queued', audio_only = excluded.audio_only,
            requested_at = excluded.requested_at,
            filename = NULL, size_bytes = NULL, height = NULL,
            error = NULL, completed_at = NULL
        """,
        (video_id, 1 if audio_only else 0, now_iso()),
    )
    conn.commit()


def mark_download_running(conn: sqlite3.Connection, video_id: str) -> None:
    conn.execute(
        "UPDATE downloads SET status = 'downloading' WHERE video_id = ?", (video_id,)
    )
    conn.commit()


def mark_download_done(
    conn: sqlite3.Connection,
    video_id: str,
    filename: str,
    size_bytes: int,
    height: Optional[int],
) -> None:
    conn.execute(
        """
        UPDATE downloads
           SET status = 'done', filename = ?, size_bytes = ?, height = ?,
               error = NULL, completed_at = ?
         WHERE video_id = ?
        """,
        (filename, size_bytes, height, now_iso(), video_id),
    )
    conn.commit()


def restore_download(
    conn: sqlite3.Connection, video_id: str, previous: dict, error: str
) -> None:
    """Point the row back at the copy a failed re-download was replacing.

    Asking for a better quality must never cost you the file you already had,
    so a failure rolls the row back to the old one. The error rides along on
    the restored row so the UI can still say the upgrade didn't happen.
    """
    conn.execute(
        """
        UPDATE downloads
           SET status = 'done', filename = ?, size_bytes = ?, height = ?,
               audio_only = ?, error = ?, completed_at = ?
         WHERE video_id = ?
        """,
        (
            previous["filename"], previous["size_bytes"], previous["height"],
            previous["audio_only"], error[:500], now_iso(), video_id,
        ),
    )
    conn.commit()


def mark_download_failed(conn: sqlite3.Connection, video_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE downloads
           SET status = 'error', error = ?, completed_at = ?
         WHERE video_id = ?
        """,
        (error[:500], now_iso(), video_id),
    )
    conn.commit()


def get_download(conn: sqlite3.Connection, video_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM downloads WHERE video_id = ?", (video_id,)
    ).fetchone()
    return dict(row) if row else None


def list_downloads(conn: sqlite3.Connection) -> List[dict]:
    """Every download row, newest request first, with enough video metadata to
    render a card without a second query."""
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT d.*, v.title, v.channel_title, v.thumbnail_url, v.duration_sec,
                   COALESCE(w.play_count, 0) AS play_count, w.last_played_at
            FROM downloads d
            JOIN videos v ON v.id = d.video_id
            LEFT JOIN watch_state w ON w.video_id = d.video_id
            ORDER BY d.requested_at DESC
            """
        )
    ]


def delete_download(conn: sqlite3.Connection, video_id: str) -> Optional[str]:
    """Drop the download row, returning the filename the caller should unlink
    (None if there was nothing recorded)."""
    row = conn.execute(
        "SELECT filename FROM downloads WHERE video_id = ?", (video_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM downloads WHERE video_id = ?", (video_id,))
    conn.commit()
    return row["filename"]


def downloaded_filenames(
    conn: sqlite3.Connection, video_ids: Iterable[str]
) -> List[str]:
    """Filenames on disk for the given videos — used to clean up media when
    videos are deleted, since ON DELETE CASCADE takes the row but not the file."""
    ids = list(video_ids)
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    return [
        r["filename"]
        for r in conn.execute(
            f"SELECT filename FROM downloads WHERE video_id IN ({placeholders})"
            " AND filename IS NOT NULL",
            ids,
        )
    ]


def downloads_disk_usage(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS files, COALESCE(SUM(size_bytes), 0) AS bytes
        FROM downloads WHERE status = 'done'
        """
    ).fetchone()
    return {"files": row["files"], "bytes": row["bytes"]}
