"""Embedding layer for theming and recommendations.

Uses a multilingual model (the library mixes PT and EN titles). The model is
loaded lazily so the base install works without the [ml] extra — everything
except embedding computation runs on plain numpy.

Vectors are L2-normalized float32, stored as raw bytes in videos.embedding,
so cosine similarity is a dot product.
"""
from typing import List, Optional

import numpy as np

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_model = None


class EmbeddingUnavailable(Exception):
    pass


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbeddingUnavailable(
                "sentence-transformers is not installed. "
                'Install the ML extra: pip install -e ".[ml]"'
            ) from e
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def video_text(video: dict) -> str:
    """Compose the text that represents a video for embedding."""
    description = (video.get("description") or "")[:500]
    return " | ".join(
        filter(
            None,
            [
                video.get("title"),
                video.get("channel_title"),
                " ".join((video.get("tags") or [])[:15]),
                description,
            ],
        )
    )


def embed_texts(texts: List[str]) -> np.ndarray:
    """Encode texts to L2-normalized float32 vectors, shape (n, dim)."""
    model = _get_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vectors, dtype=np.float32)


def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)
