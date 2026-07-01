"""Rule-based theme assignment — the transparent baseline layer.

Improvements over the legacy categorize_videos.py:
- word-boundary regex instead of raw substring ("ai" no longer matches
  "airplane", "watch" no longer matches "watching")
- score-based multi-label instead of first-match-wins
- matches against title + description + channel + tags, not title only

The embedding layer (Phase 2) handles everything these rules miss.
"""
import re
from typing import Dict, List, Tuple

# Carried over from the legacy script; consolidation happens in Phase 2.
THEME_KEYWORDS: Dict[str, List[str]] = {
    "Podcasts": ["podcast", "interview", "lex fridman", "episode"],
    "Clothes": ["clothes", "style", "fashion", "wardrobe", "outfit"],
    "Productivity": ["productivity", "focus", "career", "organize", "routine",
                     "time management", "motivation"],
    "Self_Help": ["self-help", "addiction", "confidence", "social media",
                  "doom scrolling", "mental", "psychology"],
    "Cameras": ["camera", "photography", "dslr", "lens"],
    "Tech": ["tech", "technology", "server", "hardware", "nas", "gpu", "linux",
             "bsd", "open source", "hosting", "vps", "truenas", "nvidia"],
    "CyberSecurity": ["cybersecurity", "security", "hack", "scam", "phishing",
                      "privacy"],
    "Web_Development": ["web development", "website", "html", "css",
                        "javascript"],
    "Software_Development": ["software", "programming", "code", "engineer",
                             "git", "deploy"],
    "AI": ["ai", "artificial intelligence", "machine learning",
           "deep learning", "karpathy", "neural", "cuda", "llm"],
    "Personal_Finances": ["finance", "money", "budget", "invest", "finances"],
    "Games": ["game", "gaming", "playstation", "xbox", "nintendo"],
    "Nutrition": ["nutrition", "diet", "food", "health"],
    "Guitar": ["guitar", "song", "acoustic", "musician"],
    "Watches": ["watch", "watches", "timepiece"],
    "Workouts": ["workout", "exercise", "fitness", "gym", "training"],
    "Shows_and_Animes": ["anime", "series", "tv show"],
    "to_think": ["philosophy", "debate", "singularity"],
}

_COMPILED: Dict[str, List[re.Pattern]] = {
    theme: [
        re.compile(r"(?<![\w-])" + re.escape(kw) + r"(?![\w-])", re.IGNORECASE)
        for kw in keywords
    ]
    for theme, keywords in THEME_KEYWORDS.items()
}


def assign_themes(video: dict) -> List[Tuple[str, float]]:
    """Return [(theme_name, confidence), ...] for every theme that matches."""
    text = " ".join(
        filter(
            None,
            [
                video.get("title"),
                video.get("description"),
                video.get("channel_title"),
                " ".join(video.get("tags") or []),
            ],
        )
    )
    # URLs in descriptions are keyword soup ("watch?v=" matches "watch")
    text = re.sub(r"https?://\S+", " ", text)
    results: List[Tuple[str, float]] = []
    for theme, patterns in _COMPILED.items():
        hits = sum(1 for p in patterns if p.search(text))
        if hits:
            confidence = min(0.95, 0.6 + 0.15 * (hits - 1))
            results.append((theme, confidence))
    results.sort(key=lambda pair: pair[1], reverse=True)
    return results
