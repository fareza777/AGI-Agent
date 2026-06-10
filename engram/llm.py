"""LLM provider layer.

Two entry points used by the rest of the agent:
  chat(...)    — conversational reply
  extract(...) — call that must return schema-shaped JSON (consolidation/reflection)

Providers:
  anthropic (default)        — official Anthropic SDK, adaptive thinking,
                               native structured outputs
  openrouter / minimax / openai — any OpenAI-compatible /chat/completions
                               endpoint; JSON extraction falls back to
                               prompt-instructed JSON with robust parsing
"""

import base64
import json
import re
from pathlib import Path

import requests

from . import config, tools

_anthropic_client = None

# ---------------- vision helpers ----------------
# Neutral message form: {"role": "user", "content": str, "images": [paths]}.
# Each provider branch converts images to its own block format.

_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif"}


def encode_image(path: str):
    """(media_type, base64) for a supported image within size limits, else None."""
    p = Path(path)
    media_type = _MEDIA_TYPES.get(p.suffix.lower())
    try:
        if not media_type or not p.is_file() or p.stat().st_size > config.MAX_IMAGE_BYTES:
            return None
        return media_type, base64.standard_b64encode(p.read_bytes()).decode()
    except OSError:
        return None


def _image_blocks_anthropic(paths: list) -> list:
    blocks = []
    for path in paths:
        enc = encode_image(path)
        if enc:
            blocks.append({"type": "image",
                           "source": {"type": "base64", "media_type": enc[0],
                                      "data": enc[1]}})
    return blocks


def _image_blocks_openai(paths: list) -> list:
    blocks = []
    for path in paths:
        enc = encode_image(path)
        if enc:
            blocks.append({"type": "image_url",
                           "image_url": {"url": f"data:{enc[0]};base64,{enc[1]}"}})
    return blocks


def _convert_messages(messages: list, image_fn) -> list:
    """Expand neutral messages with an 'images' key into provider blocks."""
    out = []
    for m in messages:
        if isinstance(m, dict) and m.get("images"):
            out.append({"role": m["role"],
                        "content": image_fn(m["images"])
                        + [{"type": "text", "text": m["content"]}]})
        else:
            out.append(m)
    return out


def chat(system: str, messages: list, max_tokens: int = 2000, ctx=None) -> str:
    """Conversational turn. When ctx (a tools.ToolContext) is given, the model
    gets the full tool set and we run the agentic loop until it stops calling
    tools (capped at config.MAX_TOOL_ITERS round trips)."""
    if config.PROVIDER == "anthropic":
        return _anthropic_chat(system, messages, max_tokens, ctx)
    return _openai_chat(config.CHAT_MODEL, system, messages, max_tokens, ctx)


def extract(system: str, user_text: str, schema: dict, max_tokens: int = 4000) -> dict:
    if config.PROVIDER == "anthropic":
        return _anthropic_extract(system, user_text, schema, max_tokens)
    return _openai_extract(system, user_text, schema, max_tokens)


# ---------------- Anthropic (official SDK) ----------------

def _client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def _anthropic_chat(system: str, messages: list, max_tokens: int, ctx) -> str:
    params = dict(
        model=config.CHAT_MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
    )
    if ctx is not None:
        params["tools"] = tools.tool_specs()

    messages = _convert_messages(messages, _image_blocks_anthropic)
    for _ in range(config.MAX_TOOL_ITERS):
        response = _client().messages.create(messages=messages, **params)
        if response.stop_reason != "tool_use" or ctx is None:
            return "".join(b.text for b in response.content if b.type == "text").strip()
        # Echo the assistant turn (incl. thinking blocks) then answer each tool call.
        messages.append({"role": "assistant", "content": response.content})
        results = [
            {"type": "tool_result", "tool_use_id": b.id,
             "content": tools.run_tool(b.name, b.input, ctx)}
            for b in response.content if b.type == "tool_use"
        ]
        messages.append({"role": "user", "content": results})
        # view_image queued images: attach them as a follow-up user message.
        if ctx.pending_images:
            blocks = _image_blocks_anthropic(ctx.pending_images)
            ctx.pending_images.clear()
            if blocks:
                messages.append({"role": "user", "content": blocks + [
                    {"type": "text", "text": "(gambar dari view_image terlampir)"}]})
    return "".join(b.text for b in response.content if b.type == "text").strip() \
        or "(tool budget exhausted before I reached an answer)"


def _anthropic_extract(system: str, user_text: str, schema: dict, max_tokens: int) -> dict:
    response = _client().messages.create(
        model=config.CONSOLIDATE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_text}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


# ---------------- OpenAI-compatible (OpenRouter, MiniMax, ...) ----------------

def _openai_request(payload: dict) -> dict:
    """Returns the raw choices[0].message dict."""
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    if config.PROVIDER == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/fareza777/AGI-Agent"
        headers["X-Title"] = "Engram"
    url = f"{config.OPENAI_BASE_URL}/chat/completions"

    resp = requests.post(url, json=payload, headers=headers, timeout=180)
    if resp.status_code >= 400 and "response_format" in payload:
        # Some models/providers reject response_format — retry without it.
        payload = {k: v for k, v in payload.items() if k != "response_format"}
        resp = requests.post(url, json=payload, headers=headers, timeout=180)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def _openai_chat(model: str, system: str, messages: list, max_tokens: int, ctx) -> str:
    convo = ([{"role": "system", "content": system}]
             + _convert_messages(messages, _image_blocks_openai))
    payload = {"model": model, "max_tokens": max_tokens, "messages": convo}
    if ctx is not None:
        payload["tools"] = tools.openai_tool_specs()

    for _ in range(config.MAX_TOOL_ITERS):
        msg = _openai_vision_request(payload)
        calls = msg.get("tool_calls") or []
        if not calls or ctx is None:
            return _visible_text(msg)
        convo.append(msg)
        for call in calls:
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = parse_json(fn.get("arguments") or "{}")
            convo.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": tools.run_tool(fn.get("name", ""), args, ctx),
            })
        # view_image queued images: attach them as a follow-up user message.
        if ctx.pending_images:
            blocks = _image_blocks_openai(ctx.pending_images)
            ctx.pending_images.clear()
            if blocks:
                convo.append({"role": "user", "content": blocks + [
                    {"type": "text", "text": "(gambar dari view_image terlampir)"}]})
        payload["messages"] = convo
    return _visible_text(msg) or "(tool budget exhausted before I reached an answer)"


def _openai_vision_request(payload: dict) -> dict:
    """Like _openai_request, but if the model rejects image input (no vision
    support), strip the images and retry once with a textual note instead of
    failing the whole turn."""
    try:
        return _openai_request(payload)
    except requests.HTTPError as exc:
        if not (exc.response is not None and exc.response.status_code == 400
                and _has_images(payload["messages"])):
            raise
        payload = dict(payload)
        payload["messages"] = _strip_images(payload["messages"])
        return _openai_request(payload)


def _has_images(convo: list) -> bool:
    return any(isinstance(m, dict) and isinstance(m.get("content"), list)
               and any(b.get("type") == "image_url" for b in m["content"])
               for m in convo)


def _strip_images(convo: list) -> list:
    out = []
    for m in convo:
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            texts = [b["text"] for b in m["content"] if b.get("type") == "text"]
            note = "[gambar dihapus — model ini tidak mendukung vision]"
            out.append({"role": m["role"], "content": "\n".join(texts + [note])})
        else:
            out.append(m)
    return out


def _visible_text(msg: dict) -> str:
    """User-facing text from an OpenAI-compatible message: strip reasoning."""
    text = strip_reasoning(msg.get("content") or "")
    return text or "(model hanya mengembalikan reasoning tanpa jawaban — coba ulangi)"


def _openai_extract(system: str, user_text: str, schema: dict, max_tokens: int) -> dict:
    system_json = (
        f"{system}\n\nRespond with ONLY a single JSON object matching this JSON "
        f"schema, no prose and no markdown fences:\n{json.dumps(schema)}"
    )
    msg = _openai_request({
        "model": config.CONSOLIDATE_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_json},
            {"role": "user", "content": user_text},
        ],
        "response_format": {"type": "json_object"},
    })
    # Strip reasoning BEFORE parsing — <think> blocks may contain stray braces.
    return parse_json(strip_reasoning(msg.get("content") or ""))


_THINK_BLOCK = re.compile(r"<\s*(think|thinking|reasoning)\s*>.*?<\s*/\s*\1\s*>",
                          re.DOTALL | re.IGNORECASE)
_THINK_CLOSE = re.compile(r"<\s*/\s*(think|thinking|reasoning)\s*>", re.IGNORECASE)
_THINK_OPEN = re.compile(r"\A\s*<\s*(think|thinking|reasoning)\s*>", re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Remove chain-of-thought that reasoning models (DeepSeek-R1 family,
    Nemotron, MiniMax, ...) emit inline as <think>...</think> tags, so it never
    reaches the user or the JSON parser."""
    text = _THINK_BLOCK.sub("", text)
    # Closing tag without a matching opening (opening was cut off upstream):
    # everything before the last close is reasoning.
    closes = list(_THINK_CLOSE.finditer(text))
    if closes:
        text = text[closes[-1].end():]
    # Unclosed opening tag at the start: the whole message is reasoning that
    # ran out of tokens — there is no visible answer.
    elif _THINK_OPEN.match(text):
        return ""
    return text.strip()


def parse_json(text: str) -> dict:
    """Parse JSON from model output that may include fences or surrounding prose."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise
