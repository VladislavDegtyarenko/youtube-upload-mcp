from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from youtube_mcp import server
from youtube_mcp import youtube_client as yc


def write_config(path: Path, videos_dir: Path, thumbs_dir: Path, **extra) -> Path:
    config = {
        "videos_dir": str(videos_dir),
        "thumbs_dir": str(thumbs_dir),
        **extra,
    }
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


class ConfigAndFileTests(unittest.TestCase):
    def test_load_config_applies_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos = root / "videos"
            thumbs = root / "thumbs"
            videos.mkdir()
            thumbs.mkdir()
            config_path = write_config(root / "config.json", videos, thumbs)

            config, err = yc.load_config(config_path)

            self.assertIsNone(err)
            self.assertEqual(config["default_category_id"], "22")
            self.assertEqual(config["default_language"], "en")
            self.assertEqual(config["default_privacy"], "private")
            self.assertFalse(config["made_for_kids"])

    def test_list_pending_files_pairs_by_stem_and_prefers_jpg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos = root / "videos"
            thumbs = root / "thumbs"
            videos.mkdir()
            thumbs.mkdir()
            (videos / "clip.mp4").write_text("video", encoding="utf-8")
            (videos / "orphan.mov").write_text("video", encoding="utf-8")
            (videos / "ignore.txt").write_text("nope", encoding="utf-8")
            (thumbs / "clip.png").write_text("thumb", encoding="utf-8")
            (thumbs / "clip.jpg").write_text("thumb", encoding="utf-8")
            (thumbs / "other.jpg").write_text("thumb", encoding="utf-8")
            config_path = write_config(root / "config.json", videos, thumbs)

            result = server._list_pending_files(config_path)

            self.assertEqual(result["videos"], ["clip.mp4", "orphan.mov"])
            self.assertEqual(result["thumbs"], ["clip.jpg", "clip.png", "other.jpg"])
            self.assertEqual(
                result["pairs"],
                [
                    {"video": "clip.mp4", "thumb": "clip.jpg"},
                    {"video": "orphan.mov", "thumb": None},
                ],
            )

    def test_list_pending_files_reports_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos = root / "missing-videos"
            thumbs = root / "thumbs"
            thumbs.mkdir()
            config_path = write_config(root / "config.json", videos, thumbs)

            result = server._list_pending_files(config_path)

            self.assertEqual(result["error"], "directory_not_found")
            self.assertEqual(result["path"], str(videos))


if __name__ == "__main__":
    unittest.main()
