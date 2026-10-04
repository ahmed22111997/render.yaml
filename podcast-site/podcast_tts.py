#!/usr/bin/env python3
"""
Personal Podcast / Text-to-Speech Studio  (edge-tts + Gradio)

Free, no GPU, no account. Uses the unofficial `edge-tts` package, which talks to
Microsoft's online neural voices, so it needs an internet connection.

Install:
    pip install edge-tts gradio imageio-ffmpeg numpy

Run the web UI:
    python podcast_tts.py                 # then open http://127.0.0.1:7860
    python podcast_tts.py --share         # public link (handy on Google Colab)

Run without UI (script file -> MP3/WAV/SRT):
    python podcast_tts.py episode.txt --names "Host,Guest" \
        --voices en-US-AndrewMultilingualNeural,en-US-AvaMultilingualNeural --srt

List voices:
    python podcast_tts.py --list-voices en      # or ar, ja, fr ...

Script format (podcast):
    Host: Welcome back to the show. Today we are talking about sleep.
    Guest: Thanks for having me. It is a strange story.
    (a line without a label continues the previous speaker)

  * Up to 4 speakers. Labels are matched to the speaker names you set
    ("Host", "Guest", ...), or to "Speaker 1" ... "Speaker 4".
  * Text in [square brackets] is ignored, so you can leave notes such as [laughs]
    or [music]. Markdown symbols (* # _ `) are stripped.

Emotions (delivery presets):
    Guest (excited): Thanks for having me!          <- applies to the whole turn
    Host: Sure. {pause 0.6} {sad} It was a hard year.  <- {tags} switch mid-turn
  Available: see the EMOTIONS table below. These change speed, pitch and volume only
  (edge-tts cannot do real "speaking styles"), so treat them as a starting point and
  tune the numbers by ear. {pause} / {pause 1.5} adds silence in seconds.
"""
import argparse
import asyncio
import os
import random
import re
import shutil
import subprocess
import sys
import time
import wave

import numpy as np
import edge_tts

SR = 24000                      # Edge voices return 24 kHz mono MP3
MAX_SPEAKERS = 4
OUT_DIR = os.path.join(os.getcwd(), "tts_output")

PREFERRED = {
    "en": ["en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural",
           "en-US-BrianMultilingualNeural", "en-US-EmmaMultilingualNeural",
           "en-US-GuyNeural", "en-US-JennyNeural", "en-US-ChristopherNeural", "en-US-AriaNeural"],
    "ar": ["ar-EG-ShakirNeural", "ar-EG-SalmaNeural", "ar-SA-HamedNeural", "ar-SA-ZariyahNeural"],
    "ja": ["ja-JP-KeitaNeural", "ja-JP-NanamiNeural"],
}
LANGS = {"English": "en", "Arabic": "ar", "Japanese": "ja", "French": "fr",
         "Spanish": "es", "German": "de", "All languages": ""}
DEFAULT_NAMES = ["Host", "Guest", "Speaker 3", "Speaker 4"]

# Emotion presets: (speed %, pitch Hz, volume %) ADDED to each speaker's own settings.
# Only delivery changes (prosody). Edit these numbers to taste; add your own rows freely.
EMOTIONS = {
    "neutral":    (0, 0, 0),
    "happy":      (8, 3, 5),
    "excited":    (14, 5, 8),
    "curious":    (4, 4, 0),
    "surprised":  (10, 7, 8),
    "serious":    (-8, -3, 3),
    "thoughtful": (-12, -2, -4),
    "calm":       (-10, -1, -6),
    "sad":        (-16, -5, -10),
}
EMOTION_ALIASES = {"enthusiastic": "excited", "cheerful": "happy", "shocked": "surprised",
                   "gentle": "calm", "reflective": "thoughtful"}

EXAMPLE_SCRIPT = """Host: Welcome back to the show. Today we are talking about why we sleep.
Guest (excited): Thanks for having me! It is honestly a stranger story than most people expect.
Host (curious): Let's start with the basics. What actually happens to the body during sleep?
Guest: Quite a lot. {pause 0.6} {thoughtful} Your brain replays the day, clears waste, and files memories away.
"""


# --------------------------------------------------------------------------- ffmpeg
def find_ffmpeg():
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("ffmpeg was not found. Install it or run:  pip install imageio-ffmpeg")


def mp3_to_pcm(mp3, ffmpeg):
    r = subprocess.run([ffmpeg, "-loglevel", "error", "-i", "pipe:0", "-f", "s16le",
                        "-ar", str(SR), "-ac", "1", "pipe:1"],
                       input=mp3, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg could not decode a chunk: " + r.stderr.decode(errors="ignore")[:300])
    return np.frombuffer(r.stdout, dtype=np.int16)


def trim_silence(a, thr=250, keep=0.05):
    """Cut leading/trailing silence so the pause between turns is exactly what you set."""
    idx = np.flatnonzero(np.abs(a) > thr)
    if idx.size == 0:
        return a
    s = max(0, idx[0] - int(SR * keep))
    e = min(len(a), idx[-1] + int(SR * keep))
    return a[s:e]


# --------------------------------------------------------------------------- voices
FALLBACK_VOICES = [
    {"name": n, "gender": g, "locale": n[:5]} for n, g in [
        ("en-US-AndrewMultilingualNeural", "Male"), ("en-US-AvaMultilingualNeural", "Female"),
        ("en-US-BrianMultilingualNeural", "Male"), ("en-US-EmmaMultilingualNeural", "Female"),
        ("en-US-GuyNeural", "Male"), ("en-US-JennyNeural", "Female"),
        ("ar-EG-ShakirNeural", "Male"), ("ar-EG-SalmaNeural", "Female"),
        ("ja-JP-KeitaNeural", "Male"), ("ja-JP-NanamiNeural", "Female")]
]


def load_voices():
    try:
        vs = asyncio.run(edge_tts.list_voices())
        out = [{"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]} for v in vs]
        return sorted(out, key=lambda v: v["name"])
    except Exception as e:
        print(f"[warn] Could not fetch the voice list ({e}). Using a short built-in list.")
        return FALLBACK_VOICES


def default_voices(voices, prefix, n=MAX_SPEAKERS):
    """Pick n different-sounding defaults for a language, alternating male/female when possible."""
    names = [v["name"] for v in voices if v["name"].lower().startswith(prefix.lower())] if prefix else \
            [v["name"] for v in voices]
    picked = [p for p in PREFERRED.get(prefix, []) if p in names]
    picked += [x for x in names if x not in picked]
    return (picked or [v["name"] for v in voices])[:n] + [picked[0] if picked else voices[0]["name"]] * n


# --------------------------------------------------------------------------- text
def clean_text(text):
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"\[[^\]]*\]", " ", text)          # [stage directions] are not spoken
    text = re.sub(r"[*_#`~^|<>]+", " ", text)         # markdown symbols
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r" ([.,!?;:])", r"\1", text).strip()


_LABEL = re.compile(r"^\s*([^\W\d_][\w \-]{0,29}?)\s*(?:\(([^()]{1,30})\))?\s*[:\uff1a]\s*(.*)$", re.UNICODE)


def parse_script(text):
    """-> [[label or None, emotion or None, text], ...]. Lines without a label continue the previous turn."""
    turns = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LABEL.match(line)
        if m and not re.match(r"^\s*https?$", m.group(1), re.I):
            turns.append([m.group(1).strip(), m.group(2), m.group(3)])
        elif turns:
            turns[-1][2] += "\n" + line
        else:
            turns.append([None, None, line])
    return turns


def resolve_emotion(name):
    key = re.sub(r"\s+", " ", (name or "neutral").strip().lower())
    key = EMOTION_ALIASES.get(key, key)
    if key not in EMOTIONS:
        raise ValueError(f"Unknown emotion '{name}'. Available: {', '.join(EMOTIONS)} (or {{pause 1.5}})")
    return key


def map_labels(turns, slot_names):
    """-> [(slot 0-3, emotion, text)]. Named slots first, then unknown labels in order."""
    norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
    names = [norm(n) for n in slot_names]
    mapping = {}
    labels = []
    for lab, _, _ in turns:
        if lab is not None and norm(lab) not in labels:
            labels.append(norm(lab))
    for lab in labels:
        for i, n in enumerate(names):
            if n and (lab == n or lab == f"speaker {i + 1}"):
                mapping[lab] = i
                break
    for lab in labels:
        if lab not in mapping:
            free = [i for i in range(MAX_SPEAKERS) if i not in mapping.values()]
            if not free:
                raise ValueError(f"More than {MAX_SPEAKERS} different speaker labels: {', '.join(labels)}")
            mapping[lab] = free[0]
    return [(mapping[norm(lab)] if lab is not None else 0, resolve_emotion(emo), t) for lab, emo, t in turns]


_TAG = re.compile(r"\{([^{}]{1,30})\}")


def split_directives(text, emotion):
    """Split a turn into ('say', emotion, text) and ('pause', seconds, None) items.
    {excited} changes the emotion from that point on; {pause} / {pause 1.5} adds silence."""
    items, pos = [], 0

    def add_text(t):
        if t.strip():
            items.append(("say", emotion, t))

    for m in _TAG.finditer(text):
        add_text(text[pos:m.start()])
        pos = m.end()
        tag = m.group(1).strip().lower()
        pm = re.fullmatch(r"pause(?:\s+(\d+(?:\.\d+)?))?\s*s?", tag)
        if pm:
            items.append(("pause", min(float(pm.group(1) or 0.8), 10.0), None))
        else:
            emotion = resolve_emotion(tag)
    add_text(text[pos:])
    return items


def _num(s):
    return int(re.match(r"[+-]?\d+", s).group())


def voice_params(slot, emotion, strength=1.0):
    """Speaker settings + emotion preset (scaled by strength) -> edge-tts rate, pitch, volume strings."""
    dr, dp, dv = EMOTIONS[emotion]
    rate = max(-60, min(80, _num(slot["rate"]) + round(dr * strength)))
    pitch = max(-40, min(40, _num(slot["pitch"]) + round(dp * strength)))
    vol = max(-60, min(30, _num(slot.get("volume", "+0%")) + round(dv * strength)))
    return f"{rate:+d}%", f"{pitch:+d}Hz", f"{vol:+d}%"


def _fit(sentence, n):
    if len(sentence) <= n:
        return [sentence]
    out = []
    for piece in re.split(r"(?<=[,;:\u060c\u061b])\s+", sentence):
        if len(piece) <= n:
            out.append(piece)
            continue
        cur = ""
        for w in piece.split():
            if cur and len(cur) + 1 + len(w) > n:
                out.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            out.append(cur)
    return out


def split_chunks(text, max_chars):
    chunks = []
    for para in [p.strip() for p in text.split("\n") if p.strip()]:
        cur = ""
        for sent in re.split(r"(?<=[.!?\u2026\u061f\u3002])\s+", para):
            for part in _fit(sent, max_chars):
                if cur and len(cur) + 1 + len(part) > max_chars:
                    chunks.append(cur)
                    cur = part
                else:
                    cur = f"{cur} {part}".strip()
        if cur:
            chunks.append(cur)
    return [c for c in chunks if re.search(r"\w", c)]


# --------------------------------------------------------------------------- synthesis
async def tts_bytes(text, voice, rate, pitch, volume, retries=3):
    last = None
    for attempt in range(retries):
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf += chunk["data"]
            if buf:
                return bytes(buf)
            last = RuntimeError("empty audio")
        except Exception as e:
            last = e
        await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"TTS failed for voice {voice} after {retries} attempts: {last}")


async def _run_jobs(jobs, slots, ffmpeg, progress, strength=1.0):
    jobs = [j for j in jobs if "pause" not in j]
    sem = asyncio.Semaphore(4)
    done = 0

    async def one(job):
        nonlocal done
        s = slots[job["slot"]]
        async with sem:
            rate, pitch, vol = voice_params(s, job["emotion"], strength)
            mp3 = await tts_bytes(job["text"], s["voice"], rate, pitch, vol)
            job["pcm"] = trim_silence(await asyncio.to_thread(mp3_to_pcm, mp3, ffmpeg))
        done += 1
        if progress:
            progress((done, len(jobs)), desc=f"Generated {done}/{len(jobs)} chunks")

    await asyncio.gather(*(one(j) for j in jobs))


def srt_time(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def assemble(jobs, turn_gap, jitter, chunk_gap=0.15):
    pieces, cues, total = [], [], 0
    first, after_pause = True, False
    for j in jobs:
        if "pause" in j:                      # explicit {pause}: silence, no extra gap around it
            sil = np.zeros(int(SR * j["pause"]), dtype=np.int16)
            pieces.append(sil)
            total += len(sil)
            after_pause = True
            continue
        if not first and not after_pause:
            gap = turn_gap + random.uniform(-jitter, jitter) if j["new_turn"] else chunk_gap
            sil = np.zeros(int(SR * max(0.08, gap)), dtype=np.int16)
            pieces.append(sil)
            total += len(sil)
        first = after_pause = False
        start = total / SR
        pieces.append(j["pcm"])
        total += len(j["pcm"])
        cues.append((start, total / SR, j["text"]))
    return np.concatenate(pieces), cues


def generate(script, slots, turn_gap=0.45, max_chars=600, make_srt=False, jitter=0.1,
             name="episode", progress=None, emotion_strength=1.0, out_dir=None):
    """slots: list of 4 dicts {name, voice, rate '+0%', pitch '+0Hz'}. Returns (files, status)."""
    ffmpeg = find_ffmpeg()
    turns = map_labels(parse_script(script), [s["name"] for s in slots])
    jobs = []
    for slot, emo, text in turns:
        first = True
        for kind, val, t in split_directives(text, emo):
            if kind == "pause":
                jobs.append({"pause": val})
                continue
            for c in split_chunks(clean_text(t), 140 if make_srt else max_chars):
                jobs.append({"slot": slot, "text": c, "emotion": val, "new_turn": first})
                first = False
    speech = [j for j in jobs if "pause" not in j]
    if not speech:
        raise ValueError("Nothing to read: the script is empty.")

    asyncio.run(_run_jobs(jobs, slots, ffmpeg, progress, emotion_strength))
    audio, cues = assemble(jobs, turn_gap, jitter)

    target_dir = out_dir or OUT_DIR
    os.makedirs(target_dir, exist_ok=True)
    base = os.path.join(target_dir, f"{name}_{time.strftime('%Y%m%d_%H%M%S')}")
    with wave.open(base + ".wav", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(audio.tobytes())
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1",
                    "-i", "pipe:0", "-b:a", "192k", base + ".mp3"], input=audio.tobytes(), check=True)
    files = [base + ".mp3", base + ".wav"]
    if make_srt:
        with open(base + ".srt", "w", encoding="utf-8") as f:
            for n, (a, b, t) in enumerate(cues, 1):
                f.write(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n\n")
        files.append(base + ".srt")
    used = sorted({j["slot"] for j in speech})
    emos = sorted({j["emotion"] for j in speech} - {"neutral"})
    status = (f"Done: {len(speech)} chunks, {len(audio) / SR / 60:.1f} min, speakers used: "
              + ", ".join(slots[i]["name"] for i in used)
              + (f", emotions used: {', '.join(emos)}" if emos else ""))
    return files, status


# --------------------------------------------------------------------------- UI
def make_slots(names, voices, rates, pitches):
    return [{"name": (n or "").strip(), "voice": v, "rate": f"{int(r):+d}%", "pitch": f"{int(p):+d}Hz"}
            for n, v, r, p in zip(names, voices, rates, pitches)]


def build_ui(voices):
    import gradio as gr

    def choices_for(prefix):
        vs = [v for v in voices if v["name"].lower().startswith(prefix.lower())] if prefix else voices
        return [(f"{v['name']} ({v['gender']})", v["name"]) for v in (vs or voices)]

    init = default_voices(voices, "en")

    def on_lang(lang):
        prefix = LANGS[lang]
        ch, dv = choices_for(prefix), default_voices(voices, prefix)
        return [gr.Dropdown(choices=ch, value=dv[i]) for i in range(MAX_SPEAKERS)]

    def on_generate(script, n1, v1, r1, p1, n2, v2, r2, p2, n3, v3, r3, p3, n4, v4, r4, p4,
                    gap, max_chars, srt, jitter, strength, progress=gr.Progress()):
        if not script or not script.strip():
            return None, None, "Please enter a script."
        try:
            slots = make_slots([n1, n2, n3, n4], [v1, v2, v3, v4], [r1, r2, r3, r4], [p1, p2, p3, p4])
            files, status = generate(script, slots, gap, int(max_chars), srt, 0.1 if jitter else 0.0,
                                     progress=progress, emotion_strength=float(strength))
            return files[0], files, status
        except Exception as e:
            return None, None, f"Error: {e}"

    with gr.Blocks(title="Podcast TTS Studio") as demo:
        gr.Markdown("## Personal Podcast / TTS Studio\n"
                    "Write the script as `Host: ...` / `Guest: ...` (up to 4 speakers). "
                    "Labels are matched to the speaker names below. `[notes in brackets]` are not spoken.\n\n"
                    "**Emotions:** `Guest (excited): ...` for a whole turn, or `{sad}` inside a turn to switch. "
                    f"Available: {', '.join(EMOTIONS)}. `{{pause 1.5}}` adds silence. "
                    "Emotions only change speed / pitch / volume, so tune them by ear.")
        script = gr.Textbox(label="Script", lines=14, value=EXAMPLE_SCRIPT)
        lang = gr.Dropdown(list(LANGS), value="English", label="Voice language filter")
        names, vdrops, rates, pitches = [], [], [], []
        with gr.Row():
            for i in range(MAX_SPEAKERS):
                with gr.Column():
                    names.append(gr.Textbox(value=DEFAULT_NAMES[i], label=f"Speaker {i + 1} name"))
                    vdrops.append(gr.Dropdown(choices_for("en"), value=init[i], label="Voice"))
                    rates.append(gr.Slider(-40, 40, value=0, step=5, label="Speed (%)"))
                    pitches.append(gr.Slider(-20, 20, value=0, step=1, label="Pitch (Hz)"))
        with gr.Row():
            gap = gr.Slider(0.1, 1.5, value=0.45, step=0.05, label="Pause between turns (s)")
            max_chars = gr.Slider(200, 1500, value=600, step=50, label="Max characters per request")
        with gr.Row():
            srt = gr.Checkbox(value=False, label="Also create subtitles (.srt)")
            jitter = gr.Checkbox(value=True, label="Natural pause variation")
            strength = gr.Slider(0.0, 2.0, value=1.0, step=0.1, label="Emotion strength (0 = off)")
        btn = gr.Button("Generate episode", variant="primary")
        audio = gr.Audio(label="Result", type="filepath")
        files = gr.File(label="Downloads (MP3 / WAV / SRT)", file_count="multiple")
        status = gr.Markdown()

        lang.change(on_lang, lang, vdrops)
        inputs = [script]
        for i in range(MAX_SPEAKERS):
            inputs += [names[i], vdrops[i], rates[i], pitches[i]]
        inputs += [gap, max_chars, srt, jitter, strength]
        btn.click(on_generate, inputs, [audio, files, status])
    return demo


# --------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="Personal podcast TTS studio (edge-tts)")
    ap.add_argument("script", nargs="?", help="text file to convert; omit to launch the web UI")
    ap.add_argument("--names", default=",".join(DEFAULT_NAMES[:2]), help='speaker names, e.g. "Host,Guest"')
    ap.add_argument("--voices", default="", help="comma-separated voice names, one per speaker")
    ap.add_argument("--rate", type=int, default=0, help="speed in percent, e.g. -10")
    ap.add_argument("--pitch", type=int, default=0, help="pitch in Hz, e.g. -2")
    ap.add_argument("--gap", type=float, default=0.45, help="pause between turns in seconds")
    ap.add_argument("--srt", action="store_true", help="also write subtitles")
    ap.add_argument("--emotion-strength", type=float, default=1.0, help="0 turns emotions off, 2 doubles them")
    ap.add_argument("--out", default="episode", help="output file name prefix")
    ap.add_argument("--share", action="store_true", help="public Gradio link (web UI)")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--list-voices", nargs="?", const="", metavar="PREFIX",
                    help="print available voices (optionally filtered, e.g. en, ar, ja) and exit")
    args = ap.parse_args()

    voices = load_voices()
    if args.list_voices is not None:
        for v in voices:
            if v["name"].lower().startswith(args.list_voices.lower()):
                print(f"{v['name']:40s} {v['gender']}")
        return

    if not args.script:
        build_ui(voices).queue().launch(share=args.share, server_port=args.port)
        return

    with open(args.script, encoding="utf-8") as f:
        text = f.read()
    names = [n.strip() for n in args.names.split(",")] + DEFAULT_NAMES[len(args.names.split(",")):]
    chosen = [v.strip() for v in args.voices.split(",") if v.strip()]
    dv = default_voices(voices, "en")
    chosen += dv[len(chosen):MAX_SPEAKERS]
    slots = make_slots(names[:MAX_SPEAKERS], chosen[:MAX_SPEAKERS], [args.rate] * 4, [args.pitch] * 4)
    files, status = generate(text, slots, args.gap, 600, args.srt, 0.1, name=args.out,
                             progress=lambda p, desc="": print(desc, end="\r"),
                             emotion_strength=args.emotion_strength)
    print("\n" + status)
    for f in files:
        print(" ", f)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as e:
        sys.exit(f"Error: {e}")
