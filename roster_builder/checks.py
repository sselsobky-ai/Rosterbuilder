"""Rule checks on a finished roster (BUILD_GUIDE section 7/9) - the CHECKS sheet content.

Everything here is computed from the INPUT data and the list of assignments only (never from
solver internals), so it can serve as an independent audit of any roster.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .io_xlsx import RosterInputs
from .locate import Locator, locators_for_cells, localize_issues, sanitize
from .model import Assignment, Roster, Shift, vba_weekday

EPS = 1e-9


def _up(s) -> str:
    return (s or "").strip().upper()


@dataclass
class CheckRow:
    """One line of the CHECKS summary table."""

    key: str
    name: str
    count: float
    status: str = "OK"  # OK / CHECK (rule broken) / WARN (manual exception) / INFO (manual choice, no rule broken)
    note: str = ""
    where: list[Locator] = field(default_factory=list)  # fields to look at (inputs from the store)


@dataclass
class CheckReport:
    """Result of :func:`run_checks`."""

    summary: list[CheckRow] = field(default_factory=list)
    details: dict[str, list[tuple]] = field(default_factory=dict)  # check key -> rows
    detail_headers: dict[str, tuple] = field(default_factory=dict)
    dates: list[date] = field(default_factory=list)
    employees: list[str] = field(default_factory=list)  # employee names, schedule order
    weeks: list[int] = field(default_factory=list)
    # per employee tables
    normal_by_week: dict[str, dict[int, float]] = field(default_factory=dict)
    ot_by_week: dict[str, dict[int, float]] = field(default_factory=dict)
    emp_stats: dict[str, dict[str, float]] = field(default_factory=dict)  # name -> nights/fridays/ot_shifts/...
    #: OT hours per OT-priority group (key = rank 1..10, ``None`` = unranked), only for employees allowed overtime
    ot_by_rank: dict[int | None, float] = field(default_factory=dict)
    blocks: dict[str, dict] = field(default_factory=dict)
    # coverage: rec -> date -> [24 head-counts]
    headcount: dict[str, dict[date, list[int]]] = field(default_factory=dict)
    male_headcount: dict[str, dict[date, list[int]]] = field(default_factory=dict)
    covered_hours: dict[str, dict[date, int]] = field(default_factory=dict)
    # per rec -> hour -> (min, max) over the month from targets (for colouring); None when no target
    limits: dict[str, dict[date, list[tuple[float, float]]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    issues: list[tuple[str, str, str]] = field(default_factory=list)  # (id, cell or where, message)
    issue_header: tuple = ("Id", "Cells", "Message")  # "Where" for inputs from the store

    def count(self, key: str) -> float:
        for r in self.summary:
            if r.key == key:
                return r.count
        raise KeyError(key)

    @property
    def n_check(self) -> int:
        """Number of checks that report a broken rule (manual-exception warnings and notes are not counted)."""
        return sum(1 for r in self.summary if r.status == "CHECK")

    @property
    def n_warn(self) -> int:
        """Number of checks that only warn about a manual exception (WARN) or inform about a manual choice (INFO)."""
        return sum(1 for r in self.summary if r.status in ("WARN", "INFO"))


def shift_parts(s: Shift) -> list[tuple[float, float]]:
    p = [(s.start1, s.end1)]
    if s.start2 >= 0:
        p.append((s.start2, s.end2))
    return p


def slot_hours(parts: list[tuple[float, float]]) -> set[int]:
    """Absolute 1-hour slots whose midpoint lies in one of the (start, end) parts."""
    out: set[int] = set()
    for lo, hi in parts:
        h = int(math.floor(lo))
        while h < hi:
            if lo <= h + 0.5 < hi:
                out.add(h)
            h += 1
    return out


def eff_parts(a: Assignment, s: Shift) -> list[tuple[float, float]]:
    """Worked intervals of an assignment, extension included (absolute hours from the day's 00:00)."""
    parts = shift_parts(s)
    if a.ext_before_h or a.ext_after_h:
        parts = [(min(x for x, _ in parts) - a.ext_before_h, max(y for _, y in parts) + a.ext_after_h)]
    return parts


def _in_window(h_abs: int, f: float, t: float) -> bool:
    x = (h_abs % 24) + 0.5
    if f < t:
        return f <= x < t
    return x >= f or x < t


def _rank_key(rank_of: dict[str, int | None], k: str) -> int:
    r = rank_of.get(k)
    return 99 if r is None else r


def _ot_priority_rows(inputs: RosterInputs, roster: Roster, asg: list[Assignment], per: dict, shifts: dict[str, Shift],
                      emps: dict, active: list, on_leave: set, ehours: dict, normal: dict, otw: dict,
                      rank_of: dict[str, int | None]) -> list[tuple]:
    """Overtime given to a lower priority although a higher-ranked employee could legally have taken it.

    For every non-fixed overtime assignment (an OT shift on a day off, or extension hours) every employee of a
    HIGHER priority group (unranked is the lowest) is tested: not on leave, not working that day, a reception
    he may work, some allowed shift that covers all the overtime hours (no woman in the men-only window), rest to the
    neighbouring days, weekly normal hours (his own day) or weekly OT limit and keep-one-free-day (his day off).
    The test ignores max-staff and demand rows, so it is a simple, mostly sound screen, not a proof.
    """
    st = inputs.settings
    names = st.weekday_names
    male = _up(st.male_code)
    fixed = {(_up(f.emp), f.date) for f in inputs.fixed}
    capable = [e for e in active if e.ot and st.ot_on]
    blocks = roster.blocks or {}
    rows: list[tuple] = []

    def parts_end(a: Assignment) -> float:
        return max(y for _, y in eff_parts(a, shifts[_up(a.shift_code)]))

    def parts_start(a: Assignment) -> float:
        return min(x for x, _ in eff_parts(a, shifts[_up(a.shift_code)]))

    def can_take(h, a: Assignment, need: set[int]) -> bool:
        if h.key == a.emp_key or (h.key, a.date) in per or (h.key, a.date) in on_leave:
            return False
        also = [x.strip().upper() for x in (h.also or "").split(",") if x.strip()]
        if _up(a.rec) != _up(h.home) and _up(a.rec) not in also:
            return False
        b = blocks.get(h.key)
        block_day = bool(b and b.is_block_day(a.date, names))
        wk = st.wk_of(a.date)
        allowed = {_up(x) for x in h.allowed}
        prev = per.get((h.key, a.date - timedelta(days=1)))
        nxt = per.get((h.key, a.date + timedelta(days=1)))
        for sh in inputs.shifts:
            if allowed and _up(sh.code) not in allowed:
                continue
            if _up(h.gender) != male and sh.male > 0:
                continue
            if not need <= slot_hours(shift_parts(sh)):
                continue
            if prev and 24 + min(x for x, _ in shift_parts(sh)) - parts_end(prev[0]) < st.min_rest - EPS:
                continue
            if nxt and 24 + parts_start(nxt[0]) - max(y for _, y in shift_parts(sh)) < st.min_rest - EPS:
                continue
            if block_day:
                if otw.get(h.key, {}).get(wk, 0.0) + sh.paid > st.ot_max + EPS:
                    continue
                if st.ot_keep and b is not None:
                    wdays = [d for d in (a.date + timedelta(days=i) for i in range(-6, 7)) if st.wk_of(d) == wk]
                    bdays = [d for d in wdays if b.is_block_day(d, names)]
                    if len(wdays) == 7 and bdays and sum(1 for d in bdays if (h.key, d) in per) + 1 >= len(bdays):
                        continue
            else:
                lim = h.max_h or st.max_week_h
                if normal.get(h.key, {}).get(wk, 0.0) + sh.paid > lim + EPS:
                    continue
            return True
        return False

    for a in asg:
        if (a.emp_key, a.date) in fixed or not (a.is_ot or a.ext_total_h):
            continue
        mine = _rank_key(rank_of, a.emp_key)
        higher = [h for h in capable if _rank_key(rank_of, h.key) < mine]
        if not higher:
            continue
        sh = shifts[_up(a.shift_code)]
        all_h = ehours[id(a)]
        need = all_h if a.is_ot else all_h - slot_hours(shift_parts(sh))
        if not need:
            continue
        better = [h.name for h in higher if can_take(h, a, need)]
        if better:
            r = rank_of.get(a.emp_key)
            rows.append((emps[a.emp_key].name, f"Rank {r}" if r is not None else "Unranked", a.date, a.shift_code,
                         sh.paid if a.is_ot else a.ext_total_h,
                         ", ".join(better[:4]) + (f" +{len(better) - 4}" if len(better) > 4 else "")))
    return rows


def run_checks(inputs: RosterInputs, roster: Roster) -> CheckReport:
    """Audit ``roster`` against the INPUT rules and build all tables used by the workbook."""
    st = inputs.settings
    rep = CheckReport()
    first = st.first_day or st.month_start
    ndays = st.days_in_month
    dates = [first + timedelta(days=i) for i in range(ndays)]
    rep.dates = dates
    shifts = {_up(s.code): s for s in inputs.shifts}
    emps = {e.key: e for e in inputs.employees}
    male = _up(st.male_code)
    fri = _up(st.fri_name)
    names = st.weekday_names
    active = [e for e in inputs.employees if e.name.strip() and e.active and e.home]
    rep.employees = [e.name for e in active]
    ismale = lambda k: _up(emps[k].gender) == male  # noqa: E731

    asg = [a for a in roster.assignments if _up(a.shift_code) in shifts and a.emp_key in emps]
    per: dict[tuple[str, date], list[Assignment]] = defaultdict(list)
    for a in asg:
        per[(a.emp_key, a.date)].append(a)
    ehours = {id(a): slot_hours(eff_parts(a, shifts[_up(a.shift_code)])) for a in asg}

    det: dict[str, list[tuple]] = {}
    hdr: dict[str, tuple] = {}

    def add(key: str, name: str, header: tuple, rows: list[tuple], note: str = "", count: float | None = None,
            level: str = "CHECK") -> None:
        det[key] = rows
        hdr[key] = header
        c = len(rows) if count is None else count
        rep.summary.append(CheckRow(key, name, c, "OK" if c == 0 else level, note))

    # ---------------------------------------------------------------- head-counts
    recs = [r.name for r in inputs.receptions] or sorted({_up(a.rec) for a in asg})
    recs_u = [_up(r) for r in recs]
    hc: dict[str, dict[date, list[int]]] = {r: {d: [0] * 24 for d in dates} for r in recs_u}
    mhc: dict[str, dict[date, list[int]]] = {r: {d: [0] * 24 for d in dates} for r in recs_u}
    idx = {d: i for i, d in enumerate(dates)}
    for a in asg:
        r = _up(a.rec)
        if r not in hc:
            hc[r] = {d: [0] * 24 for d in dates}
            mhc[r] = {d: [0] * 24 for d in dates}
        for h in ehours[id(a)]:
            dd = a.date + timedelta(days=h // 24)
            if dd in idx:
                hc[r][dd][h % 24] += 1
                if ismale(a.emp_key):
                    mhc[r][dd][h % 24] += 1
    rep.headcount, rep.male_headcount = hc, mhc
    # the first C16 hours of day 1 are covered by last month, so they count as covered
    rep.covered_hours = {r: {d: sum(1 for h, x in enumerate(v) if x > 0 or (d == dates[0] and h < st.carry_in))
                             for d, v in dd.items()} for r, dd in hc.items()}

    # ---------------------------------------------------------------- coverage vs targets
    empty, nomale, below, above = [], [], [], []
    short_per_target: dict[int, float] = defaultdict(float)
    lim: dict[str, dict[date, list[tuple[float, float]]]] = {r: {d: [(0.0, -1.0)] * 24 for d in dates} for r in hc}
    for t in inputs.targets:
        r = _up(t.rec)
        if r not in hc:
            continue
        for d in dates:
            if not t.applies(d, fri, names):
                continue
            for h in range(int(t.frm), int(math.ceil(t.to))):
                if not 0 <= h < 24:
                    continue
                n = hc[r][d][h]
                nm = mhc[r][d][h]
                lo, hi = lim[r][d][h]
                lim[r][d][h] = (max(lo, t.min_staff), t.max_staff if hi < 0 else (min(hi, t.max_staff) if t.max_staff >= 0 else hi))
                carried = d == dates[0] and h < st.carry_in
                if carried:
                    continue
                if t.min_staff >= 1 and n < 1:
                    empty.append((t.rec, d, f"{h:02d}:00", t.row))
                if t.min_male >= 1 and nm < 1:
                    nomale.append((t.rec, d, f"{h:02d}:00", t.row))
                if n < t.min_staff:
                    below.append((t.rec, d, f"{h:02d}:00", t.row, t.min_staff, n, t.min_staff - n))
                    short_per_target[t.row] += t.min_staff - n
                if t.max_staff >= 0 and n > t.max_staff:
                    above.append((t.rec, d, f"{h:02d}:00", t.row, t.max_staff, n))
    rep.limits = lim
    add("empty", "Hours with nobody in a Min>=1 target", ("Reception", "Date", "Hour", "Target row"), empty)
    add("no_male", "Min-male hours without a man", ("Reception", "Date", "Hour", "Target row"), nomale)
    total_short = sum(short_per_target.values())
    below_rows = [r for r in below]
    add("below_min", "Hours below full Min staff", ("Reception", "Date", "Hour", "Target row", "Min", "Present", "Short"),
        below_rows, note=f"{total_short:g} person-hours short" + (
            "; per target row: " + ", ".join(f"row {k}: {v:g}" for k, v in sorted(short_per_target.items())) if short_per_target else ""))
    add("above_max", "Hours above Max staff", ("Reception", "Date", "Hour", "Target row", "Max", "Present"), above)

    # ---------------------------------------------------------------- women in the men-only window
    women = []
    for a in asg:
        if ismale(a.emp_key):
            continue
        hs = sorted(h for h in ehours[id(a)] if _in_window(h, st.male_from, st.male_to))
        if hs:
            women.append((emps[a.emp_key].name, a.date, a.shift_code, f"{len(hs)} h"))
    add("women_window", "Women in the men-only window", ("Employee", "Date", "Shift", "Hours in window"), women)

    # ---------------------------------------------------------------- rest
    rest_bad, split_bad = [], []
    for (k, d), lst in sorted(per.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        a = lst[0]
        s = shifts[_up(a.shift_code)]
        ps = eff_parts(a, s)
        nxt = per.get((k, d + timedelta(days=1)))
        if nxt:
            end = max(y for _, y in ps)
            start2 = min(x for x, _ in eff_parts(nxt[0], shifts[_up(nxt[0].shift_code)]))
            gap = 24 + start2 - end
            if gap < st.min_rest - EPS:
                rest_bad.append((emps[k].name, d, a.shift_code, nxt[0].shift_code, round(gap, 2), st.min_rest))
        if len(ps) > 1 and st.min_rest_split > 0:
            ps2 = sorted(ps)
            for (a1, b1), (a2, b2) in zip(ps2, ps2[1:]):
                if a2 - b1 < st.min_rest_split - EPS:
                    split_bad.append((emps[k].name, d, a.shift_code, round(a2 - b1, 2), st.min_rest_split))
    add("rest", "Rest breaches between days (C11)", ("Employee", "Date", "Shift", "Next shift", "Rest h", "Required"), rest_bad)
    add("rest_split", "Rest breaches inside split shifts (C12)", ("Employee", "Date", "Shift", "Rest h", "Required"), split_bad)

    # ---------------------------------------------------------------- weekly hours / overtime
    normal: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    otw: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    ot_shifts: dict[str, int] = defaultdict(int)
    ot_ext: dict[str, float] = defaultdict(float)
    ot_block: dict[str, float] = defaultdict(float)
    for a in asg:
        wk = st.wk_of(a.date)
        paid = shifts[_up(a.shift_code)].paid
        if a.is_ot:
            otw[a.emp_key][wk] += paid
            ot_block[a.emp_key] += paid
            ot_shifts[a.emp_key] += 1
        else:
            normal[a.emp_key][wk] += paid
        if a.ext_total_h:
            otw[a.emp_key][wk] += a.ext_total_h
            ot_ext[a.emp_key] += a.ext_total_h
            ot_shifts[a.emp_key] += 0 if a.is_ot else 1
    over_norm = []
    for k, wkd in normal.items():
        lim_h = emps[k].max_h or st.max_week_h
        for wk, h in sorted(wkd.items()):
            if h > lim_h + EPS:
                over_norm.append((emps[k].name, wk, h, lim_h))
    add("weekly_normal", "Weekly normal hours above limit", ("Employee", "Week", "Normal h", "Limit"), over_norm)
    ot_rows, ot_over = [], []
    rank_of: dict[str, int | None] = {e.key: (e.ot_rank if (e.ot and st.ot_on and e.ot_rank) else None) for e in active}
    any_rank = any(v is not None for v in rank_of.values())
    for e in active:
        k = e.key
        tot = sum(otw[k].values()) if k in otw else 0.0
        if tot or ot_shifts.get(k):
            ot_rows.append((e.name, ot_shifts.get(k, 0), ot_ext.get(k, 0.0), tot,
                            rank_of[k] if rank_of[k] is not None else "-"))
        for wk, h in sorted(otw.get(k, {}).items()):
            if h > st.ot_max + EPS:
                ot_over.append((e.name, wk, h, st.ot_max))
    tot_ot = sum(r[3] for r in ot_rows)
    add("overtime", "Overtime per employee", ("Employee", "OT shifts (incl. extensions)", "Extension h", "OT hours", "OT priority"),
        ot_rows, note=f"{tot_ot:g} OT hours in total; informational", count=0)
    # OT hours per priority group + "OT priority respected" (milestone 11A)
    grp: dict[int | None, list] = {}
    for e in active:
        if not (e.ot and st.ot_on):
            continue
        g = grp.setdefault(rank_of[e.key], [[], 0, 0.0])
        g[0].append(e.name)
        g[1] += ot_shifts.get(e.key, 0)
        g[2] += sum(otw[e.key].values()) if e.key in otw else 0.0
    rep.ot_by_rank = {k: v[2] for k, v in grp.items()}
    if any_rank:
        order = sorted(grp, key=lambda r: (r is None, r or 0))
        add("ot_by_rank", "Overtime per priority group", ("OT priority", "Employees", "OT shifts (incl. extensions)", "OT hours"),
            [(f"Rank {r}" if r is not None else "Unranked", ", ".join(grp[r][0]), grp[r][1], grp[r][2]) for r in order],
            note="; ".join(f"{('rank ' + str(r)) if r is not None else 'unranked'}: {grp[r][2]:g} h" for r in order), count=0)
    add("ot_max", "Overtime above the weekly OT max", ("Employee", "Week", "OT h", "OT max"), ot_over)
    ot_perm = [(emps[a.emp_key].name, a.date, a.shift_code) for a in asg
               if (a.is_ot or a.ext_total_h) and not (st.ot_on and emps[a.emp_key].ot)
               and not any(_up(f.emp) == a.emp_key and f.date == a.date for f in inputs.fixed)]
    add("ot_permission", "Overtime without permission (G14 / column O)", ("Employee", "Date", "Shift"), ot_perm)

    # ---------------------------------------------------------------- leave / fixed
    leave_bad = []
    on_leave: set[tuple[str, date]] = set()
    for lv in inputs.leave:
        d = lv.frm
        while d <= lv.to:
            on_leave.add((_up(lv.emp), d))
            d += timedelta(days=1)
    for a in asg:
        if (a.emp_key, a.date) in on_leave:
            leave_bad.append((emps[a.emp_key].name, a.date, a.shift_code))
    add("leave", "Leave conflicts", ("Employee", "Date", "Shift"), leave_bad)
    fixed_missing = []
    for f in inputs.fixed:
        e = emps.get(_up(f.emp))
        want = _up(f.rec) or (_up(e.home) if e else "")
        if not any(_up(a.shift_code) == _up(f.shift) and _up(a.rec) == want for a in per.get((_up(f.emp), f.date), [])):
            fixed_missing.append((f.emp, f.date, f.shift, f.rec or "(home)"))
    add("fixed", "Fixed shifts missing", ("Employee", "Date", "Shift", "Reception"), fixed_missing)
    multi = [(emps[k].name, d, len(lst)) for (k, d), lst in per.items() if len(lst) > 1]
    add("double", "More than one shift on a day", ("Employee", "Date", "Shifts"), multi)

    # ---------------------------------------------------------------- OT priority respected (milestone 11A)
    pri_rows = _ot_priority_rows(inputs, roster, asg, per, shifts, emps, active, on_leave, ehours, normal, otw, rank_of)
    add("ot_priority", "OT priority respected",
        ("Employee", "OT priority", "Date", "Shift", "OT hours", "Higher-priority staff who could take it"), pri_rows,
        note=("yes" if not pri_rows else f"no - {len(pri_rows)} overtime assignment(s) went to a lower priority although a "
              "higher-ranked employee could legally take them") if any_rank else "yes (no priorities set)")

    # ---------------------------------------------------------------- days off
    blk_rows: list[tuple] = []
    blk_bad: list[tuple] = []
    blocks = roster.blocks or {}
    for e in active:
        b = blocks.get(e.key)
        if not b or not b.block_days:
            continue
        days = [_up(x) for x in b.block_days]
        pos = sorted(i for i, n in enumerate(names) if _up(n) in days)
        # cyclic contiguity: exactly one gap in the circular sequence
        runs = sum(1 for i in pos if ((i - 1) % 7) not in pos)
        length = len(pos)
        gap = 7 - length
        problem = []
        if runs != 1:
            problem.append("more than one block per week")
        if length == 2 and gap < 5:
            problem.append("fewer than 5 working days between blocks")
        # weeks where every block day was worked (no real day off)
        worked_all = []
        by_wk: dict[int, list[date]] = defaultdict(list)
        for d in dates:
            if _up(names[vba_weekday(d) - 1]) in days:
                by_wk[st.wk_of(d)].append(d)
        for wk, ds in sorted(by_wk.items()):
            if len(ds) == length and all((e.key, d) in per for d in ds):
                worked_all.append(wk)
        worked_days = sum(1 for ds in by_wk.values() for d in ds if (e.key, d) in per)
        blk_rows.append((e.name, "-".join(days), length, gap, worked_days, ", ".join(str(w) for w in worked_all) or "-"))
        rep.blocks[e.name] = {"days": days, "length": length, "gap": gap, "weeks_all_worked": worked_all}
        if problem:
            blk_bad.append((e.name, "; ".join(problem)))
    rep.details["days_off_table"] = blk_rows
    hdr["days_off_table"] = ("Employee", "Block days", "Length", "Working days between", "Block days worked", "Weeks with every block day worked")
    add("days_off", "Days-off block rule (one block, spacing)", ("Employee", "Problem"), blk_bad)

    # manual choices of the manager (pins on a built roster); a solver roster has none of these
    off_pinned = {(k, d) for (k, d) in (getattr(roster, "off_locked", ()) or ())}
    idle, extra_off = [], []
    for e in active:
        b = blocks.get(e.key)
        for d in dates:
            if (e.key, d) in per or (e.key, d) in on_leave:
                continue
            if b and b.is_block_day(d, names):
                continue
            if (e.key, d) in off_pinned:
                extra_off.append((e.name, d, names[vba_weekday(d) - 1]))
                continue
            idle.append((e.name, d, names[vba_weekday(d) - 1]))
    add("idle", "Idle days (no shift, no leave, not in block)", ("Employee", "Date", "Weekday"), idle)
    add("extra_off", "Extra day off (manual)", ("Employee", "Date", "Weekday"), extra_off,
        note="a day off the manager gave by hand; not a rule break", level="INFO")
    manual_ot = []
    for a in asg:
        b = blocks.get(a.emp_key)
        if a.is_ot and b and b.block_days and not b.is_block_day(a.date, names):
            manual_ot.append((emps[a.emp_key].name, a.date, a.shift_code))
    add("manual_ot", "Overtime on a normal working day (manual)", ("Employee", "Date", "Shift"), manual_ot,
        note="set by hand with 'work as overtime'; the planner only uses overtime on days off or as extensions", level="WARN")

    # ---------------------------------------------------------------- demand
    unf = []
    for dm in inputs.demand:
        for d in dates:
            i = vba_weekday(d) - 1
            n = dm.counts[i] if i < len(dm.counts) else 0
            if n <= 0:
                continue
            got = 0
            for a in asg:
                if a.date != d or _up(a.shift_code) != _up(dm.shift) or _up(a.rec) != _up(dm.rec):
                    continue
                if _up(dm.gender) not in ("", "ANY") and _up(emps[a.emp_key].gender) != _up(dm.gender):
                    continue
                home = _up(a.rec) == _up(emps[a.emp_key].home)
                fl = _up(dm.fill)
                if (fl == "HOME" and not home) or (fl == "HELP" and home):
                    continue
                got += 1
            if got < n:
                unf.append((dm.rec, d, dm.shift, dm.gender, n, got, f"B{dm.row}:O{dm.row}"))
    add("demand", "Unfilled demand", ("Reception", "Date", "Shift", "Gender", "Needed", "Placed", "Cells"), unf)

    # ---------------------------------------------------------------- per-employee tables
    rep.weeks = sorted({st.wk_of(d) for d in dates})
    win_hours: dict[str, int] = defaultdict(int)
    for e in active:
        k = e.key
        nights = fridays = 0
        for d in dates:
            lst = per.get((k, d))
            if not lst:
                continue
            a = lst[0]
            if any(_in_window(h, st.male_from, st.male_to) for h in ehours[id(a)]):
                nights += 1
            if _up(names[vba_weekday(d) - 1]) == fri:
                fridays += 1
            win_hours[k] += sum(1 for h in ehours[id(a)] if _in_window(h, st.male_from, st.male_to))
        rep.normal_by_week[e.name] = dict(normal.get(k, {}))
        rep.ot_by_week[e.name] = dict(otw.get(k, {}))
        rep.emp_stats[e.name] = {
            "normal": sum(normal.get(k, {}).values()), "ot": sum(otw.get(k, {}).values()),
            "ot_shifts": ot_shifts.get(k, 0), "nights": nights, "fridays": fridays,
            "days_worked": sum(1 for d in dates if (k, d) in per), "window_hours": win_hours[k],
            "ot_rank": rank_of.get(k) or 0,
        }

    # ---------------------------------------------------------------- structural load note
    need_h = sum(1 for t in inputs.targets if t.min_male >= 1 for d in dates if t.applies(d, fri, names)
                 for h in range(int(t.frm), int(math.ceil(t.to))) if 0 <= h < 24 and _in_window(h, st.male_from, st.male_to))
    men = [e for e in active if _up(e.gender) == male]
    tot_win = sum(win_hours[e.key] for e in men)
    if men and tot_win > 0:
        top = max(men, key=lambda e: win_hours[e.key])
        share = win_hours[top.key] / tot_win
        if share >= 0.5:
            rep.notes.append(
                f"Structural load: {top.name} works {win_hours[top.key]} of the {tot_win} man-hours in the men-only window "
                f"({share:.0%}, {rep.emp_stats[top.name]['nights']} night shifts); only {len(men)} men are active, so the "
                f"men-only requirement ({need_h} target hours) rests mostly on one person.")
    rep.details, rep.detail_headers = {**det, **{k: v for k, v in rep.details.items()}}, hdr

    rep.issues = [(i.id, i.cell, i.msg) for i in roster.issues]
    if getattr(inputs, "source", "") == "store":
        _localize_report(inputs, roster, rep)
    if roster.objective:
        rep.notes.append("Solver objective: " + ", ".join(f"{k}={v:g}" for k, v in roster.objective.items() if not k.endswith("_cost")))
    return rep


# ------------------------------------------------------------------------------ inputs from the store
#: check key -> Excel-layout cells (translated to field locators for store inputs)
_CHECK_CELLS = {
    "rest": "C11", "rest_split": "C12", "weekly_normal": "C9", "ot_max": "G15", "ot_permission": "G14",
    "women_window": "C13:C14", "days_off": "C15",
}


def _localize_report(inputs: RosterInputs, roster: Roster, rep: CheckReport) -> None:
    """Field locators on the summary rows and texts without Excel cell references (store inputs)."""
    idx = inputs.row_index
    localize_issues(roster.issues, inputs)
    rep.issues = [(i.id, "; ".join(w.label for w in i.where), i.msg) for i in roster.issues]
    rep.issue_header = ("Id", "Where", "Message")
    target_keys = ("empty", "no_male", "below_min", "above_max")
    cells_of: dict[str, str] = dict(_CHECK_CELLS)
    for key in target_keys:  # "Target row" column -> the coverage row itself
        hdr = list(rep.detail_headers.get(key, ()))
        if "Target row" in hdr:
            hdr[hdr.index("Target row")] = "Coverage target"
            rep.detail_headers[key] = tuple(hdr)
        cells_of[key] = ",".join(f"D{r}:J{r}" for r in sorted({t[3] for t in rep.details.get(key, [])}))
        rep.details[key] = [t[:3] + ((idx.get("target", t[3]) or (0, ""))[1],) + t[4:] for t in rep.details.get(key, [])]
    hdr = list(rep.detail_headers.get("demand", ()))
    if hdr and hdr[-1] == "Cells":
        hdr[-1] = "Demand row"
        rep.detail_headers["demand"] = tuple(hdr)
        rows, drs = [], set()
        for t in rep.details.get("demand", []):
            m = re.match(r"B(\d+):", str(t[-1]))
            r = int(m.group(1)) if m else 0
            drs.add(r)
            rows.append(t[:-1] + ((idx.get("demand", r) or (0, ""))[1],))
        rep.details["demand"] = rows
        cells_of["demand"] = ",".join(f"B{r}:O{r}" for r in sorted(drs))
    for row in rep.summary:
        row.name = sanitize(row.name, idx)
        row.note = sanitize(row.note, idx)
        if row.status != "OK":
            row.where = locators_for_cells(cells_of.get(row.key, ""), idx)
    rep.notes = [sanitize(n, idx) for n in rep.notes]
