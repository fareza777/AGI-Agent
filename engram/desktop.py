"""Digital-assistant capabilities: files, documents, git, shell.

Everything here is sandboxed to config.ALLOWED_DIRS (the workspace plus any
roots the operator opts into via ENGRAM_ALLOWED_DIRS). Paths are resolved and
checked against those roots, so the agent cannot read or write outside them
even under prompt injection.

Document generation degrades gracefully: if python-docx / openpyxl aren't
installed, the relevant tool returns a clear "pip install" message instead of
crashing, and plain-text/markdown/csv/html generation always works.
"""

import csv
import io
import os
import subprocess
from pathlib import Path

from . import config


class WorkspaceError(Exception):
    pass


def ensure_workspace():
    config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


def resolve(path: str, must_exist: bool = False, for_write: bool = False) -> Path:
    """Resolve a user/model-supplied path against the allowed roots.

    Relative paths are taken relative to the workspace. Absolute paths must fall
    under one of ALLOWED_DIRS. Raises WorkspaceError on any escape.
    """
    ensure_workspace()
    raw = Path(os.path.expanduser(path.strip()))
    candidate = (raw if raw.is_absolute() else config.WORKSPACE_DIR / raw).resolve()
    for root in config.ALLOWED_DIRS:
        try:
            candidate.relative_to(root)
            break
        except ValueError:
            continue
    else:
        roots = ", ".join(str(r) for r in config.ALLOWED_DIRS)
        raise WorkspaceError(
            f"path '{path}' is outside the allowed directories ({roots})")
    if must_exist and not candidate.exists():
        raise WorkspaceError(f"path '{path}' does not exist")
    if for_write:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _rel(p: Path) -> str:
    """Display path relative to the workspace when possible."""
    try:
        return str(p.relative_to(config.WORKSPACE_DIR))
    except ValueError:
        return str(p)


def safe_filename(name: str) -> str:
    """Sanitize an externally-supplied filename (e.g. from Telegram) so it can
    never escape the inbox: strip directories, drop control chars."""
    base = os.path.basename(name.replace("\\", "/")).strip()
    base = "".join(c for c in base if c.isprintable() and c not in '<>:"|?*')
    return base or "file.bin"


def save_inbox_bytes(filename: str, data: bytes) -> Path:
    """Save incoming bytes to workspace/inbox/<safe name>, deduping on clash."""
    ensure_workspace()
    inbox = config.WORKSPACE_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    name = safe_filename(filename)
    target = inbox / name
    stem, suffix = target.stem, target.suffix
    n = 1
    while target.exists():
        target = inbox / f"{stem}_{n}{suffix}"
        n += 1
    target.write_bytes(data)
    return target


# ---------------- file operations ----------------

def list_dir(path: str = ".") -> str:
    target = resolve(path, must_exist=True)
    if not target.is_dir():
        return f"'{path}' is a file, not a directory."
    rows = []
    for child in sorted(target.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
        if child.is_dir():
            rows.append(f"[dir]  {child.name}/")
        else:
            rows.append(f"[file] {child.name}  ({child.stat().st_size} bytes)")
    return "\n".join(rows) if rows else "(empty directory)"


def read_file(path: str) -> str:
    target = resolve(path, must_exist=True)
    if target.is_dir():
        return f"'{path}' is a directory. Use list_dir."
    data = target.read_bytes()[: config.MAX_FILE_READ_BYTES]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"'{path}' is not a UTF-8 text file ({len(data)} bytes read)."
    truncated = target.stat().st_size > config.MAX_FILE_READ_BYTES
    return text + ("\n[...truncated]" if truncated else "")


def write_file(path: str, content: str) -> Path:
    target = resolve(path, for_write=True)
    target.write_text(content, encoding="utf-8")
    return target


def append_file(path: str, content: str) -> Path:
    target = resolve(path, for_write=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(content)
    return target


def make_dir(path: str) -> Path:
    target = resolve(path, for_write=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def move(src: str, dst: str) -> Path:
    s = resolve(src, must_exist=True)
    d = resolve(dst, for_write=True)
    s.rename(d)
    return d


def delete(path: str) -> str:
    target = resolve(path, must_exist=True)
    if target.is_dir():
        if any(target.iterdir()):
            return f"Refusing to delete non-empty directory '{path}'."
        target.rmdir()
    else:
        target.unlink()
    return f"Deleted {_rel(target)}."


def search_files(query: str, path: str = ".", max_hits: int = 40) -> str:
    root = resolve(path, must_exist=True)
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            fp = Path(dirpath) / name
            if query.lower() in name.lower():
                hits.append(f"{_rel(fp)} (name match)")
                continue
            try:
                if fp.stat().st_size > config.MAX_FILE_READ_BYTES:
                    continue
                text = fp.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    hits.append(f"{_rel(fp)}:{i}: {line.strip()[:120]}")
                    break
            if len(hits) >= max_hits:
                return "\n".join(hits) + "\n[...more]"
    return "\n".join(hits) if hits else f"No matches for '{query}'."


# ---------------- document generation ----------------

def create_document(filename: str, doc_format: str, title: str,
                    sections: list, table: dict = None) -> Path:
    """Create a document in the workspace.

    sections: [{"heading": str, "body": str}]
    table (optional): {"headers": [...], "rows": [[...], ...]}
    doc_format: docx | xlsx | pdf | md | html | txt | csv
    """
    fmt = doc_format.lower().lstrip(".")
    target = resolve(_with_ext(filename, fmt), for_write=True)
    builder = _BUILDERS.get(fmt)
    if builder is None:
        raise WorkspaceError(f"unsupported document format '{doc_format}'")
    builder(target, title, sections or [], table)
    return target


def _with_ext(filename: str, fmt: str) -> str:
    return filename if filename.lower().endswith(f".{fmt}") else f"{filename}.{fmt}"


def _build_md(target, title, sections, table):
    lines = [f"# {title}", ""]
    for s in sections:
        if s.get("heading"):
            lines.append(f"## {s['heading']}")
        if s.get("body"):
            lines.append(s["body"])
        lines.append("")
    if table and table.get("headers"):
        lines.append("| " + " | ".join(table["headers"]) + " |")
        lines.append("| " + " | ".join("---" for _ in table["headers"]) + " |")
        for row in table.get("rows", []):
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
    target.write_text("\n".join(lines), encoding="utf-8")


def _build_txt(target, title, sections, table):
    lines = [title, "=" * len(title), ""]
    for s in sections:
        if s.get("heading"):
            lines.append(s["heading"])
            lines.append("-" * len(s["heading"]))
        if s.get("body"):
            lines.append(s["body"])
        lines.append("")
    if table and table.get("headers"):
        lines.append("\t".join(table["headers"]))
        for row in table.get("rows", []):
            lines.append("\t".join(str(c) for c in row))
    target.write_text("\n".join(lines), encoding="utf-8")


def _build_csv(target, title, sections, table):
    if not (table and table.get("headers")):
        raise WorkspaceError("csv format needs a table with headers and rows")
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(table["headers"])
        writer.writerows(table.get("rows", []))


def _build_html(target, title, sections, table):
    out = [f"<!doctype html><html><head><meta charset='utf-8'>",
           f"<title>{_esc(title)}</title></head><body>",
           f"<h1>{_esc(title)}</h1>"]
    for s in sections:
        if s.get("heading"):
            out.append(f"<h2>{_esc(s['heading'])}</h2>")
        if s.get("body"):
            out.append(f"<p>{_esc(s['body']).replace(chr(10), '<br>')}</p>")
    if table and table.get("headers"):
        out.append("<table border='1' cellpadding='6' cellspacing='0'><thead><tr>")
        out += [f"<th>{_esc(str(h))}</th>" for h in table["headers"]]
        out.append("</tr></thead><tbody>")
        for row in table.get("rows", []):
            out.append("<tr>" + "".join(f"<td>{_esc(str(c))}</td>" for c in row) + "</tr>")
        out.append("</tbody></table>")
    out.append("</body></html>")
    target.write_text("\n".join(out), encoding="utf-8")


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _build_docx(target, title, sections, table):
    try:
        from docx import Document
    except ImportError:
        # Zero-dependency fallback — a valid .docx, just plainer styling.
        from . import docgen
        docgen.minimal_docx(target, title, sections, table)
        return
    doc = Document()
    doc.add_heading(title, level=0)
    for s in sections:
        if s.get("heading"):
            doc.add_heading(s["heading"], level=1)
        if s.get("body"):
            for para in s["body"].split("\n\n"):
                doc.add_paragraph(para)
    if table and table.get("headers"):
        headers = table["headers"]
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(headers):
            t.rows[0].cells[i].text = str(h)
        for row in table.get("rows", []):
            cells = t.add_row().cells
            for i, c in enumerate(row[:len(headers)]):
                cells[i].text = str(c)
    doc.save(str(target))


def _build_xlsx(target, title, sections, table):
    try:
        from openpyxl import Workbook
    except ImportError:
        from . import docgen
        docgen.minimal_xlsx(target, title, sections, table)
        return
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31] or "Sheet1"
    if table and table.get("headers"):
        ws.append(table["headers"])
        for row in table.get("rows", []):
            ws.append(list(row))
    else:
        ws.append([title])
        for s in sections:
            if s.get("heading"):
                ws.append([s["heading"]])
            if s.get("body"):
                ws.append([s["body"]])
    wb.save(str(target))


def _build_pdf(target, title, sections, table):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle)
        from reportlab.lib import colors
    except ImportError:
        from . import docgen
        docgen.minimal_pdf(target, title, sections, table)
        return
    styles = getSampleStyleSheet()
    flow = [Paragraph(_esc(title), styles["Title"]), Spacer(1, 12)]
    for s in sections:
        if s.get("heading"):
            flow.append(Paragraph(_esc(s["heading"]), styles["Heading2"]))
        if s.get("body"):
            flow.append(Paragraph(_esc(s["body"]).replace("\n", "<br/>"),
                                  styles["BodyText"]))
        flow.append(Spacer(1, 8))
    if table and table.get("headers"):
        data = [table["headers"]] + [list(r) for r in table.get("rows", [])]
        tbl = Table(data)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34495e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        flow.append(tbl)
    SimpleDocTemplate(str(target), pagesize=A4).build(flow)


_BUILDERS = {
    "md": _build_md, "markdown": _build_md, "txt": _build_txt, "text": _build_txt,
    "csv": _build_csv, "html": _build_html, "docx": _build_docx,
    "xlsx": _build_xlsx, "pdf": _build_pdf,
}

_NATIVE_LIBS = {"docx": "docx", "xlsx": "openpyxl", "pdf": "reportlab"}


def document_capabilities() -> dict:
    """{format: 'native' | 'builtin'} — never 'unavailable': formats without
    their rich library fall back to the zero-dependency builders in docgen."""
    caps = {fmt: "native" for fmt in ("md", "html", "txt", "csv")}
    for fmt, module in _NATIVE_LIBS.items():
        try:
            __import__(module)
            caps[fmt] = "native"
        except ImportError:
            caps[fmt] = "builtin"
    return caps


# ---------------- git / repo inspection (read-only) ----------------

_GIT_READ_ONLY = {"status", "log", "diff", "branch", "show", "remote",
                  "ls-files", "shortlog"}


def git(repo_path: str, command: str) -> str:
    repo = resolve(repo_path, must_exist=True)
    if not (repo / ".git").exists():
        return f"'{repo_path}' is not a git repository."
    parts = command.split()
    sub = parts[0] if parts else ""
    if sub not in _GIT_READ_ONLY:
        return (f"Only read-only git commands are allowed "
                f"({', '.join(sorted(_GIT_READ_ONLY))}).")
    safe = _trim_git(parts)
    try:
        proc = subprocess.run(["git", "-C", str(repo)] + safe,
                              capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "git is not installed on this machine."
    out = (proc.stdout or proc.stderr)[:6000]
    return out.strip() or "(no output)"


def _trim_git(parts: list) -> list:
    """Cap output-heavy commands so they don't flood the context."""
    if parts and parts[0] == "log" and not any(p.startswith("-n") for p in parts):
        return parts + ["-n", "20", "--oneline"]
    return parts


# ---------------- shell (opt-in) ----------------

def run_shell(command: str) -> str:
    ensure_workspace()
    proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                          timeout=config.SHELL_TIMEOUT_SEC,
                          cwd=str(config.WORKSPACE_DIR))
    out = (proc.stdout or "")[-4000:]
    err = (proc.stderr or "")[-2000:]
    return f"exit={proc.returncode}\nstdout:\n{out}\nstderr:\n{err}"
