# YouTube Video Organizer

Local-first tool that syncs YouTube playlists/channels into SQLite,
auto-organizes videos into themes, and tracks what you've watched.
Everything runs on your machine — the only external dependency is YouTube.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # optional: add a YouTube Data API key for fast syncs
```

Without an API key the tool still works — ingestion falls back to yt-dlp,
just slower. With a (free) key, syncing costs ~1 quota unit per 50 videos
against a 10,000/day allowance.

## Usage

```bash
uvicorn app.main:app --reload
```

Interactive API docs at http://localhost:8000/docs. Highlights:

- `POST /sync` `{"url": "<playlist/channel/video URL>"}` — idempotent sync;
  Watch Later works too if `YTDLP_COOKIES_BROWSER` is set in `.env`
- `GET /themes`, `GET /themes/{name}/videos?watched=false`
- `PATCH /videos/{id}/watch-state` `{"status": "watched", "rating": 5}`

Migrate a legacy `videos.json`:

```bash
python scripts/migrate_videos_json.py videos.json organizer.db
```

## Development

```bash
pytest   # offline, quota-free
```

See [ROADMAP.md](ROADMAP.md) for where this is going (embedding-based theming,
content-based recommendations, single-page UI).
