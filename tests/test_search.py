"""Browse search: which videos a typed query is allowed to match, and in
what order. The old substring LIKE made short queries useless — "tv" hit
every description containing a "…tv/" link."""
from app import db

from tests.conftest import make_video


def seed(conn):
    make_video(conn, "titlehit00a", title="Best TV shows of 2026")
    make_video(conn, "pluralhit0b", title="Cheap smart TVs, ranked")
    make_video(conn, "chanhit000c", title="Trail running in the Alps",
               channel_title="Beira Alta TV")
    make_video(conn, "deschit000d", title="Pi-hole tutorial",
               description="Blocks ads on your TV too")
    make_video(conn, "urlonly000e", title="Wildfire documentary",
               description="Full episode: https://natgeo.com/tv/watch")
    make_video(conn, "substr0000f", title="How natgeotv covers disasters")


def titles_for(conn, query):
    return [v["id"] for v in db.list_videos(conn, query)]


def test_short_query_matches_whole_words_only(conn):
    seed(conn)
    hits = titles_for(conn, "tv")
    assert set(hits) == {"titlehit00a", "pluralhit0b", "chanhit000c", "deschit000d"}
    assert "substr0000f" not in hits   # "natgeotv" is not a "tv" hit
    assert "urlonly000e" not in hits   # links are stripped from descriptions


def test_title_and_channel_hits_rank_above_description_hits(conn):
    seed(conn)
    hits = titles_for(conn, "tv")
    assert set(hits[:2]) == {"titlehit00a", "pluralhit0b"}   # title hits lead
    assert hits[2] == "chanhit000c"                          # then the channel
    assert hits[3] == "deschit000d"                          # description last


def test_longer_query_matches_word_prefixes(conn):
    make_video(conn, "prefixhit0a", title="Assassin's Creed retrospective")
    make_video(conn, "prefixhit0b", title="The best assassins in gaming")
    make_video(conn, "midword000c", title="Cooking with pancetta")
    assert set(titles_for(conn, "assassin")) == {"prefixhit0a", "prefixhit0b"}
    assert titles_for(conn, "cet") == []   # still anchored at a word start


def test_every_word_must_match(conn):
    make_video(conn, "bothwords0a", title="Assassin's Creed Black Flag review")
    make_video(conn, "oneword000b", title="Black Sails, the better pirate show")
    assert titles_for(conn, "black flag") == ["bothwords0a"]
    assert set(titles_for(conn, "black")) == {"bothwords0a", "oneword000b"}


def test_search_ignores_case_and_stray_whitespace(conn):
    make_video(conn, "casetest00a", title="Fingerstyle GUITAR lesson")
    assert titles_for(conn, "  Guitar  ") == ["casetest00a"]
    assert len(titles_for(conn, "   ")) == 1   # blank query is no filter
