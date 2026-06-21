#!/usr/bin/env python3
"""Engram entry point: start the agent + Telegram bot.

Usage:
    cp .env.example .env   # fill in ANTHROPIC_API_KEY and TELEGRAM_BOT_TOKEN
    pip install -r requirements.txt
    python run.py
"""

import atexit
import logging
import os
import sys

from engram import config
from engram.agent import Agent
from engram.telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

_PID_FILE = config.PROJECT_ROOT / "engram.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_single_instance() -> None:
    """Refuse to start if another Engram run.py is already alive."""
    if _PID_FILE.exists():
        try:
            old = int(_PID_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            old = 0
        if _pid_alive(old):
            print(
                f"Another Engram instance is already running (PID {old}).\n"
                "Stop it first, or run start_engram.bat to restart cleanly."
            )
            sys.exit(1)
    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(lambda: _PID_FILE.unlink(missing_ok=True))


def main() -> int:
    _acquire_single_instance()
    problems = []
    if not config.TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN is not set")
    if config.PROVIDER == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            problems.append("ANTHROPIC_API_KEY is not set")
    else:
        if not config.LLM_API_KEY:
            problems.append(
                "no API key for provider "
                f"'{config.PROVIDER}' (set ENGRAM_LLM_API_KEY, OPENROUTER_API_KEY, "
                "or MINIMAX_API_KEY)")
        if not config.OPENAI_BASE_URL:
            problems.append("ENGRAM_OPENAI_BASE_URL is not set for this provider")
        if not config.CHAT_MODEL:
            problems.append(
                "ENGRAM_CHAT_MODEL must be set for non-anthropic providers, e.g. "
                "'minimax/minimax-m2' (OpenRouter) or 'MiniMax-M2' (MiniMax direct)")
    if problems:
        print("Configuration problems (see .env.example):")
        for p in problems:
            print(f"  - {p}")
        return 1

    from engram import desktop
    caps = desktop.document_capabilities()
    builtin = [f for f in ("docx", "xlsx", "pptx", "pdf") if caps[f] == "builtin"]
    if builtin:
        libs = {"docx": "python-docx", "xlsx": "openpyxl",
                "pptx": "python-pptx", "pdf": "reportlab"}
        logging.getLogger("engram").info(
            "document formats %s using built-in generators (works, plainer "
            "styling); for richer output: pip install %s",
            ", ".join(builtin), " ".join(libs[f] for f in builtin))

    agent = Agent()
    bot = TelegramBot(agent)   # wires notifier/file/activity channels itself
    agent.start_background()
    try:
        bot.run()
    except KeyboardInterrupt:
        agent.stop()
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
