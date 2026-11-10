"""Build the engine's :class:`~roster_builder.io_xlsx.RosterInputs` from a stored month.

The result is the *same* Inputs type the Excel reader produces (a test proves that the fixture gives
equal Inputs through both routes), so ``plan_days_off`` / ``solve`` / ``run_checks`` / ``report`` need no
change.  Differences by design:

* ``source == "store"`` and every object carries its store ``uid``;
* every record gets a synthetic ``row`` in the old workbook layout (:func:`locate.synthetic_row`) plus a
  :class:`~roster_builder.locate.RowIndex`, so engine messages can be mapped to app fields;
* ``settings.month_start`` is the 1st of the month.

Also here: (de)serialisation of a built roster for the store.
"""

from __future__ import annotations

import calendar
from datetime import date

from .daysoff import DayOff
from .io_xlsx import RosterInputs
from .locate import RowIndex, synthetic_row
from .model import (
    Assignment,
    CoverageTarget,
    Demand,
    Employee,
    FixedShift,
    Leave,
    Reception,
    Roster,
    Settings,
    Shift,
    WEEKDAY_NAMES,
    hour_txt,
    male_overlap,
)
from .store import Store, month_key


def _up(s) -> str:
    return (s or "").strip().upper()


def build_inputs(store: Store, year: int, month: int) -> RosterInputs:
    """Engine inputs of the stored month ``year``-``month`` (KeyError-free: ``NotFoundError`` if missing)."""
    sc = store.month(year, month)
    s = sc.settings()
    first = date(year, month, 1)
    inp = RosterInputs()
    inp.source = "store"
    inp.row_index = idx = RowIndex()
    inp.settings = Settings(
        month_start=first, first_day=first, days_in_month=calendar.monthrange(year, month)[1],
        max_week_h=s.max_week_h, min_rest=s.min_rest, male_from=s.male_from, male_to=s.male_to,
        days_off_wk=int(s.days_off_wk), max_shift_h=s.max_shift_h, min_rest_split=s.min_rest_split,
        carry_in=s.carry_in, ot_on=bool(s.ot_on), ot_max=s.ot_max, ot_keep=bool(s.ot_keep),
        weekend_days=_up(s.weekend_days) or "FRI,SAT", strict_on=bool(s.strict_on), ext_max=s.ext_max,
        week_start_name=_up(s.week_start_name) or "SAT", fri_name=_up(s.fri_name) or "FRI",
        male_code="M", female_code="F", weekday_names=WEEKDAY_NAMES)

    recs = sc.list_receptions()
    rec_name = {r.uid: _up(r.name) for r in recs}
    inp.receptions = [Reception(name=_up(r.name), uid=r.uid) for r in recs]

    shifts = sc.list_shifts()
    shift_code: dict[int, str] = {}
    for i, sh in enumerate(shifts):
        row = synthetic_row("shift", i)
        obj = Shift(code=sh.code, start1=sh.start1, end1=sh.end1,
                    start2=-1.0 if sh.start2 is None else sh.start2, end2=-1.0 if sh.end2 is None else sh.end2,
                    paid=sh.paid, ext=0.0, male=0.0, row=row, uid=sh.uid)
        obj.male = male_overlap(obj, inp.settings.male_from, inp.settings.male_to)
        inp.shifts.append(obj)
        shift_code[sh.uid] = obj.code
        idx.add("shift", row, sh.uid, obj.code)
    inp.shift_by_key = {x.code.strip().upper(): x for x in inp.shifts}

    for i, t in enumerate(sc.list_targets()):
        row = synthetic_row("target", i)
        inp.targets.append(CoverageTarget(
            rec=rec_name.get(t.rec_uid, ""), frm=t.frm_h, to=t.to_h,
            min_staff=-1.0 if t.min_staff is None else t.min_staff,
            min_male=-1.0 if t.min_male is None else t.min_male,
            max_staff=-1.0 if t.max_staff is None else t.max_staff, days=t.days or "", row=row, uid=t.uid))
        idx.add("target", row, t.uid, f"{rec_name.get(t.rec_uid, '?')}, {hour_txt(t.frm_h)} - {hour_txt(t.to_h)}, {t.days or 'ALL'}")

    # R18.2: identical coverage labels of one reception get "(row n)" = position within that reception
    labels = {r: idx.rows["target"][r][1] for r in idx.rows["target"]}
    pos: dict[str, int] = {}
    where_rec: dict[int, int] = {}
    for t in inp.targets:
        pos[t.rec] = pos.get(t.rec, 0) + 1
        where_rec[t.row] = pos[t.rec]
    seen = [lab for lab in labels.values()]
    for row, lab in labels.items():
        if seen.count(lab) > 1:
            u = idx.rows["target"][row][0]
            idx.rows["target"][row] = (u, f"{lab} (row {where_rec[row]})")

    emp_name: dict[int, str] = {}
    for i, e in enumerate(sc.list_employees()):
        row = synthetic_row("employee", i)
        fo, fo2 = _up(e.fixed_off), _up(e.fixed_off2)
        if fo == "AUTO":
            fo = ""
        if fo2 == "AUTO" or fo2 == fo:
            fo2 = ""
        if not fo and fo2:
            fo, fo2 = fo2, ""
        inp.employees.append(Employee(
            name=e.name, gender=_up(e.gender), home=rec_name.get(e.home_uid, ""),
            also=rec_name.get(e.also_uid, "") if e.also_uid is not None else "", active=bool(e.active),
            fixed_off=fo, fri=bool(e.fri), allowed=[shift_code[u] for u in e.allowed if u in shift_code],
            pref=[shift_code[u] for u in e.pref if u in shift_code],
            max_h=e.max_h if e.max_h is not None else inp.settings.max_week_h, ot=bool(e.ot), no_we=bool(e.no_we),
            fixed_off2=fo2, notes=e.notes or "", row=row, uid=e.uid,
            ot_rank=e.ot_rank if (e.ot and e.ot_rank is not None) else None))
        emp_name[e.uid] = e.name
        idx.add("employee", row, e.uid, e.name)
    inp.emp_by_key = {e.key: e for e in inp.employees}

    for i, lv in enumerate(sc.list_leave()):
        row = synthetic_row("leave", i)
        name = emp_name.get(lv.emp_uid)
        if name is None:
            continue
        inp.leave.append(Leave(emp=_up(name), frm=lv.from_date, to=lv.to_date, kind=_up(lv.kind) or "LEAVE", row=row, uid=lv.uid))
        idx.add("leave", row, lv.uid, f"{name}, {lv.from_date:%d %b %Y} - {lv.to_date:%d %b %Y}")

    for i, d in enumerate(sc.list_demand()):
        row = synthetic_row("demand", i)
        if not d.active or d.shift_uid not in shift_code:
            continue
        counts = [int(c) for c in d.counts]
        inp.demand.append(Demand(rec=rec_name.get(d.rec_uid, ""), shift=shift_code[d.shift_uid], gender=_up(d.gender) or "ANY",
                                 fill=_up(d.fill) or "HOME", prio=d.prio, counts=counts, all_week=any(c < 0 for c in counts),
                                 row=row, uid=d.uid))
        idx.add("demand", row, d.uid, f"{rec_name.get(d.rec_uid, '?')}, {shift_code[d.shift_uid]}")

    for i, f in enumerate(sc.list_fixed()):
        row = synthetic_row("fixed", i)
        name = emp_name.get(f.emp_uid)
        if name is None or f.shift_uid not in shift_code:
            continue
        inp.fixed.append(FixedShift(emp=_up(name), date=f.day, shift=shift_code[f.shift_uid],
                                    rec=rec_name.get(f.rec_uid, "") if f.rec_uid is not None else "", row=row, uid=f.uid))
        idx.add("fixed", row, f.uid, f"{name}, {f.day:%d %b}")
    return inp


# ----------------------------------------------------------------------------- built roster <-> JSON
def roster_to_dict(inp: RosterInputs, roster: Roster) -> dict:
    """JSON-able form of a roster; employees, shifts and receptions are stored by uid."""
    rec_uid = {_up(r.name): r.uid for r in inp.receptions}
    out = {
        "status": roster.status, "passes": roster.passes, "objective": roster.objective,
        "assignments": [
            {"emp": inp.emp_by_key[a.emp_key].uid, "date": a.date.isoformat(), "shift": inp.shift_by_key[_up(a.shift_code)].uid,
             "rec": rec_uid.get(_up(a.rec)), "ot": a.is_ot, "helper": a.is_helper, "eb": a.ext_before_h, "ea": a.ext_after_h}
            for a in roster.assignments if a.emp_key in inp.emp_by_key and _up(a.shift_code) in inp.shift_by_key],
        "blocks": {str(inp.emp_by_key[k].uid): {"days": list(b.block_days), "length": b.length, "warnings": list(b.warnings)}
                   for k, b in (roster.blocks or {}).items() if k in inp.emp_by_key},
        "issues": [{"msg": i.msg, "fix": i.fix, "id": i.id, "text": i.text_for_user(),
                    "where": [[w.screen, w.record_id, w.field, w.label] for w in i.where]} for i in roster.issues],
    }
    return out


def roster_from_dict(inp: RosterInputs, d: dict) -> Roster:
    """Rebuild a :class:`Roster` for ``inp`` (the same month's inputs) from :func:`roster_to_dict` output."""
    by_uid_e = {e.uid: e for e in inp.employees}
    by_uid_s = {s.uid: s for s in inp.shifts}
    by_uid_r = {r.uid: r for r in inp.receptions}
    r = Roster(status=d.get("status", ""), passes=int(d.get("passes", 1)), objective=dict(d.get("objective") or {}))
    for a in d.get("assignments", []):
        e, s = by_uid_e.get(a["emp"]), by_uid_s.get(a["shift"])
        rec = by_uid_r.get(a["rec"]) if a.get("rec") is not None else None
        if e is None or s is None:
            continue
        r.assignments.append(Assignment(emp_key=e.key, date=date.fromisoformat(a["date"]), shift_code=s.code,
                                        rec=rec.name if rec else e.home, is_ot=bool(a["ot"]), is_helper=bool(a["helper"]),
                                        ext_before_h=int(a.get("eb", 0)), ext_after_h=int(a.get("ea", 0))))
    for uid, b in (d.get("blocks") or {}).items():
        e = by_uid_e.get(int(uid))
        if e is not None:
            r.blocks[e.key] = DayOff(emp=e.name, block_days=list(b["days"]), length=int(b["length"]), warnings=list(b.get("warnings", [])))
    return r


def previous_month_key(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


__all__ = ["build_inputs", "roster_to_dict", "roster_from_dict", "previous_month_key", "month_key"]
