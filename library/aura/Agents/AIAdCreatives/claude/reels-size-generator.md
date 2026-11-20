---
name: reels-size-generator
description: "Converts an existing composite ad image to Facebook/Instagram Reels format (9:16, 1080×1920) with 384px clear at the bottom. Calls OpenAI images.edit() to recompose the layout — reducing spacing, subject size, and font sizes — so all content sits in the top 1536px. Input: image_path (required), output_path (optional)."
---

# Reels Size Generator Agent

Converts an existing ad composite to Reels format: **1080×1920, all content within top 1536px, bottom 384px pure background**.

## Inputs

```json
{
  "image_path": "output/briefs/038-AURIE-anxiety-chatgpt/images/038-v1.png",
  "output_path": "output/briefs/038-AURIE-anxiety-chatgpt/images/038-v1-reels.png"
}
```

- `image_path` — required. Path to the source composite image (any size).
- `output_path` — optional. Defaults to `{source_stem}-reels.png` in the same folder.

## Process

### Step 1: Resolve paths

- Resolve `image_path` relative to the project root.
- If `output_path` is not provided, derive it: same directory as `image_path`, filename = `{stem}-reels.png`.
- Confirm `image_path` exists before proceeding.

### Step 2: Call OpenAI edit

Run the following Python code (inline, no separate script file):

```python
import sys
sys.path.insert(0, 'scripts')
from openai_client import generate_image
from pathlib import Path

image_path = Path("<resolved_image_path>")
output_path = Path("<resolved_output_path>")
output_path.parent.mkdir(parents=True, exist_ok=True)

prompt = """Reformat this ad image for Instagram/Facebook Reels (9:16 portrait).

Keep the design identical — same background color, same text content, same visual subject, same brand elements. Only include elements that are visibly present in the source image. Do NOT add, invent, or hallucinate any elements that are not in the source — no badges, social proof panels, star ratings, award logos, or any other UI elements.

Layout adjustments required:
- Fit ALL visual content (subject, headline, bullets, and any proof bar if present) within the top 80% of the canvas
- The bottom 20% of the canvas must be plain background only — no text, no UI elements, no gradients that contain content
- First assess how much empty space already exists at the bottom of the source image. If the source already has significant bottom clearance, little or no compression is needed. Only compress as much as required to free up the bottom 20%.
- If compression is needed: reduce vertical spacing between elements (up to 20–25%), scale down the main visual subject (up to 10–15%), and reduce font sizes (up to 12–15%) — but only apply as much of each as necessary, not the maximum
- Maintain all proportional relationships between elements
- Maintain all layout details - content positioning and alignment

The result should look like the same ad with a clean background margin at the bottom — compress only as much as the source requires."""

success = generate_image(
    prompt=prompt,
    aspect="9:16",
    out_path=output_path,
    label="reels-resize",
    reference_image=image_path,
    target_size=(1080, 1920),
    force=True,
)

if success:
    print(f"Reels image saved: {output_path}")
else:
    print("ERROR: OpenAI edit failed")
```

### Step 3: Verify output

After generation:
- Confirm the file exists at `output_path`
- Open the image and print its dimensions — must be exactly 1080×1920
- If dimensions are wrong, use Pillow to resize: `img.resize((1080, 1920), Image.LANCZOS)`

### Step 4: Report

Print both paths:
```
✓ Source:  <image_path>  (<original WxH>)
✓ Output:  <output_path>  (1080×1920)
```

## Constraints

- Never modify `openai_client.py`
- Never regenerate if `output_path` already exists and `force` is not set — skip and report
- Do not crop the source image before sending to OpenAI — send the full original
- Target size passed to `generate_image` must always be `(1080, 1920)`
