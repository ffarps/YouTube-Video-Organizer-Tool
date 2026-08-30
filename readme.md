# Watchlog

Watchlog keeps a YouTube backlog in a SQLite file on your own machine. It pulls
in playlists, channels and single videos, sorts them into themes, tracks what
you have watched and how you rated it, suggests what to watch next out of your
own library, and can save individual videos to disk for offline viewing.

On Windows it opens as a desktop window. On any OS it runs as a local web app
at `http://localhost:8000`. There is no account and no server other than the
one on your machine; the only thing it talks to is YouTube.

The repository is still named YouTube-Video-Organizer-Tool. The app calls
itself Watchlog, and the window title is "My Watch Log".

## What it does not do

- It is not a bulk downloader. Offline copies are one video per request, by
  design. There is no "download everything" button and there will not be one.
- It cannot see your YouTube watch history, subscriptions or likes. The API
  does not expose them, so everything it knows about your habits is what you
  marked here.
- It is single-user and single-machine. No login, no sync between devices, no
  sharing.
- Recommendations are computed locally from your own library and your own
  votes. Nothing about your viewing leaves the machine.

## Requirements

- Python 3.11 or newer.
- Windows 10/11 for the one-click launcher and the desktop window. The web-app
  mode works anywhere Python does.
- ffmpeg, only if you want offline copies above 360p. See [Install ffmpeg
  first](#install-ffmpeg-first).
- A YouTube Data API key is optional. Without one, everything still works
  through yt-dlp, just slower.

## Install and run (Windows)

Double-click `start.bat`. The first run installs dependencies; after that the
app opens in its own window, with no browser tab and no console. Closing the
window stops the server.

Run `create-desktop-shortcut.bat` once to put a "My Watch Log" icon on your
Desktop and use that from then on. It launches `Watchlog.vbs`, which starts the
app without showing a console at all.

<details>
<summary>Manual setup (any OS)</summary>

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -e ".[dev,desktop]"
python -m app.desktop
```

To run it as a plain web app instead:

```bash
uvicorn app.main:app --reload
```

Then open http://localhost:8000. On Windows, `start.bat browser` does the same
thing (server in a console, browser tab, auto-reload on code changes).
</details>

### The window

`python -m app.desktop` starts the server on localhost and wraps it in a
desktop window. It is the same app either way; the window is only a shell
around the same page.

- On Windows it renders through the WebView2 runtime that ships with Windows
  10/11, using [pywebview] (the `desktop` extra). Nothing else to install.
- Without pywebview it falls back to Edge or Chrome in `--app` mode: the same
  chromeless window, no extra dependency. This is also the path on Linux and
  macOS if you would rather not install a GUI toolkit.
- Links out to YouTube open in your normal browser, where you are signed in.
- Set `WATCHLOG_PORT` to pin the port. Otherwise it uses 8000, or the next free
  port if something else already has it.
- There is no console to print to, so everything goes to a log file instead.
  See [When something goes wrong](#when-something-goes-wrong).

[pywebview]: https://pywebview.flowrl.com/

## Configuration

Copy `.env.example` to `.env` and fill in what you need. All of it is optional.

| Setting | What it does |
|---|---|
| `YOUTUBE_API_KEY` | Free Google Cloud key ([get one here](https://console.cloud.google.com/apis/library/youtube.googleapis.com), enable YouTube Data API v3). Makes syncs near-instant: 50 videos per request. Without it, ingestion falls back to yt-dlp. |
| `YTDLP_COOKIES_BROWSER` | `firefox`, `chrome` or `edge`. Required only to sync your private Watch Later playlist, which the official API cannot read. |
| `DATABASE_PATH` | Where the SQLite file lives. Default `organizer.db`. |
| `MEDIA_PATH` | Where offline copies are stored. Default `media/`. |
| `DOWNLOAD_MAX_HEIGHT` | Default quality ceiling for downloads: `2160`, `1440`, `1080`, `720`, `480`, `360`. Default `1440`. |

Theme suggestions and recommendations get better with the embedding model,
which is a separate install (~1 GB, includes PyTorch; the model itself
downloads on first use):

```bash
pip install -e ".[ml]"
```

Without it the app still runs: keyword rules, custom rules, manual themes and
theme-affinity recommendations all work on plain numpy.

## Using it

Paste a playlist, channel or video URL into the box at the top and press Enter.
Re-syncing the same source never duplicates anything; only new videos are
fetched. Watch Later works if you set `YTDLP_COOKIES_BROWSER`. "Bulk add" takes
a pile of pasted links at once.

New videos are themed as they arrive by a keyword pass, so the library is
sorted before you touch anything.

The main page has five tabs.

Browse — the grid, with a sidebar of themes and playlists and their counts.
Search matches whole words rather than substrings: every word you type has to
match the start of a word in the title, channel or description, words of three
or more characters match prefixes, and title and channel hits rank above
description hits. So "ai" finds AI videos and not airplanes. You can also
filter to unwatched only, videos with no theme, or offline copies only, and
sort by newest, oldest, longest, shortest, channel, recently added, most
watched or recently watched. "Select" turns on multi-select for theming or
deleting several videos at once.

What should I watch? — a ranked feed built from a profile vector of everything
you voted on: thumbs up counts positively, thumbs down and skips negatively,
and a watched video you never voted on counts weakly, because that is the "it
was fine" case. Results are blended with a recency boost and de-duplicated so
you do not get five near-identical videos in a row. Filter by theme and by how
many minutes you have.

Review queue — videos the embedding model could not confidently place, with
ranked theme suggestions. One click confirms.

Rules — your own expression-to-theme mappings on top of the built-in keywords.
A rule can be exclusive, meaning matching videos get that theme and no other.
The app also proposes channel-to-theme rules learned from videos you already
themed: if most of a channel's videos share a theme, a rule on that channel
name will theme its future uploads too. Nothing is created without you
approving it. "Re-theme existing videos" re-runs every rule over the whole
library; it prunes theme assignments the current rules no longer justify and
leaves manual ones alone.

Offline — every saved copy with its size, themes and play count. See below.

Themes can be renamed or deleted from the sidebar, and a rename sticks: the
rule engine will not recreate the old name later. Playlists in the sidebar are
your own lists, separate from themes, and a video can be in as many as you
like. Playlists that came from a YouTube sync are read-only, since a re-sync
overwrites them.

Watching happens in the app. Local copies play from disk, everything else in
the YouTube embed. A small prompt appears in the corner in the last seconds of
a video so you can thumb it up or down without interrupting anything; the full
panel, with an up-next countdown, only appears once the video ends. Every time
you open a video the play is counted, which is independent of watched and of
your vote: replaying something never re-marks it or clears a thumb.

Where you stopped is remembered. Leave a video part-way through — close it,
close the app, or have the player die under you — and the next time you open
it, it picks up there and says so, with "Start over" beside it if you wanted
the beginning after all. A card whose video you started shows how far in you
got as a bar along the bottom of the thumbnail. Finishing a video, or marking
it watched, retires its resume point.

The YouTube embed sometimes stops answering: the frame goes dead, clicks do
nothing, and it says nothing about why. The app watches the player's clock and
notices — a player that claims to be playing while its position stands still,
one that never loads at all, or one YouTube refused outright — and offers a
way out on the spot: **Reload**, which rebuilds the player at the second you
had reached, or a link to the same moment on youtube.com. `reload player` in
the row under the video does the same thing whenever you want it, whether or
not anything was detected. Each incident is written to `logs/watchlog.log`, so
"it froze again" leaves evidence: which video, what the player claimed to be
doing, and how far in it was.

Migrating from the old `videos.json` format:

```bash
python scripts/migrate_videos_json.py videos.json organizer.db
```

## Watching offline

Every video card has an offline chip. Click it and pick:

- Best available: whatever this particular video has. A 4K upload gives 4K, a
  720p upload gives 720p.
- A ceiling: up to 4K, 2K, 1080p, 720p, 480p or 360p. You get the tallest
  stream at or below that height, so on a video that tops out at 1080p every
  option above it lands in the same place.
- Audio only: the m4a track, for podcasts and music. Needs no ffmpeg.

The file downloads in the background, the chip shows progress, and you can keep
using the app. When it is done the card gets an offline badge and clicking the
thumbnail plays the local file instead of the YouTube embed: no connection, no
ads, and seeking works normally.

Up to 1080p you get H.264, which every player handles. Above that YouTube only
publishes VP9 and AV1, so that is what 2K and 4K give you: fine in any current
browser, occasionally awkward in older desktop players.

Saved copies double as a favourites shelf, since saving something is a stronger
signal than a thumb. "offline only" in the Browse toolbar narrows the grid to
them and stacks with the theme sidebar and the search box. The Offline tab
lists every copy with size, themes and play count, sortable by plays, size or
title, with buttons to open the media folder or reveal a single file in it.

Deleting a copy frees the disk space and keeps the video in your library.
Re-downloading at a different quality replaces it in place, and if that fails
the copy you already had is kept rather than lost.

### Install ffmpeg first

Without ffmpeg, downloads are capped at 360p. YouTube serves a single combined
video+audio file only at 360p; every higher quality comes as two separate
streams that have to be merged, and ffmpeg is what merges them. On Windows:

```bash
winget install Gyan.FFmpeg
```

Restart Watchlog afterwards. The Offline tab shows which state you are in and
labels any quality it cannot currently deliver. Audio-only downloads never need
ffmpeg.

### A note on scope

One video at a time is deliberate. Bulk-archiving a library is against
YouTube's Terms of Service, and the disk arithmetic is unfriendly anyway: 1080p
runs roughly 100-250 MB per video, so a 500-video backlog is about 75 GB.
Saving a few things for a flight or a commute is the case this is built for.

## When something goes wrong

Everything the app does is written to `logs/watchlog.log` (set `LOG_PATH` to
put it elsewhere). It rotates at 2 MB, keeps five files, and appends across
runs rather than starting fresh, because the first thing anyone does after a
crash is open the app again.

It records startup and shutdown, every download with its result, any request
that ends in a 500 along with the path that caused it, and the full traceback
of any exception — including ones thrown on background threads, which would
otherwise disappear without a trace.

A hard crash of the window itself, in the WebView or its .NET bridge, kills the
process before Python can log anything. Those write a native stack trace to
`logs/watchlog-crash.log` instead. If that file exists, something crashed at a
level below the app.

### If the window freezes

A freeze is not a crash: nothing raises, so nothing is logged by itself. A
watchdog thread handles that case. It asks Windows every ten seconds whether
the window is still answering — the same check behind "(Not Responding)" in a
title bar — and the moment it isn't, it writes `the window has stopped
responding to input` to the log along with a stack for every thread. It logs
again when the window recovers, so the entry says how long it lasted.

While the window is frozen the server usually still works, because it runs on
its own thread. From a terminal:

```bash
curl http://127.0.0.1:8000/diagnostics/stacks
```

A reply means the app is alive and the freeze is in the window. No reply at
all means the whole process is stuck, which is a different answer but still an
answer. Either way the output goes into the log too.

Reading a captured stack: if the main thread shows application frames, the
freeze is in this code. If it shows nothing but the pywebview message loop,
whatever is blocking sits below Python, in WebView2 or .NET.

## API

The UI is a thin layer over a REST API, with interactive docs at
`http://localhost:8000/docs` while the app is running. Roughly:

- Ingestion: `POST /sync`, `POST /sync/stream` (progress events),
  `POST /videos`, `POST /videos/bulk/stream`
- Library: `GET /videos` (search, filters, `downloaded=true`, `sort=played`),
  `GET|DELETE /videos/{id}`, `POST /videos/bulk/delete`
- Themes: `GET|POST /themes`, `PATCH|DELETE /themes/{name}`,
  `GET /themes/{name}/videos`, `POST /videos/{id}/themes`,
  `DELETE /videos/{id}/themes/{name}`, `POST /videos/themes/bulk`
- Rules: `GET|POST /rules`, `DELETE /rules/{id}`, `GET /rules/suggestions`,
  `POST /rules/apply`
- Playlists: `GET|POST /playlists`, `DELETE /playlists/{id}`,
  `GET|POST /playlists/{id}/videos`, `DELETE /playlists/{id}/videos/{video_id}`
- Embeddings and review: `POST /embeddings/build`, `POST /themes/auto-assign`,
  `POST /themes/discover`, `GET /review`
- Watching: `PATCH /videos/{id}/watch-state`, `POST /videos/{id}/play`,
  `POST /videos/{id}/position`, `GET /recommendations`
- Downloads: `POST|DELETE /videos/{id}/download`, `GET /downloads`,
  `POST /downloads/reveal`

## Development

```bash
pytest
```

The suite is offline: it fakes the network boundary, so it costs no API quota,
needs no cookies and does not require the ML extra.

[ARCHITECTURE.md](ARCHITECTURE.md) is the map of the code: what each module
owns, and why the non-obvious parts are the way they are. [ROADMAP.md](ROADMAP.md)
has the design constraints behind the whole thing (the API quota arithmetic,
why Watch Later needs yt-dlp, why recommendations are content-based) and what
is still on the list.

## What the first version was

Everything above is a rewrite. Before version 2 this repository was a single
`main.py`: a FastAPI service with no interface of its own, used from `/docs` or
curl, storing everything in a `videos.json` file at the repository root — one
dictionary of category name to a list of videos.

It could:

- add a video to a category by hand, or fetch its metadata with yt-dlp:
  title, channel, duration, thumbnail, view count, upload date
- expand a whole playlist into a category in one call
- list categories and their videos, update or delete a video, and toggle a
  watched flag, all addressed by URL string
- dump a playlist to its own `playlist_<uuid>.json` file, and import one back
  into a category

That was all of it. Categories were whatever you typed when adding the video,
so nothing was sorted for you. A video's identity was the exact URL string, so
the same video added once as `youtu.be/...` and once as `watch?v=...` counted
as two, in one category or across several. And once a video was in the file
there was nothing to do with it: no search, no themes, no recommendations, no
player, no offline copies, and no watch tracking beyond a boolean.

Version 2 kept the idea and replaced the implementation. SQLite instead of a
JSON file, the 11-character YouTube id instead of the URL, hybrid Data API +
yt-dlp ingestion, themes assigned automatically, recommendations from your own
votes, offline copies, and a UI to use it all from.
`scripts/migrate_videos_json.py` carries an old `videos.json` across, merging
the URL-variant duplicates it collected along the way. The old entry point is
still in the tree as `main.py` at the repository root; nothing imports it.

## License

Apache 2.0. See [LICENSE](LICENSE).
