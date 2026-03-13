"""
AutoTube Final — Voice Generator
Supports: ElevenLabs (premium) | gTTS (free) | auto-fallback
"""

import os
import subprocess
from pathlib import Path


# ── ElevenLabs ────────────────────────────────────────────────────────────────
def generate_elevenlabs(script: str, output_path: Path, api_key: str,
                         voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> bool:
    """
    Generate voice using ElevenLabs API.
    Default voice: Rachel (voice_id = 21m00Tcm4TlvDq8ikWAM)
    Returns True if successful.
    """
    try:
        import requests
        url     = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "Accept":       "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key":   api_key
        }
        payload = {
            "text":  script,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability":        0.5,
                "similarity_boost": 0.75
            }
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code == 200:
            output_path.write_bytes(resp.content)
            size_kb = len(resp.content) // 1024
            print(f"[Voice] ElevenLabs: {output_path.name} ({size_kb}KB)")
            return True
        else:
            print(f"[Voice] ElevenLabs error {resp.status_code}: {resp.text[:100]}")
            return False
    except ImportError:
        print("[Voice] requests not installed: pip install requests")
        return False
    except Exception as e:
        print(f"[Voice] ElevenLabs failed: {e}")
        return False


# ── gTTS ──────────────────────────────────────────────────────────────────────
def generate_gtts(script: str, output_path: Path, lang: str = "en") -> bool:
    """Generate voice using gTTS (free, Google Text-to-Speech)."""
    try:
        from gtts import gTTS
        gTTS(text=script, lang=lang, slow=False).save(str(output_path))
        print(f"[Voice] gTTS: {output_path.name}")
        return True
    except ImportError:
        print("[Voice] gTTS not installed: pip install gtts")
        return False
    except Exception as e:
        print(f"[Voice] gTTS failed: {e}")
        return False


# ── Apply speed with FFmpeg atempo ────────────────────────────────────────────
def _atempo_chain(speed: float) -> str:
    speed = round(max(0.5, min(3.0, speed)), 2)
    f = []
    while speed > 2.0: f.append("atempo=2.0"); speed /= 2.0
    while speed < 0.5: f.append("atempo=0.5"); speed /= 0.5
    f.append(f"atempo={speed:.2f}")
    return ",".join(f)


def apply_speed(input_path: Path, output_path: Path, speed: float) -> bool:
    """Apply voice speed using FFmpeg atempo filter."""
    if abs(speed - 1.0) < 0.05:
        input_path.rename(output_path)
        return True
    try:
        res = subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path),
             "-filter:a", _atempo_chain(speed),
             "-c:a", "libmp3lame", "-b:a", "128k", str(output_path)],
            capture_output=True, text=True
        )
        if res.returncode == 0:
            input_path.unlink(missing_ok=True)
            return True
        else:
            print(f"[Voice] Speed apply failed: {res.stderr[-100:]}")
            input_path.rename(output_path)
            return False
    except Exception as e:
        print(f"[Voice] Speed error: {e}")
        input_path.rename(output_path)
        return False


# ── Main Entry ────────────────────────────────────────────────────────────────
def generate_voice(script: str, audio_dir: Path, job_id: str,
                   config: dict, voice_speed: float = 1.0) -> Path:
    """
    Generate voice narration.
    Tries: ElevenLabs → gTTS → raises RuntimeError

    config keys:
      voice_provider: 'gtts' | 'elevenlabs'
      elevenlabs_key: str
      elevenlabs_voice_id: str
    
    Returns: Path to final audio file
    """
    provider    = config.get("voice_provider",
                             os.getenv("VOICE_PROVIDER", "gtts")).lower()
    el_key      = config.get("elevenlabs_key",
                             os.getenv("ELEVENLABS_API_KEY", "")).strip()
    el_voice_id = config.get("elevenlabs_voice_id",
                             os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")).strip()

    raw_path   = audio_dir / f"{job_id}_raw.mp3"
    final_path = audio_dir / f"{job_id}_narration.mp3"
    success    = False

    # Try ElevenLabs first if configured
    if provider == "elevenlabs" and el_key:
        success = generate_elevenlabs(script, raw_path, el_key, el_voice_id)
        if not success:
            print("[Voice] ElevenLabs failed — falling back to gTTS")

    # gTTS (always available)
    if not success:
        success = generate_gtts(script, raw_path)

    if not success or not raw_path.exists():
        raise RuntimeError("All voice providers failed. Check gTTS install: pip install gtts")

    # Apply speed
    apply_speed(raw_path, final_path, round(voice_speed, 2))

    if not final_path.exists():
        raise RuntimeError("Audio file not found after speed processing")

    return final_path
