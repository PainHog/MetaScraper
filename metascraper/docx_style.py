"""Reusable python-docx styling helpers for a clean, professional look.

These helpers centralise the low-level OOXML fiddling (cell shading, borders,
page numbers) so the report generators read like plain document descriptions.
"""

from __future__ import annotations

from typing import Iterable, Optional

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Twips


# --- Brand palette (deep slate + warm accent) ---------------------------------
INK = RGBColor(0x1F, 0x2A, 0x37)        # near-black slate for body text
BRAND = RGBColor(0x1B, 0x3A, 0x5B)      # deep navy for headings / bars
ACCENT = RGBColor(0xC2, 0x6B, 0x2D)     # warm amber accent
MUTED = RGBColor(0x6B, 0x72, 0x80)      # muted grey for captions
LIGHT_ROW = "F3F5F8"                     # very light blue-grey table fill
HEADER_FILL = "1B3A5B"                   # navy header fill (hex, no #)
LABEL_FILL = "E8ECF1"                    # label column fill
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

BODY_FONT = "Calibri"


def new_document() -> Document:
    """Create a Document with MetaScraper's base styles applied."""
    document = Document()
    _apply_base_styles(document)
    _widen_margins(document)
    return document


def _apply_base_styles(document: Document) -> None:
    normal = document.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.08

    for level, size in ((1, 15), (2, 12.5), (3, 11)):
        style_name = f"Heading {level}"
        if style_name in document.styles:
            style = document.styles[style_name]
            style.font.name = BODY_FONT
            style.font.size = Pt(size)
            style.font.bold = True
            style.font.color.rgb = BRAND


def _widen_margins(document: Document) -> None:
    for section in document.sections:
        section.top_margin = Twips(1080)     # 0.75"
        section.bottom_margin = Twips(1080)
        section.left_margin = Twips(1152)    # 0.8"
        section.right_margin = Twips(1152)


def set_cell_background(cell, hex_fill: str) -> None:
    """Fill a table cell with a solid colour (hex string without ``#``)."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(shd)


def set_cell_margins(cell, top=60, bottom=60, left=110, right=110) -> None:
    """Set internal cell padding in twips for a bit of breathing room."""
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = OxmlElement("w:tcMar")
    for edge, value in (("top", top), ("bottom", bottom), ("start", left),
                        ("end", right), ("left", left), ("right", right)):
        node = OxmlElement(f"w:{edge}")
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    tc_pr.append(margins)


def style_table_borders(table, color: str = "D6DBE2", size: int = 4) -> None:
    """Apply light, consistent borders to every side of a table."""
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(size))
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)
        borders.append(element)
    tbl_pr.append(borders)


def _set_run(run, *, bold=False, size=None, color=None, font=BODY_FONT) -> None:
    run.font.name = font
    run.font.bold = bold
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color


def add_title_block(
    document: Document,
    title: str,
    subtitle: Optional[str] = None,
    eyebrow: Optional[str] = None,
) -> None:
    """Add a branded title block: small eyebrow, big title, thin accent rule."""
    if eyebrow:
        p = document.add_paragraph()
        run = p.add_run(eyebrow.upper())
        _set_run(run, bold=True, size=9, color=ACCENT)
        run.font.name = BODY_FONT
        _letter_spacing(run, 60)
        p.paragraph_format.space_after = Pt(2)

    title_p = document.add_paragraph()
    title_run = title_p.add_run(title)
    _set_run(title_run, bold=True, size=22, color=BRAND)
    title_p.paragraph_format.space_after = Pt(2)

    if subtitle:
        sub_p = document.add_paragraph()
        sub_run = sub_p.add_run(subtitle)
        _set_run(sub_run, size=11, color=MUTED)
        sub_p.paragraph_format.space_after = Pt(6)

    _add_horizontal_rule(document, ACCENT)


def _letter_spacing(run, twips: int) -> None:
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:val"), str(twips))
    run._element.get_or_add_rPr().append(spacing)


def _add_horizontal_rule(document: Document, color: RGBColor) -> None:
    p = document.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(10)
    p_pr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "18")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "%02X%02X%02X" % (color[0], color[1], color[2]))
    borders.append(bottom)
    p_pr.append(borders)


def add_section_heading(document: Document, text: str) -> None:
    """Add a compact section heading with an accent tick."""
    p = document.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    tick = p.add_run("▎ ")
    _set_run(tick, bold=True, size=12, color=ACCENT)
    run = p.add_run(text)
    _set_run(run, bold=True, size=12.5, color=BRAND)


def add_key_value_table(document, rows: Iterable, label_width_in=2.1):
    """Add a two-column key/value table with shaded labels.

    ``rows`` is an iterable of ``(label, value)`` pairs. Empty values are shown
    as an em dash so the table stays visually complete.
    """
    rows = [(str(k), ("—" if v in (None, "") else str(v))) for k, v in rows]
    table = document.add_table(rows=len(rows), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    style_table_borders(table)

    for i, (label, value) in enumerate(rows):
        label_cell, value_cell = table.rows[i].cells
        label_cell.width = Twips(int(label_width_in * 1440))
        value_cell.width = Twips(int(4.3 * 1440))

        set_cell_background(label_cell, LABEL_FILL)
        for cell in (label_cell, value_cell):
            set_cell_margins(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        lp = label_cell.paragraphs[0]
        lp.paragraph_format.space_after = Pt(0)
        lrun = lp.add_run(label)
        _set_run(lrun, bold=True, size=9.5, color=BRAND)

        vp = value_cell.paragraphs[0]
        vp.paragraph_format.space_after = Pt(0)
        vrun = vp.add_run(value)
        _set_run(vrun, size=9.5, color=INK)

    return table


def add_data_table(document, headers, rows, col_widths_in=None):
    """Add a full-width data table with a navy header row and zebra striping."""
    table = document.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    style_table_borders(table)

    header_cells = table.rows[0].cells
    for idx, header in enumerate(headers):
        cell = header_cells[idx]
        set_cell_background(cell, HEADER_FILL)
        set_cell_margins(cell)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(str(header))
        _set_run(run, bold=True, size=9, color=WHITE)

    for r_idx, row in enumerate(rows):
        cells = table.add_row().cells
        fill = LIGHT_ROW if r_idx % 2 == 0 else None
        for c_idx, value in enumerate(row):
            cell = cells[c_idx]
            if fill:
                set_cell_background(cell, fill)
            set_cell_margins(cell)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            text = "—" if value in (None, "") else str(value)
            run = p.add_run(text)
            _set_run(run, size=8.5, color=INK)

    if col_widths_in:
        for idx, width in enumerate(col_widths_in):
            for row in table.rows:
                row.cells[idx].width = Twips(int(width * 1440))
    return table


def add_caption(document, text: str) -> None:
    p = document.add_paragraph()
    run = p.add_run(text)
    _set_run(run, size=9, color=MUTED)
    run.italic = True
    p.paragraph_format.space_after = Pt(4)


def add_footer(document: Document, left_text: str) -> None:
    """Add a footer with left-aligned label and a right-aligned page number."""
    section = document.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0]
    paragraph.text = ""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT

    left = paragraph.add_run(left_text + "\t")
    _set_run(left, size=8, color=MUTED)

    # Add tab stops so the page number sits flush right.
    tab = paragraph.add_run("Page ")
    _set_run(tab, size=8, color=MUTED)
    _add_page_field(paragraph, "PAGE")
    of = paragraph.add_run(" of ")
    _set_run(of, size=8, color=MUTED)
    _add_page_field(paragraph, "NUMPAGES")


def _add_page_field(paragraph, field_name: str) -> None:
    run = paragraph.add_run()
    _set_run(run, size=8, color=MUTED)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {field_name} "
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)


def set_landscape(document: Document) -> None:
    """Switch the document's first section to landscape orientation."""
    from docx.enum.section import WD_ORIENT

    section = document.sections[0]
    new_width, new_height = section.page_height, section.page_width
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = new_width
    section.page_height = new_height
