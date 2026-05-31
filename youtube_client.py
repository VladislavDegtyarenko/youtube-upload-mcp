from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import webbrowser
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
    "default_category_id": "22",
    "default_language": "en",
    "default_privacy": "private",
    "made_for_kids": False,
}

QUOTA_REASONS = {
    "quotaExceeded",
    "dailyLimitExceeded",
    "userRateLimitExceeded",
    "rateLimitExceeded",
}

CERTIFICATE_ENV_VARS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTPLIB2_CA_CERTS")


def error_payload(code: str, **fields: Any) -> dict[str, Any]:
    payload = {"error": code}
    payload.update(fields)
    return payload


def reauth_required() -> dict[str, str]:
    return {
        "error": "reauth_required",
        "hint": "OAuth should open automatically from MCP. Manual fallback: python authorize.py",
    }


def dependency_missing(package: str) -> dict[str, str]:
    return {
        "error": "dependency_missing",
        "package": package,
        "hint": "run: pip install -r requirements.txt",
    }


def oauth_credentials_missing(credentials_path: str | Path = CREDENTIALS_PATH) -> dict[str, Any]:
    return error_payload(
        "oauth_credentials_missing",
        path=str(credentials_path),
        hint="Download a Desktop OAuth client JSON from Google Cloud Console and save it as credentials.json.",
    )


def get_certifi_bundle_path(
    certifi_where: Callable[[], str] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    if certifi_where is None:
        try:
            import certifi
        except ImportError:
            return None, {
                "configured": False,
                "reason": "certifi_missing",
                "hint": "run: pip install -r requirements.txt",
            }

        certifi_where = certifi.where

    try:
        return str(certifi_where()), None
    except Exception as exc:
        return None, {
            "configured": False,
            "reason": "certifi_unavailable",
            "message": str(exc),
        }


def configure_ssl_certificates(
    certifi_where: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Point Google HTTP stacks at a reliable CA bundle when one is not configured."""
    bundle_path = next((os.environ[name] for name in CERTIFICATE_ENV_VARS if os.environ.get(name)), None)
    source = "environment" if bundle_path else "certifi"

    if bundle_path is None:
        bundle_path, error = get_certifi_bundle_path(certifi_where)
        if error:
            return error

    assert bundle_path is not None
    for name in CERTIFICATE_ENV_VARS:
        os.environ.setdefault(name, bundle_path)

    return {
        "configured": True,
        "path": bundle_path,
        "source": source,
        "env_vars": list(CERTIFICATE_ENV_VARS),
    }


def build_certified_refresh_request(
    request_cls: Any | None = None,
    session_factory: Callable[[], Any] | None = None,
    certifi_where: Callable[[], str] | None = None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Create a google-auth Requests transport pinned to certifi's CA bundle."""
    bundle_path, bundle_error = get_certifi_bundle_path(certifi_where)
    if bundle_error:
        return None, bundle_error
    assert bundle_path is not None

    if request_cls is None:
        try:
            from google.auth.transport.requests import Request
        except ImportError:
            return None, dependency_missing("google-auth")

        request_cls = Request

    if session_factory is None:
        try:
            import requests
        except ImportError:
            return None, dependency_missing("requests")

        session_factory = requests.Session

    session = session_factory()
    session.verify = bundle_path
    return request_cls(session=session), None


def build_authorized_http(
    credentials: Any,
    http_cls: Any | None = None,
    authorized_http_cls: Any | None = None,
    certifi_where: Callable[[], str] | None = None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Create an AuthorizedHttp for googleapiclient using certifi for TLS verification."""
    bundle_path, bundle_error = get_certifi_bundle_path(certifi_where)
    if bundle_error:
        return None, bundle_error
    assert bundle_path is not None

    if http_cls is None:
        try:
            import httplib2
        except ImportError:
            return None, dependency_missing("httplib2")

        http_cls = httplib2.Http

    if authorized_http_cls is None:
        try:
            from google_auth_httplib2 import AuthorizedHttp
        except ImportError:
            return None, dependency_missing("google-auth-httplib2")

        authorized_http_cls = AuthorizedHttp

    http = http_cls(ca_certs=bundle_path)
    return authorized_http_cls(credentials, http=http), None


def _find_chrome_executable() -> str | None:
    custom_path = os.environ.get("YOUTUBE_MCP_CHROME_PATH")
    if custom_path:
        path = Path(custom_path).expanduser()
        if path.exists():
            return str(path)

    candidates: list[Path] = []
    if os.name == "nt":
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env_name)
            if root:
                candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    else:
        for executable in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(executable)
            if found:
                return found

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _open_in_chrome_or_default(
    url: str,
    new: int = 1,
    autoraise: bool = True,
    fallback_open: Callable[..., bool] | None = None,
) -> bool:
    chrome_path = _find_chrome_executable()
    if chrome_path:
        try:
            return bool(webbrowser.BackgroundBrowser(chrome_path).open(url, new=new, autoraise=autoraise))
        except Exception:
            pass

    opener = fallback_open or webbrowser.open
    return bool(opener(url, new=new, autoraise=autoraise))


def _register_chrome_browser() -> str | None:
    chrome_path = _find_chrome_executable()
    if not chrome_path:
        return None

    browser_name = "youtube_mcp_chrome"
    try:
        webbrowser.register(browser_name, None, webbrowser.BackgroundBrowser(chrome_path))
    except Exception:
        return None
    return browser_name


def _accepts_browser_argument(func: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False

    return "browser" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _run_local_server_with_preferred_browser(
    flow: Any,
    browser_open: Callable[..., bool] | None = None,
) -> Any:
    run_kwargs: dict[str, Any] = {
        "port": 0,
        "open_browser": True,
        "authorization_prompt_message": None,
        "success_message": "Authorization complete. You can close this browser tab and return to Claude.",
    }
    if browser_open is None:
        browser_name = _register_chrome_browser()
        if browser_name and _accepts_browser_argument(flow.run_local_server):
            run_kwargs["browser"] = browser_name
            return flow.run_local_server(**run_kwargs)

    original_open = webbrowser.open

    def open_auth_url(url: str, new: int = 0, autoraise: bool = True) -> bool:
        if browser_open is not None:
            return bool(browser_open(url, new=new, autoraise=autoraise))
        return bool(_open_in_chrome_or_default(url, new=new, autoraise=autoraise, fallback_open=original_open))

    webbrowser.open = open_auth_url
    try:
        return flow.run_local_server(**run_kwargs)
    finally:
        webbrowser.open = original_open


def authorize_youtube(
    base_dir: str | Path | None = None,
    flow_cls: Any | None = None,
    browser_open: Callable[..., bool] | None = None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Run the installed-app OAuth flow and persist token.json."""
    configure_ssl_certificates()

    root = Path(base_dir) if base_dir is not None else BASE_DIR
    credentials_path = root / "credentials.json"
    token_path = root / "token.json"

    if not credentials_path.exists():
        return None, oauth_credentials_missing(credentials_path)

    if flow_cls is None:
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError:
            return None, dependency_missing("google-auth-oauthlib")

        flow_cls = InstalledAppFlow

    try:
        flow = flow_cls.from_client_secrets_file(str(credentials_path), SCOPES)
        credentials = _run_local_server_with_preferred_browser(flow, browser_open=browser_open)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    except Exception as exc:
        return None, error_payload(
            "oauth_failed",
            message=str(exc),
            hint="Try running python authorize.py if the browser did not complete sign-in.",
        )

    return credentials, None


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
    http_factory: Callable[[Any], tuple[Any | None, dict[str, Any] | None]] | None = None,
    auto_authorize: bool = True,
    authorize_func: Callable[[str | Path], tuple[Any | None, dict[str, Any] | None]] | None = None,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Build a YouTube API service, opening browser OAuth when token.json is missing."""
    configure_ssl_certificates()

    root = Path(base_dir) if base_dir is not None else BASE_DIR
    token_path = root / "token.json"

    creds: Any | None = None
    if token_path.exists():
        if credentials_cls is None:
            try:
                from google.oauth2.credentials import Credentials
            except ImportError:
                return None, dependency_missing("google-auth")

            credentials_cls = Credentials

        try:
            creds = credentials_cls.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None

    if creds is not None and getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        try:
            if request_factory is None:
                refresh_request, refresh_error = build_certified_refresh_request()
                if refresh_error:
                    return None, refresh_error
            else:
                refresh_request = request_factory()

            creds.refresh(refresh_request)
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            creds = None

    if creds is None or not getattr(creds, "valid", False):
        if not auto_authorize:
            return None, reauth_required()

        if authorize_func is None:
            creds, auth_error = authorize_youtube(base_dir=root)
        else:
            creds, auth_error = authorize_func(root)
        if auth_error:
            return None, auth_error
        if not getattr(creds, "valid", False):
            return None, error_payload(
                "oauth_failed",
                message="authorization did not return valid credentials",
                hint="Try running python authorize.py.",
            )

    default_build = build_func is None
    if default_build:
        try:
            from googleapiclient.discovery import build
        except ImportError:
            return None, dependency_missing("google-api-python-client")

        build_func = build

    try:
        if default_build or http_factory is not None:
            if http_factory is None:
                authorized_http, http_error = build_authorized_http(creds)
            else:
                authorized_http, http_error = http_factory(creds)
            if http_error:
                return None, http_error
            return build_func("youtube", "v3", http=authorized_http), None

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
