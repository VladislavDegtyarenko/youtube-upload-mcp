from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TOKEN_PATH = BASE_DIR / "token.json"
CREDENTIALS_PATH = BASE_DIR / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "footer_template": "",
    "default_category_id": "22", // "Education" category id
    "default_language": "en", // "English" language code
    "default_privacy": "private",
    "made_for_kids": False,
}

QUOTA_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "userRateLimitExceeded",
    "rateLimitExceeded",
}


def error_payload(code: str, **fields: Any) -> dict[str, Any]:
    payload = {"error": code}
    payload.update(fields)
    return payload


def reauth_required() -> dict[str, str]:
    return {
        "error": "reauth_required",
        "hint": "run: python authorize.py",
    }


def dependency_missing(package: str) -> dict[str, str]:
    return {
        "error": "dependency_missing",
        "package": package,
        "hint": "run: pip install -r requirements.txt",
    }


def load_config(
    config_path: str | Path | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    path = Path(config_path) if config_path is not None else CONFIG_PATH
    if not path.exists():
        return None, error_payload("config_missing", path=str(path))

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, error_payload("config_invalid", details=str(exc))

    if not isinstance(data, dict):
        return None, error_payload("config_invalid", details="config root must be an object")

    config = {**DEFAULT_CONFIG, **data}
    for required in ("videos_dir", "thumbs_dir"):
        if not isinstance(config.get(required), str) or not config[required].strip():
            return None, error_payload(
                "config_invalid",
                details=f"{required} must be a non-empty string",
            )

    if config["default_privacy"] not in {"private", "unlisted", "public"}:
        return None, error_payload(
            "config_invalid",
            details="default_privacy must be private, unlisted, or public",
        )

    config["made_for_kids"] = bool(config.get("made_for_kids", False))
    return config, None


def get_youtube_service(
    base_dir: str | Path | None = None,
    credentials_cls: Any | None = None,
    request_factory: Callable[[], Any] | None = None,
    build_func: Callable[..., Any] | None = None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Build a YouTube API service from a saved token without interactive OAuth."""
    root = Path(base_dir) if base_dir is not None else BASE_DIR
    token_path = root / "token.json"

    if not token_path.exists():
        return None, reauth_required()

    if credentials_cls is None:
        try:
            from google.oauth2.credentials import Credentials
        except ImportError:
            return None, dependency_missing("google-auth")

        credentials_cls = Credentials

    if request_factory is None:
        try:
            from google.auth.transport.requests import Request
        except ImportError:
            return None, dependency_missing("google-auth")

        request_factory = Request

    if build_func is None:
        try:
            from googleapiclient.discovery import build
        except ImportError:
            return None, dependency_missing("google-api-python-client")

        build_func = build

    try:
        creds = credentials_cls.from_authorized_user_file(str(token_path), SCOPES)
    except Exception:
        return None, reauth_required()

    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        try:
            creds.refresh(request_factory())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            return None, reauth_required()

    if not getattr(creds, "valid", False):
        return None, reauth_required()

    try:
        return build_func("youtube", "v3", credentials=creds), None
    except Exception as exc:
        return None, normalize_http_error(exc)


def normalize_http_error(exc: Exception, context: str | None = None) -> dict[str, Any]:
    """Map Google API errors into stable MCP-friendly payloads."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    reason: str | None = None
    message = str(exc)
    content = getattr(exc, "content", None)

    if content:
        try:
            if isinstance(content, bytes):
                content_text = content.decode("utf-8")
            else:
                content_text = str(content)
            parsed = json.loads(content_text)
            api_error = parsed.get("error", {})
            if isinstance(api_error, dict):
                status = api_error.get("code", status)
                message = api_error.get("message", message)
                errors = api_error.get("errors") or []
                if errors and isinstance(errors[0], dict):
                    reason = errors[0].get("reason")
                    message = errors[0].get("message", message)
        except Exception:
            pass

    reason_text = reason or ""
    if reason_text in QUOTA_REASONS or "quota" in reason_text.lower():
        return error_payload("quota_exceeded", resets_at="midnight PT")

    if context == "thumbnail" and status == 403:
        return error_payload("channel_not_verified")

    return error_payload(
        "youtube_api_error",
        status=status,
        reason=reason_text,
        message=message,
    )


def get_media_upload_class() -> tuple[Any | None, dict[str, Any] | None]:
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return None, dependency_missing("google-api-python-client")

    return MediaFileUpload, None
