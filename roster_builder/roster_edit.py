"""Manual edits of a built roster (milestone 12) - no UI code.

* The stored roster of a month always holds the EDITED roster (built roster + edits), so reopening the app,
  exporting and the history all see the user's changes.
* A **pinned** (locked) edit is also listed in ``store.get_locked``: ``{"emp": uid, "date": iso, "shift": uid|None,
  "rec": uid|None, "ot": bool, "eb": int, "ea": int}``.  ``shift`` ``None`` means the person is OFF that day.
  A rebuild feeds the pins into the solver as fixed shifts (``solver.solve`` via ``inputs.locked``).
* ``origin`` (inside the stored roster) remembers the built value of every edited cell, so "Revert to built
  value" works even after "Unlock".
* After every edit the month's history record is recomputed, so next month starts from the edited last days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from . import history as hist
from .checks import CheckReport, run_checks
from .inputs_from_store import build_inputs, roster_from_dict
from .model import Assignment, LockedCell, Roster


def _up(s) -> str:
    return (s or "").strip().upper()


def lock_dict(inp, emp_uid: int, d: date, shift_code: str | None, rec: str | None = None, ot: bool = False,
              eb: int = 0, ea: int = 0) -> dict:
    """Store form of one pinned cell (shift / reception by uid)."""
    sh = inp.shift_by_key.get(_up(shift_code)) if shift_code else None
    rc = next((r for r in inp.receptions if _up(r.name) == _up(rec)), None) if rec else None
    return {"emp": emp_uid, "date": d.isoformat(), "shift": sh.uid if sh else None, "rec": rc.uid if rc else None,
            "ot": bool(ot), "eb": int(eb), "ea": int(ea)}


def locked_cells(store, inp, ym) -> list[LockedCell]:
    """The pinned edits of month ``ym`` as engine objects (unknown employees / shifts / other months are skipped)."""
    by_e = {e.uid: e for e in inp.employees}
    by_s = {s.uid: s for s in inp.shifts}
    by_r = {r.uid: r for r in inp.receptions}
    first = inp.settings.first_day
    last = first + timedelta(days=inp.settings.days_in_month - 1)
    out: list[LockedCell] = []
    for d in store.get_locked(ym):
        e = by_e.get(d.get("emp"))
        try:
            day = date.fromisoformat(d["date"])
        except (KeyError, ValueError):
            continue
        if e is None or not (first <= day <= last):
            continue
        sh = by_s.get(d.get("shift")) if d.get("shift") is not None else None
        if d.get("shift") is not None and sh is None:
            continue
        rc = by_r.get(d.get("rec")) if d.get("rec") is not None else None
        out.append(LockedCell(emp=e.name, date=day, shift=sh.code if sh else "", rec=rc.name if rc else (e.home if sh else ""),
                              ot=bool(d.get("ot")), ext_before_h=int(d.get("eb", 0)), ext_after_h=int(d.get("ea", 0)),
                              emp_uid=e.uid))
    return out


def apply_locks(inp, roster: Roster, locks: list[LockedCell]) -> None:
    """Put the pinned cells into ``roster`` (in place; idempotent).  Pinned OFF cells are remembered in
    ``roster.off_locked`` so the schedule shows "OFF" there."""
    off = set(getattr(roster, "off_locked", ()) or ())
    for lk in locks:
        key = _up(lk.emp)
        roster.assignments = [a for a in roster.assignments if not (a.emp_key == key and a.date == lk.date)]
        off.discard((key, lk.date))
        e = inp.emp_by_key.get(key)
        if (lk.shift or "").strip():
            roster.assignments.append(Assignment(
                emp_key=key, date=lk.date, shift_code=lk.shift, rec=lk.rec or (e.home if e else ""), is_ot=bool(lk.ot),
                is_helper=bool(e and _up(lk.rec or e.home) != _up(e.home)), ext_before_h=lk.ext_before_h, ext_after_h=lk.ext_after_h))
        else:
            off.add((key, lk.date))
    if locks:
        roster.assignments.sort(key=lambda a: (a.date, a.emp_key))
    roster.off_locked = off  # type: ignore[attr-defined]


@dataclass
class CellState:
    """The value of one roster cell: ``asg`` (``None`` = no duty) and its pin (``None`` = not pinned)."""

    asg: Assignment | None = None
    lock: dict | None = None
    off: bool = False  # pinned OFF (a lock with shift None)


class RosterEditor:
    """The edited roster of one built month: load, edit cells, pin / unpin / revert, persist, re-check."""

    def __init__(self, store, ym) -> None:
        self.store = store
        self.ym = (int(ym[0]), int(ym[1]))
        self.inp = build_inputs(store, *self.ym)
        d = store.get_roster(self.ym)
        self.has_roster = bool(d)
        self.roster = roster_from_dict(self.inp, d) if d else Roster()
        self.origin: dict = dict((d or {}).get("origin") or {})
        self.locks: dict[tuple[int, str], dict] = {}
        known = {e.uid for e in self.inp.employees}
        for lk in store.get_locked(self.ym):
            if lk.get("emp") in known:
                self.locks[(lk["emp"], lk["date"])] = lk
        if self.has_roster:
            apply_locks(self.inp, self.roster, locked_cells(store, self.inp, self.ym))
        self._index()

    # ------------------------------------------------------------------ lookups
    def _index(self) -> None:
        self._asg = {(a.emp_key, a.date): a for a in self.roster.assignments}

    def emp_uid(self, emp_key: str) -> int:
        return self.inp.emp_by_key[_up(emp_key)].uid

    def is_locked(self, emp_key: str, d: date) -> bool:
        return (self.emp_uid(emp_key), d.isoformat()) in self.locks

    def assignment(self, emp_key: str, d: date) -> Assignment | None:
        return self._asg.get((_up(emp_key), d))

    def state(self, emp_key: str, d: date) -> CellState:
        key = _up(emp_key)
        lk = self.locks.get((self.emp_uid(key), d.isoformat()))
        a = self._asg.get((key, d))
        return CellState(asg=Assignment(**vars(a)) if a else None, lock=dict(lk) if lk else None,
                         off=bool(lk and lk.get("shift") is None))

    def can_revert(self, emp_key: str, d: date) -> bool:
        return f"{self.emp_uid(emp_key)}|{d.isoformat()}" in self.origin

    def locked_list(self) -> list[dict]:
        return sorted(self.locks.values(), key=lambda x: (x["date"], x["emp"]))

    # ------------------------------------------------------------------ changes
    def _asg_dict(self, a: Assignment | None):
        if a is None:
            return None
        sh = self.inp.shift_by_key.get(_up(a.shift_code))
        rc = next((r for r in self.inp.receptions if _up(r.name) == _up(a.rec)), None)
        return {"emp": self.emp_uid(a.emp_key), "date": a.date.isoformat(), "shift": sh.uid if sh else None,
                "rec": rc.uid if rc else None, "ot": a.is_ot, "helper": a.is_helper, "eb": a.ext_before_h, "ea": a.ext_after_h}

    def set_state(self, emp_key: str, d: date, st: CellState, persist: bool = True) -> None:
        """Make the cell look like ``st`` (used for edits, undo and redo)."""
        key = _up(emp_key)
        uid = self.emp_uid(key)
        okey = f"{uid}|{d.isoformat()}"
        if okey not in self.origin:  # first change since the build: remember the built value
            self.origin[okey] = self._asg_dict(self._asg.get((key, d)))
        self.roster.assignments = [a for a in self.roster.assignments if not (a.emp_key == key and a.date == d)]
        off = set(getattr(self.roster, "off_locked", ()) or ())
        off.discard((key, d))
        if st.asg is not None:
            self.roster.assignments.append(Assignment(**vars(st.asg)))
            self.roster.assignments.sort(key=lambda a: (a.date, a.emp_key))
        if st.lock is not None:
            self.locks[(uid, d.isoformat())] = dict(st.lock)
            if st.lock.get("shift") is None:
                off.add((key, d))
        else:
            self.locks.pop((uid, d.isoformat()), None)
        self.roster.off_locked = off  # type: ignore[attr-defined]
        self._index()
        if persist:
            self.persist()

    def make_state(self, emp_key: str, d: date, shift_code: str | None, rec: str | None = None, ot: bool = False,
                   eb: int = 0, ea: int = 0, pin: bool = True) -> CellState:
        """State for "this person works ``shift_code`` (or is OFF when ``None``)"; pinned by default."""
        key = _up(emp_key)
        e = self.inp.emp_by_key[key]
        uid = e.uid
        if shift_code is None:
            return CellState(None, lock_dict(self.inp, uid, d, None) if pin else None, True)
        sh = self.inp.shift_by_key[_up(shift_code)]
        rec = rec or e.home
        a = Assignment(emp_key=key, date=d, shift_code=sh.code, rec=rec, is_ot=bool(ot), is_helper=_up(rec) != _up(e.home),
                       ext_before_h=int(eb), ext_after_h=int(ea))
        return CellState(a, lock_dict(self.inp, uid, d, sh.code, rec, ot, eb, ea) if pin else None)

    def edit(self, emp_key: str, d: date, shift_code: str | None, rec: str | None = None, ot: bool = False,
             eb: int = 0, ea: int = 0) -> tuple[CellState, CellState]:
        """Edit and pin a cell.  Returns ``(before, after)`` (for undo)."""
        before = self.state(emp_key, d)
        after = self.make_state(emp_key, d, shift_code, rec, ot, eb, ea)
        self.set_state(emp_key, d, after)
        return before, after

    def unlock(self, emp_key: str, d: date) -> tuple[CellState, CellState]:
        """Remove the pin; the value stays until the next rebuild."""
        before = self.state(emp_key, d)
        after = CellState(before.asg, None, False)
        self.set_state(emp_key, d, after)
        return before, after

    def revert_state(self, emp_key: str, d: date) -> CellState:
        """The cell as the build made it (not pinned)."""
        o = self.origin.get(f"{self.emp_uid(emp_key)}|{d.isoformat()}")
        a = None
        if o:
            sh = next((s for s in self.inp.shifts if s.uid == o.get("shift")), None)
            rc = next((r for r in self.inp.receptions if r.uid == o.get("rec")), None)
            e = self.inp.emp_by_key[_up(emp_key)]
            if sh is not None:
                a = Assignment(emp_key=_up(emp_key), date=d, shift_code=sh.code, rec=rc.name if rc else e.home,
                               is_ot=bool(o.get("ot")), is_helper=bool(o.get("helper")), ext_before_h=int(o.get("eb", 0)),
                               ext_after_h=int(o.get("ea", 0)))
        return CellState(a, None, False)

    def revert(self, emp_key: str, d: date) -> tuple[CellState, CellState]:
        """Put the built value back and remove the pin."""
        before = self.state(emp_key, d)
        after = self.revert_state(emp_key, d)
        self.set_state(emp_key, d, after, persist=False)
        self.origin.pop(f"{self.emp_uid(emp_key)}|{d.isoformat()}", None)
        self.persist()
        return before, after

    # ------------------------------------------------------------------ save / check
    def persist(self) -> None:
        """Save the edited roster, the pins and the recomputed history (one auto-save step)."""
        from .inputs_from_store import roster_to_dict

        d = self.store.get_roster(self.ym) or {}
        d["assignments"] = roster_to_dict(self.inp, self.roster)["assignments"]
        d["origin"] = self.origin
        self.store.update_roster(self.ym, d)
        self.store.set_locked(self.ym, self.locked_list())
        record = hist.build_record(self.inp, self.roster, _previous(self.store, self.ym, self.inp))
        self.store.save_history(self.ym, record.to_json())

    def check(self) -> CheckReport:
        """Run every rule check on the current (edited) roster."""
        return run_checks(self.inp, self.roster)


def _previous(store, ym, inp):
    from .app import previous_history

    return previous_history(store, ym[0], ym[1], inp)


# ----------------------------------------------------------------------------- problems of the edited roster
@dataclass
class Problem:
    """One broken rule, ready for the Problems panel: the check it belongs to, the sentence for the user,
    the roster cells it concerns ``[(employee key, date)]`` and the day (for coverage problems without a cell)."""

    check: str
    check_name: str
    text: str
    cells: list = None  # type: ignore[assignment]
    day: date | None = None
    rec: str = ""
    severity: str = "error"  # error (a rule is broken) / warning (manual exception) / info (manual choice)

    def __post_init__(self) -> None:
        if self.cells is None:
            self.cells = []


def _dt(d: date) -> str:
    return d.strftime("%a %d %b")


def problems_from_report(editor: RosterEditor, rep: CheckReport) -> list[Problem]:
    """Translate the check report of the edited roster into problems that point at roster cells."""
    inp = editor.inp
    st = inp.settings
    names = {e.name: e.key for e in inp.employees}
    first = st.first_day
    days = [first + timedelta(days=i) for i in range(st.days_in_month)]
    out: list[Problem] = []
    label = {r.key: r.name for r in rep.summary}
    label["manual_ot"] = "Overtime on a normal working day – manual"
    label["extra_off"] = "Extra day off (manual)"

    def add(key, text, cells=(), day=None, rec="", severity="error"):
        out.append(Problem(key, label.get(key, key), text, list(cells), day, rec, severity))

    def week_cells(name, wk, ot_only):
        k = names.get(name, _up(name))
        res = []
        for d in days:
            if st.wk_of(d) != wk:
                continue
            a = editor.assignment(k, d)
            if a is not None and (a.is_ot or a.ext_total_h or not ot_only):
                res.append((k, d))
        return res

    for r in rep.details.get("rest", []):
        name, d, s1, s2, gap, need = r[:6]
        k = names.get(name, _up(name))
        what = (f"{name} rests only {gap:g} h" if gap > 0 else f"{name} has no rest at all (the shifts overlap by {-gap:g} h)"
                ) if gap != 0 else f"{name} has no rest at all"
        add("rest", f"{what} between {s1} on {_dt(d)} and {s2} the next day (the minimum is {need:g} h).",
            [(k, d), (k, d + timedelta(days=1))])
    for r in rep.details.get("rest_split", []):
        name, d, s1, gap, need = r[:5]
        add("rest_split", f"{name}: the break inside {s1} on {_dt(d)} is only {gap:g} h (the minimum is {need:g} h).",
            [(names.get(name, _up(name)), d)])
    for r in rep.details.get("women_window", []):
        name, d, s1, hrs = r[:4]
        add("women_window", f"{name} works {s1} on {_dt(d)}, {hrs} of it in the men-only window. Only men may work there.",
            [(names.get(name, _up(name)), d)])
    for r in rep.details.get("weekly_normal", []):
        name, wk, h, lim = r[:4]
        add("weekly_normal", f"{name} works {h:g} normal hours in week {wk} (the limit is {lim:g} h).", week_cells(name, wk, False))
    for r in rep.details.get("ot_max", []):
        name, wk, h, mx = r[:4]
        add("ot_max", f"{name} has {h:g} overtime hours in week {wk} (the limit is {mx:g} h).", week_cells(name, wk, True))
    for r in rep.details.get("ot_permission", []):
        name, d, s1 = r[:3]
        add("ot_permission", f"{name} works overtime on {_dt(d)} but overtime is not allowed for this person.",
            [(names.get(name, _up(name)), d)])
    for r in rep.details.get("leave", []):
        name, d, s1 = r[:3]
        add("leave", f"{name} is on leave on {_dt(d)} but has the shift {s1}.", [(names.get(name, _up(name)), d)])
    for r in rep.details.get("fixed", []):
        name, d, s1, rec = r[:4]
        add("fixed", f"{name} should work the fixed shift {s1} on {_dt(d)}.", [(names.get(name, _up(name)), d)])
    for r in rep.details.get("idle", []):
        name, d, wd = r[:3]
        add("idle", f"{name} has no shift on {_dt(d)} (not on leave and not a planned day off).", [(names.get(name, _up(name)), d)])
    for r in rep.details.get("extra_off", []):
        add("extra_off", f"{r[0]} has an extra day off on {_dt(r[1])} (set by hand; not part of the planned days off).",
            [(names.get(r[0], _up(r[0])), r[1])], severity="info")
    for r in rep.details.get("manual_ot", []):
        add("manual_ot", f"{r[0]} works overtime on a normal working day, {_dt(r[1])} (set by hand; the planner would not do this).",
            [(names.get(r[0], _up(r[0])), r[1])], severity="warning")
    for r in rep.details.get("days_off", []):
        add("days_off", f"{r[0]}: {r[1]}.", [])
    for r in rep.details.get("ot_priority", []):
        add("ot_priority", f"Overtime of {r[0]} on {_dt(r[2])} could have gone to higher-priority staff.",
            [(names.get(r[0], _up(r[0])), r[2])])
    cov_text = {"empty": "nobody on duty", "no_male": "no man on duty", "below_min": "fewer people than the minimum",
                "above_max": "more people than the maximum"}
    for key, why in cov_text.items():
        grouped: dict[tuple, list[str]] = {}
        for r in rep.details.get(key, []):
            grouped.setdefault((r[0], r[1]), []).append(str(r[2]))
        for (rec, d), hrs in grouped.items():
            span = hrs[0] if len(hrs) == 1 else f"{hrs[0]} – {hrs[-1]} ({len(hrs)} h)"
            add(key, f"{rec} on {_dt(d)}: {why} at {span}.", [], d, rec)
    for r in rep.details.get("demand", []):
        add("demand", f"Demand not filled at {r[0]} on {_dt(r[1])}: {r[2]} needs {r[4]}, {r[5]} placed.", [], r[1], r[0])
    return out


def coverage_strip(editor: RosterEditor, rep: CheckReport) -> dict:
    """Hours covered vs needed per reception and day: ``{rec: {date: (covered, needed, [uncovered "HH:00"])}}``."""
    st = editor.inp.settings
    first = st.first_day
    days = [first + timedelta(days=i) for i in range(st.days_in_month)]
    out: dict = {}
    for rec, per_day in rep.headcount.items():
        row = {}
        for d in days:
            need = 0
            miss = []
            for h in range(24):
                lo, _hi = rep.limits.get(rec, {}).get(d, [(0.0, -1.0)] * 24)[h]
                if lo >= 1:
                    need += 1
                    if per_day[d][h] < 1 and not (d == days[0] and h < st.carry_in):
                        miss.append(f"{h:02d}:00")
            row[d] = (need - len(miss), need, miss)
        out[rec] = row
    return out
