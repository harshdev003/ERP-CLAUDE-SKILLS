---
name: ad-assets-generator
description: "Takes a 9:16 (1080×1920) source ad image and produces three deliverables in one run: a 4:5 Feed image (via feed-size-generator), a Reels image with bottom clearance (via reels-size-generator), and a short motion video clip animated from the Reels image (via convert-to-video). Input: image_path (required). Use when: generate all ad sizes, create feed and reels from this image, package this ad for all placements, animate after resizing."
---

# Ad Assets Generator

Orchestrates the full ad asset pipeline for a single source image: Feed resize, Reels resize (run in parallel), then video animation from the Reels output.

## Inputs

```json
{
  "image_path": "output/briefs/038-AURIE-anxiety-chatgpt/images/038-v1.png"
}
```

- `image_path` — required. Path to the source 9:16 (1080×1920) composite image.

## Process

### Step 1: Validate Input

Confirm `image_path` exists. Check image dimensions using Python/Pillow — warn (but do not stop) if not exactly 1080×1920.

Derive output paths from the source image stem and directory:
- Feed image: `{same_dir}/{stem}-feed.png`
- Reels image: `{same_dir}/{stem}-reels.png`

### Step 2: Generate Feed and Reels Sizes (Parallel)

Spawn both sub-agents in a single message so they run concurrently:

**Sub-agent 1 — `feed-size-generator`:**
```json
{
  "image_path": "<resolved_image_path>",
  "output_path": "<same_dir>/<stem>-feed.png"
}
```

**Sub-agent 2 — `reels-size-generator`:**
```json
{
  "image_path": "<resolved_image_path>",
  "output_path": "<same_dir>/<stem>-reels.png"
}
```

Wait for both to complete. Confirm each output file exists and print its path before moving on.

### Step 3: Convert Both Images to Video (Subagent)

Spawn a single general-purpose subagent with the following brief:

> Read `.claude/skills/convert-to-video/SKILL.md` for the full workflow. You will generate **two** videos from the same ad — one from the Reels image (9:16) and one from the Feed image (4:5). Use the **exact same motion prompt** for both.
>
> **Phase 1 — Teardown:** Use the Reels image (`<resolved_reels_output_path>`) as the reference for image teardown and motion analysis only.
>
> **Phase 2 — Motion prompt:** Draft the motion direction prompt from the Reels image teardown. Surface it to the user and wait for their approval before generating any video.
>
> **Phase 3 — Generate both videos in parallel** using the approved prompt:
> - Reels video (9:16): source `<resolved_reels_output_path>`, output `output/videos/<datetime>/<ad-title>/<stem>-reels.mp4`
> - Feed video (4:5): source `<resolved_feed_output_path>`, output `output/videos/<datetime>/<ad-title>/<stem>-feed.mp4`
>
> Set `<datetime>` once at the start (format `YYYYMMDD-HHMMSS`) and use the same folder for both outputs. Run both `generate-video.py` calls in parallel (background `&` processes + `wait`).
>
> **Phase 4 — Deliver:** Report both video paths and which motion patterns were used.

Monitor the subagent. If it pauses to ask for review or approval of the motion prompt, surface the proposed prompt to the user and wait for their explicit approval before instructing the subagent to proceed. Only skip this user-facing approval step if the user has explicitly asked to auto-approve in the current session.

### Step 4: Final Report

After all four deliverables are ready, print a summary:

```
✓ Feed (4:5):       output/briefs/.../images/<stem>-feed.png   (1080×1350)
✓ Reels (9:16):     output/briefs/.../images/<stem>-reels.png  (1080×1920)
✓ Video (9:16):     output/videos/<datetime>/<ad-title>/<stem>-reels.mp4
✓ Video (4:5):      output/videos/<datetime>/<ad-title>/<stem>-feed.mp4
```

## Constraints

- Run feed-size-generator and reels-size-generator in parallel (single message, two Agent tool calls).
- Never start video generation before both the Feed and Reels images are confirmed on disk.
- Always generate both videos using the same motion prompt — never draft separate prompts.
- Never modify `openai_client.py` or `generate-video.py`.
- If `.env` is missing KLING keys, stop and tell the user — do not invent placeholder values.
