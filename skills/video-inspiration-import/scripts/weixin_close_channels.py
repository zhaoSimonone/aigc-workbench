#!/usr/bin/env python3
"""Close the WeChat Channels page that the download opened.

Clicking a Channels link inside WeChat opens the video in a side panel tab
that keeps autoplaying - with sound - long after the download finished.  This
script closes that tab so the desktop goes quiet again.

The tab strip at the top of the side panel renders the leading slot of the
Channels tab differently depending on its state:

    playing   a green speaker glyph   (the page is playing audio)
    idle      a dark "X" close glyph   (hovering swaps the speaker for it)

Both glyphs occupy the same slot.  Moving the pointer over the slot swaps the
speaker for the close button, and a click always moves the pointer first, so a
single click on the green speaker closes the tab.  When the page is not
playing there is no speaker and the close glyph is found by its shape.

The window rectangle is read from the accessibility tree at run time and WeChat
is brought to the front before the capture, so the screenshot cannot sample
whichever app happens to sit on top.

Usage:
    weixin_close_channels.py [--app WeChat] [--json]

Prints a JSON result and exits 0 when the tab was closed, 1 when no Channels
tab was found.
"""

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


def _load_ui():
    """Reuse the shared desktop helpers from the sibling module."""
    path = Path(__file__).with_name("wechat_ui.py")
    spec = importlib.util.spec_from_file_location("wechat_ui", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ui = _load_ui()

# The tab strip sits in the top rows of the window; only the right hand side
# holds the side panel, so the chat header and the avatar column are skipped.
STRIP_HEIGHT = 46
STRIP_RIGHT_RATIO = 0.55
# WeChat's "audio is playing" glyph is a saturated green.
GREEN_MIN = 120
GREEN_EXCESS = 40
# The close glyph is drawn in dark grey on the light tab strip.
DARK_LEVEL = 170
LIGHT_LEVEL = 200


def pixel(raw, width, x, y):
    offset = (y * width + x) * 3
    return raw[offset], raw[offset + 1], raw[offset + 2]


def is_green(colour):
    red, green, blue = colour
    return green >= GREEN_MIN and green - red >= GREEN_EXCESS and green - blue >= GREEN_EXCESS


def is_dark(colour):
    return max(colour) < DARK_LEVEL


def is_light(colour):
    return min(colour) >= LIGHT_LEVEL


def strip_region(width):
    """Return the (left, right) x range of the side panel tab strip."""
    return int(width * STRIP_RIGHT_RATIO), width


def find_speaker(raw, width, height):
    """Return the centre of the green speaker glyph, or None."""
    left, right = strip_region(width)
    points = []
    for y in range(min(STRIP_HEIGHT, height)):
        for x in range(left, right):
            if is_green(pixel(raw, width, x, y)):
                points.append((x, y))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2


def find_close_glyph(raw, width, height):
    """Return the centre of the dark "X" close glyph, or None.

    The glyph is two thin diagonal strokes: both diagonals are dark while the
    orthogonal neighbours stay light.  Dense Chinese glyphs and the round
    refresh arrow fail that test.
    """
    left, right = strip_region(width)
    for y in range(4, min(STRIP_HEIGHT, height) - 4):
        for x in range(left + 4, right - 4):
            if not is_dark(pixel(raw, width, x, y)):
                continue
            diagonals = True
            orthogonal = True
            for step in (3, 4, 5):
                for dx, dy in ((step, step), (-step, -step), (step, -step), (-step, step)):
                    if not is_dark(pixel(raw, width, x + dx, y + dy)):
                        diagonals = False
                for dx, dy in ((step, 0), (-step, 0), (0, step), (0, -step)):
                    if not is_light(pixel(raw, width, x + dx, y + dy)):
                        orthogonal = False
            if diagonals and orthogonal:
                return x, y
    return None


def grab(app):
    """Return (window, raw pixels) of the WeChat window."""
    window = ui.frontmost_window(app)
    return window, ui.capture_pixels(window)


def close_channels(app="WeChat", settle=1.2):
    """Close the Channels tab and report what happened."""
    window, raw = grab(app)
    speaker = find_speaker(raw, window["w"], window["h"])
    if speaker:
        # Hovering swaps the speaker for the close button; the slot does not
        # move, and the click moves the pointer there anyway.
        x = window["x"] + speaker[0]
        y = window["y"] + speaker[1]
        ui.mouse_move(x, y)
        time.sleep(settle)
        window, raw = grab(app)
        glyph = find_close_glyph(raw, window["w"], window["h"])
        if glyph is None:
            return {"closed": False, "reason": "播放中的标签在悬停后没有出现关闭按钮",
                    "speaker": list(speaker)}
        x = window["x"] + glyph[0]
        y = window["y"] + glyph[1]
        ui.mouse_click(x, y)
        method = "hover-swap"
    else:
        glyph = find_close_glyph(raw, window["w"], window["h"])
        if glyph is None:
            return {"closed": False, "reason": "没有找到视频号标签"}
        x = window["x"] + glyph[0]
        y = window["y"] + glyph[1]
        ui.mouse_click(x, y)
        method = "close-glyph"
    time.sleep(settle)

    window, raw = grab(app)
    speaker_after = find_speaker(raw, window["w"], window["h"])
    glyph_after = find_close_glyph(raw, window["w"], window["h"])
    # The green speaker only ever belongs to the Channels tab, so its absence
    # proves the page is gone.  Another tab may show its own close glyph while
    # the pointer rests on it, so the glyph only counts as "still there" when
    # it sits in the slot that was clicked.
    if speaker_after is not None:
        return {"closed": False, "reason": "点击后视频号标签仍在播放", "method": method}
    if glyph_after is not None and abs(glyph_after[0] - (x - window["x"])) <= 8:
        return {"closed": False, "reason": "点击后标签仍在", "method": method}
    return {"closed": True, "method": method}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="WeChat")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = close_channels(args.app)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("已关闭视频号页面" if result["closed"] else f"未能关闭：{result.get('reason')}")
    return 0 if result["closed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
