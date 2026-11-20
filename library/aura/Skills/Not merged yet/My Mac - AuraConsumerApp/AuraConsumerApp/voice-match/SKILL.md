---
name: voice-match
description: Score candidate TTS voices against Aurie's current voice on measures that hold across sexes (pitch movement, pace, pauses, dynamics, breathiness, warmth), so a male voice can be matched to the female Aurie voice. Use when the user says "match these voices against Aurie", "which male voice sounds most like Aurie", "score the voice candidates", or is shortlisting voices from Cartesia, Inworld or Fish Audio.
---

# Voice match

Ranks candidate voices by how closely they *speak* like the reference, then checks each one's
pitch against a target band for its own register. The ranking narrows a shortlist; a blind
listening review still makes the call on warmth and brand fit.

## What is compared, and why

A female and a male voice differ by anatomy in absolute pitch, formants and timbre, so comparing
those to Aurie only measures sex. They are **not scored against the reference**:

| Not compared | Why |
|---|---|
| Absolute pitch (F0 Hz) | ~an octave apart by sex — checked against `--target-band` instead (default 95–135 Hz, a calm male register) |
| Formants, MFCCs, speaker embeddings | Encode vocal-tract size, i.e. sex and identity |

Scored against the reference, because they describe speaking style rather than anatomy:

| Measure | Carries |
|---|---|
| Pitch range and movement, in **semitones** | Expressiveness — relative, so it transfers across registers |
| Pace (syllable-like onsets/s) and pause share | Calm vs rushed |
| Loudness variation | Soft and even vs punchy |
| Harmonics-to-noise ratio | Clear vs breathy |
| Spectral tilt | Warm vs bright |

Each measure becomes a 0–100 similarity; **Match** is their weighted blend (pitch movement and
pace weigh highest), **Overall** = 80% Match + 20% pitch-band fit.

## Usage

Needs numpy (use a venv — the system Python refuses `pip install`). Audio in any format
`afconvert` reads.

```bash
python3 -m venv "$TMPDIR/vm" && "$TMPDIR/vm/bin/pip" install -q numpy
"$TMPDIR/vm/bin/python" .claude/skills/voice-match/scripts/voice_match.py \
  --reference ref/*.wav --candidates candidates/ --out voice_match_report
```

Name candidate files `provider_voice[_line].ext` (e.g. `cartesia_marcus_greeting.wav`); every
file sharing `provider_voice` is measured as one voice. Every candidate must read the **same
script** at the **same settings** (speed 1.10, no emotion, vendor-default stability) — otherwise
pace and pitch differences come from the text and tuning, not the voice.

Writes `<out>.md` (ranked table) and `<out>.json` (all measurements).

## The reference must be clean

The reference sets every target, so a noisy one breaks the whole ranking. A Mac-microphone
capture of the simulator put Aurie only ~5 dB above the room floor: noise was read as speech
(pause share 0.76, pace 8/s). Use BlackHole instead — verified, gives digital silence between
turns:

1. Set the Mac's **input and output** to **BlackHole 2ch**, then restart the simulator (a running
   voice session drops when devices change, and the Simulator picks the device up at boot).
2. Open a voice session, then record and speak prompts into the same loopback:
   ```bash
   swift scripts/record_mic.swift raw.wav 160 "BlackHole 2ch" &   # captures Aurie and the prompts
   afplay prompt.aiff                                              # default output = BlackHole → the sim's mic
   ```
   Log each prompt's start/end time, and cut those spans out — what is left is Aurie only.
3. Sanity-check the reference row: pause share ~0.2–0.4, pace ~3–5/s. Clean capture measured
   Aurie at 176 Hz, pause 0.23, pace 3.9/s.
4. Put the Mac's input and output back afterwards.

`scripts/play_device.swift` plays a file on a named device (e.g. the speakers) when only the
output is on BlackHole and the prompts must reach the microphone acoustically.

Alternatively use the provider's own render of the current Aurie voice (control arm: ElevenLabs
`ELEVENLABS_VOICE_ID` in the voice agent's config) reading the same test script.
