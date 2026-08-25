# Architecture

Watchlog is a local FastAPI app that syncs YouTube playlists into SQLite,
themes the videos, recommends what to watch and can save copies to disk. This
file is the map of the code: what each module owns, and the decisions behind
the parts that are not obvious from reading them. See readme.md for what the
app does from the outside, and ROADMAP.md for the design constraints.

## Commands

```bash
pip install -e ".[dev,desktop]"  # install (Python >= 3.11); desktop = pywebview window
pip install -e ".[dev,ml]"       # + torch/sentence-transformers/sklearn (Phase 2/3 ML)
pytest                           # run tests (quota-free, no network, no torch needed)
winget install Gyan.FFmpeg       # optional: unlocks downloads above 360p
python -m app.desktop            # app in its own window (no console, no browser)
uvicorn app.main:app --reload    # same app as a web page: :8000/, API docs at /docs
start.bat                        # Windows one-click: installs deps if missing, opens the window
start.bat browser                # ...the old way instead: console + browser + --reload
python scripts/migrate_videos_json.py [videos.json] [organizer.db]  # legacy import
```

## Architecture

- `app/config.py` — pydantic-settings; reads `.env` (see `.env.example`).
  `YOUTUBE_API_KEY` optional: with it, ingestion uses the Data API (fast,
  50 videos/quota unit); without it, everything falls back to yt-dlp.
- `app/db.py` — sqlite3 schema + all queries. Video identity is the 11-char
  YouTube id. `video_themes` is many-to-many (a video can have several themes).
  Browse search (`list_videos`) uses the `search_hit` SQL function, not LIKE:
  every typed word must match at a word start (3+ chars match prefixes,
  shorter ones whole words), URLs are stripped from descriptions first, and
  title/channel hits sort above description-only ones. `list_videos` also
  takes a `channel`/`channel_id` filter, matched as **id OR name**: a yt-dlp
  row can carry the name and no id, and a channel that renames leaves its old
  name on everything ingested before the rename, so either test alone shows
  half the channel. It is not the same query as typing the name in the search
  box, which matches descriptions too and so drags in other channels.
- `app/ingest/urls.py` — URL → id canonicalization; `classify_url` decides
  video/playlist/channel/watch_later.
- `app/ingest/sync.py` — orchestrator; only fetches metadata for ids not
  already in the DB, so re-syncs are cheap and idempotent.
- `app/ingest/ytdlp.py` — flat listing + keyless fallback. Watch Later is
  ONLY reachable this way (Data API blocks WL), needs
  `YTDLP_COOKIES_BROWSER` set.
- `app/ingest/download.py` — the ONLY place the app writes media bytes;
  everything else in `ingest` is metadata-only (`skip_download`). Single
  video per call, no bulk path (ToS + disk). Above 360p YouTube splits
  audio into its own stream, so `_format_selector` silently degrades to the
  progressive format when ffmpeg is missing rather than failing — check
  `ffmpeg_available()` before promising a height. `max_height=None` means
  "best this video has". **Codec preference lives in `FORMAT_SORT`, never
  in the selector**: YouTube only serves H.264 up to 1080p, so naming
  `vcodec^=avc1` in the selector made a request for 4K match the 1080p
  branch first and silently return 1080p. `format_sort` ranks by `res`
  first and uses h264 only to break ties at the same height.
  **Two different things both surface as a 403 on every stream, and they are
  not the same problem.** The first is a missing JavaScript engine: YouTube's
  stream URLs carry a signature that has to go back through their player JS to
  be valid, and without an engine yt-dlp takes a path it now calls deprecated
  and produces URLs the CDN refuses. Only `deno` is enabled upstream by
  default, so an installed Node goes unused unless named — `js_runtime` finds
  deno/node/bun the way `ffmpeg_available` finds ffmpeg, and `yt-dlp-ejs` (a
  dependency) is the solver script run inside it. Getting this wrong is
  invisible: it looks exactly like the second problem and no message tells them
  apart. The second is the proof-of-origin token. YouTube gates its default
  player client's stream URLs behind a proof-of-origin token: metadata resolves
  and the full ladder up to 2160p is listed, then every download is refused, and
  `extractor_retries` only re-resolves URLs that get refused the same way.
  Upgrading yt-dlp does not fix it. Measured on 2026-08-18: default → 403;
  `android` → downloads, 360p ceiling on every video tried; `tv` → "DRM
  protected"; `ios`, `web_safari`, `mweb`, `tv_simply`, `web_embedded` → no
  usable formats at all. `android_vr` is the trap worth knowing about — it lists
  the whole ladder to 2160p and then 403s on every stream, so it looks like the
  answer right up until nothing downloads; `tv_downgraded` offers no formats,
  `web_creator` demands a sign-in and `web_music` calls the video unavailable.
  **Cookies are the way out, together with a JS runtime — and neither
  works without the other.** `YTDLP_COOKIES_BROWSER` pointed at a signed-in
  profile restores the full ladder: measured 2026-08-18, 1080p and 1440p
  (352 MB) downloading normally. Cookies *alone* were tested first and looked
  like they made things worse — every client returning no usable formats — but
  that measurement was taken before `js_runtime` existed, when nothing worked
  anyway; a wrong conclusion drawn from a broken configuration, and it cost
  most of an evening chasing token providers that were never the problem.
  Retest a dismissed setting after fixing anything underneath it. The value
  takes yt-dlp's `browser[:profile]` form via `cookie_spec`, which matters for
  Firefox forks: yt-dlp only knows a fixed list of browser names, so Zen is
  unreachable as `zen`, but it is Firefox underneath with the same unencrypted
  `cookies.sqlite` — `firefox:<profile dir>` reads it. Chromium-family
  profiles are a different matter (app-bound encryption) and were not needed
  here. `YTDLP_PLAYER_CLIENT` is then left **blank**: `android` was the
  no-cookies fallback and costs the whole ladder above 360p.
  The working client rotates, so this is a knob rather than a hardcoded
  default, and there is
  deliberately **no automatic fallback to `android`** — quietly turning a
  request for 1080p into 360p is the same silent downgrade `FORMAT_SORT` exists
  to prevent. `_explain` turns the raw 403 into the settings that address it,
  because nothing about "HTTP Error 403: Forbidden" points at any of them. The
  cap is advertised as well as enforced: `client_ceiling` folds it into
  `effective_height`, so a machine set to `android` offers 360p in the UI
  instead of accepting a request for 4K and returning 360p.
- `app/downloads.py` — job manager: thread per download so the request
  returns immediately, live byte counts in memory (`_active`), durable
  status in the `downloads` table. `reset_stale` runs in the lifespan
  because nothing survives a restart. `media_file` is the filesystem
  boundary — it refuses paths that escape MEDIA_PATH. A re-download
  removes the old file only *after* the new one lands, and restores the
  previous row on failure (`db.restore_download`): YouTube 403s on the
  adaptive streams often enough that deleting first regularly traded a
  working copy for nothing. `_clean_partials` sweeps the `.part`/`.ytdl`
  leftovers a failed run drops, which no table points at. `reveal` opens
  the media folder in the OS file manager (explorer /select on Windows;
  explorer exits 1 even on success, so its status is not checked).
- `app/categorize/rules.py` — word-boundary, scored, multi-label keyword
  themes applied at ingest (URLs stripped from text first — "watch?v=" used
  to match the Watches theme). Evidence is field-weighted (title/channel
  1.0, tags 0.5, description ⅓ — tags and descriptions are keyword spam).
  One mention anywhere qualifies (`MIN_EVIDENCE` = ⅓); noise is filtered
  *relatively* instead — a theme is dropped if it has less than `KEEP_RATIO`
  (0.6) of the winner's evidence, unless it reaches `STRONG_EVIDENCE` (1.0,
  a title/channel hit), which always survives. Requiring a full unit to
  qualify was what left ~40% of a playlist untagged. `rules.reapply`
  reconciles: prunes rule-sourced assignments the current rules no longer
  justify, never touches manual/embedding ones. User-defined rules (`theme_rules` table,
  managed from the UI Rules tab via `/rules`) add expression → theme
  mappings on top; an *exclusive* rule gives matching videos ONLY that
  theme. `rules.reapply` (POST `/rules/apply`) re-runs everything over
  stored videos.
- `app/categorize/embeddings.py` — lazy sentence-transformers load
  (multilingual MiniLM); vectors are L2-normalized float32 blobs, so cosine
  = dot product. Everything else runs on plain numpy without the [ml] extra.
- `app/categorize/themes.py` — prototype-based auto-assign (threshold 0.45),
  review queue, HDBSCAN discovery.
- `app/recommend/engine.py` — profile vector from watch_state (thumbs up +1.0,
  down −0.6, skipped −0.3, watched-but-unvoted +0.2, 180-day half-life),
  cosine + recency, MMR with a redundancy⁴ penalty targeting near-duplicates.
  `watch_state.rating` is a thumb (−1/+1), not a star score — NULL on a
  watched video is the deliberate "it was okay" tier, which is why it must
  stay a weak signal. `db.SCHEMA_VERSION` guards the one-way 5-star →
  thumbs migration in `init_db` (it can't be idempotent: a stored 1 means
  one star before it runs and thumbs up after) — so **never gate a new
  migration on `SCHEMA_VERSION`**; bumping it re-runs the ratings step and
  flips every thumbs up to thumbs down. `_migrate_play_counters` shows the
  pattern: guard on `PRAGMA table_info` instead.
- `watch_state.play_count` / `last_played_at` — rewatches, counted per
  player open (`POST /videos/{id}/play`), deliberately independent of
  `status` and `rating`. Reaching for something again is what identifies a
  favourite, and it must not clear a thumb or re-mark anything watched.
  Offline copies are the same idea from the other side, so `list_videos`
  and `videos_by_theme` take a `downloaded` filter (only `status='done'`
  counts — a queued or failed row has no file behind it).
- `app/api/routes.py` — all endpoints; DB connection lives on
  `app.state.db` (set in the lifespan in `app/main.py`). Downloaded files
  are served by a `StaticFiles` mount at `/media`, NOT a FileResponse
  endpoint — StaticFiles answers Range requests, which is what makes
  seeking work in a local `<video>`. Deleting a video unlinks its media
  *before* the row, since `ON DELETE CASCADE` takes the row and leaves the
  file orphaned.
- `app/logs.py` — one rotating file (`LOG_PATH`, default `logs/`) that
  everything writes to, set up from the lifespan so both entry points get it.
  Opened in **append** mode: truncating at startup empties the log exactly
  when it's needed, since the first move after a crash is relaunching. Three
  things that normal logging misses are hooked here — `sys.excepthook`,
  `threading.excepthook` (a download thread's traceback would otherwise go
  nowhere) and `faulthandler` writing to its own always-open file (a WebView2
  or pythonnet segfault kills the interpreter before any Python handler
  runs). `capture_std_streams` swaps `sys.stdout`/`stderr` for line-buffered
  log adapters when they are None under `pythonw`. Uvicorn is started with
  `log_config=None` so its loggers propagate here instead of onto a stdout
  that doesn't exist. `dump_stacks` walks `sys._current_frames()` rather than
  using `faulthandler.dump_traceback`, which needs a real fd and so can't be
  captured into a string.
- Freeze diagnosis: a hang raises nothing, so `desktop._start_watchdog` polls
  `IsHungAppWindow` (what puts "(Not Responding)" in a title bar) every 10s
  and dumps every thread's stack the moment the window stops answering, plus
  a heartbeat line every 5 minutes so a gap in the log dates the freeze.
  `GET /diagnostics/stacks` is the same dump on demand — the server thread
  keeps answering when the UI doesn't, which is what makes it reachable from
  outside a frozen app. Access logging is on in the window for the same
  reason: the last few request lines say what the page was doing when it
  stopped.
- `app/desktop.py` — the desktop shell, and the only entry point that owns a
  window. Uvicorn goes on a daemon thread (that's supported: uvicorn skips
  its signal handlers off the main thread) and the GUI keeps the main
  thread; closing the window sets `should_exit`, so there is no orphan
  server. Two window backends: pywebview/WebView2 (the `desktop` extra), and
  Chromium `--app` with its own `--user-data-dir` as a zero-dependency
  fallback — the private profile is what makes the browser process a child
  we can wait on. Everything here is shaped by there being no console:
  `_redirect_streams` fixes `sys.stdout is None` under `pythonw.exe` before
  uvicorn's first log line kills the process, failures go to a MessageBox,
  and `reveal()` runs `show()` + `restore()` because Windows applies the
  launcher's show state to the first window a process opens — a hidden or
  minimized launch is otherwise indistinguishable from a crash. pywebview's
  `private_mode` default would drop the localStorage colour theme every run,
  hence `storage_path`. **WebView2 has no fullscreen of its own** — its
  backend implements none, so `requestFullscreen()` grows the element to fill
  the webview and stops at the window's edge, title bar included. The window
  is the only thing that can go fullscreen, so `_bind_fullscreen` exposes
  `pywebview.api.set_fullscreen` and the page drives it from
  `fullscreenchange` (`syncShellFullscreen`); nothing reports the window's own
  state, so the desired one is tracked on the Python side. pywebview's
  `toggle_fullscreen` is **not** what does the move on Windows: it maximizes
  the form, and maximizing means the *work area* — the screen minus the
  taskbar, which then stays drawn over the video, in front of a window that is
  also still in the ordinary z-order band. `_fullscreen_win32` places it on
  the monitor's full rect and lifts it into the topmost band instead, saving
  the `WINDOWPLACEMENT` and window style to come back to. Topmost only holds
  while the window is in front — otherwise alt-tabbing away leaves a video
  sitting over whatever you switched to — and nothing reports a lost
  activation to this process, so `follow_focus` polls `GetForegroundWindow`
  for the duration and moves only the z-order, never the geometry. The
  Chromium fallback and a plain browser need none of this and skip it —
  `window.pywebview` isn't there.
- Launchers: `Watchlog.vbs` (silent — wscript never shows a console, a batch
  file always does) → `start.bat` for the one-time install → `pythonw -m
  app.desktop`. `create-desktop-shortcut.bat` points the Desktop icon at
  wscript + the .vbs. In the .vbs the dependency probe runs with window style
  0 (hide the console) but the app **must** use style 1: style 0 propagates
  to the app's own window and it opens invisible.
- `static/index.html` — the whole frontend, vanilla JS, served at `/`.
  No build step; talks to the API with fetch. Voting never touches playback:
  `#votePill` is a corner nudge in the last ~12s that auto-fades, and
  `#rateCard` (the full panel + up-next countdown) only appears once the
  video has actually ended. `#nextPill` leads both from ~20s out (`lead` in
  `startRateWatch`, floored at 8s so short clips still get a warning): it
  counts the *video* down and names what follows, so the panel at the end is
  never a surprise. Deliberately no auto-fade, unlike the vote pill — its job
  is to still be there when the video ends — and it withdraws if you seek
  back, since `remaining` is recomputed every tick. Both corner overlays sit
  in `#cornerStack`, one flex column, so they stack instead of landing on top
  of each other — and `syncOverlayLayer` lifts that column into the **top
  layer** as a manual popover whenever the *iframe* is the fullscreen element.
  YouTube's own button raises the iframe, and the overlays are its siblings,
  so they would be painted underneath it; nothing stacks above a fullscreen
  element except the top layer itself, and a popover gets there without
  touching YouTube's fullscreen (re-requesting fullscreen on `#playerHost`
  also works and needs no fresh gesture, but it leaves YouTube's own exit
  button doing nothing visible). The `popover` attribute is added only for the
  duration: worn permanently, the UA's popover styles — `inset: 0`,
  `margin: auto`, its own border and background — leak into the ordinary
  layout. The player swaps between the YouTube iframe and
  `#localPlayer` (a `<video>` on `/media`) — everything downstream reads
  `playerClock()` instead of branching, and hiding the iframe needs an
  explicit `iframe[hidden]` rule because `#playerHost iframe` outranks the
  UA's `[hidden]`. Fullscreen has **two** routes and both end at
  `syncShellFullscreen`: YouTube's own button (a request from inside the
  iframe is granted, and this document's `fullscreenchange` fires with the
  iframe as `fullscreenElement`), and `#playerFs` / the `f` key, which raise
  `#playerHost`. Prefer the latter — raising the host rather than the iframe
  keeps `#votePill` and `#rateCard` on screen instead of stranded behind it —
  and note `#playerHost:fullscreen` must clear the 16/9 `aspect-ratio`, or the
  host keeps a 16/9 box on a screen that isn't. Neither route does anything
  for the window on its own; the desktop shell is what makes it the whole
  screen. Download state polls `/downloads` only while something is in flight
  and repaints individual cards, never the grid (scroll position). The channel
  name on a card is a link into a third browse scope, `activeChannel`, sitting
  alongside `activeTheme` and `activePlaylist` — all three are mutually
  exclusive, and a search clears whichever is set because search always widens
  to the whole library. It scopes the grid to **what the library already
  holds**: clicking a channel deliberately does *not* ask YouTube what else
  that channel has posted. Listing a channel's uploads live would put cards on
  screen with no theme, watch state, rating or offline copy — a second kind of
  card that every downstream feature would then have to special-case — and
  ingesting them instead means a 4,000-video channel swamping a library that
  is meant to be curated. Syncing a channel URL is still the way in, one
  deliberate act rather than a side effect of a click.

## Conventions

- Pydantic v2 only (`model_dump`, no `.dict()` overrides).
- Never call `search.list` (100 quota units); reads are 1 unit per 50 items.
- Tests fake the network boundary (`sync._fetch_metadata`,
  `ytdlp.list_playlist`, `download.download_video`) — keep them quota-free
  and offline. Anything that starts a download must poll like the UI does;
  the job runs on a background thread.
- Never add a bulk-download path. One video per request is a deliberate
  limit, not an oversight.
- Secrets in `.env` (gitignored); `.env.example` documents the keys.
