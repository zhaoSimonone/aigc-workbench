---
name: video-overlay-censor
description: Apply a user-provided animated image or GIF as a tracked censor/cover overlay on a video, with fixed or keyframed position and size, transparency cleanup, audio preservation, and sampled-frame verification. Use when a user asks to cover a selected region such as a logo, face, sensitive area, or object; do not use for automatic sensitive-content detection unless a separate detector is explicitly available.
---

# Video Overlay Censor

Use this skill for deterministic, user-directed video masking with an image or animated GIF. It supports the workflow used for the prior video edit: inspect media, remove an unwanted GIF background, render a fixed-size animated overlay, move it along a manually specified path, composite it over the source, preserve audio, and verify the result.

## Capabilities

- Inspect video duration, resolution, frame rate, codecs, audio tracks, and GIF dimensions/frame timing.
- Use a supplied PNG/GIF as the visible cover instead of generating a new graphic.
- Remove only edge-connected near-white GIF background pixels, preserving enclosed white details inside the artwork.
- Loop GIF frames over a selected time range.
- Keep overlay width and height constant, or preserve the GIF aspect ratio when requested.
- Track the overlay by interpolating user-approved center-position keyframes; do not silently resize it during motion.
- Composite RGBA frames with FFmpeg while copying the source audio when compatible.
- Sample output frames/contact sheets and inspect metadata before reporting completion.

## Workflow

1. **Confirm inputs and scope.** Use only the video and overlay files the user identified. Determine the start/end time and whether the overlay should be fixed, position-tracked, size-tracked, or both. If the user supplies a red-box screenshot, treat it as visual guidance, not as a machine-readable mask.
2. **Inspect before editing.** Run `ffprobe` on the video and inspect the overlay dimensions, frame count, frame duration, loop behavior, and whether it already has alpha. Do not assume 30 fps, a 16:9 canvas, or a transparent GIF.
3. **Choose the geometry explicitly.** For a fixed-size overlay, use one width and one height for the whole active range. For tracking, specify center-position keyframes as `(time, centerX, centerY)` in source-video pixels. Interpolate between keys and smooth only enough to avoid visible jumps. Keep the same geometry through the render unless the user explicitly asks for scaling.
4. **Prepare the overlay.** For GIFs with a plain white background, use the supplied helper to remove only white pixels connected to the frame edge. Do not globally remove white, because that can erase white eyes, clothing, outlines, or highlights in the artwork. If the background is not separable safely, preserve it and report that limitation instead of guessing.
5. **Render and composite.** Use `scripts/apply_overlay.py`. It creates temporary RGBA overlay frames, loops the animated source, positions each frame from the keyframe path, and composites the result with FFmpeg. The helper uses a temporary directory and cleans it after success or failure.
6. **Verify.** Check output metadata with `ffprobe`; sample early, middle, motion, and final frames. Confirm the overlay stays within the requested region, remains the requested size, covers the intended content, and does not unexpectedly cover adjacent content. Report the exact output path and any unverified edge cases.

## Keyframe file

Use JSON with pixel coordinates in the source video:

```json
{
  "start": 3.45,
  "end": 8.29,
  "keyframes": [
    {"time": 3.45, "x": 520, "y": 875},
    {"time": 5.20, "x": 410, "y": 835},
    {"time": 7.60, "x": 285, "y": 860},
    {"time": 8.29, "x": 350, "y": 835}
  ]
}
```

`x` and `y` are the overlay center. Keyframes must be finite, ordered by time, inside the video duration, and contain at least two points when movement is requested. The helper linearly interpolates them and applies light Gaussian smoothing to position only. It never changes overlay size because the subject moves.

## Command

```bash
python3 /path/to/skills/video-overlay-censor/scripts/apply_overlay.py \
  --input /path/to/input.mp4 \
  --overlay /path/to/cover.gif \
  --output /path/to/output.mp4 \
  --start 3.45 \
  --width 270 \
  --height 190 \
  --keyframes /tmp/overlay-keyframes.json \
  --remove-edge-white
```

For a fixed screen position, omit `--keyframes` and provide `--x` and `--y` as the overlay center. For proportional sizing, omit `--height`; otherwise pass both `--width` and `--height` to keep the exact dimensions constant. Use `--end` to stop the overlay before the video ends. Use `--ffmpeg` and `--ffprobe` when the binaries are not on `PATH`.

## Important boundaries

- This is an overlay/compositing workflow, not an automatic nude or sensitive-region detector. Do not claim semantic recognition or frame-perfect segmentation.
- Never use a moving overlay without checking sampled frames; fast turns, occlusion by hands, and cuts need additional keyframes or a different mask.
- Preserve the original video. Write a new output file unless the user explicitly requests replacement and the workflow can stage an atomic replacement safely.
- Preserve audio by stream-copying it when possible; if the output container rejects the source audio, transcode explicitly and report it.
- Do not silently crop, retarget, resize, or choose a different user asset. Report adjustments.
