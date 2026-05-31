from __future__ import annotations

import copy
import unittest
from typing import Any

from youtube_mcp import server


class ExecuteRequest:
    def __init__(self, response: dict[str, Any] | None = None, exc: Exception | None = None):
        self.response = response or {}
        self.exc = exc

    def execute(self) -> dict[str, Any]:
        if self.exc:
            raise self.exc
        return self.response


class FakeHttpError(Exception):
    def __init__(self, status: int, content: bytes):
        super().__init__("http error")
        self.resp = type("Resp", (), {"status": status})()
        self.content = content


def make_video(
    video_id: str,
    title: str = "Old title",
    description: str = "Old description",
    tags: list[str] | None = None,
    category_id: str = "22",
    privacy: str = "public",
) -> dict[str, Any]:
    return {
        "id": video_id,
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags if tags is not None else ["old"],
            "categoryId": category_id,
            "defaultLanguage": "en",
            "publishedAt": "2026-01-01T00:00:00Z",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
            "publicStatsViewable": True,
            "license": "youtube",
        },
        "contentDetails": {"duration": "PT2M"},
        "recordingDetails": {"recordingDate": "2026-01-01T00:00:00Z"},
        "statistics": {"viewCount": "10", "likeCount": "2", "commentCount": "1"},
    }


class FakeYouTube:
    def __init__(self, videos: dict[str, dict[str, Any]] | None = None):
        self.videos_by_id = videos or {}
        self.channel_response = {
            "items": [
                {
                    "contentDetails": {
                        "relatedPlaylists": {"uploads": "uploads123"},
                    }
                }
            ]
        }
        self.playlist_response = {
            "items": [
                {"contentDetails": {"videoId": video_id}}
                for video_id in self.videos_by_id
            ]
        }
        self.update_errors: dict[str, Exception] = {}
        self.resource = ""
        self.channel_kwargs: dict[str, Any] | None = None
        self.playlist_kwargs: dict[str, Any] | None = None
        self.video_list_kwargs: list[dict[str, Any]] = []
        self.update_kwargs: list[dict[str, Any]] = []

    def channels(self):
        self.resource = "channels"
        return self

    def playlistItems(self):
        self.resource = "playlistItems"
        return self

    def videos(self):
        self.resource = "videos"
        return self

    def list(self, **kwargs):
        if self.resource == "channels":
            self.channel_kwargs = kwargs
            return ExecuteRequest(self.channel_response)
        if self.resource == "playlistItems":
            self.playlist_kwargs = kwargs
            return ExecuteRequest(self.playlist_response)

        self.video_list_kwargs.append(kwargs)
        ids = [video_id for video_id in kwargs.get("id", "").split(",") if video_id]
        return ExecuteRequest({"items": [copy.deepcopy(self.videos_by_id[video_id]) for video_id in ids if video_id in self.videos_by_id]})

    def update(self, **kwargs):
        self.update_kwargs.append(kwargs)
        body = kwargs["body"]
        video_id = body["id"]
        if video_id in self.update_errors:
            return ExecuteRequest(exc=self.update_errors[video_id])

        updated = copy.deepcopy(self.videos_by_id[video_id])
        for part in kwargs["part"].split(","):
            if part in body:
                updated.setdefault(part, {}).update(body[part])
        self.videos_by_id[video_id] = updated
        return ExecuteRequest(copy.deepcopy(updated))


class EditingTests(unittest.TestCase):
    def test_package_wrapper_exposes_editing_tools(self) -> None:
        self.assertTrue(callable(server.list_channel_videos))
        self.assertTrue(callable(server.edit_video))
        self.assertTrue(callable(server.bulk_edit_videos))
        if hasattr(server.mcp, "tools"):
            self.assertIn("list_channel_videos", server.mcp.tools)
            self.assertIn("edit_video", server.mcp.tools)
            self.assertIn("bulk_edit_videos", server.mcp.tools)

    def test_edit_video_updates_common_fields_and_preserves_existing_snippet(self) -> None:
        youtube = FakeYouTube({"v1": make_video("v1")})

        result = server._edit_video(
            "v1",
            {
                "title": "New title",
                "description": "",
                "tags": [],
                "category_id": "27",
                "privacy": "unlisted",
            },
            youtube=youtube,
        )

        self.assertTrue(result["updated"])
        self.assertEqual(
            result["changed_fields"],
            ["title", "description", "tags", "category_id", "privacy"],
        )
        self.assertEqual(youtube.update_kwargs[0]["part"], "snippet,status")
        body = youtube.update_kwargs[0]["body"]
        self.assertEqual(body["snippet"]["title"], "New title")
        self.assertEqual(body["snippet"]["description"], "")
        self.assertEqual(body["snippet"]["tags"], [])
        self.assertEqual(body["snippet"]["categoryId"], "27")
        self.assertEqual(body["snippet"]["defaultLanguage"], "en")
        self.assertEqual(body["status"]["privacyStatus"], "unlisted")

    def test_edit_video_dry_run_does_not_call_update(self) -> None:
        youtube = FakeYouTube({"v1": make_video("v1")})

        result = server._edit_video("v1", {"title": "New title"}, dry_run=True, youtube=youtube)

        self.assertFalse(result["updated"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["after"]["title"], "New title")
        self.assertEqual(youtube.update_kwargs, [])

    def test_edit_video_skips_noop_update(self) -> None:
        youtube = FakeYouTube({"v1": make_video("v1")})

        result = server._edit_video("v1", {"title": "Old title"}, youtube=youtube)

        self.assertFalse(result["updated"])
        self.assertEqual(result["changed_fields"], [])
        self.assertEqual(youtube.update_kwargs, [])

    def test_edit_video_rejects_invalid_changes_before_api_update(self) -> None:
        youtube = FakeYouTube({"v1": make_video("v1")})

        result = server._edit_video("v1", {"publish_at": "2026-06-01T10:00:00Z"}, youtube=youtube)

        self.assertEqual(result["error"], "validation_error")
        self.assertEqual(result["field"], "publish_at")
        self.assertEqual(youtube.update_kwargs, [])

    def test_edit_video_returns_video_not_found(self) -> None:
        youtube = FakeYouTube({})

        result = server._edit_video("missing", {"title": "New title"}, youtube=youtube)

        self.assertEqual(result, {"error": "video_not_found", "video_id": "missing"})

    def test_edit_video_maps_update_api_error(self) -> None:
        youtube = FakeYouTube({"v1": make_video("v1")})
        youtube.update_errors["v1"] = FakeHttpError(
            403,
            b'{"error":{"code":403,"message":"quota","errors":[{"reason":"quotaExceeded","message":"quota"}]}}',
        )

        result = server._edit_video("v1", {"title": "New title"}, youtube=youtube)

        self.assertEqual(result, {"error": "quota_exceeded", "resets_at": "midnight PT"})

    def test_bulk_edit_same_change_defaults_to_dry_run_and_dedupes_ids(self) -> None:
        youtube = FakeYouTube({"v1": make_video("v1"), "v2": make_video("v2")})

        result = server._bulk_edit_videos(
            video_ids=["v1", "v1", "v2"],
            changes={"category_id": "27"},
            youtube=youtube,
        )

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual([item["video_id"] for item in result["results"]], ["v1", "v2"])
        self.assertEqual(youtube.update_kwargs, [])

    def test_bulk_edit_per_video_continues_after_missing_video(self) -> None:
        youtube = FakeYouTube({"v1": make_video("v1")})

        result = server._bulk_edit_videos(
            edits=[
                {"video_id": "v1", "changes": {"title": "New title"}},
                {"video_id": "missing", "changes": {"title": "Other title"}},
            ],
            dry_run=False,
            youtube=youtube,
        )

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["updated"], 1)
        self.assertTrue(result["results"][0]["updated"])
        self.assertEqual(result["results"][1]["error"], "video_not_found")
        self.assertEqual(len(youtube.update_kwargs), 1)

    def test_list_channel_videos_uses_uploads_playlist_and_batches_details(self) -> None:
        youtube = FakeYouTube({"v1": make_video("v1"), "v2": make_video("v2", title="Second")})
        youtube.playlist_response["nextPageToken"] = "NEXT"

        result = server._list_channel_videos(max_results=100, page_token="TOKEN", youtube=youtube)

        self.assertEqual(youtube.channel_kwargs, {"part": "contentDetails", "mine": True})
        self.assertEqual(
            youtube.playlist_kwargs,
            {
                "part": "contentDetails",
                "playlistId": "uploads123",
                "maxResults": 50,
                "pageToken": "TOKEN",
            },
        )
        self.assertEqual(youtube.video_list_kwargs[-1]["id"], "v1,v2")
        self.assertEqual(result["next_page_token"], "NEXT")
        self.assertEqual(result["videos"][0]["video_id"], "v1")
        self.assertEqual(result["videos"][0]["duration"], "PT2M")
        self.assertEqual(result["videos"][1]["title"], "Second")


if __name__ == "__main__":
    unittest.main()
