"""Image generation (opt-in).

Calls an OpenAI-compatible /images/generations endpoint and writes the result
as a PNG into the workspace. Disabled unless config.IMAGE_ENDPOINT is set — the
tool then reports honestly that image generation isn't configured rather than
pretending to have made a picture.
"""

import base64
import logging

import requests

from . import config

log = logging.getLogger("engram.imagegen")


def available() -> bool:
    return bool(config.IMAGE_ENDPOINT and config.IMAGE_API_KEY)


def generate(prompt: str, out_path: str, size: str = "1024x1024") -> str:
    """Generate an image to out_path. Returns "" on success, else an error
    message (caller turns a non-empty return into an ERROR result)."""
    if not available():
        return ("image generation belum dikonfigurasi — set ENGRAM_IMAGE_ENDPOINT "
                "dan ENGRAM_IMAGE_API_KEY di .env.")
    url = f"{config.IMAGE_ENDPOINT}/images/generations"
    headers = {
        "Authorization": f"Bearer {config.IMAGE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": config.IMAGE_MODEL, "prompt": prompt, "n": 1,
               "size": size, "response_format": "b64_json"}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = (resp.json().get("data") or [{}])[0]
        b64 = data.get("b64_json")
        if not b64:
            # Some endpoints return a URL instead of base64.
            img_url = data.get("url")
            if img_url:
                img = requests.get(img_url, timeout=120)
                img.raise_for_status()
                with open(out_path, "wb") as fh:
                    fh.write(img.content)
                return ""
            return "endpoint tidak mengembalikan gambar."
        with open(out_path, "wb") as fh:
            fh.write(base64.b64decode(b64))
        return ""
    except Exception as exc:
        log.warning("image generation failed", exc_info=True)
        return f"gagal membuat gambar ({type(exc).__name__}: {exc})."
