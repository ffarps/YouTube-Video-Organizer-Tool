"""Rule-based theme assignment — the transparent baseline layer.

Improvements over the legacy categorize_videos.py:
- word-boundary regex instead of raw substring ("ai" no longer matches
  "airplane", "watch" no longer matches "watching")
- score-based multi-label instead of first-match-wins
- matches against title + description + channel + tags, not title only

The embedding layer (Phase 2) handles everything these rules miss.

On top of the built-in keywords, users can define their own rules (stored in
the theme_rules table): an expression that maps to a theme. An *exclusive*
rule suppresses everything else — a matching video gets only that theme.
"""
import re
import sqlite3
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from app import db

# Carried over from the legacy script; consolidation happens in Phase 2.
THEME_KEYWORDS: Dict[str, List[str]] = {
    "Podcasts": ["podcast", "interview", "lex fridman", "episode"],
    "Fashion": ["clothes", "style", "fashion", "wardrobe", "outfit"],
    "Productivity": ["productivity", "focus", "career", "organize", "routine",
                     "time management", "motivation"],
    "Self-Help": ["self-help", "addiction", "confidence", "social media",
                  "doom scrolling", "mental", "psychology"],
    "Photography": ["camera", "photography", "dslr", "lens"],
    "Tech": ["tech", "technology", "server", "hardware", "nas", "gpu", "linux",
             "bsd", "open source", "hosting", "vps", "truenas", "nvidia"],
    "Cybersecurity": ["cybersecurity", "security", "hack", "scam", "phishing",
                      "privacy"],
    "Web Development": ["web development", "website", "html", "css",
                        "javascript"],
    "Software Development": ["software", "programming", "code", "engineer",
                             "git", "deploy"],
    "AI": ["ai", "artificial intelligence", "machine learning",
           "deep learning", "karpathy", "neural", "cuda", "llm"],
    "Personal Finance": ["finance", "money", "budget", "invest", "finances"],
    "Gaming": ["game", "gaming", "playstation", "xbox", "nintendo"],
    "Nutrition": ["nutrition", "diet", "food", "health"],
    "Guitar": ["guitar", "song", "acoustic", "musician"],
    "Watches": ["watch", "watches", "timepiece"],
    "Fitness": ["workout", "exercise", "fitness", "gym", "training"],
    "Shows & Anime": ["anime", "series", "tv show"],
    "Philosophy": ["philosophy", "debate", "singularity"],
}

# Evidence weight per field. Titles are written for humans; tags and
# descriptions are written for the algorithm (keyword stuffing), so keywords
# found only there count fractionally.
FIELD_WEIGHTS = (
    ("title", 1.0),
    ("channel", 1.0),
    ("tags", 0.5),
    ("description", 1 / 3),
)

# One mention anywhere is enough to put a theme in the running: most videos
# never repeat their topic outside the description, and demanding a full unit
# of evidence left a third of every playlist untagged.
MIN_EVIDENCE = 1 / 3

# A full unit of evidence is a title or channel hit: strong on its own, where
# anything less is description or tag text written for the algorithm.
STRONG_EVIDENCE = 1.0

# The winner is the video's theme; a runner-up only joins it with strong
# evidence of its own ("AI podcast interview" is a podcast and genuinely about
# AI), and never more than one. Keeping every theme near the winner is what
# used to hand a sponsor-heavy description five themes at once.
MAX_KEYWORD_THEMES = 2

_COMPILED: Dict[str, List[re.Pattern]] = {
    theme: [
        re.compile(r"(?<![\w-])" + re.escape(kw) + r"(?![\w-])", re.IGNORECASE)
        for kw in keywords
    ]
    for theme, keywords in THEME_KEYWORDS.items()
}


def _matchable_fields(video: dict) -> Dict[str, str]:
    return {
        "title": video.get("title") or "",
        "channel": video.get("channel_title") or "",
        "tags": " ".join(video.get("tags") or []),
        # URLs in descriptions are keyword soup ("watch?v=" matches "watch")
        "description": re.sub(r"https?://\S+", " ", video.get("description") or ""),
    }


def _matchable_text(video: dict) -> str:
    return " ".join(_matchable_fields(video).values())


def _compile_expression(expression: str) -> re.Pattern:
    return re.compile(
        r"(?<![\w-])" + re.escape(expression) + r"(?![\w-])", re.IGNORECASE
    )


def _pick(evidence: Dict[str, float]) -> List[str]:
    """Choose which keyword themes a video gets from its evidence.

    A weak winner that ties with another theme means nothing but boilerplate
    matched ("use code", "new episode", "privacy" from a VPN read), so the
    video gets no keyword theme at all: it lands under "no theme", where it
    can be seen and themed by hand, rather than under a guess."""
    ranked = sorted(evidence.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return []
    (winner, best), rest = ranked[0], ranked[1:]
    if best < STRONG_EVIDENCE and rest and abs(rest[0][1] - best) < 1e-9:
        return []
    runners_up = [theme for theme, weight in rest if weight >= STRONG_EVIDENCE]
    return [winner] + runners_up[: MAX_KEYWORD_THEMES - 1]


def evaluate(
    video: dict,
    custom_rules: Optional[List[dict]] = None,
    name_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[List[Tuple[str, float]], bool]:
    """Return ([(theme_name, confidence), ...], exclusive).

    When an exclusive custom rule matches, the list contains only the
    exclusive theme(s) and the flag is True — callers should drop any other
    assignments the video has.

    ``name_overrides`` maps a built-in keyword theme key to a different display
    name (see db.builtin_theme_overrides) so a theme the user has renamed isn't
    reported — and later recreated — under its original name.
    """
    fields = _matchable_fields(video)
    text = " ".join(fields.values())

    # custom rules are user-authored, so they match anywhere at full strength
    exclusive_themes: List[str] = []
    custom_scores: Dict[str, float] = {}
    for rule in custom_rules or []:
        if _compile_expression(rule["pattern"]).search(text):
            if rule["exclusive"]:
                exclusive_themes.append(rule["theme_name"])
            else:
                custom_scores[rule["theme_name"]] = 0.9
    if exclusive_themes:
        return [(name, 1.0) for name in dict.fromkeys(exclusive_themes)], True

    evidence: Dict[str, float] = {}
    for theme, patterns in _COMPILED.items():
        # per keyword, count the strongest field it appears in
        weight = sum(
            max(
                (weight for field, weight in FIELD_WEIGHTS if p.search(fields[field])),
                default=0.0,
            )
            for p in patterns
        )
        if weight >= MIN_EVIDENCE:
            evidence[theme] = weight

    scores = {
        theme: min(0.95, 0.6 + 0.15 * (evidence[theme] - 1))
        for theme in _pick(evidence)
    }
    # custom rules are user-authored: they never lose to a keyword theme
    for theme, confidence in custom_scores.items():
        scores[theme] = max(scores.get(theme, 0.0), confidence)
    if not scores:
        return [], False
    if name_overrides:
        # remap renamed built-in themes to their current name, keeping the
        # strongest score if a rename collapses two keys onto one name
        remapped: Dict[str, float] = {}
        for theme, confidence in scores.items():
            final = name_overrides.get(theme, theme)
            remapped[final] = max(remapped.get(final, 0.0), confidence)
        scores = remapped
    results = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    return results, False


def assign_themes(
    video: dict,
    custom_rules: Optional[List[dict]] = None,
    name_overrides: Optional[Dict[str, str]] = None,
) -> List[Tuple[str, float]]:
    """Return [(theme_name, confidence), ...] for every theme that matches."""
    return evaluate(video, custom_rules, name_overrides)[0]


def reapply(conn: sqlite3.Connection) -> dict:
    """Re-run keyword + custom rules over every stored video and reconcile.

    Rule-sourced assignments the current rules no longer justify are removed;
    manual and embedding assignments are never touched. A video themed by hand
    gets no rule themes on top: the hand-picked ones are the answer, and a
    keyword guess beside them is exactly the noise that was corrected. An
    exclusive rule match removes everything but its theme (regardless of
    source)."""
    custom_rules = db.list_theme_rules(conn)
    overrides = db.builtin_theme_overrides(conn)
    videos = db.all_videos(conn)
    existing = db.themes_for_videos(conn, [v["id"] for v in videos])
    hand_themed = db.manually_themed_video_ids(conn)
    added = 0
    removed = 0
    for video in videos:
        assignments, exclusive = evaluate(video, custom_rules, overrides)
        if video["id"] in hand_themed and not exclusive:
            assignments = []
        fresh = [name for name, _ in assignments]
        if exclusive:
            removed += db.remove_other_themes(conn, video["id"], fresh)
        else:
            removed += db.remove_stale_rule_themes(conn, video["id"], fresh)
        current = set(existing.get(video["id"], []))
        for name, confidence in assignments:
            if name in current and not exclusive:
                continue
            theme_id = db.get_or_create_theme(conn, name)
            db.assign_theme(conn, video["id"], theme_id, confidence, "rule")
            if name not in current:
                added += 1
    conn.commit()
    return {
        "videos_scanned": len(videos),
        "themes_added": added,
        "themes_removed": removed,
    }


# --- learning: turn the themes you assign by hand into reusable rules --------

# A channel must have at least this many themed videos, and one theme must
# cover at least this fraction of them, before we suggest a channel -> theme
# rule. Conservative on purpose: suggestions the user has to approve.
SUGGEST_MIN_VIDEOS = 3
SUGGEST_DOMINANCE = 0.6


def suggest_rules(conn: sqlite3.Connection) -> List[dict]:
    """Propose channel -> theme rules learned from the videos already themed.

    A YouTube channel is usually single-topic, so if most of a channel's themed
    videos share one theme, a rule matching that channel name will theme its
    future uploads automatically. Existing rules (same pattern) are skipped, and
    nothing is created — the user approves each suggestion via POST /rules.
    """
    existing = {r["pattern"].strip().lower() for r in db.list_theme_rules(conn)}
    totals = db.channel_tagged_totals(conn)
    by_channel: Dict[str, Dict[str, int]] = defaultdict(dict)
    for row in db.channel_theme_counts(conn):
        by_channel[row["channel"]][row["theme"]] = row["n"]

    suggestions: List[dict] = []
    for channel, theme_counts in by_channel.items():
        total = totals.get(channel, 0)
        if total < SUGGEST_MIN_VIDEOS or channel.strip().lower() in existing:
            continue
        theme, matched = max(theme_counts.items(), key=lambda kv: kv[1])
        if matched / total < SUGGEST_DOMINANCE:
            continue
        suggestions.append(
            {
                "pattern": channel,
                "theme": theme,
                "channel": channel,
                "matched": matched,
                "total": total,
            }
        )
    suggestions.sort(key=lambda s: (s["matched"], s["matched"] / s["total"]), reverse=True)
    return suggestions
