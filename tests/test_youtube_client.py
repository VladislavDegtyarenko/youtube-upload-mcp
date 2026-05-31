from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from youtube_mcp import youtube_client as yc


class FakeCredentials:
    instance: "FakeCredentials"

    def __init__(self, *, valid: bool, expired: bool, refresh_token: str | None = None, fail_refresh: bool = False):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.fail_refresh = fail_refresh
        self.refreshed = False

    @classmethod
    def from_authorized_user_file(cls, path: str, scopes: list[str]) -> "FakeCredentials":
        return cls.instance

    def refresh(self, request) -> None:
        if self.fail_refresh:
            raise RuntimeError("refresh failed")
        self.valid = True
        self.expired = False
        self.refreshed = True

    def to_json(self) -> str:
        return '{"refreshed": true}'


class FakeHttpError(Exception):
    def __init__(self, status: int, content: bytes):
        super().__init__("http error")
        self.resp = type("Resp", (), {"status": status})()
        self.content = content


class YouTubeClientTests(unittest.TestCase):
    def test_missing_token_returns_reauth_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, err = yc.get_youtube_service(base_dir=tmp)

            self.assertIsNone(service)
            self.assertEqual(err, {"error": "reauth_required", "hint": "run: python authorize.py"})

    def test_refreshes_expired_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "token.json").write_text("{}", encoding="utf-8")
            FakeCredentials.instance = FakeCredentials(valid=False, expired=True, refresh_token="refresh")

            service, err = yc.get_youtube_service(
                base_dir=root,
                credentials_cls=FakeCredentials,
                request_factory=lambda: object(),
                build_func=lambda service_name, version, credentials: {
                    "service_name": service_name,
                    "version": version,
                    "credentials": credentials,
                },
            )

            self.assertIsNone(err)
            self.assertEqual(service["service_name"], "youtube")
            self.assertEqual(service["version"], "v3")
            self.assertTrue(FakeCredentials.instance.refreshed)
            self.assertEqual((root / "token.json").read_text(encoding="utf-8"), '{"refreshed": true}')

    def test_refresh_failure_returns_reauth_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "token.json").write_text("{}", encoding="utf-8")
            FakeCredentials.instance = FakeCredentials(
                valid=False,
                expired=True,
                refresh_token="refresh",
                fail_refresh=True,
            )

            service, err = yc.get_youtube_service(
                base_dir=root,
                credentials_cls=FakeCredentials,
                request_factory=lambda: object(),
                build_func=lambda *args, **kwargs: object(),
            )

            self.assertIsNone(service)
            self.assertEqual(err["error"], "reauth_required")

    def test_quota_http_error_maps_to_quota_exceeded(self) -> None:
        exc = FakeHttpError(
            403,
            b'{"error":{"code":403,"message":"quota","errors":[{"reason":"quotaExceeded","message":"quota"}]}}',
        )

        result = yc.normalize_http_error(exc)

        self.assertEqual(result, {"error": "quota_exceeded", "resets_at": "midnight PT"})

    def test_thumbnail_403_maps_to_channel_not_verified(self) -> None:
        exc = FakeHttpError(
            403,
            b'{"error":{"code":403,"message":"forbidden","errors":[{"reason":"forbidden","message":"forbidden"}]}}',
        )

        result = yc.normalize_http_error(exc, context="thumbnail")

        self.assertEqual(result, {"error": "channel_not_verified"})


if __name__ == "__main__":
    unittest.main()
