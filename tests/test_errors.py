from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from youtube_mcp import server


class FakeMediaUpload:
    def __init__(self, path: str, **kwargs):
        self.path = path
        self.kwargs = kwargs


class ExecuteRequest:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response or {}
        self.exc = exc

    def execute(self):
        if self.exc:
            raise self.exc
        return self.response


class FakeHttpError(Exception):
    def __init__(self, status: int, content: bytes):
        super().__init__("http error")
        self.resp = type("Resp", (), {"status": status})()
        self.content = content


class FakeThumbnailYouTube:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc

    def thumbnails(self):
        return self

    def set(self, **kwargs):
        return ExecuteRequest(exc=self.exc)


class ErrorTests(unittest.TestCase):
    def test_set_thumbnail_rejects_missing_file(self) -> None:
        result = server._set_thumbnail("video123", "missing.jpg", youtube=FakeThumbnailYouTube())

        self.assertEqual(result["error"], "file_not_found")

    def test_set_thumbnail_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "large.jpg"
            image.write_bytes(b"x" * (2 * 1024 * 1024 + 1))

            result = server._set_thumbnail(
                "video123",
                str(image),
                youtube=FakeThumbnailYouTube(),
                media_upload_cls=FakeMediaUpload,
            )

            self.assertEqual(result["error"], "validation_error")
            self.assertEqual(result["field"], "image_path")

    def test_set_thumbnail_maps_403_to_channel_not_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "thumb.jpg"
            image.write_bytes(b"jpg")
            exc = FakeHttpError(
                403,
                b'{"error":{"code":403,"message":"forbidden","errors":[{"reason":"forbidden","message":"forbidden"}]}}',
            )

            result = server._set_thumbnail(
                "video123",
                str(image),
                youtube=FakeThumbnailYouTube(exc=exc),
                media_upload_cls=FakeMediaUpload,
            )

            self.assertEqual(result, {"error": "channel_not_verified"})

    def test_upload_rejects_invalid_scheduled_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos = root / "videos"
            thumbs = root / "thumbs"
            videos.mkdir()
            thumbs.mkdir()
            (videos / "clip.mp4").write_text("video", encoding="utf-8")
            config = root / "config.json"
            config.write_text(
                '{"videos_dir":"' + str(videos).replace("\\", "\\\\") + '",'
                '"thumbs_dir":"' + str(thumbs).replace("\\", "\\\\") + '"}',
                encoding="utf-8",
            )

            result = server._upload_video(
                "clip.mp4",
                "Title",
                "Description",
                [],
                scheduled_time="not-a-date",
                youtube=object(),
                config_path=config,
                media_upload_cls=FakeMediaUpload,
            )

            self.assertEqual(result["error"], "validation_error")
            self.assertEqual(result["field"], "scheduled_time")


if __name__ == "__main__":
    unittest.main()
