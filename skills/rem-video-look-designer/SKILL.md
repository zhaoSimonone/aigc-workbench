---
name: rem-video-look-designer
description: Analyze an input video's pre-transformation segment and design or generate photorealistic adult Rem makeup, hair, and outfit reference images. Use for Rem makeover reference generation and face/hair-only replacement prompts; do not use for unrelated characters or final video editing unless explicitly requested.
---

# Rem Video Look Designer

Turn an input video into one or more production-ready Rem look reference images for its opening segment. Preserve the source video's visual logic while keeping Rem recognizable and temporally stable.

## Required context

- One input video.
- Optional: the exact transformation time, desired number of concepts, generation backend/model, or a user-supplied style reference.
- Infer reasonable defaults instead of blocking when these are omitted.

## Workflow

1. Create a task output directory. First extract metadata and provisional opening frames:

   ```bash
   python scripts/extract_video_refs.py INPUT_VIDEO --out-dir OUTPUT/analysis
   ```

   The default boundary at 50% is provisional. Read the duration from `manifest.json`, then scan the whole timeline before accepting the boundary:

   ```bash
   python scripts/extract_video_refs.py INPUT_VIDEO \
     --out-dir OUTPUT/full-scan \
     --full-video \
     --fractions 0.08,0.28,0.46,0.62,0.78,0.94
   ```

   Inspect both contact sheets. Identify the first frame belonging to the transformed look, set the opening boundary immediately before it, and rerun the opening extraction with `--front-end SECONDS_OR_TIMECODE`. Never infer the opening style from a provisional sample that already contains the transformed outfit.

2. Read [references/rem-identity.md](references/rem-identity.md) before designing any look. Use the bundled assets hierarchically: `rem-warm-portrait.png` is the always-on face and makeup anchor; then choose exactly one hair/body anchor—`rem-identity-full.png` for compact or medium hair, or `rem-long-wave.png` for chest-length moving waves. Never treat all three as equal hair references. They are identity assets, not wardrobe references.

3. Read [references/prompt-contract.md](references/prompt-contract.md). Analyze the opening segment for framing, pose, lighting direction, palette, background, hair movement, garment silhouette, motion/occlusion risks, and the contrast created by the later transformation.

4. Select the task mode from the user's request:

   - `source-locked-look` (default when the reference image will replace a person in an existing video): redesign Rem's face, makeup, and motion-compatible hair, but reproduce the source video's clothing exactly. Lock garment type, neckline, straps, coverage, hem, waistband, material, color, print/logo placement, drawstrings, fasteners, and silhouette. Do not simplify distinctive garment graphics.
   - `new-look` (only when the user explicitly asks to redesign the outfit): redesign Rem's makeup, hair, and wardrobe to fit the opening segment.
   - `identity-only`: replace only face and hair in the video itself. Lock the video's body, clothing geometry, neckline, exposed-skin area, material, color, motion, camera, background, and timing exactly. Ignore wardrobe and body information in still-image references.

   If the request is genuinely ambiguous and the modes would create materially different results, ask one concise question.

5. Produce the requested number of concepts; otherwise produce three meaningfully different concepts. Vary silhouette, styling language, and palette—not merely color. Rank them for opening-hook strength, Rem recognizability, motion stability, and contrast with the transformed second half.

6. For each concept, write a prompt packet using the contract. State the role of every reference image. A video frame supplies composition, pose, lighting, source clothing, and scene cues; the face anchor supplies identity and makeup; exactly one conditional hair anchor supplies hair length and motion.

7. Generate the images with the backend the user specifies. If no built-in image tool is available, read [references/image-backends.md](references/image-backends.md) and run `scripts/generate_reference.py`. The script defaults to `gpt-image-2` but the provider, model, base URL, and custom command adapter are configurable. Never place API keys in prompts, files, logs, or command arguments. Do not make more than two paid generation attempts per concept without user confirmation, and never automatically retry moderation or user-input errors.

8. Inspect every result at full resolution. Reject or regenerate images that fail the quality gate below. Do not claim success merely because an API returned a file.

## Quality gate

- Same adult Rem identity across all concepts: consistent face, proportions, ice-blue hair, side-swept fringe, and purple signature hair accessories.
- Hair choice follows source motion: use the bob for compact/static motion; chest-length soft S-waves for a source with dynamic long hair. Never use waist-length hair or dense curls that obscure hands, neckline, waist, or face.
- Natural realistic skin and makeup; no plastic skin or aggressive whitening.
- Clear hands, shoulders, waist, and feet when present in the source framing. No fused limbs, extra fingers, warped shoes, floating accessories, text, logos, or watermarks.
- `source-locked-look`: compare the result against a clear source frame garment-by-garment. Clothing type, neckline, straps, coverage, crop/hem, waistband, fabric, base color, edging, drawstrings, fasteners, and distinctive prints or licensed character patches must match in placement and scale. Treat any mismatch as a failed QC because it can cause deformation during video replacement. Garment-native graphics are allowed; unrelated captions and watermarks are not.
- `new-look`: garments remain readable during the source motion; avoid long loose ribbons, chains, tassels, and unstable multilayer hems unless the source action is nearly static.
- `identity-only`: source clothing remains pixel-semantically unchanged in design, coverage, color, and silhouette. In particular, do not raise, shrink, close, or redesign an existing neckline.
- Match source aspect ratio unless the user requests a different deliverable. For vertical short video, prefer a 9:16 full-body or source-matched crop.

## CapCut prompt output

When the user asks for a CapCut/剪映 replacement prompt, read the `identity-only` section in [references/prompt-contract.md](references/prompt-contract.md). In the external prompt, call the subject “参考图片中的同一位成年蓝发女性”; do not rely on a copyrighted character name. The source video remains the sole authority for body, clothing, neckline, coverage, motion, expression timing, camera, background, text, and audio. Include natural hair-strand physics and facial micro-expression constraints so the result does not look like a plastic wig or frozen mask.

## Deliverables

Save a reproducible packet:

```text
OUTPUT/
  analysis/manifest.json
  analysis/frames/*.png
  analysis/contact-sheet.png       # when Pillow is available
  concept-01/prompt.md
  concept-01/reference.png
  concept-01/qc.md
  capcut-prompt.md                 # when requested
  concept-02/...
```

Include the chosen mode, transformation boundary, backend/provider, model name, and which references were used. If generation cannot run, still deliver the analysis and complete prompt packets with the exact configuration needed to resume.

Do not edit or overwrite the user's source video. Final video replacement is a separate task unless the user explicitly requests it.
