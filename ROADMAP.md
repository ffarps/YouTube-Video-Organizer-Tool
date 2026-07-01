# Roadmap

Local-first YouTube video organizer: playlist sync, ML theming, content-based
recommendations. One SQLite file, no cloud dependency beyond YouTube.

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
  users; a solo user has none. Watch state + ratings tracked in-app are the
  feedback signal (YouTube won't expose watch history via API).

## Phase 0 — Foundation ✅
Package layout (`app/` with `ingest`, `categorize`, `api` modules), pinned
deps in `pyproject.toml`, SQLite schema (`app/db.py`: videos, themes,
video_themes many-to-many, watch_state, playlists, playlist_items),
`scripts/migrate_videos_json.py` for the legacy category-keyed videos.json
(merges cross-category URL-variant duplicates). Legacy one-off scripts deleted;
the markdown importer lives on as `app/ingest/markdown.py`.

## Phase 1 — Reliable ingestion ✅
`app/ingest/youtube_api.py` (playlistItems paging + videos.list batching),
`app/ingest/ytdlp.py` (flat listing, WL via cookies, keyless fallback),
`app/ingest/sync.py` orchestration. API: `POST /sync`, `POST /videos`,
`GET /themes`, `GET /themes/{name}/videos`, `GET|DELETE /videos/{id}`,
`PATCH /videos/{id}/watch-state`. Re-sync is idempotent and only fetches
metadata for unknown videos. Rule-based themes assigned at ingest
(`app/categorize/rules.py`: word-boundary, scored, multi-label — fixes the
legacy substring matcher where "ai" matched "airplane").

## Phase 2 — Theming ✅
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

## Phase 3 — Recommendation ✅
`app/recommend/engine.py`: profile vector = rating- and recency-weighted mean
of watched-video embeddings (rating 1 → −0.6 … 5 → +1.0, skipped −0.3,
180-day half-life); unwatched candidates ranked by cosine blended with a
recency boost, then MMR re-ranked (redundancy⁴ penalty so near-duplicates pay
full price while related videos pass). Cold start without embeddings falls
back to theme-affinity counts. `GET /recommendations?theme=&max_duration=&limit=`.

## Phase 4 — Frontend ✅
`static/index.html` (vanilla JS, no build step) served at `/`: sync box,
theme sidebar, video cards with watch/rate stars, "What should I watch?" feed
with time-budget filter, review queue with one-click theme confirmation,
Embed/Auto-theme maintenance buttons.

## Later ideas
- Consolidate the 20 legacy categories using discovery clusters (AI/
  AI_Development and Clothes/Watches overlap).
- Scheduled re-sync; export back to a YouTube playlist; LLM cluster naming.
