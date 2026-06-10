#!/usr/bin/env python3
"""Engram entry point: start the agent + Telegram bot.

Usage:
    cp .env.example .env   # fill in ANTHROPIC_API_KEY and TELEGRAM_BOT_TOKEN
    pip install -r requirements.txt
    python run.py
"""

import logging
import sys

from engram import config
from engram.agent import Agent
from engram.telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def main() -> int:
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

    agent = Agent()
    bot = TelegramBot(agent)
    agent.notifier = bot.send                # scheduler: reminders + task results
    agent.file_notifier = bot.send_document  # scheduler: files from task runs
    agent.start_background()
    try:
        bot.run()
    except KeyboardInterrupt:
        agent.stop()
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
