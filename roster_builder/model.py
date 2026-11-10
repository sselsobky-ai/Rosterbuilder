"""Domain model for the Python roster builder.

Faithful port of the concepts in ``RosterBuilder.bas``:

- weekday numbering (VBA ``Weekday(date, vbSunday)`` -> 1..7, Sunday = 1)
- shift hour intervals, split parts and the male-only window
- the dataclasses used by the builder (Settings, Reception, CoverageTarget, Shift,
  Employee, Leave, Demand, FixedShift, Issue, Unfilled, BuildResult, Roster)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .locate import Locator

# VBA: Weekday(..., vbSunday) -> 1 = Sunday, 2 = Monday, ... 7 = Saturday
WEEKDAY_NAMES: tuple[str, ...] = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")


def vba_weekday(d: date) -> int:
    """Return the VBA weekday number of ``d`` (Sunday=1 .. Saturday=7)."""
    return ((d.weekday() + 1) % 7) + 1


def weekday_index(name: str) -> int:
    """1-based weekday index for a weekday name such as 'FRI' (0 if unknown)."""
    u = (name or "").strip().upper()
    for i, n in enumerate(WEEKDAY_NAMES, start=1):
        if n == u:
            return i
    return 0


def day_label(idx: int, names: tuple[str, ...] = WEEKDAY_NAMES) -> str:
    """Weekday name for a 1..7 VBA weekday number."""
    return names[idx - 1]


def in_list(code: str, lst: str) -> bool:
    """True if ``code`` is one of the comma-separated codes in ``lst``."""
    lst = (lst or "").strip().upper()
    if not lst:
        return False
    code_u = (code or "").strip().upper()
    return any(p.strip().upper() == code_u for p in lst.split(","))


def covers(shift: "Shift", hour: float) -> bool:
    """Does ``shift`` cover ``hour``? Handles split parts and shifts that end the
    next day (end <= start means end += 24)."""
    ok = (hour >= shift.start1 and hour < shift.end1) or (hour + 24.0 >= shift.start1 and hour + 24.0 < shift.end1)
    if shift.start2 >= 0:
        ok = ok or (hour >= shift.start2 and hour < shift.end2) or (hour + 24.0 >= shift.start2 and hour + 24.0 < shift.end2)
    return ok


def covered_hours(shift: "Shift") -> set[int]:
    """Absolute hour slots ``h`` (slot ``[h, h+1)`` counted from 00:00 of the shift's start day) that
    the shift covers. A slot counts when its midpoint lies inside a part; hours >= 24 are the
    next day (night shifts spill there). Split shifts cover only their two parts."""
    out: set[int] = set()
    parts = [(shift.start1, shift.end1)]
    if shift.start2 >= 0:
        parts.append((shift.start2, shift.end2))
    for s, e in parts:
        h = int(s // 1)
        while h < e:
            if s <= h + 0.5 < e:
                out.add(h)
            h += 1
    return out


def male_overlap(shift: "Shift", male_from: float, male_to: float) -> float:
    """Hours of ``shift`` that fall inside the male-only window (C13 -> C14, may wrap midnight)."""

    def overlap(s: float, e: float, ws: float, we: float) -> float:
        lo = max(s, ws)
        hi = min(e, we)
        return hi - lo if hi > lo else 0.0

    def part(s: float, e: float) -> float:
        if male_from < male_to:
            # window inside a single day
            return overlap(s, e, male_from, male_to)
        # window wraps past midnight (male_from -> male_to, e.g. 22 -> 06):
        # split the shift part into day segments and count male hours per segment.
        total = 0.0
        seg_start = s
        while seg_start < e:
            day_idx = int(seg_start // 24.0)
            day_begin = day_idx * 24.0
            day_end = day_begin + 24.0
            seg_end = min(e, day_end)
            total = total + overlap(seg_start, seg_end, day_begin + male_from, day_end) + overlap(seg_start, seg_end, day_begin, day_begin + male_to)
            seg_start = seg_end
        return total

    n = part(shift.start1, shift.end1)
    if shift.start2 >= 0:
        n = n + part(shift.start2, shift.end2)
    return n


def hour_txt(x: float) -> str:
    """'10 PM' -> '22', '12 MN' -> '0', '06:30 AM' -> '6:30' (end of day = 24)."""
    hh = x
    while hh >= 24:
        hh = hh - 24
    hi = int(hh)
    mi = int(round((hh - hi) * 60))
    if hi == 0 and mi == 0:
        return "12 MN"
    h12 = hi % 12
    if h12 == 0:
        h12 = 12
    t = f"{h12:02d}"
    if mi > 0:
        t = t + f":{mi:02d}"
    t = t + (" AM" if hi < 12 else " PM")
    return t


def shift_code_from_times(start1: float, end1: float, start2: float | None = None, end2: float | None = None) -> str:
    """Shift code generated from the times, in the workbook's text format.

    One part: ``"10 PM - 06 AM"``; split: ``"08AM-12PM & 04PM-08PM"`` (no blanks inside a part).
    ``start2`` ``None`` or negative = no second part.
    """
    if start2 is None or end2 is None or start2 < 0:
        return f"{hour_txt(start1)} - {hour_txt(end1)}"
    a = f"{hour_txt(start1)}-{hour_txt(end1)}".replace(" ", "")
    b = f"{hour_txt(start2)}-{hour_txt(end2)}".replace(" ", "")
    return f"{a} & {b}"


def num(v, dflt: float = 0.0) -> float:
    """Excel-ish Num(): numeric and non-empty -> float, else default."""
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


def max_l(a: float, b: float) -> float:
    return a if a > b else b


@dataclass
class Settings:
    month_start: date | None = None
    first_day: date | None = None
    days_in_month: int = 0
    max_week_h: float = 40.0
    min_rest: float = 8.0
    male_from: float = 22.0
    male_to: float = 6.0
    days_off_wk: int = 2
    max_shift_h: float = 8.0
    min_rest_split: float = 0.0
    carry_in: float = 0.0
    ot_on: bool = True
    ot_max: float = 168.0
    ot_keep: bool = True
    weekend_days: str = "FRI,SAT"
    strict_on: bool = True
    ext_max: float = 6.0
    week_start_name: str = "SAT"
    fri_name: str = "FRI"
    male_code: str = "M"
    female_code: str = "F"
    weekday_names: tuple[str, ...] = WEEKDAY_NAMES

    def week1_start(self) -> date | None:
        """First day of week 1 (weeks start on the 'Week starts on' day)."""
        if self.first_day is None:
            return None
        idx = weekday_index(self.week_start_name)
        v = vba_weekday(self.first_day)
        offset = (v - idx + 7) % 7
        return self.first_day - timedelta(days=offset)

    def wk_of(self, d: date) -> int:
        """Week number of ``d`` (weeks start on the 'Week starts on' day)."""
        return int((d - self.week1_start()).days // 7) + 1

    def weekend_day_indexes(self) -> list[int]:
        out: list[int] = []
        for part in (self.weekend_days or "").split(","):
            i = weekday_index(part)
            if i:
                out.append(i)
        return out

    def is_weekend(self, d: date) -> bool:
        idx = vba_weekday(d)
        return idx in self.weekend_day_indexes()


@dataclass
class Reception:
    name: str
    uid: int = 0  # store id (0 = not from the store)


@dataclass
class CoverageTarget:
    rec: str
    frm: float
    to: float
    min_staff: float
    min_male: float
    max_staff: float
    days: str
    row: int = 0
    uid: int = 0

    def applies(self, d: date, fri_name: str, names: tuple[str, ...] = WEEKDAY_NAMES) -> bool:
        if not (self.days or "").strip() or (self.days or "").strip().upper() == "ALL":
            return True
        dn = day_label(vba_weekday(d), names)
        if (self.days or "").strip().upper() == "WEEKDAYS":
            return dn != fri_name
        return in_list(dn, self.days)


@dataclass
class Shift:
    code: str
    start1: float
    end1: float
    start2: float
    end2: float
    paid: float
    ext: float = 0.0
    male: float = 0.0
    row: int = 0
    uid: int = 0

    def __post_init__(self) -> None:
        if self.end1 <= self.start1:
            self.end1 = self.end1 + 24.0
        if self.start2 >= 0 and self.end2 <= self.start2:
            self.end2 = self.end2 + 24.0

    def covers(self, hour: float) -> bool:
        return covers(self, hour)


@dataclass
class Employee:
    name: str
    gender: str = ""
    home: str = ""
    also: str = ""
    active: bool = True
    fixed_off: str = ""
    fri: bool = False
    allowed: list[str] = field(default_factory=list)
    pref: list[str] = field(default_factory=list)
    max_h: float = 0.0
    ot: bool = False
    no_we: bool = False
    fixed_off2: str = ""
    notes: str = ""
    row: int = 0
    uid: int = 0
    ot_rank: int | None = None  # overtime priority 1 (first) .. 10; None = unranked (only with ot)

    def is_male(self, male_code: str) -> bool:
        return (self.gender or "").upper() == (male_code or "").upper()

    @property
    def key(self) -> str:
        """Normalised employee key (name, trimmed, upper case) for case-insensitive lookups."""
        return (self.name or "").strip().upper()


@dataclass
class Leave:
    emp: str
    frm: date
    to: date
    kind: str = "LEAVE"
    row: int = 0
    uid: int = 0


@dataclass
class Demand:
    rec: str
    shift: str
    gender: str
    fill: str
    prio: float
    counts: list[int]
    all_week: bool
    row: int = 0
    uid: int = 0


@dataclass
class FixedShift:
    emp: str
    date: date
    shift: str
    rec: str = ""
    row: int = 0
    uid: int = 0


@dataclass
class LockedCell:
    """A manual roster edit the user pinned (milestone 12): the solver keeps it exactly as a fixed shift.

    ``shift`` is a shift code, ``""`` = the person must be OFF that day. ``ot`` marks the duty as overtime;
    ``ext_before_h`` / ``ext_after_h`` are whole extension hours (always overtime).
    """

    emp: str
    date: date
    shift: str = ""
    rec: str = ""
    ot: bool = False
    ext_before_h: int = 0
    ext_after_h: int = 0
    emp_uid: int = 0


@dataclass
class Issue:
    """A finding of the input check, the solver diagnosis or the checks.

    ``cell`` / ``msg`` / ``fix`` are the original (Excel-flavoured) values. For inputs that come
    from the data store, :func:`roster_builder.locate.localize_issues` fills ``where`` (field
    locators) and ``fix_set`` (machine-readable auto-fix) and rewrites ``msg`` / ``fix`` without any
    Excel cell reference; ``text_for_user()`` is the text the app shows.
    """

    cell: str
    msg: str
    fix: str = ""
    id: str = ""
    where: list[Locator] = field(default_factory=list)
    fix_set: list = field(default_factory=list)  # list[locate.FieldSet]

    def text_for_user(self) -> str:
        """Message with the field labels instead of Excel cell references."""
        from .locate import issue_text

        return issue_text(self)

    def where_text(self) -> str:
        """``Screen › Record › Field`` of the places to look at (shown as a small grey second line)."""
        from .locate import where_text

        return where_text(self)


@dataclass
class Unfilled:
    date: date
    day_lbl: str
    rec: str
    code: str
    gender: str
    reason: str


@dataclass
class BuildResult:
    ok: bool
    message: str = ""
    unfilled: list[Unfilled] = field(default_factory=list)
    relaxed: list[str] = field(default_factory=list)
    moved: int = 0
    overtime_shifts: int = 0
    gap_fills: int = 0
    fixed_placed: int = 0
    issues: list[Issue] = field(default_factory=list)


@dataclass
class Assignment:
    """One person working one shift on one day at one reception (solver output)."""

    emp_key: str
    date: date
    shift_code: str
    rec: str
    is_ot: bool = False
    is_helper: bool = False
    ext_before_h: int = 0  # whole overtime hours added BEFORE the shift start (extension)
    ext_after_h: int = 0  # whole overtime hours added AFTER the shift end (extension)

    @property
    def ext_total_h(self) -> int:
        """Total extension hours (all of them overtime)."""
        return self.ext_before_h + self.ext_after_h

    def ext_text(self) -> str:
        """``"(EXT +2h OT)"`` for an extension, ``""`` without one."""
        if not self.ext_total_h:
            return ""
        return f"(EXT +{self.ext_total_h}h OT)"

    def text(self, shift: "Shift | None" = None) -> str:
        """Schedule cell text (BUILD_GUIDE section 7): code, extension, ``" @REC"`` helper, ``" [OT]"``.

        With an extension the label shows the NEW times, e.g. ``"08 PM - 06 AM (EXT +2h OT)"``;
        ``shift`` (the base :class:`Shift`) is then needed to compute them.
        """
        out = self.shift_code
        if self.ext_total_h:
            if shift is not None:
                out = f"{hour_txt(shift.start1 - self.ext_before_h)} - {hour_txt(shift.end1 + self.ext_after_h)}"
            out += " " + self.ext_text()
        if self.is_helper:
            out += f" @{self.rec}"
        if self.is_ot:
            out += " [OT]"
        return out


@dataclass
class Roster:
    """Assignment state: employee (by name) -> day (date) -> shift code / helper / OT.

    The solver (``solver.solve``) fills ``assignments``, ``status``, ``objective`` (breakdown)
    and ``issues`` (infeasibility report); the older dict fields are kept for the builder.
    """

    assignments: list[Assignment] = field(default_factory=list)
    status: str = ""
    objective: dict[str, float] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    blocks: dict = field(default_factory=dict)  # employee key -> DayOff actually used by the solve
    passes: int = 1  # 2 when the MarkIdle second pass was used

    asg: dict[tuple[str, date], str] = field(default_factory=dict)
    help: dict[tuple[str, date], str] = field(default_factory=dict)
    is_ot: dict[tuple[str, date], bool] = field(default_factory=dict)
    is_off: set[tuple[str, date]] = field(default_factory=set)
