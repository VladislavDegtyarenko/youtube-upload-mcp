from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
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


class UploadRequest:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        if self.calls == 1:
            return type("Progress", (), {"progress": lambda self: 0.5})(), None
        return None, self.response


class FakeMediaUpload:
    instances: list["FakeMediaUpload"] = []

    def __init__(self, path: str, **kwargs):
        self.path = path
        self.kwargs = kwargs
        self.__class__.instances.append(self)


class FakeYouTube:
    def __init__(self):
        self.search_response: dict[str, Any] = {}
        self.video_response: dict[str, Any] = {}
        self.channel_response: dict[str, Any] = {}
        self.thumbnail_response: dict[str, Any] = {}
        self.upload_response: dict[str, Any] = {"id": "uploaded123"}
        self.search_kwargs: dict[str, Any] | None = None
        self.video_list_kwargs: dict[str, Any] | None = None
        self.insert_kwargs: dict[str, Any] | None = None
        self.thumbnail_kwargs: dict[str, Any] | None = None

    def search(self):
        return self

    def videos(self):
        return self

    def channels(self):
        return self

    def thumbnails(self):
        return self

    def list(self, **kwargs):
        if kwargs.get("q") is not None:
            self.search_kwargs = kwargs
            return ExecuteRequest(self.search_response)
        if kwargs.get("mine") is True:
            return ExecuteRequest(self.channel_response)
        self.video_list_kwargs = kwargs
        return ExecuteRequest(self.video_response)

    def insert(self, **kwargs):
        self.insert_kwargs = kwargs
        return UploadRequest(self.upload_response)

    def set(self, **kwargs):
        self.thumbnail_kwargs = kwargs
        return ExecuteRequest(self.thumbnail_response)


def write_config(root: Path, videos: Path, thumbs: Path, **extra) -> Path:
    config = {
        "videos_dir": str(videos),
        "thumbs_dir": str(thumbs),
        "default_privacy": "private",
        "made_for_kids": False,
        **extra,
    }
    path = root / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


class ToolTests(unittest.TestCase):
    def test_search_competitors_uses_two_step_flow_and_defaults_stats(self) -> None:
        youtube = FakeYouTube()
        youtube.search_response = {
            "items": [
                {"id": {"videoId": "v1"}},
                {"id": {"videoId": "v2"}},
            ]
        }
        youtube.video_response = {
            "items": [
                {
                    "id": "v1",
                    "snippet": {
                        "title": "Title 1",
                        "description": "A" * 400,
                        "channelTitle": "Channel",
                        "tags": ["one"],
                    },
                    "statistics": {"viewCount": "10"},
                },
                {
                    "id": "v2",
                    "snippet": {"title": "Title 2", "description": "", "channelTitle": "Channel 2"},
                    "statistics": {"viewCount": "20", "likeCount": "2", "commentCount": "1"},
                },
            ]
        }

        result = server._search_competitors("query", max_results=100, youtube=youtube)

        self.assertEqual(youtube.search_kwargs["maxResults"], 50)
        self.assertEqual(youtube.video_list_kwargs["id"], "v1,v2")
        self.assertEqual(result[0]["description"], "A" * 300)
        self.assertEqual(result[0]["like_count"], 0)
        self.assertEqual(result[1]["comment_count"], 1)

    def test_get_video_details_returns_video_not_found_for_empty_response(self) -> None:
        youtube = FakeYouTube()
        youtube.video_response = {"items": []}

        result = server._get_video_details("missing", youtube=youtube)

        self.assertEqual(result, {"error": "video_not_found", "video_id": "missing"})

    def test_get_channel_info_maps_hidden_subscriber_count_to_zero(self) -> None:
        youtube = FakeYouTube()
        youtube.channel_response = {
            "items": [
                {
                    "id": "channel123",
                    "snippet": {"title": "My Channel"},
                    "statistics": {"videoCount": "7"},
                }
            ]
        }

        result = server._get_channel_info(youtube=youtube)

        self.assertEqual(
            result,
            {
                "channel_id": "channel123",
                "title": "My Channel",
                "subscriber_count": 0,
                "video_count": 7,
            },
        )

    def test_upload_forces_private_for_scheduled_video_and_preserves_upload_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos = root / "videos"
            thumbs = root / "thumbs"
            videos.mkdir()
            thumbs.mkdir()
            (videos / "clip.mp4").write_text("video", encoding="utf-8")
            (thumbs / "clip.jpg").write_text("thumb", encoding="utf-8")
            config_path = write_config(root, videos, thumbs)
            youtube = FakeYouTube()

            def thumbnail_func(*args, **kwargs):
                return {"error": "channel_not_verified"}

            result = server._upload_video(
                "clip.mp4",
                "Title",
                "Description",
                ["tag"],
                thumbnail_path="clip.jpg",
                scheduled_time="2026-06-01T18:00:00+03:00",
                privacy="public",
                youtube=youtube,
                config_path=config_path,
                media_upload_cls=FakeMediaUpload,
                thumbnail_func=thumbnail_func,
            )

            body = youtube.insert_kwargs["body"]
            self.assertEqual(body["status"]["privacyStatus"], "private")
            self.assertEqual(body["status"]["publishAt"], "2026-06-01T18:00:00+03:00")
            self.assertEqual(body["snippet"]["description"], "Description")
            self.assertEqual(result["video_id"], "uploaded123")
            self.assertFalse(result["thumbnail_set"])
            self.assertEqual(result["warnings"][0]["code"], "privacy_forced_private")
            self.assertEqual(result["warnings"][1]["code"], "channel_not_verified")

    def test_upload_omits_publish_at_without_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos = root / "videos"
            thumbs = root / "thumbs"
            videos.mkdir()
            thumbs.mkdir()
            (videos / "clip.mp4").write_text("video", encoding="utf-8")
            config_path = write_config(root, videos, thumbs)
            youtube = FakeYouTube()

            result = server._upload_video(
                str(videos / "clip.mp4"),
                "Title",
                "Description",
                [],
                youtube=youtube,
                config_path=config_path,
                media_upload_cls=FakeMediaUpload,
            )

            self.assertNotIn("publishAt", youtube.insert_kwargs["body"]["status"])
            self.assertFalse(result["thumbnail_set"])

    def test_upload_works_without_configured_queue_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "clip.mp4").write_text("video", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps({"default_privacy": "private", "made_for_kids": False}),
                encoding="utf-8",
            )
            youtube = FakeYouTube()

            result = server._upload_video(
                str(root / "clip.mp4"),
                "Title",
                "Description",
                [],
                youtube=youtube,
                config_path=config_path,
                media_upload_cls=FakeMediaUpload,
            )

            self.assertEqual(result["video_id"], "uploaded123")

    def test_upload_uses_explicit_category_and_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos = root / "videos"
            thumbs = root / "thumbs"
            videos.mkdir()
            thumbs.mkdir()
            (videos / "clip.mp4").write_text("video", encoding="utf-8")
            config_path = write_config(root, videos, thumbs)
            youtube = FakeYouTube()

            server._upload_video(
                str(videos / "clip.mp4"),
                "Title",
                "Description",
                [],
                category_id="28",
                language="ru",
                youtube=youtube,
                config_path=config_path,
                media_upload_cls=FakeMediaUpload,
            )

            snippet = youtube.insert_kwargs["body"]["snippet"]
            self.assertEqual(snippet["categoryId"], "28")
            self.assertEqual(snippet["defaultLanguage"], "ru")

    def test_upload_falls_back_to_default_category_and_language_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos = root / "videos"
            thumbs = root / "thumbs"
            videos.mkdir()
            thumbs.mkdir()
            (videos / "clip.mp4").write_text("video", encoding="utf-8")
            config_path = write_config(root, videos, thumbs)
            youtube = FakeYouTube()

            server._upload_video(
                str(videos / "clip.mp4"),
                "Title",
                "Description",
                [],
                youtube=youtube,
                config_path=config_path,
                media_upload_cls=FakeMediaUpload,
            )

            snippet = youtube.insert_kwargs["body"]["snippet"]
            self.assertEqual(snippet["categoryId"], "27")
            self.assertEqual(snippet["defaultLanguage"], "en")


if __name__ == "__main__":
    unittest.main()
