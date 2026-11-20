---
name: publish-approved-ads
description: "Read a Slack thread where ad visuals were posted for review, identify which ads were approved, generate all ad sizes (feed 4:5, reels 9:16, video) for each approved ad in batches of 3, then post every ad back to Slack as one message per ad with all four assets attached. Use when: publish approved ads, package and post approved creatives, generate assets for approved ads and post to Slack."
---

# Publish Approved Ads

End-to-end skill: reads a Slack review thread → identifies approved ads → generates feed/reels/video assets → posts each ad to Slack with all creatives attached.

---

## What the User Must Provide

| Input | Required | Notes |
|-------|----------|-------|
| Slack thread URL | Yes | e.g. `https://auramobile.slack.com/archives/C0AU7C07R8X/p1778181680208749` |
| Output folder | No | Root folder containing per-ad subfolders. Defaults to auto-detecting from the filenames in the thread. |
| Slack channel | No | Channel to post final assets to. Defaults to the same channel as the thread. |

If the Slack thread URL is missing, stop and ask.

---

## Phase 1: Read the Slack Thread

Parse the thread URL to extract `channel_id` and `message_ts`:
- URL format: `https://<workspace>.slack.com/archives/<channel_id>/p<ts_digits>`
- `channel_id` — the `C...` segment after `/archives/`
- `message_ts` — insert a `.` after the 10th digit of the `p...` segment (e.g. `p1778181680208749` → `1778181680.208749`)

Call `slack_read_thread` with those values. Read the full thread including all replies.

From the thread:
1. **Identify the ad filenames** — extract the filenames from the parent message (the bot post listing the ad visuals).
2. **Determine approval status** — scan replies for explicit approvals. Count an ad as approved if:
   - A reviewer says they're down to test all / approve all (applies to every ad)
   - A reviewer explicitly calls out specific ad names or all ads in the batch
   - The thread reaches clear consensus (e.g. multiple reviewers agree)

3. **Present the full list and ask for confirmation** — always show every ad from the thread, split into two groups, then pause:

```
Approved (will generate + post):
  ✓ embered-heart-cradle
  ✓ lantern-release-shore
  ✓ aurora-ribbon-figure

Not approved (will be skipped):
  ✗ some-other-ad

Proceed?
```

Do not move to Phase 2 until the user explicitly confirms. If the user corrects the list (e.g. "skip aurora-ribbon-figure" or "include some-other-ad"), update accordingly and re-present before proceeding.

If no ads are approved, stop and report. Do not generate assets for unapproved ads.

---

## Phase 2: Locate Local Files

For each approved ad filename (e.g. `embered-heart-cradle.png`), derive the slug by stripping the extension: `embered-heart-cradle`.

Search for the source image:
```bash
find output/ -type f -name "<slug>.png" | head -5
```

The canonical path pattern is:
```
output/visual-reskin/<YYYYMMDD>/<slug>/<slug>.png
```

Collect the resolved absolute path for each approved ad.

**If any source image is not found, stop immediately.** Do not proceed to Phase 3 or Phase 4. Report every missing file clearly:

```
✗ embered-heart-cradle — source image not found (searched: output/visual-reskin/...)
✗ lantern-release-shore — source image not found
```

Then tell the user:

> The above source images could not be located. Please confirm the correct folder and file paths, then re-run.

Do not generate, create, or post anything until every approved ad has a confirmed source image on disk.

**Once all source images are confirmed**, check which derivative assets already exist — skip generating any size that already exists:
- Feed: `<same_dir>/<slug>-feed.png`
- Reels: `<same_dir>/<slug>-reels.png`
- Reels video: search `output/videos/` for `<slug>-reels.mp4` — if found, note its path
- Feed video: search `output/videos/` for `<slug>-feed.mp4` — if found, note its path

Report which ads need full generation vs. partial vs. already complete.

---

## Phase 3: Generate Assets (Batches of 3)

Process approved ads in batches of **3** (never all at once). For each batch:

### Step A — Feed + Reels (parallel)

For each ad in the batch that is missing feed or reels, spawn feed-size-generator and reels-size-generator sub-agents in a **single message** (parallel). Use explicit output paths:

```
feed:  <same_dir>/<slug>-feed.png
reels: <same_dir>/<slug>-reels.png
```

Wait for all resize agents in the batch to complete and confirm each output file exists before continuing.

### Step B — Video (parallel within batch)

Once feed and reels are confirmed on disk, spawn one general-purpose agent per ad in the batch. Each agent generates **two** videos (9:16 from the reels image, 4:5 from the feed image) using the **exact same motion prompt**:

> Read `.claude/skills/convert-to-video/SKILL.md` for the full workflow. You will generate **two** videos for this ad — one from the Reels image (9:16) and one from the Feed image (4:5). Use the **exact same motion prompt** for both.
>
> **Phase 1 — Teardown:** Use `<same_dir>/<slug>-reels.png` as the reference for image teardown and motion analysis only.
>
> **Phase 2 — Motion prompt:** Draft the motion direction prompt from the Reels image teardown. Auto-approve it without surfacing for review.
>
> **Phase 3 — Generate both videos in parallel** using the same prompt:
> - Reels video (9:16): source `<same_dir>/<slug>-reels.png`, output `output/videos/<datetime>/<slug>/<slug>-reels.mp4`
> - Feed video (4:5): source `<same_dir>/<slug>-feed.png`, output `output/videos/<datetime>/<slug>/<slug>-feed.mp4`
>
> Set `<datetime>` once at the start and use the same folder for both. Run both `generate-video.py` calls in parallel (background `&` processes + `wait`).
>
> **Phase 4 — Deliver:** Report both video paths.

Wait for all video agents in the batch to complete before starting the next batch.

### Batch loop

Repeat Step A → Step B for each batch of 3 until all approved ads have assets.

After all batches complete, print an asset inventory table:

```
Ad                       Feed    Reels   Video (9:16)                          Video (4:5)
embered-heart-cradle     ✓       ✓       output/videos/20260509-005222/...reels.mp4    ...feed.mp4
lantern-release-shore    ✓       ✓       output/videos/20260509-005223/...reels.mp4    ...feed.mp4
...
```

---

## Phase 4: Post to Slack

Post one message per ad to the Slack channel. Use the channel from the thread URL (or the user-specified override).

For each approved ad, run:

```bash
python3 scripts/post-to-slack.py \
  --channel <channel_id> \
  --text "*Ad Creative Ready*\n<slug>" \
  --file <same_dir>/<slug>.png \
  --file <same_dir>/<slug>-feed.png \
  --file <same_dir>/<slug>-reels.png \
  --file <video_reels_path> \
  --file <video_feed_path>
```

Run all posts in parallel using background `&` processes + `wait`. This avoids rate-limit stacking and keeps message ordering reasonable.

If any post fails, retry once. If it fails again, report the error but continue with remaining ads.

---

## Phase 5: Final Report

After all posts complete, print a summary:

```
✓ embered-heart-cradle    — posted (original + feed + reels + video 9:16 + video 4:5)
✓ lantern-release-shore   — posted (original + feed + reels + video 9:16 + video 4:5)
✓ aurora-ribbon-figure    — posted (original + feed + reels + video 9:16 + video 4:5)
...

7 ads posted to #<channel-name>.
```

If any ads were skipped (file not found, post failed), list them separately with the reason.

---

## Constraints

- Never generate assets for ads that were not explicitly approved in the thread.
- Never generate or post anything if any approved ad's source image is missing — block and report all missing files first.
- Never skip the approval-check step, even if the user says "just post them all."
- Process in batches of 3 — never spawn more than 3 video agents at once.
- Always run feed-size-generator and reels-size-generator in parallel (single message, two agent calls).
- Never start video generation before both the feed and reels images are confirmed on disk.
- Always generate both videos (9:16 from reels, 4:5 from feed) using the same motion prompt — never draft separate prompts.
- Use `scripts/post-to-slack.py` for all Slack posts — never raw API calls or GitHub URLs.
- If `.env` is missing `SLACK_BOT_TOKEN` or `SLACK_CHANNEL_ID`, stop and prompt the user.
