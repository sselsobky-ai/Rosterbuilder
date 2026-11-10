"""Roster workbook (SCHEDULE, CHECKS, HOURS, COVERAGE, TIMELINE) and PDF export - BUILD_GUIDE section 7."""

from __future__ import annotations

import os
import re
import textwrap
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .checks import CheckReport, run_checks
from .io_xlsx import RosterInputs
from .model import Roster, vba_weekday

# colour coding (hex without #)
C_OT = "FF8080"
C_FIXED = "A9D08E"
C_HELPER = "9DC3E6"
C_LEAVE = "CDB4E8"
C_OFF = "D9D9D9"
C_HEAD = "1F3864"
C_SUB = "D6DCE4"
C_BELOW = "FF8080"
C_ABOVE = "F4B183"
C_GOOD = "C6E0B4"

THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
COL_W = 17
WRAP_CHARS = 14  # conservative characters per line for COL_W at 9 pt
LINE_PT = 12.5


def _up(s) -> str:
    return (s or "").strip().upper()


def _fill(hex_: str) -> PatternFill:
    return PatternFill("solid", start_color=hex_, end_color=hex_)


@dataclass
class Cell:
    """One schedule cell: text and kind (ot / fixed / helper / leave / off / work / empty)."""

    text: str = ""
    kind: str = "empty"


def build_cells(inputs: RosterInputs, roster: Roster, report: CheckReport | None = None) -> dict[tuple[str, date], Cell]:
    """Text and colour class of every (employee name, date) cell of the SCHEDULE."""
    st = inputs.settings
    shifts = {_up(s.code): s for s in inputs.shifts}
    per = {(a.emp_key, a.date): a for a in roster.assignments}
    fixed = {(_up(f.emp), f.date, _up(f.shift)) for f in inputs.fixed}
    leave = set()
    for lv in inputs.leave:
        d = lv.frm
        while d <= lv.to:
            leave.add((_up(lv.emp), d))
            d += timedelta(days=1)
    out: dict[tuple[str, date], Cell] = {}
    first = st.first_day or st.month_start
    for e in inputs.employees:
        if not (e.name.strip() and e.active and e.home):
            continue
        b = (roster.blocks or {}).get(e.key)
        for i in range(st.days_in_month):
            d = first + timedelta(days=i)
            a = per.get((e.key, d))
            if a is not None:
                txt = a.text(shifts.get(_up(a.shift_code)))
                if a.is_ot or a.ext_total_h:
                    kind = "ot"
                elif (e.key, d, _up(a.shift_code)) in fixed:
                    kind = "fixed"
                elif a.is_helper:
                    kind = "helper"
                else:
                    kind = "work"
                out[(e.name, d)] = Cell(txt, kind)
            elif (e.key, d) in leave:
                out[(e.name, d)] = Cell("LEAVE", "leave")
            elif (b is not None and b.is_block_day(d, st.weekday_names)) or (e.key, d) in getattr(roster, "off_locked", ()):
                out[(e.name, d)] = Cell("OFF", "off")
            else:
                out[(e.name, d)] = Cell("", "empty")
    return out


def est_lines(text: str, chars: int = WRAP_CHARS) -> int:
    """Estimated wrapped line count of a cell text."""
    if not text:
        return 1
    return max(1, len(textwrap.wrap(text, width=chars, break_long_words=True)) or 1)


KIND_FILL = {"ot": C_OT, "fixed": C_FIXED, "helper": C_HELPER, "leave": C_LEAVE, "off": C_OFF}
LEGEND = [("Overtime / extension", C_OT), ("Fixed shift", C_FIXED), ("Helper at other reception", C_HELPER),
          ("Leave", C_LEAVE), ("Day off (OFF)", C_OFF)]


def _active(inputs: RosterInputs):
    return [e for e in inputs.employees if e.name.strip() and e.active and e.home]


def _title(inputs: RosterInputs, roster: Roster) -> str:
    st = inputs.settings
    first = st.first_day or st.month_start
    return f"Roster {first:%B %Y}  -  generated {datetime.now():%Y-%m-%d %H:%M}  -  solver {roster.status or 'n/a'}"


# =============================================================================== workbook
def _sheet_schedule(wb: Workbook, inputs: RosterInputs, roster: Roster, report: CheckReport) -> None:
    ws = wb.active
    ws.title = "SCHEDULE"
    st = inputs.settings
    emps = _active(inputs)
    cells = build_cells(inputs, roster, report)
    ncol = 2 + len(emps)
    ws.cell(1, 1, _title(inputs, roster)).font = Font(bold=True, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(ncol, 2))
    ws.row_dimensions[1].height = 22
    hdr_font = Font(bold=True, color="FFFFFF")
    for r, label in ((3, "Reception"), (4, "Gender"), (5, "Employee")):
        c = ws.cell(r, 1, label if r < 5 else "Date")
        ws.cell(r, 2, "" if r < 5 else "Day")
        for col in (1, 2):
            x = ws.cell(r, col)
            x.font, x.fill, x.border = hdr_font, _fill(C_HEAD), BORDER
            x.alignment = Alignment(horizontal="center", vertical="center")
        for j, e in enumerate(emps):
            val = {3: e.home, 4: e.gender, 5: e.name}[r]
            x = ws.cell(r, 3 + j, val)
            x.font, x.fill, x.border = hdr_font if r == 5 else Font(bold=True), _fill(C_HEAD if r == 5 else C_SUB), BORDER
            x.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[r].height = 18
    first = st.first_day or st.month_start
    for i in range(st.days_in_month):
        d = first + timedelta(days=i)
        r = 6 + i
        wk = _up(st.weekday_names[vba_weekday(d) - 1])
        dc = ws.cell(r, 1, d)
        dc.number_format = "dd-mmm-yy"
        wc = ws.cell(r, 2, wk)
        weekend = st.is_weekend(d)
        for x in (dc, wc):
            x.font, x.border = Font(bold=True), BORDER
            x.alignment = Alignment(horizontal="center", vertical="center")
            if weekend:
                x.fill = _fill("FFE699")
        lines = 1
        for j, e in enumerate(emps):
            c = cells[(e.name, d)]
            x = ws.cell(r, 3 + j, c.text)
            x.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
            x.border = BORDER
            x.font = Font(size=9, bold=c.kind == "ot", color="000000")
            if c.kind in KIND_FILL:
                x.fill = _fill(KIND_FILL[c.kind])
            lines = max(lines, est_lines(c.text))
        ws.row_dimensions[r].height = max(20.0, lines * LINE_PT + 4)
    ws.column_dimensions["A"].width = 11
    ws.column_dimensions["B"].width = 6
    for j in range(len(emps)):
        ws.column_dimensions[get_column_letter(3 + j)].width = COL_W
    # legend
    lr = 6 + st.days_in_month + 1
    for k, (lab, col) in enumerate(LEGEND):
        x = ws.cell(lr, 3 + k, lab)
        x.fill, x.border = _fill(col), BORDER
        x.alignment = Alignment(wrap_text=True, horizontal="center", vertical="center")
        x.font = Font(size=9, bold=col == C_OT)
    ws.row_dimensions[lr].height = 28
    ws.cell(lr, 1, "Legend").font = Font(bold=True)
    ws.freeze_panes = ws.cell(6, 3)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:5"
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.3


def _put_table(ws, r: int, header: tuple, rows: list[tuple]) -> int:
    for j, h in enumerate(header):
        x = ws.cell(r, 1 + j, h)
        x.font, x.fill, x.border = Font(bold=True, color="FFFFFF"), _fill(C_HEAD), BORDER
        x.alignment = Alignment(wrap_text=True, vertical="center")
    r += 1
    for row in rows:
        for j, v in enumerate(row):
            if isinstance(v, float):
                v = round(v, 2)
            x = ws.cell(r, 1 + j, v)
            x.border = BORDER
            if isinstance(v, date):
                x.number_format = "dd-mmm-yy"
        r += 1
    return r


def _sheet_checks(wb: Workbook, inputs: RosterInputs, roster: Roster, report: CheckReport) -> None:
    ws = wb.create_sheet("CHECKS")
    ws.cell(1, 1, "CHECKS - " + _title(inputs, roster)).font = Font(bold=True, size=13)
    r = _put_table(ws, 3, ("Check", "Count", "Status", "Note"),
                   [(s.name, s.count, s.status, s.note) for s in report.summary])
    for rr in range(4, r):
        st_ = ws.cell(rr, 3)
        st_.fill = _fill(C_GOOD if st_.value == "OK" else C_BELOW)
        st_.font = Font(bold=True)
    r += 1
    for n in report.notes:
        ws.cell(r, 1, n).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws.row_dimensions[r].height = max(18, est_lines(n, 110) * LINE_PT + 4)
        r += 1
    r += 1
    ws.cell(r, 1, "Relaxations and issues reported by the solver").font = Font(bold=True, size=12)
    r += 1
    if report.issues:
        r = _put_table(ws, r, tuple(report.issue_header), [tuple(i) for i in report.issues])
    else:
        ws.cell(r, 1, "none")
        r += 1
    names = {s.key: s.name for s in report.summary}
    for key, rows in report.details.items():
        if key == "overtime" or key == "days_off_table" or rows:
            r += 1
            ws.cell(r, 1, names.get(key, "Days-off blocks per employee")).font = Font(bold=True, size=12)
            r += 1
            if rows:
                r = _put_table(ws, r, report.detail_headers[key], rows)
            else:
                ws.cell(r, 1, "none")
                r += 1
    for col, w in zip("ABCDEFG", (44, 14, 14, 22, 18, 18, 18)):
        ws.column_dimensions[col].width = w
    for rr in range(3, 4 + len(report.summary)):
        c = ws.cell(rr, 4)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["D"].width = 60
    for rr in range(4, 4 + len(report.summary)):
        ws.row_dimensions[rr].height = max(15, est_lines(str(ws.cell(rr, 4).value or ""), 60) * LINE_PT + 3)
    ws.freeze_panes = "A4"


def _sheet_hours(wb: Workbook, inputs: RosterInputs, report: CheckReport) -> None:
    ws = wb.create_sheet("HOURS")
    weeks = report.weeks
    head = ["Employee"] + [f"Wk {w} normal h" for w in weeks] + [f"Wk {w} OT h" for w in weeks] + [
        "Normal total", "OT total", "Nights", "Fridays", "Days worked", "OT priority"]
    rows = []
    for name in report.employees:
        s = report.emp_stats[name]
        rows.append((name, *[report.normal_by_week[name].get(w, 0) for w in weeks],
                     *[report.ot_by_week[name].get(w, 0) for w in weeks],
                     s["normal"], s["ot"], s["nights"], s["fridays"], s["days_worked"], s.get("ot_rank") or "-"))
    _put_table(ws, 1, tuple(head), rows)
    ws.column_dimensions["A"].width = 16
    for j in range(2, len(head) + 1):
        ws.column_dimensions[get_column_letter(j)].width = 13
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "B2"


def _grid_sheet(wb: Workbook, title: str, inputs: RosterInputs, report: CheckReport, heat: bool) -> None:
    ws = wb.create_sheet(title)
    r = 1
    if heat:
        ws.cell(r, 1, "Head-count heat map: days x hours, per reception (darker = more people)").font = Font(bold=True, size=12)
        r += 2
    for rec, days in report.headcount.items():
        ws.cell(r, 1, rec).font = Font(bold=True, size=12)
        r += 1
        ws.cell(r, 1, "Date")
        ws.cell(r, 2, "Day")
        for h in range(24):
            ws.cell(r, 3 + h, h)
        if not heat:
            ws.cell(r, 27, "Hours covered")
        for c in range(1, 28 if not heat else 27):
            x = ws.cell(r, c)
            x.font, x.fill, x.border = Font(bold=True, color="FFFFFF"), _fill(C_HEAD), BORDER
            x.alignment = Alignment(horizontal="center", wrap_text=True)
        r += 1
        top = r
        for d, vals in days.items():
            ws.cell(r, 1, d).number_format = "dd-mmm"
            ws.cell(r, 2, _up(inputs.settings.weekday_names[vba_weekday(d) - 1]))
            for h, n in enumerate(vals):
                x = ws.cell(r, 3 + h, n)
                x.alignment = Alignment(horizontal="center")
                x.border = BORDER
                if not heat:
                    lo, hi = report.limits.get(rec, {}).get(d, [(0, -1)] * 24)[h]
                    if n < lo:
                        x.fill = _fill(C_BELOW)
                    elif hi >= 0 and n > hi:
                        x.fill = _fill(C_ABOVE)
            if not heat:
                ws.cell(r, 27, report.covered_hours[rec][d]).alignment = Alignment(horizontal="center")
            r += 1
        if heat:
            ws.conditional_formatting.add(
                f"C{top}:Z{r - 1}", ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF",
                                                    mid_type="num", mid_value=2, mid_color="9DC3E6",
                                                    end_type="num", end_value=5, end_color="1F4E79"))
        r += 2
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 6
    for h in range(24):
        ws.column_dimensions[get_column_letter(3 + h)].width = 4.5
    ws.column_dimensions["AA"].width = 14
    ws.freeze_panes = "C1"
    if not heat:
        ws.cell(r, 1, "Red = below Min staff, orange = above Max staff. The first C16 hours of day 1 are covered by last month "
                      "(counted in 'Hours covered').")


def write_roster_workbook(inputs: RosterInputs, roster: Roster, report: CheckReport | None, out_path) -> Path:
    """Write the roster workbook with SCHEDULE, CHECKS, HOURS, COVERAGE and TIMELINE sheets."""
    report = report or run_checks(inputs, roster)
    wb = Workbook()
    _sheet_schedule(wb, inputs, roster, report)
    _sheet_checks(wb, inputs, roster, report)
    _sheet_hours(wb, inputs, report)
    _grid_sheet(wb, "COVERAGE", inputs, report, heat=False)
    _grid_sheet(wb, "TIMELINE", inputs, report, heat=True)
    p = Path(str(out_path))
    wb.save(str(p))
    return p


# =============================================================================== PDF
_AR_RANGE = re.compile("[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_PDF_FONTS: dict[str, tuple[str, str] | None] = {}


def _has_arabic(s: str) -> bool:
    return bool(_AR_RANGE.search(s or ""))


def _font_candidates() -> list[tuple[str, str]]:
    """(regular, bold) TTF pairs with Arabic glyphs: Windows Arial / Tahoma, macOS Arial, Linux DejaVu Sans / Noto."""
    win = Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows") / "Fonts"
    pairs = [(win / "arial.ttf", win / "arialbd.ttf"), (win / "tahoma.ttf", win / "tahomabd.ttf"),
             (win / "segoeui.ttf", win / "segoeuib.ttf")]
    for d in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF", "/usr/local/share/fonts"):
        pairs.append((Path(d) / "DejaVuSans.ttf", Path(d) / "DejaVuSans-Bold.ttf"))
    pairs += [(Path("/System/Library/Fonts/Supplemental/Arial.ttf"), Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")),
              (Path("/Library/Fonts/Arial Unicode.ttf"), Path("/Library/Fonts/Arial Unicode.ttf"))]
    return [(str(a), str(b)) for a, b in pairs]


def arabic_pdf_fonts() -> tuple[str, str] | None:
    """Register a TrueType font that has Arabic letters with reportlab; returns (regular name, bold name), or ``None``
    when no such font is installed (the PDF then falls back to Helvetica and Arabic letters are missing)."""
    if "f" in _PDF_FONTS:
        return _PDF_FONTS["f"]
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    found = None
    for reg, bold in _font_candidates():
        if os.path.exists(reg):
            try:
                pdfmetrics.registerFont(TTFont("RB-Arabic", reg))
                b = bold if os.path.exists(bold) else reg
                pdfmetrics.registerFont(TTFont("RB-Arabic-Bold", b))
                found = ("RB-Arabic", "RB-Arabic-Bold")
                break
            except Exception:  # noqa: BLE001 - try the next font
                continue
    _PDF_FONTS["f"] = found
    return found


def shape_arabic(text: str) -> str:
    """One visual line of Arabic (letters joined, right-to-left order) for a left-to-right PDF engine.  Text without Arabic
    letters, or a missing arabic-reshaper / python-bidi, is returned unchanged."""
    if not _has_arabic(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(text))
    except Exception:  # noqa: BLE001
        return text


def wrap_shaped(text: str, font: str, size: float, width: float) -> list[str]:
    """Wrap ``text`` into lines no wider than ``width`` (logical order, word by word) and shape every line separately,
    so a long Arabic name that needs two lines still reads in the right order."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    out: list[str] = []
    for part in (text or "").split("\n"):
        words, cur = part.split(" "), ""
        for w in words:
            t = (cur + " " + w) if cur else w
            if cur and stringWidth(shape_arabic(t), font, size) > width:
                out.append(cur)
                cur = w
            else:
                cur = t
        out.append(cur)
    return [shape_arabic(x) for x in out]


def export_pdf(inputs: RosterInputs, roster: Roster, out_path, paper: str | None = None) -> Path:
    """PDF of the SCHEDULE: landscape A3 (A4 for up to 8 employees), fit to width, header rows repeated,
    wrapped text in every cell, same colour coding and a legend at the bottom."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A3, A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    st = inputs.settings
    emps = _active(inputs)
    cells = build_cells(inputs, roster)
    ar_fonts = arabic_pdf_fonts() if any(_has_arabic(s) for e in emps for s in (e.name, e.home, e.gender)) else None
    f_reg, f_bold = ar_fonts or ("Helvetica", "Helvetica-Bold")
    size = landscape(A4 if (paper == "A4" or (paper is None and len(emps) <= 8)) else A3)
    margin = 24
    doc = SimpleDocTemplate(str(out_path), pagesize=size, leftMargin=margin, rightMargin=margin, topMargin=40, bottomMargin=28,
                            title="Roster", author="RosterBuilder")
    avail = size[0] - 2 * margin
    w_date, w_day = 44, 28
    w_emp = (avail - w_date - w_day) / max(1, len(emps))
    fs = 7.5 if w_emp >= 70 else 6.5
    base = ParagraphStyle("c", fontName=f_reg, fontSize=fs, leading=fs + 1.6, alignment=1)
    bold = ParagraphStyle("b", parent=base, fontName=f_bold)
    head = ParagraphStyle("h", parent=bold, textColor=colors.white)

    def P(txt: str, style=base) -> Paragraph:
        if _has_arabic(txt):  # wrap in logical order, then shape each line (names typed in Arabic)
            lines = wrap_shaped(txt, style.fontName, style.fontSize, w_emp - 6)
            return Paragraph("<br/>".join(escape(x) for x in lines), style)
        t = escape(txt).replace(" (EXT", "<br/>(EXT")
        return Paragraph(t, style)

    data = [
        [P("Reception", head), ""] + [P(e.home, bold) for e in emps],
        [P("Gender", head), ""] + [P(e.gender, bold) for e in emps],
        [P("Date", head), P("Day", head)] + [P(e.name, head) for e in emps],
    ]
    styles = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (1, 2), colors.HexColor("#" + C_HEAD)),
        ("BACKGROUND", (2, 0), (-1, 1), colors.HexColor("#" + C_SUB)),
        ("BACKGROUND", (2, 2), (-1, 2), colors.HexColor("#" + C_HEAD)),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    first = st.first_day or st.month_start
    for i in range(st.days_in_month):
        d = first + timedelta(days=i)
        r = 3 + i
        row = [P(f"{d:%d-%b}", bold), P(_up(st.weekday_names[vba_weekday(d) - 1]), bold)]
        for j, e in enumerate(emps):
            c = cells[(e.name, d)]
            row.append(P(c.text, bold if c.kind == "ot" else base))
            if c.kind in KIND_FILL:
                styles.append(("BACKGROUND", (2 + j, r), (2 + j, r), colors.HexColor("#" + KIND_FILL[c.kind])))
        if st.is_weekend(d):
            styles.append(("BACKGROUND", (0, r), (1, r), colors.HexColor("#FFE699")))
        data.append(row)
    t = Table(data, colWidths=[w_date, w_day] + [w_emp] * len(emps), repeatRows=3)
    t.setStyle(TableStyle(styles))
    leg = Table([[P(lab) for lab, _ in LEGEND]], colWidths=[min(130, avail / len(LEGEND))] * len(LEGEND))
    leg.setStyle(TableStyle([("BACKGROUND", (k, 0), (k, 0), colors.HexColor("#" + col)) for k, (_, col) in enumerate(LEGEND)]
                            + [("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]))
    title = _title(inputs, roster)

    def deco(canvas, d_):
        canvas.saveState()
        canvas.setFont(f_bold, 11)
        canvas.drawString(margin, size[1] - 26, shape_arabic(title))
        canvas.setFont(f_reg, 7)
        canvas.drawRightString(size[0] - margin, 14, f"Page {d_.page}")
        canvas.restoreState()

    doc.build([t, Spacer(1, 8), leg], onFirstPage=deco, onLaterPages=deco)
    return Path(str(out_path))
