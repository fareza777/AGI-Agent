"""Configuration loaded from environment variables (and .env if present)."""

import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
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
CONSOLIDATE_EVERY_N_EVENTS = int(
    os.environ.get("ENGRAM_CONSOLIDATE_EVERY_N_EVENTS", "20")
)
# Comma-separated chat IDs allowed to use the bot; empty = open to anyone.
ALLOWED_CHAT_IDS = {
    c.strip()
    for c in os.environ.get("ENGRAM_ALLOWED_CHAT_IDS", "").split(",")
    if c.strip()
}
# Max output tokens per chat turn. Must be generous: tool calls carry their
# whole payload (e.g. a full create_document report) inside the response, and
# reasoning models spend output tokens thinking first. Too small = truncated
# tool-call JSON = JSONDecodeError.
MAX_OUTPUT_TOKENS = int(os.environ.get("ENGRAM_MAX_OUTPUT_TOKENS", "8000"))
# Context budget knobs (S6 Working-Memory Composer)
MAX_RETRIEVED_CLAIMS = 25
MAX_RETRIEVED_EPISODES = 8
CONVERSATION_TAIL = 16  # recent events included verbatim
# Hard ceiling on the assembled system prompt (rough tokens ~= chars/4). The
# retrieved-memory sections (claims, episodes, lessons) are trimmed by priority
# to fit under this so context can't silently balloon cost or truncate tool
# JSON. Static identity/instructions/capabilities are always kept.
MAX_CONTEXT_TOKENS = int(os.environ.get("ENGRAM_MAX_CONTEXT_TOKENS", "12000"))
# Tool layer
MAX_TOOL_ITERS = 10  # max tool-use round trips per turn
SKILLS_DIR = PROJECT_ROOT / "skills"
REMINDER_POLL_SEC = 30  # scheduler tick
# Digital-assistant workspace. All file/document tools are sandboxed to dirs in
# ALLOWED_DIRS; by default just this workspace. Add more roots (e.g. your repos
# folder) via ENGRAM_ALLOWED_DIRS=/path/a,/path/b to let the agent read/inspect
# them. Files the agent generates land here and are delivered back over Telegram.
WORKSPACE_DIR = Path(
    os.environ.get("ENGRAM_WORKSPACE_DIR", str(PROJECT_ROOT / "workspace"))
).resolve()
ALLOWED_DIRS = [WORKSPACE_DIR] + [
    Path(p.strip()).resolve()
    for p in os.environ.get("ENGRAM_ALLOWED_DIRS", "").split(",")
    if p.strip()
]
# Vision: images the user sends (or view_image loads) are passed to the model
# as base64. Cap raw size to stay under provider limits (~5MB at Anthropic).
MAX_IMAGE_BYTES = 4_500_000
# Live activity feed: stream compact "🔎 web_search: ..." lines to the chat
# while the agent works. Set 0 to disable.
SHOW_ACTIVITY = os.environ.get("ENGRAM_SHOW_ACTIVITY", "1") == "1"
# The run_python tool executes model-written code on your machine. Off by
# default; enable only if you trust everyone who can message the bot.
ENABLE_CODE_TOOL = os.environ.get("ENGRAM_ENABLE_CODE_TOOL", "0") == "1"
# The run_shell tool runs shell commands (scoped to the workspace). Same trust
# caveat — off by default.
ENABLE_SHELL_TOOL = os.environ.get("ENGRAM_ENABLE_SHELL_TOOL", "0") == "1"
SHELL_TIMEOUT_SEC = int(os.environ.get("ENGRAM_SHELL_TIMEOUT_SEC", "30"))
# Git tool mode: 1 = read-only (default, safe), 0 = write also allowed
# (add, commit, checkout, branch -d, stash, push, fetch). --force,
# --force-with-lease, reset --hard, clean -fd are blocked in BOTH modes.
GIT_READ_ONLY = os.environ.get("ENGRAM_GIT_READ_ONLY", "1") == "1"
MAX_FILE_READ_BYTES = 200_000

# Branding: a logo placed on the cover of generated docx/pptx. Defaults to the
# bundled Engram avatar when present; override with ENGRAM_BRAND_LOGO=/path.
_DEFAULT_LOGO = PROJECT_ROOT / "engram-agent-avatar.png"
BRAND_LOGO = os.environ.get("ENGRAM_BRAND_LOGO", "") or (
    str(_DEFAULT_LOGO) if _DEFAULT_LOGO.is_file() else ""
)

# Voice: transcribe incoming Telegram voice notes via an OpenAI-compatible
# /audio/transcriptions endpoint. Disabled unless an endpoint is configured —
# when off, a voice note is saved and the agent says STT isn't set up (it never
# pretends to have heard audio it couldn't transcribe).
STT_ENDPOINT = os.environ.get("ENGRAM_STT_ENDPOINT", "").rstrip("/")
STT_API_KEY = os.environ.get("ENGRAM_STT_API_KEY", "") or LLM_API_KEY
STT_MODEL = os.environ.get("ENGRAM_STT_MODEL", "whisper-1")

# Outbound email (connectors.send_email). Off until SMTP is configured; the
# send_email tool then reports honestly that email isn't set up.
SMTP_HOST = os.environ.get("ENGRAM_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("ENGRAM_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("ENGRAM_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("ENGRAM_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("ENGRAM_SMTP_FROM", "") or SMTP_USER

# Semantic memory retrieval. When an embeddings endpoint is configured, claim
# search becomes hybrid: BM25 fetches a candidate pool, then results are
# re-ranked by embedding cosine similarity so paraphrases and cross-language
# queries ("makanan favorit" vs a belief stored in English) still match. With
# no endpoint set, retrieval stays pure BM25 — identical to before.
EMBED_ENDPOINT = os.environ.get("ENGRAM_EMBED_ENDPOINT", "").rstrip("/")
EMBED_API_KEY = os.environ.get("ENGRAM_EMBED_API_KEY", "") or LLM_API_KEY
EMBED_MODEL = os.environ.get("ENGRAM_EMBED_MODEL", "text-embedding-3-small")

# Image generation. When an images endpoint (OpenAI-compatible
# /images/generations) is configured, the generate_image tool produces a PNG
# and delivers it. Unset = the tool reports it isn't configured (never fakes).
IMAGE_ENDPOINT = os.environ.get("ENGRAM_IMAGE_ENDPOINT", "").rstrip("/")
IMAGE_API_KEY = os.environ.get("ENGRAM_IMAGE_API_KEY", "") or LLM_API_KEY
IMAGE_MODEL = os.environ.get("ENGRAM_IMAGE_MODEL", "gpt-image-1")
