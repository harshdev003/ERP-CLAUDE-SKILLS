---
name: feed-size-generator
description: "Converts an existing composite ad image to Facebook Feed format (4:5, 1080×1350). Calls OpenAI images.edit() to recompose the layout — reducing spacing, subject size, and font sizes — so all content fits the shorter canvas without cropping. Input: image_path (required), output_path (optional)."
---

# Feed Size Generator Agent

Converts an existing ad composite to Feed format: **1080×1350 (4:5)**.

## Inputs

```json
{
  "image_path": "output/briefs/038-AURIE-anxiety-chatgpt/images/038-v1.png",
  "output_path": "output/briefs/038-AURIE-anxiety-chatgpt/images/038-v1-feed.png"
}
```

- `image_path` — required. Path to the source composite image (any size, typically 9:16).
- `output_path` — optional. Defaults to `{source_stem}-feed.png` in the same folder.

## Process

### Step 1: Resolve paths

- Resolve `image_path` relative to the project root.
- If `output_path` is not provided, derive it: same directory as `image_path`, filename = `{stem}-feed.png`.
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

prompt = """Reformat this ad image for Facebook Feed (4:5 portrait ratio).

Keep the design identical — same background color, same text content, same visual subject, same brand elements. Only include elements that are visibly present in the source image. Do NOT add, invent, or hallucinate any elements that are not in the source — no badges, social proof panels, star ratings, award logos, or any other UI elements.

Layout adjustments required:
- Fit ALL visual content within a 4:5 canvas (shorter than the original 9:16)
- Reduce vertical spacing between elements by 25–30% to compensate for the reduced height
- Scale down the main visual subject (orb, character, illustration) and other visual elements by 15–20%
- Reduce font sizes by approximately 15%
- Maintain all proportional relationships between elements
- Maintain all layout details - content positioning and alignment
- No empty dead space at the bottom — content should fill the frame naturally

The result must be an exact visual copy of the input image, just resized to fit the 4:5 canvas."""

success = generate_image(
    prompt=prompt,
    aspect="4:5",
    out_path=output_path,
    label="feed-resize",
    reference_image=image_path,
    target_size=(1080, 1350),
    force=True,
)

if success:
    print(f"Feed image saved: {output_path}")
else:
    print("ERROR: OpenAI edit failed")
```

### Step 3: Verify output

After generation:
- Confirm the file exists at `output_path`
- Open the image and print its dimensions — must be exactly 1080×1350

### Step 4: Report

Print both paths:
```
✓ Source:  <image_path>  (<original WxH>)
✓ Output:  <output_path>  (1080×1350)
```

## Constraints

- Never modify `openai_client.py`
- Never regenerate if `output_path` already exists and `force` is not set — skip and report
- Do not crop the source image before sending to OpenAI — send the full original
- Target size passed to `generate_image` must always be `(1080, 1350)`
