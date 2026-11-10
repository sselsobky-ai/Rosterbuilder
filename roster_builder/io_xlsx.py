"""Read the roster workbook INPUT sheet into the in-memory model.

Only the INPUT sheet is read (BUILD_GUIDE.md section 2).  Hidden mirror sheets
(SETTINGS, SHIFTS, EMPLOYEES, DEMAND, RECEPTIONS, LEAVE) only hold formulas such
as ``=IF(INPUT!x="","",INPUT!x)`` which give stale or empty values in openpyxl.

Cell map (INPUT sheet):

  Settings      C6 month (any date in it) · C7 week starts on · C8 Friday name · C9
                max h/week · C10 max h/shift · C11 min rest h · C12 min split rest h ·
                C13/C14 men-only from/until · C15 days off/week · C16 day-1 carry-in h ·
                C17 weekend days (comma list) · C18 max extension h
  Weekday names E6:E12  (SUN..SAT)
  Gender codes  G6 (male) · G7 (female)
  OT / strict   G14 (OT master ON/OFF) · G15 (OT max h/week) · G16 (keep 1 free day)
                · G17 (strict Y/N)
  Receptions    B21:B28 (names)
  Coverage tgt  D21:J60 (D rec · E from h · F to h (end<=start = next day) · G min staff
                · H min male · I max staff · J days (ALL / WEEKDAYS / list))
  Shifts        B65:G104 (B code · C start1 · D end1 · E start2 · F end2 (split) · G paid h)
  Employees     B109:Q168 (B name · C gender · D home · E can-also-cover · F active · G fixed
                day off (AUTO = none) · H Friday eligible · I:K allowed 1-3 (blank = all) ·
                L:M preferred 1-2 · N max h/week (blank = C9) · O overtime ON/OFF · P no weekends
                · Q fixed day off 2)
  Leave         B173:E272 (B employee · C from · D to · E type)
  Demand        B277:O336 (B active · C rec · D shift · E gender · F fill · G priority ·
                H:N counts SUN..SAT (number or ALL) · O note)
  Fixed shifts B341:E400 (B employee · C date · D shift · E reception (blank = home))
"""

from __future__ import annotations

from datetime import date

import openpyxl

from .model import (
    CoverageTarget,
    Demand,
    Employee,
    FixedShift,
    Issue,
    Leave,
    Reception,
    Settings,
    Shift,
    WEEKDAY_NAMES,
    male_overlap,
)


def _cell(ws, row: int, col: int):
    return ws.cell(row=row, column=col)


def _val(ws, row: int, col: int):
    v = _cell(ws, row, col).value
    if hasattr(v, "year") and hasattr(v, "month") and hasattr(v, "day"):
        return date(v.year, v.month, v.day)
    return v


def _s(ws, row: int, col: int) -> str:
    v = _val(ws, row, col)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Y" if v else "N"
    return str(v).strip().upper()


def _n(ws, row: int, col: int, dflt: float = 0.0) -> float:
    v = _val(ws, row, col)
    if v is None or v == "":
        return dflt
    try:
        return float(str(v))
    except (ValueError, TypeError):
        return dflt


def _d(ws, row: int, col: int):
    v = _val(ws, row, col)
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    return None


def _split_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip().upper() for x in v if str(x).strip()]
    s = str(v).strip().upper()
    if not s:
        return []
    return [p.strip().upper() for p in s.split(",") if p.strip()]


class RosterInputs:
    """Everything the builder needs, read from the INPUT sheet of the workbook."""

    #: where the data came from: "xlsx" (the INPUT sheet) or "store" (the SQLite data store)
    source: str = "xlsx"
    #: ``locate.RowIndex`` (synthetic row -> record) for inputs built from the store
    row_index = None
    #: manual roster edits pinned by the user (``model.LockedCell``); the solver treats them as fixed shifts
    locked: tuple = ()

    def __init__(self, wb: "openpyxl.Workbook | None" = None) -> None:
        self.issues: list[Issue] = []
        if wb is None:  # empty container, filled by inputs_from_store
            self.settings = Settings()
            self.receptions: list[Reception] = []
            self.targets: list[CoverageTarget] = []
            self.shifts: list[Shift] = []
            self.employees: list[Employee] = []
            self.leave: list[Leave] = []
            self.demand: list[Demand] = []
            self.fixed: list[FixedShift] = []
            self.shift_by_key: dict[str, Shift] = {}
            self.emp_by_key: dict[str, Employee] = {}
            return
        ws = wb["INPUT"]
        self.settings = self._read_settings(ws)
        self.receptions: list[Reception] = []
        self.targets: list[CoverageTarget] = []
        self.shifts: list[Shift] = []
        self.employees: list[Employee] = []
        self.leave: list[Leave] = []
        self.demand: list[Demand] = []
        self.fixed: list[FixedShift] = []
        self._read_receptions(ws)
        self._read_shifts(ws)
        self.shift_by_key: dict[str, Shift] = {s.code.strip().upper(): s for s in self.shifts}
        self._read_employees(ws)
        self._read_leave(ws)
        self._read_demand(ws)
        self._read_fixed(ws)
        self.emp_by_key: dict[str, Employee] = {e.key: e for e in self.employees}

    def emp_by_name(self, name: str) -> "Employee | None":
        """Normalised employee lookup (case-insensitive)."""
        return self.emp_by_key.get((name or "").strip().upper())

    def _canon_shift(self, code: str) -> str:
        """Canonical shift code: the Shift object's code when known, else the upper-cased text (R3.3)."""
        u = (code or "").strip().upper()
        sh = self.shift_by_key.get(u)
        return sh.code if sh is not None else u

    # ------------------------------------------------------------------ settings
    def _read_settings(self, ws) -> Settings:
        month_start = _d(ws, 6, 3)
        if month_start is None:
            # R2.6 / R3.4: a missing month is an Issue on C6 (never a silent fallback to
            # November); the build stops because the input check reports this finding.
            self.issues.append(Issue(cell="C6", msg="Settings: month start date is missing or not a date (INPUT C6)", fix="Enter the month"))
            month_start = None
        first_day = date(month_start.year, month_start.month, 1) if month_start else None
        import calendar

        days_in_month = calendar.monthrange(month_start.year, month_start.month)[1] if month_start else 0
        names: list[str] = []
        bad_names = False
        for i in range(7):
            nm = _s(ws, 6 + i, 5)
            if not nm or nm not in WEEKDAY_NAMES:
                bad_names = True
                nm = WEEKDAY_NAMES[i]
            names.append(nm)
        if bad_names:
            # R2.7: weekday names must be exactly SUN..SAT.
            self.issues.append(Issue(cell="E6:E12", msg="Weekday names must be SUN..SAT", fix="Set E6:E12 to SUN..SAT"))
        return Settings(
            month_start=month_start,
            first_day=first_day,
            days_in_month=days_in_month,
            max_week_h=_n(ws, 9, 3, 40.0),
            min_rest=_n(ws, 11, 3, 8.0),
            male_from=_n(ws, 13, 3, 22.0),
            male_to=_n(ws, 14, 3, 6.0),
            days_off_wk=int(round(_n(ws, 15, 3, 2.0))),
            max_shift_h=_n(ws, 10, 3, 8.0),
            min_rest_split=_n(ws, 12, 3, 0.0),
            carry_in=_n(ws, 16, 3, 0.0),
            ot_on=_s(ws, 14, 7) == "ON",
            ot_max=_n(ws, 15, 7, 168.0),
            ot_keep=_s(ws, 16, 7) != "N",
            weekend_days=_s(ws, 17, 3) or "FRI,SAT",
            strict_on=_s(ws, 17, 7) != "N",
            ext_max=_n(ws, 18, 3, 6.0),
            week_start_name=_s(ws, 7, 3) or "SAT",
            fri_name=_s(ws, 8, 3) or "FRI",
            male_code=_s(ws, 6, 7) or "M",
            female_code=_s(ws, 7, 7) or "F",
            weekday_names=tuple(names),
        )

    # ---------------------------------------------------------------- receptions
    def _read_receptions(self, ws) -> None:
        for r in range(21, 29):
            nm = str(_cell(ws, r, 2).value or "").strip()
            if nm:
                self.receptions.append(Reception(name=nm))
        for r in range(21, 61):
            rec = str(_cell(ws, r, 4).value or "").strip()
            if not rec:
                continue
            self.targets.append(
                CoverageTarget(
                    rec=rec,
                    frm=_n(ws, r, 5, 0.0),
                    to=_n(ws, r, 6, 0.0),
                    min_staff=_n(ws, r, 7, -1.0),
                    min_male=_n(ws, r, 8, -1.0),
                    max_staff=_n(ws, r, 9, -1.0),
                    days=str(_cell(ws, r, 10).value or ""),
                    row=r,
                )
            )

    # -------------------------------------------------------------------- shifts
    def _read_shifts(self, ws) -> None:
        for r in range(65, 105):
            code = str(_cell(ws, r, 2).value or "").strip()
            if not code:
                continue
            s1 = _n(ws, r, 3, -1.0)
            e1 = _n(ws, r, 4, -1.0)
            s2 = _n(ws, r, 5, -1.0)
            e2 = _n(ws, r, 6, -1.0)
            if s1 < 0 or e1 < 0 or (s2 >= 0 and e2 < 0) or (s2 < 0 and e2 >= 0):
                # R2.5: code but blank start/end -> do not build a phantom shift, report instead.
                self.issues.append(Issue(cell=f"C{r}/D{r}", msg=f"Shift {code}: start/end hours missing", fix="Set start and end hours"))
                continue
            sh = Shift(code=code, start1=s1, end1=e1, start2=s2, end2=e2, paid=_n(ws, r, 7, 8.0), ext=0.0, male=0.0, row=r)
            sh.male = male_overlap(sh, self.settings.male_from, self.settings.male_to)
            self.shifts.append(sh)

    # ----------------------------------------------------------------- employees
    def _read_employees(self, ws) -> None:
        for r in range(109, 169):
            name = str(_cell(ws, r, 2).value or "").strip()
            if not name:
                continue
            allowed_raw = _split_list(_cell(ws, r, 9).value) + _split_list(_cell(ws, r, 10).value) + _split_list(_cell(ws, r, 11).value)
            pref_raw = _split_list(_cell(ws, r, 12).value) + _split_list(_cell(ws, r, 13).value)
            for code in allowed_raw + pref_raw:
                if code not in self.shift_by_key:
                    # R2.4: unknown shift code -> Issue, never a silent miss.
                    self.issues.append(Issue(cell=f"I{r}", msg=f"Unknown allowed shift {code}", fix="Set the shift code correctly"))
            emp = Employee(
                name=name,
                gender=str(_cell(ws, r, 3).value or "").strip().upper(),
                home=str(_cell(ws, r, 4).value or "").strip().upper(),
                also=str(_cell(ws, r, 5).value or "").strip().upper(),
                active=str(_cell(ws, r, 6).value or "").strip().upper() != "N",
                fixed_off=str(_cell(ws, r, 7).value or "").strip().upper(),
                fri=str(_cell(ws, r, 8).value or "").strip().upper() == "Y",
                allowed=[self._canon_shift(c) for c in allowed_raw],
                pref=[self._canon_shift(c) for c in pref_raw],
                max_h=_n(ws, r, 14, self.settings.max_week_h),
                ot=str(_cell(ws, r, 15).value or "").strip().upper() == "ON",
                no_we=str(_cell(ws, r, 16).value or "").strip().upper() == "Y",
                fixed_off2=str(_cell(ws, r, 17).value or "").strip().upper(),
                notes=str(_cell(ws, r, 18).value or ""),
                row=r,
            )
            if emp.fixed_off == "AUTO":
                emp.fixed_off = ""
            if emp.fixed_off2 == emp.fixed_off and emp.fixed_off2:
                emp.fixed_off2 = ""
            if not emp.fixed_off and emp.fixed_off2:
                emp.fixed_off = emp.fixed_off2
                emp.fixed_off2 = ""
            self.employees.append(emp)

    # ---------------------------------------------------------------------- leave
    def _read_leave(self, ws) -> None:
        for r in range(173, 273):
            emp = str(_cell(ws, r, 2).value or "").strip().upper()
            frm = _d(ws, r, 3)
            to = _d(ws, r, 4)
            kind = str(_cell(ws, r, 5).value or "").strip().upper()
            if emp and frm and to:
                self.leave.append(Leave(emp=emp, frm=frm, to=to, kind=kind or "LEAVE", row=r))

    # --------------------------------------------------------------------- demand
    def _read_demand(self, ws) -> None:
        for r in range(277, 337):
            active = str(_cell(ws, r, 2).value or "").strip().upper()
            rec = str(_cell(ws, r, 3).value or "").strip().upper()
            code = str(_cell(ws, r, 4).value or "").strip()
            if active == "N" or not rec or not code:
                # R2.2: only "N" makes a demand row inactive.
                continue
            if code.strip().upper() not in self.shift_by_key:
                # R2.4: unknown shift code -> Issue, never a silent miss.
                self.issues.append(Issue(cell=f"D{r}", msg=f"Unknown shift code {code}", fix="Set the shift code correctly"))
                continue
            counts: list[int] = []
            all_week = False
            for j in range(7):
                c = str(_cell(ws, r, 8 + j).value or "").strip().upper()
                if c == "ALL":
                    all_week = True
                    counts.append(-1)
                else:
                    try:
                        counts.append(int(round(float(c))) if c else 0)
                    except ValueError:
                        counts.append(0)
            self.demand.append(
                Demand(
                    rec=rec,
                    shift=self._canon_shift(code),
                    gender=str(_cell(ws, r, 5).value or "").strip().upper() or "ANY",
                    fill=str(_cell(ws, r, 6).value or "").strip().upper() or "HOME",
                    prio=_n(ws, r, 7, 99.0),
                    counts=counts,
                    all_week=all_week,
                    row=r,
                )
            )

    # --------------------------------------------------------------------- fixed
    def _read_fixed(self, ws) -> None:
        for r in range(341, 401):
            emp = str(_cell(ws, r, 2).value or "").strip().upper()
            d = _d(ws, r, 3)
            code = str(_cell(ws, r, 4).value or "").strip()
            if not emp or not d or not code:
                continue
            if code.strip().upper() not in self.shift_by_key:
                # R2.4: unknown shift code -> Issue, never a silent miss.
                self.issues.append(Issue(cell=f"D{r}", msg=f"Unknown shift code {code}", fix="Set the shift code correctly"))
                continue
            self.fixed.append(FixedShift(emp=emp, date=d, shift=self._canon_shift(code), rec=str(_cell(ws, r, 5).value or "").strip().upper(), row=r))


def load_inputs(path: str) -> RosterInputs:
    wb = openpyxl.load_workbook(path, data_only=True)
    return RosterInputs(wb)
