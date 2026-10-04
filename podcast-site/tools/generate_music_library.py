#!/usr/bin/env python3
"""
Generates a tiny royalty-free background-music library for Podcast TTS Studio.

Every track here is 100% procedurally synthesized (sums of sine tones with a
slow amplitude LFO) — nothing sampled or copied from anywhere, so there are
no licensing concerns. They're meant as functional placeholders: simple,
seamless-looping ambient pads. Swap in nicer real tracks any time by adding
an MP3 to static/music/ and a matching entry to static/music/manifest.json —
no code changes needed.

Run once during development:
    python tools/generate_music_library.py
"""
import json
import os
import subprocess
import wave

import numpy as np


def find_ffmpeg():
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        return path
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

SR = 24000
LOOP_SECONDS = 40.0
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "music")

# id -> (display name, mood blurb, [(freq_hz, weight), ...], lfo_period_s, lfo_depth)
TRACKS = {
    "calm-focus": (
        "Calm Focus", "Soft, steady pad — explainer / educational narration",
        [(130.81, 1.0), (196.00, 0.55), (261.63, 0.28)], 10.0, 0.12,
    ),
    "warm-curiosity": (
        "Warm Curiosity", "Light, inquisitive pad — trivia / discovery content",
        [(146.83, 1.0), (220.00, 0.6), (293.66, 0.4), (349.23, 0.22)], 8.0, 0.16,
    ),
    "gentle-mystery": (
        "Gentle Mystery", "Low, slow-moving drone — curiosity / mystery channels",
        [(110.00, 1.0), (164.81, 0.5), (207.65, 0.32)], 13.33, 0.20,
    ),
    "friendly-chat": (
        "Friendly Chat", "Bright, easygoing pad — conversational / interview podcasts",
        [(164.81, 1.0), (196.00, 0.5), (246.94, 0.35), (329.63, 0.2)], 10.0, 0.10,
    ),
}


def render(freqs_weights, lfo_period, lfo_depth):
    n = int(SR * LOOP_SECONDS)
    t = np.arange(n) / SR
    sig = np.zeros(n)
    for freq, weight in freqs_weights:
        # snap to a frequency whose period divides the loop length evenly,
        # so the waveform is perfectly continuous at the loop point
        k = round(freq * LOOP_SECONDS)
        f = k / LOOP_SECONDS
        sig += weight * np.sin(2 * np.pi * f * t)
    k_lfo = round(LOOP_SECONDS / lfo_period)
    lfo_f = k_lfo / LOOP_SECONDS
    lfo = 1.0 + lfo_depth * np.sin(2 * np.pi * lfo_f * t)
    sig *= lfo
    sig /= np.max(np.abs(sig)) * 1.4  # headroom
    return sig


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    ffmpeg = find_ffmpeg()
    manifest = []
    for tid, (name, mood, freqs, lfo_period, lfo_depth) in TRACKS.items():
        sig = render(freqs, lfo_period, lfo_depth)
        pcm = (sig * 32767 * 0.5).astype(np.int16)
        wav_path = f"/tmp/{tid}.wav"
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        mp3_path = os.path.join(OUT_DIR, f"{tid}.mp3")
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", wav_path, "-b:a", "128k", mp3_path], check=True)
        os.remove(wav_path)
        manifest.append({"id": tid, "name": name, "mood": mood, "file": f"{tid}.mp3",
                         "license": "Original synthesized track, generated for this project — free to use."})
        print("built", mp3_path)

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print("wrote manifest with", len(manifest), "tracks")


if __name__ == "__main__":
    main()
