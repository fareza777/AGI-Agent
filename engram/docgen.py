"""Zero-dependency document generators.

DOCX and XLSX are just ZIP packages of XML; PDF is a plain-text object graph.
These builders produce valid, opens-in-Word/Excel/any-viewer files using only
the standard library — so create_document NEVER fails because python-docx /
openpyxl / reportlab aren't installed. The richer libraries are still
preferred when present (desktop.py tries them first); these are the always-
available floor.
"""

import textwrap
import zipfile
from xml.sax.saxutils import escape


# ====================== DOCX ======================

_DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOCX_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""


def _w_par(text: str, bold: bool = False, half_points: int = 22) -> str:
    """One paragraph; size is in half-points (22 = 11pt)."""
    rpr = f"<w:rPr>{'<w:b/>' if bold else ''}<w:sz w:val=\"{half_points}\"/></w:rPr>"
    runs = []
    for i, line in enumerate(text.split("\n")):
        if i:
            runs.append("<w:br/>")
        runs.append(f"<w:t xml:space=\"preserve\">{escape(line)}</w:t>")
    return f"<w:p><w:pPr>{rpr}</w:pPr><w:r>{rpr}{''.join(runs)}</w:r></w:p>"


_W_BORDER = ('<w:{side} w:val="single" w:sz="4" w:space="0" w:color="999999"/>')
_W_TBL_BORDERS = ("<w:tblBorders>"
                  + "".join(_W_BORDER.format(side=s) for s in
                            ("top", "left", "bottom", "right", "insideH", "insideV"))
                  + "</w:tblBorders>")


def _w_table(headers: list, rows: list) -> str:
    def cell(text, bold=False):
        return f"<w:tc>{_w_par(str(text), bold=bold)}</w:tc>"

    out = [f"<w:tbl><w:tblPr>{_W_TBL_BORDERS}</w:tblPr>"]
    out.append("<w:tr>" + "".join(cell(h, bold=True) for h in headers) + "</w:tr>")
    for row in rows:
        cells = list(row) + [""] * (len(headers) - len(row))
        out.append("<w:tr>" + "".join(cell(c) for c in cells[:len(headers)]) + "</w:tr>")
    out.append("</w:tbl><w:p/>")
    return "".join(out)


def minimal_docx(target, title: str, sections: list, table: dict = None):
    body = [_w_par(title, bold=True, half_points=40)]
    for s in sections or []:
        if s.get("heading"):
            body.append(_w_par(s["heading"], bold=True, half_points=28))
        if s.get("body"):
            for para in str(s["body"]).split("\n\n"):
                body.append(_w_par(para))
    if table and table.get("headers"):
        body.append(_w_table(table["headers"], table.get("rows", [])))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}</w:body></w:document>")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        z.writestr("_rels/.rels", _DOCX_RELS)
        z.writestr("word/_rels/document.xml.rels", _DOCX_DOC_RELS)
        z.writestr("word/document.xml", document)


# ====================== XLSX ======================

_XLSX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

_XLSX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_XLSX_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>"""

_XLSX_WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


def _col_letter(idx: int) -> str:
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def _xlsx_cell(ref: str, value) -> str:
    text = str(value)
    try:
        float(text)
        return f'<c r="{ref}"><v>{text}</v></c>'
    except ValueError:
        return (f'<c r="{ref}" t="inlineStr"><is>'
                f'<t xml:space="preserve">{escape(text)}</t></is></c>')


def minimal_xlsx(target, title: str, sections: list, table: dict = None):
    rows_data = []
    if table and table.get("headers"):
        rows_data.append(table["headers"])
        rows_data.extend(table.get("rows", []))
    else:
        rows_data.append([title])
        for s in sections or []:
            if s.get("heading"):
                rows_data.append([s["heading"]])
            if s.get("body"):
                rows_data.append([str(s["body"])])
    xml_rows = []
    for r, row in enumerate(rows_data, start=1):
        cells = "".join(_xlsx_cell(f"{_col_letter(c)}{r}", v)
                        for c, v in enumerate(row))
        xml_rows.append(f'<row r="{r}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f"<sheetData>{''.join(xml_rows)}</sheetData></worksheet>")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _XLSX_CONTENT_TYPES)
        z.writestr("_rels/.rels", _XLSX_RELS)
        z.writestr("xl/workbook.xml", _XLSX_WORKBOOK)
        z.writestr("xl/_rels/workbook.xml.rels", _XLSX_WB_RELS)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


# ====================== PDF ======================

_PAGE_W, _PAGE_H = 595, 842        # A4 in points
_MARGIN, _BOTTOM = 50, 60


def _pdf_escape(text: str) -> str:
    return (text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)"))


def _flatten_pdf_lines(title, sections, table):
    """[(text, font, size)] — F2 is bold, F1 regular."""
    lines = [(title, "F2", 18), ("", "F1", 10)]
    for s in sections or []:
        if s.get("heading"):
            lines.append((s["heading"], "F2", 13))
        if s.get("body"):
            for para in str(s["body"]).split("\n"):
                lines.extend((w, "F1", 10) for w in
                             textwrap.wrap(para, width=95) or [""])
        lines.append(("", "F1", 10))
    if table and table.get("headers"):
        lines.append((" | ".join(str(h) for h in table["headers"]), "F2", 10))
        lines.append(("-" * 90, "F1", 10))
        for row in table.get("rows", []):
            lines.extend((w, "F1", 10) for w in
                         textwrap.wrap(" | ".join(str(c) for c in row), width=95))
    return lines


def minimal_pdf(target, title: str, sections: list, table: dict = None):
    # Paginate lines into per-page content streams.
    pages, current, y = [], [], _PAGE_H - _MARGIN
    for text, font, size in _flatten_pdf_lines(title, sections, table):
        step = size + 5
        if y - step < _BOTTOM:
            pages.append(current)
            current, y = [], _PAGE_H - _MARGIN
        current.append(f"BT /{font} {size} Tf {_MARGIN} {y - size} Td "
                       f"({_pdf_escape(text)}) Tj ET")
        y -= step
    pages.append(current)

    # Object layout: 1 catalog, 2 pages, 3+4 fonts, then (page, content)*N.
    n_pages = len(pages)
    objs = {}
    first_page_obj = 5
    kids = " ".join(f"{first_page_obj + 2 * i} 0 R" for i in range(n_pages))
    objs[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>"
    objs[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objs[4] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
    for i, page_ops in enumerate(pages):
        page_no = first_page_obj + 2 * i
        stream = "\n".join(page_ops)
        objs[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {page_no + 1} 0 R >>")
        objs[page_no + 1] = ("STREAM", stream)

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        body = objs[num]
        if isinstance(body, tuple):
            data = body[1].encode("latin-1", "replace")
            out += (f"{num} 0 obj\n<< /Length {len(data)} >>\nstream\n"
                    .encode("latin-1"))
            out += data + b"\nendstream\nendobj\n"
        else:
            out += f"{num} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    count = max(objs) + 1
    out += f"xref\n0 {count}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, count):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {count} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode()
    with open(target, "wb") as fh:
        fh.write(bytes(out))
