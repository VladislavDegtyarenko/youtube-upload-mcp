from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class FakeFlow:
    path: str
    scopes: list[str]
    run_kwargs: dict[str, object]

    @classmethod
    def from_client_secrets_file(cls, path: str, scopes: list[str]) -> "FakeFlow":
        cls.path = path
        cls.scopes = scopes
        return cls()

    def run_local_server(self, **kwargs) -> FakeCredentials:
        self.__class__.run_kwargs = kwargs
        return FakeCredentials.instance


class FakeSession:
    def __init__(self):
        self.verify: str | None = None


class FakeRefreshRequest:
    def __init__(self, session: FakeSession):
        self.session = session


class FakeHttp:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeAuthorizedHttp:
    def __init__(self, credentials: FakeCredentials, http: FakeHttp):
        self.credentials = credentials
        self.http = http


def _failing_injector() -> None:
    raise RuntimeError("truststore unavailable")


class YouTubeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        # Each test starts with TLS verification on the certifi path unless it opts in.
        self._saved_os_trust = yc.os_trust_enabled()
        yc.set_os_trust_enabled(False)
        self.addCleanup(lambda: yc.set_os_trust_enabled(self._saved_os_trust))

    def test_enable_os_trust_store_injects_and_flips_flag(self) -> None:
        calls: list[int] = []
        result = yc.enable_os_trust_store(injector=lambda: calls.append(1))

        self.assertTrue(result["configured"])
        self.assertEqual(result["source"], "os_trust_store")
        self.assertEqual(calls, [1])
        self.assertTrue(yc.os_trust_enabled())

    def test_enable_os_trust_store_reports_failure_without_flipping_flag(self) -> None:
        result = yc.enable_os_trust_store(injector=_failing_injector)

        self.assertFalse(result["configured"])
        self.assertEqual(result["reason"], "truststore_failed")
        self.assertFalse(yc.os_trust_enabled())

    def test_configure_prefers_os_trust_store(self) -> None:
        result = yc.configure_ssl_certificates(injector=lambda: None)

        self.assertTrue(result["configured"])
        self.assertEqual(result["source"], "os_trust_store")

    def test_configure_ssl_certificates_uses_certifi_when_os_trust_unavailable(self) -> None:
        with patch.dict(yc.os.environ, {}, clear=True):
            result = yc.configure_ssl_certificates(
                certifi_where=lambda: "C:/certifi/cacert.pem",
                injector=_failing_injector,
            )

            self.assertTrue(result["configured"])
            self.assertEqual(result["source"], "certifi")
            for name in yc.CERTIFICATE_ENV_VARS:
                self.assertEqual(yc.os.environ[name], "C:/certifi/cacert.pem")

    def test_configure_ssl_certificates_reuses_existing_bundle(self) -> None:
        with patch.dict(yc.os.environ, {"SSL_CERT_FILE": "D:/custom-ca.pem"}, clear=True):
            result = yc.configure_ssl_certificates(
                certifi_where=lambda: "C:/certifi/cacert.pem",
                injector=_failing_injector,
            )

            self.assertTrue(result["configured"])
            self.assertEqual(result["source"], "environment")
            for name in yc.CERTIFICATE_ENV_VARS:
                self.assertEqual(yc.os.environ[name], "D:/custom-ca.pem")

    def test_build_certified_refresh_request_sets_requests_verify(self) -> None:
        request, err = yc.build_certified_refresh_request(
            request_cls=FakeRefreshRequest,
            session_factory=FakeSession,
            certifi_where=lambda: "C:/certifi/cacert.pem",
            os_trust=False,
        )

        self.assertIsNone(err)
        self.assertIsInstance(request, FakeRefreshRequest)
        self.assertEqual(request.session.verify, "C:/certifi/cacert.pem")

    def test_build_certified_refresh_request_skips_verify_under_os_trust(self) -> None:
        request, err = yc.build_certified_refresh_request(
            request_cls=FakeRefreshRequest,
            session_factory=FakeSession,
            os_trust=True,
        )

        self.assertIsNone(err)
        self.assertIsNone(request.session.verify)

    def test_build_authorized_http_sets_httplib2_ca_certs(self) -> None:
        credentials = FakeCredentials(valid=True, expired=False)

        authorized_http, err = yc.build_authorized_http(
            credentials,
            http_cls=FakeHttp,
            authorized_http_cls=FakeAuthorizedHttp,
            certifi_where=lambda: "C:/certifi/cacert.pem",
            os_trust=False,
        )

        self.assertIsNone(err)
        self.assertIsInstance(authorized_http, FakeAuthorizedHttp)
        self.assertIs(authorized_http.credentials, credentials)
        self.assertEqual(authorized_http.http.kwargs["ca_certs"], "C:/certifi/cacert.pem")

    def test_build_authorized_http_omits_ca_certs_under_os_trust(self) -> None:
        credentials = FakeCredentials(valid=True, expired=False)

        authorized_http, err = yc.build_authorized_http(
            credentials,
            http_cls=FakeHttp,
            authorized_http_cls=FakeAuthorizedHttp,
            os_trust=True,
        )

        self.assertIsNone(err)
        self.assertNotIn("ca_certs", authorized_http.http.kwargs)

    def test_diagnose_tls_interception_identifies_antivirus(self) -> None:
        der = b"\x30\x82" + b"AVG Web/Mail Shield Root generated by AVG Antivirus"
        diagnosis = yc.diagnose_tls_interception(der_loader=lambda host: der)

        self.assertIsNotNone(diagnosis)
        self.assertEqual(diagnosis["interceptor"], "AVG Antivirus")
        self.assertIn("HTTPS", diagnosis["hint"])

    def test_diagnose_tls_interception_returns_none_for_clean_chain(self) -> None:
        der = b"\x30\x82" + b"GTS Root R1 Google Trust Services"
        self.assertIsNone(yc.diagnose_tls_interception(der_loader=lambda host: der))

    def test_normalize_http_error_surfaces_interception_hint(self) -> None:
        import ssl

        exc = ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer")
        with patch.object(
            yc,
            "diagnose_tls_interception",
            return_value={
                "interceptor": "AVG Antivirus",
                "message": "intercepted",
                "hint": "turn off HTTPS scanning",
            },
        ):
            payload = yc.normalize_http_error(exc)

        self.assertEqual(payload["error"], "tls_interception")
        self.assertEqual(payload["interceptor"], "AVG Antivirus")

    def test_missing_token_returns_reauth_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, err = yc.get_youtube_service(base_dir=tmp, auto_authorize=False)

            self.assertIsNone(service)
            self.assertEqual(err["error"], "reauth_required")

    def test_authorize_youtube_writes_token_and_suppresses_stdout_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "credentials.json").write_text("{}", encoding="utf-8")
            FakeCredentials.instance = FakeCredentials(valid=True, expired=False)

            creds, err = yc.authorize_youtube(base_dir=root, flow_cls=FakeFlow)

            self.assertIsNone(err)
            self.assertIs(creds, FakeCredentials.instance)
            self.assertEqual(FakeFlow.path, str(root / "credentials.json"))
            self.assertEqual(FakeFlow.scopes, yc.SCOPES)
            self.assertEqual((root / "token.json").read_text(encoding="utf-8"), '{"refreshed": true}')
            self.assertIsNone(FakeFlow.run_kwargs["authorization_prompt_message"])
            self.assertTrue(FakeFlow.run_kwargs["open_browser"])
            self.assertEqual(FakeFlow.run_kwargs["port"], 0)

    def test_missing_token_auto_authorizes_and_builds_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            FakeCredentials.instance = FakeCredentials(valid=True, expired=False)
            authorize_calls: list[Path] = []

            def authorize_func(base_dir: str | Path):
                authorize_calls.append(Path(base_dir))
                return FakeCredentials.instance, None

            service, err = yc.get_youtube_service(
                base_dir=root,
                authorize_func=authorize_func,
                build_func=lambda service_name, version, credentials: {
                    "service_name": service_name,
                    "version": version,
                    "credentials": credentials,
                },
            )

            self.assertIsNone(err)
            self.assertEqual(authorize_calls, [root])
            self.assertEqual(service["service_name"], "youtube")
            self.assertIs(service["credentials"], FakeCredentials.instance)

    def test_get_youtube_service_passes_authorized_http_to_googleapiclient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "token.json").write_text("{}", encoding="utf-8")
            FakeCredentials.instance = FakeCredentials(valid=True, expired=False)
            authorized_http = object()

            service, err = yc.get_youtube_service(
                base_dir=root,
                credentials_cls=FakeCredentials,
                http_factory=lambda credentials: (authorized_http, None),
                build_func=lambda service_name, version, http: {
                    "service_name": service_name,
                    "version": version,
                    "http": http,
                },
            )

            self.assertIsNone(err)
            self.assertEqual(service["service_name"], "youtube")
            self.assertEqual(service["version"], "v3")
            self.assertIs(service["http"], authorized_http)

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
                auto_authorize=False,
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
