# YouTube Automation MCP Server

Local Python MCP server for automating YouTube uploads through YouTube Data API v3.

The server exposes tools for listing queued media, competitor research, video details,
channel verification, uploads, and custom thumbnails. OAuth browser login is handled
only by `authorize.py`; the MCP stdio server never opens a browser.

## Tools

- `list_pending_files()`
- `search_competitors(query, max_results=10)`
- `get_video_details(video_id)`
- `upload_video(video_path, title, description, tags, thumbnail_path=None, scheduled_time=None, privacy=None)`
- `set_thumbnail(video_id, image_path)`
- `get_channel_info()`

## 1. Google Cloud Project

1. Open https://console.cloud.google.com.
2. Create a project, for example `YouTube Automation`.
3. Go to APIs & Services > Library.
4. Enable YouTube Data API v3.

## 2. OAuth Consent Screen

1. Go to APIs & Services > OAuth consent screen.
2. Choose External, then Create.
3. Fill the required app name and email fields.
4. You can skip adding scopes on the consent-screen wizard.
5. Add your Google account under Test users. Use the account that owns or manages the YouTube channel.
6. Save the app in Testing mode. That is fine for personal/local use.

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
cd youtube_mcp
pip install -r requirements.txt
```

## 5. Authorize Once

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
