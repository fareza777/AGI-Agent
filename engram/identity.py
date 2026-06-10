"""S5 Identity Core — a small version-controlled document loaded into every context.

The file lives at identity/CORE.md and is tracked in git: every change is a
diff in history, which is what makes identity drift visible and reversible.
The agent never edits it at runtime.
"""

from . import config

_FALLBACK = "I am Engram, a personal AI agent with permanent memory."


def load() -> str:
    try:
        return config.IDENTITY_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK
