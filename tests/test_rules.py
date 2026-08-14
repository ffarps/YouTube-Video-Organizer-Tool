from app import db
from app.categorize import rules
from app.categorize.rules import assign_themes
from app.ingest import sync


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
    # two tag hits add up to a full unit of evidence
    themes = _themes(
        {"title": "Ep. 12", "channel_title": "SomeChannel", "tags": ["guitar", "acoustic"]}
    )
    assert "Guitar" in themes


def test_single_stray_tag_is_not_enough():
    # creators stuff unrelated tags; one tag hit alone must not theme
    themes = _themes({"title": "How I learned to code", "tags": ["training"]})
    assert "Fitness" not in themes and "Software Development" in themes


def test_description_only_single_hits_dropped():
    # even without a dominant leader, weak description-only evidence loses
    video = {
        "title": "How I Make $50/Hour Freelance Programming on Upwork!",
        "description": (
            "Building confidence as a freelancer. New episode every week. "
            "I also talk about the tech I use."
        ),
    }
    assert _themes(video) == ["Software Development"]


def test_description_only_evidence_still_themes():
    # regression: requiring a full unit of evidence meant a video whose topic
    # never appears in the title or channel got no theme at all — that silently
    # left 30-50% of every synced playlist untagged
    video = {
        "title": "God-Tier Developer Roadmap",
        "channel_title": "Fireship",
        "description": "Every programming language and software tool you need.",
    }
    assert _themes(video) == ["Software Development"]


def test_strong_evidence_survives_a_stronger_winner():
    # a title hit is real signal even when another theme scores twice as high
    themes = _themes({"title": "AI podcast interview"})
    assert themes == ["Podcasts", "AI"]


def test_confidence_grows_with_hits():
    single = dict(assign_themes({"title": "guitar"}))["Guitar"]
    double = dict(assign_themes({"title": "acoustic guitar"}))["Guitar"]
    assert double > single


def test_dominant_theme_drops_boilerplate_noise():
    # real-world case: a gaming video whose description mentions the
    # creator's day job, a discount code, music credits, and "Xbox Series X"
    video = {
        "title": "I Played Every Need for Speed Game EVER.",
        "description": (
            "Play through 30 years of gaming history, from the PlayStation 1 "
            "to the Xbox Series X. I went from construction shifts to Senior "
            "Software Developer. Use code 'goose' for 10% off. Final Song "
            "in the description. Watch More videos!"
        ),
    }
    assert _themes(video) == ["Gaming"]


def test_equal_scores_keep_multi_label():
    # two themes with the same hit count both survive the cutoff
    themes = _themes({"title": "AI podcast: machine learning interview"})
    assert "AI" in themes and "Podcasts" in themes


def _rule(pattern, theme, exclusive=False):
    return {"id": 1, "theme_name": theme, "pattern": pattern, "exclusive": exclusive}


def test_custom_rule_adds_theme_alongside_keywords():
    themes = [
        name
        for name, _ in assign_themes(
            {"title": "Lex Fridman podcast #300"}, [_rule("lex fridman", "Lex")]
        )
    ]
    assert "Lex" in themes and "Podcasts" in themes


def test_custom_rule_is_word_boundary_matched():
    themes = [
        name
        for name, _ in assign_themes(
            {"title": "Alexandria travel vlog"}, [_rule("lex", "Lex")]
        )
    ]
    assert "Lex" not in themes


def test_exclusive_rule_suppresses_everything_else():
    result = assign_themes(
        {"title": "Lex Fridman podcast about AI and machine learning"},
        [_rule("lex fridman", "Lex", exclusive=True)],
    )
    assert result == [("Lex", 1.0)]


def test_exclusive_rule_ignored_when_not_matching():
    themes = [
        name
        for name, _ in assign_themes(
            {"title": "AI podcast interview"},
            [_rule("lex fridman", "Lex", exclusive=True)],
        )
    ]
    assert "Lex" not in themes and "AI" in themes and "Podcasts" in themes


def test_reapply_prunes_stale_rule_themes(conn):
    db.upsert_video(conn, {"id": "aivideo00ok", "title": "Machine learning explained"})
    # simulate an old noisy auto-assignment plus a user-made one
    stale = db.get_or_create_theme(conn, "Watches")
    db.assign_theme(conn, "aivideo00ok", stale, 0.6, "rule")
    manual = db.get_or_create_theme(conn, "Favorites")
    db.assign_theme(conn, "aivideo00ok", manual, 1.0, "manual")
    conn.commit()

    result = rules.reapply(conn)
    themes = {
        r["name"]
        for r in conn.execute(
            """
            SELECT t.name FROM themes t
            JOIN video_themes vt ON vt.theme_id = t.id
            WHERE vt.video_id = 'aivideo00ok'
            """
        )
    }
    assert "AI" in themes  # re-derived by the current rules
    assert "Watches" not in themes  # stale rule assignment pruned
    assert "Favorites" in themes  # manual assignment untouched
    assert result["themes_removed"] == 1


def test_name_overrides_remap_builtin_theme():
    # a renamed built-in theme is reported under its new name, not "AI"
    result = assign_themes(
        {"title": "Machine learning explained"}, None, {"AI": "Artificial Intelligence"}
    )
    names = [name for name, _ in result]
    assert "Artificial Intelligence" in names and "AI" not in names


def test_rename_builtin_theme_persists_through_reapply(conn):
    # renaming a built-in keyword theme must stick: the rule engine should feed
    # matching videos into the new name instead of recreating the original
    db.upsert_video(conn, {"id": "aivideo00ok", "title": "Machine learning explained"})
    rules.reapply(conn)  # assigns the built-in "AI" theme
    db.rename_theme(conn, "AI", "Artificial Intelligence", rules.THEME_KEYWORDS)
    conn.commit()

    rules.reapply(conn)  # the bug: this used to recreate "AI"
    names = {r["name"] for r in conn.execute("SELECT name FROM themes")}
    assert "AI" not in names  # original name not resurrected
    assert db.themes_for_videos(conn, ["aivideo00ok"])["aivideo00ok"] == [
        "Artificial Intelligence"
    ]


def test_rename_survives_new_video_ingest(conn):
    db.upsert_video(conn, {"id": "aivideo00ok", "title": "Machine learning explained"})
    rules.reapply(conn)
    db.rename_theme(conn, "AI", "Artificial Intelligence", rules.THEME_KEYWORDS)
    conn.commit()

    # a brand-new matching video comes in via the ingest path
    sync._store_videos(conn, [{"id": "neuralnets1", "title": "deep learning with CUDA"}])
    conn.commit()
    names = {r["name"] for r in conn.execute("SELECT name FROM themes")}
    assert "AI" not in names
    assert db.themes_for_videos(conn, ["neuralnets1"])["neuralnets1"] == [
        "Artificial Intelligence"
    ]


def _tag_channel(conn, ids, channel, theme):
    for vid in ids:
        db.upsert_video(conn, {"id": vid, "title": "x", "channel_title": channel})
        db.assign_theme(conn, vid, db.get_or_create_theme(conn, theme), 1.0, "manual")
    conn.commit()


def test_suggest_rule_from_consistent_channel(conn):
    _tag_channel(conn, ["techguru001", "techguru002", "techguru003"], "TechGuru", "Tech")
    suggestions = rules.suggest_rules(conn)
    match = next(s for s in suggestions if s["channel"] == "TechGuru")
    assert match["theme"] == "Tech"
    assert match["pattern"] == "TechGuru" and match["matched"] == 3 and match["total"] == 3


def test_suggest_rule_dominant_but_not_unanimous(conn):
    # 2 of 3 share a theme -> 0.67 clears the 0.6 dominance bar
    _tag_channel(conn, ["mostlyaa001", "mostlyaa002"], "MostlyTech", "Tech")
    db.upsert_video(conn, {"id": "mostlyaa003", "title": "x", "channel_title": "MostlyTech"})
    db.assign_theme(conn, "mostlyaa003", db.get_or_create_theme(conn, "Guitar"), 1.0, "manual")
    conn.commit()
    match = next(s for s in rules.suggest_rules(conn) if s["channel"] == "MostlyTech")
    assert match["theme"] == "Tech" and match["matched"] == 2 and match["total"] == 3


def test_no_suggestion_below_min_videos(conn):
    _tag_channel(conn, ["tinychan001", "tinychan002"], "TinyChannel", "Tech")
    assert all(s["channel"] != "TinyChannel" for s in rules.suggest_rules(conn))


def test_no_suggestion_for_mixed_channel(conn):
    for vid, theme in [("mixed00001", "Tech"), ("mixed00002", "Guitar"), ("mixed00003", "Games")]:
        db.upsert_video(conn, {"id": vid, "title": "x", "channel_title": "Mixed"})
        db.assign_theme(conn, vid, db.get_or_create_theme(conn, theme), 1.0, "manual")
    conn.commit()
    assert all(s["channel"] != "Mixed" for s in rules.suggest_rules(conn))


def test_no_suggestion_when_rule_already_exists(conn):
    _tag_channel(conn, ["dupchan0001", "dupchan0002", "dupchan0003"], "DupChan", "Tech")
    db.add_theme_rule(conn, "Tech", "DupChan", False)
    conn.commit()
    assert all(s["channel"] != "DupChan" for s in rules.suggest_rules(conn))
