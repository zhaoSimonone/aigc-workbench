#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from upload_assets import load_manifest  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_per_file_folder_and_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            clothing = Path(tmp) / "coat.jpg"
            clip = Path(tmp) / "clip.mp4"
            clothing.write_bytes(b"x")
            clip.write_bytes(b"y")
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({
                "items": [
                    {
                        "path": str(clothing),
                        "folder": "服装库",
                        "name": "灰色高领露背针织裙",
                        "tags": ["灰色", "高领", "针织"],
                    },
                    {
                        "path": str(clip),
                        "folder": "通用片段",
                        "name": "蓝色双麻花辫-黑色吊带裙",
                        "tags": "蓝色双麻花辫, 黑色吊带裙, 通用片段",
                    },
                ]
            }), encoding="utf-8")
            plan, errors = load_manifest(str(manifest), [clothing, clip])
            self.assertEqual(errors, {})
            self.assertEqual(plan[os.path.abspath(str(clothing))]["folder"], "服装库")
            self.assertEqual(plan[os.path.abspath(str(clip))]["tags"], ["蓝色双麻花辫", "黑色吊带裙", "通用片段"])

    def test_illegal_folder_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "a.jpg"
            image.write_bytes(b"x")
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({
                "items": [{"path": str(image), "folder": "不存在的库", "name": "x"}]
            }), encoding="utf-8")
            plan, errors = load_manifest(str(manifest), [image])
            key = os.path.abspath(str(image))
            self.assertIn(key, errors)
            self.assertNotIn(key, plan)

    def test_missing_manifest_exits(self):
        with self.assertRaises(SystemExit):
            load_manifest("/tmp/definitely-missing-aigc-manifest.json", [])


if __name__ == "__main__":
    unittest.main()
