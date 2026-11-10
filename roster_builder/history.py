"""Month-to-month state (BUILD_GUIDE section 7 ``history``; milestone 7).

After a successful build one small JSON record per month is written to a ``history/`` folder
(``history_YYYY-MM.json``). It holds, per employee, the last 7 days of the month (date, shift code,
overtime flag, hours, hour at which the shift ended - for the rest rule), the days-off block
weekdays, and the counters (overtime hours, nights, Fridays; this month and cumulative).

The next month reads it with :func:`load_previous` and uses it as an OPTIONAL extra input:

* rest rule across the month boundary (C11 from the last shift of the previous month to the
  first shift of the new month) - applied in :mod:`roster_builder.solver`;
* days-off spacing across the boundary (at least :data:`MIN_WORK_BETWEEN` working days between the
  last day off of the previous month and the first block of the new month) - applied in
  :mod:`roster_builder.daysoff` (candidate filter) and reported by :func:`spacing_issues`;
* the carry-in hours (``Settings.carry_in``, C16) are unchanged: history only ADDS constraints.

A record is used only when it ends the day before the new month starts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .io_xlsx import RosterInputs
from .model import Issue, Roster, Shift, vba_weekday

VERSION = 1
TAIL_DAYS = 7
#: working days required between two day-off blocks (BUILD_GUIDE section 3)
MIN_WORK_BETWEEN = 5


def _up(s) -> str:
    return (s or "").strip().upper()


def file_name(month_start: date) -> str:
    """History file name of the month containing ``month_start``."""
    return f"history_{month_start.year:04d}-{month_start.month:02d}.json"


def default_history_dir(output_path) -> Path:
    """``<output folder>/history`` for an output file (or folder) path."""
    p = Path(str(output_path))
    folder = p if p.suffix == "" and not p.is_file() else p.parent
    return folder / "history"


@dataclass
class EmpHistory:
    """History of one employee."""

    name: str
    tail: list[dict] = field(default_factory=list)  # last 7 days, oldest first
    block_days: list[str] = field(default_factory=list)  # days-off block weekdays
    ot_hours: float = 0.0
    nights: int = 0
    fridays: int = 0
    cum_ot_hours: float = 0.0
    cum_nights: int = 0
    cum_fridays: int = 0

    def last_day_entry(self) -> dict | None:
        return self.tail[-1] if self.tail else None


@dataclass
class MonthHistory:
    """History record of one finished month."""

    month: str  # "YYYY-MM"
    first_day: date
    last_day: date
    employees: dict[str, EmpHistory] = field(default_factory=dict)  # normalised employee key -> history
    version: int = VERSION

    def is_previous_of(self, first_day: date) -> bool:
        """True when the record ends the day before ``first_day`` (contiguous months)."""
        return self.last_day + timedelta(days=1) == first_day

    def ended_at(self, emp_key: str) -> float | None:
        """Hour (counted from 00:00 of the last day; >= 24 = next day) at which the employee's last
        shift of the previous month ended, or None when they did not work on the last day."""
        e = self.employees.get(emp_key)
        last = e.last_day_entry() if e else None
        if last and last.get("shift") and last.get("ended_at") is not None:
            return float(last["ended_at"])
        return None

    def last_off_date(self, emp_key: str) -> date | None:
        """Most recent planned day off (block day) inside the tail, or None."""
        e = self.employees.get(emp_key)
        if not e:
            return None
        for t in reversed(e.tail):
            if t.get("block"):
                return date.fromisoformat(t["date"])
        return None

    def tail_run(self, emp_key: str) -> int:
        """Number of consecutive planned days off at the very end of the previous month (0 if the
        last day was not a day off)."""
        e = self.employees.get(emp_key)
        n = 0
        for t in reversed(e.tail if e else []):
            if not t.get("block"):
                break
            n += 1
        return n

    # -- JSON
    def to_json(self) -> dict:
        return {
            "version": self.version,
            "month": self.month,
            "first_day": self.first_day.isoformat(),
            "last_day": self.last_day.isoformat(),
            "employees": {
                k: {
                    "name": e.name, "tail": e.tail, "block_days": e.block_days,
                    "ot_hours": e.ot_hours, "nights": e.nights, "fridays": e.fridays,
                    "cum_ot_hours": e.cum_ot_hours, "cum_nights": e.cum_nights, "cum_fridays": e.cum_fridays,
                } for k, e in self.employees.items()
            },
        }

    @classmethod
    def from_json(cls, data: dict) -> "MonthHistory":
        emps = {}
        for k, e in (data.get("employees") or {}).items():
            emps[k] = EmpHistory(
                name=e.get("name", k), tail=list(e.get("tail") or []), block_days=list(e.get("block_days") or []),
                ot_hours=float(e.get("ot_hours", 0)), nights=int(e.get("nights", 0)), fridays=int(e.get("fridays", 0)),
                cum_ot_hours=float(e.get("cum_ot_hours", 0)), cum_nights=int(e.get("cum_nights", 0)),
                cum_fridays=int(e.get("cum_fridays", 0)))
        return cls(month=data["month"], first_day=date.fromisoformat(data["first_day"]),
                   last_day=date.fromisoformat(data["last_day"]), employees=emps, version=int(data.get("version", VERSION)))


# ----------------------------------------------------------------------------- build / save / load
def _shift_end(s: Shift) -> float:
    return max(s.end1, s.end2) if s.start2 >= 0 else s.end1


def _shift_start(s: Shift) -> float:
    return min(s.start1, s.start2) if s.start2 >= 0 else s.start1


def build_record(inputs: RosterInputs, roster: Roster, previous: MonthHistory | None = None) -> MonthHistory:
    """Create the history record of a finished month. ``previous`` (the record of the month before,
    if any) is used to accumulate the cumulative counters."""
    st = inputs.settings
    first = st.first_day
    n = st.days_in_month
    days = [first + timedelta(days=i) for i in range(n)]
    shifts = {_up(s.code): s for s in inputs.shifts}
    fri = _up(st.fri_name) or "FRI"
    by_ed = {(a.emp_key, a.date): a for a in roster.assignments}
    rec = MonthHistory(month=f"{first.year:04d}-{first.month:02d}", first_day=first, last_day=days[-1])
    prev = previous if previous is not None and previous.is_previous_of(first) else None
    for e in inputs.employees:
        if not (e.name.strip() and e.active and e.home):
            continue
        b = roster.blocks.get(e.key)
        bdays = list(b.block_days) if b else []
        eh = EmpHistory(name=e.name, block_days=bdays)
        ot_h = 0.0
        for d in days:
            a = by_ed.get((e.key, d))
            is_block = bool(b and b.is_block_day(d, st.weekday_names))
            if a is None:
                continue
            sh = shifts.get(_up(a.shift_code))
            paid = sh.paid if sh else 0.0
            if a.is_ot:
                ot_h += paid
            ot_h += a.ext_total_h
            if sh is not None and sh.male > 0:
                eh.nights += 1
            if _up(st.weekday_names[vba_weekday(d) - 1]) == fri:
                eh.fridays += 1
        eh.ot_hours = ot_h
        for d in days[-TAIL_DAYS:]:
            a = by_ed.get((e.key, d))
            is_block = bool(b and b.is_block_day(d, st.weekday_names))
            entry: dict = {"date": d.isoformat(), "shift": "", "ot": False, "hours": 0.0,
                           "started_at": None, "ended_at": None, "block": is_block}
            if a is not None:
                sh = shifts.get(_up(a.shift_code))
                paid = sh.paid if sh else 0.0
                entry.update({
                    "shift": a.shift_code, "ot": bool(a.is_ot or a.ext_total_h), "hours": paid + a.ext_total_h,
                    "rec": a.rec,
                    "started_at": (_shift_start(sh) - a.ext_before_h) if sh else None,
                    "ended_at": (_shift_end(sh) + a.ext_after_h) if sh else None})
            eh.tail.append(entry)
        pe = prev.employees.get(e.key) if prev else None
        eh.cum_ot_hours = eh.ot_hours + (pe.cum_ot_hours if pe else 0.0)
        eh.cum_nights = eh.nights + (pe.cum_nights if pe else 0)
        eh.cum_fridays = eh.fridays + (pe.cum_fridays if pe else 0)
        rec.employees[e.key] = eh
    return rec


def save_history(record: MonthHistory, history_dir) -> Path:
    """Write ``history_YYYY-MM.json`` into ``history_dir`` (created if needed); returns the path."""
    d = Path(str(history_dir))
    d.mkdir(parents=True, exist_ok=True)
    p = d / file_name(record.first_day)
    p.write_text(json.dumps(record.to_json(), indent=1, ensure_ascii=False), encoding="utf-8")
    return p


def load_previous(history_dir, month_start: date) -> MonthHistory | None:
    """Record of the month before the one containing ``month_start``, or None (no folder, no file,
    unreadable file)."""
    if history_dir is None or month_start is None:
        return None
    prev_last = month_start.replace(day=1) - timedelta(days=1)
    p = Path(str(history_dir)) / file_name(prev_last)
    try:
        return MonthHistory.from_json(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_after_build(inputs: RosterInputs, roster: Roster, history_dir) -> Path:
    """Build the record of ``roster`` (cumulating on the previous month's record) and save it."""
    prev = load_previous(history_dir, inputs.settings.first_day)
    return save_history(build_record(inputs, roster, prev), history_dir)


# ----------------------------------------------------------------------------- days-off spacing
def spacing_ok(first_day: date, block_weekdays: set[str], last_off: date | None, names: tuple[str, ...],
               min_work: int = MIN_WORK_BETWEEN, run_before: int = 0, max_run: int | None = None) -> bool:
    """Would a weekly block on ``block_weekdays`` (upper-case names) keep at least ``min_work``
    working days after ``last_off`` (the last day off of the previous month)?

    A block that touches ``last_off`` (no working day in between) continues the previous block; it
    is accepted only while the whole run of days off (``run_before`` days at the end of last month
    plus the new ones) stays within ``max_run`` days (when given).
    """
    if last_off is None:
        return True
    prev = last_off
    run = run_before if last_off == first_day - timedelta(days=1) else 0
    for i in range(7):
        d = first_day + timedelta(days=i)
        if names[vba_weekday(d) - 1].strip().upper() not in block_weekdays:
            continue
        gap = (d - prev).days - 1
        if gap == 0:
            run += 1
            if max_run is not None and run > max_run:
                return False
        elif gap < min_work:
            return False
        else:
            run = 1
        prev = d
    return True


def spacing_issues(inputs: RosterInputs, blocks: dict, history: MonthHistory | None) -> list[Issue]:
    """Employees whose first block of the month comes too soon after their last day off of the
    previous month (empty without history). Messages are warnings for the manager."""
    st = inputs.settings
    if history is None or st.first_day is None or not history.is_previous_of(st.first_day):
        return []
    out: list[Issue] = []
    for e in inputs.employees:
        b = blocks.get(e.key)
        lo = history.last_off_date(e.key)
        if b is None or lo is None:
            continue
        if not spacing_ok(st.first_day, {x.strip().upper() for x in b.block_days}, lo, st.weekday_names,
                          run_before=history.tail_run(e.key), max_run=len(b.block_days)):
            out.append(Issue(
                id=f"HISTORY|SPACING|{e.row}", cell=f"G{e.row},Q{e.row}",
                msg=(f"{e.name}: the first days off of the month ({'+'.join(b.block_days)}) come fewer than "
                     f"{MIN_WORK_BETWEEN} working days after the last day off of the previous month ({lo.isoformat()})."),
                fix="Change the fixed day(s) off or the 'No weekends' / Friday settings of this employee."))
    return out
