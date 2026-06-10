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
    missing = [name for name, val in
               [("ANTHROPIC_API_KEY", config.ANTHROPIC_API_KEY),
                ("TELEGRAM_BOT_TOKEN", config.TELEGRAM_BOT_TOKEN)] if not val]
    if missing:
        print(f"Missing required env vars: {', '.join(missing)} (see .env.example)")
        return 1

    agent = Agent()
    agent.start_background()
    bot = TelegramBot(agent)
    try:
        bot.run()
    except KeyboardInterrupt:
        agent.stop()
        print("\nbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
