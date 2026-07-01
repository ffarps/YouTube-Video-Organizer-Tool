"""Embedding-based theme assignment and discovery.

Prototype approach: each theme's prototype is the mean embedding of its
confirmed members (manual + rule assignments). Unthemed videos are assigned
to the nearest prototype when similarity clears a threshold; the rest wait in
the review queue with ranked suggestions.
"""
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np

from app import db
from app.categorize import embeddings

# Below this cosine similarity a suggestion isn't auto-assigned.
DEFAULT_ASSIGN_THRESHOLD = 0.45


def build_embeddings(conn: sqlite3.Connection, limit: int = 500) -> dict:
    """Embed videos that don't have a vector yet (needs the [ml] extra)."""
    videos = db.videos_missing_embedding(conn, limit)
    if videos:
        vectors = embeddings.embed_texts([embeddings.video_text(v) for v in videos])
        for video, vector in zip(videos, vectors):
            db.save_embedding(conn, video["id"], embeddings.to_blob(vector))
        conn.commit()
    counts = db.embedding_counts(conn)
    return {
        "embedded_now": len(videos),
        "embedded_total": counts["embedded"],
        "remaining": counts["total"] - counts["embedded"],
    }


def theme_prototypes(conn: sqlite3.Connection) -> Dict[str, np.ndarray]:
    """Mean (re-normalized) embedding per theme from confirmed members."""
    grouped: Dict[str, list] = defaultdict(list)
    for row in db.theme_member_embeddings(conn):
        vector = embeddings.from_blob(row["embedding"])
        if vector is not None:
            grouped[row["theme"]].append(vector)
    prototypes = {}
    for theme, vectors in grouped.items():
        mean = np.mean(vectors, axis=0)
        norm = np.linalg.norm(mean)
        if norm > 0:
            prototypes[theme] = mean / norm
    return prototypes


def suggest_for_video(
    video: dict, prototypes: Dict[str, np.ndarray], top_k: int = 3
) -> List[dict]:
    vector = embeddings.from_blob(video.get("embedding"))
    if vector is None or not prototypes:
        return []
    scored = sorted(
        ((theme, float(np.dot(vector, proto))) for theme, proto in prototypes.items()),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return [{"theme": t, "score": round(s, 3)} for t, s in scored[:top_k]]


def auto_assign(
    conn: sqlite3.Connection,
    threshold: float = DEFAULT_ASSIGN_THRESHOLD,
    limit: int = 1000,
) -> dict:
    """Assign unthemed embedded videos to their nearest prototype."""
    prototypes = theme_prototypes(conn)
    if not prototypes:
        return {"assigned": 0, "needs_review": 0, "detail": "no theme prototypes yet"}
    assigned = 0
    needs_review = 0
    for video in db.videos_without_themes(conn, with_embedding_only=True, limit=limit):
        suggestions = suggest_for_video(video, prototypes, top_k=1)
        if suggestions and suggestions[0]["score"] >= threshold:
            theme_id = db.get_or_create_theme(conn, suggestions[0]["theme"])
            db.assign_theme(
                conn, video["id"], theme_id, suggestions[0]["score"], "embedding"
            )
            assigned += 1
        else:
            needs_review += 1
    conn.commit()
    return {"assigned": assigned, "needs_review": needs_review}


def review_queue(conn: sqlite3.Connection, limit: int = 50) -> List[dict]:
    """Unthemed videos with ranked theme suggestions (when embeddings exist)."""
    prototypes = theme_prototypes(conn)
    queue = []
    for video in db.videos_without_themes(conn, limit=limit):
        suggestions = suggest_for_video(video, prototypes)
        video.pop("embedding", None)
        queue.append({"video": video, "suggestions": suggestions})
    return queue


def discover(
    conn: sqlite3.Connection,
    min_cluster_size: int = 5,
    scope: str = "unthemed",
) -> dict:
    """Cluster embedded videos to propose new themes (needs scikit-learn).

    Returns proposals only — nothing is created until POST /themes confirms.
    """
    try:
        from sklearn.cluster import HDBSCAN
    except ImportError as e:
        raise embeddings.EmbeddingUnavailable(
            'scikit-learn is not installed. Install the ML extra: pip install -e ".[ml]"'
        ) from e

    if scope == "unthemed":
        videos = db.videos_without_themes(conn, with_embedding_only=True, limit=10000)
    else:
        videos = [
            v
            for v in db.unwatched_candidates(conn)
            if v.get("embedding")
        ]
    vectors = [embeddings.from_blob(v["embedding"]) for v in videos]
    videos = [v for v, vec in zip(videos, vectors) if vec is not None]
    vectors = [vec for vec in vectors if vec is not None]
    if len(videos) < min_cluster_size * 2:
        return {"clusters": [], "detail": f"only {len(videos)} embedded videos in scope"}

    matrix = np.vstack(vectors)
    labels = HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(matrix)

    clusters = []
    for label in sorted(set(labels)):
        if label == -1:  # noise
            continue
        members = [v for v, l in zip(videos, labels) if l == label]
        clusters.append(
            {
                "suggested_name": _label_from_titles([m["title"] for m in members]),
                "size": len(members),
                "sample_titles": [m["title"] for m in members[:5]],
                "video_ids": [m["id"] for m in members],
            }
        )
    clusters.sort(key=lambda c: c["size"], reverse=True)
    return {"clusters": clusters, "noise": int((labels == -1).sum())}


def _label_from_titles(titles: List[str], top_n: int = 3) -> str:
    """Cheap cluster label: the most frequent informative title words."""
    from collections import Counter

    stopwords = {
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with",
        "how", "why", "what", "is", "are", "you", "your", "my", "this", "that",
        "de", "da", "do", "e", "o", "que", "um", "uma", "como", "para", "com",
    }
    words = Counter()
    for title in titles:
        for word in title.lower().split():
            word = word.strip(".,!?:;()[]|\"'")
            if len(word) > 2 and word not in stopwords and not word.isdigit():
                words[word] += 1
    return " ".join(w for w, _ in words.most_common(top_n)) or "unnamed"
