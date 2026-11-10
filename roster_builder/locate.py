"""Field locators for issues (BUILD_GUIDE section 5/6) and the text for the user.

The engine (validate / solver / daysoff / checks) was written against the workbook layout and still
produces Excel cell strings (``Issue.cell``, e.g. ``"O111"`` or ``"D24:J24"``).  For inputs that come
from the data store every record gets a *synthetic row* in the old layout (:func:`synthetic_row`) and a
:class:`RowIndex` remembers which record sits on which row.  :func:`localize_issues` then

* translates each cell string into :class:`Locator` objects (screen, record id, field, label), e.g.
  ``Employees > FAHAD > Overtime allowed``;
* turns the machine-readable auto-fix (``"H110=N"``) into :class:`FieldSet` objects;
* rewrites ``msg`` / ``fix`` / ``id`` so that no Excel cell reference is left (:func:`sanitize`).

``Locator.field`` is the attribute name of the record in :mod:`roster_builder.store`, so the UI can
bind straight to it.  This module imports nothing from the rest of the package (``model`` imports it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

SEP = " › "  # " > " with a proper chevron


@dataclass(frozen=True)
class Locator:
    """Where a problem sits in the app: ``screen`` / record / ``field`` plus readable ``label``.

    ``record_id`` is the store uid of the record (``None`` for the Settings screen or a whole table);
    ``field`` is the store attribute name (``""`` = the whole record).
    """

    screen: str
    record_id: int | None
    field: str
    label: str

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class FieldSet:
    """A safe auto-fix: set ``field`` of the record to ``value`` (``op`` = ``"set"``, or ``"add"``
    for list fields such as the allowed shifts; ``value`` is a shift *code* then)."""

    locator: Locator
    value: object
    op: str = "set"


# ------------------------------------------------------------------------------ labels
SCREEN_LABEL = {
    "settings": "Settings", "receptions": "Receptions", "coverage": "Coverage", "shifts": "Shifts",
    "employees": "Employees", "leave": "Leave", "fixed_shifts": "Fixed shifts", "demand": "Demand",
    "roster": "Roster",
}

FIELD_LABEL: dict[str, dict[str, str]] = {
    "settings": {
        "month": "Month", "week_start_name": "Week starts on", "fri_name": "Friday day name",
        "max_week_h": "Max normal hours per week", "max_shift_h": "Max hours per shift",
        "min_rest": "Min rest between shifts", "min_rest_split": "Min rest inside a split shift",
        "male_from": "Men-only window from", "male_to": "Men-only window until", "days_off_wk": "Days off per week",
        "carry_in": "Hours of day 1 already covered by last month", "weekend_days": "Weekend days",
        "ext_max": "Max extension hours", "ot_on": "Overtime allowed", "ot_max": "Max overtime hours per week",
        "ot_keep": "Keep 1 free day per week", "strict_on": "Strict rule",
    },
    "receptions": {"name": "Name"},
    "coverage": {"rec_uid": "Reception", "frm_h": "From hour", "to_h": "To hour", "min_staff": "Min staff",
                 "min_male": "Min men", "max_staff": "Max staff", "days": "Days"},
    "shifts": {"start1": "Start", "end1": "End", "start2": "Second part start", "end2": "Second part end",
               "paid": "Paid hours"},
    "employees": {"name": "Name", "gender": "Gender", "home_uid": "Home reception", "also_uid": "Can also cover",
                  "active": "Active", "fixed_off": "Fixed day off", "fixed_off2": "Fixed day off 2",
                  "fri": "Can work Fridays", "allowed": "Allowed shifts", "pref": "Preferred shifts",
                  "max_h": "Max hours per week", "ot": "Overtime allowed", "ot_rank": "OT priority", "no_we": "No weekends", "notes": "Notes"},
    "leave": {"emp_uid": "Employee", "from_date": "From", "to_date": "To", "kind": "Type"},
    "fixed_shifts": {"emp_uid": "Employee", "day": "Date", "shift_uid": "Shift", "rec_uid": "Reception"},
    "demand": {"active": "Active", "rec_uid": "Reception", "shift_uid": "Shift", "gender": "Gender",
               "fill": "Fill mode", "prio": "Priority", "counts": "Count per weekday", "note": "Note"},
}

# ------------------------------------------------------------------------------ old layout (importer / engine)
#: (column, row) of the Settings cells -> settings field
SETTINGS_CELLS: dict[tuple[str, int], str] = {
    ("C", 6): "month", ("C", 7): "week_start_name", ("C", 8): "fri_name", ("C", 9): "max_week_h",
    ("C", 10): "max_shift_h", ("C", 11): "min_rest", ("C", 12): "min_rest_split", ("C", 13): "male_from",
    ("C", 14): "male_to", ("C", 15): "days_off_wk", ("C", 16): "carry_in", ("C", 17): "weekend_days",
    ("C", 18): "ext_max", ("G", 14): "ot_on", ("G", 15): "ot_max", ("G", 16): "ot_keep", ("G", 17): "strict_on",
}

#: section -> (screen, first row, last row, base row of the synthetic numbering, column -> field)
SECTIONS: dict[str, tuple[str, int, int, dict[str, str]]] = {
    "target": ("coverage", 21, 60, {"D": "rec_uid", "E": "frm_h", "F": "to_h", "G": "min_staff", "H": "min_male",
                                    "I": "max_staff", "J": "days"}),
    "shift": ("shifts", 65, 104, {"B": "start1", "C": "start1", "D": "end1", "E": "start2", "F": "end2", "G": "paid"}),
    "employee": ("employees", 109, 168, {"B": "name", "C": "gender", "D": "home_uid", "E": "also_uid", "F": "active",
                                          "G": "fixed_off", "H": "fri", "I": "allowed", "J": "allowed", "K": "allowed",
                                          "L": "pref", "M": "pref", "N": "max_h", "O": "ot", "P": "no_we",
                                          "Q": "fixed_off2", "R": "notes"}),
    "leave": ("leave", 173, 272, {"B": "emp_uid", "C": "from_date", "D": "to_date", "E": "kind"}),
    "demand": ("demand", 277, 336, {"B": "active", "C": "rec_uid", "D": "shift_uid", "E": "gender", "F": "fill",
                                    "G": "prio", "H": "counts", "I": "counts", "J": "counts", "K": "counts",
                                    "L": "counts", "M": "counts", "N": "counts", "O": "note"}),
    "fixed": ("fixed_shifts", 341, 400, {"B": "emp_uid", "C": "day", "D": "shift_uid", "E": "rec_uid"}),
}
_OVERFLOW = {"target": 10000, "shift": 20000, "employee": 30000, "leave": 40000, "demand": 50000, "fixed": 60000}
_OVERFLOW_SECTION = {v // 10000: k for k, v in _OVERFLOW.items()}

#: employee table column letter -> label (for "column O" in old message texts)
_EMP_COL_LABEL = {c: FIELD_LABEL["employees"][f] for c, f in SECTIONS["employee"][3].items()}
_TARGET_COL_LABEL = {c: FIELD_LABEL["coverage"][f] for c, f in SECTIONS["target"][3].items()}


def synthetic_row(section: str, index: int) -> int:
    """Row (old workbook layout) given to the ``index``-th (0-based) record of ``section``.

    Rows follow the INPUT sheet layout while the table still fits (so the engine's fixed ranges such as
    ``C109:C168`` keep working) and continue in a private range afterwards."""
    _, lo, hi, _ = SECTIONS[section]
    return lo + index if lo + index <= hi else _OVERFLOW[section] + index


def section_of_row(row: int) -> str | None:
    """Section name of a (synthetic) row, ``"settings"`` for rows 6-18, ``None`` if unknown."""
    if row >= 10000:
        return _OVERFLOW_SECTION.get(row // 10000)
    if 6 <= row <= 18:
        return "settings"
    for name, (_, lo, hi, _) in SECTIONS.items():
        if lo <= row <= hi:
            return name
    return None


class RowIndex:
    """Synthetic row -> (uid, label) of the store record, per section."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[int, tuple[int, str]]] = {k: {} for k in SECTIONS}

    def add(self, section: str, row: int, uid: int, label: str) -> None:
        self.rows[section][row] = (uid, label)

    def get(self, section: str, row: int) -> tuple[int, str] | None:
        return self.rows.get(section, {}).get(row)


# ------------------------------------------------------------------------------ cell strings -> locators
_CELL = r"[A-Q]\d{1,3}"
_REF = re.compile(rf"({_CELL})(?:[:/]({_CELL}))?")
_TOKEN = re.compile(rf"\b{_CELL}(?::{_CELL})?(?:/{_CELL}(?::{_CELL})?)*\b")


def _split_ref(a: str) -> tuple[str, int]:
    return a[0], int(a[1:])


def _loc(screen: str, rid: int | None, field: str, rec_label: str = "") -> Locator:
    parts = [SCREEN_LABEL[screen]]
    if rec_label:
        parts.append(rec_label)
    if field:
        parts.append(FIELD_LABEL[screen].get(field, field))
    return Locator(screen, rid, field, SEP.join(parts))


def locators_for_ref(a: str, b: str | None, idx: RowIndex | None) -> list[Locator]:
    """Locators of one cell (``"O111"``) or range (``"D24:J24"``)."""
    c1, r1 = _split_ref(a)
    c2, r2 = _split_ref(b) if b else (c1, r1)
    if c2 < c1:
        c1, c2 = c2, c1
    if r2 < r1:
        r1, r2 = r2, r1
    out: list[Locator] = []
    sec = section_of_row(r1)
    if sec == "settings":
        for r in range(r1, min(r2, 18) + 1):
            for c in range(ord(c1), ord(c2) + 1):
                f = SETTINGS_CELLS.get((chr(c), r))
                if f:
                    out.append(_loc("settings", None, f))
        return _dedupe(out)
    if sec is None:
        return []
    screen, _lo, _hi, cols = SECTIONS[sec]
    fields = _dedupe_str(cols.get(chr(c)) for c in range(ord(c1), ord(c2) + 1) if cols.get(chr(c)))
    whole = (ord(c2) - ord(c1) + 1) > 3 and len(fields) > 2
    if r2 - r1 > 5:  # a whole table column
        return _dedupe([_loc(screen, None, "" if whole else f) for f in (fields or [""])])
    for r in range(r1, r2 + 1):
        rec = idx.get(sec, r) if idx is not None else None
        if rec is None:
            out.extend(_loc(screen, None, "" if whole else f) for f in (fields or [""]))
            continue
        uid, label = rec
        if whole or not fields:
            out.append(_loc(screen, uid, "", label))
        else:
            out.extend(_loc(screen, uid, f, label) for f in fields)
    return _dedupe(out)


def _dedupe(items: Iterable[Locator]) -> list[Locator]:
    seen: set[Locator] = set()
    out: list[Locator] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _dedupe_str(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def locators_for_cells(cell: str, idx: RowIndex | None) -> list[Locator]:
    """All locators of an ``Issue.cell`` string (comma separated cells / ranges)."""
    out: list[Locator] = []
    for part in re.split(r"\s*,\s*", cell or ""):
        for m in _REF.finditer(part):
            out.extend(locators_for_ref(m.group(1), m.group(2), idx))
    return _dedupe(out)


def short_text(loc: Locator, idx_label: str = "") -> str:
    """Inline wording of a locator inside a sentence."""
    fl = FIELD_LABEL[loc.screen].get(loc.field, loc.field) if loc.field else ""
    rec = loc.label.split(SEP)[1] if loc.record_id is not None else ""
    if loc.screen == "settings":
        return f"'{fl}'"
    if rec and fl:
        return f"'{rec}' ({fl})"
    if rec:
        return f"'{rec}'"
    if fl:
        return f"'{SCREEN_LABEL[loc.screen]}: {fl}'"
    return f"'{SCREEN_LABEL[loc.screen]}'"


# ------------------------------------------------------------------------------ text clean-up
_EMP_COLS_RE = re.compile(r"\bcolumns?\s+([A-Q])(?::([A-Q]))?((?:\s*(?:,|and|/)\s*[A-Q]\b)*)")


def _cols_text(first: str, second: str | None, rest: str, table: dict[str, str]) -> str:
    letters = [first]
    if second:
        letters = [chr(c) for c in range(ord(first), ord(second) + 1)]
    letters += re.findall(r"[A-Q]", rest or "")
    labs = _dedupe_str(f"'{table.get(c, c)}'" for c in letters if c in table)
    return " and ".join(labs) if labs else ""


def sanitize(text: str, idx: RowIndex | None = None) -> str:
    """Remove every Excel cell / column / row reference from a message, replacing it with the field or
    record it stands for (``idx`` resolves record rows; without it only the Settings fields are named)."""
    s = text or ""
    s = re.sub(r"\bINPUT\s+(?=[A-Q]\d)", "", s)
    s = re.sub(r"'keep 1 free day'\s+G16", "'Keep 1 free day per week'", s)
    s = re.sub(r"\bovertime flag O\d{1,3}\b", "'Overtime allowed' flag", s)
    s = re.sub(r"\bthe master overtime switch G14\b", "the master 'Overtime allowed' switch", s)
    # (Min male in H) / Min staff (column I) -> keep the words only
    s = re.sub(r"\b(Min male|Min staff|Min|Max staff)\s+in\s+(?:column\s+)?[A-Q]\b(?!\d)", r"\1", s)
    s = re.sub(r"\b(Min male|Min staff|Min|Max staff)\s*\((?:column\s+)?[A-Q]\)", r"\1", s)
    # "(C9 / column N)", "(C13/C14)", "(settings C13/C14)", "(G14 / column O)" -> dropped
    s = re.sub(rf"\s*\((?:settings\s+)?{_CELL}(?::{_CELL})?(?:\s*[/,]\s*(?:{_CELL}(?::{_CELL})?|columns?\s+[A-Q]))*\)", "", s)
    # "(column F)", "(I:K)", "(columns I:K)" -> the field name
    def paren_cols(m: re.Match) -> str:
        t = _cols_text(m.group(1), m.group(2), "", _EMP_COL_LABEL)
        return f"({t})" if t else ""
    s = re.sub(r"\((?:columns?\s+)?([A-Q])(?::([A-Q]))?\)", paren_cols, s)
    s = _EMP_COLS_RE.sub(lambda m: _cols_text(m.group(1), m.group(2), m.group(3), _EMP_COL_LABEL), s)

    def rows(m: re.Match) -> str:
        labs = []
        for g in (m.group(2), m.group(3)):
            if not g:
                continue
            r = int(g)
            sec = section_of_row(r)
            rec = idx.get(sec, r) if (idx is not None and sec and sec != "settings") else None
            labs.append(f"'{rec[1]}'" if rec else "that entry")
        return " and ".join(labs)
    s = re.sub(r"(?<!\()\b([Rr]ows?)\s+(\d+)(?:\s+and\s+(\d+))?(?!\))", rows, s)  # "(row 4)" is a finished label

    def cells(m: re.Match) -> str:
        labs: list[str] = []
        for part in m.group(0).split("/"):
            mm = _REF.fullmatch(part)
            if not mm:
                continue
            ls = locators_for_ref(mm.group(1), mm.group(2), idx)
            t = " / ".join(_dedupe_str(short_text(x) for x in ls))
            if t:
                labs.append(t)
        return " / ".join(labs)
    s = _TOKEN.sub(cells, s)
    s = re.sub(rf"\b{_CELL}\b", "", s)  # anything left over
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([.,;:])", r"\1", s)
    return s.strip()


# ------------------------------------------------------------------------------ wording polish (store inputs only)
_ISO = r"(\d{4})-(\d{2})-(\d{2})"
_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"


def _nice_date(y: str, m: str, d: str, year: bool = False) -> str:
    try:
        dt = date(int(y), int(m), int(d))
    except ValueError:
        return f"{y}-{m}-{d}"
    return f"{dt:%a} {dt.day} {dt:%b}" + (f" {dt.year}" if year else "")


#: names the old input sheet used -> the label on the screen
_OLD_NAMES = {"Friday eligible": "Can work Fridays", "Active": "Active"}


def polish(text: str) -> str:
    """Plain-English clean-up of an engine message for the app: UI labels instead of Y/N field talk, 'their'
    instead of 'her/his', dates like 'Wed 11 Nov', '1 man'.  Only used for inputs from the store."""
    s = text or ""
    # dates: ranges first ("2026-11-25-2026-11-25"), then single ISO dates, then "Fri 20-Nov"
    def rng(m: re.Match) -> str:
        a, b = m.group(1, 2, 3), m.group(4, 5, 6)
        y = a[0] != b[0]
        if a == b:
            return _nice_date(*a)
        return f"{_nice_date(*a, year=y)} to {_nice_date(*b, year=y)}"
    s = re.sub(rf"{_ISO}-{_ISO}", rng, s)
    s = re.sub(_ISO, lambda m: _nice_date(*m.groups()), s)
    s = re.sub(rf"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) 0?(\d{{1,2}})-({_MONTHS})\b", r"\1 \2 \3", s)
    s = re.sub(r"\((\w{3} \d{1,2} \w{3})\)", r"(\1)", s)
    # people
    s = re.sub(r"\b(?:her/his|his/her)\b", "their", s)
    s = re.sub(r"\b1 man/men\b", "1 man", s)
    s = re.sub(r"\b(\d+) man/men\b", lambda m: f"{m.group(1)} men", s)
    # Y/N field talk -> on/off with the label shown on the screen
    for old, new in _OLD_NAMES.items():
        s = s.replace(f"'{old}'", f"'{new}'")
    s = re.sub(r"'([^']+)'\s*=\s*Y\b", r"'\1' is on", s)
    s = re.sub(r"'([^']+)'\s*=\s*N\b", r"'\1' is off", s)
    s = re.sub(r"\bSet ('[^']+') to N\b", r"Switch \1 off", s)
    s = re.sub(r"\bSet ('[^']+') to Y\b", r"Switch \1 on", s)
    s = re.sub(r"\bActive = N\b", "'Active' off", s)
    return s


def _strip_suggested(s: str) -> str:
    """Drop the 'Suggested: ...' sentence (the Fix line says it)."""
    return re.sub(r"\s*Suggested:[^.]*\.?", "", s).strip()


def _convert(field: str, v: str):
    u = v.strip()
    if field in ("fri", "active", "no_we", "ot_keep", "strict_on"):
        return u.upper() not in ("N", "NO", "OFF", "FALSE", "0")
    if field in ("ot", "ot_on"):
        return u.upper() in ("ON", "Y", "YES", "TRUE", "1")
    if u == "":
        return None
    try:
        return float(u)
    except ValueError:
        return u


def parse_fix(fix: str, idx: RowIndex | None) -> list[FieldSet]:
    """Machine fix of the old engine (``"H110=N"``, ``"C65=8.0|D65=17.0"``, ``"I24="``) -> FieldSets."""
    out: list[FieldSet] = []
    for part in (fix or "").split("|"):
        m = re.fullmatch(rf"\s*({_CELL})\s*=\s*(.*?)\s*", part)
        if not m:
            return []
        locs = locators_for_ref(m.group(1), None, idx)
        if not locs:
            return []
        loc = locs[0]
        op = "add" if loc.field in ("allowed", "pref") and m.group(2) else "set"
        out.append(FieldSet(loc, _convert(loc.field, m.group(2)) if op == "set" else m.group(2).strip(), op))
    return out


def _fix_text(fs: list[FieldSet]) -> str:
    """The auto-fix in words: ``switch 'Can work Fridays' off``, ``set 'Max staff' to 4``, ``add 08 AM-04 PM to 'Allowed shifts'``."""
    parts = []
    for f in fs:
        fl = FIELD_LABEL.get(f.locator.screen, {}).get(f.locator.field, f.locator.field)
        v = f.value
        if f.op == "add":
            parts.append(f"add {v} to '{fl}'")
        elif isinstance(v, bool):
            parts.append(f"switch '{fl}' {'on' if v else 'off'}")
        elif v is None or v == "":
            parts.append(f"clear '{fl}'")
        else:
            if isinstance(v, float) and v == int(v):
                v = int(v)
            parts.append(f"set '{fl}' to {v}")
    return "; ".join(parts)


def localize_issues(issues: Iterable, inputs) -> None:
    """Fill ``where`` / ``fix_set`` and clean ``msg`` / ``fix`` / ``id`` / ``cell`` of ``issues`` in place.

    Does nothing unless the inputs come from the store (``inputs.source == "store"``)."""
    if getattr(inputs, "source", "") != "store":
        return
    idx = getattr(inputs, "row_index", None)
    for i in issues:
        if i.where:
            continue
        i.where = locators_for_cells(i.cell, idx)
        fs = parse_fix(i.fix, idx)
        if fs:
            i.fix_set = fs
            i.fix = _fix_text(fs)
        else:
            i.fix = polish(sanitize(i.fix, idx))
        i.msg = polish(sanitize(i.msg, idx))
        if fs:
            i.msg = _strip_suggested(i.msg)
        i.id = re.sub(rf"{_CELL}(?::{_CELL})?", lambda m: _id_token(m, idx), i.id or "")
        i.cell = ""


def _id_token(m: re.Match, idx: RowIndex | None) -> str:
    ls = locators_for_ref(m.group(0).split(":")[0], m.group(0).split(":")[1] if ":" in m.group(0) else None, idx)
    return "+".join(f"{x.screen}-{x.record_id if x.record_id is not None else 'all'}-{x.field}" for x in ls[:1]) or "x"


def where_text(issue) -> str:
    """``Employees › FAHAD › Overtime allowed; Settings › ...`` - where to look (shown as a small second line)."""
    from . import i18n

    labels = _dedupe_str(i18n.tr_label(w.label) for w in issue.where)
    shown = labels[:6] + ([i18n.tr("+{n} more", n=len(labels) - 6)] if len(labels) > 6 else [])
    return ("؛ " if i18n.is_ar() else "; ").join(shown)


def issue_text(issue) -> str:
    """The sentence the app shows for an issue (no Excel cell references, no locator): message and fix."""
    from . import i18n

    out = sanitize(issue.msg)
    fix = issue.fix
    fx = ""
    if fix and not re.match(rf"\s*{_CELL}\s*=", fix):
        fx = sanitize(fix)
        if not fx or fx.lower().rstrip(".") in out.lower():
            fx = ""
    if i18n.is_ar():
        out = i18n.tr_msg(out)
        if fx:
            out += "  " + i18n.tr("Fix:" if issue.fix_set else "Suggestion:") + " " + i18n.tr_msg(fx)
        return out
    if fx:
        out += ("  Fix: " if issue.fix_set else "  Suggestion: ") + fx
    return out
