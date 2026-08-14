from yt_dlp.utils import DownloadError

from app.ingest import ytdlp


def test_fetch_videos_full_skips_unavailable(monkeypatch):
    # public playlists can list private/deleted videos; those must not
    # abort the whole batch
    def fake_fetch(vid):
        if vid == "private000k":
            raise DownloadError("Private video. Sign in if ...")
        return {"id": vid, "title": "ok"}

    monkeypatch.setattr(ytdlp, "fetch_video_full", fake_fetch)
    out = ytdlp.fetch_videos_full(["ok00000000a", "private000k", "ok00000000b"])
    assert [v["id"] for v in out] == ["ok00000000a", "ok00000000b"]
