"""One-time import of an old INPUT workbook into the data store (BUILD_GUIDE sections 5, 10, 12 row 9).

``import_excel(store, path, mode="empty" | "replace", month=None, confirmed=False)`` reads the INPUT sheet only
(``io_xlsx``), writes settings, receptions, coverage rows, shifts and employees into the master lists, creates
the month of cell C6 (or ``month``) as a draft with its leave, fixed shifts and demand, and returns an
:class:`ImportReport` (counts, skipped rows, warnings, input-check findings of the imported month).

* ``empty``: refused when the store already holds master data.
* ``replace``: needs ``confirmed=True``; wipes the master data and all draft months.  Built months stay frozen.
* The whole write is one transaction: an error leaves the store exactly as it was.
* Leave that runs past the month stays stored in the imported month; ``Store.create_month`` carries it into
  every later month it overlaps.  Leave that ended before the month and fixed shifts outside the month are
  skipped (and listed in the report).

Messages here may mention workbook locations ("INPUT row 112") because they are about the import itself.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .io_xlsx import RosterInputs
from .model import shift_code_from_times
from .store import DuplicateError, Store, StoreError, month_key


class ImportError(StoreError):  # noqa: A001 - deliberate: friendly import error shown to the user
    """The workbook cannot be imported; the message is plain English."""


#: report order and labels of the counted sections
SECTIONS = (("settings", "Settings"), ("receptions", "Receptions"), ("coverage", "Coverage rows"),
            ("shifts", "Shifts"), ("employees", "Employees"), ("leave", "Leave periods"),
            ("fixed", "Fixed shifts"), ("demand", "Demand rows"))


@dataclass
class SkippedRow:
    where: str  # e.g. "INPUT row 112"
    section: str
    reason: str

    def __str__(self) -> str:
        from . import i18n

        return f"{i18n.tr_msg(self.where)} ({i18n.tr(self.section)}): {i18n.tr_msg(self.reason)}"


@dataclass
class ImportReport:
    year: int = 0
    month: int = 0
    mode: str = "empty"
    source: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    skipped: list[SkippedRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)  # input check of the imported month, text for the user
    drafts_removed: int = 0

    def summary_text(self) -> str:
        """Readable multi-line summary for the UI / CLI."""
        from . import i18n
        from .i18n import tr

        lines = []
        what = tr("Replaced the existing data with") if self.mode == "replace" else tr("Imported")
        name = i18n.iso(Path(self.source).name) or tr("the workbook")
        lines.append(f"{what} {name}"
                     + (tr(" - month {y}-{m:02d} created as a draft.", y=self.year, m=self.month) if self.year else "."))
        lines.append("")
        lines.append(tr("Imported:"))
        for key, label in SECTIONS:
            if key in self.counts:
                lines.append(f"  {tr(label)}: {self.counts[key]}")
        if self.drafts_removed:
            lines.append(tr("({n} draft month(s) were removed; built months were kept.)", n=self.drafts_removed))
        lines.append("")
        if self.skipped:
            lines.append(tr("Skipped rows ({n}):", n=len(self.skipped)))
            lines += [f"  - {s}" for s in self.skipped]
        else:
            lines.append(tr("Skipped rows: none"))
        if self.warnings:
            lines.append(tr("Warnings ({n}):", n=len(self.warnings)))
            lines += [f"  - {i18n.tr_msg(w)}" for w in self.warnings]
        else:
            lines.append(tr("Warnings: none"))
        if self.findings:
            lines.append(tr("Input check of {y}-{m:02d}: {n} finding(s)", y=self.year, m=self.month, n=len(self.findings)))
            lines += [f"  - {i18n.tr_msg(f)}" for f in self.findings]
        else:
            lines.append(tr("Input check of {y}-{m:02d}: no findings", y=self.year, m=self.month))
        return "\n".join(lines)


def _up(s) -> str:
    return (s or "").strip().upper()


def _row_of(cell: str) -> int:
    m = re.search(r"[A-Z]+(\d+)", cell or "")
    return int(m.group(1)) if m else 0


# ----------------------------------------------------------------------------- reading the file
def _open_workbook(path):
    """Open the workbook (values only) or raise a friendly :class:`ImportError`."""
    import openpyxl
    from zipfile import BadZipFile

    p = Path(str(path))
    if not p.exists() or not p.is_file():
        raise ImportError(f"The file '{p.name}' was not found. Check the name and the folder.")
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ImportError(f"'{p.name}' is not an Excel workbook. Choose a .xlsx or .xlsm file.")
    try:
        with open(p, "rb") as fh:
            head = fh.read(8)
    except PermissionError:
        raise ImportError(f"'{p.name}' cannot be opened - it is locked or you have no permission. "
                          "Close it in Excel and try again.") from None
    except OSError as exc:
        raise ImportError(f"'{p.name}' cannot be read ({exc.strerror or 'file error'}).") from None
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise ImportError(f"'{p.name}' is password-protected (or an old .xls file). Remove the password in Excel, "
                          "save it as .xlsx or .xlsm, and try again.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(str(p), data_only=True)
    except (BadZipFile, KeyError, ValueError, OSError, openpyxl.utils.exceptions.InvalidFileException) as exc:
        if isinstance(exc, PermissionError):
            raise ImportError(f"'{p.name}' cannot be opened - it is locked. Close it in Excel and try again.") from None
        raise ImportError(f"'{p.name}' is not a valid Excel workbook (it is damaged or not an Excel file).") from None
    except Exception:  # noqa: BLE001 - never a raw traceback
        raise ImportError(f"'{p.name}' could not be read as an Excel workbook.") from None
    if "INPUT" not in wb.sheetnames:
        raise ImportError(f"'{p.name}' has no sheet named INPUT, so it is not a roster workbook this app can import.")
    return wb


def _classify_reader_issues(inp: RosterInputs, rep: ImportReport) -> None:
    """Turn the reader's issues into skipped rows / warnings with plain-English reasons."""
    for i in inp.issues:
        row = _row_of(i.cell)
        where = f"INPUT row {row}" if row else "INPUT"
        msg = i.msg
        if i.cell == "C6":
            continue  # handled by the month check
        if msg.startswith("Unknown allowed shift"):
            rep.warnings.append(f"{where}: {msg.replace('Unknown allowed shift', 'The allowed or preferred shift')} is not in the shift list and was ignored.")
        elif msg.startswith("Unknown shift code"):
            sec = "Demand" if 277 <= row <= 336 else "Fixed shifts"
            rep.skipped.append(SkippedRow(where, sec, f"shift '{msg.split('code', 1)[1].strip()}' is not in the shift list"))
        elif re.match(r"Shift .*start/end hours missing", msg):
            rep.skipped.append(SkippedRow(where, "Shifts", "start or end hour is missing"))
        elif i.cell.startswith("E6"):
            rep.warnings.append("The weekday names in the workbook are not SUN..SAT; the app always uses SUN..SAT.")
        else:
            rep.warnings.append(f"{where}: {msg}")


# ----------------------------------------------------------------------------- the import
def load_inputs_into_store(store: Store, inp: RosterInputs, create_month: bool = True, *,
                           month=None, shift_raw_names: dict[int, str] | None = None,
                           rep: ImportReport | None = None) -> ImportReport:
    """Write ``inp`` into ``store``: master lists + the month (``month`` or ``inp.settings.first_day``) with its
    leave, fixed shifts and demand.  Row-level problems go into ``rep.skipped`` / ``rep.warnings``; the caller
    owns the transaction."""
    st = inp.settings
    rep = rep or ImportReport()
    m = store.master
    m.update_settings(
        week_start_name=st.week_start_name, fri_name=st.fri_name, max_week_h=st.max_week_h, max_shift_h=st.max_shift_h,
        min_rest=st.min_rest, min_rest_split=st.min_rest_split, male_from=st.male_from, male_to=st.male_to,
        days_off_wk=int(st.days_off_wk), carry_in=st.carry_in, weekend_days=st.weekend_days, ext_max=st.ext_max,
        ot_on=st.ot_on, ot_max=st.ot_max, ot_keep=st.ot_keep, strict_on=st.strict_on)
    rep.counts["settings"] = 16

    # receptions: the listed ones first, then names that are only used elsewhere
    rec_uid: dict[str, int] = {}
    listed = {_up(r.name) for r in inp.receptions}
    extra_src = [(t.rec, f"INPUT row {t.row}") for t in inp.targets] + \
                [(e.home, f"INPUT row {e.row}") for e in inp.employees] + [(e.also, f"INPUT row {e.row}") for e in inp.employees]
    for n in [r.name for r in inp.receptions] + [n for n, _ in extra_src]:
        if n and _up(n) not in rec_uid:
            rec_uid[_up(n)] = m.add_reception(n.strip()).uid
            if _up(n) not in listed:
                where = next(w for nn, w in extra_src if _up(nn) == _up(n))
                rep.warnings.append(f"{where}: the reception '{n.strip()}' is not in the reception list; it was added.")
    rep.counts["receptions"] = len(rec_uid)

    # shifts
    shift_uid: dict[str, int] = {}
    n_shifts = 0
    for s in inp.shifts:
        s2 = None if s.start2 < 0 else s.start2
        e2 = None if s.end2 < 0 else s.end2
        try:
            rec = m.add_shift(start1=s.start1, end1=s.end1, start2=s2, end2=e2, paid=s.paid)
            n_shifts += 1
            raw = (shift_raw_names or {}).get(s.row, s.code)
            if raw != rec.code:
                rep.warnings.append(f"INPUT row {s.row}: shift imported as '{rec.code}' (workbook said '{raw}'); "
                                    "in the app the name always follows the times.")
        except DuplicateError:
            code = shift_code_from_times(s.start1, s.end1, s2, e2)
            rec = next(x for x in m.list_shifts() if x.code == code)
            rep.skipped.append(SkippedRow(f"INPUT row {s.row}", "Shifts",
                                          f"'{s.code}' has the same times as '{rec.code}', which is already imported"))
        shift_uid[_up(s.code)] = rec.uid
    rep.counts["shifts"] = n_shifts

    # coverage rows
    n = 0
    for t in inp.targets:
        try:
            m.add_target(rec_uid=rec_uid[_up(t.rec)], frm_h=t.frm, to_h=t.to,
                         min_staff=None if t.min_staff < 0 else t.min_staff,
                         min_male=None if t.min_male < 0 else t.min_male,
                         max_staff=None if t.max_staff < 0 else t.max_staff, days=t.days or "ALL")
            n += 1
        except StoreError as exc:
            rep.skipped.append(SkippedRow(f"INPUT row {t.row}", "Coverage", str(exc)))
    rep.counts["coverage"] = n

    # employees
    emp_uid: dict[str, int] = {}
    for e in inp.employees:
        if not e.home or _up(e.home) not in rec_uid:
            rep.skipped.append(SkippedRow(f"INPUT row {e.row}", "Employees", f"{e.name} has no home reception"))
            continue
        try:
            rec = m.add_employee(
                name=e.name, gender=e.gender, home_uid=rec_uid[_up(e.home)],
                also_uid=rec_uid.get(_up(e.also)) if e.also else None, active=e.active, fixed_off=e.fixed_off,
                fixed_off2=e.fixed_off2, fri=e.fri, allowed=[shift_uid[_up(c)] for c in e.allowed if _up(c) in shift_uid],
                pref=[shift_uid[_up(c)] for c in e.pref if _up(c) in shift_uid],
                max_h=None if (e.max_h or 0) == st.max_week_h else e.max_h, ot=e.ot, no_we=e.no_we, notes=e.notes)
            emp_uid[e.key] = rec.uid
        except DuplicateError:
            rep.skipped.append(SkippedRow(f"INPUT row {e.row}", "Employees", f"{e.name} is listed twice; the first one is kept"))
        except StoreError as exc:
            rep.skipped.append(SkippedRow(f"INPUT row {e.row}", "Employees", f"{e.name}: {exc}"))
    rep.counts["employees"] = len(emp_uid)

    rep.counts.update({"leave": 0, "fixed": 0, "demand": 0})
    if not create_month:
        return rep
    d0 = month if month is not None else st.first_day
    y, mo = month_key(d0)
    store.create_month(y, mo)
    sc = store.month(y, mo)
    rep.year, rep.month = y, mo
    first = date(y, mo, 1)
    last = date.fromordinal(date(y + (mo == 12), mo % 12 + 1, 1).toordinal() - 1)
    for lv in inp.leave:
        where = f"INPUT row {lv.row}"
        if _up(lv.emp) not in emp_uid:
            rep.skipped.append(SkippedRow(where, "Leave", f"'{lv.emp.title()}' is not in the employee list"))
        elif lv.to < first:
            rep.skipped.append(SkippedRow(where, "Leave", f"{lv.emp.title()}'s leave ended {lv.to:%d %b %Y}, before {first:%B %Y}"))
        elif lv.to < lv.frm:
            rep.skipped.append(SkippedRow(where, "Leave", "the leave ends before it starts"))
        else:
            sc.add_leave(emp_uid=emp_uid[_up(lv.emp)], from_date=lv.frm, to_date=lv.to, kind=lv.kind)
            rep.counts["leave"] += 1
            if lv.frm > last:
                rep.warnings.append(f"{where}: {lv.emp.title()}'s leave starts after {first:%B %Y}; "
                                    "it is kept for the later months.")
    for f in inp.fixed:
        where = f"INPUT row {f.row}"
        if _up(f.emp) not in emp_uid:
            rep.skipped.append(SkippedRow(where, "Fixed shifts", f"'{f.emp.title()}' is not in the employee list"))
        elif _up(f.shift) not in shift_uid:
            rep.skipped.append(SkippedRow(where, "Fixed shifts", f"shift '{f.shift}' is not in the shift list"))
        elif not first <= f.date <= last:
            rep.skipped.append(SkippedRow(where, "Fixed shifts", f"{f.date:%d %b %Y} is not in {first:%B %Y}"))
        elif f.rec and _up(f.rec) not in rec_uid:
            rep.skipped.append(SkippedRow(where, "Fixed shifts", f"reception '{f.rec}' is not in the reception list"))
        else:
            sc.add_fixed(emp_uid=emp_uid[_up(f.emp)], day=f.date, shift_uid=shift_uid[_up(f.shift)],
                         rec_uid=rec_uid.get(_up(f.rec)) if f.rec else None)
            rep.counts["fixed"] += 1
    for d in inp.demand:
        where = f"INPUT row {d.row}"
        if _up(d.shift) not in shift_uid or _up(d.rec) not in rec_uid:
            rep.skipped.append(SkippedRow(where, "Demand", "the reception or the shift is not in the lists"))
            continue
        sc.add_demand(rec_uid=rec_uid[_up(d.rec)], shift_uid=shift_uid[_up(d.shift)], gender=d.gender, fill=d.fill,
                      prio=d.prio, counts=list(d.counts), active=True)
        rep.counts["demand"] += 1
    return rep


def import_excel(store: Store, path, mode: str = "empty", month=None, confirmed: bool = False) -> ImportReport:
    """Import an INPUT workbook (``.xlsx`` / ``.xlsm``) into ``store``; see the module docstring.

    Raises :class:`ImportError` (a :class:`StoreError`) with a friendly message; the store is left unchanged then."""
    if mode not in ("empty", "replace"):
        raise ImportError("Import mode must be 'empty' or 'replace'.")
    if mode == "replace" and not confirmed:
        raise ImportError("Replacing the existing data must be confirmed first. Nothing was changed.")
    if mode == "empty" and store.has_master_data():
        raise ImportError("Data already exists – choose Replace to overwrite.")
    wb = _open_workbook(path)
    try:
        inp = RosterInputs(wb)
    except Exception:  # noqa: BLE001
        raise ImportError(f"'{Path(str(path)).name}' has an INPUT sheet this app cannot read.") from None
    ws = wb["INPUT"]
    if month is not None:
        try:
            y, mo = month_key(month)
            date(y, mo, 1)
        except (ValueError, TypeError, IndexError):
            raise ImportError("The month to import is not valid. Use a year and a month, e.g. 2026-11.") from None
        target = (y, mo)
    elif inp.settings.first_day is not None:
        target = (inp.settings.first_day.year, inp.settings.first_day.month)
    else:
        raise ImportError("The month cell of the INPUT sheet (C6) is empty, so the app cannot tell which month this is. "
                          "Enter a date in that month in the workbook and try again.")
    if store.has_month(target) and (mode == "empty" or store.get_month(target).status == "built"):
        raise ImportError(f"The month {target[0]}-{target[1]:02d} already exists and is built, so it cannot be replaced. "
                          "Import with a different month.")
    rep = ImportReport(mode=mode, source=str(path))
    raw_names = {s.row: str(ws.cell(row=s.row, column=2).value or "") for s in inp.shifts}
    _classify_reader_issues(inp, rep)
    try:
        with store._tx():
            if mode == "replace":
                rep.drafts_removed = store.wipe_master_and_drafts()
            load_inputs_into_store(store, inp, month=target, shift_raw_names=raw_names, rep=rep)
    except StoreError as exc:
        raise ImportError(f"The import failed and nothing was changed: {exc}") from None
    try:
        from .inputs_from_store import build_inputs
        from .validate import run_input_check

        rep.findings = [f.text_for_user() for f in run_input_check(build_inputs(store, *target))]
    except Exception as exc:  # noqa: BLE001 - the import itself succeeded
        rep.warnings.append(f"The input check of the new month could not run ({type(exc).__name__}).")
    return rep
