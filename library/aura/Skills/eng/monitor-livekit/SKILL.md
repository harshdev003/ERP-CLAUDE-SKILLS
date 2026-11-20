---
name: monitor-livekit
description: >
  Watch the Aurie LiveKit voice agent (aurie-voice-agent) over a short recent window and say
  whether anything changed. Compares the last 30 minutes (or N) with the same window 24 hours
  earlier from the forwarded Cloud Logging rows: sessions by release, ERROR+ per session, ERROR
  groups that are new, WARNING groups, log-screen stamps, error codes and self-recovery rows
  (STT stall reconnects, TTS retries, turn-detector re-probes, metrics retries), then a NORMAL
  or LOOK verdict naming each reason. A second mode classifies every STT stall reconnect. Use for
  "monitor livekit", "monitor the voice agent", "anything new on the livekit agent", "voice
  agent errors since the release", "did the stalls recover", "post-release livekit check", or
  one tick of eng:livekit-loop. Do NOT use for the daily standup snapshot
  (eng:report-livekit-health), a go-live PASS/FAIL gate (eng:check-livekit-health), the live
  lk log tail (eng:investigate-livekit-logs) or one user's complaint (eng:investigate-voice-issue).
metadata:
  version: "1.4.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/GCP     # GCP_KEY service account for Cloud Logging reads
---

# monitor-livekit

A short-window, change-oriented monitor for the Aurie LiveKit voice agent. The question it answers is **"is this window different from the same window yesterday, and did the agent's own recovery paths work?"**, which is what a post-release watch or a loop tick needs. Read-only.

It reads `projects/firebase-auratech16/logs/aurie-voice-agent`. That is the agent's own per-job Cloud Logging forwarder, so every row carries `serviceContext.version`, the release that produced it. Rows logged from the worker process (prewarm, the startup banner) never reach it.

## Run

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/monitor-livekit/scripts"
python3 "$S/monitor_livekit.py" window                 # last 30 min vs same window -24h
python3 "$S/monitor_livekit.py" window --minutes 120   # wider window
python3 "$S/monitor_livekit.py" window --json          # machine-readable, for a loop
python3 "$S/monitor_livekit.py" stalls --hours 12      # STT stall reconnect outcomes
```

Credentials: `GCP_KEY` (the service account) through the bundled `gcloud_sa.py`. A read failure exits 2 and names the credential. So does a read that fills its row cap: a truncated page looks exactly like a quiet window, so the script refuses it instead of reporting wrong counts. Narrow the window. The user's gcloud login is used only when `GCP_KEY` is unset. The rollout check also needs `gh` authenticated with read access to `AuraHealth/LivekitVoiceAgent`; without it, a mixed window stays LOOK.

## Reading the output

**`window`** prints both windows, then `VERDICT: NORMAL` or `VERDICT: LOOK` with one reason per line. The verdict is a prompt to read, not a diagnosis. It says LOOK when:

- ERROR+ per session is more than twice the -24h rate (and above 0.05);
- an ERROR group appears that the -24h window did not have;
- a failed recovery shows up (`STT_STALL_RECOVERY_FAILED`, `TTS_RETRY_RECOVERY_FAILED`, `TURN_DETECTOR_REPROBE_FAILED`, `CONVERSATION_METRICS_FAILED`, `TTS_RETRY_STALLED[none]`);
- STT stall reconnects outnumber their recoveries by two or more, in which case run `stalls`;
- more than one release is serving sessions, unless it is a planned rollout (below);
- the median session got an LLM reply on fewer than 40% of its user turns (the answered-turn ratio:
  `Turn latency` rows with `metric=llm_ttft_ms` over `User turn shape` rows, per `room`, else per agent job `transactionSlice`), counting
  sessions with 5+ user turns and needing at least 4 of them. Normal is 65–75%. A failed LLM turn logs
  no error, so this is the only signal for silent replies: in the 2026-11-03 03:45–04:15Z outage the
  median was 0% over 11 sessions with no ERROR row (ENG-1767, PROD-8206). It is the median, not the
  window total, because one user who keeps talking over Aurie's replies cancels them unheard: on
  2026-11-04 06:26Z one 83-turn session with 5 answered pulled a healthy window to 35% (74/210) while
  the median was 56% over 9 (ENG-1787).

**A planned rollout is not a LOOK.** LiveKit Cloud drains old sessions for up to about an hour after a deploy, so a mixed window is expected. The script reads the latest LivekitVoiceAgent release with `gh release view` and, when the newest version in the window **is** that release and it was published under 90 minutes before the window's end, prints `info: rollout in progress: …` instead of the LOOK reason. It stays LOOK when an older version is still serving 90+ minutes after the latest release (a stuck drain), when the newest version in the window isn't the latest release, or when `gh` can't answer.

Three ways to misread it:

- **Low traffic makes ratios noisy.** At night a window can hold 2 sessions, so one ERROR row doubles a rate. Read the counts, not just the ratio, and widen `--minutes` before calling a trend.
- **A zero is not proof a path is healthy.** Every signal is matched on an exact message or `error_code`. If a release renames a row, it reads as zero here. When a signal disappears right after a release, check the agent's diff before celebrating.
- **One ElevenLabs mid-reply drop a day is the accepted baseline**: `TTS_STREAM_CLOSED` plus its `SESSION_ERROR`. Flag it only when it repeats above roughly two ERROR rows a day.

**`stalls`** joins each `Reconnected stalled STT stream` to the same session's next `STT stream recovered after a stall reconnect` or `Ending session`:

- `recovered`: the reconnect worked.
- `session ended: <reason>`: the session closed first. `reconnect timeout` points at the client's network rather than at speech-to-text; `user away` and `client left` are users leaving.
- `pending (<10 min)`: too recent to judge.
- `unknown`: no recovery and no end row. Look at that session's transaction in Cloud Logging.

## The signal catalog

The lists at the top of `scripts/monitor_livekit.py` (`STAMPS`, `CODES`, `RECOVERY_MESSAGES`, `ALWAYS_LOOK`) mirror rows the agent emits. They come from the LivekitVoiceAgent repo: `src/errors.py` for codes, `src/log/*_logging.py` for stamps, and `src/sessions/` for recovery rows. When an agent PR adds, renames or removes one of those rows, update the catalog in the same breath, or this skill silently stops seeing it.

## Privacy

The script reads structured fields only: message templates, codes, stamps, versions and transaction ids. Never paste a row's spoken or transcript text into a card, PR or Slack post. Refer to a session by its `transaction` id.
