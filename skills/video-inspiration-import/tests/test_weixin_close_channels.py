import importlib.util
from pathlib import Path
import unittest


def _load(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


closer = _load("weixin_close_channels")

WIDTH = 600
HEIGHT = 80
STRIP = (238, 238, 238)
INK = (60, 60, 60)
GREEN = (7, 193, 96)


def frame():
    return bytearray(STRIP * (WIDTH * HEIGHT))


def paint(raw, x, y, colour):
    offset = (y * WIDTH + x) * 3
    raw[offset:offset + 3] = bytes(colour)


def draw_x(raw, cx, cy, colour=INK, radius=5, thickness=1):
    """Draw the two diagonal strokes of the close glyph."""
    for k in range(-radius, radius + 1):
        for offset in range(thickness):
            paint(raw, cx + k, cy + k + offset, colour)
            paint(raw, cx + k, cy - k + offset, colour)


class StripRegionTest(unittest.TestCase):
    def test_region_skips_the_chat_side(self):
        left, right = closer.strip_region(WIDTH)
        self.assertEqual(right, WIDTH)
        self.assertGreater(left, WIDTH // 2)


class SpeakerTest(unittest.TestCase):
    def test_finds_the_green_speaker(self):
        raw = frame()
        for y in range(20, 30):
            for x in range(400, 414):
                paint(raw, x, y, GREEN)
        centre = closer.find_speaker(bytes(raw), WIDTH, HEIGHT)
        self.assertIsNotNone(centre)
        self.assertEqual(centre, (406, 24))

    def test_without_a_speaker_returns_none(self):
        raw = frame()
        draw_x(raw, 420, 24)
        self.assertIsNone(closer.find_speaker(bytes(raw), WIDTH, HEIGHT))


class CloseGlyphTest(unittest.TestCase):
    def test_finds_the_x_glyph(self):
        raw = frame()
        draw_x(raw, 420, 24)
        centre = closer.find_close_glyph(bytes(raw), WIDTH, HEIGHT)
        self.assertIsNotNone(centre)
        self.assertEqual(centre, (420, 24))

    def test_dense_block_is_not_a_close_glyph(self):
        # A solid glyph block has dark orthogonal neighbours, so it must not
        # be mistaken for the X.
        raw = frame()
        for y in range(18, 31):
            for x in range(414, 427):
                paint(raw, x, y, INK)
        self.assertIsNone(closer.find_close_glyph(bytes(raw), WIDTH, HEIGHT))

    def test_hollow_square_is_not_a_close_glyph(self):
        raw = frame()
        for y in range(18, 31):
            for x in (414, 426):
                paint(raw, x, y, INK)
        for x in range(414, 427):
            for y in (18, 30):
                paint(raw, x, y, INK)
        self.assertIsNone(closer.find_close_glyph(bytes(raw), WIDTH, HEIGHT))

    def test_plain_strip_has_no_glyph(self):
        raw = frame()
        self.assertIsNone(closer.find_close_glyph(bytes(raw), WIDTH, HEIGHT))


if __name__ == "__main__":
    unittest.main()
