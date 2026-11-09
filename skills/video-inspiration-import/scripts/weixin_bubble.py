#!/usr/bin/env python3
"""Locate the newest WeChat message bubble by its fill colour.

WeChat paints its own messages green and messages from other people in a pale
cool grey, so the fill colour is a stable visual invariant that survives window
resizes, scrolling and new messages.  Finding it beats hard-coding pixel
coordinates.

Measured on WeChat 4.x for macOS (light theme), in logical points:

    own message bubble      #95EC69-ish, matched by hue
    other people's bubble   #EEEEF0 (238, 238, 240)
    chat pane background    #FAFAFA (250, 250, 250)
    composer input box      #FFFFFF (255, 255, 255)

The chat background sits *between* the pane and pure white, so "white" alone is
not enough to tell a bubble from the page.  The other people's bubble is a
narrow band of cool light grey, which is why it is detected by an explicit
range plus a blue tint instead of a plain brightness cut.  Pure white is still
accepted so the script keeps working on themes that paint incoming bubbles
#FFFFFF, but it ranks below the cool grey and below green.

The window rectangle is read from the accessibility tree at run time and WeChat
is brought to the front before the capture, so the screenshot cannot sample
whichever app happens to sit on top.

Usage:
    weixin_bubble.py [--app WeChat] [--kind auto|green|white]

Prints "x y kind" in screen coordinates (logical points) on success.  The kind
tells the caller which side of the bubble is safe to click: own (green) bubbles
carry the avatar on the right, other people's bubbles carry it on the left.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# WeChat's own-message fill is green, but the exact shade differs between the
# plain bubble and the link bubble, so match on hue instead of RGB.
MIN_GREEN = 140
MIN_GREEN_EXCESS = 25
# Classic incoming bubbles are pure white.
WHITE_LEVEL = 252
# WeChat 4.x paints incoming bubbles #EEEEF0: a narrow cool grey.  Anything
# brighter (the #FAFAFA pane) or lighter (the #FFFFFF composer) is not a bubble.
RECEIVED_MIN = 232
RECEIVED_MAX = 249
RECEIVED_TINT = 1

# A message bubble is a filled rectangle: wide enough to read, tall enough to
# hold text, but never as wide as the composer box at the bottom of the pane.
# A long link list can cover most of the pane, so the width cap only has to
# reject the full-width chrome; the composer itself is excluded geometrically.
# A real bubble is solidly painted; stray anti-aliased pixels can stretch a
# sparse band across the pane, so density decides what counts.
BAND_GAP = 3
MIN_BAND_WIDTH = 60
MIN_BAND_HEIGHT = 8
MAX_BAND_RATIO = 0.8
MIN_BAND_DENSITY = 0.45
# The composer occupies the bottom of the chat pane; scanning it only finds
# the white input box.
COMPOSER_HEIGHT = 150


def run(command, binary=False):
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-600:])
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def run_jxa(script):
    """Run JXA and return its console output, which osascript writes to stderr."""
    result = subprocess.run(["osascript", "-l", "JavaScript", "-e", script], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-600:])
    return result.stderr.decode("utf-8", "replace")


def frontmost_window(app):
    """Return (x, y, width, height) of the app's front window in logical points."""
    # Activating through AppleScript is reliable; the JXA property setter is not.
    run(["osascript", "-e", f'tell application "{app}" to activate'])
    time.sleep(1.5)
    script = f'''
    const se = Application('System Events');
    const p = se.processes.byName({json.dumps(app)});
    const w = p.windows[0];
    const pos = w.position(), size = w.size();
    console.log(JSON.stringify({{x: pos[0], y: pos[1], w: size[0], h: size[1]}}));
    '''
    output = run_jxa(script)
    return json.loads(output.strip().splitlines()[-1])


def capture(region):
    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    handle.close()
    x, y, width, height = region
    run(["screencapture", "-x", "-R", f"{x},{y},{width},{height}", handle.name])
    return Path(handle.name)


def classify(red, green, blue):
    """Return "green", "received", "white" or None for one pixel."""
    if green >= MIN_GREEN and green - max(red, blue) >= MIN_GREEN_EXCESS:
        return "green"
    if red >= WHITE_LEVEL and green >= WHITE_LEVEL and blue >= WHITE_LEVEL:
        return "white"
    if (RECEIVED_MIN <= red <= RECEIVED_MAX
            and RECEIVED_MIN <= green <= RECEIVED_MAX
            and RECEIVED_MIN <= blue <= RECEIVED_MAX
            and blue >= red + RECEIVED_TINT):
        return "received"
    return None


def collect_bands(raw, width, height, pane_left=0, pane_right=None, pane_bottom=None):
    """Find filled rectangles in the chat pane and group them into bands."""
    if pane_right is None:
        pane_right = width
    if pane_bottom is None:
        pane_bottom = height
    stride = width * 3
    rows = {}
    for y in range(min(pane_bottom, height)):
        line = raw[y * stride:(y + 1) * stride]
        found = {}
        for x in range(pane_left, min(pane_right, width)):
            offset = x * 3
            kind = classify(line[offset], line[offset + 1], line[offset + 2])
            if kind:
                found.setdefault(kind, []).append(x)
        for kind, xs in found.items():
            rows.setdefault(kind, {})[y] = xs

    bands = []
    pane_span = max(1, pane_right - pane_left)
    for kind, by_row in rows.items():
        current = []
        for y in sorted(by_row):
            if current and y - current[-1] > BAND_GAP:
                bands.append(_make_band(kind, current, by_row, pane_span))
                current = []
            current.append(y)
        if current:
            bands.append(_make_band(kind, current, by_row, pane_span))
    return [band for band in bands if band]


def _make_band(kind, rows, by_row, pane_span):
    xs = [x for row in rows for x in by_row[row]]
    width = max(xs) - min(xs) + 1 if xs else 0
    height = rows[-1] - rows[0] + 1
    return {
        "kind": kind,
        "left": min(xs) if xs else 0,
        "right": max(xs) if xs else 0,
        "top": rows[0],
        "bottom": rows[-1],
        "width": width,
        "height": height,
        "pixels": len(xs),
        "pane_span": pane_span,
    }


def is_message_bubble(band):
    """Reject bands that are too small, too wide (the composer), too flat or too sparse."""
    if band["width"] < MIN_BAND_WIDTH or band["height"] < MIN_BAND_HEIGHT:
        return False
    if band["width"] > band["pane_span"] * MAX_BAND_RATIO:
        return False
    density = band["pixels"] / float(band["width"] * band["height"])
    if density < MIN_BAND_DENSITY:
        return False
    return True


def pick_band(bands, kind="auto"):
    """Return the lowest message bubble, preferring own (green) messages.

    "white" selects any other people's bubble: the cool grey WeChat 4.x paints,
    or the pure white of older themes.
    """
    candidates = [band for band in bands if is_message_bubble(band)]
    if kind == "green":
        candidates = [band for band in candidates if band["kind"] == "green"]
    elif kind == "white":
        candidates = [band for band in candidates if band["kind"] in ("received", "white")]
    elif kind == "auto":
        for preferred in ("green", "received", "white"):
            group = [band for band in candidates if band["kind"] == preferred]
            if group:
                candidates = group
                break
    if not candidates:
        return None
    return max(candidates, key=lambda band: band["bottom"])


def click_point(band, origin_x, origin_y):
    """Pick a point inside the bubble, away from the avatar."""
    if band["kind"] == "green":
        # Own bubbles keep the avatar on the right.
        offset = (band["right"] - band["left"]) // 4
    else:
        # Other people's bubbles keep the avatar on the left.
        offset = (band["right"] - band["left"]) * 3 // 4
    x = origin_x + band["left"] + offset
    y = origin_y + (band["top"] + band["bottom"]) // 2
    return x, y


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="WeChat")
    parser.add_argument("--kind", choices=("auto", "green", "white"), default="auto")
    parser.add_argument("--pane-left", type=int, default=300,
                        help="logical x where the chat pane starts (skips the chat list)")
    parser.add_argument("--pane-right", type=int, default=880,
                        help="logical x where the chat pane ends (skips the video panel)")
    parser.add_argument("--pane-bottom", type=int, default=None,
                        help="logical y where the chat pane ends (default: above the composer)")
    parser.add_argument("--dump", action="store_true",
                        help="print every detected band as JSON and exit")
    args = parser.parse_args()
    window = frontmost_window(args.app)
    region = (window["x"], window["y"], window["w"], window["h"])
    image = capture(region)
    try:
        ffmpeg = shutil.which("ffmpeg") or "/Users/simon/.minimax/tools/ffmpeg/bin/ffmpeg"
        raw = run([ffmpeg, "-v", "error", "-i", str(image), "-vf",
                   f"scale={window['w']}:{window['h']}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                  binary=True)
    finally:
        image.unlink(missing_ok=True)
    pane_bottom = args.pane_bottom
    if pane_bottom is None:
        pane_bottom = max(0, window["h"] - COMPOSER_HEIGHT)
    bands = collect_bands(raw, window["w"], window["h"], args.pane_left, args.pane_right, pane_bottom)
    if args.dump:
        for band in bands:
            band["bubble"] = is_message_bubble(band)
            print(json.dumps(band, sort_keys=True))
        return 0
    band = pick_band(bands, args.kind)
    if band is None:
        print(f"no {args.kind} message bubble found", file=sys.stderr)
        return 1
    x, y = click_point(band, window["x"], window["y"])
    print(f"{x} {y} {band['kind']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
