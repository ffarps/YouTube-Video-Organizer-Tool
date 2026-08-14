# Watchlog

A local-first tool for taming your YouTube backlog. Sync playlists and
channels into a database on your machine, get videos auto-organized into
themes, track what you've watched, and ask *"what should I watch?"* — it
recommends from your own library based on what you liked.

Everything runs locally: one SQLite file, an optional on-device ML model,
no accounts, no cloud. The only thing it talks to is YouTube.

## Features

- **One-box sync** — paste any playlist, channel, or video URL. Re-syncing
  never duplicates; new videos just appear. Your **Watch Later** playlist
  works too (via browser cookies — the official API can't touch it).
- **Auto-theming** — a transparent keyword layer plus a multilingual
  embedding model (PT/EN friendly) sort videos into themes. Videos the
  model isn't sure about land in a **review queue** where one click fixes them.
- **Watch tracking** — mark watched, then thumbs up or down. No star scale to
  agonize over: a video you watched without voting already counts as "it was
  okay", so you only press a button when you actually have an opinion.
- **Recommendations** — ranked by similarity to what you thumbed up,
  de-duplicated for variety, filterable by theme and by *"I have N minutes"*.
- **Theme discovery** — cluster your library to find themes you didn't know
  you had.
- **Offline copies** — save any single video to your machine (4K down to
  360p, or audio only) and it plays from disk in the built-in player, no
  connection needed. One video at a time, on purpose — see
  [Watching offline](#watching-offline).
- **Simple UI** — a single page, in its own desktop window or at
  `http://localhost:8000`, with Dark, Light, and Black & white color themes.

## Quick start (Windows)

Double-click **`start.bat`**. On first run it installs the dependencies; after
that the app opens in its own window — no browser tab, no console, no server
to remember to stop. Closing the window shuts everything down.

Run **`create-desktop-shortcut.bat`** once to put a *My Watch Log* icon on
your Desktop, and use that from then on. (It points at `Watchlog.vbs`, the
launcher that starts the app without even a flicker of a console window.)

<details>
<summary>Manual setup (any OS)</summary>

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e ".[dev,desktop]"
python -m app.desktop        # app in its own window
```

Or run it as a plain web app and use your browser:

```bash
uvicorn app.main:app --reload
```

Then open http://localhost:8000. On Windows, `start.bat browser` does the
same thing (server in a console, auto-reloading) if you'd rather work that way.
</details>

### The window

`python -m app.desktop` starts the server on localhost and puts it in a
desktop window. It's the same app either way — the window is just a shell
around the same page.

- **Windows** renders it with the WebView2 runtime that already ships with
  Windows 10/11, via [pywebview] (the `desktop` extra). Nothing else to install.
- **No pywebview?** It falls back to Edge/Chrome in `--app` mode: the same
  chromeless window, minus the dependency.
- Links out to YouTube open in your real browser, where you're signed in.
- Set `WATCHLOG_PORT` to pin the port; otherwise it takes 8000, or the next
  free port if something else has it.
- Nothing can print to a console that isn't there, so anything that would
  have been logged goes to `watchlog-desktop.log` next to the database.

[pywebview]: https://pywebview.flowrl.com/

## Optional setup

Copy `.env.example` to `.env` and fill in what you need:

| Setting | What it does |
|---|---|
| `YOUTUBE_API_KEY` | Free Google Cloud key ([get one here](https://console.cloud.google.com/apis/library/youtube.googleapis.com), enable *YouTube Data API v3*). Makes playlist syncs near-instant (50 videos per request). Without it everything still works via yt-dlp, just slower. |
| `YTDLP_COOKIES_BROWSER` | `firefox`, `chrome`, or `edge` — lets sync read your private **Watch Later** playlist. |
| `DATABASE_PATH` | Where the SQLite file lives (default `organizer.db`). |
| `MEDIA_PATH` | Where offline copies are stored (default `media/`). |
| `DOWNLOAD_MAX_HEIGHT` | Default download quality: `2160` (4K), `1440` (2K), `1080`, `720`… Default `1440`. |

For embedding-based theming and recommendations, install the ML extra once
(~1 GB, includes PyTorch; the model itself downloads on first use):

```bash
pip install -e ".[ml]"
```

## Typical workflow

1. Paste a playlist URL into the sync box.
2. Click **Embed**, then **Auto-theme** — most videos get sorted; check the
   **Review queue** tab for the stragglers.
3. Browse by theme and watch things. As a video winds down a small prompt
   slides into the corner — thumb it up or down, or ignore it and keep
   watching.
4. Open **What should I watch?**, optionally set a time budget, and let it
   pick for you. The more you vote, the better it gets.

Migrating from the old `videos.json` format:

```bash
python scripts/migrate_videos_json.py videos.json organizer.db
```

## Watching offline

Every video card has an **⬇ offline** chip. Click it and pick:

- **Best available** — whatever this particular video actually has. Use this
  when you don't want to think about it; a 4K upload gives you 4K, a 720p
  upload gives you 720p.
- **A ceiling** — *up to* 4K / 2K / 1080p / 720p / 480p / 360p. You get the
  tallest stream at or below that height, so on a video that tops out at
  1080p every option above it lands in the same place.
- **Audio only** — the m4a track, for podcasts and music. Needs no ffmpeg.

The file downloads in the background — the chip shows progress, and you can
keep using the app. Once it's done the card gets an **offline** badge and
clicking the thumbnail plays the local file instead of the YouTube embed: no
connection, no ads, and seeking works normally.

Up to 1080p you get H.264, which every player handles. Above that YouTube only
publishes VP9/AV1, so that's what 2K and 4K give you — fine in any current
browser, occasionally fussy in older desktop players.

### Offline copies are your favourites shelf

Saving something is a stronger signal than any thumb: it's the small set of
videos you actually come back to. So the app treats it that way.

- **`offline only`** in the Browse toolbar narrows the grid to saved copies,
  and it stacks with everything else — pick a theme in the sidebar to get
  *"my offline Homelab videos"*, or add a search on top.
- **Replay counts.** Every time you open a video, the play is counted, and the
  card shows a **▶ 7×** badge. Sort by **most watched** or **recently
  watched** to surface what you keep returning to. This is independent of
  watched/thumbs — rewatching never re-marks a video or clears a vote.
- **The Offline tab** lists every copy with its size, category chips, and play
  count, filterable by category and sortable by plays, size, or title.
  **Open folder** and **Show in folder** open the media directory in Explorer
  (or Finder / your Linux file manager).

Deleting a copy frees the disk space and keeps the video in your library.
Re-downloading at a different quality replaces it in place — and if that
fails, the copy you already had is kept rather than lost.

### Install ffmpeg first

**Without ffmpeg, downloads are capped at 360p.** YouTube only serves a
single combined video+audio file at 360p; every higher quality comes as two
separate streams that have to be merged, and ffmpeg is the thing that merges
them. On Windows:

```bash
winget install Gyan.FFmpeg
```

Restart Watchlog afterwards. The **Offline** tab tells you which state you're
in and labels any quality it can't currently deliver. Audio-only downloads
never need ffmpeg.

### A note on scope

This is deliberately one video at a time — there's no "download everything"
button, and there won't be. Bulk-archiving a library is against YouTube's
Terms of Service, and the disk math is unkind anyway (1080p runs roughly
100–250 MB per video, so a 500-video backlog is ~75 GB). Saving individual
videos for a flight or a commute is the use case this is built for.

## API

The UI is a thin layer over a REST API — interactive docs at
`http://localhost:8000/docs`. Key endpoints: `POST /sync`, `GET /themes`,
`GET /themes/{name}/videos`, `PATCH /videos/{id}/watch-state`,
`GET /recommendations`, `POST /themes/discover`, `GET /review`,
`POST /videos/{id}/download`, `GET /downloads`, `POST /videos/{id}/play`,
`POST /downloads/reveal`. `GET /videos` takes `downloaded=true` and
`sort=played`.

## Development

```bash
pytest        # test suite — offline, no API quota, no ML model needed
```

See [ROADMAP.md](ROADMAP.md) for the design constraints (API quota math,
why Watch Later needs yt-dlp, why recommendations are content-based) and
what's next.

## License

Apache 2.0 — see [LICENSE](LICENSE).
