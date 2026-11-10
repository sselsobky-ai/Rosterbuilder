"""OR-Tools CP-SAT roster model - BUILD_GUIDE.md sections 3 and 6 (milestone 4: HARD rules).

One CP-SAT model per month.  Decision variable ``x[e, d, s, r]`` = employee ``e`` works shift
``s`` at reception ``r`` on day ``d`` (``d = 0 .. days_in_month-1``).  A day holds at most one
shift per employee.  Times are float hours; a night shift spills into the next day and counts
there (hour slots >= 24).

HARD rules enforced (nothing here is ever "traded" for the objective):

1. leave days are never worked; 2. fixed shifts are placed exactly (reception or home);
3. allowed shifts (fixed shifts are exempt); 4. men-only window: a woman may not work a shift
touching it; 5. rest between consecutive days >= C11 (split-shift inner rest C12 is validated
on the shift definitions); 6. shifts with paid hours > C10 are unusable (reported); 7. every hour of
a coverage target with Min >= 1 has >= 1 person on duty, and >= 1 man where Min-male >= 1
(the first C16 hours of day 1 are covered by last month); 8. max staff; 9. weekly *normal*
paid hours <= employee limit (weeks start on C7); 10. a planned day-off (block) day may only be
worked as overtime, only if the OT master switch G14 and the employee's OT flag are ON, weekly OT
hours <= G15, and with G16 = Y one block day per (complete) week stays free; 11. every active
demand row/day with count >= 1 gets at least one person (the full count is a soft shortfall).

SOFT goals (milestone 5, weights in :data:`WEIGHTS`, highest importance first): target hours below
Min / unfilled demand; overtime, cheapest first - helper before overtime, shift EXTENSION (an
existing non-split shift stretched by whole hours before and/or after, at most C18 h, only the extra
hours are overtime) before a whole overtime shift on a block day, and (G16 = N) keep a block day
free; fill-to-contract (no idle non-block, non-leave day); consistency (same shift on consecutive
workdays of a week, preferred shifts); fairness (minimise the maximum overtime hours, Friday shifts
and night shifts per employee).  When the model is INFEASIBLE it is solved again with slack variables on every
hard-rule *group*; the used slacks become :class:`~roster_builder.model.Issue` objects naming the
INPUT cells involved.  A partial roster is never returned silently.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta

from ortools.sat.python import cp_model

from .daysoff import DayOff, plan_days_off
from .history import spacing_issues
from .io_xlsx import RosterInputs
from .locate import SEP, Locator, localize_issues
from .model import (
    Assignment,
    CoverageTarget,
    Employee,
    Issue,
    Roster,
    Shift,
    covered_hours,
    male_overlap,
    vba_weekday,
)

#: Objective weights (one place, BUILD_GUIDE section 6).
WEIGHTS: dict[str, int] = {
    # 1. target hours / demand
    "coverage_shortfall": 1000,  # per person-hour below Min (beyond the hard 1)
    "demand_shortfall": 100,  # per missing person of a demand row
    # 2. overtime, cheapest first: helper (4) < extension hour (12/h, <= C18 h) < OT shift (120) < free day lost
    "helper": 4,  # per shift worked as helper at another reception
    "ext_hour": 12,  # per overtime hour of a shift extension
    "overtime_shift": 120,  # per whole shift worked on a planned day off
    "ot_keep_free": 60,  # per week in which an employee works ALL block days (G16 = N)
    # 3. fill to contract
    "idle_day": 300,  # per idle day outside leave / day-off block (strong, below coverage)
    # 4. consistency
    "shift_change": 8,  # per change of shift/reception between consecutive workdays of a week
    "pref_miss": 3,  # per worked shift that is not one of the employee's preferred shifts
    # 5. fairness (per hour / per shift of the maximum over employees)
    "fair_ot_max": 25,
    "fair_fri_max": 20,
    "fair_night_max": 20,
    # 6. OT priority ranks (milestone 11A): spread the OT of one rank group evenly (only when ranks are set)
    "fair_ot_rank": 25,
}

#: soft term -> (raw-count key, cost key) in ``Roster.objective``.
TERM_KEYS: dict[str, tuple[str, str]] = {
    "coverage_shortfall": ("coverage_shortfall_hours", "coverage_shortfall_cost"),
    "demand_shortfall": ("demand_shortfall", "demand_shortfall_cost"),
    "helper": ("helper_shifts", "helper_cost"),
    "ext_hour": ("ext_hours", "ext_cost"),
    "overtime_shift": ("overtime_shifts", "overtime_cost"),
    "ot_keep_free": ("weeks_all_block_days_worked", "ot_keep_free_cost"),
    "idle_day": ("idle_days", "idle_cost"),
    "shift_change": ("shift_changes", "shift_change_cost"),
    "pref_miss": ("pref_misses", "pref_miss_cost"),
    "fair_ot_max": ("max_ot_hours", "fair_ot_cost"),
    "fair_fri_max": ("max_friday_shifts", "fair_fri_cost"),
    "fair_night_max": ("max_night_shifts", "fair_night_cost"),
    "fair_ot_rank": ("max_ot_hours_in_rank", "fair_ot_rank_cost"),
}

#: group key of the employees without an OT priority rank (they come after rank 10)
UNRANKED = 99
#: share of the time limit used by the base solve / the rank stages when OT ranks are in use
_BASE_SHARE, _STAGES_SHARE = 0.4, 0.3

#: Weight of one unit of slack in the infeasibility-relaxation model.
RELAX_WEIGHT: dict[str, int] = {
    "coverage": 100,
    "men_window": 100,
    "allowed": 60,
    "ot_flag": 60,
    "rest": 60,
    "weekly_hours": 1,  # per minute of weekly hours (see _MIN)
    "ot_max": 1,  # per minute
    "ot_keep": 60,
    "max_staff": 100,
    "demand": 100,
}

_MIN = 60  # hours are modelled in minutes so float paid hours stay exact


@dataclass
class _Cand:
    """One candidate variable x[e, d, s, r]."""

    e: Employee
    d: int
    shift: Shift
    rec: str
    var: cp_model.IntVar
    is_block: bool
    fixed: bool = False
    #: cumulative extension indicators: ``ext_b[k-1]`` = shift starts >= k h earlier, ``ext_a[k-1]`` = ends >= k h later
    ext_b: list[cp_model.IntVar] = field(default_factory=list)
    ext_a: list[cp_model.IntVar] = field(default_factory=list)


@dataclass
class _Ctx:
    """Everything built for one CP-SAT model (normal or relaxed)."""

    model: cp_model.CpModel
    cands: list[_Cand] = field(default_factory=list)
    shortfall: list[tuple[str, cp_model.IntVar]] = field(default_factory=list)
    demand_short: list[cp_model.IntVar] = field(default_factory=list)
    #: soft term name (key of WEIGHTS) -> list of expressions whose sum is the raw count of the term
    soft: dict[str, list] = field(default_factory=dict)
    # relaxation bookkeeping: group -> list of (info, var, weight)
    slack: dict[str, list[tuple[object, cp_model.IntVar, int]]] = field(default_factory=dict)
    objective_terms: list = field(default_factory=list)
    #: employee index -> minutes of OT that the model can still choose (OT shifts on block days + extension hours)
    ot_var: dict[int, list] = field(default_factory=dict)
    #: OT-priority groups, lowest priority first (UNRANKED, 10, 9 ... 2): list of (group key, [employee index]);
    #: rank 1 (the highest group present) is not listed - it takes whatever the others do not.
    rank_stages: list[tuple[int, list[int]]] = field(default_factory=list)
    #: diagnosis mode only: (locked cell, literal that switches the lock on) - used as solver assumptions
    lock_lits: list = field(default_factory=list)

    def add_soft(self, name: str, expr) -> None:
        self.soft.setdefault(name, []).append(expr)

    def add_slack(self, group: str, info: object, var: cp_model.IntVar, weight: int) -> None:
        self.slack.setdefault(group, []).append((info, var, weight))
        self.objective_terms.append(weight * var)


# --------------------------------------------------------------------------- helpers
def _up(s: str) -> str:
    return (s or "").strip().upper()


def _dates(inputs: RosterInputs) -> list[date]:
    st = inputs.settings.first_day
    if st is None:
        return []
    return [st + timedelta(days=i) for i in range(inputs.settings.days_in_month)]


def _shift_end(s: Shift) -> float:
    return max(s.end1, s.end2) if s.start2 >= 0 else s.end1


def _shift_start(s: Shift) -> float:
    return min(s.start1, s.start2) if s.start2 >= 0 else s.start1


def _also(e: Employee) -> list[str]:
    return [p.strip().upper() for p in (e.also or "").split(",") if p.strip()]


def _day_txt(d: date) -> str:
    return d.strftime("%a %d-%b")


def _hour_txt(h: int) -> str:
    return f"{h % 24:02d}:00"


def _target_hours(t: CoverageTarget) -> list[int]:
    """Whole-hour slots ``h`` (``[h, h+1)``) inside ``[frm, to)`` (to <= 24)."""
    out: list[int] = []
    h = int(math.floor(t.frm))
    while h < t.to:
        if h + 1 > t.frm:
            out.append(h)
        h += 1
    return [h for h in out if 0 <= h < 24]


def _need_of(t: CoverageTarget) -> float:
    return t.min_staff if t.min_staff is not None else -1.0


def _unusable_shifts(inputs: RosterInputs) -> tuple[set[str], list[Issue]]:
    """Shifts that cannot be used (paid > C10, inner rest < C12) and Issues describing them."""
    bad: set[str] = set()
    issues: list[Issue] = []
    st = inputs.settings
    for s in inputs.shifts:
        if st.max_shift_h > 0 and s.paid > st.max_shift_h + 1e-9:
            bad.add(_up(s.code))
            issues.append(Issue(
                id=f"SOLVER|SHIFT_MAXH|{s.row}", cell=f"G{s.row}",
                msg=f"Shift {s.code} pays {s.paid:g} h, more than the maximum {st.max_shift_h:g} h per shift (C10); it will not be used.",
                fix="Lower the paid hours of the shift or raise C10."))
        if s.start2 >= 0 and st.min_rest_split > 0 and (s.start2 - s.end1) < st.min_rest_split - 1e-9:
            bad.add(_up(s.code))
            issues.append(Issue(
                id=f"SOLVER|SHIFT_SPLITREST|{s.row}", cell=f"D{s.row}:E{s.row}",
                msg=f"Shift {s.code}: break between the two parts is {s.start2 - s.end1:g} h, less than the minimum {st.min_rest_split:g} h (C12); it will not be used.",
                fix="Lengthen the break or lower C12."))
    return bad, issues


# ------------------------------------------------------------------- model building
def _ext_slots(shift: Shift, side: str, k: int) -> set[int]:
    """Absolute hour slots added by extending ``shift`` by ``k`` whole hours on ``side`` ('b' / 'a')."""
    if side == "a":
        big = Shift(shift.code, shift.start1, shift.end1 + k, -1.0, -1.0, shift.paid)
    else:
        big = Shift(shift.code, shift.start1 - k, shift.end1, -1.0, -1.0, shift.paid)
    return covered_hours(big) - covered_hours(shift)


def _add_extension(m: cp_model.CpModel, c: _Cand, st, woman: bool, name: str) -> None:
    """Create the cumulative extension indicators of candidate ``c`` (non-split shifts only).

    ``z[k]`` = 1 means "extended by at least k hours on that side"; ``z[1] <= x`` and
    ``z[k] <= z[k-1]``.  Extension hours are overtime and may not reach into the men-only window
    for a woman; the extended shift must stay within hours 0..48 of its day.
    """
    kmax = int(math.floor(st.ext_max + 1e-9))
    if kmax < 1 or c.shift.start2 >= 0:
        return
    base_male = male_overlap(c.shift, st.male_from, st.male_to)
    for side, lst in (("b", c.ext_b), ("a", c.ext_a)):
        for k in range(1, kmax + 1):
            lo = c.shift.start1 - k if side == "b" else c.shift.start1
            hi = c.shift.end1 + k if side == "a" else c.shift.end1
            if lo < 0 or hi > 48:
                break
            big = (Shift(c.shift.code, lo, c.shift.end1, -1.0, -1.0, c.shift.paid) if side == "b"
                   else Shift(c.shift.code, c.shift.start1, hi, -1.0, -1.0, c.shift.paid))
            if woman and male_overlap(big, st.male_from, st.male_to) > base_male + 1e-9:
                break
            z = m.NewBoolVar(f"ext{side}{k}_{name}")
            m.Add(z <= (lst[-1] if lst else c.var))
            lst.append(z)
    if len(c.ext_b) + len(c.ext_a) > kmax:  # C18 limits the extension as a whole, not per side
        m.Add(sum(c.ext_b) + sum(c.ext_a) <= kmax)


def _lock_issue(lk, e: Employee, text: str, id_: str = "LOCK") -> Issue:
    """Conflict that points at the roster cell of a pinned change (screen ``roster``, record = employee, field = ISO date)."""
    label = f"Roster{SEP}{e.name}{SEP}{lk.date.strftime('%a %d %b')}"
    return Issue(id=f"SOLVER|{id_}|{e.uid}|{lk.date.isoformat()}", cell="", msg=text,
                 where=[Locator("roster", e.uid, lk.date.isoformat(), label)])


def _build(inputs: RosterInputs, blocks: dict[str, DayOff], relax: bool, bad_shifts: set[str],
           history=None, diag_locks: bool = False) -> tuple[_Ctx, list[Issue]]:
    """Build the CP-SAT model.  ``relax`` adds slack variables for every hard-rule group.

    Returns ``(ctx, early_issues)``; ``early_issues`` lists fixed shifts that contradict
    leave / men-only / overtime rules (the model is then infeasible by construction).
    """
    st = inputs.settings
    days = _dates(inputs)
    nd = len(days)
    m = cp_model.CpModel()
    ctx = _Ctx(model=m)
    early: list[Issue] = []
    male_code = _up(st.male_code) or "M"
    ot_master = bool(st.ot_on)
    emps = [e for e in inputs.employees if e.name.strip() and e.active and e.home]
    emp_ids = {e.key: i for i, e in enumerate(emps)}
    shifts = [s for s in inputs.shifts if _up(s.code) not in bad_shifts]
    cov_of = {_up(s.code): covered_hours(s) for s in shifts}
    date_idx = {d: i for i, d in enumerate(days)}

    def on_leave(e: Employee, d: date) -> bool:
        return any(_up(lv.emp) == e.key and lv.frm <= d <= lv.to for lv in inputs.leave)

    def is_block(e: Employee, d: date) -> bool:
        b = blocks.get(e.key)
        return bool(b and b.is_block_day(d, st.weekday_names))

    # ---- fixed shifts -> (emp idx, day) -> (shift, rec)
    fixed_at: dict[tuple[int, int], tuple[Shift, str, int]] = {}
    for fx in inputs.fixed:
        e = inputs.emp_by_name(fx.emp)
        di = date_idx.get(fx.date)
        if di is None:
            continue
        if e is None or e.key not in emp_ids:
            early.append(Issue(id=f"SOLVER|FIXED_EMP|{fx.row}", cell=f"B{fx.row}",
                               msg=f"Fixed shift row {fx.row}: employee {fx.emp} is unknown, inactive or has no home reception.",
                               fix="Fix the employee name or set the employee active."))
            continue
        sh = inputs.shift_by_key.get(_up(fx.shift))
        if sh is None or _up(sh.code) in bad_shifts:
            early.append(Issue(id=f"SOLVER|FIXED_SHIFT|{fx.row}", cell=f"D{fx.row}",
                               msg=f"Fixed shift row {fx.row}: shift {fx.shift} is unknown or unusable (hours above C10 / split break below C12).",
                               fix="Use a valid shift."))
            continue
        key = (emp_ids[e.key], di)
        if key in fixed_at:
            early.append(Issue(id=f"SOLVER|FIXED_DUP|{fx.row}", cell=f"B{fx.row}:E{fx.row}",
                               msg=f"{e.name} has two fixed shifts on {_day_txt(fx.date)} (rows {fixed_at[key][2]} and {fx.row}).",
                               fix="Keep one fixed shift per person per day."))
            continue
        fixed_at[key] = (sh, _up(fx.rec) or e.home, fx.row)

    # ---- pinned manual edits (milestone 12): work -> like a fixed shift (+ OT flag, extension); OFF -> no work
    lock_at: dict[tuple[int, int], object] = {}
    for lk in (getattr(inputs, "locked", ()) or ()):
        le = inputs.emp_by_name(lk.emp)
        ldi = date_idx.get(lk.date)
        if le is not None and ldi is not None and le.key in emp_ids:
            lock_at[(emp_ids[le.key], ldi)] = lk
    off_lits: list[tuple[tuple[int, int], cp_model.IntVar]] = []

    # ---- candidate variables
    by_ed: dict[tuple[int, int], list[_Cand]] = {}
    for ei, e in enumerate(emps):
        recs = [e.home] + [r for r in _also(e) if r != e.home]
        allowed = {_up(a) for a in e.allowed}
        ot_ok = ot_master and e.ot
        woman = _up(e.gender) != male_code
        for di, d in enumerate(days):
            leave = on_leave(e, d)
            block = is_block(e, d)
            fx = fixed_at.get((ei, di))
            lk = lock_at.get((ei, di))
            if lk is not None and not (lk.shift or "").strip():  # pinned OFF
                if diag_locks:
                    lv = m.NewBoolVar(f"lock_{ei}_{di}")
                    ctx.lock_lits.append((lk, e, lv))
                    off_lits.append(((ei, di), lv))
                else:
                    continue
            elif lk is not None:
                sh = inputs.shift_by_key.get(_up(lk.shift))
                rec = _up(lk.rec) or e.home
                why = []
                where_ot = ot_ok
                if sh is None or _up(sh.code) in bad_shifts:
                    why.append(f"the shift {lk.shift} is unknown or cannot be used")
                else:
                    if leave:
                        why.append("this is a leave day")
                    if woman and sh.male > 0:
                        why.append(f"{sh.code} touches the men-only window and only men may work there")
                    if (block or lk.ot or lk.ext_before_h or lk.ext_after_h) and not where_ot:
                        why.append("it counts as overtime and overtime is not allowed for this person")
                if why:
                    early.append(_lock_issue(lk, e, f"Your pinned change for {e.name} on {lk.date.strftime('%a %d %b')} cannot be kept: "
                                                    + "; ".join(why) + ". Unpin or change it."))
                    continue
                v = m.NewBoolVar(f"x_{ei}_{di}_l")
                c = _Cand(e, di, sh, rec, v, block or bool(lk.ot), True)
                if lk.ext_before_h or lk.ext_after_h:
                    _add_extension(m, c, st, woman, f"{ei}_{di}_l")
                    if lk.ext_before_h > len(c.ext_b) or lk.ext_after_h > len(c.ext_a):
                        early.append(_lock_issue(lk, e, f"Your pinned change for {e.name} on {lk.date.strftime('%a %d %b')} cannot be kept: "
                                                        f"{sh.code} cannot be extended by {lk.ext_before_h} h before and {lk.ext_after_h} h after "
                                                        "(split shift, longest extension, men-only window or day limits). Unpin or change it."))
                        continue
                    for k, z in enumerate(c.ext_b, start=1):
                        m.Add(z == (1 if k <= lk.ext_before_h else 0))
                    for k, z in enumerate(c.ext_a, start=1):
                        m.Add(z == (1 if k <= lk.ext_after_h else 0))
                if diag_locks:
                    lv = m.NewBoolVar(f"lock_{ei}_{di}")
                    ctx.lock_lits.append((lk, e, lv))
                    m.Add(v == 1).OnlyEnforceIf(lv)
                else:
                    m.Add(v == 1)
                ctx.cands.append(c)
                by_ed.setdefault((ei, di), []).append(c)
                continue
            if fx is not None:
                sh, rec, row = fx
                why = []
                if leave:
                    why.append(f"{e.name} is on leave on {_day_txt(d)} (leave table) - a fixed shift cannot be placed.")
                if woman and sh.male > 0:
                    why.append(f"{e.name} is a woman but fixed shift {sh.code} touches the men-only window (C13/C14).")
                if block and not ot_ok:
                    reason = ("the master overtime switch G14 is OFF" if not ot_master
                              else f"employee overtime flag O{e.row} is OFF")
                    why.append(f"{_day_txt(d)} is a planned day off of {e.name} and overtime is not allowed: {reason}.")
                for w in why:
                    early.append(Issue(id=f"SOLVER|FIXED|{row}|{di}", cell=f"B{row}:E{row}", msg=f"Fixed shift row {row}: {w}",
                                       fix="Move or delete the fixed shift, or change leave / overtime settings."))
                if why:
                    continue
                v = m.NewBoolVar(f"x_{ei}_{di}_f")
                m.Add(v == 1)
                c = _Cand(e, di, sh, rec, v, block, True)
                ctx.cands.append(c)
                by_ed.setdefault((ei, di), []).append(c)
                continue
            if leave:
                continue
            for sh in shifts:
                tags: list[str] = []
                if allowed and _up(sh.code) not in allowed:
                    tags.append("allowed")
                if woman and sh.male > 0:
                    tags.append("men_window")
                if block and not ot_ok:
                    tags.append("ot_flag")
                if tags and not relax:
                    continue
                for rec in recs:
                    v = m.NewBoolVar(f"x_{ei}_{di}_{len(ctx.cands)}")
                    c = _Cand(e, di, sh, rec, v, block)
                    if ot_ok and not block:
                        _add_extension(m, c, st, woman, f"{ei}_{di}_{len(ctx.cands)}")
                    ctx.cands.append(c)
                    by_ed.setdefault((ei, di), []).append(c)
                    for tg in tags:
                        ctx.add_slack(tg, (e, d, sh), v, RELAX_WEIGHT[tg])

    # one shift per employee per day
    work: dict[tuple[int, int], cp_model.LinearExpr] = {}
    for key, cs in by_ed.items():
        work[key] = sum(c.var for c in cs)
        if len(cs) > 1:
            m.Add(work[key] <= 1)

    def work_expr(ei: int, di: int):
        return work.get((ei, di), 0)

    if diag_locks:
        for key, lv in off_lits:
            if key in work:
                m.Add(work[key] == 0).OnlyEnforceIf(lv)
        if ctx.lock_lits:
            m.AddAssumptions([lv for _lk, _e, lv in ctx.lock_lits])

    # ---- pinned edits that contradict each other / a neighbouring fixed shift on rest (reported at their cells)
    if lock_at and not relax:
        fx_by = {(c.e.key, c.d): c for c in ctx.cands if c.fixed}
        for (ekey, dd), c1 in sorted(fx_by.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            c2 = fx_by.get((ekey, dd + 1))
            if c2 is None:
                continue
            ei2 = emp_ids[ekey]
            lk1, lk2 = lock_at.get((ei2, dd)), lock_at.get((ei2, dd + 1))
            if lk1 is None and lk2 is None:
                continue
            e1 = _shift_end(c1.shift) + (lk1.ext_after_h if lk1 else 0)
            s2 = _shift_start(c2.shift) - (lk2.ext_before_h if lk2 else 0)
            if 24.0 + s2 - e1 < st.min_rest - 1e-9:
                e_ = emps[ei2]
                early.append(_lock_issue(
                    lk1 or lk2, e_, f"{e_.name} would rest only {24.0 + s2 - e1:g} h between {c1.shift.code} on {days[dd].strftime('%a %d %b')} "
                                    f"and {c2.shift.code} on {days[dd + 1].strftime('%a %d %b')} (the minimum is {st.min_rest:g} h). "
                                    "One of these days is a pinned change - unpin or change it.", "LOCKREST"))

    # ---- rest between consecutive days
    for ei, e in enumerate(emps):
        for di in range(nd - 1):
            for s1 in shifts:
                a = [c.var for c in by_ed.get((ei, di), []) if c.shift is s1]
                if not a:
                    continue
                end1 = _shift_end(s1)
                bad = [c.var for c in by_ed.get((ei, di + 1), [])
                       if 24.0 + _shift_start(c.shift) - end1 < st.min_rest - 1e-9]
                if not bad:
                    continue
                lhs = sum(a) + sum(bad)
                if relax:
                    rs = m.NewBoolVar(f"rest_{ei}_{di}_{s1.row}")
                    ctx.add_slack("rest", (e, days[di]), rs, RELAX_WEIGHT["rest"])
                    m.Add(lhs <= 1 + rs)
                else:
                    m.Add(lhs <= 1)

    # ---- rest across the month boundary (history of the previous month, optional)
    if history is not None and st.first_day is not None and history.is_previous_of(st.first_day):
        for ei, e in enumerate(emps):
            ended = history.ended_at(e.key)
            if ended is None:
                continue
            for c in by_ed.get((ei, 0), []):
                slack_h = 24.0 + _shift_start(c.shift) - ended - st.min_rest
                if slack_h < -1e-9:
                    if relax:
                        rs = m.NewBoolVar(f"hrest_{ei}_{c.shift.row}")
                        ctx.add_slack("rest", (e, days[0]), rs, RELAX_WEIGHT["rest"])
                        m.Add(c.var <= rs)
                    else:
                        m.Add(c.var == 0)
                else:  # an extension before the start eats into the rest
                    for k, z in enumerate(c.ext_b, start=1):
                        if k > slack_h + 1e-9:
                            m.Add(z == 0)

    # ---- weekly normal hours / overtime
    wk_days: dict[int, list[int]] = {}
    for di, d in enumerate(days):
        wk_days.setdefault(st.wk_of(d), []).append(di)
    for ei, e in enumerate(emps):
        limit = int(round((e.max_h if (e.max_h or 0) > 0 else st.max_week_h) * _MIN))
        for wk, dis in wk_days.items():
            normal = [int(round(c.shift.paid * _MIN)) * c.var for di in dis for c in by_ed.get((ei, di), []) if not c.is_block]
            ot = [int(round(c.shift.paid * _MIN)) * c.var for di in dis for c in by_ed.get((ei, di), []) if c.is_block]
            ot += [_MIN * z for di in dis for c in by_ed.get((ei, di), []) for z in c.ext_b + c.ext_a]
            if normal:
                if relax:
                    sl = m.NewIntVar(0, 24 * 7 * _MIN, f"wk_{ei}_{wk}")
                    ctx.add_slack("weekly_hours", (e, wk), sl, RELAX_WEIGHT["weekly_hours"])
                    m.Add(sum(normal) <= limit + sl)
                else:
                    m.Add(sum(normal) <= limit)
            if ot:
                omax = int(round(st.ot_max * _MIN))
                if relax:
                    sl = m.NewIntVar(0, 24 * 7 * _MIN, f"otmax_{ei}_{wk}")
                    ctx.add_slack("ot_max", (e, wk), sl, RELAX_WEIGHT["ot_max"])
                    m.Add(sum(ot) <= omax + sl)
                else:
                    m.Add(sum(ot) <= omax)
            if st.ot_keep and len(dis) == 7:
                bdays = [di for di in dis if is_block(e, days[di])]
                if bdays:
                    wsum = sum(work_expr(ei, di) for di in bdays)
                    if relax:
                        sl = m.NewBoolVar(f"keep_{ei}_{wk}")
                        ctx.add_slack("ot_keep", (e, wk), sl, RELAX_WEIGHT["ot_keep"])
                        m.Add(wsum <= len(bdays) - 1 + sl)
                    else:
                        m.Add(wsum <= len(bdays) - 1)

    # ---- rest between an extended shift and the neighbouring day's shift
    kext = 2 * max(1, int(math.floor(st.ext_max + 1e-9)))
    for ei, e in enumerate(emps):
        for di in range(nd - 1):
            a_h = [z for c in by_ed.get((ei, di), []) for z in c.ext_a]
            b_h = [z for c in by_ed.get((ei, di + 1), []) for z in c.ext_b]
            if not a_h and not b_h:
                continue
            for s1 in shifts:
                x1 = [c.var for c in by_ed.get((ei, di), []) if c.shift is s1]
                if not x1:
                    continue
                for s2 in shifts:
                    x2 = [c.var for c in by_ed.get((ei, di + 1), []) if c.shift is s2]
                    if not x2:
                        continue
                    slack_h = 24.0 + _shift_start(s2) - _shift_end(s1) - st.min_rest
                    if slack_h >= kext:
                        continue
                    ub = max(0, int(math.floor(slack_h + 1e-9)))
                    m.Add(sum(a_h) + sum(b_h) <= ub + kext * (2 - sum(x1) - sum(x2)))

    # ---- coverage / max staff, aggregated per (reception, day, hour)
    # duty index: (reception, day, hour) -> variables of everybody on duty (base shift + extension hours)
    on_all: dict[tuple[str, int, int], list[cp_model.IntVar]] = {}
    on_men: dict[tuple[str, int, int], list[cp_model.IntVar]] = {}

    def put(c: _Cand, var: cp_model.IntVar, abs_hour: int) -> None:
        key = (c.rec, c.d + abs_hour // 24, abs_hour % 24)
        on_all.setdefault(key, []).append(var)
        if _up(c.e.gender) == male_code:
            on_men.setdefault(key, []).append(var)

    for c in ctx.cands:
        for ah in cov_of[_up(c.shift.code)]:
            put(c, c.var, ah)
        for side, lst in (("b", c.ext_b), ("a", c.ext_a)):
            prev: set[int] = set()
            for k, z in enumerate(lst, start=1):
                cur = _ext_slots(c.shift, side, k)
                for ah in cur - prev:
                    put(c, z, ah)
                prev = cur

    def duty(rec: str, di: int, h: int, male_only: bool) -> list[cp_model.IntVar]:
        return (on_men if male_only else on_all).get((rec, di, h), [])

    spec: dict[tuple[str, int, int], dict] = {}
    fri = _up(st.fri_name) or "FRI"
    for t in inputs.targets:
        rec = _up(t.rec)
        for di, d in enumerate(days):
            if not t.applies(d, fri, st.weekday_names):
                continue
            for h in _target_hours(t):
                sp = spec.setdefault((rec, di, h), {"min": 0.0, "male": 0.0, "max": None, "rows_min": [], "rows_male": [], "rows_max": []})
                if (t.min_staff or 0) >= 1:
                    sp["rows_min"].append(t.row)
                if (t.min_staff or 0) > sp["min"]:
                    sp["min"] = t.min_staff
                if (t.min_male or 0) >= 1:
                    sp["rows_male"].append(t.row)
                if (t.min_male or 0) > sp["male"]:
                    sp["male"] = t.min_male
                if t.max_staff is not None and t.max_staff >= 0:
                    sp["rows_max"].append(t.row)
                    sp["max"] = t.max_staff if sp["max"] is None else min(sp["max"], t.max_staff)

    carry = st.carry_in or 0.0
    for (rec, di, h), sp in sorted(spec.items()):
        carried = di == 0 and h < carry
        dv = duty(rec, di, h, False)
        mv = duty(rec, di, h, True)
        if sp["max"] is not None:
            if relax:
                sl = m.NewIntVar(0, 50, f"mx_{rec}_{di}_{h}")
                ctx.add_slack("max_staff", (rec, days[di], h, tuple(sp["rows_max"])), sl, RELAX_WEIGHT["max_staff"])
                m.Add(sum(dv) <= int(sp["max"]) + sl)
            else:
                m.Add(sum(dv) <= int(sp["max"]))
        if carried:
            continue
        need = int(math.ceil(sp["min"] - 1e-9)) if sp["min"] > 0 else 0
        hard_staff = sp["min"] >= 1
        hs = None
        if hard_staff:
            if relax:
                hs = m.NewBoolVar(f"cov_{rec}_{di}_{h}")
                ctx.add_slack("coverage", ("staff", rec, days[di], h, tuple(sp["rows_min"])), hs, RELAX_WEIGHT["coverage"])
                m.Add(sum(dv) + hs >= 1)
            else:
                m.Add(sum(dv) >= 1)
        if need > 1 or (need == 1 and not hard_staff):
            sf = m.NewIntVar(0, need, f"sf_{rec}_{di}_{h}")
            m.Add(sum(dv) + (hs if hs is not None else 0) + sf >= need)
            ctx.shortfall.append(("staff", sf))
        needm = int(math.ceil(sp["male"] - 1e-9)) if sp["male"] > 0 else 0
        hms = None
        if sp["male"] >= 1:
            if relax:
                hms = m.NewBoolVar(f"covm_{rec}_{di}_{h}")
                ctx.add_slack("coverage", ("male", rec, days[di], h, tuple(sp["rows_male"])), hms, RELAX_WEIGHT["coverage"])
                m.Add(sum(mv) + hms >= 1)
            else:
                m.Add(sum(mv) >= 1)
        if needm > 1:
            sf = m.NewIntVar(0, needm, f"sfm_{rec}_{di}_{h}")
            m.Add(sum(mv) + (hms if hms is not None else 0) + sf >= needm)
            ctx.shortfall.append(("male", sf))

    # ---- demand rows
    for dm in inputs.demand:
        for di, d in enumerate(days):
            idx = vba_weekday(d) - 1
            n = dm.counts[idx] if idx < len(dm.counts) else 0
            if n <= 0:
                continue
            vs = []
            for c in ctx.cands:
                if c.d != di or _up(c.shift.code) != _up(dm.shift) or c.rec != _up(dm.rec):
                    continue
                if _up(dm.gender) not in ("", "ANY") and _up(c.e.gender) != _up(dm.gender):
                    continue
                fill = _up(dm.fill)
                home = c.rec == c.e.home
                if fill == "HOME" and not home or fill == "HELP" and home:
                    continue
                vs.append(c.var)
            if relax:
                sl = m.NewBoolVar(f"dm_{dm.row}_{di}")
                ctx.add_slack("demand", (dm, d), sl, RELAX_WEIGHT["demand"])
                m.Add(sum(vs) + sl >= 1)
            else:
                m.Add(sum(vs) >= 1)
            sf = m.NewIntVar(0, n, f"dms_{dm.row}_{di}")
            m.Add(sum(vs) + sf >= n)
            ctx.demand_short.append(sf)

    # ---- soft goals (only in the normal model; the relaxed diagnosis model only minimises slack)
    if not relax:
        _add_soft_goals(ctx, inputs, emps, days, by_ed, work, wk_days, is_block, on_leave)
    return ctx, early


def _add_soft_goals(ctx: _Ctx, inputs: RosterInputs, emps: list[Employee], days: list[date],
                    by_ed: dict[tuple[int, int], list[_Cand]], work: dict, wk_days: dict[int, list[int]],
                    is_block, on_leave) -> None:
    """Register the weighted soft goals of BUILD_GUIDE section 3 on ``ctx`` (see :data:`WEIGHTS`)."""
    st = inputs.settings
    m = ctx.model
    nd = len(days)
    fri = _up(st.fri_name) or "FRI"
    is_fri = [_up(st.weekday_names[vba_weekday(d) - 1]) == fri for d in days]
    ot_min: dict[int, list] = {ei: [] for ei in range(len(emps))}
    ot_var: dict[int, list] = {ei: [] for ei in range(len(emps))}

    emp_idx = {e.key: i for i, e in enumerate(emps)}

    for c in ctx.cands:
        ei = emp_idx[c.e.key]
        if c.fixed:
            continue
        if c.is_block:
            ctx.add_soft("overtime_shift", c.var)
            ot_min[ei].append(int(round(c.shift.paid * _MIN)) * c.var)
            ot_var[ei].append(int(round(c.shift.paid * _MIN)) * c.var)
        if c.rec != c.e.home:
            ctx.add_soft("helper", c.var)
        if c.e.pref and _up(c.shift.code) not in {_up(p) for p in c.e.pref}:
            ctx.add_soft("pref_miss", c.var)
        for z in c.ext_b + c.ext_a:
            ctx.add_soft("ext_hour", z)
            ot_min[ei].append(_MIN * z)
            ot_var[ei].append(_MIN * z)
    for c in ctx.cands:  # fixed shifts on block days are overtime too (constant, but counted for fairness)
        if c.fixed and c.is_block:
            ot_min[emp_idx[c.e.key]].append(int(round(c.shift.paid * _MIN)))

    # fill to contract: idle non-block, non-leave days
    for ei, e in enumerate(emps):
        for di, d in enumerate(days):
            if on_leave(e, d) or is_block(e, d) or (ei, di) not in work:
                continue
            idle = m.NewBoolVar(f"idle_{ei}_{di}")
            m.Add(work[(ei, di)] + idle >= 1)
            ctx.add_soft("idle_day", idle)

    # G16 = N: still prefer to keep one block day per complete week free
    if not st.ot_keep:
        for ei, e in enumerate(emps):
            for wk, dis in wk_days.items():
                bdays = [di for di in dis if is_block(e, days[di]) and (ei, di) in work]
                if len(dis) != 7 or len(bdays) < 1:
                    continue
                allw = m.NewBoolVar(f"allblk_{ei}_{wk}")
                m.Add(sum(work[(ei, di)] for di in bdays) - (len(bdays) - 1) <= allw)
                ctx.add_soft("ot_keep_free", allw)

    # consistency: change of (shift, reception) between consecutive workdays of one week
    wk_of_day = [st.wk_of(d) for d in days]
    for ei in range(len(emps)):
        for di in range(nd - 1):
            if wk_of_day[di] != wk_of_day[di + 1] or (ei, di) not in work or (ei, di + 1) not in work:
                continue
            k1 = {(c.shift.code, c.rec): c.var for c in by_ed[(ei, di)]}
            k2 = {(c.shift.code, c.rec): c.var for c in by_ed[(ei, di + 1)]}
            sames = []
            for key, v1 in k1.items():
                v2 = k2.get(key)
                if v2 is None:
                    continue
                sv = m.NewBoolVar(f"same_{ei}_{di}")
                m.Add(sv <= v1)
                m.Add(sv <= v2)
                sames.append(sv)
            chg = m.NewBoolVar(f"chg_{ei}_{di}")
            m.Add(chg >= work[(ei, di)] + work[(ei, di + 1)] - 1 - sum(sames))
            ctx.add_soft("shift_change", chg)

    # fairness: minimise the maximum per employee of OT hours, Friday shifts and night shifts
    top_h = m.NewIntVar(0, 24 * 7 * 6, "max_ot_h")
    for ei in range(len(emps)):
        if ot_min[ei]:
            m.Add(_MIN * top_h >= sum(ot_min[ei]))
    ctx.add_soft("fair_ot_max", top_h)

    # OT priority ranks (milestone 11A): groups by rank; one extra fairness term inside every group
    ctx.ot_var = ot_var
    groups: dict[int, list[int]] = {}
    for ei, e in enumerate(emps):
        if ot_var[ei]:
            groups.setdefault(e.ot_rank if e.ot_rank is not None else UNRANKED, []).append(ei)
    if any(e.ot_rank is not None for ei, e in enumerate(emps) if ot_var[ei]):
        for key, members in sorted(groups.items()):
            if len(members) > 1:
                top_g = m.NewIntVar(0, 24 * 7 * 6, f"max_ot_rank{key}")
                for ei in members:
                    m.Add(_MIN * top_g >= sum(ot_min[ei]))
                ctx.add_soft("fair_ot_rank", top_g)
        if len(groups) > 1:  # stages: lowest priority first; the best group (smallest key) is left free
            best = min(groups)
            ctx.rank_stages = [(k, groups[k]) for k in sorted(groups, reverse=True) if k != best]
    top_f = m.NewIntVar(0, nd, "max_fri")
    top_n = m.NewIntVar(0, nd, "max_night")
    for ei in range(len(emps)):
        fr = [work[(ei, di)] for di in range(nd) if is_fri[di] and (ei, di) in work]
        if fr:
            m.Add(top_f >= sum(fr))
        nt = [c.var for di in range(nd) for c in by_ed.get((ei, di), []) if c.shift.male > 0]
        if nt:
            m.Add(top_n >= sum(nt))
    ctx.add_soft("fair_fri_max", top_f)
    ctx.add_soft("fair_night_max", top_n)


# ----------------------------------------------------------------- infeasibility report
def _rows_txt(rows) -> str:
    rows = sorted(set(rows))
    return ", ".join(f"D{r}:J{r}" for r in rows)


def _report_slacks(inputs: RosterInputs, ctx: _Ctx, solver: cp_model.CpSolver) -> list[Issue]:
    """Turn the slack variables used by the relaxed solution into plain-English Issues."""
    st = inputs.settings
    win = f"{st.male_from:g}:00-{st.male_to:g}:00"
    out: list[Issue] = []

    # coverage: one Issue per target-row set
    cov: dict[tuple, list[tuple[str, date, int]]] = {}
    for info, var, _w in ctx.slack.get("coverage", []):
        if solver.Value(var):
            kind, rec, d, h, rows = info
            cov.setdefault((kind, rows), []).append((rec, d, h))
    for (kind, rows), lst in cov.items():
        recs = sorted({r for r, _, _ in lst})
        first = min(lst, key=lambda z: (z[1], z[2]))
        cells = _rows_txt(rows)
        if kind == "male":
            msg = (f"Coverage target {cells} needs a MAN on duty in every hour ({len(lst)} reception-hours cannot be staffed with the men "
                   f"available, first {_day_txt(first[1])} {_hour_txt(first[2])} at {recs[0]}). Only men may work the men-only window "
                   f"{win} (C13/C14), and the men are limited by leave, allowed shifts, rest, weekly hours and days off.")
            fix = "Make more men active (column F), allow them the night shift (I:K), allow overtime (G14 / column O), or lower Min male in H."
        else:
            msg = (f"Coverage target {cells} needs at least 1 person every hour ({len(lst)} reception-hours cannot be staffed, first "
                   f"{_day_txt(first[1])} {_hour_txt(first[2])} at {recs[0]}).")
            fix = "Add staff for this reception, allow more shifts (I:K), allow overtime (G14 / column O), or lower Min in G."
        out.append(Issue(id=f"SOLVER|COVERAGE|{kind}|{cells}", cell=cells, msg=msg, fix=fix))

    def collect(group: str) -> list[tuple[object, int]]:
        return [(info, int(solver.Value(v))) for info, v, _w in ctx.slack.get(group, []) if solver.Value(v)]

    men: dict[str, int] = {}
    for (e, d, sh), _n in collect("men_window"):
        men[e.name] = men.get(e.name, 0) + 1
    seen: set[str] = set()
    for (e, d, sh), _n in collect("men_window"):
        if e.name in seen:
            continue
        seen.add(e.name)
        out.append(Issue(id=f"SOLVER|MEN_WINDOW|{e.row}", cell=f"C{e.row}",
                         msg=(f"{e.name} is a woman but would need to work {men[e.name]} shift(s) inside the men-only window {win} "
                              f"(settings C13/C14) to cover the targets; only men may work there (e.g. {sh.code} on {_day_txt(d)})."),
                         fix="Add a man for the night hours, or change the men-only window C13:C14."))
    al: dict[str, tuple[Employee, int, str]] = {}
    for (e, d, sh), _n in collect("allowed"):
        cur = al.get(e.name)
        al[e.name] = (e, (cur[1] if cur else 0) + 1, cur[2] if cur else sh.code)
    for name, (e, n, code) in al.items():
        out.append(Issue(id=f"SOLVER|ALLOWED|{e.row}", cell=f"I{e.row}:K{e.row}",
                         msg=f"{name} would have to work {n} shift(s) outside the allowed shifts (e.g. {code}) to cover the targets.",
                         fix="Add shifts to the employee's allowed list (columns I:K) or add staff."))
    otf: dict[str, tuple[Employee, int]] = {}
    for (e, d, sh), _n in collect("ot_flag"):
        otf[e.name] = (e, (otf[e.name][1] if e.name in otf else 0) + 1)
    for name, (e, n) in otf.items():
        # R14.2: word the reason from the actual values - G14 only when G14 itself is OFF
        if not st.ot_on:
            why, cell = "the master overtime switch G14 is OFF", f"G14,O{e.row}"
            fix = "Switch the master overtime switch G14 ON (and the employee's column O flag), or add staff."
        else:
            why, cell = f"employee overtime flag O{e.row} is OFF", f"O{e.row}"
            fix = f"Switch overtime ON for this employee (O{e.row}), or add staff."
        out.append(Issue(id=f"SOLVER|OT_FLAG|{e.row}", cell=cell,
                         msg=f"{name} would have to work {n} planned day(s) off as overtime, but overtime is not allowed: {why}.",
                         fix=fix))
    rest: dict[str, tuple[Employee, int]] = {}
    for (e, d), _n in collect("rest"):
        rest[e.name] = (e, (rest[e.name][1] if e.name in rest else 0) + 1)
    for name, (e, n) in rest.items():
        out.append(Issue(id=f"SOLVER|REST|{e.row}", cell=f"C11,B{e.row}",
                         msg=f"{name} would have {n} day-pair(s) with less than the minimum rest of {st.min_rest:g} h (C11) between shifts to cover the targets.",
                         fix="Lower C11 (not below the legal minimum), allow other shifts, or add staff."))
    for info, mins in collect("weekly_hours"):
        e, wk = info
        lim = e.max_h if (e.max_h or 0) > 0 else st.max_week_h
        who = "man" if _up(e.gender) == (_up(st.male_code) or "M") else "woman"
        out.append(Issue(id=f"SOLVER|WEEKLY|{e.row}|{wk}", cell=f"B{e.row},N{e.row},C9",
                         msg=(f"{e.name} ({who}) would exceed the weekly limit of {lim:g} h (C9 / column N) by {mins / _MIN:g} h in week {wk} "
                              f"to cover the targets; the people who can work the required hours (including the men-only window {win}) are not enough."),
                         fix="Add staff or men, allow overtime, or raise the weekly hours."))
    for info, mins in collect("ot_max"):
        e, wk = info
        out.append(Issue(id=f"SOLVER|OTMAX|{e.row}|{wk}", cell=f"G15,O{e.row}",
                         msg=f"{e.name} would exceed the weekly overtime maximum {st.ot_max:g} h (G15) by {mins / _MIN:g} h in week {wk}.",
                         fix="Raise G15 or add staff."))
    for info, _n in collect("ot_keep"):
        e, wk = info
        out.append(Issue(id=f"SOLVER|OTKEEP|{e.row}|{wk}", cell=f"G16,B{e.row}",
                         msg=f"{e.name} would have no free day left in week {wk} ('keep 1 free day' G16 = Y).",
                         fix="Set G16 to N or add staff."))
    for info, _n in collect("max_staff"):
        rec, d, h, rows = info
        out.append(Issue(id=f"SOLVER|MAXSTAFF|{rec}|{d}|{h}", cell=", ".join(f"I{r}" for r in sorted(set(rows))),
                         msg=f"Max staff at {rec} on {_day_txt(d)} {_hour_txt(h)} conflicts with the people that must be on duty (fixed shifts, demand or Min male).",
                         fix="Raise Max staff (column I) or remove the conflicting fixed shift / demand."))
    for info, _n in collect("demand"):
        dm, d = info
        out.append(Issue(id=f"SOLVER|DEMAND|{dm.row}|{d}", cell=f"B{dm.row}:O{dm.row}",
                         msg=f"Demand row {dm.row} ({dm.rec}, {dm.shift}) cannot get even 1 person on {_day_txt(d)}.",
                         fix="Make the row inactive, lower the count, or add eligible staff."))
    return out


def _men_capacity_issues(inputs: RosterInputs, blocks: dict[str, DayOff]) -> list[Issue]:
    """Quick necessary-condition checks on the men-only targets (plain-English, cell-specific)."""
    st = inputs.settings
    days = _dates(inputs)
    male_code = _up(st.male_code) or "M"
    win = f"{st.male_from:g}:00-{st.male_to:g}:00"
    fri = _up(st.fri_name) or "FRI"
    need: dict[tuple[str, int, int], list[int]] = {}
    for t in inputs.targets:
        if (t.min_male or 0) < 1:
            continue
        for di, d in enumerate(days):
            if not t.applies(d, fri, st.weekday_names):
                continue
            for h in _target_hours(t):
                if di == 0 and h < (st.carry_in or 0.0):
                    continue
                need.setdefault((_up(t.rec), di, h), []).append(t.row)
    if not need:
        return []
    rows = sorted({r for v in need.values() for r in v})
    cells = _rows_txt(rows)
    men = [e for e in inputs.employees if e.active and e.name.strip() and _up(e.gender) == male_code]
    out: list[Issue] = []
    if not men:
        return [Issue(id="SOLVER|NO_MEN", cell=f"{cells},C109:C168",
                      msg=f"Targets {cells} need a man every hour (men-only window {win}), but no active man is listed in the employee table.",
                      fix="Add or activate men (columns C and F).")]
    # (a) hours no eligible man can cover at all (allowed shifts / reception)
    uncovered: dict[tuple[str, int], list[tuple[str, int]]] = {}
    for (rec, di, h), rws in need.items():
        ok = False
        for e in men:
            if rec != e.home and rec not in _also(e):
                continue
            al = {_up(a) for a in e.allowed}
            for s in inputs.shifts:
                if al and _up(s.code) not in al:
                    continue
                cv = covered_hours(s)
                if h in cv or (di > 0 and h + 24 in cv):
                    ok = True
                    break
            if ok:
                break
        if not ok:
            uncovered.setdefault((rec, h), []).append((_day_txt(days[di]), di))
    if uncovered:
        (rec, h), v = next(iter(sorted(uncovered.items())))
        out.append(Issue(id="SOLVER|MEN_NO_SHIFT", cell=f"{cells},I109:K168",
                         msg=(f"Targets {cells} need a man at {rec} at {_hour_txt(h)} (men-only window {win}, C13/C14) but none of the "
                              f"{len(men)} active men is allowed a shift that covers that hour ({len(uncovered)} hour(s) affected)."),
                         fix="Give a man the night shift in his allowed shifts (columns I:K), or add a man."))
    # (b) man-hours needed in the month vs what the men can work
    required = float(len(need))
    cap = 0.0
    for e in men:
        free = sum(1 for d in days if not any(_up(lv.emp) == e.key and lv.frm <= d <= lv.to for lv in inputs.leave))
        lim = e.max_h if (e.max_h or 0) > 0 else st.max_week_h
        wk = lim
        if st.ot_on and e.ot:
            b = blocks.get(e.key)
            blen = len(b.block_days) if b else 0
            wk += min(st.ot_max, 8.0 * blen)
        cap += wk * (free / 7.0)
    cap = min(cap, 24.0 * len(days) * len(men))
    if required > cap + 1e-9:
        out.append(Issue(id="SOLVER|MEN_HOURS", cell=f"{cells},C9,C13:C14",
                         msg=(f"Targets {cells} need a man on duty for {required:g} reception-hours this month (men-only window {win}), "
                              f"but the {len(men)} active men can work at most about {cap:.0f} h (weekly limit C9 / column N, leave"
                              f"{', overtime' if st.ot_on else ', no overtime'})."),
                         fix="Add more men (column F / C), allow overtime for them (G14, column O), or reduce Min male in column H."))
    return out


# ------------------------------------------------------------------------- public API
def _status_name(status: int) -> str:
    return {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE", cp_model.INFEASIBLE: "INFEASIBLE",
            cp_model.MODEL_INVALID: "MODEL_INVALID"}.get(status, "UNKNOWN")


class Cancelled(Exception):
    """The caller asked the solver to stop (``solve(..., stop=...)``)."""


#: the stop callback of the running ``solve`` call (one build at a time); ``None`` = cannot be cancelled
_STOP_CB = None


class _StoppableSolver(cp_model.CpSolver):
    """CP-SAT solver that a watcher thread stops as soon as the stop callback says so (clean stop:
    ``StopSearch`` returns the best solution found, then :class:`Cancelled` is raised)."""

    def Solve(self, model, solution_callback=None):  # noqa: N802
        cb = _STOP_CB
        if cb is None:
            return super().Solve(model, solution_callback)
        if cb():
            raise Cancelled()
        done = threading.Event()

        def watch() -> None:
            while not done.wait(0.1):
                if cb():
                    self.StopSearch()
                    return

        t = threading.Thread(target=watch, daemon=True)
        t.start()
        try:
            status = super().Solve(model, solution_callback)
        finally:
            done.set()
            t.join()
        if cb():
            raise Cancelled()
        return status


def _solver(time_limit: float, workers: int) -> cp_model.CpSolver:
    s = _StoppableSolver()
    s.parameters.max_time_in_seconds = float(time_limit)
    s.parameters.random_seed = 1
    s.parameters.num_workers = max(1, int(workers))
    return s


def _relaxed_issues(inputs: RosterInputs, blocks: dict[str, DayOff], bad: set[str], time_limit: float, workers: int,
                    history=None) -> list[Issue]:
    """Solve the slack-relaxed model and turn the slack that had to be used into Issues."""
    rctx, _ = _build(inputs, blocks, True, bad, history)
    rctx.model.Minimize(sum(rctx.objective_terms))
    rs = _solver(min(float(time_limit), 30.0), workers)
    rstat = rs.Solve(rctx.model)
    if rstat in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _report_slacks(inputs, rctx, rs)
    return [Issue(
        id="SOLVER|UNRESOLVED", cell="C9,C11,G14:G16",
        msg="No roster exists even when coverage, men-only window, rest, weekly hours and overtime limits are relaxed; "
            "check leave, fixed shifts, max staff and demand rows.",
        fix="Review the fixed shifts, leave table, Max staff column and demand rows.")]


def _lock_core_issues(inputs: RosterInputs, blocks: dict[str, DayOff], bad: set[str], time_limit: float, workers: int,
                      history=None) -> list[Issue]:
    """Which pinned changes cannot all be kept?  Solves the hard model with every pin as an assumption and
    reports the pins of the infeasibility core, each at its roster cell (empty list when none are pinned)."""
    if not getattr(inputs, "locked", ()):
        return []
    ctx, _early = _build(inputs, blocks, False, bad, history, diag_locks=True)
    if not ctx.lock_lits:
        return []
    sv = _solver(min(float(time_limit), 20.0), 1)
    status = sv.Solve(ctx.model)
    if status != cp_model.INFEASIBLE:
        return []
    core = set(sv.SufficientAssumptionsForInfeasibility())
    hit = [(lk, e) for lk, e, lv in ctx.lock_lits if lv.Index() in core] or [(lk, e) for lk, e, _lv in ctx.lock_lits]
    out = []
    for lk, e in hit[:8]:
        what = f"works {lk.shift}" if (lk.shift or "").strip() else "is off"
        out.append(_lock_issue(
            lk, e, f"Your pinned change ({e.name} {what} on {lk.date.strftime('%a %d %b')}) cannot be kept together with the other pinned "
                   "changes and the rules (coverage, rest, weekly hours or days off). Unpin it or change it.", "LOCKCORE"))
    return out


def _hint_from(m: cp_model.CpModel, solver: cp_model.CpSolver) -> None:
    """Use the solution of ``solver`` as the starting point (hint) of the next solve of ``m``."""
    m.ClearHints()
    for i in range(len(m.Proto().variables)):
        v = m.GetIntVarFromProtoIndex(i)
        m.AddHint(v, solver.Value(v))


def _rank_stages(ctx: _Ctx, terms: list, solver: cp_model.CpSolver, status: int, time_limit: float,
                 workers: int) -> tuple[cp_model.CpSolver, int, object]:
    """OT priority ranks, strict order (milestone 11A, BUILD_GUIDE section 3).

    The base solve (all soft goals, ranks ignored) has fixed how much coverage / demand is missed and how many
    idle days there are; those stay as they are (hard bounds).  Then OT *minutes* are minimised group by
    group, lowest priority first: unranked, rank 10, 9 ... 2.  Every stage freezes its optimum before the
    next one starts, so a lower group only gets OT that no higher group can take (rank 1 is never limited).
    The last stage solves the normal weighted objective again inside these bounds (fairness, cheapest-first
    type order, consistency ...).  A stage whose group already has 0 OT minutes needs no solve.

    Returns ``(solver, status, how)`` of the last good solution; ``how`` is ``"full"`` when it comes from the
    final full-objective solve, ``"stage"`` when a stage solution had to be used, ``False`` if nothing changed.
    """
    m = ctx.model
    t_stage = max(1.0, time_limit * _STAGES_SHARE / max(1, len(ctx.rank_stages)))
    m.Add(sum(v for _, v in ctx.shortfall) <= int(sum(solver.Value(v) for _, v in ctx.shortfall)))
    if ctx.demand_short:
        m.Add(sum(ctx.demand_short) <= int(sum(solver.Value(v) for v in ctx.demand_short)))
    idle = ctx.soft.get("idle_day", [])
    if idle:
        m.Add(sum(idle) <= int(sum(solver.Value(v) for v in idle)))
    cur, cur_status, how = solver, status, False
    for _key, members in ctx.rank_stages:
        exprs = [x for ei in members for x in ctx.ot_var.get(ei, [])]
        if not exprs:
            continue
        expr = sum(exprs)
        val = sum(cur.Value(x) for x in exprs)
        if val > 0:
            _hint_from(m, cur)
            m.Minimize(expr)
            s = _solver(t_stage, workers)
            st_ = s.Solve(m)
            if st_ not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                return cur, cur_status, how  # out of time: keep the last good solution
            cur, cur_status, how = s, st_, "stage"
            val = sum(cur.Value(x) for x in exprs)
        m.Add(expr <= val)
    if how == "stage":
        _hint_from(m, cur)
        m.Minimize(sum(terms))
        s = _solver(max(1.0, time_limit * (1.0 - _BASE_SHARE - _STAGES_SHARE)), workers)
        st_ = s.Solve(m)
        if st_ in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            cur, cur_status, how = s, st_, "full"
    return cur, cur_status, how


def _solve_once(inputs: RosterInputs, blocks: dict[str, DayOff], time_limit: float, workers: int, history=None) -> Roster:
    """Solve the month with CP-SAT and return a :class:`Roster`.

    ``status`` is OPTIMAL / FEASIBLE (assignments filled), INFEASIBLE (``issues`` names the input
    cells to change, no assignments) or UNKNOWN (time limit hit without any solution).
    ``objective`` holds the breakdown (``total`` and the four weighted terms plus raw counts).
    """
    st = inputs.settings
    roster = Roster(blocks=blocks)
    if st.first_day is None or st.days_in_month <= 0:
        roster.status = "INFEASIBLE"
        roster.issues = [Issue(id="SOLVER|NO_MONTH", cell="C6", msg="The month in INPUT C6 is missing, so no roster can be built.", fix="Enter a date in the month.")]
        return roster
    bad, shift_issues = _unusable_shifts(inputs)

    ctx, early = _build(inputs, blocks, False, bad, history)
    if early:
        # R11.4: fixed shifts that cannot be placed make the model infeasible by construction; still
        # run the relaxed diagnosis so every other problem is reported in the same run.
        roster.status = "INFEASIBLE"
        roster.issues = shift_issues + early + _men_capacity_issues(inputs, blocks) + _relaxed_issues(
            inputs, blocks, bad, time_limit, workers, history)
        return roster
    terms = [WEIGHTS["coverage_shortfall"] * v for _, v in ctx.shortfall]
    terms += [WEIGHTS["demand_shortfall"] * v for v in ctx.demand_short]
    for name, exprs in ctx.soft.items():
        terms += [WEIGHTS[name] * x for x in exprs]
    ctx.model.Minimize(sum(terms))
    staged = bool(ctx.rank_stages)
    solver = _solver(time_limit * _BASE_SHARE if staged else time_limit, workers)
    status = solver.Solve(ctx.model)
    refined = False
    if staged and status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        solver, status, refined = _rank_stages(ctx, terms, solver, status, time_limit, workers)
    roster.status = _status_name(status)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        days = _dates(inputs)
        for c in ctx.cands:
            if solver.Value(c.var):
                roster.assignments.append(Assignment(
                    emp_key=c.e.key, date=days[c.d], shift_code=c.shift.code, rec=c.rec,
                    is_ot=c.is_block, is_helper=(c.rec != c.e.home),
                    ext_before_h=sum(solver.Value(z) for z in c.ext_b),
                    ext_after_h=sum(solver.Value(z) for z in c.ext_a)))
        roster.assignments.sort(key=lambda a: (a.date, a.emp_key))
        raw = {
            "coverage_shortfall": sum(solver.Value(v) for _, v in ctx.shortfall),
            "demand_shortfall": sum(solver.Value(v) for v in ctx.demand_short),
        }
        for name in TERM_KEYS:
            if name not in raw:
                raw[name] = sum(solver.Value(x) for x in ctx.soft.get(name, []))
        roster.objective = {}
        for name, (rkey, ckey) in TERM_KEYS.items():
            roster.objective[rkey] = float(raw[name])
            roster.objective[ckey] = float(raw[name] * WEIGHTS[name])
        if staged:  # the last solver may have minimised an OT-hours stage objective: recompute the weighted total
            total = float(sum(roster.objective[ckey] for _, ckey in TERM_KEYS.values()))
            roster.objective["total"] = total
            roster.objective["best_bound"] = float(solver.BestObjectiveBound()) if refined == "full" else total
        else:
            roster.objective["total"] = float(solver.ObjectiveValue())
            roster.objective["best_bound"] = float(solver.BestObjectiveBound())
        roster.issues = shift_issues + spacing_issues(inputs, blocks, history)
        return roster

    if status == cp_model.INFEASIBLE:
        roster.issues = (shift_issues + _lock_core_issues(inputs, blocks, bad, time_limit, workers, history)
                         + _men_capacity_issues(inputs, blocks)
                         + _relaxed_issues(inputs, blocks, bad, time_limit, workers, history))
        roster.objective = {}
        return roster

    roster.issues = shift_issues + [Issue(
        id="SOLVER|NO_SOLUTION", cell="C9,C11",
        msg=f"No roster was found within {time_limit:g} s (solver status {roster.status}); the problem may be feasible but hard.",
        fix="Increase the time limit or simplify the targets.")]
    return roster


def idle_weekdays(inputs: RosterInputs, blocks: dict[str, DayOff], roster: Roster) -> dict[str, set[str]]:
    """Port of ``MarkIdle``: employee -> weekdays on which they were idle (no shift, no leave, not in
    their block) on at least 2 dates and on all but at most one occurrence of that weekday."""
    st = inputs.settings
    names = st.weekday_names
    worked = {(a.emp_key, a.date) for a in roster.assignments}
    out: dict[str, set[str]] = {}
    for e in inputs.employees:
        if not (e.name.strip() and e.active and e.home):
            continue
        b = blocks.get(e.key)
        occ = [0] * 7
        idl = [0] * 7
        for d in _dates(inputs):
            if any(_up(lv.emp) == e.key and lv.frm <= d <= lv.to for lv in inputs.leave):
                continue
            x = vba_weekday(d) - 1
            occ[x] += 1
            if (e.key, d) not in worked and not (b and b.is_block_day(d, names)):
                idl[x] += 1
        for x in range(7):
            if occ[x] > 0 and idl[x] >= 2 and idl[x] >= occ[x] - 1:
                out.setdefault(e.key, set()).add(names[x].strip().upper())
    return out


def solve(inputs: RosterInputs, blocks: dict[str, DayOff] | None = None, time_limit: float = 60, workers: int = 8,
          history=None, stop=None) -> Roster:
    """Solve the month (see :func:`_solve_two_pass`); for inputs from the data store the issues also
    carry field locators and texts without Excel cell references.

    ``stop`` (optional callable returning True) cancels the search cleanly: the solver is stopped within
    about 0.1 s and the result is a roster with ``status == "CANCELLED"`` and no assignments.
    Pinned manual edits (``inputs.locked``) are kept exactly as fixed shifts."""
    global _STOP_CB
    _STOP_CB = stop
    try:
        r = _solve_two_pass(inputs, blocks, time_limit, workers, history)
    except Cancelled:
        r = Roster(blocks=blocks or {}, status="CANCELLED")
        return r
    finally:
        _STOP_CB = None
    localize_issues(r.issues, inputs)
    return r


def _solve_two_pass(inputs: RosterInputs, blocks: dict[str, DayOff] | None = None, time_limit: float = 60, workers: int = 8,
                    history=None) -> Roster:
    """Solve the month with CP-SAT and return a :class:`Roster`.

    ``status`` is OPTIMAL / FEASIBLE (assignments filled), INFEASIBLE (``issues`` names the input
    cells to change, no assignments) or UNKNOWN (time limit hit without any solution).
    ``objective`` holds the breakdown (``total`` and the weighted terms plus raw counts) and
    ``blocks`` the day-off blocks used.

    Second pass (BUILD_GUIDE section 9 "no idle day", port of ``MarkIdle``): if the first solve is
    feasible and some employee was idle on a whole weekday outside the block, that weekday is added
    to the employee's required days, the blocks are re-planned and the month is solved ONCE more.
    ``history`` (optional :class:`~roster_builder.history.MonthHistory` of the previous month) adds
    the rest rule across the month boundary (C11 from the last shift of last month to the first shift
    of this month) and plans the days off with the spacing across the boundary; with ``None`` nothing
    changes. Spacing problems of explicitly passed blocks are reported in ``issues`` (non-fatal).

    The second result replaces the first when it is feasible and has no more coverage shortfall.
    """
    st = inputs.settings
    if blocks is None and st.first_day is not None:
        blocks, _ = plan_days_off(inputs, history=history)
    first = _solve_once(inputs, blocks or {}, time_limit, workers, history)
    if first.status not in ("OPTIMAL", "FEASIBLE"):
        return first
    force = idle_weekdays(inputs, blocks or {}, first)
    if not force:
        return first
    blocks2, _ = plan_days_off(inputs, force_off=force, history=history)
    second = _solve_once(inputs, blocks2, time_limit, workers, history)
    if second.status in ("OPTIMAL", "FEASIBLE") and (
            second.objective["coverage_shortfall_hours"] <= first.objective["coverage_shortfall_hours"]
            and second.objective["demand_shortfall"] <= first.objective["demand_shortfall"]):
        second.passes = 2
        return second
    return first
