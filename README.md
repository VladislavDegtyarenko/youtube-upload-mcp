# YouTube Automation MCP Server

Local Python MCP server for automating YouTube uploads through YouTube Data API v3.

The server exposes tools for listing queued media, competitor research, video details,
channel verification, uploads, custom thumbnails, and editing existing channel videos.
OAuth browser login starts automatically from MCP on the first authenticated tool call
when `token.json` is missing or invalid. `authorize.py` remains available as a manual
fallback.

## Tools

- `list_pending_files()`
- `search_competitors(query, max_results=10)`
- `list_channel_videos(max_results=50, page_token=None)`
- `get_video_details(video_id)`
- `edit_video(video_id, changes, dry_run=False)`
- `bulk_edit_videos(video_ids=None, changes=None, edits=None, dry_run=True)`
- `upload_video(video_path, title, description, tags, thumbnail_path=None, scheduled_time=None, privacy=None)`
- `set_thumbnail(video_id, image_path)`
- `get_channel_info()`

## 1. Google Cloud Project

1. Open https://console.cloud.google.com.
2. Create a project, for example `YouTube Automation`.
3. Go to APIs & Services > Library.
4. Enable YouTube Data API v3.

## 2. OAuth Consent Screen

Google now shows this as **Google Auth Platform**, so you might not see the old
single-page "External" menu.

1. Go to Google Auth Platform > Overview.
2. If you see "Google Auth Platform not configured yet", click **Get started**.
3. Under App information, enter an app name such as `YouTube Automation`.
4. Choose your user support email, then click **Next**.
5. Under Audience, choose **External** as the user type, then click **Next**.
   - If your project is not inside a Google Workspace organization, External may be the only practical option.
   - If you do not see External before clicking Get started, that is expected in the new UI.
6. Under Contact information, enter your email, then click **Next**.
7. Accept the Google API Services User Data Policy, then click **Continue** and **Create**.
8. Open Google Auth Platform > Audience.
9. Under Test users, click **Add users** and add your Google account. Use the account that owns or manages the YouTube channel.
10. Keep the app in Testing mode for personal/local use.

Without the test user entry, Google may return `access_denied` during authorization.

## 3. Credentials

1. Go to APIs & Services > Credentials.
2. Choose Create credentials > OAuth client ID.
3. Select Desktop app.
4. Download the JSON file.
5. Rename it to `credentials.json`.
6. Place it in this directory next to `authorize.py`.

## 4. Install

```bash
cd youtube-upload-mcp
pip install -r requirements.txt
```

On startup the server routes TLS verification through the operating-system trust store
via `truststore`. This transparently handles HTTPS interception by antivirus software
(AVG, Avast, Kaspersky, ESET, Bitdefender, …) and corporate proxies, whose root
certificate already lives in the OS store but is rejected by the bundled `certifi`
list — the usual cause of `CERTIFICATE_VERIFY_FAILED: unable to get local issuer
certificate`. When `truststore` is unavailable, it falls back to the `certifi` bundle
for Python, Requests, httplib2, token refresh, and the YouTube Data API transport. You
normally do not need to set `SSL_CERT_FILE` manually.

If a certificate error still occurs, the affected tool returns a plain-language message
naming the intercepting product and how to fix it (disable its HTTPS/SSL scanning, e.g.
AVG's "Web Shield", or add `*.googleapis.com` and `*.youtube.com` to its exclusions).

## 5. Authorize

Usually no terminal command is needed. After `credentials.json` is in place, start the
MCP server and call an authenticated tool such as `get_channel_info`. If `token.json`
is missing or invalid, the MCP server opens Google OAuth in Chrome, saves `token.json`,
and then continues the tool call.

If Chrome is installed in a custom location, set `YOUTUBE_MCP_CHROME_PATH` to the full
path of the Chrome executable. If Chrome cannot be found, the system default browser is
used.

Manual fallback:

```bash
python authorize.py
```

The browser opens for Google OAuth. If Google shows an unverified-app warning, use
Advanced and continue to the app, then grant access. The script creates `token.json`.

## 6. Configure

Edit `config.json`:

```json
{
  "videos_dir": "E:/YouTube/queue/videos",
  "thumbs_dir": "E:/YouTube/queue/thumbs",
  "footer_template": "\n\n---\nInstagram: https://...\nPortfolio: https://...\nTikTok: https://...",
  "default_category_id": "27",
  "default_language": "en",
  "default_privacy": "private",
  "made_for_kids": false
}
```

`upload_video` also accepts relative filenames. For relative video paths, it checks
`videos_dir`; for relative thumbnail paths, it checks `thumbs_dir`.

## 7. Connect To Claude Desktop

Use the absolute path to `server.py`:

```json
{
  "mcpServers": {
    "youtube-automation": {
      "command": "python",
      "args": ["D:/WebDev/youtube-upload-mcp/youtube_mcp/server.py"]
    }
  }
}
```

On Windows, if `python` is not found, use the full path to `python.exe` or use `py`.

## 8. Verify

1. In your MCP client, call `get_channel_info`.
2. Confirm it returns the intended YouTube channel.
3. Put a test video in `videos_dir`.
4. Call `list_pending_files`.
5. Upload only a private test video first.

## Editing Existing Videos

Use `list_channel_videos` to inspect uploaded videos and choose explicit `video_id`
targets. The MCP does not auto-filter Shorts or any other video type; the LLM/client
chooses what to edit.

`edit_video` and `bulk_edit_videos` accept these `changes` keys:

- `title`, `description`, `tags`, `category_id`, `default_language`
- `privacy`, `publish_at`, `made_for_kids`, `contains_synthetic_media`
- `embeddable`, `public_stats_viewable`, `license`, `recording_date`

Omit fields to leave them unchanged. Use `description: ""` or `tags: []` to clear
those values. `null` values are rejected to avoid accidental metadata deletion.

Single-video edits execute by default:

```json
{
  "video_id": "VIDEO_ID",
  "changes": {
    "title": "New title",
    "category_id": "27",
    "privacy": "unlisted"
  }
}
```

Bulk edits target explicit IDs only and default to `dry_run: true`:

```json
{
  "video_ids": ["VIDEO_ID_1", "VIDEO_ID_2"],
  "changes": {"category_id": "27"},
  "dry_run": true
}
```

For different edits per video, use `edits`:

```json
{
  "edits": [
    {"video_id": "VIDEO_ID_1", "changes": {"title": "First title"}},
    {"video_id": "VIDEO_ID_2", "changes": {"privacy": "private"}}
  ],
  "dry_run": false
}
```

## Scheduling

If `scheduled_time` is provided, YouTube requires the upload privacy to be `private`.
The server automatically forces private privacy and returns a warning when the caller
asked for `public` or `unlisted`.

Example:

```json
{
  "video_path": "clip.mp4",
  "title": "Test Upload",
  "description": "Description",
  "tags": ["test", "upload"],
  "thumbnail_path": "clip.jpg",
  "scheduled_time": "2026-06-01T18:00:00+03:00",
  "privacy": "public"
}
```

## Thumbnail Notes

Custom thumbnails require an eligible or verified YouTube channel. If thumbnail upload
fails after the video upload succeeds, `upload_video` returns the video URL with
`thumbnail_set: false` and a warning instead of treating the entire upload as failed.

## Quota

YouTube quota costs can change. Check the official quota calculator before estimating
daily capacity: https://developers.google.com/youtube/v3/determine_quota_cost

`search_competitors` intentionally uses a two-step flow: `search.list` first, then
`videos.list` for statistics and tags, because search results do not include video
statistics.

## Tests

Automated tests use fakes and do not call YouTube:

```bash
python -m unittest discover -s tests
```

If you install pytest, this also works:

```bash
python -m pytest
```
