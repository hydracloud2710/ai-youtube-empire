"""
AutoTube v2 — AI Script Generator
Supports: Anthropic Claude API (primary) | OpenAI (fallback) | Template (offline)
"""

import re


# ── Anthropic Claude ──────────────────────────────────────────────────────────
def generate_with_claude(topic: str, api_key: str, duration_hint: int = 60) -> dict:
    """
    Generate script + title + tags + description via Claude API.
    Returns: { title, script, tags, description }
    """
    try:
        import anthropic

        word_count = duration_hint * 2   # ~2 words/sec for narration

        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""You are a YouTube script writer. Write a complete video script for the topic below.

Topic: {topic}
Target length: ~{word_count} words (approximately {duration_hint} seconds when spoken)

Respond ONLY with a valid JSON object — no markdown, no extra text:
{{
  "title": "Engaging YouTube title (max 70 chars)",
  "script": "Full narration script here...",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "description": "YouTube description (2-3 sentences)"
}}"""

        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()

        import json
        data = json.loads(raw)
        data["source"] = "claude"
        return data

    except Exception as e:
        raise RuntimeError(f"Claude API error: {e}")


# ── OpenAI fallback ───────────────────────────────────────────────────────────
def generate_with_openai(topic: str, api_key: str, duration_hint: int = 60) -> dict:
    try:
        import openai, json
        openai.api_key = api_key
        word_count = duration_hint * 2

        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Write a YouTube script for: {topic}\n"
                    f"~{word_count} words.\n"
                    "Reply ONLY with JSON: {title, script, tags[], description}"
                )
            }],
            max_tokens=1200
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        data["source"] = "openai"
        return data
    except Exception as e:
        raise RuntimeError(f"OpenAI error: {e}")


# ── Offline template fallback ─────────────────────────────────────────────────
def generate_template(topic: str) -> dict:
    """Zero-API offline script template."""
    title = f"Amazing Facts About {topic.title()}"
    script = (
        f"Welcome to today's video about {topic}. "
        f"In this video, we'll explore the most fascinating and surprising facts "
        f"about {topic} that you probably never knew. "
        f"From its incredible history to its impact on our world today, "
        f"{topic} is a subject full of wonder and discovery. "
        f"Let's dive right in and uncover what makes {topic} so extraordinary. "
        f"Stay tuned until the end — the last fact might surprise you! "
        f"Don't forget to like and subscribe for more amazing content."
    )
    return {
        "title":       title,
        "script":      script,
        "tags":        [topic.lower(), "facts", "education", "amazing", "learn"],
        "description": f"Discover amazing facts about {topic} in this video. "
                       f"Like and subscribe for more educational content!",
        "source":      "template"
    }


# ── Main entry ────────────────────────────────────────────────────────────────
def generate_script(topic: str, config: dict, duration_hint: int = 60) -> dict:
    """
    Try providers in order: Claude → OpenAI → Template
    config keys: anthropic_key, openai_key
    """
    anthropic_key = config.get("anthropic_key", "").strip()
    openai_key    = config.get("openai_key", "").strip()

    if anthropic_key:
        try:
            return generate_with_claude(topic, anthropic_key, duration_hint)
        except Exception as e:
            print(f"[AI] Claude failed: {e} — trying OpenAI")

    if openai_key:
        try:
            return generate_with_openai(topic, openai_key, duration_hint)
        except Exception as e:
            print(f"[AI] OpenAI failed: {e} — using template")

    print("[AI] Using offline template (no API keys configured)")
    return generate_template(topic)
