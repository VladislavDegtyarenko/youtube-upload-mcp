# Changelog

## 2026-06-01

### Ignore empty videos_dir/thumbs_dir instead of erroring

- `load_config` no longer rejects an empty or whitespace-only `videos_dir`/`thumbs_dir` with `config_invalid`. Such values are now treated as "not set" — the key is dropped — so a `config.json` left with blank folder strings loads cleanly and the user just passes full paths to `upload_video`. A non-string value is still rejected.
- Added a test covering blank/whitespace queue-dir values.

### Make videos_dir/thumbs_dir optional

- `load_config` no longer requires `videos_dir` and `thumbs_dir`. They are now an optional "watched folder" used only by `list_pending_files` and for resolving relative filenames; an absolute path passed to `upload_video` works without any folder in `config.json`, so a non-technical user never has to hand-edit JSON. They are still validated as non-empty strings when present.
- `list_pending_files` returns a `queue_dir_not_configured` hint (instead of crashing) when no folder is configured, telling the caller to pass a full path. `_upload_video` resolves paths via `config.get(...)`, falling back to the path as given.
- Updated the `youtube-upload` skill to ask for the full video/thumbnail path when no watched folder is set, and updated the README to show `config.json` (and every key in it) as optional.

### Move footer, category, and language out of config.json

- Removed `footer_template`, `default_category_id`, and `default_language` from `config.json` and `DEFAULT_CONFIG`. Real users should not hand-edit JSON, so this per-video metadata is now supplied conversationally by the `youtube-upload` skill/prompt. `config.json` keeps only environment settings (`videos_dir`, `thumbs_dir`, `default_privacy`, `made_for_kids`).
- `upload_video` no longer appends a footer; `description` is uploaded verbatim, and the skill asks the user for any social/footer links and composes them into the description (supersedes the earlier automatic blank-line separator).
- Added a `language` argument to `upload_video`. `category_id` and `language` now fall back to module constants `27` (Education) and `en` when omitted, instead of reading config.
- Updated the `youtube-upload` skill: it now first asks the user to describe the video (topic, audience, key points) and builds keyword research and all metadata from that description, and it asks for footer links and metadata language when not provided.

### YouTube upload skill with vidIQ SEO packaging

- Added `.claude/skills/youtube-upload/SKILL.md`, an on-demand workflow skill for the upload/edit flow. It enforces confirming SEO metadata with the user before `upload_video`, bakes vidIQ guidance (keyword-first, title formula, description first-line hook, chapters, 2–3 hashtags, tag disambiguation, thumbnail/CTR rules, private-first, schedule-by-activity) into the metadata draft, and maps the Studio-only steps the MCP cannot do (playlist, captions, end screen, cards, pinned comment, analytics) plus the Title Flip / post-publish iteration via `edit_video`.
- Distilled from `docs/vidiq_youtube_studio_seo_checklist.md`.

### Per-upload category and metadata confirmation guidance

- `upload_video` now accepts an explicit `category_id` argument. When omitted it falls back to `config.json`'s `default_category_id`, so existing behavior is unchanged, but the LLM/client can now choose a category per upload instead of always silently using the config default (previously the tool had no way to set the category at upload time).
- Rewrote the `upload_video` tool docstring to instruct the assistant to confirm title, description, tags, and category with the user — proposing values and waiting for approval — rather than inventing metadata or uploading with empty tags.
- Added tests for explicit-category passthrough and the config-default fallback.

### Automatic blank-line separator before footer

- `upload_video` now inserts two blank lines between the description and `footer_template` automatically, so `config.json` no longer needs a leading `\n\n` in the footer. The separator is skipped when the description is empty (footer starts clean) or when no footer is configured.

## 2026-05-31

### Fix resumable upload 308 handling

- Fixed `upload_video` failing with `youtube_api_error: "Redirected but the response is missing a Location: header."`. Resumable uploads answer each chunk with `308 Resume Incomplete` (a `Range` header, no `Location`), but httplib2 >= 0.20 lists 308 among its redirect codes and raised `RedirectMissingLocation` before googleapiclient could read it.
- `build_authorized_http` now removes 308 from the httplib2 transport's `redirect_codes`, mirroring `googleapiclient.http.build_http()`, which this server bypasses by constructing `httplib2.Http` directly. The guard is a no-op on older httplib2 and on injected test doubles.

### OS trust store and TLS interception guidance

- Routed TLS verification through the operating-system trust store via `truststore` at server startup, transparently fixing `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` caused by antivirus/proxy HTTPS interception (e.g. AVG Web Shield) whose root CA the bundled `certifi` list cannot accept.
- Kept `certifi` as an automatic fallback: `build_authorized_http` and `build_certified_refresh_request` skip certifi pinning when the OS trust store is active and pin it otherwise.
- Added a friendly diagnosis: certificate-verification errors now return a plain-language payload (`tls_interception`) naming the intercepting product and the steps to disable its HTTPS scanning, instead of a raw SSL stack trace.
- Added tests for OS-trust enablement, the certifi fallback path, interceptor diagnosis, and the error mapping.

### Explicit certifi transport verification

- Pinned OAuth token refresh requests to `certifi` via `requests.Session.verify`.
- Built the YouTube Data API client with `httplib2.Http(ca_certs=certifi.where())`, so channel listing, edits, uploads, and thumbnail calls no longer depend on a broken local CA store.

### Automatic MCP OAuth startup

- Added automatic browser OAuth from MCP when `token.json` is missing or invalid, so users do not need to run `python authorize.py` before the first authenticated tool call.
- Reused the same OAuth helper for `authorize.py`, preferred Chrome when available, and documented the manual fallback and custom Chrome path option.

### Automatic SSL certificate bundle setup

- Added `certifi` and automatic CA bundle configuration for OAuth and YouTube API calls, so MCP users do not need to set `SSL_CERT_FILE` manually on machines with broken local certificate stores.
- Documented the automatic SSL setup in the install instructions and added tests for preserving existing custom certificate configuration.

### Agent changelog instructions

- Added `AGENTS.md` with instructions for future agents to update `CHANGELOG.md` for patches, fixes, feature work, and meaningful documentation changes.

### Universal video editing tools

- Added `list_channel_videos` to inspect uploaded channel videos before choosing edit targets.
- Expanded `get_video_details` with editable metadata, status, content details, and statistics.
- Added `edit_video` for single-video metadata/status edits with optional dry-run.
- Added `bulk_edit_videos` for explicit-ID bulk edits, defaulting to `dry_run=true`.
- Added package compatibility files under `youtube_mcp/` for the documented MCP server path.
- Added tests for single edit, bulk edit, dry-run behavior, partial failures, and channel video listing.

### Initial YouTube upload MCP script

- Created the local Python MCP server for YouTube automation.
- Added OAuth authorization via `authorize.py` and reusable YouTube API client setup.
- Added tools for pending file discovery, competitor search, video details, uploads, thumbnails, and channel info.
- Added configuration through `config.json` for videos, thumbnails, upload defaults, privacy, language, and kids settings.
- Added automated tests covering config loading, file listing, uploads, thumbnail errors, API error mapping, and auth handling.
