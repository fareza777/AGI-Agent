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

import json
import re

import requests

from . import config

_anthropic_client = None


def chat(system: str, messages: list, max_tokens: int = 2000) -> str:
    if config.PROVIDER == "anthropic":
        return _anthropic_chat(system, messages, max_tokens)
    return _openai_chat(config.CHAT_MODEL, system, messages, max_tokens)


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


def _anthropic_chat(system: str, messages: list, max_tokens: int) -> str:
    response = _client().messages.create(
        model=config.CHAT_MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


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

def _openai_request(payload: dict) -> str:
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
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def _openai_chat(model: str, system: str, messages: list, max_tokens: int) -> str:
    return _openai_request({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages,
    })


def _openai_extract(system: str, user_text: str, schema: dict, max_tokens: int) -> dict:
    system_json = (
        f"{system}\n\nRespond with ONLY a single JSON object matching this JSON "
        f"schema, no prose and no markdown fences:\n{json.dumps(schema)}"
    )
    text = _openai_request({
        "model": config.CONSOLIDATE_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_json},
            {"role": "user", "content": user_text},
        ],
        "response_format": {"type": "json_object"},
    })
    return parse_json(text)


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
