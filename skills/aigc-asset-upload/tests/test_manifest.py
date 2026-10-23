#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from upload_assets import (  # noqa: E402
    extension_mismatch,
    iter_media_files,
    load_manifest,
    resolve_folder,
    server_base_name,
    sniff_mime_type,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF_BYTES = b"GIF89a" + b"\x00" * 32


class GifRoutingTests(unittest.TestCase):
    """GIF 默认落进 GIF 素材，但显式指定的文件夹永远优先。"""

    def test_gif_without_explicit_folder_goes_to_overlay_library(self):
        self.assertEqual(resolve_folder("", Path("/tmp/cover.gif")), "GIF 素材")
        self.assertEqual(resolve_folder(None, Path("/tmp/COVER.GIF")), "GIF 素材")

    def test_non_gif_without_explicit_folder_goes_to_inspiration(self):
        self.assertEqual(resolve_folder("", Path("/tmp/photo.jpg")), "灵感收集")
        self.assertEqual(resolve_folder("", Path("/tmp/clip.mp4")), "灵感收集")

    def test_explicit_folder_overrides_gif_default(self):
        self.assertEqual(resolve_folder("灵感收集", Path("/tmp/cover.gif")), "灵感收集")
        self.assertEqual(resolve_folder("角色设定", Path("/tmp/cover.gif")), "角色设定")
        self.assertEqual(resolve_folder("服装库", Path("/tmp/photo.jpg")), "服装库")

    def test_overlay_library_is_an_allowed_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            gif = Path(tmp) / "cover.gif"
            gif.write_bytes(b"x")
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({
                "items": [{"path": str(gif), "folder": "GIF 素材", "name": "心形贴纸"}]
            }), encoding="utf-8")
            plan, errors = load_manifest(str(manifest), [gif])
            self.assertEqual(errors, {})
            self.assertEqual(plan[os.path.abspath(str(gif))]["folder"], "GIF 素材")


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


class MimeSniffingTests(unittest.TestCase):
    """MIME 必须按文件头判断，不能只看扩展名。

    微信保存的图经常是 PNG 内容却叫 .jpg，按扩展名猜 MIME 会把 PNG 当
    image/jpeg 上传，服务端按文件名存进 COS 后扩展名就永久错了。
    """

    def test_png_content_named_jpg_is_detected_as_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "WechatIMG001.jpg"
            fake.write_bytes(PNG_BYTES)
            mime, fallback = sniff_mime_type(fake)
            self.assertEqual(mime, "image/png")
            self.assertFalse(fallback)
            self.assertIsNotNone(extension_mismatch(fake, mime))

    def test_real_jpeg_is_detected_as_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "photo.jpg"
            real.write_bytes(JPEG_BYTES)
            mime, fallback = sniff_mime_type(real)
            self.assertEqual(mime, "image/jpeg")
            self.assertFalse(fallback)
            self.assertIsNone(extension_mismatch(real, mime))

    def test_matching_extension_has_no_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            gif = Path(tmp) / "cover.gif"
            gif.write_bytes(GIF_BYTES)
            mime, _ = sniff_mime_type(gif)
            self.assertEqual(mime, "image/gif")
            self.assertIsNone(extension_mismatch(gif, mime))

    def test_missing_file_falls_back_to_extension(self):
        mime, fallback = sniff_mime_type("/tmp/definitely-missing-aigc.gif")
        self.assertEqual(mime, "image/gif")
        self.assertTrue(fallback)


class DirectoryScanTests(unittest.TestCase):
    """扫目录只取本层文件，子目录（已上传/）不能被当成待上传素材。"""

    def test_scan_skips_subdirectories_and_hidden_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.png").write_bytes(PNG_BYTES)
            (root / ".DS_Store").write_bytes(b"x")
            (root / "notes.txt").write_text("x")
            archive = root / "已上传"
            archive.mkdir()
            (archive / "b.png").write_bytes(PNG_BYTES)
            found = [item.name for item in iter_media_files(root)]
            self.assertEqual(found, ["a.png"])


class ArchiveNamingTests(unittest.TestCase):
    """归档后的本地文件名 = 服务端素材名 + 原扩展名。"""

    def test_server_name_keeps_local_extension(self):
        asset = {"name": "米白碎花蕾丝吊带裙"}
        self.assertEqual(server_base_name(asset, ".png") + ".png", "米白碎花蕾丝吊带裙.png")

    def test_server_name_with_extension_is_not_doubled(self):
        asset = {"name": "米白碎花蕾丝吊带裙.png"}
        self.assertEqual(server_base_name(asset, ".png"), "米白碎花蕾丝吊带裙")

    def test_illegal_filename_characters_are_replaced(self):
        asset = {"name": "奇怪/名字:带*符号"}
        self.assertNotIn("/", server_base_name(asset, ".png"))
        self.assertNotIn(":", server_base_name(asset, ".png"))


if __name__ == "__main__":
    unittest.main()
