"""Input check – Python port of ``AdviseInputs`` / ``AdvMark`` / ``ApplyAdvice``
from ``RosterBuilder_current_source.bas`` (BUILD_GUIDE.md section 5).

Milestone 2: run the input checks against the in-memory model and write the
findings back into a *copy* of the workbook (orange fill + comment). Auto-fixes
are only applied when the user approves them (never automatically).
"""

from __future__ import annotations

import re
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import PatternFill

from .io_xlsx import RosterInputs
from .locate import localize_issues
from .model import Issue, Shift, day_label, vba_weekday

ADV_TAG = "INPUT CHECK: "
ORANGE = "FFFF9900"  # RGB(255, 153, 0)


def _num(v, dflt: float = 0.0) -> float:
    if v is None or isinstance(v, bool):
        return dflt
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "":
        return dflt
    try:
        return float(s)
    except ValueError:
        return dflt


def _is_date_val(v) -> bool:
    return hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day")


def _adv_time(t: str, is_end: bool = False) -> float:
    s = (t or "").strip().upper()
    if s == "":
        return -1.0
    if "MN" in s or "MIDNIGHT" in s:
        return 24.0 if is_end else 0.0
    m = re.match(r"^(\d{1,2})(?::(\d{1,2}))?\s*(AM|PM)?$", s)
    if not m:
        return -1.0
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    ap = (m.group(3) or "").upper()
    if ap == "PM" and h < 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return h + mi / 60.0


def _adv_day_in(dn: str, lst: str, fri: str) -> bool:
    x = (lst or "").strip().upper()
    if x == "" or x == "ALL":
        return True
    if x == "WEEKDAYS":
        return (dn or "").strip().upper() != (fri or "").strip().upper()
    return (dn or "").strip().upper() in [(p or "").strip().upper() for p in x.split(",") if (p or "").strip()]


def _adv_in_list(code: str, a: str, b: str, c: str) -> bool:
    u = (code or "").strip().upper()
    return u in [(a or "").strip().upper(), (b or "").strip().upper(), (c or "").strip().upper()]


def _adv_covers(s: Shift, x: float) -> bool:
    if s.start1 < 0 or s.end1 < 0:
        return False
    b = s.end1
    if b <= s.start1:
        b = b + 24.0
    ok = (x >= s.start1 and x < b) or (x + 24.0 >= s.start1 and x + 24.0 < b)
    if s.start2 >= 0 and s.end2 > 0:
        e = s.end2
        if e <= s.start2:
            e = e + 24.0
        ok = ok or (x >= s.start2 and x < e)
    return ok


def _adv_shift_hours(s: Shift) -> float:
    return s.paid if s.paid > 0 else 8.0


def _adv_shift_row(inp: RosterInputs, code: str) -> int:
    u = (code or "").strip().upper()
    if not u:
        return 0
    for s in inp.shifts:
        if s.code.strip().upper() == u:
            return s.row
    return 0


def _shift_at(inp: RosterInputs, row: int):
    for s in inp.shifts:
        if s.row == row:
            return s
    return None


def _emp_day_off(e, wkendL: str) -> int:
    offs = 0
    if e.fixed_off and e.fixed_off != "AUTO":
        offs = offs + 1
    if e.fixed_off2 and e.fixed_off2 and e.fixed_off2 != e.fixed_off:
        offs = offs + 1
    if e.no_we and wkendL:
        for dn in [p.strip().upper() for p in wkendL.split(",") if p.strip()]:
            if dn and dn != e.fixed_off and dn != e.fixed_off2:
                offs = offs + 1
    return offs


def _weekly_needs(inp: RosterInputs, fri: str, wd: list[str]) -> tuple[float, float, list[float]]:
    """R4.7 / R6.3: per reception and day, take for each hour the maximum Min (and maximum
    Min male) over all applicable targets, sum the hours, compare with the demand hours and
    keep the larger."""
    needH = 0.0
    needM = 0.0
    needMd = [0.0] * 7
    recs: list[str] = []
    for t in inp.targets:
        c = (t.rec or "").strip().upper()
        if c and c not in recs:
            recs.append(c)
    for d in inp.demand:
        c = (d.rec or "").strip().upper()
        if c and c not in recs and (d.rec or "").strip() != "N":
            recs.append(c)
    for rec in recs:
        for j in range(7):
            day_need_h = 0.0
            day_need_m = 0.0
            day_dem_h = 0.0
            day_dem_m = 0.0
            day_tgts = [t for t in inp.targets if (t.rec or "").strip().upper() == rec and _adv_day_in(wd[j], t.days, fri)]
            for h in range(24):
                mins: list[float] = []
                mmins: list[float] = []
                for t in day_tgts:
                    if int(t.frm) <= h < int(t.to) and (t.max_staff < 0 or t.min_staff <= t.max_staff):
                        mins.append(t.min_staff if t.min_staff >= 0 else 0.0)
                        mmins.append(t.min_male if t.min_male >= 0 else 0.0)
                day_need_h += max(mins) if mins else 0.0
                day_need_m += max(mmins) if mmins else 0.0
            for d in inp.demand:
                if (d.rec or "").strip().upper() != rec or not d.rec or (d.rec or "").strip().upper() == "N":
                    continue
                sr = _adv_shift_row(inp, d.shift)
                sh = _shift_at(inp, sr) if sr > 0 else None
                cnt = d.counts[j] if j < len(d.counts) else 0
                if d.all_week and cnt < 0:
                    cnt = -1
                if sh is None or not isinstance(cnt, int) or cnt <= 0:
                    continue
                dem_h = cnt * _adv_shift_hours(sh)
                dem_m = dem_h if (d.gender or "ANY").strip().upper() == (inp.settings.male_code or "M").strip().upper() else 0.0
                day_dem_h += dem_h
                day_dem_m += dem_m
            needH += day_need_h if day_need_h > day_dem_h else day_dem_h
            needM += day_need_m if day_need_m > day_dem_m else day_dem_m
            needMd[j] += day_need_m
    return needH, needM, needMd


def run_input_check(inp: RosterInputs) -> list[Issue]:
    """Port of ``AdviseInputs``: returns the list of input problems found.

    For inputs from the data store the findings also carry field locators (``Issue.where``), a
    machine-readable fix (``Issue.fix_set``) and texts without Excel cell references."""
    findings = _run_input_check(inp)
    localize_issues(findings, inp)
    return findings


def _run_input_check(inp: RosterInputs) -> list[Issue]:
    findings: list[Issue] = list(inp.issues)

    def add(cell: str, msg: str, fix: str = "", check: str = "GEN") -> None:
        findings.append(Issue(id=f"{check}|{cell}", cell=cell, msg=msg, fix=fix))

    fri = (inp.settings.fri_name or "FRI").strip().upper()
    wd = [(inp.settings.weekday_names[i - 1] or "").strip().upper() for i in range(1, 8)]
    dOff = int(round(inp.settings.days_off_wk))
    maxH = inp.settings.max_week_h
    maxSh = inp.settings.max_shift_h
    otOnX = inp.settings.ot_on
    otMaxX = inp.settings.ot_max
    wkendL = (inp.settings.weekend_days or "FRI,SAT").strip().upper()

    # ---- 1. shift names that do not match their times
    for s in inp.shifts:
        r = s.row
        if not s.code or "&" in s.code or " - " not in s.code or s.start2 >= 0:
            continue
        p = s.code.find(" - ")
        s1 = _adv_time(s.code[:p])
        e1 = _adv_time(s.code[p + 3:])
        if s1 < 0 or e1 < 0:
            continue
        exp_end = e1 + 24.0 if e1 <= s1 else e1
        if abs(s.start1 - s1) > 0.01 or abs(s.end1 - exp_end) > 0.01:
            add(f"C{r},D{r}", f"Shift '{s.code}' is entered as {s.start1} to {s.end1}. Suggested: Start {s1}, End {e1} (as its name says).", f"C{r}={s1}|D{r}={e1}", check="SHIFTNAME")

    # ---- 2. coverage targets: min above max (same row / overlapping rows), duplicates
    n_t = len(inp.targets)
    for i in range(n_t):
        t = inp.targets[i]
        r = t.row
        min_v = t.min_staff if t.min_staff >= 0 else 0.0
        max_v = t.max_staff if t.max_staff >= 0 else 999.0
        if min_v > max_v and max_v < 999.0:
            add(f"G{r},I{r}", f"Min staff {t.min_staff} is above Max staff {t.max_staff} on the same row. Suggested: Max = {t.min_staff}.", f"I{r}={t.min_staff}")
        for j in range(n_t):
            if j == i:
                continue
            t2 = inp.targets[j]
            r2 = t2.row
            if (t2.rec or "").strip().upper() != (t.rec or "").strip().upper():
                continue
            if not (t.frm < t2.to and t2.frm < t.to):
                continue
            days_overlap = any(_adv_day_in(wd[jj], t.days, fri) and _adv_day_in(wd[jj], t2.days, fri) for jj in range(7))
            max2 = t2.max_staff if t2.max_staff >= 0 else 999.0
            if days_overlap and min_v > max2 and max2 < 999.0:
                add(f"G{r},I{r2}", f"{t.rec}: row {r} needs at least {t.min_staff} ({t.frm}-{t.to}h) but row {r2} allows at most {t2.max_staff} ({t2.frm}-{t2.to}h) in the same hours. Suggested: clear Max on row {r2}.", f"I{r2}=")
            if j > i and t.frm == t2.frm and t.to == t2.to and days_overlap:
                add(f"D{r},D{r2}", f"Rows {r} and {r2} are the same hours for {t.rec} on the same day(s). Keep one row (the higher minimum counts).", "")

    # ---- 3. employees
    for i, e in enumerate(inp.employees):
        r = e.row
        if not e.name.strip() or e.active is False:
            continue
        allowed = e.allowed
        allowed_full = [allowed[k] if k < len(allowed) else "" for k in range(3)]
        for j, code in enumerate(e.pref):
            if not code:
                continue
            if allowed and not _adv_in_list(code, allowed_full[0], allowed_full[1], allowed_full[2]):
                slot = 9 + len(allowed) if len(allowed) < 3 else 0
                if slot > 0:
                    add(f"{chr(64 + slot)}{r}", f"{e.name}: preferred shift '{code}' is not in her/his allowed shifts, so it is never used. Suggested: add it to allowed shift {slot - 8}.", f"{chr(64 + slot)}{r}={code}")
                else:
                    add(f"{chr(64 + 12 + j)}{r}", f"{e.name}: preferred shift '{code}' is not in her/his 3 allowed shifts, so it is never used. Replace one allowed shift or the preferred shift.", "")
        if e.no_we and e.fri and _adv_day_in(fri, inp.settings.weekend_days, fri):
            add(f"H{r},P{r}", f"{e.name}: 'No weekends' = Y but 'Friday eligible' = Y - Friday is a weekend day. Suggested: Friday eligible = N.", f"H{r}=N")
        if allowed:
            home = (e.home or "").strip().upper()
            needed = False
            for d in inp.demand:
                if (d.rec or "").strip().upper() != home:
                    continue
                ok_shift = any(_adv_in_list(d.shift, allowed[0], allowed[1] if len(allowed) > 1 else "", allowed[2] if len(allowed) > 2 else "") for _ in [0])
                if not ok_shift:
                    continue
                for c in d.counts:
                    if c > 0 or (d.all_week and c < 0):
                        needed = True
                        break
            if not needed:
                for t in inp.targets:
                    if (t.rec or "").strip().upper() != home:
                        continue
                    hit = False
                    for s in inp.shifts:
                        if not any(_adv_in_list(s.code, allowed[0], allowed[1] if len(allowed) > 1 else "", allowed[2] if len(allowed) > 2 else "") for _ in [0]):
                            continue
                        for h in range(int(t.frm), int(t.to)):
                            if _adv_covers(s, h + 0.5):
                                hit = True
                                break
                        if hit:
                            break
                    if hit:
                        needed = True
            if not needed:
                add(f"I{r},J{r},K{r}", f"{e.name}: none of the allowed shifts is needed in {home} (no demand row and no coverage-target hour), so she/he will only be off or used as cover.", "")

    # fixed days off 1 and 2 not next to each other
    for i, e in enumerate(inp.employees):
        r = e.row
        if not e.name.strip() or e.active is False:
            continue
        g = (e.fixed_off or "").strip().upper()
        q = (e.fixed_off2 or "").strip().upper()
        if g and g != "AUTO" and q and q != "AUTO" and g != q:
            p_idx = wd.index(g) if g in wd else -1
            k_idx = wd.index(q) if q in wd else -1
            if p_idx >= 0 and k_idx >= 0 and abs(p_idx - k_idx) > 1 and abs(p_idx - k_idx) != 6:
                add(f"G{r},Q{r}", f"{e.name}: fixed days off {g} and {q} are not next to each other. Days off must be in a row (e.g. {g} + {wd[(p_idx + 1) % 7]}).", f"Q{r}={wd[(p_idx + 1) % 7]}")

    # fixed days off + no-weekend days more than C15
    for i, e in enumerate(inp.employees):
        r = e.row
        if not e.name.strip() or e.active is False:
            continue
        offs = _emp_day_off(e, wkendL)
        if offs > dOff:
            add(f"G{r},P{r},Q{r}", f"{e.name}: fixed days off + no-weekend days = {offs} days off a week, more than 'Days off per week' ({dOff}).", "")

    # ---- 4. demand rows without counts
    for d in inp.demand:
        r = d.row
        any_count = False
        for j in range(7):
            c = d.counts[j] if j < len(d.counts) else 0
            if d.all_week and c < 0:
                any_count = True
            elif c > 0:
                any_count = True
        if not any_count:
            add(f"B{r},H{r}", f"Demand row {r} ('{d.shift}') has no counts on any day, so it adds nobody. Suggested: Active = N (or enter the counts).", f"B{r}=N")

    # ---- 5. fixed shifts that clash with leave / day off / allowed shifts / another fixed shift
    for i, f in enumerate(inp.fixed):
        r = f.row
        if not f.emp or not f.shift:
            continue
        for li, lv in enumerate(inp.leave):
            if lv.emp.strip().upper() == f.emp.strip().upper() and lv.frm <= f.date <= lv.to:
                add(f"C{r},B{lv.row}", f"{f.emp}: fixed shift on {f.date} is inside her/his leave ({lv.frm}-{lv.to}). Remove one of them.", "")
        for j, g in enumerate(inp.fixed):
            if j >= i or not g.emp or not g.shift:
                continue
            if g.emp.strip().upper() == f.emp.strip().upper() and g.date == f.date:
                add(f"C{r},C{g.row}", f"{f.emp}: has two fixed shifts on {f.date}. Remove one of them.", "")
        for ei, e in enumerate(inp.employees):
            if e.name.strip().upper() != f.emp.strip().upper():
                continue
            wdn = day_label(vba_weekday(f.date))
            if (e.fixed_off and e.fixed_off == wdn) or (e.fixed_off2 and e.fixed_off2 == wdn):
                add(f"C{r}", f"{e.name}: fixed shift on {f.date} is on her/his fixed day off. It will be worked as overtime.", "")
            if e.allowed and not any(
                _adv_in_list(f.shift, e.allowed[k], e.allowed[k + 1] if k + 1 < len(e.allowed) else "", e.allowed[k + 2] if k + 2 < len(e.allowed) else "") for k in range(len(e.allowed))
            ):
                add(f"I{e.row}", f"{e.name}: fixed shift '{f.shift}' is not one of her/his allowed shifts. Fine for that day, but check it is intended.", "")

    if inp.settings.month_start is None:
        # R2.6 / R4.9: without a month, return the missing-month Issue together with the
        # other problems found so far, in one go, and stop before the month-dependent check.
        return findings

    # ---- 6. capacity: staff hours needed per week vs available
    needH, needM, needMd = _weekly_needs(inp, fri, wd)

    availH = 0.0
    availM = 0.0
    otH = 0.0
    otM = 0.0
    for e in inp.employees:
        if not e.name.strip() or e.active is False:
            continue
        long_lv = False
        for lv in inp.leave:
            if lv.emp.strip().upper() == e.name.strip().upper() and (lv.to - lv.frm).days >= 13:
                long_lv = True
        if long_lv:
            continue
        empH = e.max_h if e.max_h >= 0 else maxH
        if empH > (7 - dOff) * maxSh:
            empH = (7 - dOff) * maxSh
        availH = availH + empH
        if (e.gender or "").strip().upper() == (inp.settings.male_code or "M").strip().upper():
            availM = availM + empH
        if otOnX and e.ot:
            otH = otH + otMaxX
            if (e.gender or "").strip().upper() == (inp.settings.male_code or "M").strip().upper():
                otM = otM + otMaxX
    if needH > availH + 0.5:
        add("C9,C15,G14", f"Per week the targets/demand need about {needH:.0f} staff-hours, but active staff (without long leave) can give only {availH:.0f} h" + (f" + up to {otH:.0f} h overtime" if otH > 0 else "") + f". Short by about {needH - availH:.0f} h/week. Options: lower the minimums, raise Max hours/week (C9), lower Days off (C15), switch overtime ON (G14) or add staff.", "")
    if needM > availM + 0.5:
        add("C9,G14", f"Per week about {needM:.0f} hours need a MAN (Min male / male demand), but the men available can give only {availM:.0f} h" + (f" + up to {otM:.0f} h overtime" if otM > 0 else "") + ". Options: remove 'Min male' outside the men-only hours, overtime ON for the men, or add a male employee.", "")

    # ---- 6b. days on which a man is needed but no man may work
    for j in range(7):
        if needMd[j] > 0:
            menD = 0
            who_no = ""
            who_cells: list[str] = []
            for ei, e in enumerate(inp.employees):
                if not e.name.strip() or e.active is False:
                    continue
                if (e.gender or "").strip().upper() != (inp.settings.male_code or "M").strip().upper():
                    continue
                long_lv = False
                for lv in inp.leave:
                    if lv.emp.strip().upper() == e.name.strip().upper() and (lv.to - lv.frm).days >= 13:
                        long_lv = True
                if long_lv:
                    continue
                cell = ""
                if e.no_we and _adv_day_in(wd[j], inp.settings.weekend_days, fri):
                    who_no = who_no + (", " if who_no else "") + e.name + " (no weekends)"
                    cell = f"P{e.row}"
                elif e.fixed_off and e.fixed_off == wd[j]:
                    who_no = who_no + (", " if who_no else "") + e.name + " (fixed day off)"
                    cell = f"G{e.row}"
                elif e.fixed_off2 and e.fixed_off2 == wd[j]:
                    who_no = who_no + (", " if who_no else "") + e.name + " (fixed day off 2)"
                    cell = f"Q{e.row}"
                elif wd[j] == fri and not e.fri:
                    who_no = who_no + (", " if who_no else "") + e.name + " (not Friday-eligible)"
                    cell = f"H{e.row}"
                else:
                    menD = menD + 1
                    continue
                if cell and cell not in who_cells:
                    who_cells.append(cell)
            if menD * (inp.settings.max_shift_h or 8) < needMd[j] - 0.5:
                add(",".join(who_cells) if who_cells else "C9", f"On {wd[j]} about {needMd[j]:.0f} h need a man, but only {menD} man/men may work that day" + (f" - not available: {who_no}" if who_no else "") + ". Change 'No weekends' / fixed day off for one man, or remove 'Min male' for that day.", "")

    # ---- 7. rest settings
    if inp.settings.min_rest < 8:
        add("C11", f"Min rest between shifts is {inp.settings.min_rest} h - a night shift can be followed directly by a morning shift. Suggested: 8 h.", "C11=8")

    return findings


NORMAL_FILL = "00FFF2CC"  # Excel-style input fill (light yellow) used to clear old marks


def mark_findings(wb, findings: list[Issue]) -> list[Issue]:
    """Write the findings back into the workbook copy: orange fill + comment.
    Before marking, any previous INPUT CHECK mark (comment + orange fill) is cleared
    back to the normal input fill, so running the check twice never stacks marks."""
    ws = wb["INPUT"]
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0
    if max_row and max_col:
        for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
            for cell in row:
                if cell.comment is not None and str(cell.comment.text or "").startswith(ADV_TAG):
                    cell.comment = None
                    cell.fill = PatternFill(start_color=NORMAL_FILL, end_color=NORMAL_FILL, fill_type="solid")
    for f in findings:
        for addr in [a.strip() for a in (f.cell or "").split(",") if a.strip()]:
            cell = ws[addr]
            cell.fill = PatternFill(start_color=ORANGE, end_color=ORANGE, fill_type="solid")
            cell.comment = Comment(ADV_TAG + (f.msg or ""), author="RosterBuilder")
    return findings


def copy_target(src_path, out_path) -> tuple[Path, bool]:
    """Real output path of a review copy and whether the macros are kept (R14.1).

    The macros are kept (``keep_vba=True``, saved as ``.xlsm``) only when the input is an
    ``.xlsm`` and the output name also ends in ``.xlsm``; every other output is saved as ``.xlsx``.
    """
    out = Path(str(out_path))
    keep = Path(str(src_path)).suffix.lower() == ".xlsm" and out.suffix.lower() == ".xlsm"
    return (out if keep else out.with_suffix(".xlsx")), keep


def _load_for_copy(src_path, out_path):
    _, keep = copy_target(src_path, out_path)
    return load_workbook(src_path, keep_vba=keep)


def _save_copy(wb, out_path, src_path=None) -> Path:
    """Save a copy of the workbook and return the real path. With macros kept (see
    :func:`copy_target`) the file is an ``.xlsm``; otherwise it is an ``.xlsx`` (R7.2, R14.1).
    The input file is never modified."""
    p, _ = copy_target(src_path if src_path is not None else "", out_path)
    wb.save(str(p))
    return p


def write_highlights(src_path: str, out_path: str, findings: list[Issue]) -> list[Issue]:
    """Copy the workbook and write the findings (orange fill + comment) into the copy.
    Old INPUT CHECK marks are cleared first. ``.xlsm`` in -> ``.xlsm`` out keeps the macros
    (R14.1); anything else is saved as ``.xlsx``. Use :func:`copy_target` for the real path."""
    wb = _load_for_copy(src_path, out_path)
    mark_findings(wb, findings)
    _save_copy(wb, out_path, src_path)
    return findings


def apply_fixes(src_path: str, out_path: str, findings: list[Issue], approved_ids) -> list[Issue]:
    """Apply only the approved auto-fixes (``addr=value``; ``addr=`` clears the cell) to a copy.
    Approval is per finding id (``check|cells``), so all parts of a multi-cell fix apply together."""
    wb = _load_for_copy(src_path, out_path)
    ws = wb["INPUT"]
    approved = set(approved_ids or [])
    for f in findings:
        fid = f.id or f.cell
        if fid not in approved or not f.fix:
            continue
        for part in f.fix.split("|"):
            if "=" not in part:
                continue
            addr = part.split("=")[0].strip()
            val = part.split("=")[1].strip() if "=" in part else ""
            if val == "":
                ws[addr].value = None
            elif val.replace(".", "", 1).isdigit():
                ws[addr].value = float(val)
            else:
                ws[addr].value = val
    _save_copy(wb, out_path, src_path)
    return findings
