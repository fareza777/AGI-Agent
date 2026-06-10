"""Configuration loaded from environment variables (and .env if present)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# LLM provider: "anthropic" (default, official SDK) or any OpenAI-compatible
# API — "openrouter", "minimax", or "openai" (generic, set your own base URL).
PROVIDER = os.environ.get("ENGRAM_PROVIDER", "anthropic").lower()

_DEFAULT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "minimax": "https://api.minimax.io/v1",
}
OPENAI_BASE_URL = os.environ.get(
    "ENGRAM_OPENAI_BASE_URL", _DEFAULT_BASE_URLS.get(PROVIDER, "")
).rstrip("/")

LLM_API_KEY = (
    os.environ.get("ENGRAM_LLM_API_KEY")
    or os.environ.get("OPENROUTER_API_KEY")
    or os.environ.get("MINIMAX_API_KEY")
    or ""
)

# Model IDs are provider-specific. The claude-opus-4-8 default only applies to
# the anthropic provider; for others you must set the model explicitly
# (e.g. "anthropic/claude-opus-4.5" or "minimax/minimax-m2" on OpenRouter,
# "MiniMax-M2" on MiniMax direct).
_DEFAULT_MODEL = "claude-opus-4-8" if PROVIDER == "anthropic" else ""
CHAT_MODEL = os.environ.get("ENGRAM_CHAT_MODEL", _DEFAULT_MODEL)
CONSOLIDATE_MODEL = os.environ.get("ENGRAM_CONSOLIDATE_MODEL", CHAT_MODEL)

DB_PATH = os.environ.get("ENGRAM_DB_PATH", str(PROJECT_ROOT / "engram.db"))
IDENTITY_PATH = PROJECT_ROOT / "identity" / "CORE.md"

CONSOLIDATE_INTERVAL_MIN = int(os.environ.get("ENGRAM_CONSOLIDATE_INTERVAL_MIN", "30"))
# Also consolidate after this many new events accumulate in a chat.
CONSOLIDATE_EVERY_N_EVENTS = int(os.environ.get("ENGRAM_CONSOLIDATE_EVERY_N_EVENTS", "20"))

# Comma-separated chat IDs allowed to use the bot; empty = open to anyone.
ALLOWED_CHAT_IDS = {
    c.strip() for c in os.environ.get("ENGRAM_ALLOWED_CHAT_IDS", "").split(",") if c.strip()
}

# Context budget knobs (S6 Working-Memory Composer)
MAX_RETRIEVED_CLAIMS = 25
MAX_RETRIEVED_EPISODES = 8
CONVERSATION_TAIL = 16  # recent events included verbatim
