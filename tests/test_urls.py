import pytest

from app.ingest.urls import classify_url, playlist_id_from_url, video_id_from_url

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}&t=42s",
        f"https://m.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}",
        f"https://youtu.be/{VID}?si=abc123",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/live/{VID}",
        f"https://music.youtube.com/watch?v={VID}",
        VID,  # bare id
    ],
)
def test_video_id_extraction(url):
    assert video_id_from_url(url) == VID


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=dQw4w9WgXcQ",  # wrong host
        "https://www.youtube.com/playlist?list=PLabc",  # no video
        "not a url",
        "https://youtu.be/tooshort",
    ],
)
def test_video_id_rejects(url):
    assert video_id_from_url(url) is None


def test_playlist_id():
    assert (
        playlist_id_from_url("https://www.youtube.com/playlist?list=PLx0sYbCqOb8Q")
        == "PLx0sYbCqOb8Q"
    )
    assert playlist_id_from_url("PLx0sYbCqOb8Q") == "PLx0sYbCqOb8Q"
    assert playlist_id_from_url("https://www.youtube.com/watch?v=abc") is None


def test_classify():
    assert classify_url(f"https://youtu.be/{VID}") == {"kind": "video", "video_id": VID}
    assert classify_url("https://www.youtube.com/playlist?list=WL") == {
        "kind": "watch_later",
        "playlist_id": "WL",
    }
    assert classify_url("https://www.youtube.com/playlist?list=PLabc123")["kind"] == "playlist"
    # a watch URL inside a playlist is treated as the playlist
    assert (
        classify_url(f"https://www.youtube.com/watch?v={VID}&list=PLabc123")["kind"]
        == "playlist"
    )
    assert classify_url("https://www.youtube.com/@veritasium") == {
        "kind": "channel",
        "handle": "@veritasium",
    }
    assert classify_url("https://www.youtube.com/channel/UCabc")["channel_id"] == "UCabc"
    assert classify_url("https://example.com/foo")["kind"] == "unknown"
