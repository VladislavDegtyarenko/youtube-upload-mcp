---
name: youtube-upload
description: >
  Workflow for uploading and editing YouTube videos through the
  youtube-automation MCP server, with vidIQ-based SEO packaging. Use whenever
  the user wants to upload a video, set a thumbnail, schedule a publish, write
  or improve title/description/tags, or edit existing video metadata. Enforces
  proposing and confirming SEO-optimized metadata with the user BEFORE calling
  upload_video, and reminds the user of the Studio-only steps the MCP cannot do.
---

# YouTube Upload Workflow (SEO-aware)

The `youtube-automation` MCP provides the capabilities (upload, thumbnail,
edit, list). This skill provides the procedure, the SEO judgment, and a map of
what must still be done by hand in YouTube Studio.

SEO source distilled in `docs/vidiq_youtube_studio_seo_checklist.md` (vidIQ).
Core idea: metadata helps YouTube *understand* the video; **title + thumbnail
earn the click; retention decides reach.** Tags alone never save a weak package.

## Core rule: confirm before uploading

Never call `upload_video` with invented or empty metadata. Always:

1. Ask the user what the video is about, then do the keyword step (below).
2. Assemble a full SEO draft and show it as a single preview.
3. Wait for the user's explicit approval. On change requests, show an updated
   preview and wait again. Only upload after a clear "yes".

Default metadata language is **English**, but always honor the user's stated
preference in the conversation (Russian, bilingual, etc.) over the default.

## Step 1 — Ask the user what the video is about

Before any research or metadata, ask the user to describe the video in their own
words: topic, target audience, what happens / what is shown, the key points or
takeaways, and the desired tone. If the video is already clearly described in the
conversation, confirm your understanding instead of re-asking. This description
is the foundation for keyword research and every metadata field — never skip it
and never invent the subject.

## Step 2 — Pick ONE main keyword

Based on the user's description, do not settle for a name that just "sounds
nice." Find a phrase people actually search, ideally an *opportunity keyword*
(enough search volume + beatable competition for this channel). Sources to reason
from: YouTube autocomplete, related searches, competitors (`search_competitors`
tool), long-tail variants. State the chosen keyword to the user; everything else
(title, first line, filename, tags) is built around it.

## A. Baked into the metadata (do these via the MCP)

### Video file & folder
Ask the user for the video. They can give either a **full path**
(`E:/clips/my-video.mp4`) or a bare filename if a watched folder is configured.
There is no required folder in `config.json`: if `list_pending_files` returns
`queue_dir_not_configured`, just ask the user to paste the full path to the
video (and thumbnail) — never tell them to edit JSON. The watched folder
(`videos_dir`/`thumbs_dir`) is an optional convenience for users who drop files
into a queue; absolute paths always work without it.

### Filename
Before upload, prefer an SEO filename over `final_v3_export.mp4`, e.g.
`youtube-thumbnail-downloader-javascript-tutorial.mp4`. The MCP uploads from
`video_path`; if the file is poorly named, suggest renaming it first.

### Title  → `title`
Formula: **keyword + clear benefit + reason to click.** Keep ~60–70 characters,
put the main keyword near the front, avoid pure clickbait, design it in pair
with the thumbnail. Max 100 chars (hard MCP limit).
- Weak: `My New JavaScript Project`
- OK: `JavaScript YouTube Thumbnail Downloader - Beginner Project`
- Strong: `Build a YouTube Thumbnail Downloader with JavaScript`

### Description  → `description`
**The first 100–150 characters are the most important** — they act as the search
snippet. Open with a hook + the main keyword. Never start with "Hey guys" or a
raw link.
- Practical length for tutorials: **700–1500 chars** (not the full 5000).
- Structure after the hook: what the viewer will learn → links (GitHub / demo /
  watch-next) → subscribe CTA → chapters → 2–3 hashtags.
- **Chapters** for videos longer than ~8–10 min: start at `0:00`, give specific
  section names (not generic). They aid navigation and can surface as key
  moments in search.
- **Hashtags: 2–3 only**, mix one broad + one niche (e.g.
  `#JavaScript #WebDevelopment #CodingTutorial`). Do not dump 10–15.
- Footer/social links: the server does NOT add a footer anymore. If the user
  wants one, ask them for their links (Discord, Instagram, portfolio, etc.) —
  and reuse links they already gave earlier in the conversation/project — then
  include them at the end of the description yourself, separated by a blank line.
  If they don't want a footer, leave it out.

### Tags  → `tags`
A *minor* ranking signal — weaker than title/description/thumbnail/retention —
but useful to disambiguate the topic. Propose 5–15: include the main keyword,
variants, relevant tech/brands, and common misspellings. Never leave empty.
Example: `javascript tutorial, html css javascript project, youtube thumbnail
downloader, beginner javascript project, vanilla javascript`.

### Category  → `category_id`
Default **27 (Education)**. Pass it explicitly and name it in words. See table.
Not stored in config — ask the user (or infer from their description) per upload.

### Language  → `language`
BCP-47 metadata language tag (`en`, `ru`, …). Ask the user or infer it from the
described audience; falls back to `en` when omitted. This is the metadata tag —
the actual title/description text language follows the user's stated preference.

### Privacy & schedule — ASK the user  → `privacy` / `scheduled_time`
**Always ask how the user wants to publish** before uploading — do not silently
assume. Offer these options in plain language and map the answer to the MCP
parameters:

- **Приватно (только я)** → `privacy="private"`
- **По ссылке (доступ по ссылке)** → `privacy="unlisted"`
- **В открытом доступе (для всех)** → `privacy="public"`
- **Спланировать на дату/время** → `scheduled_time` (ISO 8601, e.g.
  `2026-06-01T18:00:00+03:00`). The MCP forces `private` until the publish time,
  so the video goes live automatically at that moment.
- **Премьера** → MCP **cannot** do this. The YouTube Data API does not support
  creating a premiere, so this is a **manual Studio-only step** (see section B).
  If the user asks for a premiere, upload as `private`/`unlisted` first, then
  tell them to turn it into a premiere by hand in Studio.

Recommend uploading as **private or unlisted first** (MCP default is `private`)
so the user can verify title, description, thumbnail, chapters, processing
quality, and compliance before going public. When scheduling, prefer the
audience's active hours (checked in YouTube Analytics) over publishing
immediately.

### Playlist — ASK the user early  → `playlist_id`
Adding a video to a playlist helps discoverability and binge-watching. The MCP
**can** do this now, so make it part of the upfront questions (alongside
privacy/schedule), not an afterthought. Offer two clear options:

- **В плейлист** → call `list_playlists` to show the channel's real playlists,
  let the user pick one, and pass its `playlist_id` to `upload_video`.
- **Без плейлиста** → omit `playlist_id` (leave it `None`). This is fine and
  should be an explicit, equal choice — never force a playlist.

If the user wants a brand-new playlist that doesn't exist yet, the Data API
exposed here only *adds to existing* playlists; tell them to create the playlist
once in Studio, then it will appear in `list_playlists` for future uploads.
A failed playlist add never fails the upload — it comes back as a warning in the
result, so re-run `add_to_playlist(video_id, playlist_id)` if needed.

### Thumbnail  → `thumbnail_path` / `set_thumbnail`
The thumbnail is SEO because it drives CTR. It should *reinforce* the title, not
repeat it: one focus, high contrast, readable on a phone, minimal clutter, 2–3
strong visual elements. Specs: **1280×720, 16:9, under 2 MB, JPG/PNG/WebP.**

## B. MCP cannot do these — remind the user to do them in YouTube Studio

After (or alongside) the upload, explicitly remind the user that the MCP has no
tool for the following, so they must be done by hand in Studio:

- **Premiere (премьера)** — the Data API cannot create one. If the user wants a
  premiere (public countdown page + live chat), upload `private`/`unlisted`
  first, then convert it to a premiere by hand in Studio (or set it up at the
  scheduled time). Scheduling via `scheduled_time` is a normal timed publish, NOT
  a premiere.
- **Captions / subtitles** — even auto-captions; verify names, brands, tech
  terms (e.g. `localStorage`, `createElement`, `Vite`, `Next.js`) that auto-CC
  mangles.
- **End screen & cards** — point to 1–2 logical next videos or a playlist, not 5.
- **Pinned comment** — e.g. source code + live demo link.
- **Analytics** — audience-active hours, post-publish metrics (see below).

## C. Title Flip & post-publish iteration (via `edit_video`)

SEO is not finished at upload:

- **Title Flip Framework**: for the first push you may use a slightly more
  curiosity-driven title (Browse/Suggested). After ~48–72 h, if the video
  underperforms, flip it toward a more search-optimized title. Use `edit_video`
  to change `title`.
- **Iteration checkpoints**: at 48–72 h, 7 days, 28 days, the user reviews
  impressions, CTR, watch time, retention, traffic source, search terms (in
  Studio — MCP can't read analytics).
  - Impressions present but **low CTR** → change title or thumbnail
    (`edit_video` / `set_thumbnail`).
  - **Low impressions** → strengthen keyword, description, chapters, packaging
    (`edit_video`).
  - Check the Reach tab for search terms that earned impressions and fold them
    into title/description/chapters.

## Preview format (show, then wait for approval)

```
Topic:       <one-line summary of what the user described>
Keyword:     <main keyword>
Filename:    <seo-name.mp4>   (rename suggested if needed)
Title:       <~60–70 chars, keyword near front>
Description: <first line = hook + keyword; then learn / links / chapters / 2–3 #tags;
             footer/social links included only if the user provided them>
Tags:        <5–15: keyword, variants, tech, misspellings>
Category:    27 (Education)
Language:    en | ru | ...
Privacy:     private (только я) | unlisted (по ссылке) | public (для всех)
Schedule:    none | 2026-06-01T18:00:00+03:00   (timed publish, not a premiere)
Playlist:    без плейлиста | <playlist title> (<playlist_id>)
Thumbnail:   none | clip.jpg (1280×720, <2 MB)

Manual in Studio after upload: premiere (if requested), captions check,
end screen, cards, pinned comment.
```

## Category reference (category_id)

| ID | Category               |
|----|------------------------|
| 27 | Education (default)    |
| 28 | Science & Technology   |
| 22 | People & Blogs         |
| 1  | Film & Animation       |
| 24 | Entertainment          |
| 10 | Music                  |
| 20 | Gaming                 |
| 26 | Howto & Style          |

If unsure, propose the closest and let the user correct it.

## Standard sequence

1. `list_pending_files` — discover queued media (relative names resolve against
   `videos_dir` / `thumbs_dir`). If it returns `queue_dir_not_configured`, skip
   it and ask the user for the full path to the video instead.
2. Ask what the video is about → keyword step → build the SEO metadata draft
   (including footer links only if the user supplied them) → confirm with the user.
   As part of this, **ask how to publish**: private / unlisted / public /
   schedule on a date. If they ask for a premiere, flag it as a manual Studio
   step (section B) and upload private/unlisted instead. Also **ask about the
   playlist up front**: "в плейлист" (call `list_playlists`, let them pick) or
   "без плейлиста".
3. `upload_video(video_path, title, description, tags, thumbnail_path,
   scheduled_time, privacy, category_id, language, playlist_id)`. This returns **immediately**
   with `{job_id, status: "uploading"}` — the upload runs in the background so large
   files don't hit the MCP request timeout. It is NOT done yet.
4. Poll `get_upload_status(job_id)` every few seconds until `status` is
   `"completed"` or `"error"`. While `"uploading"`, optionally report
   `progress_percent` to the user. On `"completed"`, the real upload result
   (`video_id`, `url`, `thumbnail_set`, `added_to_playlist`, `playlist_id`,
   `warnings`) is under `result`. On `"error"`, the error payload is under
   `result` — surface it to the user.
5. If `thumbnail_set: false` in the result, retry `set_thumbnail`. If a playlist
   was requested but `added_to_playlist: false`, retry `add_to_playlist`.
6. `get_video_details(video_id)` to confirm metadata landed.
7. Remind the user of the Studio-only steps (section B).
8. Schedule the iteration review (section C) at 48–72 h / 7 d / 28 d.

## Editing existing videos

`list_channel_videos` to find targets, then `edit_video` (single) or
`bulk_edit_videos` (explicit IDs; defaults to `dry_run: true`). Omit fields to
leave unchanged; `description: ""` / `tags: []` to clear. Confirm proposed
changes before executing a non-dry-run bulk edit. To put an already-uploaded
video into a playlist, use `add_to_playlist(video_id, playlist_id)` (get the
`playlist_id` from `list_playlists`).

## Hard limits & cautions

- Title ≤ 100 chars; description + footer ≤ 5000 chars.
- Thumbnail ≤ 2 MB, 1280×720, 16:9.
- `config.json` holds personal data — never echo its contents wholesale or
  commit it.