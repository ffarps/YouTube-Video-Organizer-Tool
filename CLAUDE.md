# YouTube Video Organizer

Local-first FastAPI app that syncs YouTube playlists into SQLite, auto-themes
videos, and (Phase 3) recommends what to watch. See ROADMAP.md for phases and
design constraints.

## Commands

```bash
pip install -e ".[dev]"          # install (Python >= 3.11)
pytest                           # run tests (quota-free, no network)
uvicorn app.main:app --reload    # run the API on :8000 (docs at /docs)
python scripts/migrate_videos_json.py [videos.json] [organizer.db]  # legacy import
```

## Architecture

- `app/config.py` — pydantic-settings; reads `.env` (see `.env.example`).
  `YOUTUBE_API_KEY` optional: with it, ingestion uses the Data API (fast,
  50 videos/quota unit); without it, everything falls back to yt-dlp.
- `app/db.py` — sqlite3 schema + all queries. Video identity is the 11-char
  YouTube id. `video_themes` is many-to-many (a video can have several themes).
- `app/ingest/urls.py` — URL → id canonicalization; `classify_url` decides
  video/playlist/channel/watch_later.
- `app/ingest/sync.py` — orchestrator; only fetches metadata for ids not
  already in the DB, so re-syncs are cheap and idempotent.
- `app/ingest/ytdlp.py` — flat listing + keyless fallback. Watch Later is
  ONLY reachable this way (Data API blocks WL), needs
  `YTDLP_COOKIES_BROWSER` set.
- `app/categorize/rules.py` — word-boundary, scored, multi-label keyword
  themes applied at ingest. Embedding layer comes in Phase 2.
- `app/api/routes.py` — all endpoints; DB connection lives on
  `app.state.db` (set in the lifespan in `app/main.py`).

## Conventions

- Pydantic v2 only (`model_dump`, no `.dict()` overrides).
- Never call `search.list` (100 quota units); reads are 1 unit per 50 items.
- Tests fake the network boundary (`sync._fetch_metadata`,
  `ytdlp.list_playlist`) — keep them quota-free and offline.
- Secrets in `.env` (gitignored); `.env.example` documents the keys.
