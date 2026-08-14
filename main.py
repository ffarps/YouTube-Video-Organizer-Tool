from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from typing import List, Dict, Optional
from urllib.parse import urlparse, parse_qs
from datetime import datetime
from typing import Any
import yt_dlp
import uuid
import json
import os

app = FastAPI(title="Watchlog", version="v1")


# Data Models
class VideoInfo(BaseModel):
    title: str
    url: HttpUrl
    description: Optional[str] = None
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    view_count: Optional[int] = None
    upload_date: Optional[str] = None
    channel: Optional[str] = None
    watched: bool = False

    # Make the model JSON serializable (HttpUrl to string)
    def dict(self, *args, **kwargs):
        d = super().dict(*args, **kwargs)
        d["url"] = str(d["url"])
        return d


class CategoryVideos(BaseModel):
    videos: List[VideoInfo]


# File operations
JSON_FILE = "videos.json"


def initialize_json_file():
    """Create an empty JSON file if it doesn't exist or is corrupted"""
    if not os.path.exists(JSON_FILE) or os.path.getsize(JSON_FILE) == 0:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    else:
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                json.load(f)  # Try to load to check if it's valid
        except json.JSONDecodeError:
            # Backup the corrupted file
            backup_file = f"{JSON_FILE}.bak"
            print(f"Backing up corrupted {JSON_FILE} to {backup_file}")
            os.rename(JSON_FILE, backup_file)
            # Create a new empty file
            with open(JSON_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f)


def load_videos() -> Dict:
    """Load videos from JSON file, with error handling"""
    initialize_json_file()  # Make sure the file exists and is valid
    with open(JSON_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_videos(data: Dict):
    """Save videos to JSON file"""
    # Ensure the data is JSON serializable
    serializable_data = {}
    for category, videos in data.items():
        serializable_data[category] = [
            {
                "title": video["title"],
                "url": str(video["url"]),
                "watched": video.get("watched", False),
                "description": video.get("description"),
                "duration": video.get("duration"),
                "thumbnail": video.get("thumbnail"),
                "view_count": video.get("view_count"),
                "upload_date": video.get("upload_date"),
                "channel": video.get("channel"),
            }
            for video in videos
        ]

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable_data, f, indent=2)


def extract_video_info(url: str):
    """Extract video or playlist information using yt-dlp. Returns VideoInfo or List[VideoInfo]"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,  # Needed for full video info
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            # If it's a playlist, info['entries'] is a list of videos
            if "entries" in info and isinstance(info["entries"], list):
                videos = []
                for entry in info["entries"]:
                    if entry is None:
                        continue
                    videos.append(
                        VideoInfo(
                            title=entry.get("title", ""),
                            url=(
                                f"https://www.youtube.com/watch?v={entry['id']}"
                                if entry.get("id")
                                else url
                            ),
                            description=entry.get("description", ""),
                            duration=entry.get("duration"),
                            thumbnail=entry.get("thumbnail"),
                            view_count=entry.get("view_count"),
                            upload_date=entry.get("upload_date"),
                            channel=entry.get("uploader"),
                            watched=False,
                        )
                    )
                return videos
            else:
                return VideoInfo(
                    title=info.get("title", ""),
                    url=url,
                    description=info.get("description", ""),
                    duration=info.get("duration"),
                    thumbnail=info.get("thumbnail"),
                    view_count=info.get("view_count"),
                    upload_date=info.get("upload_date"),
                    channel=info.get("uploader"),
                    watched=False,
                )
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Error fetching video info: {str(e)}"
        )


@app.get("/categories")
async def get_categories():
    """Get all categories"""
    videos = load_videos()
    return {"categories": list(videos.keys())}


@app.get("/categories/{category}")
async def get_category_videos(category: str):
    """Get all videos in a category"""
    videos = load_videos()
    if category not in videos:
        raise HTTPException(status_code=404, detail="Category not found")
    return {category: videos[category]}


@app.post("/categories/{category}/videos")
async def add_video(category: str, video: VideoInfo):
    videos = load_videos()  # Load the entire existing data

    if category not in videos:
        videos[category] = []

    # Check for duplicate URLs
    if any(v["url"] == str(video.url) for v in videos[category]):
        raise HTTPException(status_code=400, detail="Video URL already exists")

    videos[category].append(
        {"title": video.title, "url": str(video.url), "watched": video.watched}
    )

    save_videos(videos)  # Save the entire updated data
    return {"message": "Video added successfully"}


@app.get("/videos")
async def get_video_by_url(url: str):
    """Get detailed information about a specific video using its URL"""
    videos = load_videos()

    # Search for the video in all categories
    for category, video_list in videos.items():
        for video in video_list:
            if video["url"] == url:
                return {
                    "category": category,
                    "video": {
                        "title": video["title"],
                        "url": video["url"],
                        "watched": video["watched"],
                    },
                }

    # If video not found, raise 404 error
    raise HTTPException(status_code=404, detail="Video not found")


@app.put("/categories/{category}/videos")
async def update_video(category: str, url: str, video: VideoInfo):
    """Update a video by URL"""
    videos = load_videos()
    if category not in videos:
        raise HTTPException(status_code=404, detail="Category not found")

    video_dict = {"title": video.title, "url": str(video.url), "watched": video.watched}

    for idx, v in enumerate(videos[category]):
        if v["url"] == url:
            videos[category][idx] = video_dict
            save_videos(videos)
            return {"message": "Video updated successfully"}
    raise HTTPException(status_code=404, detail="Video not found")


@app.delete("/categories/{category}/videos")
async def delete_video(category: str, url: str):
    """Delete a video by URL"""
    videos = load_videos()
    if category not in videos:
        raise HTTPException(status_code=404, detail="Category not found")

    initial_length = len(videos[category])
    videos[category] = [v for v in videos[category] if v["url"] != url]

    if len(videos[category]) == initial_length:
        raise HTTPException(status_code=404, detail="Video not found")

    save_videos(videos)
    return {"message": "Video deleted successfully"}


@app.patch("/categories/{category}/videos/watched")
async def toggle_watched(category: str, url: str):
    """Toggle watched status by URL"""
    videos = load_videos()
    if category not in videos:
        raise HTTPException(status_code=404, detail="Category not found")

    for v in videos[category]:
        if v["url"] == url:
            v["watched"] = not v["watched"]
            save_videos(videos)
            return {"message": "Watched status toggled successfully"}

    raise HTTPException(status_code=404, detail="Video not found")


@app.post("/categories/{category}/videos/fetch")
async def add_video_from_url(category: str, url: str):
    """Add a video or all videos from a playlist to a category using the URL"""
    videos = load_videos()

    if category not in videos:
        videos[category] = []

    video_info = extract_video_info(url)

    added = []
    skipped = []

    if isinstance(video_info, list):
        # Playlist: add all videos
        for v in video_info:
            if any(existing["url"] == str(v.url) for existing in videos[category]):
                skipped.append(v.title)
                continue
            video_dict = v.dict()
            videos[category].append(video_dict)
            added.append(v.title)
        save_videos(videos)
        return {
            "message": f"Added {len(added)} videos, skipped {len(skipped)} (duplicates)",
            "added": added,
            "skipped": skipped,
        }
    else:
        # Single video
        if any(v["url"] == str(video_info.url) for v in videos[category]):
            raise HTTPException(status_code=400, detail="Video URL already exists")
        video_dict = video_info.dict()
        videos[category].append(video_dict)
        save_videos(videos)
        return {"message": "Video added successfully", "video": video_dict}


def extract_playlist_videos(playlist_url: str) -> List[VideoInfo]:
    """Extract all videos from a playlist"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            playlist_info = ydl.extract_info(playlist_url, download=False)

            videos = []
            for entry in playlist_info["entries"]:
                video_info = VideoInfo(
                    title=entry.get("title", ""),
                    url=f"https://www.youtube.com/watch?v={entry['id']}",
                    description=entry.get("description", ""),
                    duration=entry.get("duration"),
                    thumbnail=entry.get("thumbnail"),
                    view_count=entry.get("view_count"),
                    upload_date=entry.get("upload_date"),
                    channel=entry.get("uploader"),
                    watched=False,
                )
                videos.append(video_info)

            return videos
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Error fetching playlist: {str(e)}"
        )


@app.post("/playlists/convert")
async def convert_playlist_to_json(playlist_url: str):
    """Convert a YouTube playlist to JSON file"""
    try:
        # Generate UUID for the filename
        filename = f"playlist_{uuid.uuid4()}.json"

        # Extract videos from playlist
        videos = extract_playlist_videos(playlist_url)

        # Create playlist data structure
        playlist_data = {
            "playlist_url": playlist_url,
            "converted_date": datetime.now().isoformat(),
            "videos": [video.dict() for video in videos],
        }

        # Save to JSON file
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(playlist_data, f, indent=2, ensure_ascii=False)

        return {
            "message": "Playlist converted successfully",
            "filename": filename,
            "video_count": len(videos),
        }
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Error converting playlist: {str(e)}"
        )


# Optional: Add an endpoint to import playlist JSON to your video collection
@app.post("/playlists/import/{category}")
async def import_playlist_json(category: str, filename: str):
    """Import videos from a playlist JSON file into a category"""
    if not os.path.exists(filename):
        raise HTTPException(status_code=404, detail="Playlist file not found")

    try:
        # Load playlist JSON
        with open(filename, "r", encoding="utf-8") as f:
            playlist_data = json.load(f)

        # Load current videos
        videos = load_videos()

        if category not in videos:
            videos[category] = []

        # Add new videos, skip duplicates
        added_count = 0
        skipped_count = 0

        for video in playlist_data["videos"]:
            if not any(v["url"] == video["url"] for v in videos[category]):
                videos[category].append(video)
                added_count += 1
            else:
                skipped_count += 1

        save_videos(videos)

        return {
            "message": "Playlist imported successfully",
            "added_videos": added_count,
            "skipped_videos": skipped_count,
        }
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Error importing playlist: {str(e)}"
        )


# Initialize the JSON file when the app starts
@app.on_event("startup")
async def startup_event():
    initialize_json_file()
