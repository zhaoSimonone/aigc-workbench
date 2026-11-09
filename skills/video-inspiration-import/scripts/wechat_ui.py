#!/usr/bin/env python3
"""Shared desktop helpers for the WeChat automation scripts.

WeChat draws its own UI, so the accessibility tree exposes nothing useful:
the chat list, the message bubbles and the side panel controls have to be
reached through the mouse.  This module keeps that plumbing in one place:

* window rectangle and pixel capture (read from the accessibility tree, with
  the app brought to the front first, so a screenshot cannot sample another
  application),
* pointer movement and clicks, issued through CoreGraphics so the hardware
  pointer really moves - background events that never move the pointer are
  ignored by WeChat.

The pointer calls use ctypes against CoreGraphics, so no extra runtime
dependency is needed; when the framework cannot be loaded the script falls
back to the local clicker binary.
"""

import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

APPLICATION = "System Events"
FRAMEWORK = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
# CoreGraphics event constants.
MOUSE_MOVED = 5
LEFT_MOUSE_DOWN = 1
LEFT_MOUSE_UP = 2
HID_EVENT_TAP = 0


class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def _core_graphics():
    """Return (library, CGEventCreateMouseEvent, CGEventPost) or None."""
    try:
        library = ctypes.cdll.LoadLibrary(FRAMEWORK)
    except OSError:
        return None
    library.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    library.CGEventCreateMouseEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_int, Point, ctypes.c_uint32,
    ]
    library.CGEventPost.restype = None
    library.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    return library


_CG = _core_graphics()


def _clicker(name):
    """Return a clicker binary path, preferring the local helper."""
    candidates = [
        os.environ.get(f"AIGC_WECHAT_{name.upper()}"),
        str(Path.home() / ".minimax" / "tools" / "clicker" / name),
        shutil.which(name),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def mouse_move(x, y):
    """Move the pointer to a screen position without clicking."""
    if _CG:
        event = _CG.CGEventCreateMouseEvent(None, MOUSE_MOVED, Point(float(x), float(y)), 0)
        if event:
            _CG.CGEventPost(HID_EVENT_TAP, event)
            return True
    helper = _clicker("move")
    if helper:
        subprocess.run([helper, str(x), str(y)], capture_output=True)
        return True
    return False


def mouse_click(x, y, settle=0.12):
    """Click a screen position; the pointer moves there first."""
    if _CG:
        move = _CG.CGEventCreateMouseEvent(None, MOUSE_MOVED, Point(float(x), float(y)), 0)
        if move:
            _CG.CGEventPost(HID_EVENT_TAP, move)
            time.sleep(0.05)
            down = _CG.CGEventCreateMouseEvent(None, LEFT_MOUSE_DOWN, Point(float(x), float(y)), 0)
            _CG.CGEventPost(HID_EVENT_TAP, down)
            time.sleep(settle)
            up = _CG.CGEventCreateMouseEvent(None, LEFT_MOUSE_UP, Point(float(x), float(y)), 0)
            _CG.CGEventPost(HID_EVENT_TAP, up)
            return True
    helper = _clicker("click")
    if helper:
        subprocess.run([helper, str(x), str(y)], capture_output=True)
        return True
    return False


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
    """Return (x, y, width, height) of the app's front window in logical points.

    The first window in the accessibility tree is not always the main one:
    WeChat puts transient popups (search suggestions, menus) in the list too,
    and they can come first.  The largest window is the real chat window.
    """
    # Activating through AppleScript is reliable; the JXA property setter is not.
    run(["osascript", "-e", f'tell application "{app}" to activate'])
    time.sleep(1.5)
    script = f'''
    const se = Application('System Events');
    const p = se.processes.byName({json.dumps(app)});
    const windows = p.windows();
    let best = null;
    for (const w of windows) {{
        const pos = w.position(), size = w.size();
        const area = size[0] * size[1];
        if (size[0] > 0 && size[1] > 0 && (best === null || area > best.area)) {{
            best = {{x: pos[0], y: pos[1], w: size[0], h: size[1], area: area}};
        }}
    }}
    if (best === null) {{ throw new Error("no window"); }}
    console.log(JSON.stringify({{x: best.x, y: best.y, w: best.w, h: best.h}}));
    '''
    output = run_jxa(script)
    return json.loads(output.strip().splitlines()[-1])


def capture(region):
    """Screenshot a screen region and return the PNG path."""
    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    handle.close()
    x, y, width, height = region
    run(["screencapture", "-x", "-R", f"{x},{y},{width},{height}", handle.name])
    return Path(handle.name)


def capture_pixels(window):
    """Return the raw rgb24 pixels of a window region."""
    ffmpeg = shutil.which("ffmpeg") or "/Users/simon/.minimax/tools/ffmpeg/bin/ffmpeg"
    image = capture((window["x"], window["y"], window["w"], window["h"]))
    try:
        return run([ffmpeg, "-v", "error", "-i", str(image), "-vf",
                    f"scale={window['w']}:{window['h']}", "-f", "rawvideo",
                    "-pix_fmt", "rgb24", "-"], binary=True)
    finally:
        image.unlink(missing_ok=True)
