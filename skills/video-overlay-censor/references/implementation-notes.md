# Implementation notes

## What the prior workflow used

1. `ffprobe` to establish duration, video dimensions, frame rate, and audio stream presence.
2. Frame extraction and contact sheets to inspect the transformation point and motion.
3. A user-supplied GIF as an animated overlay rather than a generated censor shape.
4. PIL/OpenCV to decode GIF frames, remove only edge-connected white background pixels, and preserve enclosed white artwork.
5. A pixel-coordinate keyframe path for the overlay center. The position was interpolated and lightly smoothed; the overlay width and height were deliberately kept constant after the user requested that behavior.
6. RGBA PNG overlay frames composited by FFmpeg, with source audio mapped and copied when possible.
7. Output metadata checks and sampled-frame inspection, especially the first covered frame, motion turns, and final frames.

## Why not use a plain blur or glow

A glow or semi-transparent blur changes the appearance but may leave the covered detail legible. A user-provided opaque animated artwork is a stronger cover when the user explicitly asks for it. Keep the overlay geometry separate from the visual artwork so size and motion can be adjusted without changing the source asset.

## Coordinate convention

Coordinates are source-video pixels with origin at the top-left. `x` and `y` in the keyframe file refer to the overlay center. The frame renderer places the top-left corner at `(x - width / 2, y - height / 2)`.
