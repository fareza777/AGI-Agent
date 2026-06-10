"""Thin wrapper around the Anthropic SDK.

Two entry points:
  chat(...)    — conversational reply with adaptive thinking (Opus 4.8)
  extract(...) — structured-output call returning schema-validated JSON,
                 used by the consolidation and reflection jobs
"""

import json

import anthropic

from . import config

_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def chat(system: str, messages: list, max_tokens: int = 2000) -> str:
    response = client().messages.create(
        model=config.CHAT_MODEL,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


def extract(system: str, user_text: str, schema: dict, max_tokens: int = 4000) -> dict:
    response = client().messages.create(
        model=config.CONSOLIDATE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_text}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)
