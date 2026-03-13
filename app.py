"""
AutoTube Empire Engine — Production Flask App
- Reads config from .env (via python-dotenv)
- Voice: ElevenLabs → gTTS fallback (voice_gen.py)
- Tasks: async via Celery if Redis available, sync fallback
- Routes: /generate /upload /trends /analytics /batch /config /videos /status
"""

import os
import re
import shutil
import uuid
import subprocess
import traceback
import threading
import json
from pathlib import Path

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ── Load .env ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

import sys
sys.path.insert(0, str(Path(__file__).parent))

from ai_script     import generate_script
from subtitle_gen  import generate_srt, get_audio_duration
from thumbnail_gen import create_thumbnail
from voice_gen     import generate_voice
from batch         import load_topics, save_batch_results
from trend_finder  import get_trending_topics, suggest_video_idea
from analytics     import (record_video, get_dashboard_summary,
                            sync_analytics_to_local_db, update_video_stats)

# ── Dirs ───────────────────────────────────────────────────────────────────────
IMAGES_DIR    = BASE_DIR / "images"
AUDIO_DIR     = BASE_DIR / "audio"
VIDEOS_DIR    = BASE_DIR / "videos"
SUBTITLES_DIR = BASE_DIR / "subtitles"
THUMBS_DIR    = BASE_DIR / "thumbnails"
BATCH_DIR     = BASE_DIR / "batch"
for d in [IMAGES_DIR, AUDIO_DIR, VIDEOS_DIR, SUBTITLES_DIR, THUMBS_DIR, BATCH_DIR]:
    d.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = BASE_DIR / "config.json"

# ── App ────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# ── Config helpers ─────────────────────────────────────────────────────────────
def load_config() -> dict:
    """Merge .env values + config.json (config.json takes priority for API keys)."""
    base = {
        "anthropic_key":       os.getenv("ANTHROPIC_API_KEY", ""),
        "openai_key":          os.getenv("OPENAI_API_KEY", ""),
        "pexels_key":          os.getenv("PEXELS_API_KEY", ""),
        "elevenlabs_key":      os.getenv("ELEVENLABS_API_KEY", ""),
        "elevenlabs_voice_id": os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
        "voice_provider":      os.getenv("VOICE_PROVIDER", "gtts"),
        "use_ai_images":       os.getenv("USE_AI_IMAGES", "true").lower() == "true",
    }
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            base.update({k: v for k, v in saved.items() if v})
        except Exception:
            pass
    return base

def save_config(data: dict):
    existing = load_config()
    existing.update({k: v for k, v in data.items() if v})
    CONFIG_FILE.write_text(json.dumps(existing, indent=2))

def slugify(text, n=35):
    text = re.sub(r"[^\w\s-]", "", text.lower().strip())
    return re.sub(r"[\s_-]+", "_", text)[:n].strip("_") or "video"

def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except:
        return False


# ── Celery (optional) ──────────────────────────────────────────────────────────
def get_celery():
    try:
        from tasks import celery, celery_available
        if celery_available():
            return celery
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  CORE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
def run_pipeline(params: dict) -> dict:
    job_img_dir = None
    try:
        topic       = params.get("topic", "").strip()
        resolution  = params.get("resolution", "1920x1080")
        voice_speed = float(params.get("voice_speed", 1.0))
        img_count   = int(params.get("img_count", 6))
        job_id      = str(uuid.uuid4())[:8]
        slug        = slugify(topic)
        config      = load_config()

        print(f"\n{'='*55}\n  JOB {job_id} | {topic}\n{'='*55}")

        # 1 — Script
        script      = params.get("script", "").strip()
        title       = params.get("title",  "").strip()
        description = params.get("description", "").strip()
        tags        = params.get("tags", [])
        if not script:
            ai = generate_script(topic, config, duration_hint=60)
            script      = ai["script"]
            title       = title or ai["title"]
            description = description or ai["description"]
            tags        = tags or ai["tags"]
        else:
            title = title or topic
        if not script:
            raise ValueError("Script is empty")

        # 2 — Images
        provided = params.get("images", [])
        job_img_dir = IMAGES_DIR / job_id
        if provided:
            job_img_dir.mkdir(parents=True, exist_ok=True)
            image_paths = list(provided)
        else:
            image_paths = fetch_images(topic, job_img_dir, img_count, config, resolution)
        if not image_paths:
            raise ValueError("No images available")

        # 3 — Voice (ElevenLabs → gTTS)
        final_audio = generate_voice(script, AUDIO_DIR, job_id, config, voice_speed)

        # 4 — Smart duration
        audio_dur    = get_audio_duration(str(final_audio))
        req_dur      = int(params.get("img_duration", 0))
        img_duration = req_dur if req_dur > 0 else max(2, int(audio_dur / len(image_paths)))
        print(f"[{job_id}] Audio:{audio_dur:.1f}s | {len(image_paths)} imgs × {img_duration}s")

        # 5 — Subtitles
        srt_path = None
        if params.get("use_subtitles", True):
            srt_path = SUBTITLES_DIR / f"{slug}_{job_id}.srt"
            generate_srt(script, audio_dur, srt_path)

        # 6 — FFmpeg
        if not check_ffmpeg():
            raise RuntimeError("FFmpeg not found")

        video_filename = f"{slug}_{job_id}.mp4"
        video_path     = VIDEOS_DIR / video_filename
        width, height  = resolution.split("x")

        list_file = job_img_dir / f"{job_id}_list.txt"
        list_file.parent.mkdir(parents=True, exist_ok=True)
        with open(list_file, "w") as f:
            for p in image_paths:
                f.write(f"file '{p}'\n")
                f.write(f"duration {img_duration}\n")
            if image_paths:
                f.write(f"file '{image_paths[-1]}'\n")

        vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps=24")
        if srt_path and srt_path.exists():
            esc = str(srt_path).replace("\\","/").replace(":","\\:")
            vf += f",subtitles='{esc}':force_style='FontSize=22,PrimaryColour=&Hffffff,OutlineColour=&H000000,Outline=2,Bold=1'"

        cmd = ["ffmpeg","-y","-f","concat","-safe","0","-i",str(list_file),
               "-i",str(final_audio),"-vf",vf,"-c:v","libx264","-preset","slow",
               "-crf","18","-c:a","aac","-b:a","192k","-shortest",
               "-movflags","+faststart",str(video_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Retry without subtitles
            vf2 = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,fps=24")
            cmd2 = cmd[:-2] + ["-vf", vf2, str(video_path)]
            res2 = subprocess.run(cmd2, capture_output=True, text=True)
            if res2.returncode != 0:
                raise RuntimeError(f"FFmpeg: {res2.stderr[-250:]}")

        # 7 — Thumbnail
        thumb_path = None
        if params.get("use_thumbnail", True):
            tp = THUMBS_DIR / f"{slug}_{job_id}.png"
            try:
                create_thumbnail(title or topic, tp, background_image=image_paths[0] if image_paths else None)
                thumb_path = tp
            except Exception as te:
                print(f"[{job_id}] Thumb: {te}")

        result = {
            "success":        True,
            "job_id":         job_id,
            "topic":          topic,
            "title":          title,
            "script":         script,
            "description":    description,
            "tags":           tags,
            "audio_file":     final_audio.name,
            "video_file":     video_filename,
            "video_path":     str(video_path),
            "srt_path":       str(srt_path) if srt_path else None,
            "thumb_path":     str(thumb_path) if thumb_path else None,
            "images_used":    len(image_paths),
            "audio_duration": round(audio_dur, 1),
            "img_duration":   img_duration,
            "resolution":     resolution,
            "voice_provider": config.get("voice_provider", "gtts")
        }
        record_video(job_id, topic, title, str(video_path))
        return result

    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}
    finally:
        if job_img_dir and job_img_dir.exists():
            try: shutil.rmtree(job_img_dir)
            except: pass


def do_upload(data: dict) -> dict:
    """YouTube upload — shared by /upload route and Celery task."""
    video_path  = data.get("video_path")
    title       = data.get("title", "Auto Video")
    description = data.get("description", "")
    tags        = data.get("tags", [])
    privacy     = data.get("privacy", os.getenv("YOUTUBE_DEFAULT_PRIVACY","private"))
    thumb_path  = data.get("thumb_path")
    job_id      = data.get("job_id")

    if not video_path or not Path(video_path).exists():
        return {"error": "Video not found: " + str(video_path)}

    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return {"error": "Run: pip install google-auth google-auth-oauthlib google-api-python-client"}

    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    CS     = BASE_DIR / "client_secrets.json"
    TF     = BASE_DIR / "token.json"
    if not CS.exists():
        return {"error": "client_secrets.json not found"}

    creds = None
    if TF.exists():
        creds = Credentials.from_authorized_user_file(str(TF), SCOPES)
    if not creds or not creds.valid:
        flow  = InstalledAppFlow.from_client_secrets_file(str(CS), SCOPES)
        creds = flow.run_local_server(port=8080)
        TF.write_text(creds.to_json())

    yt   = build("youtube", "v3", credentials=creds)
    body = {
        "snippet": {"title": title, "description": description,
                    "tags": tags if isinstance(tags,list) else [t.strip() for t in tags.split(",")],
                    "categoryId": os.getenv("YOUTUBE_CATEGORY_ID", "22")},
        "status": {"privacyStatus": privacy}
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=2*1024*1024)
    req   = yt.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status: print(f"Upload: {int(status.progress()*100)}%")
    video_id = response["id"]
    if thumb_path and Path(thumb_path).exists():
        try:
            yt.thumbnails().set(videoId=video_id,
                media_body=MediaFileUpload(thumb_path, mimetype="image/png")).execute()
        except Exception as te:
            print(f"Thumb upload: {te}")
    if job_id:
        update_video_stats(job_id, {"youtube_id": video_id})
    return {"success": True, "video_id": video_id, "youtube_url": f"https://youtu.be/{video_id}"}


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/generate", methods=["POST"])
def generate():
    try:
        if request.content_type and "multipart" in request.content_type:
            params   = dict(request.form)
            uploaded = request.files.getlist("images")
            if uploaded:
                tid = str(uuid.uuid4())[:8]
                tmp = IMAGES_DIR / ("up_" + tid)
                tmp.mkdir(parents=True, exist_ok=True)
                paths = []
                for i, f in enumerate(uploaded):
                    ext = Path(f.filename).suffix.lower() or ".jpg"
                    p   = tmp / f"img_{i:04d}{ext}"; f.save(p); paths.append(str(p))
                params["images"] = paths; params["_tmp"] = str(tmp)
        else:
            params = request.get_json() or {}

        # Use Celery async if available
        celery = get_celery()
        if celery and params.get("async", False):
            from tasks import generate_video_task
            task = generate_video_task.delay(params)
            return jsonify({"task_id": task.id, "status": "queued"})

        result = run_pipeline(params)
        tmp = params.get("_tmp")
        if tmp and Path(tmp).exists():
            shutil.rmtree(tmp, ignore_errors=True)
        return jsonify(result), (200 if result.get("success") else 500)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/task/<task_id>", methods=["GET"])
def task_status(task_id):
    """Check async Celery task status."""
    try:
        from celery.result import AsyncResult
        from tasks import celery as cel
        r = AsyncResult(task_id, app=cel)
        return jsonify({"task_id": task_id, "state": r.state,
                        "result": r.result if r.ready() else None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/upload", methods=["POST"])
def upload():
    try:
        data   = request.get_json()
        result = do_upload(data)
        return jsonify(result), (200 if result.get("success") else 500)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/trends")
def trends():
    try:
        data = get_trending_topics(
            sources=request.args.get("sources","youtube,reddit,evergreen").split(","),
            region=request.args.get("region","US"),
            category=request.args.get("category","all"),
            max_results=20
        )
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/trends/suggest", methods=["POST"])
def trends_suggest():
    try:
        title = (request.get_json() or {}).get("title","")
        return jsonify(suggest_video_idea(title))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics/summary")
def analytics_summary():
    try: return jsonify(get_dashboard_summary())
    except Exception as e: return jsonify({"error": str(e)}), 500


@app.route("/analytics/sync", methods=["POST"])
def analytics_sync():
    try: return jsonify(sync_analytics_to_local_db())
    except Exception as e: return jsonify({"error": str(e)}), 500


batch_state = {"running": False, "jobs": [], "current": 0}

@app.route("/batch/start", methods=["POST"])
def batch_start():
    global batch_state
    if batch_state["running"]:
        return jsonify({"error": "Already running"}), 400
    data   = request.get_json() or {}
    topics = data.get("topics", []) or load_topics(BATCH_DIR / "topics.txt")
    if not topics: return jsonify({"error": "No topics"}), 400
    base   = {k: v for k, v in data.items() if k != "topics"}

    def run_batch():
        global batch_state
        batch_state = {"running": True, "jobs": [], "current": 0}
        results = []
        for i, topic in enumerate(topics):
            batch_state["current"] = i
            p = dict(base); p["topic"] = topic
            r = run_pipeline(p); r["topic"] = topic; r["batch_index"] = i
            results.append(r); batch_state["jobs"] = results
        save_batch_results(results, BATCH_DIR / "results.json")
        batch_state["running"] = False

    threading.Thread(target=run_batch, daemon=True).start()
    return jsonify({"started": True, "total": len(topics)})


@app.route("/batch/status")
def batch_status():
    return jsonify({"running": batch_state["running"],
                    "current": batch_state["current"],
                    "total": len(batch_state["jobs"]),
                    "jobs": batch_state["jobs"]})


@app.route("/config", methods=["GET","POST"])
def config_route():
    if request.method == "POST":
        save_config(request.get_json() or {})
        return jsonify({"saved": True})
    cfg    = load_config()
    masked = {k: ("***"+v[-4:] if isinstance(v,str) and len(v)>6 else v) for k,v in cfg.items()}
    return jsonify(masked)


@app.route("/videos")
def list_videos():
    files = sorted(VIDEOS_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    return jsonify([{"name": f.name, "path": str(f),
                     "size_mb": round(f.stat().st_size/1024/1024, 2)} for f in files[:50]])


@app.route("/status")
def status():
    try:
        from tasks import celery_available
        celery_ok = celery_available()
    except Exception:
        celery_ok = False
    cfg = load_config()
    return jsonify({
        "server":   "AutoTube Empire Engine",
        "version":  "final",
        "ffmpeg":   check_ffmpeg(),
        "celery":   celery_ok,
        "voice":    cfg.get("voice_provider", "gtts"),
        "ai_images": cfg.get("use_ai_images", True),
        "keys": {
            "anthropic": bool(cfg.get("anthropic_key")),
            "openai":    bool(cfg.get("openai_key")),
            "pexels":    bool(cfg.get("pexels_key")),
            "elevenlabs": bool(cfg.get("elevenlabs_key")),
        }
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print("="*55)
    print(f"  AutoTube Empire Engine — Production")
    print(f"  FFmpeg : {check_ffmpeg()}")
    print(f"  URL    : http://127.0.0.1:{port}")
    print("="*55)
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_ENV") != "production")
