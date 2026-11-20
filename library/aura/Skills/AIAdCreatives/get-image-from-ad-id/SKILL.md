---
name: get-image-from-ad-id
description: "Download full-size ad images from Meta by ad ID. Use when the user provides one or more Meta ad IDs and wants the actual creative image files. Handles both static creatives (direct image_url) and dynamic/multi-image creatives (resolves via asset_feed_spec image hashes). Outputs downloaded images and displays them inline."
---

# Get Image From Ad ID

Download full-size ad creative images from the Meta Marketing API given one or more ad IDs.

---

## What the User Must Provide

| Input | Required | Default |
|-------|----------|---------|
| Ad ID(s) | Yes | — |
| Output folder | No | `/tmp/meta-ad-images` |

Ad IDs can come from:
- `python3 scripts/meta/top-ads.py --json` output
- The Meta Ad Library URL (`facebook.com/ads/library/?id=<ad_id>`)
- Any previous `top-ads` run in this session

---

## Phase 1: Validate Environment

Check `.env` exists and `META_TOKEN_AURIE` is set:

```bash
grep -q META_TOKEN_AURIE .env && echo "OK" || echo "MISSING"
```

If missing, tell the user to add `META_TOKEN_AURIE=<token>` to `.env` and stop.

---

## Phase 2: Download Images

Run the script with the provided ad IDs:

```bash
python3 scripts/meta/get-images.py <ad_id> [<ad_id> ...] --out /tmp/meta-ad-images
```

The script handles the full resolution chain automatically:
1. Fetches the creative attached to each ad (`image_url`, `thumbnail_url`, `asset_feed_spec`)
2. For dynamic creatives, extracts image hashes from `asset_feed_spec`
3. Batch-fetches full-size CDN URLs from the `adimages` endpoint by hash
4. Downloads each image to the output folder

Output filenames follow the pattern `{ad_id}-{hash_prefix}.jpg`.

If an ad only has a thumbnail (64×64) — usually a video ad — the script flags it with `[thumbnail]` in the output. Tell the user that video ads don't expose a static frame via the API.

---

## Phase 3: Display Images

After download, read and display each image inline using the Read tool so the user can see them directly in the conversation.

If multiple images were downloaded (dynamic creative with multiple image variants), display all of them.

---

## Error Handling

| Error | Action |
|-------|--------|
| API error on a specific ad ID | Log and continue with remaining IDs |
| No images found for an ad | Tell the user — likely a video-only ad |
| Thumbnail only | Download it but flag as low-res; suggest checking Ad Library link directly |
| Missing token | Stop and prompt user to add `META_TOKEN_AURIE` to `.env` |

---

## Quick Reference

```bash
# Single ad
python3 scripts/meta/get-images.py 120246806405040184

# Multiple ads, custom output folder
python3 scripts/meta/get-images.py 120246806405040184 120246298586460184 --out /tmp/my-ads

# Print URLs only (no download)
python3 scripts/meta/get-images.py 120246806405040184 --json
```
