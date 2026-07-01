# YouTube Video Organizer

A local-first tool for taming your YouTube backlog. Sync playlists and
channels into a database on your machine, get videos auto-organized into
themes, track what you've watched, and ask *"what should I watch?"* — it
recommends from your own library based on what you rated.

Everything runs locally: one SQLite file, an optional on-device ML model,
no accounts, no cloud. The only thing it talks to is YouTube.

## Features

- **One-box sync** — paste any playlist, channel, or video URL. Re-syncing
  never duplicates; new videos just appear. Your **Watch Later** playlist
  works too (via browser cookies — the official API can't touch it).
- **Auto-theming** — a transparent keyword layer plus a multilingual
  embedding model (PT/EN friendly) sort videos into themes. Videos the
  model isn't sure about land in a **review queue** where one click fixes them.
- **Watch tracking** — mark watched, rate 1–5 stars.
- **Recommendations** — ranked by similarity to what you rated highly,
  de-duplicated for variety, filterable by theme and by *"I have N minutes"*.
- **Theme discovery** — cluster your library to find themes you didn't know
  you had.
- **Simple UI** — a single page at `http://localhost:8000`, with Dark,
  Light, and Black & white color themes.

## Quick start (Windows)

Double-click **`start.bat`**. That's it — on first run it installs the
dependencies, then it starts the server and opens the app in your browser.
Close the window to stop it.

<details>
<summary>Manual setup (any OS)</summary>

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Then open http://localhost:8000.
</details>

## Optional setup

Copy `.env.example` to `.env` and fill in what you need:

| Setting | What it does |
|---|---|
| `YOUTUBE_API_KEY` | Free Google Cloud key ([get one here](https://console.cloud.google.com/apis/library/youtube.googleapis.com), enable *YouTube Data API v3*). Makes playlist syncs near-instant (50 videos per request). Without it everything still works via yt-dlp, just slower. |
| `YTDLP_COOKIES_BROWSER` | `firefox`, `chrome`, or `edge` — lets sync read your private **Watch Later** playlist. |
| `DATABASE_PATH` | Where the SQLite file lives (default `organizer.db`). |

For embedding-based theming and recommendations, install the ML extra once
(~1 GB, includes PyTorch; the model itself downloads on first use):

```bash
pip install -e ".[ml]"
```

## Typical workflow

1. Paste a playlist URL into the sync box.
2. Click **Embed**, then **Auto-theme** — most videos get sorted; check the
   **Review queue** tab for the stragglers.
3. Browse by theme, watch things, rate them with the stars.
4. Open **What should I watch?**, optionally set a time budget, and let it
   pick for you. The more you rate, the better it gets.

Migrating from the old `videos.json` format:

```bash
python scripts/migrate_videos_json.py videos.json organizer.db
```

## API

The UI is a thin layer over a REST API — interactive docs at
`http://localhost:8000/docs`. Key endpoints: `POST /sync`, `GET /themes`,
`GET /themes/{name}/videos`, `PATCH /videos/{id}/watch-state`,
`GET /recommendations`, `POST /themes/discover`, `GET /review`.

## Development

```bash
pytest        # test suite — offline, no API quota, no ML model needed
```

See [ROADMAP.md](ROADMAP.md) for the design constraints (API quota math,
why Watch Later needs yt-dlp, why recommendations are content-based) and
what's next.

## License

Apache 2.0 — see [LICENSE](LICENSE).
