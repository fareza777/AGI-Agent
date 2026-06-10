"""Skill library — Hermes-style markdown skills with progressive disclosure.

Each file in skills/ is one skill:

    ---
    name: weekly_review
    description: One-line description shown to the model in every context.
    ---
    Full instructions, loaded only when the agent calls use_skill(name).

Only the name+description index sits in the context window by default; the
full body is pulled in on demand via the use_skill tool. That keeps the prompt
small no matter how many skills you add.
"""

import re

from . import config

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse(path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta = {"name": path.stem, "description": ""}
    m = _FRONTMATTER.match(text)
    body = text
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip().lower()] = val.strip()
    meta["body"] = body.strip()
    return meta


def index() -> list:
    """[(name, description)] for every skill on disk."""
    if not config.SKILLS_DIR.is_dir():
        return []
    out = []
    for path in sorted(config.SKILLS_DIR.glob("*.md")):
        meta = _parse(path)
        out.append((meta["name"], meta["description"]))
    return out


def load(name: str) -> str:
    """Full body of one skill, or '' if it doesn't exist."""
    if not config.SKILLS_DIR.is_dir():
        return ""
    for path in config.SKILLS_DIR.glob("*.md"):
        meta = _parse(path)
        if meta["name"] == name:
            return meta["body"]
    return ""
