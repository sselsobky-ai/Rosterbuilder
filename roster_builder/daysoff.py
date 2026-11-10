"""Day-off block planner – BUILD_GUIDE.md section 3 (hard rule "DAYS OFF – strict rule").

Faithful port of ``PlanDaysOff`` from ``RosterBuilder_current_source.bas``:

- block length = ``max(C15, 7 - floor(weekly max hours / standard shift hours))``;
- the block must hold the required days off (fixed days off, the weekend days of the staff
  with "No weekends", the Friday of the staff who is not Friday-eligible); when the required
  days need a longer run than the computed block, the block is extended to the shortest run
  that holds them all (the input check keeps the problem as a warning);
- AUTO blocks go to the window with the best slack, scored like any other window;
- blocks are placed most-constrained-first (fewest valid windows) and scored by the worst day
  slack ``available - 1 - need`` (men on their own male numbers), decrementing availability;
- the plan repeats weekly by weekday, so ``DayOff.is_block_day`` works across month boundaries;
- employees on leave do not count as available.

Milestone 3 deliverable of BUILD_GUIDE.md section 8 (days-off planner).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .io_xlsx import RosterInputs
from .history import MIN_WORK_BETWEEN, spacing_ok
from .locate import localize_issues
from .model import Employee, Issue, WEEKDAY_NAMES, day_label, vba_weekday


@dataclass
class DayOff:
    """Day-off block of one employee (weekday pattern, repeats every 7 days)."""

    emp: str
    block_days: list[str]
    length: int
    warnings: list[str] = field(default_factory=list)

    def is_block_day(self, d: date, names: tuple[str, ...] = WEEKDAY_NAMES) -> bool:
        if not self.block_days:
            return False
        return day_label(vba_weekday(d), names) in self.block_days


def _shift_len(inp: RosterInputs) -> float:
    return inp.settings.max_shift_h if (inp.settings.max_shift_h or 0.0) > 0 else 8.0


def block_length(inp: RosterInputs, emp: Employee) -> int:
    """Block length = max(C15 days off/week, 7 - floor(weekly max hours / max shift hours))."""
    weekly = emp.max_h if (emp.max_h or 0) > 0 else (inp.settings.max_week_h or 0.0)
    sh = _shift_len(inp)
    full_weeks = int(weekly // sh + 0.0001) if sh > 0 else 0
    return max(int(round(inp.settings.days_off_wk)), 7 - full_weeks)


def required_days(inp: RosterInputs, emp: Employee) -> set[str]:
    """Weekdays that the employee's day-off block must contain (upper-case names, no 'AUTO')."""
    req: set[str] = set()
    for dn in (emp.fixed_off, emp.fixed_off2):
        if dn and dn.strip().upper() != "AUTO":
            req.add(dn.strip().upper())
    if emp.no_we:
        for part in (inp.settings.weekend_days or "FRI,SAT").split(","):
            dn = (part or "").strip().upper()
            if dn and dn not in req:
                req.add(dn)
    if not emp.fri:
        req.add((inp.settings.fri_name or "FRI").strip().upper())
    return req


def _holds(names: list[str], s: int, k: int, req: set[str]) -> bool:
    """True if the cyclic window of length k starting at s (0-based) holds all required days."""
    for dn in req:
        i = names.index(dn) if dn in names else -1
        if i < 0:
            return False
        if ((i - s) % 7) >= k:
            return False
    return True


def _shortest_run(names: list[str], req: set[str]) -> list[str] | None:
    """Shortest consecutive cyclic run of weekdays holding all required days (R8.3)."""
    if not req:
        return None
    for k in range(len(req), 8):
        for s in range(7):
            days = [names[(s + i) % 7] for i in range(k)]
            if req <= set(days):
                return days
    return None


def _rep_date(inp: RosterInputs, vba_idx: int) -> date | None:
    """First day of the month whose VBA weekday number is ``vba_idx`` (1=SUN..7=SAT)."""
    st = inp.settings.first_day or inp.settings.month_start
    if st is None:
        return None
    n = inp.settings.days_in_month or 30
    for i in range(n):
        d = st + timedelta(days=i)
        if vba_weekday(d) == vba_idx:
            return d
    return None


def _month_dates(inp: RosterInputs) -> list[date]:
    """The days of the planned month (settings.first_day or settings.month_start, days_in_month long)."""
    st = inp.settings.first_day or inp.settings.month_start
    n = inp.settings.days_in_month or 30
    if st is None:
        return []
    return [st + timedelta(days=i) for i in range(n)]


def _free_days(inp: RosterInputs, e: Employee, vba_idx: int) -> int:
    """How many days of the month with VBA weekday ``vba_idx`` (1=SUN..7=SAT) the employee is NOT on leave for."""
    return sum(1 for d in _month_dates(inp) if vba_weekday(d) == vba_idx and not _emp_on_leave(inp, e, d))


def _weekday_count(inp: RosterInputs, vba_idx: int) -> int:
    """How many days of the month have VBA weekday ``vba_idx`` (1=SUN..7=SAT)."""
    return sum(1 for d in _month_dates(inp) if vba_weekday(d) == vba_idx)


def _tg_need(inp: RosterInputs, rec: str, d: date | None, male_only: bool) -> float:
    """People needed on day ``d`` for reception ``rec`` to meet the coverage targets."""
    if d is None:
        return 0.0
    sh = _shift_len(inp)
    tot = 0.0
    for h in range(24):
        mx = 0.0
        for t in inp.targets:
            if (t.rec or "").strip().upper() != (rec or "").strip().upper():
                continue
            if not t.applies(d, inp.settings.fri_name or "FRI"):
                continue
            if not (t.frm <= h < t.to):
                continue
            m = 0.0
            if male_only:
                if (t.min_male or 0) > 0:
                    m = t.min_male
            else:
                if (t.min_staff or 0) > 0:
                    m = t.min_staff
            if m > mx:
                mx = m
        tot += mx
    if sh > 0:
        return tot / sh
    return tot


def _day_need(inp: RosterInputs, rec: str, day_name: str, male_only: bool) -> float:
    """People needed for reception ``rec`` on weekday ``day_name``: max(demand, coverage need)."""
    names = list(inp.settings.weekday_names)
    dn = (day_name or "").strip().upper()
    idx = names.index(dn) if dn in names else -1
    tgn = math.ceil(_tg_need(inp, rec, _rep_date(inp, idx + 1), male_only) - 1e-9) if idx >= 0 else 0.0
    dem = 0.0
    for dm in inp.demand:
        if (dm.rec or "").strip().upper() != (rec or "").strip().upper():
            continue
        cnt = -1 if dm.all_week else (dm.counts[idx] if 0 <= idx < len(dm.counts) else 0)
        if cnt <= 0:
            continue
        if male_only and (dm.gender or "ANY").strip().upper() != (inp.settings.male_code or "M"):
            continue
        dem += cnt
    return max(dem, tgn)


def _emp_on_leave(inp: RosterInputs, e: Employee, d: date | None) -> bool:
    if d is None:
        return False
    for lv in inp.leave:
        if (lv.emp or "").strip().upper() == e.key and lv.frm <= d <= lv.to:
            return True
    return False


def plan_days_off(inp: RosterInputs, force_off: dict[str, set[str]] | None = None, history=None) -> tuple[dict[str, DayOff], list[Issue]]:
    """Plan the day-off block of every active employee (port of ``PlanDaysOff``).

    ``force_off`` maps a normalised employee key to weekday names that must lie inside that
    employee's block (the ``forceOff`` array filled by ``MarkIdle`` after a first solve).

    Returns ``(blocks, issues)``: ``blocks`` maps the normalised employee key to their
    :class:`DayOff`, ``issues`` lists the input problems that could not be resolved.

    ``history`` (optional :class:`~roster_builder.history.MonthHistory` of the previous month) keeps
    at least 5 working days between the last day off of the previous month and the first block of
    this month: windows that would break it are dropped from the candidates (reported as an Issue
    when no window is left). Without history (``None``) nothing changes.
    """
    issues: list[Issue] = []
    settings = inp.settings
    names = list(settings.weekday_names)

    rec_names: list[str] = []
    for e in inp.employees:
        if not e.name.strip() or not e.active:
            continue
        if e.home and e.home not in rec_names:
            rec_names.append(e.home)
    rec_idx = {r: i for i, r in enumerate(rec_names)}
    nR = len(rec_names)

    avail = [[0.0] * 7 for _ in range(nR)]
    availM = [[0.0] * 7 for _ in range(nR)]
    need = [[0.0] * 7 for _ in range(nR)]
    needMl = [[0.0] * 7 for _ in range(nR)]
    # R9.1: availability is per weekday and accounts for leave over the whole month – the
    # share of the month's days with that weekday on which the person is not on leave.
    share_of: dict[str, list[float]] = {}
    for e in inp.employees:
        if not e.name.strip() or not e.active:
            continue
        shares = [0.0] * 7
        share_of[e.key] = shares
        j = rec_idx.get(e.home) if e.home else None
        if j is None:
            continue
        for x in range(7):
            vidx = x + 1
            total = _weekday_count(inp, vidx)
            free = _free_days(inp, e, vidx)
            if total > 0 and free > 0:
                share = free / total
                shares[x] = share
                avail[j][x] += share
                if e.is_male(settings.male_code):
                    availM[j][x] += share
    for j, rn in enumerate(rec_names):
        for x in range(7):
            need[j][x] = _day_need(inp, rn, names[x], False)
            needMl[j][x] = _day_need(inp, rn, names[x], True)

    valid = [e for e in inp.employees if e.name.strip() and e.active]
    n = len(valid)
    kk = [block_length(inp, e) for e in valid]
    orig_kk = list(kk)
    reqs = [required_days(inp, e) | {d.strip().upper() for d in (force_off or {}).get(e.key, set())} for e in valid]
    done = [False] * n
    starts = [0] * n
    extended = [False] * n
    hist_warns: dict[int, str] = {}

    for i in range(n):
        if reqs[i]:
            run = _shortest_run(names, reqs[i])
            if run is not None and len(run) > kk[i]:
                extended[i] = True
                kk[i] = len(run)

    for _ in range(n):
        pick = -1
        minC = 99
        for i in range(n):
            if not done[i] and kk[i] > 0:
                c = sum(1 for s in range(7) if _holds(names, s, kk[i], reqs[i]))
                if c < minC:
                    minC = c
                    pick = i
        if pick < 0:
            break
        i = pick
        home_j = rec_idx.get(valid[i].home) if valid[i].home else None
        sh_i = share_of[valid[i].key]
        cands = [s for s in range(7) if _holds(names, s, kk[i], reqs[i])]
        if not cands:
            cands = list(range(7))
        hist_warn = ""
        if history is not None and settings.first_day is not None and history.is_previous_of(settings.first_day):
            lo = history.last_off_date(valid[i].key)
            ok_c = [s for s in cands if spacing_ok(settings.first_day, {names[(s + o) % 7] for o in range(kk[i])}, lo, tuple(names),
                                run_before=history.tail_run(valid[i].key), max_run=kk[i])]
            if ok_c:
                cands = ok_c
            elif lo is not None:
                hist_warn = (f"{valid[i].name}: no block window keeps {MIN_WORK_BETWEEN} working days after the last day off of the "
                             f"previous month ({lo.isoformat()}); change the fixed day off or 'No weekends'.")
        best_s = cands[0]
        best_sc: float | None = None
        for s in cands:
            mn = math.inf
            sc_sum = 0.0
            for o in range(kk[i]):
                x = (s + o) % 7
                h = (avail[home_j][x] - sh_i[x]) - need[home_j][x] if home_j is not None else 0.0
                if valid[i].is_male(settings.male_code):
                    hm = (availM[home_j][x] - sh_i[x]) - needMl[home_j][x] if home_j is not None else 0.0
                    if hm < h:
                        h = hm
                mn = min(mn, h)
                sc_sum += h
            if home_j is None:
                mn = 0.0
            sc = mn * 1000 + sc_sum - 0.01 * ((s + i) % 7)
            if best_sc is None or sc > best_sc:
                best_sc = sc
                best_s = s
        starts[i] = best_s
        done[i] = True
        if hist_warn:
            hist_warns[i] = hist_warn
        for o in range(kk[i]):
            x = (best_s + o) % 7
            if home_j is not None:
                avail[home_j][x] -= sh_i[x]
                if valid[i].is_male(settings.male_code):
                    availM[home_j][x] -= sh_i[x]

    blocks: dict[str, DayOff] = {}
    for i, e in enumerate(valid):
        if not e.name.strip() or not e.active:
            continue
        if kk[i] <= 0:
            issues.append(Issue(id=f"DAYSOFF|G{e.row},Q{e.row}", cell=f"G{e.row},Q{e.row}", msg=f"{e.name}: no day-off block could be planned.", fix=""))
            continue
        block_days = [names[(starts[i] + o) % 7] for o in range(kk[i])]
        warns: list[str] = []
        if extended[i]:
            days_txt = f"{block_days[0]}\u2013{block_days[-1]}"
            warn_msg = (f"{e.name}: days off {days_txt} = {len(block_days)} days in a row "
                        f"(more than {orig_kk[i]}); only {7 - len(block_days)} working days between blocks, "
                        f"so the 5-working-day rule can't be met. Change the fixed day off or 'No weekends'.")
            warns.append(warn_msg)
            issues.append(Issue(id=f"DAYSOFF|G{e.row},P{e.row},Q{e.row}", cell=f"G{e.row},P{e.row},Q{e.row}",
                                msg=warn_msg, fix=""))
        if i in hist_warns:
            warns.append(hist_warns[i])
            issues.append(Issue(id=f"HISTORY|SPACING|{e.row}", cell=f"G{e.row},P{e.row},Q{e.row}", msg=hist_warns[i], fix=""))
        blocks[e.key] = DayOff(emp=e.key, block_days=block_days, length=kk[i], warnings=warns)
    localize_issues(issues, inp)
    return blocks, issues


def coverage_after_blocks(inp: RosterInputs, blocks: dict[str, DayOff]) -> list[tuple[str, str, float, float]]:
    """Per home reception and weekday: (reception, weekday name, available staff, people needed).

    R9.2: rows are built from every reception x weekday, so a day on which nobody is
    available still shows up with 0 available instead of being missing from the output.
    """
    out: list[tuple[str, str, float, float]] = []
    names = list(inp.settings.weekday_names)
    recs: list[str] = []
    for e in inp.employees:
        if not e.name.strip() or not e.active:
            continue
        if e.home and e.home not in recs:
            recs.append(e.home)
    for rn in recs:
        for x in range(7):
            dn = names[x]
            av = 0.0
            for e in inp.employees:
                if not e.name.strip() or not e.active or (e.home or "") != rn:
                    continue
                b = blocks.get(e.key)
                if b is not None and dn in b.block_days:
                    continue
                total = _weekday_count(inp, x + 1)
                if total > 0:
                    av += _free_days(inp, e, x + 1) / total
            out.append((rn, dn, av, _day_need(inp, rn, dn, False)))
    return out
