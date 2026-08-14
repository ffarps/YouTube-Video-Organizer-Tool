"""Content-based recommendation — the right choice for a single user
(collaborative filtering needs many users' interaction data).

Profile vector = weighted mean of embeddings of videos with watch signal:
thumbs up counts +1.0, thumbs down -0.6, skipped -0.3, and a watched video
with no thumb counts a mild +0.2 — that's the everyday "it was okay" case, so
it must not pull as hard as a deliberate vote. A recency half-life of ~180
days keeps the profile tracking current interests. Candidates are ranked by
cosine to the profile blended with a small recency boost, then MMR re-ranked
so the top of the feed isn't ten near-duplicates.

Cold start (no embedded watch history): theme-affinity counts + recency.
"""
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np

from app import db
from app.categorize.embeddings import from_blob

PROFILE_HALF_LIFE_DAYS = 180.0
RECENCY_HALF_LIFE_DAYS = 365.0
SIMILARITY_WEIGHT = 0.85
MMR_LAMBDA = 0.7


def _days_since(iso: Optional[str]) -> float:
    if not iso:
        return 3650.0
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 3650.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - then).total_seconds() / 86400)


def _signal_weight(status: str, rating: Optional[int]) -> float:
    if rating:  # a vote was cast; 0/None means it wasn't
        return 1.0 if rating > 0 else -0.6
    if status == "watched":
        return 0.2  # "it was okay": you finished it, but you didn't vote
    if status == "skipped":
        return -0.3
    return 0.0


def profile_vector(conn: sqlite3.Connection) -> Optional[np.ndarray]:
    total = None
    for row in db.watched_with_embeddings(conn):
        vector = from_blob(row["embedding"])
        if vector is None:
            continue
        weight = _signal_weight(row["status"], row["rating"])
        weight *= 0.5 ** (_days_since(row["watched_at"]) / PROFILE_HALF_LIFE_DAYS)
        if weight == 0.0:
            continue
        total = weight * vector if total is None else total + weight * vector
    if total is None:
        return None
    norm = np.linalg.norm(total)
    return total / norm if norm > 0 else None


def _mmr(candidates: List[dict], vectors: List[np.ndarray], limit: int) -> List[dict]:
    """Maximal Marginal Relevance: relevance minus similarity to already-picked."""
    picked: List[int] = []
    remaining = list(range(len(candidates)))
    while remaining and len(picked) < limit:
        best_idx = None
        best_score = -np.inf
        for i in remaining:
            redundancy = max(
                (float(np.dot(vectors[i], vectors[j])) for j in picked), default=0.0
            )
            # 4th power: near-duplicates (sim ~1) pay the full penalty while
            # merely-related videos (sim ~0.7 -> 0.24) pass almost freely
            score = (
                MMR_LAMBDA * candidates[i]["_score"]
                - (1 - MMR_LAMBDA) * max(0.0, redundancy) ** 4
            )
            if score > best_score:
                best_score, best_idx = score, i
        picked.append(best_idx)
        remaining.remove(best_idx)
    return [candidates[i] for i in picked]


def _cold_start(
    conn: sqlite3.Connection, candidates: List[dict], limit: int
) -> List[dict]:
    """Theme-affinity + recency ranking over every candidate — the fallback
    when there aren't enough embedded videos for a similarity-based feed."""
    affinity = db.theme_affinity(conn)
    total_affinity = sum(affinity.values()) or 1
    theme_map = db.themes_for_videos(conn, [v["id"] for v in candidates])
    for video in candidates:
        names = theme_map.get(video["id"], [])
        affinity_score = sum(affinity.get(n, 0) for n in names) / total_affinity
        recency = 0.5 ** (_days_since(video["published_at"]) / RECENCY_HALF_LIFE_DAYS)
        video["_score"] = 0.7 * affinity_score + 0.3 * recency
    candidates.sort(key=lambda v: v["_score"], reverse=True)
    return candidates[:limit]


def recommend(
    conn: sqlite3.Connection,
    theme: Optional[str] = None,
    max_duration_sec: Optional[int] = None,
    limit: int = 20,
) -> dict:
    candidates = db.unwatched_candidates(conn, theme, max_duration_sec)
    profile = profile_vector(conn)

    scored = []
    vectors = []
    if profile is not None:
        for video in candidates:
            vector = from_blob(video.get("embedding"))
            if vector is None:
                continue
            similarity = float(np.dot(profile, vector))
            recency = 0.5 ** (_days_since(video["published_at"]) / RECENCY_HALF_LIFE_DAYS)
            video["_score"] = SIMILARITY_WEIGHT * similarity + (1 - SIMILARITY_WEIGHT) * recency
            scored.append(video)
            vectors.append(vector)

    # Profile mode only wins if enough candidates are embedded to fill the feed;
    # otherwise a handful of stray embeddings would starve the whole library out.
    if profile is not None and len(scored) >= min(limit, len(candidates)):
        order = np.argsort([-v["_score"] for v in scored])[: max(limit * 4, 40)]
        shortlist = [scored[i] for i in order]
        shortlist_vecs = [vectors[i] for i in order]
        results = _mmr(shortlist, shortlist_vecs, limit)
        mode = "profile"
    else:
        results = _cold_start(conn, candidates, limit)
        mode = "cold_start"

    theme_map = db.themes_for_videos(conn, [v["id"] for v in results])
    output = []
    for video in results:
        video.pop("embedding", None)
        video["score"] = round(video.pop("_score"), 4)
        video["themes"] = theme_map.get(video["id"], [])
        output.append(video)
    return {"mode": mode, "recommendations": output}
