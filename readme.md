# YouTube Video Organizer

Local-first tool that syncs YouTube playlists/channels into SQLite,
auto-organizes videos into themes (keyword rules + multilingual embeddings),
tracks what you've watched, and recommends what to watch next based on your
ratings. Everything runs on your machine — the only external dependency is
YouTube.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[dev,ml]"   # optional: embedding theming + recommendations
cp .env.example .env         # optional: YouTube Data API key for fast syncs
```

Without an API key the tool still works — ingestion falls back to yt-dlp,
just slower. With a (free) key, syncing costs ~1 quota unit per 50 videos
against a 10,000/day allowance.

## Usage

```bash
uvicorn app.main:app --reload
```

Open http://localhost:8000/ — the UI has a sync box (paste any playlist,
channel, or video URL), a theme browser with watch/rate buttons, a
"What should I watch?" feed with a time-budget filter, and a review queue
for videos the auto-theming wasn't sure about. Interactive API docs at
`/docs`. Highlights:

- `POST /sync` `{"url": "<playlist/channel/video URL>"}` — idempotent sync;
  Watch Later works too if `YTDLP_COOKIES_BROWSER` is set in `.env`
- `GET /themes`, `GET /themes/{name}/videos?watched=false`
- `PATCH /videos/{id}/watch-state` `{"status": "watched", "rating": 5}`
- `POST /embeddings/build` then `POST /themes/auto-assign` — embedding-based
  theming (needs the `[ml]` extra; first run downloads the model)
- `GET /recommendations?theme=&max_duration=1500&limit=10` — content-based,
  ranked by your ratings, diversity re-ranked
- `POST /themes/discover` — cluster the library to propose new themes

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
