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
_LOCK_FILE = config.PROJECT_ROOT / "engram.lock"
_lock_handle = None


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


def _stale_instance_pids() -> int:
    old = 0
    if _PID_FILE.exists():
        try:
            old = int(_PID_FILE.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            old = 0
    return old


def _clear_stale_instance_files() -> None:
    _PID_FILE.unlink(missing_ok=True)
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _exit_if_instance_running(old: int) -> None:
    msg = "Another Engram instance is already running"
    if old:
        msg += f" (PID {old})"
    msg += ".\nStop it first, or run start_engram.bat to restart cleanly."
    print(msg)
    sys.exit(1)


def _release_single_instance() -> None:
    global _lock_handle
    _PID_FILE.unlink(missing_ok=True)
    if _lock_handle is not None:
        if os.name != "nt":
            import fcntl
            try:
                fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            _lock_handle.close()
        except OSError:
            pass
        _lock_handle = None
    try:
        _LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _acquire_single_instance() -> None:
    """Refuse to start if another Engram run.py holds the lock."""
    global _lock_handle
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        # Atomic create avoids msvcrt byte-lock + flush PermissionError on Windows.
        try:
            fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            old = _stale_instance_pids()
            if old and not _pid_alive(old):
                _clear_stale_instance_files()
                return _acquire_single_instance()
            _exit_if_instance_running(old)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        _lock_handle = open(_LOCK_FILE, "r+", encoding="utf-8")
        _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        atexit.register(_release_single_instance)
        return

    _lock_handle = open(_LOCK_FILE, "a+", encoding="utf-8")
    import fcntl
    try:
        fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        old = _stale_instance_pids()
        if old and not _pid_alive(old):
            try:
                _lock_handle.close()
            except OSError:
                pass
            _lock_handle = None
            _clear_stale_instance_files()
            return _acquire_single_instance()
        try:
            _lock_handle.close()
        except OSError:
            pass
        _lock_handle = None
        _exit_if_instance_running(old)

    _lock_handle.seek(0)
    _lock_handle.truncate()
    _lock_handle.write(str(os.getpid()))
    _lock_handle.flush()
    _PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_release_single_instance)


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
