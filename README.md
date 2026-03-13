# AutoTube Empire Engine — Final Production System

## Quick Start (Local)
```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python backend/app.py
# open frontend/index.html
```

## Deploy Backend → Render.com
1. Push repo to GitHub
2. render.com → New → Blueprint → connect repo
3. Add API keys in Render dashboard environment tab
4. Your API: https://autotube-backend.onrender.com

## Deploy Frontend → Vercel.com
1. vercel.com → Import GitHub repo
2. Update index.html: const API = 'https://autotube-backend.onrender.com';
3. Your dashboard: https://autotube-dashboard.vercel.app

## Enable Auto-Scheduler (Celery)
```bash
# Start Redis, then:
celery -A backend.tasks worker --loglevel=info
celery -A backend.tasks beat --loglevel=info
# Set AUTO_SCHEDULE_ENABLED=true in .env
```

## Full Pipeline
Topic → AI Script → Pollinations Images → ElevenLabs/gTTS Voice
→ SRT Subtitles → FFmpeg MP4 → Thumbnail → YouTube Upload → Analytics

## Files Added in Final Build
- voice_gen.py   — ElevenLabs + gTTS fallback
- tasks.py       — Celery async + cron scheduler
- .env.example   — all config keys documented
- Dockerfile     — cloud container with FFmpeg
- render.yaml    — Render backend deployment
- vercel.json    — Vercel frontend deployment
- app.py         — production-ready, reads .env

## API Keys Needed
- Anthropic / OpenAI — AI script generation
- Pexels — real photos (Pollinations AI is free, no key)
- ElevenLabs — professional voice (gTTS is free fallback)
- Google Cloud — YouTube upload (free, OAuth2)
