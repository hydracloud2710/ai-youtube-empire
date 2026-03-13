"""
AutoTube v2 — Subtitle Generator
Converts a script into a timed .srt file based on estimated word timing.
No external API needed — pure Python.
"""

import re
import math
from pathlib import Path


def seconds_to_srt_time(seconds: float) -> str:
    """Convert 90.5 → '00:01:30,500'"""
    ms  = int((seconds % 1) * 1000)
    sec = int(seconds) % 60
    mn  = int(seconds // 60) % 60
    hr  = int(seconds // 3600)
    return f"{hr:02d}:{mn:02d}:{sec:02d},{ms:03d}"


def split_into_chunks(text: str, words_per_chunk: int = 8) -> list:
    """Split script into subtitle chunks of ~N words each."""
    # Clean up whitespace
    text   = re.sub(r"\s+", " ", text).strip()
    words  = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = words[i : i + words_per_chunk]
        # Don't break in the middle of a sentence if possible
        chunk_text = " ".join(chunk)
        chunks.append(chunk_text)
        i += words_per_chunk
    return chunks


def generate_srt(script: str, audio_duration: float, output_path: Path,
                 words_per_chunk: int = 8) -> str:
    """
    Generate a .srt subtitle file timed to audio_duration.
    
    Args:
        script:         narration text
        audio_duration: total audio length in seconds
        output_path:    where to save the .srt file
        words_per_chunk: words per subtitle line

    Returns:
        path to the saved .srt file
    """
    chunks     = split_into_chunks(script, words_per_chunk)
    n          = len(chunks)
    time_each  = audio_duration / n if n > 0 else 2.0
    lines      = []

    for i, chunk in enumerate(chunks):
        start = i * time_each
        end   = min((i + 1) * time_each - 0.1, audio_duration)
        lines.append(str(i + 1))
        lines.append(f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}")
        lines.append(chunk)
        lines.append("")

    srt_content = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(srt_content, encoding="utf-8")
    print(f"[SRT] Generated {n} subtitle chunks → {output_path.name}")
    return str(output_path)


def get_audio_duration(audio_path: str) -> float:
    """
    Get audio file duration in seconds using FFprobe.
    Falls back to word-count estimate if FFprobe unavailable.
    """
    import subprocess, json

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams", audio_path
            ],
            capture_output=True, text=True, timeout=15
        )
        data    = json.loads(result.stdout)
        streams = data.get("streams", [])
        for stream in streams:
            dur = stream.get("duration")
            if dur:
                return float(dur)
    except Exception as e:
        print(f"[SRT] FFprobe duration error: {e} — estimating from word count")

    # Fallback: estimate ~2.5 words per second
    word_count = len(audio_path.split()) if isinstance(audio_path, str) else 100
    return word_count / 2.5
