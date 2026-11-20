#!/usr/bin/env python3
"""
voice_match.py — score candidate voices against a reference voice on measures that stay
meaningful across sexes, so a male voice can be matched to Aurie's female voice.

Absolute pitch, formants and timbre fingerprints (MFCCs, speaker embeddings) differ between a
female and a male voice by anatomy, so comparing them to the reference only measures sex. This
script compares how a voice *speaks* instead — pitch movement in semitones, pace, pauses,
dynamics, breathiness, spectral tilt — and checks absolute pitch against a target band for the
candidate's own register.

Usage:
    python3 voice_match.py --reference ref1.wav [ref2.wav ...] --candidates DIR_OR_FILES...
        [--target-band 95 135] [--out report]

Any format afconvert reads (wav, aiff, mp3, m4a) works. Needs numpy only.
"""

import argparse
import json
import math
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SR = 16_000
FRAME = int(0.040 * SR)
HOP = int(0.010 * SR)
F0_MIN, F0_MAX = 60.0, 400.0

# Tolerance is the difference treated as "one unit apart"; weight is how much the measure counts.
# Pitch movement and pace carry Aurie's calm, warm character the most, so they weigh highest.
FEATURES = {
    "pitch_range_st": {"tol": 2.0, "weight": 2.0, "label": "Pitch range (semitones, p10–p90)"},
    "pitch_var_st": {"tol": 0.8, "weight": 2.0, "label": "Pitch movement (semitone SD)"},
    "speech_rate": {"tol": 0.6, "weight": 1.5, "label": "Pace (syllable-like onsets/s)"},
    "pause_ratio": {"tol": 0.08, "weight": 1.0, "label": "Pause share"},
    "loudness_var_db": {"tol": 1.5, "weight": 1.0, "label": "Loudness variation (dB SD)"},
    "hnr_db": {"tol": 2.0, "weight": 1.0, "label": "Clarity vs breathiness (HNR dB)"},
    "tilt_db_oct": {"tol": 1.5, "weight": 1.0, "label": "Warmth (spectral tilt dB/oct)"},
}


# MARK: - Audio

def load(path: Path) -> np.ndarray:
    """Returns mono float audio at 16 kHz, converting through afconvert when needed."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(path), str(wav)],
                       check=True, capture_output=True)
        with wave.open(str(wav)) as w:
            data = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64)
    return data / 32768.0


def frames(x: np.ndarray) -> np.ndarray:
    n = 1 + max(0, (len(x) - FRAME) // HOP)
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    return x[idx] * np.hanning(FRAME)


# MARK: - Features

def analyse(x: np.ndarray) -> dict:
    fr = frames(x)
    energy_db = 10 * np.log10((fr ** 2).mean(axis=1) + 1e-12)
    # A noisy mic capture has a high floor, so speech is "clearly above the quietest fifth".
    # A loopback capture has digital silence, where that floor is meaningless and every
    # breath would count as speech; there, speech is "within 35 dB of the loudest frames".
    floor = np.percentile(energy_db, 20)
    speech = energy_db > max(floor + 6, np.percentile(energy_db, 95) - 35)
    # Lead-in and tail silence come from when the capture started and stopped, not from how
    # the voice speaks, so measure only between the first and last speech frame.
    spoken = np.where(speech)[0]
    if len(spoken) == 0:
        raise ValueError("no speech found")
    fr, energy_db, speech = (a[spoken[0]:spoken[-1] + 1] for a in (fr, energy_db, speech))

    f0 = np.full(len(fr), np.nan)
    hnr = np.full(len(fr), np.nan)
    lag_lo, lag_hi = int(SR / F0_MAX), int(SR / F0_MIN)
    for i in np.where(speech)[0]:
        f = fr[i] - fr[i].mean()
        ac = np.fft.irfft(np.abs(np.fft.rfft(f, 2 * FRAME)) ** 2)[:FRAME]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lag = lag_lo + int(np.argmax(ac[lag_lo:lag_hi]))
        peak = ac[lag]
        if peak > 0.45:
            f0[i] = SR / lag
            hnr[i] = 10 * np.log10(peak / max(1 - peak, 1e-6))

    voiced = ~np.isnan(f0)
    if voiced.sum() < 20:
        raise ValueError("too little voiced speech to measure")
    st = 12 * np.log2(f0[voiced] / np.median(f0[voiced]))
    # Octave jumps from the autocorrelation are errors, not intonation.
    st = st[np.abs(st) < 9]

    # Pace: rises of the speech envelope through its median, per second of speech.
    env = np.convolve(energy_db, np.ones(5) / 5, mode="same")
    thr = np.median(env[speech])
    onsets = np.sum((env[1:] > thr) & (env[:-1] <= thr) & speech[1:])
    speech_secs = speech.sum() * HOP / SR

    spec = np.abs(np.fft.rfft(fr[voiced], axis=1)).mean(axis=0)
    freqs = np.fft.rfftfreq(FRAME, 1 / SR)
    band = (freqs > 100) & (freqs < 5000)
    tilt = np.polyfit(np.log2(freqs[band]), 20 * np.log10(spec[band] + 1e-12), 1)[0]

    return {
        "f0_median_hz": float(np.median(f0[voiced])),
        "pitch_range_st": float(np.percentile(st, 90) - np.percentile(st, 10)),
        "pitch_var_st": float(np.std(st)),
        "speech_rate": float(onsets / max(speech_secs, 1e-6)),
        "pause_ratio": float(1 - speech.mean()),
        "loudness_var_db": float(np.std(energy_db[speech])),
        "hnr_db": float(np.nanmedian(hnr)),
        "tilt_db_oct": float(tilt),
        "speech_seconds": float(speech_secs),
    }


def analyse_files(paths: list) -> dict:
    """Concatenates clips (with a short gap) so one voice is measured over all its samples."""
    gap = np.zeros(int(0.3 * SR))
    audio = np.concatenate([np.concatenate([load(p), gap]) for p in paths])
    return analyse(audio)


# MARK: - Scoring

def score(ref: dict, cand: dict, band: tuple) -> dict:
    parts, total, wsum = {}, 0.0, 0.0
    for key, spec in FEATURES.items():
        d = (cand[key] - ref[key]) / spec["tol"]
        parts[key] = round(100 * math.exp(-0.5 * d * d), 1)
        total += spec["weight"] * d * d
        wsum += spec["weight"]
    match = 100 * math.exp(-0.5 * total / wsum)
    lo, hi = band
    f0 = cand["f0_median_hz"]
    # Outside the band, every semitone away costs 10 points of pitch fit.
    off = 0.0 if lo <= f0 <= hi else 12 * abs(math.log2(f0 / (lo if f0 < lo else hi)))
    pitch_fit = max(0.0, 100 - 10 * off)
    return {"match": round(match, 1), "pitch_fit": round(pitch_fit, 1),
            "overall": round(0.8 * match + 0.2 * pitch_fit, 1), "parts": parts}


def collect(items: list) -> dict:
    """Groups candidate files by voice: `provider_voice[_anything].ext` → `provider_voice`."""
    files = []
    for item in items:
        p = Path(item)
        files += sorted(f for f in p.iterdir() if f.suffix.lower() in {".wav", ".aiff", ".aif", ".mp3", ".m4a"}) \
            if p.is_dir() else [p]
    voices = {}
    for f in files:
        voices.setdefault("_".join(f.stem.split("_")[:2]), []).append(f)
    return voices


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", nargs="+", required=True)
    ap.add_argument("--candidates", nargs="+", required=True)
    ap.add_argument("--target-band", nargs=2, type=float, default=[95, 135], metavar=("LO", "HI"))
    ap.add_argument("--out", default="voice_match_report")
    args = ap.parse_args()

    ref = analyse_files(args.reference)
    rows = []
    for voice, paths in collect(args.candidates).items():
        try:
            feats = analyse_files(paths)
        except (ValueError, subprocess.CalledProcessError) as err:
            print(f"skip {voice}: {err}", file=sys.stderr)
            continue
        rows.append({"voice": voice, "files": [str(p) for p in paths], "features": feats,
                     **score(ref, feats, tuple(args.target_band))})
    rows.sort(key=lambda r: r["overall"], reverse=True)

    Path(args.out + ".json").write_text(json.dumps({"reference": ref, "targetBand": args.target_band,
                                                     "candidates": rows}, indent=2))
    keys = list(FEATURES)
    lines = [f"# Voice match vs reference\n",
             f"Reference median pitch {ref['f0_median_hz']:.0f} Hz (not scored). "
             f"Candidate pitch band {args.target_band[0]:.0f}–{args.target_band[1]:.0f} Hz.\n",
             "| Rank | Voice | Overall | Match | Pitch fit | F0 Hz | " + " | ".join(keys) + " |",
             "|" + "---|" * (6 + len(keys)),
             "| — | **reference** | — | — | — | " + f"{ref['f0_median_hz']:.0f} | "
             + " | ".join(f"{ref[k]:.2f}" for k in keys) + " |"]
    for i, r in enumerate(rows, 1):
        f = r["features"]
        lines.append(f"| {i} | {r['voice']} | **{r['overall']}** | {r['match']} | {r['pitch_fit']} | "
                     f"{f['f0_median_hz']:.0f} | " + " | ".join(f"{f[k]:.2f}" for k in keys) + " |")
    lines.append("\nColumns: " + "; ".join(f"`{k}` = {v['label']}" for k, v in FEATURES.items()))
    Path(args.out + ".md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
