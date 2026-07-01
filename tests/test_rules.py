from app.categorize.rules import assign_themes


def _themes(video):
    return [name for name, _ in assign_themes(video)]


def test_word_boundaries_fix_legacy_false_positives():
    # legacy substring matching put "airplane" in AI and "watching" in Watches
    assert "AI" not in _themes({"title": "Airplane spotting compilation"})
    assert "Watches" not in _themes({"title": "Watching the sunset"})


def test_urls_in_description_ignored():
    themes = _themes(
        {"title": "Plain video", "description": "See https://youtube.com/watch?v=abc"}
    )
    assert "Watches" not in themes


def test_multi_label():
    themes = _themes({"title": "AI podcast: machine learning interview"})
    assert "AI" in themes and "Podcasts" in themes


def test_matches_tags_and_channel():
    themes = _themes(
        {"title": "Ep. 12", "channel_title": "SomeChannel", "tags": ["guitar"]}
    )
    assert "Guitar" in themes


def test_confidence_grows_with_hits():
    single = dict(assign_themes({"title": "guitar"}))["Guitar"]
    double = dict(assign_themes({"title": "acoustic guitar"}))["Guitar"]
    assert double > single
