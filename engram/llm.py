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
import time
from pathlib import Path

import requests

from . import config, tools

_anthropic_client = None


class LLMError(Exception):
    """Provider failure with a user-presentable message and HTTP status."""

    def __init__(self, message: str, status: int = None):
        super().__init__(message)
        self.status = status


def describe_error(exc: Exception) -> str:
    """Short Indonesian-friendly description of a provider failure, for the
    chat reply (the full traceback goes to the server log)."""
    if isinstance(exc, LLMError):
        return str(exc)
    name = type(exc).__name__
    if "RateLimit" in name:
        return "kena rate limit provider — tunggu sebentar lalu coba lagi"
    if "Authentication" in name or "PermissionDenied" in name:
        return "API key ditolak provider — cek konfigurasi .env"
    if "Connection" in name or "Timeout" in name:
        return "koneksi ke provider bermasalah — cek jaringan server"
    if "Overloaded" in name:
        return "provider sedang kelebihan beban — coba lagi sebentar lagi"
    return f"error provider ({name})"

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

_RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 524, 529}


def _openai_request(payload: dict) -> dict:
    """POST /chat/completions and return choices[0].message.

    Uses STREAMING (SSE) and accumulates the deltas client-side. Long
    reasoning turns (MiniMax M2/M3, DeepSeek-R1) can run for minutes; a
    non-streaming request sits idle and gets killed by gateways/proxies
    (524/timeout) — the classic 'works in other agents, fails here' cause.
    With streaming, each chunk resets the read timeout.

    Also retries transient failures (429/5xx/timeouts) with backoff honoring
    Retry-After, and raises LLMError carrying the provider's real message."""
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }
    if config.PROVIDER == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/fareza777/AGI-Agent"
        headers["X-Title"] = "Engram"
    url = f"{config.OPENAI_BASE_URL}/chat/completions"

    attempts = 3
    last_err = "unknown error"
    last_status = None
    for attempt in range(attempts):
        body = dict(payload)
        body["stream"] = True
        try:
            # (connect, read) — read timeout is PER CHUNK on a streaming
            # response, so heartbeats/deltas keep long turns alive.
            resp = requests.post(url, json=body, headers=headers,
                                 stream=True, timeout=(15, 120))
        except requests.RequestException as exc:
            last_err, last_status = f"koneksi gagal ({type(exc).__name__})", None
            time.sleep(2 ** attempt)
            continue

        if resp.status_code < 400:
            ctype = resp.headers.get("content-type", "")
            try:
                if "event-stream" in ctype:
                    return _accumulate_sse(resp.iter_lines(decode_unicode=True))
                data = resp.json()  # provider ignored stream=true
                choices = data.get("choices") or []
                if not choices:
                    raise ValueError(f"respons kosong: {str(data)[:200]}")
                return choices[0]["message"]
            except (requests.RequestException, ValueError) as exc:
                last_err = f"stream terputus ({exc})"
                last_status = None
                time.sleep(2 ** attempt)
                continue

        last_status = resp.status_code
        last_err = _provider_error(resp)
        if resp.status_code in _RETRYABLE and attempt < attempts - 1:
            retry_after = resp.headers.get("retry-after", "")
            delay = (int(retry_after) if retry_after.isdigit()
                     else 2 ** (attempt + 1))
            time.sleep(min(delay, 30))
            continue
        if "response_format" in payload:
            # Some models/providers reject response_format — retry without it.
            payload = {k: v for k, v in payload.items() if k != "response_format"}
            continue
        break

    hint = ""
    if last_status == 429:
        hint = " (rate limit provider — tunggu sebentar)"
    elif last_status == 402:
        hint = " (kredit provider habis)"
    raise LLMError(f"provider error {last_status or ''}: {last_err}{hint}".strip(),
                   status=last_status)


def _accumulate_sse(lines) -> dict:
    """Fold an OpenAI-style SSE stream back into one message dict
    (content + reasoning_content + tool_calls)."""
    content, reasoning = [], []
    tool_calls = {}
    saw_chunk = False
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if chunk.get("error"):
            err = chunk["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise ValueError(f"provider mengirim error di stream: {msg}")
        for choice in chunk.get("choices") or []:
            saw_chunk = True
            delta = choice.get("delta") or choice.get("message") or {}
            if delta.get("content"):
                content.append(delta["content"])
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(
                    idx, {"id": "", "type": "function",
                          "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name") and not slot["function"]["name"]:
                    slot["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
    if not saw_chunk:
        raise ValueError("stream kosong dari provider")
    msg = {"role": "assistant", "content": "".join(content)}
    if reasoning:
        msg["reasoning_content"] = "".join(reasoning)
    if tool_calls:
        msg["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return msg


def _provider_error(resp) -> str:
    """Pull the human-readable error out of a provider error body."""
    try:
        err = resp.json().get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        if err:
            return str(err)[:300]
    except ValueError:
        pass
    return (resp.text or "")[:300] or f"HTTP {resp.status_code}"


def _merge_consecutive(messages: list) -> list:
    """Merge adjacent same-role plain-text messages. Strict providers
    (MiniMax among them) reject conversations where user/assistant roles
    don't alternate — which our history can produce (e.g. a user message
    following a failed turn)."""
    out = []
    for m in messages:
        prev = out[-1] if out else None
        if (prev is not None
                and isinstance(m, dict) and isinstance(prev, dict)
                and m.get("role") == prev.get("role")
                and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str)
                and isinstance(prev.get("content"), str)
                and "tool_calls" not in m and "tool_calls" not in prev):
            prev["content"] = f"{prev['content']}\n\n{m['content']}".strip()
        else:
            out.append(dict(m) if isinstance(m, dict) else m)
    return out


def _openai_chat(model: str, system: str, messages: list, max_tokens: int, ctx) -> str:
    convo = _merge_consecutive(
        [{"role": "system", "content": system}]
        + _convert_messages(messages, _image_blocks_openai))
    payload = {"model": model, "max_tokens": max_tokens, "messages": convo}
    if ctx is not None:
        payload["tools"] = tools.openai_tool_specs()

    for _ in range(config.MAX_TOOL_ITERS):
        msg = _openai_vision_request(payload)
        if not (msg.get("tool_calls") or []) or ctx is None:
            return _visible_text(msg)
        # Echo back ONLY the standard fields. Some models attach extras
        # (reasoning, refusal, provider metadata) that other requests then
        # reject with 400 — a classic intermittent-failure source. Iterate the
        # CLEANED calls so tool_call_id always matches what we echoed.
        cleaned = _clean_assistant(msg)
        convo.append(cleaned)
        calls = cleaned["tool_calls"]
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


def _clean_assistant(msg: dict) -> dict:
    """Reduce an assistant message to the fields every OpenAI-compatible
    endpoint accepts back: role, content, tool_calls. MiniMax additionally
    gets reasoning_content echoed — their interleaved-thinking models are
    documented to perform better when it's kept in multi-turn history."""
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if config.PROVIDER == "minimax" and msg.get("reasoning_content"):
        out["reasoning_content"] = msg["reasoning_content"]
    calls = []
    for i, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function") or {}
        calls.append({"id": c.get("id") or f"call_{i}", "type": "function",
                      "function": {"name": fn.get("name", ""),
                                   "arguments": fn.get("arguments") or "{}"}})
    if calls:
        out["tool_calls"] = calls
    return out


def _openai_vision_request(payload: dict) -> dict:
    """Like _openai_request, but if the model rejects image input (no vision
    support), strip the images and retry once with a textual note instead of
    failing the whole turn."""
    try:
        return _openai_request(payload)
    except LLMError as exc:
        if exc.status != 400 or not _has_images(payload["messages"]):
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
