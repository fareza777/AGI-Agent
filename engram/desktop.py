"""Digital-assistant capabilities: files, documents, git, shell.

Everything here is sandboxed to config.ALLOWED_DIRS (the workspace plus any

roots the operator opts into via ENGRAM_ALLOWED_DIRS). Paths are resolved and

checked against those roots, so the agent cannot read or write outside them

even under prompt injection.

Document generation degrades gracefully: if python-docx / openpyxl /

python-pptx / reportlab aren't installed, the relevant tool returns a clear

"pip install" message instead of crashing, and plain-text/markdown/csv/html

generation always works.

"""

import csv
import io
import os
import re
import subprocess
from datetime import date
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
            f"path '{path}' is outside the allowed directories ({roots})"
        )
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
def _sections_from_md(text: str) -> list:
    """Split markdown into create_document sections (## headings → slides/sections)."""
    sections = []
    heading = ""
    body = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading or body:
                sections.append({"heading": heading, "body": "\n".join(body).strip()})
            heading = line[3:].strip()
            body = []
        elif line.startswith("# ") and not sections and not heading and not body:
            continue  # top-level title — create_document title arg covers this
        else:
            body.append(line)
    if heading or body:
        sections.append({"heading": heading, "body": "\n".join(body).strip()})
    if not sections and text.strip():
        sections = [{"heading": "", "body": text.strip()}]
    return sections


def create_document(
    filename: str,
    doc_format: str,
    title: str,
    sections: list = None,
    table: dict = None,
    source_path: str = None,
    chart: dict = None,
) -> Path:
    """Create a document in the workspace.

    sections: [{"heading": str, "body": str}]

    source_path: optional workspace .md/.txt/.html to render (preferred for long

      reports — write_file first, then call with source_path to keep tool args small).

    table (optional): {"headers": [...], "rows": [[...], ...]}

    doc_format: docx | xlsx | pptx | pdf | md | html | txt | csv

    Body text understands light markup in docx/pptx: lines starting with

    "- "/"* " become bullets (two leading spaces = sub-bullet), "1. " becomes

    a numbered item, and **text** renders bold.

    """
    fmt = doc_format.lower().lstrip(".")
    target = resolve(_with_ext(filename, fmt), for_write=True)
    builder = _BUILDERS.get(fmt)
    if builder is None:
        raise WorkspaceError(f"unsupported document format '{doc_format}'")
    title = _unescape(title)
    if source_path:
        src = resolve(source_path, must_exist=True)
        text = src.read_text(encoding="utf-8")
        ext = src.suffix.lower()
        if ext in (".md", ".markdown"):
            sections = _sections_from_md(text)
        elif ext in (".txt", ".html", ".htm"):
            sections = [{"heading": "", "body": text}]
        else:
            raise WorkspaceError(
                f"source_path must be .md, .txt, or .html (got '{ext}')"
            )
    sections = [
        {"heading": _unescape(s.get("heading")), "body": _unescape(s.get("body"))}
        for s in (sections or [])
    ]
    if table:
        table = {
            "headers": [_unescape(h) for h in table.get("headers", [])],
            "rows": [[_unescape(c) for c in row] for row in table.get("rows", [])],
        }
    builder(target, title, sections, table, chart)
    return target


def _unescape(text):
    """Fix double-escaped newlines from LLM tool calls.

    Models sometimes emit '\\n' as two literal characters inside JSON string

    args; the parsed value then contains backslash-n text instead of real

    line breaks, which kills bullet/paragraph parsing. Only rewrite when the

    string has NO real newlines (so legit backslashes in normal multi-line

    text, e.g. Windows paths, are left alone)."""
    text = str(text or "")
    # Rewrite when the literal escapes are clearly the intended line breaks:
    # no real newlines at all, OR at least as many literal "\n" as real ones
    # (the mixed-mangling case the old "\n not in text" guard silently missed).
    if text.count("\\n") and text.count("\\n") >= text.count("\n"):
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "    ")
    return text


def _with_ext(filename: str, fmt: str) -> str:
    return filename if filename.lower().endswith(f".{fmt}") else f"{filename}.{fmt}"


def _build_md(target, title, sections, table, chart=None):
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


def _build_txt(target, title, sections, table, chart=None):
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


def _build_csv(target, title, sections, table, chart=None):
    if not (table and table.get("headers")):
        raise WorkspaceError("csv format needs a table with headers and rows")
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(table["headers"])
        writer.writerows(table.get("rows", []))


def _build_html(target, title, sections, table, chart=None):
    out = [
        f"<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{_esc(title)}</title></head><body>",
        f"<h1>{_esc(title)}</h1>",
    ]
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
            out.append(
                "<tr>" + "".join(f"<td>{_esc(str(c))}</td>" for c in row) + "</tr>"
            )
        out.append("</tbody></table>")
    out.append("</body></html>")
    target.write_text("\n".join(out), encoding="utf-8")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---- shared document styling: palette + light markup parsing ----
_PRIMARY = "1F3864"  # dark blue — titles, table headers
_ACCENT = "2E74B5"  # medium blue — secondary headings, rules
_LIGHT = "DCE6F1"  # light blue — banded table rows
_GRAY = "595959"  # subtitles, footers
_BODY_COLOR = "262626"
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_NUM_LINE = re.compile(r"^\d+[.)]\s+")


def _plain(text: str) -> str:
    """Strip **bold** markers for formats that don't render them."""
    return _BOLD_RE.sub(r"\1", str(text or ""))


def _parse_lines(body: str):
    """Classify body lines: yields (kind, level, text).

    kind: 'bullet' ('- '/'* '), 'number' ('1. '), or 'text'. Two or more

    leading spaces on a bullet/number make it a sub-item (level 1).

    """
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        level = 1 if (len(raw) - len(raw.lstrip(" "))) >= 2 else 0
        if stripped[:2] in ("- ", "* "):
            yield "bullet", level, stripped[2:].strip()
        elif _NUM_LINE.match(stripped):
            yield "number", level, _NUM_LINE.sub("", stripped)
        else:
            yield "text", 0, stripped


def _coerce(value):
    """Turn numeric-looking strings into real numbers for spreadsheet cells."""
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return _plain(text)


def _silent_unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _render_chart_png(table, chart, target):
    """Render the chart to a temp PNG next to `target`. Returns path or None."""
    if not chart or not table or not table.get("headers"):
        return None
    from . import charts

    png = str(Path(target).with_name(f".{Path(target).stem}_chart.png"))
    return charts.render(table, chart, png)


def _build_docx(target, title, sections, table, chart=None):
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt, RGBColor
    except ImportError:
        # Zero-dependency fallback — a valid .docx, just plainer styling.
        from . import docgen

        docgen.minimal_docx(target, title, sections, table)
        return
    doc = Document()
    # python-docx's default template opens in "[Compatibility Mode]" in modern
    # Word; declare compatibilityMode 15 (Word 2013+) so it opens as a normal
    # modern document.
    settings = doc.settings.element
    compat = settings.find(qn("w:compat"))
    if compat is None:
        compat = OxmlElement("w:compat")
        settings.append(compat)
    for cs in compat.findall(qn("w:compatSetting")):
        if cs.get(qn("w:name")) == "compatibilityMode":
            compat.remove(cs)
    mode = OxmlElement("w:compatSetting")
    mode.set(qn("w:name"), "compatibilityMode")
    mode.set(qn("w:uri"), "http://schemas.microsoft.com/office/word")
    mode.set(qn("w:val"), "15")
    compat.append(mode)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15
    for style_name, size, color in (
        ("Heading 1", 14, _PRIMARY),
        ("Heading 2", 12, _ACCENT),
    ):
        st = doc.styles[style_name]
        st.font.name = "Calibri"
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor.from_string(color)

    def rich(par, text):
        """Add runs to a paragraph, rendering **segments** bold."""
        pos = 0
        for m in _BOLD_RE.finditer(text):
            if m.start() > pos:
                par.add_run(text[pos : m.start()])
            par.add_run(m.group(1)).bold = True
            pos = m.end()
        if pos < len(text):
            par.add_run(text[pos:])

    def shade(cell, fill):
        el = OxmlElement("w:shd")
        el.set(qn("w:val"), "clear")
        el.set(qn("w:fill"), fill)
        cell._tc.get_or_add_tcPr().append(el)

    # Optional brand logo on the cover, centered above the title.
    if config.BRAND_LOGO and os.path.isfile(config.BRAND_LOGO):
        try:
            from docx.shared import Inches

            lpar = doc.add_paragraph()
            lpar.alignment = WD_ALIGN_PARAGRAPH.CENTER
            lpar.add_run().add_picture(config.BRAND_LOGO, width=Inches(1.3))
        except Exception:
            pass  # a bad/oversized logo must never break document generation

    # Title block: large colored title, gray date line, thin rule under it.
    tpar = doc.add_paragraph()
    trun = tpar.add_run(_plain(title))
    trun.font.size = Pt(24)
    trun.font.bold = True
    trun.font.color.rgb = RGBColor.from_string(_PRIMARY)
    dpar = doc.add_paragraph()
    drun = dpar.add_run(date.today().strftime("%d %B %Y"))
    drun.font.size = Pt(10)
    drun.font.color.rgb = RGBColor.from_string(_GRAY)
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), _ACCENT)
    border.append(bottom)
    dpar._p.get_or_add_pPr().append(border)

    # Static table of contents for multi-section reports — always renders (a
    # Word TOC field stays blank until the user updates fields).
    headings = [s["heading"] for s in sections if s.get("heading")]
    if len(headings) >= 3:
        toc_title = doc.add_paragraph()
        tr = toc_title.add_run("Daftar Isi")
        tr.font.bold = True
        tr.font.size = Pt(13)
        tr.font.color.rgb = RGBColor.from_string(_ACCENT)
        for i, h in enumerate(headings, 1):
            item = doc.add_paragraph()
            item.paragraph_format.left_indent = Pt(12)
            item.add_run(f"{i}.  {_plain(h)}")
        doc.add_page_break()

    for s in sections:
        if s.get("heading"):
            doc.add_heading(_plain(s["heading"]), level=1)
        for kind, level, text in _parse_lines(s.get("body") or ""):
            if kind == "bullet":
                par = doc.add_paragraph(
                    style="List Bullet 2" if level else "List Bullet"
                )
                rich(par, text)
            elif kind == "number":
                par = doc.add_paragraph(
                    style="List Number 2" if level else "List Number"
                )
                rich(par, text)
            else:
                par = doc.add_paragraph()
                rich(par, text)
                # Left-aligned (not justified): justification on short report
                # lines opens ugly inter-word "rivers". Left reads cleaner.
                par.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    if table and table.get("headers"):
        headers = table["headers"]
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        for i, h in enumerate(headers):
            cell = t.rows[0].cells[i]
            run = cell.paragraphs[0].add_run(_plain(h))
            run.font.bold = True
            run.font.color.rgb = RGBColor.from_string("FFFFFF")
            shade(cell, _PRIMARY)
        for r, row in enumerate(table.get("rows", [])):
            cells = t.add_row().cells
            for i, c in enumerate(row[: len(headers)]):
                cells[i].text = _plain(c)
                if r % 2 == 1:
                    shade(cells[i], _LIGHT)
    # Optional chart rendered from the table, embedded after it.
    chart_png = _render_chart_png(table, chart, target)
    if chart_png:
        try:
            from docx.shared import Inches

            cpar = doc.add_paragraph()
            cpar.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cpar.add_run().add_picture(chart_png, width=Inches(6.0))
        except Exception:
            pass
        finally:
            _silent_unlink(chart_png)
    # Footer: centered page number field.
    fpar = doc.sections[0].footer.paragraphs[0]
    fpar.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    fpar._p.append(fld)
    doc.save(str(target))


def _add_xlsx_chart(ws, headers, rows, chart, col_letter):
    """Add a native Excel chart from the table. No-op on any problem so the
    workbook is always saved."""
    if not chart or not rows:
        return
    try:
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference

        kind = str(chart.get("type", "bar")).lower()
        lcol = int(chart.get("label_col", 0))
        vcol = int(chart.get("value_col", 1 if len(headers) > 1 else 0))
        n = len(rows)
        cls = {"line": LineChart, "pie": PieChart}.get(kind, BarChart)
        obj = cls()
        obj.title = chart.get("title") or (headers[vcol] if vcol < len(headers) else None)
        data = Reference(ws, min_col=vcol + 1, min_row=1, max_row=n + 1)
        cats = Reference(ws, min_col=lcol + 1, min_row=2, max_row=n + 1)
        obj.add_data(data, titles_from_data=True)
        obj.set_categories(cats)
        obj.height, obj.width = 8, 16
        ws.add_chart(obj, f"{col_letter(len(headers) + 2)}2")
    except Exception:
        pass


def _build_xlsx(target, title, sections, table, chart=None):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        from . import docgen

        docgen.minimal_xlsx(target, title, sections, table)
        return
    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\[\]:*?/\\]", "-", _plain(title))[:31] or "Sheet1"
    thin = Side(style="thin", color="BFBFBF")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    if table and table.get("headers"):
        headers = [_plain(h) for h in table["headers"]]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(name="Calibri", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=_PRIMARY)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = box
        for row in table.get("rows", []):
            ws.append([_coerce(c) for c in row])
        band = PatternFill("solid", fgColor="F2F6FA")
        for r in range(2, ws.max_row + 1):
            for c in range(1, len(headers) + 1):
                cell = ws.cell(row=r, column=c)
                cell.border = box
                if r % 2 == 1:
                    cell.fill = band
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for c in range(1, len(headers) + 1):
            width = max(
                len(str(ws.cell(row=r, column=c).value or ""))
                for r in range(1, ws.max_row + 1)
            )
            ws.column_dimensions[get_column_letter(c)].width = min(
                max(width + 3, 10), 50
            )
        _add_xlsx_chart(ws, headers, table.get("rows", []), chart, get_column_letter)
    else:
        ws["A1"] = _plain(title)
        ws["A1"].font = Font(bold=True, size=14, color=_PRIMARY)
        r = 3
        for s in sections:
            if s.get("heading"):
                cell = ws.cell(row=r, column=1, value=_plain(s["heading"]))
                cell.font = Font(bold=True, color=_ACCENT)
                r += 1
            for _kind, _level, text in _parse_lines(s.get("body") or ""):
                ws.cell(row=r, column=1, value=_plain(text))
                r += 1
            r += 1
        ws.column_dimensions["A"].width = 90
    wb.save(str(target))


def _build_pptx(target, title, sections, table, chart=None):
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Inches, Pt
    except ImportError:
        # Zero-dependency fallback — a valid .pptx, plainer styling. PPTX must
        # never be the one format that hard-fails when the lib is absent.
        from . import docgen

        docgen.minimal_pptx(target, title, sections, table)
        return
    prs = Presentation()
    prs.slide_width = Inches(13.333)  # 16:9
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    primary = RGBColor.from_string(_PRIMARY)
    accent = RGBColor.from_string(_ACCENT)
    gray = RGBColor.from_string(_GRAY)
    body_color = RGBColor.from_string(_BODY_COLOR)

    def bar(slide, x, y, w, h, color):
        shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = color
        shape.line.fill.background()
        shape.shadow.inherit = False

    def textframe(slide, x, y, w, h):
        box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        box.text_frame.word_wrap = True
        return box.text_frame

    def rich(par, text, size, color, bold=False):
        pos = 0
        for m in _BOLD_RE.finditer(text):
            for chunk, chunk_bold in (
                (text[pos : m.start()], bold),
                (m.group(1), True),
            ):
                if chunk:
                    run = par.add_run()
                    run.text = chunk
                    run.font.size = Pt(size)
                    run.font.bold = chunk_bold
                    run.font.color.rgb = color
            pos = m.end()
        if pos < len(text):
            run = par.add_run()
            run.text = text[pos:]
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = color

    # ----- title slide -----
    slide = prs.slides.add_slide(blank)
    bar(slide, 0, 0, 0.3, 7.5, primary)
    bar(slide, 0.3, 0, 0.07, 7.5, accent)
    tf = textframe(slide, 1.0, 2.7, 11.6, 1.8)
    rich(tf.paragraphs[0], _plain(title), 40, primary, bold=True)
    sub = textframe(slide, 1.0, 4.4, 11.6, 0.6)
    rich(sub.paragraphs[0], date.today().strftime("%d %B %Y"), 16, gray)

    def content_slide(heading):
        slide = prs.slides.add_slide(blank)
        bar(slide, 0, 0, 13.333, 0.12, accent)
        htf = textframe(slide, 0.6, 0.45, 12.1, 0.9)
        rich(htf.paragraphs[0], _plain(heading), 28, primary, bold=True)
        return slide

    max_lines = 8
    for s in sections:
        lines = list(_parse_lines(s.get("body") or ""))
        chunks = [
            lines[i : i + max_lines] for i in range(0, len(lines), max_lines)
        ] or [[]]
        counter = 0
        for n, chunk in enumerate(chunks):
            heading = s.get("heading") or _plain(title)
            if n:
                heading += " (lanjutan)"
            slide = content_slide(heading)
            tf = textframe(slide, 0.9, 1.6, 11.6, 5.4)
            first = True
            for kind, level, text in chunk:
                par = tf.paragraphs[0] if first else tf.add_paragraph()
                first = False
                par.space_after = Pt(10)
                par.level = level
                if kind == "number":
                    counter += 1
                    prefix = f"{counter}.  "
                elif kind == "bullet":
                    prefix = "–  " if level else "•  "
                else:
                    prefix = ""
                rich(par, prefix + text, 16 if level else 18, body_color)
    if table and table.get("headers"):
        headers = table["headers"]
        rows = table.get("rows", [])
        shown = rows[:12]
        slide = content_slide("Data")
        shape = slide.shapes.add_table(
            len(shown) + 1,
            len(headers),
            Inches(0.9),
            Inches(1.7),
            Inches(11.5),
            Inches(0.45 * (len(shown) + 1)),
        )
        tbl = shape.table
        for i, h in enumerate(headers):
            cell = tbl.cell(0, i)
            cell.text = _plain(h)
            cell.fill.solid()
            cell.fill.fore_color.rgb = primary
            for par in cell.text_frame.paragraphs:
                for run in par.runs:
                    run.font.bold = True
                    run.font.size = Pt(14)
                    run.font.color.rgb = RGBColor.from_string("FFFFFF")
        for r, row in enumerate(shown, start=1):
            for i, c in enumerate(row[: len(headers)]):
                cell = tbl.cell(r, i)
                cell.text = _plain(c)
                for par in cell.text_frame.paragraphs:
                    for run in par.runs:
                        run.font.size = Pt(12)
                        run.font.color.rgb = body_color
        if len(rows) > len(shown):
            note = textframe(slide, 0.9, 6.9, 11.5, 0.4)
            rich(
                note.paragraphs[0],
                f"Menampilkan {len(shown)} dari {len(rows)} baris — lengkapnya "
                f"di lampiran xlsx.",
                12,
                gray,
            )

    # Optional chart on its own slide.
    chart_png = _render_chart_png(table, chart, target)
    if chart_png:
        try:
            slide = content_slide("Grafik")
            slide.shapes.add_picture(
                chart_png, Inches(1.5), Inches(1.6), height=Inches(5.2))
        except Exception:
            pass
        finally:
            _silent_unlink(chart_png)
    prs.save(str(target))


def _build_pdf(target, title, sections, table, chart=None):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
        )
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
            flow.append(
                Paragraph(_esc(s["body"]).replace("\n", "<br/>"), styles["BodyText"])
            )
        flow.append(Spacer(1, 8))
    if table and table.get("headers"):
        data = [table["headers"]] + [list(r) for r in table.get("rows", [])]
        tbl = Table(data)
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34495e")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ]
            )
        )
        flow.append(tbl)
    SimpleDocTemplate(str(target), pagesize=A4).build(flow)


_BUILDERS = {
    "md": _build_md,
    "markdown": _build_md,
    "txt": _build_txt,
    "text": _build_txt,
    "csv": _build_csv,
    "html": _build_html,
    "docx": _build_docx,
    "xlsx": _build_xlsx,
    "pptx": _build_pptx,
    "pdf": _build_pdf,
}
_NATIVE_LIBS = {"docx": "docx", "xlsx": "openpyxl", "pptx": "pptx",
                "pdf": "reportlab"}


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


# ---------------- git / repo inspection ----------------
# Mode comes from config.GIT_READ_ONLY (env ENGRAM_GIT_READ_ONLY):
#   1 = read-only subcommands only (safe default)
#   0 = write subcommands also allowed (add, commit, checkout, push, ...)
# Destructive flags are blocked in BOTH modes regardless of toggle.
_GIT_READ_SUBCMDS = {
    "status",
    "log",
    "diff",
    "branch",
    "show",
    "remote",
    "ls-files",
    "shortlog",
}
_GIT_WRITE_SUBCMDS = {
    "add",
    "commit",
    "checkout",
    "restore",
    "reset",
    "stash",
    "tag",
    "fetch",
    "pull",
    "push",
    "merge",
    "rebase",
    "cherry-pick",
    "rm",
}
# Flag-level guard: block destructive patterns no matter the subcommand.
# Each entry is a substring matched against the full argv list (case-insensitive).
_GIT_BLOCKED_PATTERNS = (
    "--force-with-lease",
    "-f-with-lease",
    # bare "--force" / "-f" — matched as separate tokens below
)


def _git_flag_blocked(parts: list) -> str:
    """Return reason string if any flag in parts is destructive, else ''."""
    lowered = [p.lower() for p in parts]
    for pat in _GIT_BLOCKED_PATTERNS:
        if pat in lowered:
            return f"flag '{pat}' is blocked (destructive)"
    # bare --force / -f only dangerous for push; for other subcommands it's fine
    if lowered and lowered[0] in {"push"}:
        if "--force" in lowered or "-f" in lowered:
            return "'git push --force/-f' is blocked (destructive)"
    # reset --hard: subcommand 'reset' with --hard flag
    if lowered and lowered[0] == "reset" and "--hard" in lowered:
        return "'git reset --hard' is blocked (destructive)"
    # clean -fd / -fdx
    if (
        lowered
        and lowered[0] == "clean"
        and any(t in lowered for t in ("-fd", "-fdx", "-df", "-f"))
    ):
        return "'git clean -fd' is blocked (destructive)"
    return ""


def git(repo_path: str, command: str) -> str:
    repo = resolve(repo_path, must_exist=True)
    if not (repo / ".git").exists():
        return f"'{repo_path}' is not a git repository."
    parts = command.split()
    sub = parts[0] if parts else ""
    if not sub:
        return "empty git command"
    # Always-blocked destructive flags (run BEFORE the read-only check
    # so a write-mode user can't slip --force through either).
    blocked = _git_flag_blocked(parts)
    if blocked:
        return f"Refused: {blocked}. To override, run git manually."
    if sub in _GIT_READ_SUBCMDS:
        mode = "read"
    elif sub in _GIT_WRITE_SUBCMDS:
        if config.GIT_READ_ONLY:
            return (
                f"git '{sub}' is a write command but git tool is in "
                f"read-only mode (ENGRAM_GIT_READ_ONLY=1). Flip the env "
                f"var to 0 in .env and restart to allow writes."
            )
        mode = "write"
    else:
        return (
            f"git subcommand '{sub}' is not in the allowed list. "
            f"Read: {', '.join(sorted(_GIT_READ_SUBCMDS))}. "
            f"Write: {', '.join(sorted(_GIT_WRITE_SUBCMDS))}."
        )
    safe = _trim_git(parts)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo)] + safe, capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        return "git is not installed on this machine."
    out = (proc.stdout or proc.stderr)[:6000]
    prefix = f"[git {mode}]"
    body = out.strip() or "(no output)"
    return f"{prefix}\n{body}" if mode == "write" else body


def _trim_git(parts: list) -> list:
    """Cap output-heavy commands so they don't flood the context."""
    if parts and parts[0] == "log" and not any(p.startswith("-n") for p in parts):
        return parts + ["-n", "20", "--oneline"]
    return parts


# ---------------- shell (opt-in) ----------------
def run_shell(command: str) -> str:
    ensure_workspace()
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=config.SHELL_TIMEOUT_SEC,
        cwd=str(config.WORKSPACE_DIR),
    )
    out = (proc.stdout or "")[-4000:]
    err = (proc.stderr or "")[-2000:]
    return f"exit={proc.returncode}\nstdout:\n{out}\nstderr:\n{err}"
