import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "weixin_bubble", Path(__file__).resolve().parents[1] / "scripts/weixin_bubble.py"
)
bubble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bubble)

WIDTH = 600
HEIGHT = 400
GREEN = (140, 210, 160)
RECEIVED = (238, 238, 240)
WHITE = (255, 255, 255)
BACKGROUND = (250, 250, 250)


def frame():
    """Return an empty rgb24 buffer plus a helper to paint rectangles."""
    raw = bytearray(BACKGROUND * (WIDTH * HEIGHT))

    def paint(x0, y0, x1, y1, colour):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                offset = (y * WIDTH + x) * 3
                raw[offset:offset + 3] = bytes(colour)
        return raw

    return raw, paint


class ClassifyTest(unittest.TestCase):
    def test_own_message_green_is_green(self):
        self.assertEqual(bubble.classify(*GREEN), "green")

    def test_other_message_cool_grey_is_received(self):
        self.assertEqual(bubble.classify(*RECEIVED), "received")

    def test_classic_white_bubble_is_white(self):
        self.assertEqual(bubble.classify(*WHITE), "white")

    def test_chat_background_matches_neither(self):
        self.assertIsNone(bubble.classify(*BACKGROUND))

    def test_dark_pixel_matches_neither(self):
        self.assertIsNone(bubble.classify(40, 40, 40))

    def test_green_shade_without_excess_is_not_green(self):
        # A pale tint that is bright but not green enough must not be read as
        # an own-message bubble.
        self.assertIsNone(bubble.classify(250, 252, 250))

    def test_neutral_grey_is_not_a_received_bubble(self):
        # The received bubble carries a blue tint; a plain grey is picture
        # content, not chrome.
        self.assertIsNone(bubble.classify(238, 238, 238))


class CollectBandsTest(unittest.TestCase):
    def test_finds_green_and_received_rectangles(self):
        raw, paint = frame()
        paint(300, 100, 500, 130, GREEN)
        paint(20, 200, 180, 220, RECEIVED)
        bands = bubble.collect_bands(bytes(raw), WIDTH, HEIGHT)
        kinds = sorted(band["kind"] for band in bands)
        self.assertEqual(kinds, ["green", "received"])

    def test_sparse_pixels_do_not_form_a_wide_band(self):
        # Anti-aliasing noise used to stretch into a band as wide as the pane.
        raw, paint = frame()
        for y in range(300, 306):
            for x in (5, 100, 200, 300, 400, 500):
                offset = (y * WIDTH + x) * 3
                raw[offset:offset + 3] = bytes(RECEIVED)
        bands = bubble.collect_bands(bytes(raw), WIDTH, HEIGHT)
        self.assertFalse([band for band in bands if bubble.is_message_bubble(band)])

    def test_pane_bounds_are_respected(self):
        raw, paint = frame()
        paint(20, 200, 180, 220, RECEIVED)
        bands = bubble.collect_bands(bytes(raw), WIDTH, HEIGHT, pane_left=300, pane_right=600)
        self.assertEqual(bands, [])

    def test_composer_area_is_skipped(self):
        raw, paint = frame()
        paint(20, 340, 500, 390, WHITE)
        bands = bubble.collect_bands(bytes(raw), WIDTH, HEIGHT, pane_bottom=300)
        self.assertEqual(bands, [])


class BubbleFilterTest(unittest.TestCase):
    def band(self, **kwargs):
        base = {
            "kind": "received", "left": 0, "right": 199, "top": 0, "bottom": 19,
            "width": 200, "height": 20, "pixels": 4000, "pane_span": 600,
        }
        base.update(kwargs)
        return base

    def test_solid_bubble_is_accepted(self):
        self.assertTrue(bubble.is_message_bubble(self.band()))

    def test_narrow_band_is_rejected(self):
        self.assertFalse(bubble.is_message_bubble(self.band(width=40, right=39)))

    def test_short_bubble_is_accepted(self):
        # "好的" is only a few characters wide but still a real bubble.
        self.assertTrue(bubble.is_message_bubble(self.band(width=60, right=59, pixels=1200)))

    def test_flat_band_is_rejected(self):
        self.assertFalse(bubble.is_message_bubble(self.band(height=4, bottom=3, pixels=800)))

    def test_composer_width_is_rejected(self):
        self.assertFalse(bubble.is_message_bubble(self.band(width=560, right=559)))

    def test_sparse_band_is_rejected(self):
        self.assertFalse(bubble.is_message_bubble(self.band(pixels=600)))


class PickBandTest(unittest.TestCase):
    def band(self, kind, bottom, left=0, right=199, top=None):
        top = bottom - 19 if top is None else top
        return {
            "kind": kind, "left": left, "right": right, "top": top, "bottom": bottom,
            "width": right - left + 1, "height": bottom - top + 1,
            "pixels": (right - left + 1) * (bottom - top + 1), "pane_span": 600,
        }

    def test_auto_prefers_green_even_when_received_is_lower(self):
        bands = [self.band("green", 100), self.band("received", 300)]
        self.assertEqual(bubble.pick_band(bands, "auto")["kind"], "green")

    def test_auto_takes_the_lowest_green(self):
        bands = [self.band("green", 100), self.band("green", 260)]
        self.assertEqual(bubble.pick_band(bands, "auto")["bottom"], 260)

    def test_auto_falls_back_to_received(self):
        bands = [self.band("received", 100), self.band("received", 260)]
        self.assertEqual(bubble.pick_band(bands, "auto")["bottom"], 260)

    def test_auto_prefers_received_over_white(self):
        bands = [self.band("white", 300), self.band("received", 100)]
        self.assertEqual(bubble.pick_band(bands, "auto")["kind"], "received")

    def test_explicit_white_selects_other_peoples_bubble(self):
        bands = [self.band("green", 300), self.band("received", 100)]
        self.assertEqual(bubble.pick_band(bands, "white")["kind"], "received")

    def test_explicit_white_also_matches_classic_white(self):
        bands = [self.band("green", 300), self.band("white", 100)]
        self.assertEqual(bubble.pick_band(bands, "white")["kind"], "white")

    def test_no_candidate_returns_none(self):
        self.assertIsNone(bubble.pick_band([self.band("green", 100, right=39)], "auto"))

    def test_noise_only_returns_none(self):
        noise = self.band("received", 300, left=5, right=500)
        noise["pixels"] = 36
        self.assertIsNone(bubble.pick_band([noise], "auto"))


class ClickPointTest(unittest.TestCase):
    def test_own_bubble_click_stays_left_of_the_avatar(self):
        band = {"kind": "green", "left": 100, "right": 299, "top": 40, "bottom": 59}
        x, y = bubble.click_point(band, 10, 20)
        self.assertEqual(x, 10 + 100 + (199 // 4))
        self.assertEqual(y, 20 + 49)

    def test_other_bubble_click_stays_right_of_the_avatar(self):
        band = {"kind": "received", "left": 100, "right": 299, "top": 40, "bottom": 59}
        x, _ = bubble.click_point(band, 10, 20)
        self.assertEqual(x, 10 + 100 + (199 * 3 // 4))

    def test_classic_white_bubble_clicks_on_the_right(self):
        band = {"kind": "white", "left": 100, "right": 299, "top": 40, "bottom": 59}
        x, _ = bubble.click_point(band, 10, 20)
        self.assertEqual(x, 10 + 100 + (199 * 3 // 4))


if __name__ == "__main__":
    unittest.main()
