"""Render model output as clean Telegram messages.

Telegram has no markdown tables and no headings, so raw model markdown (the
"| col | col |" walls and "## heading" lines seen in the wild) renders as ugly
literal pipes and hashes. This module converts markdown to the small HTML
subset Telegram supports (<b> <i> <code> <pre> <a>), flattens tables into
readable bullet lines, and strips any leaked <think> reasoning.

Telegram HTML reference: only b/strong, i/em, u, s, code, pre, a, blockquote.
"""

import re

_THINK = re.compile(r"<\s*(think|thinking|reasoning)\s*>.*?<\s*/\s*\1\s*>",
                    re.DOTALL | re.IGNORECASE)


def strip_legacy_system_notes(text: str) -> str:
    """Drop old 'Catatan sistem' footers the model may copy from chat history."""
    marker = "⚠️ Catatan sistem:"
    if marker not in (text or ""):
        return text or ""
    head, tail = text.split(marker, 1)
    if any(k in tail.lower() for k in (
            "create_document", "send_file", "ter-queue", "wajib pakai tool",
            "tidak dipanggil", "belum sampai ke chat")):
        return head.rstrip()
    return text


def strip_system_notes(text: str) -> str:
    """Remove any ⚠️ Catatan sistem ... paragraph from a string.

    This is used both to clean the model's own output before appending a fresh
    guard note, and to stop the composer from replaying previous guard notes
    back into the model as history. It must NOT be used in the final Telegram
    formatter, because the current turn's guard note should be visible.
    """
    return re.sub(r"⚠️ Catatan sistem.*?(?=\n\n|$)", "", text or "",
                  flags=re.DOTALL).strip()


_INJECTION_LEAD = re.compile(
    r"^[\s\S]*?(?:injeksi|injection|pesan tersembunyi|hidden message)"
    r"[\s\S]*?\n---\n",
    re.I | re.MULTILINE,
)
_INJECTION_TAIL = re.compile(
    r"\n*(?:\*{1,2}|_)?Sekali lagi:[^\n]*(?:injeksi|injection)[^\n]*(?:\*{1,2}|_)?\s*$",
    re.I,
)
_INJECTION_NOTE = re.compile(
    r"\n\n(?:Note:)?[^\n]*(?:injeksi|injection|pesan tersembunyi)[^\n]*(?=\n\n|\Z)",
    re.I,
)


def strip_injection_paranoia(text: str) -> str:
    """Remove model hallucinations accusing the user of prompt injection."""
    text = text or ""
    if not re.search(r"injeksi|injection|pesan tersembunyi|hidden message", text, re.I):
        return text
    text = _INJECTION_LEAD.sub("", text, count=1)
    text = _INJECTION_TAIL.sub("", text)
    text = _INJECTION_NOTE.sub("\n\n", text)
    return text.rstrip()


def sanitize_agent_reply(text: str) -> str:
    return strip_injection_paranoia(strip_legacy_system_notes(text or ""))


def to_telegram_html(text: str) -> str:
    text = sanitize_agent_reply(_THINK.sub("", text or "").strip())

    # 1. Pull out fenced code blocks so their contents aren't reformatted.
    blocks = []

    def _stash(m):
        blocks.append(m.group(1))
        return f"\x00BLOCK{len(blocks) - 1}\x00"

    text = re.sub(r"```[a-zA-Z0-9_]*\n?(.*?)```", _stash, text, flags=re.DOTALL)

    # 2. Flatten markdown tables into bullet lines.
    text = _flatten_tables(text)

    # 3. Escape HTML, then re-apply inline formatting on the escaped text.
    out_lines = []
    for line in text.split("\n"):
        out_lines.append(_format_line(line))
    rendered = "\n".join(out_lines)

    # 4. Restore code blocks as <pre>.
    def _restore(m):
        body = _esc(blocks[int(m.group(1))]).rstrip("\n")
        return f"<pre>{body}</pre>"

    rendered = re.sub(r"\x00BLOCK(\d+)\x00", _restore, rendered)
    # Collapse 3+ blank lines.
    return re.sub(r"\n{3,}", "\n\n", rendered).strip()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_line(line: str) -> str:
    if "\x00BLOCK" in line:
        return line  # placeholder line, restored later

    stripped = line.strip()
    # Horizontal rules → drop.
    if re.fullmatch(r"[-*_]{3,}", stripped):
        return ""

    # Headings → bold line.
    heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
    if heading:
        return f"<b>{_inline(heading.group(2))}</b>"

    # Bullet / numbered list markers → tidy bullet.
    bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
    if bullet:
        return f"• {_inline(bullet.group(1))}"
    numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
    if numbered:
        return f"{numbered.group(1)}. {_inline(numbered.group(2))}"

    # Blockquote.
    quote = re.match(r"^>\s?(.*)$", stripped)
    if quote:
        return f"<i>{_inline(quote.group(1))}</i>"

    return _inline(line)


def _inline(text: str) -> str:
    """Escape then apply inline markdown (bold/italic/code/links)."""
    # Protect inline code spans first.
    spans = []

    def _stash_code(m):
        spans.append(m.group(1))
        return f"\x01C{len(spans) - 1}\x01"

    text = re.sub(r"`([^`]+)`", _stash_code, text)
    text = _esc(text)

    # Links [text](url)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)
    # Bold **x** or __x__
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^_]+)__", r"<b>\1</b>", text)
    # Italic *x* or _x_ (avoid touching ** already consumed)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<i>\1</i>", text)
    text = re.sub(r"(?<![_\w])_([^_\n]+)_(?![_\w])", r"<i>\1</i>", text)

    def _restore_code(m):
        return f"<code>{_esc(spans[int(m.group(1))])}</code>"

    return re.sub(r"\x01C(\d+)\x01", _restore_code, text)


def _flatten_tables(text: str) -> str:
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        if _is_table_row(lines[i]) and i + 1 < len(lines) and _is_separator(lines[i + 1]):
            headers = _cells(lines[i])
            i += 2
            rows = []
            while i < len(lines) and _is_table_row(lines[i]):
                rows.append(_cells(lines[i]))
                i += 1
            out.extend(_render_table(headers, rows))
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line)) and "-" in line


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _render_table(headers, rows):
    out = []
    for row in rows:
        pairs = []
        for h, c in zip(headers, row):
            if not c:
                continue
            pairs.append(f"{h}: {c}" if h else c)
        # extra cells beyond headers
        for c in row[len(headers):]:
            if c:
                pairs.append(c)
        if pairs:
            out.append("• " + " — ".join(pairs))
    return out
