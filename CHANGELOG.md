# Changelog

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
