"""
AutoTube Final — Celery Task Scheduler
Enables async video generation + scheduled auto-publishing.

Requirements:
  pip install celery redis

Start worker:
  celery -A tasks worker --loglevel=info

Start scheduler (for cron jobs):
  celery -A tasks beat --loglevel=info
"""

import os
import sys
from pathlib import Path

# ── Setup path ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from celery import Celery
from celery.schedules import crontab
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Celery config ──────────────────────────────────────────────────────────────
BROKER_URL  = os.getenv("CELERY_BROKER_URL",  "redis://localhost:6379/0")
RESULT_BACK = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery = Celery("autotube", broker=BROKER_URL, backend=RESULT_BACK)

celery.conf.update(
    task_serializer    = "json",
    result_serializer  = "json",
    accept_content     = ["json"],
    timezone           = "UTC",
    enable_utc         = True,
    task_track_started = True,
    worker_prefetch_multiplier = 1,   # one video at a time — FFmpeg is CPU heavy
    task_acks_late     = True,
)


# ══════════════════════════════════════════════════════════════════════════════
#  TASKS
# ══════════════════════════════════════════════════════════════════════════════

@celery.task(bind=True, name="autotube.generate_video", max_retries=2)
def generate_video_task(self, params: dict) -> dict:
    """
    Async video generation task.
    Called by /generate route when Celery is available.
    Falls back to synchronous if Celery unavailable.
    """
    try:
        from app import run_pipeline
        self.update_state(state="PROGRESS", meta={"step": "starting"})
        result = run_pipeline(params)
        return result
    except Exception as exc:
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise self.retry(exc=exc, countdown=10)


@celery.task(bind=True, name="autotube.upload_video")
def upload_video_task(self, upload_params: dict) -> dict:
    """Async YouTube upload task."""
    try:
        from app import do_upload
        self.update_state(state="PROGRESS", meta={"step": "uploading"})
        result = do_upload(upload_params)
        return result
    except Exception as exc:
        self.update_state(state="FAILURE", meta={"error": str(exc)})
        raise


@celery.task(name="autotube.auto_generate_scheduled")
def auto_generate_scheduled() -> dict:
    """
    Scheduled task: reads next topic from topics.txt and generates a video.
    Runs on the cron defined in BEAT_SCHEDULE below.
    """
    try:
        from batch import load_topics
        from analytics import get_dashboard_summary
        from app import run_pipeline, load_config

        BASE_DIR    = Path(__file__).parent.parent
        topics_file = BASE_DIR / "batch" / "topics.txt"
        done_file   = BASE_DIR / "batch" / "done.txt"

        topics = load_topics(topics_file)
        if not topics:
            print("[Scheduler] No topics left in topics.txt")
            return {"skipped": True, "reason": "No topics"}

        # Track which topics are done
        done = set()
        if done_file.exists():
            done = set(done_file.read_text().splitlines())

        remaining = [t for t in topics if t not in done]
        if not remaining:
            print("[Scheduler] All topics processed. Reset batch/done.txt to restart.")
            return {"skipped": True, "reason": "All topics done"}

        topic  = remaining[0]
        config = load_config()

        print(f"[Scheduler] Auto-generating: {topic}")
        result = run_pipeline({
            "topic":          topic,
            "resolution":     "1280x720",
            "privacy":        os.getenv("YOUTUBE_DEFAULT_PRIVACY", "private"),
            "use_subtitles":  True,
            "use_thumbnail":  True,
            "img_count":      6,
            **config
        })

        # Mark as done
        with open(done_file, "a") as f:
            f.write(topic + "\n")

        if result.get("success") and os.getenv("AUTO_UPLOAD_ENABLED", "false").lower() == "true":
            upload_video_task.delay({
                "video_path":  result["video_path"],
                "title":       result["title"],
                "description": result["description"],
                "tags":        result["tags"],
                "privacy":     os.getenv("YOUTUBE_DEFAULT_PRIVACY", "private"),
                "thumb_path":  result.get("thumb_path"),
                "job_id":      result["job_id"]
            })

        return result

    except Exception as e:
        print(f"[Scheduler] Error: {e}")
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
#  BEAT SCHEDULE (cron jobs)
# ══════════════════════════════════════════════════════════════════════════════
AUTO_ENABLED = os.getenv("AUTO_SCHEDULE_ENABLED", "false").lower() == "true"
CRON_EXP     = os.getenv("AUTO_SCHEDULE_CRON", "0 9 * * *")   # default: 9am daily

if AUTO_ENABLED:
    # Parse "0 9 * * *" → crontab(minute=0, hour=9)
    parts = CRON_EXP.split()
    if len(parts) == 5:
        minute, hour = parts[0], parts[1]
        celery.conf.beat_schedule = {
            "auto-generate-video": {
                "task":     "autotube.auto_generate_scheduled",
                "schedule": crontab(minute=minute, hour=hour),
            }
        }
        print(f"[Scheduler] Beat schedule active: {CRON_EXP}")


# ── Celery availability check (used by app.py) ─────────────────────────────────
def celery_available() -> bool:
    """Check if Redis/Celery is reachable."""
    try:
        celery.control.inspect(timeout=1).ping()
        return True
    except Exception:
        return False
