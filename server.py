from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    class FastMCP:  # type: ignore[no-redef]
        """Tiny import-time fallback for tests before runtime deps are installed."""

        def __init__(self, name: str):
            self.name = name
            self.tools: dict[str, Any] = {}

        def tool(self):
            def decorator(func):
                self.tools[func.__name__] = func
                return func

            return decorator

        def run(self, transport: str = "stdio") -> None:
            raise RuntimeError("Install dependencies first: pip install -r requirements.txt")

try:
    from . import youtube_client as yc
except ImportError:
    import youtube_client as yc  # type: ignore[no-redef]

mcp = FastMCP("youtube-automation")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png"}
THUMBNAIL_ORDER = {".jpg": 0, ".jpeg": 1, ".png": 2}
VALID_PRIVACY = {"private", "unlisted", "public"}
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000
MAX_TAGS_TOTAL_LENGTH = 500
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024


def _safe_call(func, *args, **kwargs) -> Any:
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        return yc.error_payload("internal_error", message=str(exc))


def _service_or_error(youtube: Any | None = None) -> tuple[Any | None, dict[str, Any] | None]:
    if youtube is not None:
        return youtube, None
    return yc.get_youtube_service()


def _load_config(config_path: str | Path | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    return yc.load_config(config_path)


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _execute(request: Any) -> Any:
    return request.execute()


def _resolve_input_path(value: str, fallback_dir: str | Path | None = None) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute() or path.exists() or fallback_dir is None:
        return path
    return Path(fallback_dir).expanduser() / path


def _validate_iso8601(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _normalize_tags(tags: Any) -> tuple[list[str] | None, dict[str, Any] | None]:
    if tags is None:
        return [], None
    if not isinstance(tags, list):
        return None, yc.error_payload(
            "validation_error",
            field="tags",
            details="tags must be a list of strings",
        )

    cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
    if sum(len(tag) for tag in cleaned) > MAX_TAGS_TOTAL_LENGTH:
        return None, yc.error_payload(
            "validation_error",
            field="tags",
            details="combined tag length must be at most 500 characters",
        )
    return cleaned, None


def _best_thumbnail_url(item: dict[str, Any]) -> str:
    for key in ("maxres", "standard", "high", "medium", "default"):
        url = item.get(key, {}).get("url")
        if url:
            return url
    return ""


def _serialize_video_details(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    return {
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "tags": snippet.get("tags") or [],
        "view_count": _int_value(statistics.get("viewCount")),
        "like_count": _int_value(statistics.get("likeCount")),
        "comment_count": _int_value(statistics.get("commentCount")),
    }


def _serialize_competitor(item: dict[str, Any]) -> dict[str, Any]:
    details = _serialize_video_details(item)
    snippet = item.get("snippet", {})
    return {
        "video_id": item.get("id", ""),
        "title": details["title"],
        "description": details["description"][:300],
        "channel_title": snippet.get("channelTitle", ""),
        "tags": details["tags"],
        "view_count": details["view_count"],
        "like_count": details["like_count"],
        "comment_count": details["comment_count"],
    }


def _list_pending_files(config_path: str | Path | None = None) -> dict[str, Any]:
    config, config_error = _load_config(config_path)
    if config_error:
        return config_error

    assert config is not None
    videos_dir = Path(config["videos_dir"]).expanduser()
    thumbs_dir = Path(config["thumbs_dir"]).expanduser()

    if not videos_dir.is_dir():
        return yc.error_payload("directory_not_found", path=str(videos_dir))
    if not thumbs_dir.is_dir():
        return yc.error_payload("directory_not_found", path=str(thumbs_dir))

    videos = sorted(
        [path for path in videos_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS],
        key=lambda path: path.name.lower(),
    )
    thumbs = sorted(
        [path for path in thumbs_dir.iterdir() if path.is_file() and path.suffix.lower() in THUMBNAIL_EXTENSIONS],
        key=lambda path: path.name.lower(),
    )

    thumbs_by_stem: dict[str, Path] = {}
    for thumb in thumbs:
        stem = thumb.stem.lower()
        current = thumbs_by_stem.get(stem)
        if current is None or THUMBNAIL_ORDER[thumb.suffix.lower()] < THUMBNAIL_ORDER[current.suffix.lower()]:
            thumbs_by_stem[stem] = thumb

    return {
        "videos": [path.name for path in videos],
        "thumbs": [path.name for path in thumbs],
        "pairs": [
            {
                "video": video.name,
                "thumb": thumbs_by_stem[video.stem.lower()].name
                if video.stem.lower() in thumbs_by_stem
                else None,
            }
            for video in videos
        ],
    }


def _search_competitors(
    query: str,
    max_results: int = 10,
    youtube: Any | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        return yc.error_payload("validation_error", field="query", details="query is required")

    try:
        limit = int(max_results)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    try:
        search_response = _execute(
            service.search()
            .list(part="snippet", q=query, type="video", order="viewCount", maxResults=limit)
        )
        video_ids = [
            item.get("id", {}).get("videoId")
            for item in search_response.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        if not video_ids:
            return []

        details_response = _execute(
            service.videos().list(part="snippet,statistics", id=",".join(video_ids))
        )
    except Exception as exc:
        return yc.normalize_http_error(exc)

    videos_by_id = {item.get("id"): item for item in details_response.get("items", [])}
    return [
        _serialize_competitor(videos_by_id[video_id])
        for video_id in video_ids
        if video_id in videos_by_id
    ]


def _get_video_details(video_id: str, youtube: Any | None = None) -> dict[str, Any]:
    if not isinstance(video_id, str) or not video_id.strip():
        return yc.error_payload("validation_error", field="video_id", details="video_id is required")

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    try:
        response = _execute(service.videos().list(part="snippet,statistics", id=video_id))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    items = response.get("items", [])
    if not items:
        return yc.error_payload("video_not_found", video_id=video_id)

    return _serialize_video_details(items[0])


def _get_channel_info(youtube: Any | None = None) -> dict[str, Any]:
    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    try:
        response = _execute(service.channels().list(part="snippet,statistics", mine=True))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    items = response.get("items", [])
    if not items:
        return yc.error_payload("channel_not_found")

    item = items[0]
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    return {
        "channel_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "subscriber_count": _int_value(statistics.get("subscriberCount")),
        "video_count": _int_value(statistics.get("videoCount")),
    }


def _set_thumbnail(
    video_id: str,
    image_path: str,
    youtube: Any | None = None,
    media_upload_cls: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(video_id, str) or not video_id.strip():
        return yc.error_payload("validation_error", field="video_id", details="video_id is required")

    image = Path(image_path).expanduser()
    if not image.is_file():
        return yc.error_payload("file_not_found", path=str(image))
    if image.suffix.lower() not in THUMBNAIL_EXTENSIONS:
        return yc.error_payload(
            "validation_error",
            field="image_path",
            details="thumbnail must be .jpg, .jpeg, or .png",
        )
    if image.stat().st_size > MAX_THUMBNAIL_BYTES:
        return yc.error_payload(
            "validation_error",
            field="image_path",
            details="thumbnail must be at most 2 MB",
        )

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    if media_upload_cls is None:
        media_upload_cls, media_error = yc.get_media_upload_class()
        if media_error:
            return media_error

    try:
        media = media_upload_cls(str(image))
        response = _execute(service.thumbnails().set(videoId=video_id, media_body=media))
    except Exception as exc:
        return yc.normalize_http_error(exc, context="thumbnail")

    items = response.get("items", [])
    return {"thumbnail_url": _best_thumbnail_url(items[0]) if items else ""}


def _upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] | None,
    thumbnail_path: str | None = None,
    scheduled_time: str | None = None,
    privacy: str | None = None,
    youtube: Any | None = None,
    config_path: str | Path | None = None,
    media_upload_cls: Any | None = None,
    thumbnail_func: Any | None = None,
) -> dict[str, Any]:
    config, config_error = _load_config(config_path)
    if config_error:
        return config_error
    assert config is not None

    video = _resolve_input_path(video_path, config["videos_dir"])
    if not video.is_file():
        return yc.error_payload("file_not_found", path=str(video))

    thumbnail: Path | None = None
    if thumbnail_path:
        thumbnail = _resolve_input_path(thumbnail_path, config["thumbs_dir"])
        if not thumbnail.is_file():
            return yc.error_payload("file_not_found", path=str(thumbnail))

    title_text = title.strip() if isinstance(title, str) else ""
    if not title_text:
        return yc.error_payload("validation_error", field="title", details="title is required")
    if len(title_text) > MAX_TITLE_LENGTH:
        return yc.error_payload(
            "validation_error",
            field="title",
            details="title must be at most 100 characters",
        )

    description_text = description if isinstance(description, str) else ""
    final_description = description_text + str(config.get("footer_template", ""))
    if len(final_description) > MAX_DESCRIPTION_LENGTH:
        return yc.error_payload(
            "validation_error",
            field="description",
            details="description plus footer must be at most 5000 characters",
        )

    clean_tags, tags_error = _normalize_tags(tags)
    if tags_error:
        return tags_error
    assert clean_tags is not None

    requested_privacy = privacy or config.get("default_privacy", "private")
    if requested_privacy not in VALID_PRIVACY:
        return yc.error_payload(
            "validation_error",
            field="privacy",
            details="privacy must be private, unlisted, or public",
        )

    warnings: list[dict[str, Any]] = []
    effective_privacy = requested_privacy
    if scheduled_time:
        if not _validate_iso8601(scheduled_time):
            return yc.error_payload(
                "validation_error",
                field="scheduled_time",
                details="scheduled_time must be ISO 8601",
            )
        if effective_privacy != "private":
            warnings.append(
                {
                    "code": "privacy_forced_private",
                    "message": "Scheduled uploads must be private until publishAt.",
                    "requested_privacy": effective_privacy,
                }
            )
            effective_privacy = "private"

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    if media_upload_cls is None:
        media_upload_cls, media_error = yc.get_media_upload_class()
        if media_error:
            return media_error

    status_body: dict[str, Any] = {
        "privacyStatus": effective_privacy,
        "selfDeclaredMadeForKids": bool(config.get("made_for_kids", False)),
    }
    if scheduled_time:
        status_body["publishAt"] = scheduled_time

    body = {
        "snippet": {
            "title": title_text,
            "description": final_description,
            "tags": clean_tags,
            "categoryId": str(config.get("default_category_id", "22")),
            "defaultLanguage": str(config.get("default_language", "ru")),
        },
        "status": status_body,
    }

    try:
        media = media_upload_cls(
            str(video),
            chunksize=1024 * 1024,
            resumable=True,
            mimetype="video/*",
        )
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            progress, response = request.next_chunk()
            if progress is not None and hasattr(progress, "progress"):
                percent = int(progress.progress() * 100)
                print(f"YouTube upload progress: {percent}%", file=sys.stderr)
    except Exception as exc:
        return yc.normalize_http_error(exc)

    video_id = response.get("id") if isinstance(response, dict) else None
    if not video_id:
        return yc.error_payload("youtube_api_error", status=None, reason="", message="upload response missing video id")

    thumbnail_set = False
    if thumbnail is not None:
        if thumbnail_func is None:
            thumbnail_func = _set_thumbnail
        thumbnail_result = thumbnail_func(
            video_id,
            str(thumbnail),
            youtube=service,
            media_upload_cls=media_upload_cls,
        )
        if isinstance(thumbnail_result, dict) and "error" in thumbnail_result:
            warnings.append(
                {
                    "code": thumbnail_result["error"],
                    "message": "Video uploaded, but thumbnail was not set.",
                    "details": thumbnail_result,
                }
            )
        else:
            thumbnail_set = True

    return {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "status": effective_privacy,
        "scheduled_time": scheduled_time or None,
        "thumbnail_set": thumbnail_set,
        "warnings": warnings,
    }


@mcp.tool()
def list_pending_files() -> Any:
    """List queued videos, thumbnails, and filename-stem pairs from config paths."""
    return _safe_call(_list_pending_files)


@mcp.tool()
def search_competitors(query: str, max_results: int = 10) -> Any:
    """Search competitor videos and return metadata plus statistics."""
    return _safe_call(_search_competitors, query, max_results)


@mcp.tool()
def get_video_details(video_id: str) -> Any:
    """Get title, description, tags, and statistics for one YouTube video."""
    return _safe_call(_get_video_details, video_id)


@mcp.tool()
def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] | None = None,
    thumbnail_path: str | None = None,
    scheduled_time: str | None = None,
    privacy: str | None = None,
) -> Any:
    """Upload a video with metadata, optional schedule, and optional thumbnail."""
    return _safe_call(
        _upload_video,
        video_path,
        title,
        description,
        tags,
        thumbnail_path,
        scheduled_time,
        privacy,
    )


@mcp.tool()
def set_thumbnail(video_id: str, image_path: str) -> Any:
    """Set a custom thumbnail for an uploaded YouTube video."""
    return _safe_call(_set_thumbnail, video_id, image_path)


@mcp.tool()
def get_channel_info() -> Any:
    """Return the authenticated YouTube channel identity and high-level statistics."""
    return _safe_call(_get_channel_info)


if __name__ == "__main__":
    mcp.run(transport="stdio")
