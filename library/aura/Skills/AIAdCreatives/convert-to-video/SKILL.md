---
name: convert-to-video
description: "Animate a static ad image into a 5–10s video clip via Kling (or any registered video provider). Reads the image as a visual formula, writes a motion-only direction prompt, and submits to the provider — keeping the on-image text stable. Use when: turn this ad into a video, animate this image, make a video from this composite, image to video, motion ad."
---

# Convert to Video — Image → Motion Ad

User points at an ad image (composite under `output/briefs/.../images/` or anywhere on disk) and wants a short Meta-ready video clip. This skill reads the image, drafts a motion-only direction prompt, and dispatches to a registered video provider (Kling today; the pipeline accepts new providers without changes here).

**Before starting:** identify the app (Aurie or Aura) from the brief folder name or the user. Load brand context from `references/brand/aurie.md` or `references/brand/aura.md` — the brand sets the motion menu (warm/healing for Aurie, calm/sleep for Aura).

---

## What the User Must Provide

| Input | Required | Default |
|-------|----------|---------|
| Source image path | Yes | — |
| Provider | No | `kling` |
| Duration (5 or 10s) | No | `5` |
| Mode (`std` / `pro`) | No | `std` |
| Model | No | provider default (`kling-v3`) |
| Motion direction notes | No | inferred from image teardown |

If no source image is provided, ask. If the user just says "make a video of brief 51", resolve the path under `output/briefs/051-*/images/` — confirm which image when there are multiple.

---

## Phase 1: Read the Image

View the image and run a formula extraction using [`references/generation/formula-extraction.md`](../../../references/generation/formula-extraction.md). The fields that matter for animation:

- **Subject** — what could move? (eyes, chest, hair, fabric, ears, fur)
- **Background** — flat, gradient, environmental? Where can ambient motion live?
- **Color palette** — what light wash would feel native? (Aurie: warm gold; Aura: cool blue/lavender)
- **Text zones** — record their pixel bounds. These must stay perfectly still.
- **Composition** — is the subject centered? Off-axis? This decides whether a push-in or hold reads better.

Keep this in conversation. Do not save a teardown file.

---

## Phase 2: Draft the Motion Direction

Open [`references/generation/image-to-video-directives.md`](../../../references/generation/image-to-video-directives.md) and pick **at most two** patterns from the brand-fit motion menu — one subject motion, optionally one ambient. Stacking more drifts.

Compose the prompt using the skeleton from the directives doc:
1. Subject motion (1–2 short clauses, with pace verbs)
2. Optional ambient/background motion
3. Optional subtle camera move (default to "camera holds")
4. End-state clause ("…then settles back into stillness.")

Keep under ~80 words. Do **not** re-describe the scene the image already shows.

Always include the explicit text-stability clause: `"text and bottom UI remain perfectly still."`

Present the drafted prompt to the user in one block, plus a one-line summary of which patterns you picked and why. Wait for approval (or edits) before generating — Kling jobs cost real money and time.

---

## Phase 3: Generate the Video

**Check `.env` first** — must contain `KLING_ACCESS_KEY` and `KLING_SECRET_KEY` for Kling, or the equivalent env vars for whichever provider is chosen. If missing, stop and ask the user to add them; do not invent placeholder values.

**Output folder:** all videos are written under `output/videos/` using a consistent per-session structure.

1. Compute a **session timestamp** once at the start of the run: `YYYYMMDD-HHMMSS` (e.g. `20260502-143022`).
2. For each ad, derive an **ad title**:
   - Brief composites: use the brief folder name (e.g. `051-AURIE-3am-anxiety`)
   - One-off images outside `output/briefs/`: use the image stem (e.g. `my-composite`)
3. Final structure per ad:
   ```
   output/videos/{datetime}/{ad-title}/
       {image-stem}.mp4    ← the generated clip
       prompt.txt          ← the motion-direction prompt (written by the script automatically)
   ```

Create the folder before invoking the script (`mkdir -p`).

### Run the generator

```bash
python3 scripts/generate-video.py \
    --image "<path to source image>" \
    --prompt "<motion-only prompt from Phase 2>" \
    --out "output/videos/<datetime>/<ad-title>/<image-stem>.mp4" \
    --provider kling \
    --duration 5 \
    --mode std
```

Optional flags:
- `--model kling-v2-6` — strong motion fidelity, slower
- `--model kling-v2-5-turbo` — fastest draft
- `--mode pro` — higher fidelity, slower, supports `--image-tail`
- `--cfg-scale 0.6` — raise if the model is ignoring the prompt
- `--image-tail <path>` — supply a near-identical end frame to lock the on-image text (Kling pro mode only)

A standard negative prompt covering text warping, jitter, and morphing is applied automatically — override with `--negative-prompt` only if the brief calls for it.

The script blocks until the MP4 lands on disk (typical: 1–5 minutes). It streams `task_id` and polling status to stdout.

---

## Phase 4: Deliver

Once the file is saved, share with the user:
- Path to the MP4
- Which patterns animated (in one line)
- Any caveats (e.g., text shifted slightly — re-run with `--image-tail`?)

No tracker update, no `data.json` edits. Video URLs from the provider are ephemeral — always download to disk before considering the job done. The downloader is built in.

---

## Key Rules

- **Animate, don't reinvent.** Image is the anchor — describe motion only, never re-describe what the image already shows.
- **Text never moves.** Always include the text-stability clause; prefer holds or sub-5% pushes.
- **One or two patterns.** Stacking more reliably drifts. Pick from the brand-fit motion menu.
- **End-state always.** Every prompt closes with how motion settles.
- **Wait for approval.** Surface the prompt before submitting — Kling jobs cost money.
- **Never bypass the directives.** If a generation reveals a new pattern, edit `references/generation/image-to-video-directives.md` rather than encoding the lesson in conversation.
