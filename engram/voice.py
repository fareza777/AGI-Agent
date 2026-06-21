"""Speech-to-text for incoming Telegram voice notes.

Transcribes an audio file via an OpenAI-compatible /audio/transcriptions
endpoint (Whisper-style). This is opt-in: if config.STT_ENDPOINT is unset,
transcription is unavailable and the caller falls back to telling the user that
voice isn't configured — the agent never fabricates what a voice note "said".
"""

import logging

import requests

from . import config

log = logging.getLogger("engram.voice")


def available() -> bool:
    return bool(config.STT_ENDPOINT and config.STT_API_KEY)


def transcribe(path: str) -> str:
    """Return the transcript text, or "" if STT is unavailable / fails."""
    if not available():
        return ""
    url = f"{config.STT_ENDPOINT}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {config.STT_API_KEY}"}
    try:
        with open(path, "rb") as fh:
            resp = requests.post(
                url,
                headers=headers,
                data={"model": config.STT_MODEL},
                files={"file": (path.rsplit("/", 1)[-1], fh)},
                timeout=120,
            )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            return (resp.json().get("text") or "").strip()
        return resp.text.strip()
    except Exception:
        log.warning("transcription failed for %s", path, exc_info=True)
        return ""
