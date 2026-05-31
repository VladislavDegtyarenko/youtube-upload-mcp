# Changelog

## 2026-05-31 - Fix resumable upload 308 handling

- Fixed `upload_video` failing with `youtube_api_error: "Redirected but the response is missing a Location: header."`. Resumable uploads answer each chunk with `308 Resume Incomplete` (a `Range` header, no `Location`), but httplib2 >= 0.20 lists 308 among its redirect codes and raised `RedirectMissingLocation` before googleapiclient could read it.
- `build_authorized_http` now removes 308 from the httplib2 transport's `redirect_codes`, mirroring `googleapiclient.http.build_http()`, which this server bypasses by constructing `httplib2.Http` directly. The guard is a no-op on older httplib2 and on injected test doubles.

## 2026-05-31 - OS trust store and TLS interception guidance

- Routed TLS verification through the operating-system trust store via `truststore` at server startup, transparently fixing `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` caused by antivirus/proxy HTTPS interception (e.g. AVG Web Shield) whose root CA the bundled `certifi` list cannot accept.
- Kept `certifi` as an automatic fallback: `build_authorized_http` and `build_certified_refresh_request` skip certifi pinning when the OS trust store is active and pin it otherwise.
- Added a friendly diagnosis: certificate-verification errors now return a plain-language payload (`tls_interception`) naming the intercepting product and the steps to disable its HTTPS scanning, instead of a raw SSL stack trace.
- Added tests for OS-trust enablement, the certifi fallback path, interceptor diagnosis, and the error mapping.

## 2026-05-31 - Explicit certifi transport verification

- Pinned OAuth token refresh requests to `certifi` via `requests.Session.verify`.
- Built the YouTube Data API client with `httplib2.Http(ca_certs=certifi.where())`, so channel listing, edits, uploads, and thumbnail calls no longer depend on a broken local CA store.

## 2026-05-31 - Automatic MCP OAuth startup

- Added automatic browser OAuth from MCP when `token.json` is missing or invalid, so users do not need to run `python authorize.py` before the first authenticated tool call.
- Reused the same OAuth helper for `authorize.py`, preferred Chrome when available, and documented the manual fallback and custom Chrome path option.

## 2026-05-31 - Automatic SSL certificate bundle setup

- Added `certifi` and automatic CA bundle configuration for OAuth and YouTube API calls, so MCP users do not need to set `SSL_CERT_FILE` manually on machines with broken local certificate stores.
- Documented the automatic SSL setup in the install instructions and added tests for preserving existing custom certificate configuration.

## 2026-05-31 - Agent changelog instructions

- Added `AGENTS.md` with instructions for future agents to update `CHANGELOG.md` for patches, fixes, feature work, and meaningful documentation changes.

## 2026-05-31 - Universal video editing tools

- Added `list_channel_videos` to inspect uploaded channel videos before choosing edit targets.
- Expanded `get_video_details` with editable metadata, status, content details, and statistics.
- Added `edit_video` for single-video metadata/status edits with optional dry-run.
- Added `bulk_edit_videos` for explicit-ID bulk edits, defaulting to `dry_run=true`.
- Added package compatibility files under `youtube_mcp/` for the documented MCP server path.
- Added tests for single edit, bulk edit, dry-run behavior, partial failures, and channel video listing.

## 2026-05-31 - Initial YouTube upload MCP script

- Created the local Python MCP server for YouTube automation.
- Added OAuth authorization via `authorize.py` and reusable YouTube API client setup.
- Added tools for pending file discovery, competitor search, video details, uploads, thumbnails, and channel info.
- Added configuration through `config.json` for videos, thumbnails, upload defaults, privacy, language, and kids settings.
- Added automated tests covering config loading, file listing, uploads, thumbnail errors, API error mapping, and auth handling.
