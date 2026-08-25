# Roadmap

Watchlog — local YouTube backlog organizer: playlist sync, theming,
content-based recommendations, offline copies. One SQLite file, no cloud
dependency beyond YouTube.

## Design constraints

- **Quota:** YouTube Data API gives 10,000 free units/day. `playlistItems.list`
  and `videos.list` cost 1 unit per 50 items; `search.list` costs 100 and is
  never used. A personal tool stays under 1% of quota.
- **Watch Later:** the Data API cannot read the WL playlist (blocked since
  2016). yt-dlp with browser cookies can. Hence hybrid ingestion: yt-dlp
  flat-lists private playlists (ids only, fast), the Data API batch-enriches;
  public playlists/channels go straight through the API. Without an API key
  everything still works via yt-dlp, just slower.
- **Identity:** the 11-char YouTube video id is the primary key. URL variants
  (youtu.be, watch?v=, shorts) canonicalize to the same video.
- **Recommendations are content-based.** Collaborative filtering needs many
  users; a solo user has none. Watch state + thumbs tracked in-app are the
  feedback signal (YouTube won't expose watch history via API).
- **Downloads are single-video.** Bulk archiving is against YouTube's ToS and
  the disk arithmetic doesn't work anyway. `app/ingest/download.py` is the only
  place the app writes media bytes, and it takes one id per call.

## Phase 0 — Foundation (done)

Package layout (`app/` with `ingest`, `categorize`, `api` modules), pinned
deps in `pyproject.toml`, SQLite schema (`app/db.py`: videos, themes,
video_themes many-to-many, watch_state, playlists, playlist_items),
`scripts/migrate_videos_json.py` for the legacy category-keyed videos.json
(merges cross-category URL-variant duplicates). Legacy one-off scripts deleted;
the markdown importer lives on as `app/ingest/markdown.py`.

## Phase 1 — Reliable ingestion (done)

`app/ingest/youtube_api.py` (playlistItems paging + videos.list batching),
`app/ingest/ytdlp.py` (flat listing, WL via cookies, keyless fallback),
`app/ingest/sync.py` orchestration. API: `POST /sync`, `POST /videos`,
`GET /themes`, `GET /themes/{name}/videos`, `GET|DELETE /videos/{id}`,
`PATCH /videos/{id}/watch-state`. Re-sync is idempotent and only fetches
metadata for unknown videos. Rule-based themes assigned at ingest
(`app/categorize/rules.py`: word-boundary, scored, multi-label — fixes the
legacy substring matcher where "ai" matched "airplane").

## Phase 2 — Theming (done)

Embedding layer in `app/categorize/embeddings.py` + `themes.py`:
`paraphrase-multilingual-MiniLM-L12-v2` (PT/EN mix) over
title+channel+tags+description, L2-normalized float32 in `videos.embedding`.
Lazy model load — the base install works without the `[ml]` extra.
`POST /embeddings/build` embeds new videos; `POST /themes/auto-assign` puts
unthemed videos on the nearest theme prototype (mean of manual+rule members)
above a 0.45 cosine threshold; the rest surface in `GET /review` with ranked
suggestions, confirmed via `POST /videos/{id}/themes`. `POST /themes/discover`
clusters with sklearn HDBSCAN and proposes labeled clusters (confirm with
`POST /themes`).

## Phase 3 — Recommendation (done)

`app/recommend/engine.py`: profile vector = vote- and recency-weighted mean
of watched-video embeddings (thumbs up +1.0, thumbs down −0.6, skipped −0.3,
watched-but-unvoted +0.2, 180-day half-life); unwatched candidates ranked by
cosine blended with a recency boost, then MMR re-ranked (redundancy⁴ penalty so
near-duplicates pay full price while related videos pass). Cold start without
embeddings falls back to theme-affinity counts.
`GET /recommendations?theme=&max_duration=&limit=`.

## Phase 4 — Frontend (done)

`static/index.html` (vanilla JS, no build step) served at `/`: sync box,
theme sidebar, video cards with watch/thumbs buttons, "What should I watch?"
feed with time-budget filter, review queue with one-click theme confirmation,
Embed/Auto-theme maintenance buttons. Dark, Light and Black & white colour
themes, stored in localStorage.

## Phase 5 — Curation (done)

The parts that make a few-thousand-video library usable rather than stored.
Word-boundary browse search (`search_hit` in `app/db.py`, not LIKE) with
unwatched / no-theme / offline filters and eight sort orders; multi-select for
theming or deleting in bulk (`POST /videos/themes/bulk`,
`POST /videos/bulk/delete`); bulk paste of many links (`POST
/videos/bulk/stream`); user playlists alongside themes (`/playlists`, synced
ones read-only); theme rename and delete with `theme_aliases` so the rule
engine can't resurrect an old name; user-defined rules (`/rules`) with
exclusive matching, channel→theme suggestions learned from what is already
themed, and `POST /rules/apply` to reconcile the whole library. Ratings
migrated from 5 stars to thumbs, since a star scale asked a question the user
did not have an answer to.

## Phase 6 — Offline copies (done)

`app/ingest/download.py` (yt-dlp, format selection, ffmpeg-aware degradation)
and `app/downloads.py` (thread per job, live byte counts in memory, durable
status in the `downloads` table, stale-job reset at startup). Files are served
by a `StaticFiles` mount so Range requests work and the local `<video>` can
seek. Rewatches counted per player open (`play_count`, `last_played_at`),
deliberately independent of watched state and thumbs, plus an Offline tab and a
`downloaded` filter — saving a video turns out to be the strongest favourite
signal in the app.

## Phase 7 — Desktop shell (done)

`app/desktop.py`: uvicorn on a daemon thread, GUI on the main thread, so
closing the window stops the server. pywebview/WebView2 where available,
Chromium `--app` as a zero-dependency fallback. Console-less launch end to end
(`Watchlog.vbs` → `start.bat` → `pythonw -m app.desktop`), with a log file
standing in for the stdout that no longer exists.

## Later ideas

- Consolidate the legacy categories using discovery clusters (AI/AI_Development
  and Clothes/Watches overlap).
- Scheduled re-sync in the background instead of on demand.
- Export a theme or playlist back to a real YouTube playlist.
- LLM naming for discovered clusters.
- Detecting videos gone private or deleted upstream is
  `scripts/find_unavailable.py` — a script rather than a feature, because a
  2,452-video library had three of them.
