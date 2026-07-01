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

## Phase 2 — Theming (next)
- Embedding layer: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (PT/EN mix) over title+description+channel+tags, stored in `videos.embedding`.
- Assign by cosine similarity to theme prototypes (mean of confirmed members).
- HDBSCAN discovery pass to propose consolidating the 20 legacy categories
  (~10 real themes; AI/AI_Development and Clothes/Watches overlap) and to name
  new clusters (optionally LLM-labeled).
- Review queue endpoint for low-confidence assignments; confirmations become
  prototype members.

## Phase 3 — Recommendation
- Profile vector = rating- and recency-weighted mean of embeddings of
  watched/liked videos; rank unwatched by cosine.
- MMR re-rank for diversity; filters: theme, time budget
  (`duration_sec <= X`), recency boost.
- Cold start: theme-affinity counts from watch_state.
- `GET /recommendations?theme=&max_duration=&limit=`.

## Phase 4 — Frontend
One `static/index.html` (vanilla JS, no build step) served by FastAPI: sync
box, theme browser, watch/rate buttons, recommendation feed, review queue.
