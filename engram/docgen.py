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


# ====================== PPTX ======================
# A .pptx is an OOXML package: a presentation part that references a slide
# master, which references a layout and a theme, plus one part per slide.
# This builds the minimal valid chain (PowerPoint / LibreOffice / Google
# Slides all open it) so pptx is never the one format that hard-fails when
# python-pptx isn't installed.

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_EMU_IN = 914400
_SLIDE_W = int(13.333 * _EMU_IN)   # 16:9
_SLIDE_H = int(7.5 * _EMU_IN)

_PPTX_PRIMARY = "1F3864"
_PPTX_ACCENT = "2E74B5"
_PPTX_BODY = "262626"
_PPTX_GRAY = "595959"

_PPTX_THEME = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="%(a)s" name="Engram"><a:themeElements>
<a:clrScheme name="Engram"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
<a:dk2><a:srgbClr val="1F3864"/></a:dk2><a:lt2><a:srgbClr val="EEECE1"/></a:lt2>
<a:accent1><a:srgbClr val="2E74B5"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2>
<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4>
<a:accent5><a:srgbClr val="4472C4"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6>
<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink>
</a:clrScheme>
<a:fontScheme name="Engram"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>
<a:fmtScheme name="Engram">
<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
<a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>
</a:fmtScheme></a:themeElements></a:theme>""" % {"a": _A}

_PPTX_EMPTY_TREE = (
    '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
    '</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree>')

_PPTX_MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldMaster xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"><p:cSld>'
    f'{_PPTX_EMPTY_TREE}</p:cSld>'
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
    'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
    'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/>'
    '</p:sldLayoutIdLst></p:sldMaster>')

_PPTX_LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<p:sldLayout xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}" type="blank" '
    'preserve="1"><p:cSld name="Blank">'
    f'{_PPTX_EMPTY_TREE}</p:cSld>'
    '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')


def _pptx_paragraph(text, size, bold=False, color=_PPTX_BODY, bullet=False):
    if not text:
        return "<a:p/>"
    bu = '<a:buChar char="&#8226;"/>' if bullet else "<a:buNone/>"
    indent = ' marL="285750" indent="-285750"' if bullet else ""
    return (f'<a:p><a:pPr{indent}>{bu}</a:pPr><a:r><a:rPr lang="en-US" '
            f'sz="{int(size * 100)}" b="{1 if bold else 0}" dirty="0">'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:rPr>'
            f'<a:t>{escape(text)}</a:t></a:r></a:p>')


def _pptx_shape(shape_id, name, x, y, cx, cy, paragraphs):
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/>'
            '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            '<p:txBody><a:bodyPr wrap="square"><a:normAutofit/></a:bodyPr>'
            f'<a:lstStyle/>{"".join(paragraphs)}</p:txBody></p:sp>')


def _pptx_slide_xml(shapes):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}"><p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f'{"".join(shapes)}</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


def _pptx_body_paragraphs(body):
    """Each non-empty line becomes a paragraph; '- '/'* ' lines become bullets."""
    paras = []
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line[:2] in ("- ", "* "):
            paras.append(_pptx_paragraph(line[2:].strip(), 18, bullet=True))
        else:
            paras.append(_pptx_paragraph(line, 18))
    return paras


def _pptx_slides(title, sections, table):
    """Return a list of slide XML strings (title slide first)."""
    slides = []
    # Title slide.
    slides.append(_pptx_slide_xml([
        _pptx_shape(2, "Title", int(_EMU_IN), int(2.6 * _EMU_IN),
                    int(11.3 * _EMU_IN), int(1.8 * _EMU_IN),
                    [_pptx_paragraph(title, 40, bold=True, color=_PPTX_PRIMARY)]),
    ]))
    for s in sections or []:
        heading = s.get("heading") or title
        shapes = [_pptx_shape(2, "Heading", int(0.6 * _EMU_IN), int(0.4 * _EMU_IN),
                              int(12.1 * _EMU_IN), int(0.9 * _EMU_IN),
                              [_pptx_paragraph(heading, 28, bold=True,
                                               color=_PPTX_PRIMARY)])]
        body = _pptx_body_paragraphs(s.get("body"))
        if body:
            shapes.append(_pptx_shape(3, "Body", int(0.8 * _EMU_IN),
                                      int(1.6 * _EMU_IN), int(11.7 * _EMU_IN),
                                      int(5.4 * _EMU_IN), body))
        slides.append(_pptx_slide_xml(shapes))
    if table and table.get("headers"):
        headers = table["headers"]
        lines = [" | ".join(str(h) for h in headers)]
        for row in table.get("rows", [])[:12]:
            lines.append(" | ".join(str(c) for c in row))
        body = [_pptx_paragraph(l, 16) for l in lines]
        slides.append(_pptx_slide_xml([
            _pptx_shape(2, "Heading", int(0.6 * _EMU_IN), int(0.4 * _EMU_IN),
                        int(12.1 * _EMU_IN), int(0.9 * _EMU_IN),
                        [_pptx_paragraph("Data", 28, bold=True, color=_PPTX_PRIMARY)]),
            _pptx_shape(3, "Table", int(0.8 * _EMU_IN), int(1.6 * _EMU_IN),
                        int(11.7 * _EMU_IN), int(5.4 * _EMU_IN), body),
        ]))
    return slides


def minimal_pptx(target, title: str, sections: list, table: dict = None):
    slides = _pptx_slides(title or "Presentation", sections, table)
    n = len(slides)

    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
    ]
    for i in range(1, n + 1):
        content_types.append(
            f'<Override PartName="/ppt/slides/slide{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
    content_types.append("</Types>")

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        '</Relationships>')

    # presentation.xml.rels: rId1 -> master, rId2.. -> slides.
    pres_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>']
    sld_ids = []
    for i in range(1, n + 1):
        rid = f"rId{i + 1}"
        pres_rels.append(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>')
        sld_ids.append(f'<p:sldId id="{255 + i}" r:id="{rid}"/>')
    pres_rels.append("</Relationships>")

    presentation = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:presentation xmlns:a="{_A}" xmlns:r="{_R}" xmlns:p="{_P}">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>{"".join(sld_ids)}</p:sldIdLst>'
        f'<p:sldSz cx="{_SLIDE_W}" cy="{_SLIDE_H}"/>'
        '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>')

    master_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>'
        '</Relationships>')

    layout_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
        '</Relationships>')

    slide_rel = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        '</Relationships>')

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(content_types))
        z.writestr("_rels/.rels", root_rels)
        z.writestr("ppt/presentation.xml", presentation)
        z.writestr("ppt/_rels/presentation.xml.rels", "".join(pres_rels))
        z.writestr("ppt/slideMasters/slideMaster1.xml", _PPTX_MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        z.writestr("ppt/slideLayouts/slideLayout1.xml", _PPTX_LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels)
        z.writestr("ppt/theme/theme1.xml", _PPTX_THEME)
        for i, slide in enumerate(slides, start=1):
            z.writestr(f"ppt/slides/slide{i}.xml", slide)
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", slide_rel)
