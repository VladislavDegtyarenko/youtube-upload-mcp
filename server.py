from __future__ import annotations

import sys
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import Icon
except ImportError:
    class FastMCP:  # type: ignore[no-redef]
        """Tiny import-time fallback for tests before runtime deps are installed."""

        def __init__(self, name: str, **_kwargs: Any):
            self.name = name
            self.tools: dict[str, Any] = {}

        def tool(self, *_args: Any, **_kwargs: Any):
            def decorator(func):
                self.tools[func.__name__] = func
                return func

            return decorator

        def run(self, transport: str = "stdio") -> None:
            raise RuntimeError("Install dependencies first: pip install -r requirements.txt")

    Icon = None  # type: ignore[assignment, misc]

try:
    from . import youtube_client as yc
except ImportError:
    import youtube_client as yc  # type: ignore[no-redef]

# Route TLS verification through the OS trust store as early as possible so that
# antivirus/proxy HTTPS interception is handled transparently for every API call.
yc.enable_os_trust_store()

# Red YouTube glyph (official play-button mark) embedded as a self-contained SVG
# data URI so the icon ships with the server and needs no external hosting.
YOUTUBE_ICON_SRC = (
    "data:image/svg+xml;base64,"
    "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAy"
    "NCI+PHBhdGggZmlsbD0iI0ZGMDAwMCIgZD0iTTIzLjQ5OCA2LjE4NmEzLjAxNiAzLjAxNiAwIDAg"
    "MC0yLjEyMi0yLjEzNkMxOS41MDUgMy41NDUgMTIgMy41NDUgMTIgMy41NDVzLTcuNTA1IDAtOS4z"
    "NzcuNTA1QTMuMDE3IDMuMDE3IDAgMCAwIC41MDIgNi4xODZDMCA4LjA3IDAgMTIgMCAxMnMwIDMu"
    "OTMuNTAyIDUuODE0YTMuMDE2IDMuMDE2IDAgMCAwIDIuMTIyIDIuMTM2YzEuODcxLjUwNSA5LjM3"
    "Ni41MDUgOS4zNzYuNTA1czcuNTA1IDAgOS4zNzctLjUwNWEzLjAxNSAzLjAxNSAwIDAgMCAyLjEy"
    "Mi0yLjEzNkMyNCAxNS45MyAyNCAxMiAyNCAxMnMwLTMuOTMtLjUwMi01LjgxNHoiLz48cGF0aCBm"
    "aWxsPSIjZmZmIiBkPSJNOS41NDUgMTUuNTY4VjguNDMyTDE1LjgxOCAxMmwtNi4yNzMgMy41Njh6"
    "Ii8+PC9zdmc+"
)

# Shared icon list reused for the server and every tool so the red YouTube mark
# is visible on the server itself and on each individual operation.
YOUTUBE_ICONS = (
    [Icon(src=YOUTUBE_ICON_SRC, mimeType="image/svg+xml", sizes=["any"])]
    if Icon is not None
    else None
)

mcp = FastMCP("youtube-automation", icons=YOUTUBE_ICONS)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png"}
THUMBNAIL_ORDER = {".jpg": 0, ".jpeg": 1, ".png": 2}
VALID_PRIVACY = {"private", "unlisted", "public"}
MAX_TITLE_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 5000
MAX_TAGS_TOTAL_LENGTH = 500
MAX_PLAYLIST_TITLE_LENGTH = 150
MAX_PLAYLIST_DESCRIPTION_LENGTH = 5000

# Last-resort fallbacks when the caller (skill/prompt) does not supply these.
# Kept out of config.json on purpose: real users should not hand-edit JSON;
# the skill asks for category, language, and footer links conversationally.
DEFAULT_CATEGORY_ID = "27"
DEFAULT_LANGUAGE = "en"
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
VALID_LICENSE = {"youtube", "creativeCommon"}
VIDEO_DETAILS_PARTS = "snippet,status,recordingDetails,contentDetails,statistics"
CHANGE_FIELD_ORDER = (
    "title",
    "description",
    "tags",
    "category_id",
    "default_language",
    "privacy",
    "publish_at",
    "made_for_kids",
    "contains_synthetic_media",
    "embeddable",
    "public_stats_viewable",
    "license",
    "recording_date",
)
SUPPORTED_CHANGE_FIELDS = set(CHANGE_FIELD_ORDER)
BOOLEAN_CHANGE_FIELDS = {
    "made_for_kids",
    "contains_synthetic_media",
    "embeddable",
    "public_stats_viewable",
}
FIELD_PARTS = {
    "title": "snippet",
    "description": "snippet",
    "tags": "snippet",
    "category_id": "snippet",
    "default_language": "snippet",
    "privacy": "status",
    "publish_at": "status",
    "made_for_kids": "status",
    "contains_synthetic_media": "status",
    "embeddable": "status",
    "public_stats_viewable": "status",
    "license": "status",
    "recording_date": "recordingDetails",
}
EDIT_PART_ORDER = ("snippet", "status", "recordingDetails")

def _safe_call(func, *args, **kwargs) -> Any:
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        return yc.error_payload("internal_error", message=str(exc))


# Background upload jobs. A multi-gigabyte upload takes far longer than an MCP
# client's per-request timeout, so upload_video must not block the request for the
# whole transfer. Instead it starts the upload on a daemon thread, returns a job_id
# immediately, and the client polls get_upload_status until it finishes. This is
# robust regardless of whether the client resets its timeout on progress.
_UPLOAD_JOBS: dict[str, dict[str, Any]] = {}
_UPLOAD_JOBS_LOCK = threading.Lock()
_MAX_TRACKED_JOBS = 50


def _prune_upload_jobs_locked() -> None:
    """Drop the oldest finished jobs so the registry cannot grow without bound."""
    if len(_UPLOAD_JOBS) <= _MAX_TRACKED_JOBS:
        return
    finished = [
        (job.get("updated_at", 0.0), jid)
        for jid, job in _UPLOAD_JOBS.items()
        if job.get("status") in ("completed", "error")
    ]
    finished.sort()
    for _, jid in finished[: len(_UPLOAD_JOBS) - _MAX_TRACKED_JOBS]:
        _UPLOAD_JOBS.pop(jid, None)


def _update_job(job_id: str, **fields: Any) -> None:
    with _UPLOAD_JOBS_LOCK:
        job = _UPLOAD_JOBS.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def _run_upload_job(job_id: str, args: tuple[Any, ...]) -> None:
    def progress_callback(percent: int, uploaded: int | None, total: int | None) -> None:
        fields: dict[str, Any] = {"progress_percent": percent}
        if uploaded is not None:
            fields["uploaded_bytes"] = uploaded
        if total is not None:
            fields["total_bytes"] = total
        _update_job(job_id, **fields)

    result = _safe_call(_upload_video, *args, progress_callback=progress_callback)

    if isinstance(result, dict) and "error" in result:
        _update_job(job_id, status="error", result=result)
    else:
        _update_job(job_id, status="completed", progress_percent=100, result=result)


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
    status = item.get("status", {})
    content_details = item.get("contentDetails", {})
    recording_details = item.get("recordingDetails", {})
    description = snippet.get("description", "")
    return {
        "video_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "description": description,
        "description_preview": description[:300],
        "tags": snippet.get("tags") or [],
        "category_id": str(snippet.get("categoryId", "")),
        "default_language": snippet.get("defaultLanguage", ""),
        "privacy": status.get("privacyStatus", ""),
        "publish_at": status.get("publishAt"),
        "made_for_kids": status.get("selfDeclaredMadeForKids", status.get("madeForKids")),
        "contains_synthetic_media": status.get("containsSyntheticMedia"),
        "embeddable": status.get("embeddable"),
        "public_stats_viewable": status.get("publicStatsViewable"),
        "license": status.get("license", ""),
        "recording_date": recording_details.get("recordingDate"),
        "duration": content_details.get("duration", ""),
        "published_at": snippet.get("publishedAt", ""),
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


def _serialize_channel_video_summary(item: dict[str, Any]) -> dict[str, Any]:
    details = _serialize_video_details(item)
    return {
        "video_id": details["video_id"],
        "title": details["title"],
        "category_id": details["category_id"],
        "privacy": details["privacy"],
        "duration": details["duration"],
        "published_at": details["published_at"],
        "tags": details["tags"],
        "description_preview": details["description_preview"],
    }


def _editable_video_state(item: dict[str, Any]) -> dict[str, Any]:
    details = _serialize_video_details(item)
    return {field: details.get(field) for field in CHANGE_FIELD_ORDER}


def _validate_changes(changes: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(changes, dict):
        return None, yc.error_payload(
            "validation_error",
            field="changes",
            details="changes must be an object",
        )
    if not changes:
        return None, yc.error_payload(
            "validation_error",
            field="changes",
            details="changes must include at least one field",
        )

    unsupported = sorted(set(changes) - SUPPORTED_CHANGE_FIELDS)
    if unsupported:
        return None, yc.error_payload(
            "validation_error",
            field="changes",
            details="unsupported change field",
            unsupported=unsupported,
            supported=list(CHANGE_FIELD_ORDER),
        )

    normalized: dict[str, Any] = {}
    for field, value in changes.items():
        if value is None:
            return None, yc.error_payload(
                "validation_error",
                field=field,
                details=f"{field} cannot be null; omit it to leave unchanged",
            )

        if field == "title":
            if not isinstance(value, str):
                return None, yc.error_payload("validation_error", field=field, details="title must be a string")
            title = value.strip()
            if not title:
                return None, yc.error_payload("validation_error", field=field, details="title is required")
            if len(title) > MAX_TITLE_LENGTH:
                return None, yc.error_payload(
                    "validation_error",
                    field=field,
                    details="title must be at most 100 characters",
                )
            normalized[field] = title
        elif field == "description":
            if not isinstance(value, str):
                return None, yc.error_payload("validation_error", field=field, details="description must be a string")
            if len(value) > MAX_DESCRIPTION_LENGTH:
                return None, yc.error_payload(
                    "validation_error",
                    field=field,
                    details="description must be at most 5000 characters",
                )
            normalized[field] = value
        elif field == "tags":
            clean_tags, tags_error = _normalize_tags(value)
            if tags_error:
                return None, tags_error
            assert clean_tags is not None
            normalized[field] = clean_tags
        elif field == "category_id":
            category_id = str(value).strip()
            if not category_id:
                return None, yc.error_payload("validation_error", field=field, details="category_id is required")
            normalized[field] = category_id
        elif field == "default_language":
            if not isinstance(value, str) or not value.strip():
                return None, yc.error_payload(
                    "validation_error",
                    field=field,
                    details="default_language must be a non-empty string",
                )
            normalized[field] = value.strip()
        elif field == "privacy":
            if value not in VALID_PRIVACY:
                return None, yc.error_payload(
                    "validation_error",
                    field=field,
                    details="privacy must be private, unlisted, or public",
                )
            normalized[field] = value
        elif field in {"publish_at", "recording_date"}:
            if not isinstance(value, str) or not value.strip() or not _validate_iso8601(value):
                return None, yc.error_payload(
                    "validation_error",
                    field=field,
                    details=f"{field} must be ISO 8601",
                )
            normalized[field] = value
        elif field in BOOLEAN_CHANGE_FIELDS:
            if not isinstance(value, bool):
                return None, yc.error_payload("validation_error", field=field, details=f"{field} must be boolean")
            normalized[field] = value
        elif field == "license":
            if value not in VALID_LICENSE:
                return None, yc.error_payload(
                    "validation_error",
                    field=field,
                    details="license must be youtube or creativeCommon",
                )
            normalized[field] = value

    return normalized, None


def _fetch_video_item(
    service: Any,
    video_id: str,
    parts: str = VIDEO_DETAILS_PARTS,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        response = _execute(service.videos().list(part=parts, id=video_id))
    except Exception as exc:
        return None, yc.normalize_http_error(exc)

    items = response.get("items", [])
    if not items:
        return None, yc.error_payload("video_not_found", video_id=video_id)
    return items[0], None


def _mutable_snippet_body(snippet: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "tags": snippet.get("tags") or [],
        "categoryId": str(snippet.get("categoryId", "22")),
    }
    for field in ("defaultLanguage", "defaultAudioLanguage"):
        if snippet.get(field):
            body[field] = snippet[field]
    return body


def _mutable_status_body(status: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for field in (
        "privacyStatus",
        "publishAt",
        "selfDeclaredMadeForKids",
        "containsSyntheticMedia",
        "embeddable",
        "publicStatsViewable",
        "license",
    ):
        if field in status:
            body[field] = status[field]
    return body


def _mutable_recording_details_body(recording_details: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for field in ("recordingDate", "locationDescription", "location"):
        if field in recording_details:
            body[field] = recording_details[field]
    return body


def _build_edit_body(
    item: dict[str, Any],
    changes: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str], dict[str, Any] | None]:
    after_item = deepcopy(item)
    snippet = after_item.setdefault("snippet", {})
    status = after_item.setdefault("status", {})
    recording_details = after_item.setdefault("recordingDetails", {})

    if "title" in changes:
        snippet["title"] = changes["title"]
    if "description" in changes:
        snippet["description"] = changes["description"]
    if "tags" in changes:
        snippet["tags"] = changes["tags"]
    if "category_id" in changes:
        snippet["categoryId"] = changes["category_id"]
    if "default_language" in changes:
        snippet["defaultLanguage"] = changes["default_language"]
    if "privacy" in changes:
        status["privacyStatus"] = changes["privacy"]
    if "publish_at" in changes:
        status["publishAt"] = changes["publish_at"]
    if "made_for_kids" in changes:
        status["selfDeclaredMadeForKids"] = changes["made_for_kids"]
    if "contains_synthetic_media" in changes:
        status["containsSyntheticMedia"] = changes["contains_synthetic_media"]
    if "embeddable" in changes:
        status["embeddable"] = changes["embeddable"]
    if "public_stats_viewable" in changes:
        status["publicStatsViewable"] = changes["public_stats_viewable"]
    if "license" in changes:
        status["license"] = changes["license"]
    if "recording_date" in changes:
        recording_details["recordingDate"] = changes["recording_date"]

    after_state = _editable_video_state(after_item)
    if "publish_at" in changes and after_state.get("privacy") != "private":
        return {}, after_item, [], yc.error_payload(
            "validation_error",
            field="publish_at",
            details="publish_at requires effective privacy to be private",
        )

    before_state = _editable_video_state(item)
    changed_fields = [
        field
        for field in CHANGE_FIELD_ORDER
        if field in changes and before_state.get(field) != after_state.get(field)
    ]
    changed_parts = {FIELD_PARTS[field] for field in changed_fields}

    body: dict[str, Any] = {"id": item.get("id", "")}
    if "snippet" in changed_parts:
        body["snippet"] = _mutable_snippet_body(after_item.get("snippet", {}))
    if "status" in changed_parts:
        body["status"] = _mutable_status_body(after_item.get("status", {}))
    if "recordingDetails" in changed_parts:
        body["recordingDetails"] = _mutable_recording_details_body(after_item.get("recordingDetails", {}))

    parts = [part for part in EDIT_PART_ORDER if part in changed_parts]
    body["_parts"] = parts
    return body, after_item, changed_fields, None


def _edit_video(
    video_id: str,
    changes: Any,
    dry_run: bool = False,
    youtube: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(video_id, str) or not video_id.strip():
        return yc.error_payload("validation_error", field="video_id", details="video_id is required")
    clean_video_id = video_id.strip()

    normalized_changes, changes_error = _validate_changes(changes)
    if changes_error:
        return changes_error
    assert normalized_changes is not None

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    item, fetch_error = _fetch_video_item(service, clean_video_id)
    if fetch_error:
        return fetch_error
    assert item is not None

    body, after_item, changed_fields, build_error = _build_edit_body(item, normalized_changes)
    if build_error:
        return build_error

    result = {
        "video_id": clean_video_id,
        "dry_run": bool(dry_run),
        "updated": False,
        "changed_fields": changed_fields,
        "before": _editable_video_state(item),
        "after": _editable_video_state(after_item),
    }
    if not changed_fields or dry_run:
        return result

    parts = body.pop("_parts")
    try:
        response = _execute(service.videos().update(part=",".join(parts), body=body))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    result["updated"] = True
    if isinstance(response, dict) and response.get("id"):
        confirmed_item = deepcopy(after_item)
        for part in ("snippet", "status", "recordingDetails"):
            if part in response:
                confirmed_item[part] = response[part]
        result["after"] = _editable_video_state(confirmed_item)
    return result


def _normalize_video_ids(video_ids: Any) -> tuple[list[str] | None, dict[str, Any] | None]:
    if not isinstance(video_ids, list) or not video_ids:
        return None, yc.error_payload(
            "validation_error",
            field="video_ids",
            details="video_ids must be a non-empty list",
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for value in video_ids:
        if not isinstance(value, str) or not value.strip():
            return None, yc.error_payload(
                "validation_error",
                field="video_ids",
                details="each video_id must be a non-empty string",
            )
        clean = value.strip()
        if clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized, None


def _bulk_edit_videos(
    video_ids: list[str] | None = None,
    changes: Any | None = None,
    edits: list[dict[str, Any]] | None = None,
    dry_run: bool = True,
    youtube: Any | None = None,
) -> dict[str, Any]:
    same_change_mode = video_ids is not None or changes is not None
    per_video_mode = edits is not None
    if same_change_mode == per_video_mode:
        return yc.error_payload(
            "validation_error",
            field="bulk_edit_videos",
            details="use exactly one mode: video_ids plus changes, or edits",
        )

    batch: list[dict[str, Any]] = []
    if same_change_mode:
        if video_ids is None or changes is None:
            return yc.error_payload(
                "validation_error",
                field="bulk_edit_videos",
                details="same-change mode requires video_ids and changes",
            )
        clean_ids, ids_error = _normalize_video_ids(video_ids)
        if ids_error:
            return ids_error
        assert clean_ids is not None
        _, changes_error = _validate_changes(changes)
        if changes_error:
            return changes_error
        batch = [{"video_id": video_id, "changes": changes} for video_id in clean_ids]
    else:
        if not isinstance(edits, list) or not edits:
            return yc.error_payload(
                "validation_error",
                field="edits",
                details="edits must be a non-empty list",
            )
        seen: set[str] = set()
        for index, edit in enumerate(edits):
            if not isinstance(edit, dict):
                return yc.error_payload("validation_error", field="edits", details="each edit must be an object")
            video_id = edit.get("video_id")
            if not isinstance(video_id, str) or not video_id.strip():
                return yc.error_payload(
                    "validation_error",
                    field="video_id",
                    details=f"edits[{index}].video_id is required",
                )
            clean_video_id = video_id.strip()
            if clean_video_id in seen:
                return yc.error_payload(
                    "validation_error",
                    field="video_id",
                    details="duplicate video_id in per-video edits",
                    video_id=clean_video_id,
                )
            seen.add(clean_video_id)
            _, changes_error = _validate_changes(edit.get("changes"))
            if changes_error:
                return changes_error
            batch.append({"video_id": clean_video_id, "changes": edit.get("changes")})

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    results: list[dict[str, Any]] = []
    for edit in batch:
        result = _edit_video(edit["video_id"], edit["changes"], dry_run=dry_run, youtube=service)
        if isinstance(result, dict) and "error" in result and "video_id" not in result:
            result = {"video_id": edit["video_id"], **result}
        results.append(result)

    return {
        "dry_run": bool(dry_run),
        "total": len(results),
        "updated": sum(1 for result in results if result.get("updated") is True),
        "results": results,
    }


def _list_channel_videos(
    max_results: int = 50,
    page_token: str | None = None,
    youtube: Any | None = None,
) -> dict[str, Any]:
    try:
        limit = int(max_results)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 50))

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    try:
        channel_response = _execute(service.channels().list(part="contentDetails", mine=True))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    channel_items = channel_response.get("items", [])
    if not channel_items:
        return yc.error_payload("channel_not_found")

    uploads_playlist_id = (
        channel_items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )
    if not uploads_playlist_id:
        return yc.error_payload("channel_uploads_not_found")

    playlist_kwargs: dict[str, Any] = {
        "part": "contentDetails",
        "playlistId": uploads_playlist_id,
        "maxResults": limit,
    }
    if page_token:
        playlist_kwargs["pageToken"] = page_token

    try:
        playlist_response = _execute(service.playlistItems().list(**playlist_kwargs))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    video_ids = [
        item.get("contentDetails", {}).get("videoId")
        for item in playlist_response.get("items", [])
        if item.get("contentDetails", {}).get("videoId")
    ]
    if not video_ids:
        return {
            "videos": [],
            "next_page_token": playlist_response.get("nextPageToken"),
        }

    try:
        details_response = _execute(
            service.videos().list(part="snippet,status,contentDetails", id=",".join(video_ids))
        )
    except Exception as exc:
        return yc.normalize_http_error(exc)

    videos_by_id = {item.get("id"): item for item in details_response.get("items", [])}
    return {
        "videos": [
            _serialize_channel_video_summary(videos_by_id[video_id])
            for video_id in video_ids
            if video_id in videos_by_id
        ],
        "next_page_token": playlist_response.get("nextPageToken"),
    }

def _list_playlists(
    max_results: int = 50,
    page_token: str | None = None,
    youtube: Any | None = None,
) -> dict[str, Any]:
    try:
        limit = int(max_results)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 50))

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    kwargs: dict[str, Any] = {
        "part": "snippet,contentDetails,status",
        "mine": True,
        "maxResults": limit,
    }
    if page_token:
        kwargs["pageToken"] = page_token

    try:
        response = _execute(service.playlists().list(**kwargs))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    playlists = []
    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        playlists.append(
            {
                "playlist_id": item.get("id"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "item_count": item.get("contentDetails", {}).get("itemCount"),
                "privacy": item.get("status", {}).get("privacyStatus"),
            }
        )

    return {
        "playlists": playlists,
        "next_page_token": response.get("nextPageToken"),
    }


def _add_video_to_playlist(
    video_id: str,
    playlist_id: str,
    youtube: Any | None = None,
) -> dict[str, Any]:
    video = video_id.strip() if isinstance(video_id, str) else ""
    if not video:
        return yc.error_payload(
            "validation_error", field="video_id", details="video_id is required"
        )
    playlist = playlist_id.strip() if isinstance(playlist_id, str) else ""
    if not playlist:
        return yc.error_payload(
            "validation_error", field="playlist_id", details="playlist_id is required"
        )

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    body = {
        "snippet": {
            "playlistId": playlist,
            "resourceId": {"kind": "youtube#video", "videoId": video},
        }
    }

    try:
        response = _execute(service.playlistItems().insert(part="snippet", body=body))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    return {
        "playlist_item_id": response.get("id"),
        "playlist_id": playlist,
        "video_id": video,
    }


def _serialize_playlist(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet", {})
    return {
        "playlist_id": item.get("id", ""),
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "privacy": item.get("status", {}).get("privacyStatus", ""),
    }


def _fetch_playlist(
    service: Any,
    playlist_id: str,
    parts: str = "snippet,status",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        response = _execute(service.playlists().list(part=parts, id=playlist_id))
    except Exception as exc:
        return None, yc.normalize_http_error(exc)

    items = response.get("items", [])
    if not items:
        return None, yc.error_payload("playlist_not_found", playlist_id=playlist_id)
    return items[0], None


def _create_playlist(
    title: str,
    description: str = "",
    privacy: str = "private",
    youtube: Any | None = None,
) -> dict[str, Any]:
    title_text = title.strip() if isinstance(title, str) else ""
    if not title_text:
        return yc.error_payload("validation_error", field="title", details="title is required")
    if len(title_text) > MAX_PLAYLIST_TITLE_LENGTH:
        return yc.error_payload(
            "validation_error",
            field="title",
            details="title must be at most 150 characters",
        )

    description_text = description if isinstance(description, str) else ""
    if len(description_text) > MAX_PLAYLIST_DESCRIPTION_LENGTH:
        return yc.error_payload(
            "validation_error",
            field="description",
            details="description must be at most 5000 characters",
        )

    if privacy not in VALID_PRIVACY:
        return yc.error_payload(
            "validation_error",
            field="privacy",
            details="privacy must be private, unlisted, or public",
        )

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    body = {
        "snippet": {"title": title_text, "description": description_text},
        "status": {"privacyStatus": privacy},
    }

    try:
        response = _execute(service.playlists().insert(part="snippet,status", body=body))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    return _serialize_playlist(response if isinstance(response, dict) else {})


def _update_playlist(
    playlist_id: str,
    title: str | None = None,
    description: str | None = None,
    privacy: str | None = None,
    youtube: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(playlist_id, str) or not playlist_id.strip():
        return yc.error_payload("validation_error", field="playlist_id", details="playlist_id is required")
    clean_playlist_id = playlist_id.strip()

    if title is None and description is None and privacy is None:
        return yc.error_payload(
            "validation_error",
            field="changes",
            details="provide at least one of title, description, or privacy",
        )

    title_text: str | None = None
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            return yc.error_payload("validation_error", field="title", details="title cannot be empty")
        title_text = title.strip()
        if len(title_text) > MAX_PLAYLIST_TITLE_LENGTH:
            return yc.error_payload(
                "validation_error",
                field="title",
                details="title must be at most 150 characters",
            )

    if description is not None:
        if not isinstance(description, str):
            return yc.error_payload("validation_error", field="description", details="description must be a string")
        if len(description) > MAX_PLAYLIST_DESCRIPTION_LENGTH:
            return yc.error_payload(
                "validation_error",
                field="description",
                details="description must be at most 5000 characters",
            )

    if privacy is not None and privacy not in VALID_PRIVACY:
        return yc.error_payload(
            "validation_error",
            field="privacy",
            details="privacy must be private, unlisted, or public",
        )

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    item, fetch_error = _fetch_playlist(service, clean_playlist_id)
    if fetch_error:
        return fetch_error
    assert item is not None

    before = _serialize_playlist(item)
    snippet = deepcopy(item.get("snippet", {}))
    status = deepcopy(item.get("status", {}))

    # playlists.update always requires snippet.title, so carry the existing title
    # forward when only other fields change.
    snippet["title"] = title_text if title_text is not None else snippet.get("title", "")
    if description is not None:
        snippet["description"] = description
    changed_parts = ["snippet"]
    body: dict[str, Any] = {
        "id": clean_playlist_id,
        "snippet": {"title": snippet["title"], "description": snippet.get("description", "")},
    }
    if privacy is not None:
        status["privacyStatus"] = privacy
        body["status"] = {"privacyStatus": privacy}
        changed_parts.append("status")

    try:
        response = _execute(
            service.playlists().update(part=",".join(changed_parts), body=body)
        )
    except Exception as exc:
        return yc.normalize_http_error(exc)

    after = _serialize_playlist(response if isinstance(response, dict) else {})
    return {"playlist_id": clean_playlist_id, "updated": True, "before": before, "after": after}


def _delete_playlist(playlist_id: str, youtube: Any | None = None) -> dict[str, Any]:
    if not isinstance(playlist_id, str) or not playlist_id.strip():
        return yc.error_payload("validation_error", field="playlist_id", details="playlist_id is required")
    clean_playlist_id = playlist_id.strip()

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    try:
        _execute(service.playlists().delete(id=clean_playlist_id))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    return {"deleted": True, "playlist_id": clean_playlist_id}


def _list_playlist_items(
    playlist_id: str,
    max_results: int = 50,
    page_token: str | None = None,
    youtube: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(playlist_id, str) or not playlist_id.strip():
        return yc.error_payload("validation_error", field="playlist_id", details="playlist_id is required")
    clean_playlist_id = playlist_id.strip()

    try:
        limit = int(max_results)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 50))

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    kwargs: dict[str, Any] = {
        "part": "snippet,contentDetails",
        "playlistId": clean_playlist_id,
        "maxResults": limit,
    }
    if page_token:
        kwargs["pageToken"] = page_token

    try:
        response = _execute(service.playlistItems().list(**kwargs))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    items = []
    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        resource_id = snippet.get("resourceId", {})
        items.append(
            {
                "playlist_item_id": item.get("id", ""),
                "video_id": resource_id.get("videoId")
                or item.get("contentDetails", {}).get("videoId", ""),
                "title": snippet.get("title", ""),
                "position": snippet.get("position"),
            }
        )

    return {
        "playlist_id": clean_playlist_id,
        "items": items,
        "next_page_token": response.get("nextPageToken"),
    }


def _remove_from_playlist(playlist_item_id: str, youtube: Any | None = None) -> dict[str, Any]:
    if not isinstance(playlist_item_id, str) or not playlist_item_id.strip():
        return yc.error_payload(
            "validation_error",
            field="playlist_item_id",
            details="playlist_item_id is required",
        )
    clean_item_id = playlist_item_id.strip()

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    try:
        _execute(service.playlistItems().delete(id=clean_item_id))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    return {"removed": True, "playlist_item_id": clean_item_id}


def _move_playlist_item(
    playlist_item_id: str,
    position: int,
    youtube: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(playlist_item_id, str) or not playlist_item_id.strip():
        return yc.error_payload(
            "validation_error",
            field="playlist_item_id",
            details="playlist_item_id is required",
        )
    clean_item_id = playlist_item_id.strip()

    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        return yc.error_payload(
            "validation_error",
            field="position",
            details="position must be a non-negative integer (0 = first)",
        )

    service, service_error = _service_or_error(youtube)
    if service_error:
        return service_error

    # playlistItems.update needs playlistId + resourceId in the body, so read the
    # item first to learn which playlist and video it points at.
    try:
        lookup = _execute(service.playlistItems().list(part="snippet", id=clean_item_id))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    lookup_items = lookup.get("items", [])
    if not lookup_items:
        return yc.error_payload("playlist_item_not_found", playlist_item_id=clean_item_id)

    snippet = lookup_items[0].get("snippet", {})
    playlist_id = snippet.get("playlistId", "")
    resource_id = snippet.get("resourceId", {})

    body = {
        "id": clean_item_id,
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": resource_id,
            "position": position,
        },
    }

    try:
        response = _execute(service.playlistItems().update(part="snippet", body=body))
    except Exception as exc:
        return yc.normalize_http_error(exc)

    result_snippet = response.get("snippet", {}) if isinstance(response, dict) else {}
    return {
        "playlist_item_id": clean_item_id,
        "playlist_id": result_snippet.get("playlistId", playlist_id),
        "video_id": result_snippet.get("resourceId", resource_id).get("videoId", "")
        if isinstance(result_snippet.get("resourceId", resource_id), dict)
        else "",
        "position": result_snippet.get("position", position),
    }


def _list_pending_files(config_path: str | Path | None = None) -> dict[str, Any]:
    config, config_error = _load_config(config_path)
    if config_error:
        return config_error

    assert config is not None
    videos_value = config.get("videos_dir")
    thumbs_value = config.get("thumbs_dir")
    if not videos_value or not thumbs_value:
        return yc.error_payload(
            "queue_dir_not_configured",
            hint=(
                "No watched folder is configured. Set videos_dir and thumbs_dir in "
                "config.json to scan a queue, or just pass a full path to upload_video."
            ),
        )

    videos_dir = Path(videos_value).expanduser()
    thumbs_dir = Path(thumbs_value).expanduser()

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

    item, fetch_error = _fetch_video_item(service, video_id.strip())
    if fetch_error:
        return fetch_error
    assert item is not None

    return _serialize_video_details(item)

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
    category_id: str | None = None,
    language: str | None = None,
    playlist_id: str | None = None,
    youtube: Any | None = None,
    config_path: str | Path | None = None,
    media_upload_cls: Any | None = None,
    thumbnail_func: Any | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    config, config_error = _load_config(config_path)
    if config_error:
        return config_error
    assert config is not None

    video = _resolve_input_path(video_path, config.get("videos_dir"))
    if not video.is_file():
        return yc.error_payload("file_not_found", path=str(video))

    thumbnail: Path | None = None
    if thumbnail_path:
        thumbnail = _resolve_input_path(thumbnail_path, config.get("thumbs_dir"))
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

    # The description is used verbatim. Any footer/social links are composed by
    # the caller (skill/prompt) into this text; the server no longer appends a
    # footer from config.json.
    final_description = description if isinstance(description, str) else ""
    if len(final_description) > MAX_DESCRIPTION_LENGTH:
        return yc.error_payload(
            "validation_error",
            field="description",
            details="description must be at most 5000 characters",
        )

    clean_tags, tags_error = _normalize_tags(tags)
    if tags_error:
        return tags_error
    assert clean_tags is not None

    category_text = str(category_id).strip() if category_id is not None else ""
    effective_category_id = category_text or DEFAULT_CATEGORY_ID

    language_text = str(language).strip() if language is not None else ""
    effective_language = language_text or DEFAULT_LANGUAGE

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
            "categoryId": effective_category_id,
            "defaultLanguage": effective_language,
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
                if progress_callback is not None:
                    # Each callback emits an MCP progress notification, which resets
                    # the client's per-request timeout so large uploads do not trip
                    # the default MCP request deadline. Never let it abort the upload.
                    try:
                        progress_callback(
                            percent,
                            getattr(progress, "resumable_progress", None),
                            getattr(progress, "total_size", None),
                        )
                    except Exception:
                        pass
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

    added_to_playlist = False
    playlist_target = playlist_id.strip() if isinstance(playlist_id, str) else ""
    if playlist_target:
        playlist_result = _add_video_to_playlist(
            video_id, playlist_target, youtube=service
        )
        if isinstance(playlist_result, dict) and "error" in playlist_result:
            warnings.append(
                {
                    "code": playlist_result["error"],
                    "message": "Video uploaded, but it was not added to the playlist.",
                    "details": playlist_result,
                }
            )
        else:
            added_to_playlist = True

    return {
        "video_id": video_id,
        "url": f"https://youtu.be/{video_id}",
        "status": effective_privacy,
        "scheduled_time": scheduled_time or None,
        "thumbnail_set": thumbnail_set,
        "playlist_id": playlist_target or None,
        "added_to_playlist": added_to_playlist,
        "warnings": warnings,
    }


@mcp.tool(icons=YOUTUBE_ICONS)
def list_pending_files() -> Any:
    """List queued videos, thumbnails, and filename-stem pairs from config paths."""
    return _safe_call(_list_pending_files)


@mcp.tool(icons=YOUTUBE_ICONS)
def search_competitors(query: str, max_results: int = 10) -> Any:
    """Search competitor videos and return metadata plus statistics."""
    return _safe_call(_search_competitors, query, max_results)


@mcp.tool(icons=YOUTUBE_ICONS)
def get_video_details(video_id: str) -> Any:
    """Get editable metadata, status, content details, and statistics for one video."""
    return _safe_call(_get_video_details, video_id)


@mcp.tool(icons=YOUTUBE_ICONS)
def list_channel_videos(max_results: int = 50, page_token: str | None = None) -> Any:
    """List uploaded channel videos with metadata useful for selecting edit targets."""
    return _safe_call(_list_channel_videos, max_results, page_token)


@mcp.tool(icons=YOUTUBE_ICONS)
def list_playlists(max_results: int = 50, page_token: str | None = None) -> Any:
    """List the authenticated channel's playlists (id, title, item count, privacy).

    Use this to offer the user real playlist choices before uploading. Each entry's
    "playlist_id" is what you pass as playlist_id to upload_video or add_to_playlist.
    """
    return _safe_call(_list_playlists, max_results, page_token)


@mcp.tool(icons=YOUTUBE_ICONS)
def add_to_playlist(video_id: str, playlist_id: str) -> Any:
    """Add an existing YouTube video to one of the channel's playlists."""
    return _safe_call(_add_video_to_playlist, video_id, playlist_id)


@mcp.tool(icons=YOUTUBE_ICONS)
def create_playlist(title: str, description: str = "", privacy: str = "private") -> Any:
    """Create a new playlist on the authenticated channel.

    privacy is private, unlisted, or public (defaults to private). Returns the new
    playlist_id, which can be passed to add_to_playlist or upload_video. Confirm the
    title, description, and privacy with the user before calling.
    """
    return _safe_call(_create_playlist, title, description, privacy)


@mcp.tool(icons=YOUTUBE_ICONS)
def update_playlist(
    playlist_id: str,
    title: str | None = None,
    description: str | None = None,
    privacy: str | None = None,
) -> Any:
    """Rename a playlist or change its description/privacy.

    Pass only the fields you want to change; omitted fields (None) are left as-is.
    Returns before/after state. privacy must be private, unlisted, or public.
    """
    return _safe_call(_update_playlist, playlist_id, title, description, privacy)


@mcp.tool(icons=YOUTUBE_ICONS)
def delete_playlist(playlist_id: str) -> Any:
    """Permanently delete one of the channel's playlists.

    Destructive and irreversible (the videos themselves are not deleted, only the
    playlist). Confirm explicitly with the user before calling.
    """
    return _safe_call(_delete_playlist, playlist_id)


@mcp.tool(icons=YOUTUBE_ICONS)
def list_playlist_items(playlist_id: str, max_results: int = 50, page_token: str | None = None) -> Any:
    """List the videos in one playlist with their playlist_item_id and position.

    The playlist_item_id is what you pass to remove_from_playlist and
    move_playlist_item (the API addresses items by their own id, not by video_id).
    """
    return _safe_call(_list_playlist_items, playlist_id, max_results, page_token)


@mcp.tool(icons=YOUTUBE_ICONS)
def remove_from_playlist(playlist_item_id: str) -> Any:
    """Remove one item from a playlist by its playlist_item_id.

    Get the playlist_item_id from list_playlist_items. This removes the video from
    the playlist only; the video itself is not deleted.
    """
    return _safe_call(_remove_from_playlist, playlist_item_id)


@mcp.tool(icons=YOUTUBE_ICONS)
def move_playlist_item(playlist_item_id: str, position: int) -> Any:
    """Reorder a playlist item to a new zero-based position (0 = first).

    Get the playlist_item_id from list_playlist_items.
    """
    return _safe_call(_move_playlist_item, playlist_item_id, position)


@mcp.tool(icons=YOUTUBE_ICONS)
def edit_video(video_id: str, changes: dict[str, Any], dry_run: bool = False) -> Any:
    """Edit mutable metadata and status fields for one existing YouTube video."""
    return _safe_call(_edit_video, video_id, changes, dry_run)


@mcp.tool(icons=YOUTUBE_ICONS)
def bulk_edit_videos(
    video_ids: list[str] | None = None,
    changes: dict[str, Any] | None = None,
    edits: list[dict[str, Any]] | None = None,
    dry_run: bool = True,
) -> Any:
    """Edit existing YouTube videos by explicit IDs, defaulting to dry-run."""
    return _safe_call(_bulk_edit_videos, video_ids, changes, edits, dry_run)

@mcp.tool(icons=YOUTUBE_ICONS)
def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] | None = None,
    thumbnail_path: str | None = None,
    scheduled_time: str | None = None,
    privacy: str | None = None,
    category_id: str | None = None,
    language: str | None = None,
    playlist_id: str | None = None,
) -> Any:
    """Start a background video upload and return a job_id immediately.

    The upload runs asynchronously so large files cannot trip the MCP client's
    request timeout. This call returns at once with {"job_id", "status":
    "uploading"}; you MUST then poll get_upload_status(job_id) until status is
    "completed" (the final upload result, including video_id/url, is in "result")
    or "error". Do not assume the upload succeeded just because this call returned.

    Before calling this, confirm the metadata with the user: title, description,
    tags, category_id, and language. Do not invent these silently or upload with
    empty tags — propose values, show them, and wait for the user's approval.

    - description is used verbatim. If the user wants a footer with social/links,
      ask them for it and include it in description yourself; the server does not
      add one.
    - category_id is a YouTube category number (e.g. "27" Education, "28" Science
      & Technology, "22" People & Blogs). Falls back to "27" when omitted.
    - language is the BCP-47 metadata language (e.g. "en", "ru"). Ask the user;
      falls back to "en" when omitted.
    - playlist_id (optional) adds the video to that playlist after upload. Ask the
      user up front whether to add it to a playlist; call list_playlists to show
      real options. Omit/leave None to upload without any playlist. A failed
      playlist add does not fail the upload — it surfaces as a warning in result.
    """
    job_id = uuid.uuid4().hex
    now = time.time()
    with _UPLOAD_JOBS_LOCK:
        _prune_upload_jobs_locked()
        _UPLOAD_JOBS[job_id] = {
            "job_id": job_id,
            "status": "uploading",
            "progress_percent": 0,
            "video_path": video_path,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "result": None,
        }

    args = (
        video_path,
        title,
        description,
        tags,
        thumbnail_path,
        scheduled_time,
        privacy,
        category_id,
        language,
        playlist_id,
    )
    thread = threading.Thread(target=_run_upload_job, args=(job_id, args), daemon=True)
    thread.start()

    return {
        "job_id": job_id,
        "status": "uploading",
        "message": (
            "Upload started in the background. Poll get_upload_status(job_id) until "
            "status is 'completed' or 'error'."
        ),
    }


@mcp.tool(icons=YOUTUBE_ICONS)
def get_upload_status(job_id: str) -> Any:
    """Return the current state of a background upload started by upload_video.

    status is "uploading" (see progress_percent), "completed" (the final upload
    result is under "result": video_id, url, thumbnail_set, warnings, ...), or
    "error" (the error payload is under "result"). Poll every few seconds while
    status is "uploading".
    """
    with _UPLOAD_JOBS_LOCK:
        job = _UPLOAD_JOBS.get(job_id)
        if job is None:
            return yc.error_payload(
                "not_found", message=f"No upload job with id {job_id!r}"
            )
        return deepcopy(job)


@mcp.tool(icons=YOUTUBE_ICONS)
def set_thumbnail(video_id: str, image_path: str) -> Any:
    """Set a custom thumbnail for an uploaded YouTube video."""
    return _safe_call(_set_thumbnail, video_id, image_path)


@mcp.tool(icons=YOUTUBE_ICONS)
def get_channel_info() -> Any:
    """Return the authenticated YouTube channel identity and high-level statistics."""
    return _safe_call(_get_channel_info)


if __name__ == "__main__":
    mcp.run(transport="stdio")
