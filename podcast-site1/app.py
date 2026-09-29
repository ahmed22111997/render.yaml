#!/usr/bin/env python3
"""
FastAPI website wrapper around podcast_tts.py.

Run locally:
    pip install -r requirements.txt
    uvicorn app:app --host 0.0.0.0 --port 8000
    open http://127.0.0.1:8000

Deploy: see DEPLOY.md
"""
import os
import shutil
import time
import traceback
import uuid

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import podcast_tts as ptts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
os.makedirs(JOBS_DIR, exist_ok=True)

app = FastAPI(title="Podcast TTS Studio")

# In-memory job store: {job_id: {status, message, files, error, created}}
# status: "queued" -> "running" -> "done" | "error"
JOBS: dict[str, dict] = {}

# Voice list is fetched once at startup (needs internet) and cached in memory.
VOICES: list[dict] = []


@app.on_event("startup")
def _load_voices():
    global VOICES
    VOICES = ptts.load_voices()


# --------------------------------------------------------------------------- models
class SpeakerIn(BaseModel):
    name: str
    voice: str
    rate: int = 0     # percent, e.g. -10
    pitch: int = 0    # Hz, e.g. -2


class GenerateIn(BaseModel):
    script: str
    speakers: list[SpeakerIn]
    turn_gap: float = 0.45
    max_chars: int = 600
    srt: bool = False
    jitter: bool = True
    emotion_strength: float = 1.0
    name: str = "episode"


# --------------------------------------------------------------------------- job worker
def _run_job(job_id: str, payload: GenerateIn):
    JOBS[job_id]["status"] = "running"
    try:
        slots = ptts.make_slots(
            [s.name for s in payload.speakers],
            [s.voice for s in payload.speakers],
            [s.rate for s in payload.speakers],
            [s.pitch for s in payload.speakers],
        )
        # pad to MAX_SPEAKERS so map_labels() never runs out of slots
        while len(slots) < ptts.MAX_SPEAKERS:
            slots.append({"name": f"Speaker {len(slots) + 1}", "voice": slots[0]["voice"],
                          "rate": "+0%", "pitch": "+0Hz"})

        job_dir = os.path.join(JOBS_DIR, job_id)
        files, status = ptts.generate(
            payload.script, slots,
            turn_gap=payload.turn_gap,
            max_chars=payload.max_chars,
            make_srt=payload.srt,
            jitter=0.1 if payload.jitter else 0.0,
            name=payload.name,
            emotion_strength=payload.emotion_strength,
            out_dir=job_dir,
        )
        JOBS[job_id].update(
            status="done",
            message=status,
            files=[os.path.basename(f) for f in files],
        )
    except Exception as e:
        JOBS[job_id].update(status="error", error=str(e))
        traceback.print_exc()


# --------------------------------------------------------------------------- API
@app.get("/api/voices")
def api_voices(lang: str = ""):
    if lang:
        return [v for v in VOICES if v["name"].lower().startswith(lang.lower())]
    return VOICES


@app.get("/api/defaults")
def api_defaults():
    return {
        "langs": ptts.LANGS,
        "default_names": ptts.DEFAULT_NAMES,
        "emotions": list(ptts.EMOTIONS.keys()),
        "example_script": ptts.EXAMPLE_SCRIPT,
        "max_speakers": ptts.MAX_SPEAKERS,
        "default_voices_en": ptts.default_voices(VOICES, "en"),
    }


@app.post("/api/generate")
def api_generate(payload: GenerateIn, background_tasks: BackgroundTasks):
    if not payload.script.strip():
        raise HTTPException(400, "Script is empty.")
    if not (1 <= len(payload.speakers) <= ptts.MAX_SPEAKERS):
        raise HTTPException(400, f"Provide between 1 and {ptts.MAX_SPEAKERS} speakers.")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "message": "", "files": [], "error": None, "created": time.time()}
    background_tasks.add_task(_run_job, job_id, payload)
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def api_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job id.")
    return job


@app.get("/api/download/{job_id}/{filename}")
def api_download(job_id: str, filename: str):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(404, "File not ready.")
    if filename not in job["files"]:
        raise HTTPException(404, "Unknown file.")
    path = os.path.join(JOBS_DIR, job_id, filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "File missing on disk.")
    media = "audio/mpeg" if filename.endswith(".mp3") else \
            "audio/wav" if filename.endswith(".wav") else "text/plain"
    return FileResponse(path, media_type=media, filename=filename)


# Housekeeping: delete job folders older than 2 hours so a free-tier disk doesn't fill up.
@app.on_event("startup")
def _cleanup_old_jobs():
    cutoff = time.time() - 2 * 3600
    if not os.path.isdir(JOBS_DIR):
        return
    for name in os.listdir(JOBS_DIR):
        p = os.path.join(JOBS_DIR, name)
        try:
            if os.path.isdir(p) and os.path.getmtime(p) < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


# --------------------------------------------------------------------------- frontend
STATIC_DIR = os.path.join(BASE_DIR, "static")
if not os.path.isdir(STATIC_DIR):
    # Guard against a deploy where static/index.html wasn't committed to git
    # (e.g. missing from the repo / excluded by .gitignore) — build a minimal
    # fallback instead of crashing the whole server on startup.
    os.makedirs(STATIC_DIR, exist_ok=True)
    with open(os.path.join(STATIC_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(
            "<!doctype html><html><body style='font-family:sans-serif;padding:40px'>"
            "<h2>static/index.html is missing from this deploy</h2>"
            "<p>The API is running (try <code>/api/voices</code>), but the real frontend "
            "file was not found on disk. Make sure <code>static/index.html</code> is "
            "committed to git and re-deploy.</p></body></html>"
        )

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
