"""Skill library — markdown skills with progressive disclosure, versioning,
and a learning lifecycle.

Each file in skills/ is one skill:

    ---
    name: weekly_review
    description: One-line description shown to the model in every context.
    status: active        (active | draft — drafts need user approval)
    version: 1
    ---
    Full instructions, loaded only when the agent calls use_skill(name).

Lifecycle (S8 Skill Compiler, staged rollout per DESIGN.md):
  - Users drop .md files in skills/ → active immediately.
  - The agent saves a skill mid-conversation (create_skill tool) when the user
    teaches it a procedure → active immediately (explicit teaching).
  - The background miner proposes skills from repeated patterns in the event
    log → saved as *draft*, activated only via /approve.
  - improve_skill upgrades a skill from experience: the old version is
    archived to skills/history/<name>.v<N>.md and the version is bumped, so
    every revision is auditable and reversible.
"""

import re

from . import config

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def sanitize(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", name.strip().lower().replace(" ", "_").replace("-", "_"))


def _parse(path) -> dict:
    text = path.read_text(encoding="utf-8")
    meta = {"name": path.stem, "description": "", "status": "active", "version": 1}
    body = text
    m = _FRONTMATTER.match(text)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip().lower()] = val.strip()
    try:
        meta["version"] = int(meta["version"])
    except (TypeError, ValueError):
        meta["version"] = 1
    meta["body"] = body.strip()
    return meta


def entries() -> list:
    """All skills (active and draft) as metadata dicts."""
    if not config.SKILLS_DIR.is_dir():
        return []
    return [_parse(p) for p in sorted(config.SKILLS_DIR.glob("*.md"))]


def index() -> list:
    """[(name, description)] for ACTIVE skills only — what the model sees."""
    return [(m["name"], m["description"]) for m in entries() if m["status"] == "active"]


def get(name: str) -> dict:
    for m in entries():
        if m["name"] == name:
            return m
    return None


def load(name: str) -> str:
    """Full body of one ACTIVE skill (use_skill tool), '' otherwise."""
    meta = get(name)
    return meta["body"] if meta and meta["status"] == "active" else ""


def save(name: str, description: str, body: str,
         status: str = "active", version: int = 1):
    name = sanitize(name)
    if not name:
        raise ValueError("invalid skill name")
    config.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    description = description.replace("\n", " ").strip()
    content = (f"---\nname: {name}\ndescription: {description}\n"
               f"status: {status}\nversion: {version}\n---\n{body.strip()}\n")
    (config.SKILLS_DIR / f"{name}.md").write_text(content, encoding="utf-8")
    return name


def bump(name: str, description: str, body: str) -> int:
    """Upgrade a skill: archive the current version, write version+1. Returns
    the new version number."""
    meta = get(name)
    if meta is None:
        raise ValueError(f"no skill named '{name}'")
    history = config.SKILLS_DIR / "history"
    history.mkdir(parents=True, exist_ok=True)
    src = config.SKILLS_DIR / f"{name}.md"
    (history / f"{name}.v{meta['version']}.md").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8")
    new_version = meta["version"] + 1
    save(name, description or meta["description"], body,
         status=meta["status"], version=new_version)
    return new_version


def approve(name: str) -> bool:
    """Promote a draft skill to active."""
    meta = get(name)
    if meta is None or meta["status"] != "draft":
        return False
    save(name, meta["description"], meta["body"], status="active",
         version=meta["version"])
    return True


def drop(name: str) -> bool:
    """Delete a skill file (drafts you reject, skills you retire)."""
    meta = get(name)
    if meta is None:
        return False
    (config.SKILLS_DIR / f"{name}.md").unlink()
    return True
