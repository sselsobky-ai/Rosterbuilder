"""SQLite data store of the roster app (BUILD_GUIDE sections 4, 5 and 12 row 8).

Everything the user enters lives here; every write is committed immediately (auto-save).

Layout
------
* **Master data** (scope ``0``): settings, receptions, coverage targets, shifts, employees - the
  "current lists" the user edits.
* **Months**: ``create_month`` snapshots the master data (or the previous month's snapshot) into the
  month (scope = ``months.id``).  A month also owns its leave, fixed shifts, demand rows, the built
  roster, the locked manual edits and the history record.
* Every record has a **stable integer uid** (the same uid in the master and in every snapshot), and
  references are uids, so renaming a reception / shift / employee never breaks a reference.
* Editing the master data is propagated to the months that are *not built* (``status = 'draft'``);
  a built month is frozen until :meth:`Store.reopen_month` is called.

Tables: ``schema_version``, ``seq``, ``months``, ``settings``, ``receptions``, ``targets``,
``shifts``, ``employees``, ``leave``, ``fixed_shifts``, ``demand`` (all data tables carry
``scope`` + ``uid``).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from .model import shift_code_from_times

MASTER = 0
SCHEMA_VERSION = 2
BACKUPS_KEPT = 10


class StoreError(Exception):
    """Base class of the store's errors (the UI shows the message)."""


class DuplicateError(StoreError):
    """A name / shift code is already used."""


class InUseError(StoreError):
    """The record is still referenced by other records."""


class MonthFrozenError(StoreError):
    """The month is built; call ``reopen_month`` first."""


class NotFoundError(StoreError):
    """No such month / record."""


# ----------------------------------------------------------------------------- records
@dataclass
class SettingsRec:
    """Settings screen (section 4). Weekday names and the gender codes M/F are fixed in the app."""

    week_start_name: str = "SAT"
    fri_name: str = "FRI"
    max_week_h: float = 40.0
    max_shift_h: float = 8.0
    min_rest: float = 8.0
    min_rest_split: float = 0.0
    male_from: float = 22.0
    male_to: float = 6.0
    days_off_wk: int = 2
    carry_in: float = 0.0
    weekend_days: str = "FRI,SAT"
    ext_max: float = 6.0
    ot_on: bool = True
    ot_max: float = 168.0
    ot_keep: bool = True
    strict_on: bool = True


@dataclass
class ReceptionRec:
    name: str
    uid: int = 0


@dataclass
class TargetRec:
    """Coverage row: hours ``frm_h`` -> ``to_h`` (<= 24), min/max staff, min men (None = blank), days."""

    rec_uid: int
    frm_h: float
    to_h: float
    min_staff: float | None = 1.0
    min_male: float | None = None
    max_staff: float | None = None
    days: str = "ALL"  # ALL / WEEKDAYS (all but Friday) / comma list of weekday names
    uid: int = 0


@dataclass
class ShiftRec:
    """Shift: times (end <= start = next day), optional second part, paid hours. ``code`` is generated."""

    start1: float
    end1: float
    start2: float | None = None
    end2: float | None = None
    paid: float = 8.0
    code: str = ""  # read-only, generated from the times
    uid: int = 0


@dataclass
class EmployeeRec:
    name: str
    gender: str = "F"
    home_uid: int = 0
    also_uid: int | None = None
    active: bool = True
    fixed_off: str = ""  # weekday name or "" (AUTO)
    fixed_off2: str = ""
    fri: bool = True
    allowed: list[int] = field(default_factory=list)  # shift uids; empty = any
    pref: list[int] = field(default_factory=list)
    max_h: float | None = None  # None = the setting
    ot: bool = False
    no_we: bool = False
    notes: str = ""
    uid: int = 0
    ot_rank: int | None = None  # overtime priority 1..10 (1 = first); None = none; needs ot


@dataclass
class LeaveRec:
    emp_uid: int
    from_date: date
    to_date: date
    kind: str = "LEAVE"
    uid: int = 0


@dataclass
class FixedRec:
    emp_uid: int
    day: date
    shift_uid: int
    rec_uid: int | None = None  # None = home reception
    uid: int = 0


@dataclass
class DemandRec:
    """Demand row; ``counts`` = 7 numbers SUN..SAT, -1 = ALL."""

    rec_uid: int
    shift_uid: int
    gender: str = "ANY"
    fill: str = "HOME"
    prio: float = 99.0
    counts: list[int] = field(default_factory=lambda: [0] * 7)
    active: bool = True
    note: str = ""
    uid: int = 0

    @property
    def all_week(self) -> bool:
        return any(c < 0 for c in self.counts)


@dataclass
class MonthInfo:
    year: int
    month: int
    status: str  # draft / built
    id: int = 0
    created_at: str = ""
    built_at: str = ""

    @property
    def key(self) -> tuple[int, int]:
        return (self.year, self.month)


# kind -> (table, record class, {field: sql type kind}, month-only?)
_KINDS: dict[str, tuple[str, type, dict[str, str], bool]] = {
    "reception": ("receptions", ReceptionRec, {"name": "text"}, False),
    "target": ("targets", TargetRec, {"rec_uid": "int", "frm_h": "real", "to_h": "real", "min_staff": "real",
                                      "min_male": "real", "max_staff": "real", "days": "text"}, False),
    "shift": ("shifts", ShiftRec, {"start1": "real", "end1": "real", "start2": "real", "end2": "real",
                                   "paid": "real", "code": "text"}, False),
    "employee": ("employees", EmployeeRec, {"name": "text", "gender": "text", "home_uid": "int", "also_uid": "int",
                                            "active": "bool", "fixed_off": "text", "fixed_off2": "text",
                                            "fri": "bool", "allowed": "json", "pref": "json", "max_h": "real",
                                            "ot": "bool", "no_we": "bool", "notes": "text", "ot_rank": "int"}, False),
    "leave": ("leave", LeaveRec, {"emp_uid": "int", "from_date": "date", "to_date": "date", "kind": "text"}, True),
    "fixed": ("fixed_shifts", FixedRec, {"emp_uid": "int", "day": "date", "shift_uid": "int", "rec_uid": "int"}, True),
    "demand": ("demand", DemandRec, {"rec_uid": "int", "shift_uid": "int", "gender": "text", "fill": "text",
                                     "prio": "real", "counts": "json", "active": "bool", "note": "text"}, True),
}
_SQL_TYPE = {"text": "TEXT", "real": "REAL", "int": "INTEGER", "bool": "INTEGER", "date": "TEXT", "json": "TEXT"}
_SETTINGS_TYPES = {"week_start_name": "text", "fri_name": "text", "max_week_h": "real", "max_shift_h": "real",
                   "min_rest": "real", "min_rest_split": "real", "male_from": "real", "male_to": "real",
                   "days_off_wk": "int", "carry_in": "real", "weekend_days": "text", "ext_max": "real",
                   "ot_on": "bool", "ot_max": "real", "ot_keep": "bool", "strict_on": "bool"}


def _to_sql(kind: str, v: Any) -> Any:
    if v is None:
        return None
    if kind == "bool":
        return 1 if v else 0
    if kind == "date":
        return v.isoformat() if isinstance(v, date) else str(v)
    if kind == "json":
        return json.dumps(v)
    if kind == "int":
        return int(v)
    if kind == "real":
        return float(v)
    return str(v)


def _from_sql(kind: str, v: Any) -> Any:
    if v is None:
        return None
    if kind == "bool":
        return bool(v)
    if kind == "date":
        return date.fromisoformat(v)
    if kind == "json":
        return json.loads(v)
    return v


def _ddl() -> list[str]:
    out = [
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS seq (name TEXT PRIMARY KEY, value INTEGER NOT NULL)",
        """CREATE TABLE IF NOT EXISTS months (
            id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER NOT NULL, month INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft', created_at TEXT, built_at TEXT,
            roster_json TEXT, history_json TEXT, locked_json TEXT, UNIQUE(year, month))""",
        "CREATE TABLE IF NOT EXISTS settings (scope INTEGER PRIMARY KEY, "
        + ", ".join(f"{k} {_SQL_TYPE[t]}" for k, t in _SETTINGS_TYPES.items()) + ")",
    ]
    for table, _cls, cols, _mo in _KINDS.values():
        out.append(f"CREATE TABLE IF NOT EXISTS {table} (scope INTEGER NOT NULL, uid INTEGER NOT NULL, "
                   + ", ".join(f"{k} {_SQL_TYPE[t]}" for k, t in cols.items()) + ", PRIMARY KEY (scope, uid))")
    return out


def _migrate_2(conn: sqlite3.Connection) -> None:
    """Version 2 (milestone 11A): ``employees.ot_rank`` (overtime priority 1..10, NULL = none)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(employees)")}
    if "ot_rank" not in cols:
        conn.execute("ALTER TABLE employees ADD COLUMN ot_rank INTEGER")


#: schema migrations: ``{target_version: callable(conn)}``; version 1 is the original layout created by ``_ddl``
#: (a new database is created at :data:`SCHEMA_VERSION` straight away).
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {2: _migrate_2}


def default_db_path() -> Path:
    """``%APPDATA%\\RosterBuilder\\roster.db`` on Windows, ``~/.rosterbuilder/roster.db`` elsewhere."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "RosterBuilder" / "roster.db"
    return Path.home() / ".rosterbuilder" / "roster.db"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def month_key(m) -> tuple[int, int]:
    """``(year, month)`` from a tuple, a date or ``"YYYY-MM"``."""
    if isinstance(m, str):
        y, mo = m.split("-")[:2]
        return int(y), int(mo)
    if isinstance(m, date):
        return m.year, m.month
    return int(m[0]), int(m[1])


# ----------------------------------------------------------------------------- the store
class Store:
    """The SQLite data store.  ``Store(path)`` opens or creates the database; ``":memory:"`` works too."""

    def __init__(self, path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._depth = 0
        self._migrate()

    # ------------------------------------------------------------------ plumbing
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *a) -> None:
        self.close()

    @contextmanager
    def _tx(self):
        """One transaction (nested calls join the outer one); committed on exit, rolled back on error."""
        with self._lock:
            if self._depth == 0:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("COMMIT")

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args=()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def _next_uid(self) -> int:
        row = self._q("SELECT value FROM seq WHERE name='uid'")
        v = (row[0][0] if row else 0) + 1
        self._x("INSERT OR REPLACE INTO seq(name, value) VALUES('uid', ?)", (v,))
        return v

    # ------------------------------------------------------------------ schema / migrations
    @property
    def schema_version(self) -> int:
        r = self._q("SELECT MAX(version) FROM schema_version")
        return int(r[0][0] or 0) if r else 0

    def _migrate(self) -> None:
        with self._tx():
            for stmt in _ddl():
                self._x(stmt)
            cur = self.schema_version
            if cur == 0:  # a new database already has the newest layout
                cur = max([SCHEMA_VERSION, *MIGRATIONS])
                self._x("INSERT INTO schema_version(version) VALUES (?)", (cur,))
            if cur > max([SCHEMA_VERSION, *MIGRATIONS]):
                raise StoreError(f"The database is from a newer version of the app (schema {cur}).")
            for v in sorted(MIGRATIONS):
                if v > cur:
                    MIGRATIONS[v](self._conn)
                    self._x("INSERT INTO schema_version(version) VALUES (?)", (v,))
                    cur = v
            if not self._q("SELECT 1 FROM settings WHERE scope=?", (MASTER,)):
                self._insert_settings(MASTER, SettingsRec())

    # ------------------------------------------------------------------ backup
    def backup(self, dest_dir=None, keep: int = BACKUPS_KEPT) -> Path:
        """Copy the database to ``<db folder>/backups/roster-<timestamp>.db`` and keep the last ``keep`` copies."""
        if self.path == ":memory:":
            raise StoreError("An in-memory database cannot be backed up.")
        folder = Path(dest_dir) if dest_dir else Path(self.path).parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = folder / f"roster-{stamp}.db"
        n = 1
        while target.exists():
            target = folder / f"roster-{stamp}-{n}.db"
            n += 1
        with self._lock:
            dst = sqlite3.connect(str(target))
            try:
                self._conn.backup(dst)
            finally:
                dst.close()
        olds = sorted(folder.glob("roster-*.db"), key=lambda p: p.name)
        for p in olds[:-keep] if keep > 0 else olds:
            try:
                p.unlink()
            except OSError:
                pass
        return target

    def list_backups(self, dest_dir=None) -> list[Path]:
        folder = Path(dest_dir) if dest_dir else Path(self.path).parent / "backups"
        return sorted(folder.glob("roster-*.db"), key=lambda p: p.name)

    @staticmethod
    def restore(backup_path, db_path) -> "Store":
        """Replace the database file at ``db_path`` with a backup copy and open it."""
        shutil.copyfile(str(backup_path), str(db_path))
        return Store(db_path)

    # ------------------------------------------------------------------ months
    def _month_row(self, ym) -> sqlite3.Row:
        y, m = month_key(ym)
        r = self._q("SELECT * FROM months WHERE year=? AND month=?", (y, m))
        if not r:
            raise NotFoundError(f"The month {y}-{m:02d} does not exist.")
        return r[0]

    @staticmethod
    def _info(r: sqlite3.Row) -> MonthInfo:
        return MonthInfo(r["year"], r["month"], r["status"], r["id"], r["created_at"] or "", r["built_at"] or "")

    def has_month(self, ym) -> bool:
        y, m = month_key(ym)
        return bool(self._q("SELECT 1 FROM months WHERE year=? AND month=?", (y, m)))

    def get_month(self, ym) -> MonthInfo:
        return self._info(self._month_row(ym))

    def list_months(self) -> list[MonthInfo]:
        return [self._info(r) for r in self._q("SELECT * FROM months ORDER BY year, month")]

    def previous_month(self, ym) -> MonthInfo | None:
        """The latest existing month before ``ym``."""
        y, m = month_key(ym)
        r = self._q("SELECT * FROM months WHERE (year*12+month) < ? ORDER BY year DESC, month DESC LIMIT 1", (y * 12 + m,))
        return self._info(r[0]) if r else None

    def scope_id(self, month=None) -> int:
        """Scope id of the master data (``month=None``) or of a month."""
        return MASTER if month is None else int(self._month_row(month)["id"])

    def _draft_scopes(self) -> list[int]:
        return [r["id"] for r in self._q("SELECT id FROM months WHERE status='draft'")]

    def create_month(self, year: int, month: int, copy_from=None, copy_fixed: bool = False) -> MonthInfo:
        """Create a month.

        ``copy_from`` ``None`` snapshots the current master lists; ``"previous"`` takes the month-specific data
        (demand) of the latest earlier month; settings, receptions, targets, shifts and employees ALWAYS
        come from the current master; a ``(year, month)`` takes the demand from that month.  Leave periods that
        overlap the new month are carried over from every existing month; fixed shifts are copied only
        with ``copy_fixed`` (same day of the month, when it exists)."""
        if not 1 <= int(month) <= 12:
            raise ValueError("month must be 1..12")
        if self.has_month((year, month)):
            raise StoreError(f"The month {year}-{month:02d} already exists.")
        with self._tx():
            src_scope = MASTER
            src_info = None
            if copy_from == "previous":
                src_info = self.previous_month((year, month))
            elif copy_from is not None:
                src_info = self.get_month(copy_from)
            if src_info is not None:
                src_scope = src_info.id  # only month-specific data (demand, fixed shifts) comes from here
            cur = self._x("INSERT INTO months(year, month, status, created_at) VALUES (?,?,?,?)",
                          (year, month, "draft", _now()))
            sid = int(cur.lastrowid)
            self._x("DELETE FROM settings WHERE scope=?", (sid,))
            # master data always from the CURRENT master; demand from the source month (R18.1)
            self._x("INSERT INTO settings SELECT ?, " + ", ".join(_SETTINGS_TYPES) + " FROM settings WHERE scope=?", (sid, MASTER))
            for k, scope_from in (("reception", MASTER), ("target", MASTER), ("shift", MASTER), ("employee", MASTER),
                                  ("demand", src_scope)):
                if k == "demand" and src_scope == MASTER:
                    continue
                table, _c, cols, _mo = _KINDS[k]
                names = ", ".join(["uid", *cols])
                self._x(f"INSERT INTO {table}(scope, {names}) SELECT ?, {names} FROM {table} WHERE scope=?", (sid, scope_from))
            if src_scope != MASTER:  # drop copied demand rows whose reception / shift no longer exists in the master
                self._x("DELETE FROM demand WHERE scope=? AND (rec_uid NOT IN (SELECT uid FROM receptions WHERE scope=?) "
                        "OR shift_uid NOT IN (SELECT uid FROM shifts WHERE scope=?))", (sid, sid, sid))
            first = date(year, month, 1)
            last = date(year + (month == 12), month % 12 + 1, 1)
            last = date.fromordinal(last.toordinal() - 1)
            emp_ids = {r[0] for r in self._q("SELECT uid FROM employees WHERE scope=?", (sid,))}
            seen: set[tuple] = set()
            for r in self._q("SELECT l.* FROM leave l JOIN months m ON m.id = l.scope WHERE m.id != ? ORDER BY m.year, m.month, l.uid", (sid,)):
                key = (r["emp_uid"], r["from_date"], r["to_date"], r["kind"])
                if (r["emp_uid"] in emp_ids and key not in seen
                        and date.fromisoformat(r["from_date"]) <= last and date.fromisoformat(r["to_date"]) >= first):
                    seen.add(key)
                    self._insert("leave", sid, LeaveRec(r["emp_uid"], date.fromisoformat(r["from_date"]),
                                                        date.fromisoformat(r["to_date"]), r["kind"], self._next_uid()))
            if copy_fixed and src_info is not None:
                for r in self._q("SELECT * FROM fixed_shifts WHERE scope=?", (src_scope,)):
                    d0 = date.fromisoformat(r["day"])
                    try:
                        d1 = date(year, month, d0.day)
                    except ValueError:
                        continue
                    self._insert("fixed", sid, FixedRec(r["emp_uid"], d1, r["shift_uid"], r["rec_uid"], self._next_uid()))
        return self.get_month((year, month))

    def reopen_month(self, ym) -> MonthInfo:
        """Un-freeze a built month (status back to draft); master edits reach it again."""
        r = self._month_row(ym)
        self._x("UPDATE months SET status='draft' WHERE id=?", (r["id"],))
        return self.get_month(ym)

    def delete_month(self, ym) -> None:
        r = self._month_row(ym)
        with self._tx():
            for table, *_ in _KINDS.values():
                self._x(f"DELETE FROM {table} WHERE scope=?", (r["id"],))
            self._x("DELETE FROM settings WHERE scope=?", (r["id"],))
            self._x("DELETE FROM months WHERE id=?", (r["id"],))

    def has_master_data(self) -> bool:
        """True when the master lists hold any receptions, coverage rows, shifts or employees."""
        return any(self._q(f"SELECT 1 FROM {_KINDS[k][0]} WHERE scope=? LIMIT 1", (MASTER,))
                   for k in ("reception", "target", "shift", "employee"))

    def wipe_master_and_drafts(self) -> int:
        """Delete all master data (settings go back to the defaults) and every draft month; built months stay
        frozen and untouched.  Returns the number of draft months deleted.  One transaction."""
        with self._tx():
            drafts = [m for m in self.list_months() if m.status == "draft"]
            for m in drafts:
                self.delete_month(m.key)
            for table, *_ in _KINDS.values():
                self._x(f"DELETE FROM {table} WHERE scope=?", (MASTER,))
            self._insert_settings(MASTER, SettingsRec())
        return len(drafts)

    # ------------------------------------------------------------------ built roster / history / locked edits
    def save_roster(self, ym, roster: dict, history: dict | None = None) -> None:
        """Store the built roster (JSON dict, see ``inputs_from_store.roster_to_dict``) and optionally the
        history record; marks the month built."""
        r = self._month_row(ym)
        with self._tx():
            self._x("UPDATE months SET roster_json=?, status='built', built_at=? WHERE id=?",
                    (json.dumps(roster), _now(), r["id"]))
            if history is not None:
                self._x("UPDATE months SET history_json=? WHERE id=?", (json.dumps(history), r["id"]))

    def update_roster(self, ym, roster: dict, history: dict | None = None) -> None:
        """Replace the stored roster of a month (a manual edit) WITHOUT changing the month's status or build time."""
        r = self._month_row(ym)
        with self._tx():
            self._x("UPDATE months SET roster_json=? WHERE id=?", (json.dumps(roster), r["id"]))
            if history is not None:
                self._x("UPDATE months SET history_json=? WHERE id=?", (json.dumps(history), r["id"]))

    def mark_built(self, ym) -> MonthInfo:
        """Mark a month with a roster as built / final (inputs locked)."""
        r = self._month_row(ym)
        if not r["roster_json"]:
            raise StoreError("This month has no roster yet.")
        self._x("UPDATE months SET status='built', built_at=COALESCE(built_at, ?) WHERE id=?", (_now(), r["id"]))
        return self.get_month(ym)

    def get_roster(self, ym) -> dict | None:
        v = self._month_row(ym)["roster_json"]
        return json.loads(v) if v else None

    def save_history(self, ym, record: dict) -> None:
        self._x("UPDATE months SET history_json=? WHERE id=?", (json.dumps(record), self._month_row(ym)["id"]))

    def get_history(self, ym) -> dict | None:
        v = self._month_row(ym)["history_json"]
        return json.loads(v) if v else None

    def set_locked(self, ym, edits: list[dict]) -> None:
        """Locked manual edits of the month: ``[{"emp": uid, "date": iso, "shift": uid|None, "rec": uid|None, "ot": bool}]``."""
        self._x("UPDATE months SET locked_json=? WHERE id=?", (json.dumps(edits), self._month_row(ym)["id"]))

    def get_locked(self, ym) -> list[dict]:
        v = self._month_row(ym)["locked_json"]
        return json.loads(v) if v else []

    # ------------------------------------------------------------------ settings
    def _insert_settings(self, sid: int, s: SettingsRec) -> None:
        self._x("INSERT OR REPLACE INTO settings(scope, " + ", ".join(_SETTINGS_TYPES) + ") VALUES (?" + ",?" * len(_SETTINGS_TYPES) + ")",
                (sid, *[_to_sql(t, getattr(s, k)) for k, t in _SETTINGS_TYPES.items()]))

    def _read_settings(self, sid: int) -> SettingsRec:
        r = self._q("SELECT * FROM settings WHERE scope=?", (sid,))
        if not r:
            raise NotFoundError("No settings for this scope.")
        return SettingsRec(**{k: _from_sql(t, r[0][k]) for k, t in _SETTINGS_TYPES.items()})

    # ------------------------------------------------------------------ generic record access
    def _insert(self, kind: str, sid: int, rec) -> None:
        table, _cls, cols, _mo = _KINDS[kind]
        names = ", ".join(cols)
        self._x(f"INSERT INTO {table}(scope, uid, {names}) VALUES (?, ?" + ",?" * len(cols) + ")",
                (sid, rec.uid, *[_to_sql(t, getattr(rec, k)) for k, t in cols.items()]))

    def _row_to_rec(self, kind: str, r: sqlite3.Row):
        _t, cls, cols, _mo = _KINDS[kind]
        kw = {k: _from_sql(t, r[k]) for k, t in cols.items()}
        if kind == "shift":
            if kw["start2"] is None or kw["end2"] is None:
                kw["start2"] = kw["end2"] = None
        return cls(uid=r["uid"], **kw)

    def _read(self, kind: str, sid: int) -> list:
        table = _KINDS[kind][0]
        return [self._row_to_rec(kind, r) for r in self._q(f"SELECT * FROM {table} WHERE scope=? ORDER BY uid", (sid,))]

    def _read_one(self, kind: str, sid: int, uid: int):
        table = _KINDS[kind][0]
        r = self._q(f"SELECT * FROM {table} WHERE scope=? AND uid=?", (sid, uid))
        if not r:
            raise NotFoundError(f"No {kind} with id {uid}.")
        return self._row_to_rec(kind, r[0])

    def scope(self, month=None) -> "Scope":
        """The master data (``None``) or one month's data, with the full CRUD API."""
        return Scope(self, month)

    @property
    def master(self) -> "Scope":
        return Scope(self, None)

    def month(self, year: int, month: int) -> "Scope":
        return Scope(self, (year, month))


# ----------------------------------------------------------------------------- scope (CRUD)
class Scope:
    """CRUD on the master data or one month.  Writes on the master are propagated to every draft month;
    writes on a built month raise :class:`MonthFrozenError`.  Every call is one committed transaction."""

    def __init__(self, store: Store, month=None) -> None:
        self.store = store
        self.month = None if month is None else month_key(month)

    # -- scope helpers
    @property
    def sid(self) -> int:
        return self.store.scope_id(self.month)

    def _writable_scopes(self) -> list[int]:
        st = self.store
        if self.month is None:
            return [MASTER] + st._draft_scopes()
        if st.get_month(self.month).status != "draft":
            raise MonthFrozenError(f"The month {self.month[0]}-{self.month[1]:02d} is built and frozen; reopen it to edit.")
        return [self.sid]

    # -- settings
    def settings(self) -> SettingsRec:
        return self.store._read_settings(self.sid)

    def update_settings(self, **kw) -> SettingsRec:
        bad = set(kw) - set(_SETTINGS_TYPES)
        if bad:
            raise TypeError(f"unknown settings field(s): {sorted(bad)}")
        with self.store._tx():
            for sid in self._writable_scopes():
                cur = self.store._read_settings(sid)
                self.store._insert_settings(sid, replace(cur, **kw))
        return self.settings()

    # -- generic
    def list(self, kind: str) -> list:
        return self.store._read(kind, self.sid)

    def get(self, kind: str, uid: int):
        return self.store._read_one(kind, self.sid, uid)

    def add(self, kind: str, /, **kw):
        table, cls, cols, month_only = _KINDS[kind]
        if month_only and self.month is None:
            raise StoreError(f"{kind} records belong to a month, not to the master data.")
        bad = set(kw) - set(cols) - {"uid"}
        if bad:
            raise TypeError(f"unknown {kind} field(s): {sorted(bad)}")
        st = self.store
        with st._tx():
            scopes = self._writable_scopes()
            kw.setdefault("uid", 0)
            rec = cls(**kw)
            self._prepare(kind, rec)
            rec.uid = st._next_uid()
            for sid in scopes:
                self._check(kind, sid, rec, creating=True)
                st._insert(kind, sid, rec)
            return st._read_one(kind, self.sid, rec.uid)

    def update(self, kind: str, uid: int, /, **kw):
        table, cls, cols, _mo = _KINDS[kind]
        bad = set(kw) - set(cols)
        if bad:
            raise TypeError(f"unknown {kind} field(s): {sorted(bad)}")
        if kind == "shift":
            kw.pop("code", None)
        st = self.store
        with st._tx():
            scopes = self._writable_scopes()
            st._read_one(kind, self.sid, uid)  # NotFound in this scope
            for sid in scopes:
                try:
                    cur = st._read_one(kind, sid, uid)
                except NotFoundError:
                    continue
                new = replace(cur, **kw)
                if kind == "employee" and "ot_rank" not in kw and not new.ot:
                    new.ot_rank = None  # overtime switched off: the priority goes with it
                self._prepare(kind, new)
                self._check(kind, sid, new, creating=False)
                st._x(f"DELETE FROM {table} WHERE scope=? AND uid=?", (sid, uid))
                st._insert(kind, sid, new)
            return st._read_one(kind, self.sid, uid)

    def delete(self, kind: str, uid: int, force: bool = False) -> None:
        """Delete a record.  A record that others still use raises :class:`InUseError` unless ``force``
        (then the dependent targets / demand / fixed shifts are deleted too).  Deleting an employee also
        deletes their leave and fixed shifts; deleting a shift removes it from the allowed / preferred lists."""
        st = self.store
        with st._tx():
            scopes = self._writable_scopes()
            st._read_one(kind, self.sid, uid)
            for sid in scopes:
                try:
                    st._read_one(kind, sid, uid)
                except NotFoundError:
                    continue
                self._cascade(kind, sid, uid, force)
                st._x(f"DELETE FROM {_KINDS[kind][0]} WHERE scope=? AND uid=?", (sid, uid))

    def _delete_where(self, kind: str, sid: int, cond: str, args: tuple) -> None:
        self.store._x(f"DELETE FROM {_KINDS[kind][0]} WHERE scope=? AND {cond}", (sid, *args))

    def _cascade(self, kind: str, sid: int, uid: int, force: bool) -> None:
        st = self.store
        if kind == "reception":
            users = st._q("SELECT name FROM employees WHERE scope=? AND home_uid=?", (sid, uid))
            if users:
                raise InUseError("Reception is the home reception of: " + ", ".join(r[0] for r in users))
            deps = (st._q("SELECT 1 FROM targets WHERE scope=? AND rec_uid=?", (sid, uid))
                    + st._q("SELECT 1 FROM demand WHERE scope=? AND rec_uid=?", (sid, uid))
                    + st._q("SELECT 1 FROM fixed_shifts WHERE scope=? AND rec_uid=?", (sid, uid)))
            if deps and not force:
                raise InUseError("Reception is used by coverage rows, demand rows or fixed shifts.")
            self._delete_where("target", sid, "rec_uid=?", (uid,))
            self._delete_where("demand", sid, "rec_uid=?", (uid,))
            self._delete_where("fixed", sid, "rec_uid=?", (uid,))
            st._x("UPDATE employees SET also_uid=NULL WHERE scope=? AND also_uid=?", (sid, uid))
        elif kind == "shift":
            deps = (st._q("SELECT 1 FROM demand WHERE scope=? AND shift_uid=?", (sid, uid))
                    + st._q("SELECT 1 FROM fixed_shifts WHERE scope=? AND shift_uid=?", (sid, uid)))
            if deps and not force:
                raise InUseError("Shift is used by demand rows or fixed shifts.")
            self._delete_where("demand", sid, "shift_uid=?", (uid,))
            self._delete_where("fixed", sid, "shift_uid=?", (uid,))
            for e in st._read("employee", sid):
                if uid in e.allowed or uid in e.pref:
                    st._x("UPDATE employees SET allowed=?, pref=? WHERE scope=? AND uid=?",
                          (json.dumps([x for x in e.allowed if x != uid]), json.dumps([x for x in e.pref if x != uid]), sid, e.uid))
        elif kind == "employee":
            self._delete_where("leave", sid, "emp_uid=?", (uid,))
            self._delete_where("fixed", sid, "emp_uid=?", (uid,))

    # -- validation of references / names
    def _prepare(self, kind: str, rec) -> None:
        if kind == "shift":
            if rec.start2 is None or rec.end2 is None or rec.start2 < 0:
                rec.start2 = rec.end2 = None
            rec.code = shift_code_from_times(rec.start1, rec.end1, rec.start2, rec.end2)
        elif kind == "employee":
            rec.name = (rec.name or "").strip()
            rec.gender = (rec.gender or "").strip().upper()
            rec.fixed_off = (rec.fixed_off or "").strip().upper()
            rec.fixed_off2 = (rec.fixed_off2 or "").strip().upper()
            rec.allowed = list(dict.fromkeys(rec.allowed))
            rec.pref = list(dict.fromkeys(rec.pref))
            if rec.ot_rank is not None:
                try:
                    rec.ot_rank = int(rec.ot_rank)
                except (TypeError, ValueError):
                    raise StoreError("The overtime priority must be a whole number from 1 to 10.") from None
        elif kind == "reception":
            rec.name = (rec.name or "").strip()
        elif kind == "demand":
            rec.counts = [int(c) for c in rec.counts]
            if len(rec.counts) != 7:
                raise ValueError("counts needs 7 numbers (SUN..SAT, -1 = ALL)")

    def _check(self, kind: str, sid: int, rec, creating: bool) -> None:
        st = self.store

        def exists(table: str, uid) -> bool:
            return bool(st._q(f"SELECT 1 FROM {table} WHERE scope=? AND uid=?", (sid, uid)))

        def dup(table: str, col: str, val: str, what: str) -> None:
            for r in st._q(f"SELECT uid FROM {table} WHERE scope=? AND upper({col})=upper(?) AND uid != ?", (sid, val, rec.uid)):
                raise DuplicateError(f"{what} '{val}' already exists.")

        if kind == "reception":
            if not rec.name:
                raise StoreError("A reception needs a name.")
            dup("receptions", "name", rec.name, "The reception")
        elif kind == "employee":
            if not rec.name:
                raise StoreError("An employee needs a name.")
            dup("employees", "name", rec.name, "The employee")
            if not exists("receptions", rec.home_uid):
                raise StoreError("The home reception does not exist.")
            if rec.also_uid is not None and not exists("receptions", rec.also_uid):
                raise StoreError("The 'can also cover' reception does not exist.")
            for u in rec.allowed + rec.pref:
                if not exists("shifts", u):
                    raise StoreError(f"Shift id {u} does not exist.")
            if rec.ot_rank is not None:
                if not 1 <= rec.ot_rank <= 10:
                    raise StoreError("The overtime priority must be between 1 and 10.")
                if not rec.ot:
                    raise StoreError(f"{rec.name} cannot get an overtime priority because 'Overtime allowed' is off. "
                                     "Switch it on first.")
        elif kind == "target":
            if not exists("receptions", rec.rec_uid):
                raise StoreError("The reception does not exist.")
        elif kind == "shift":
            dup("shifts", "code", rec.code, "The shift")
        elif kind == "leave":
            if not exists("employees", rec.emp_uid):
                raise StoreError("The employee does not exist.")
            if rec.to_date < rec.from_date:
                raise StoreError("Leave ends before it starts.")
        elif kind == "fixed":
            if not exists("employees", rec.emp_uid) or not exists("shifts", rec.shift_uid):
                raise StoreError("The employee or the shift does not exist.")
            if rec.rec_uid is not None and not exists("receptions", rec.rec_uid):
                raise StoreError("The reception does not exist.")
        elif kind == "demand":
            if not exists("receptions", rec.rec_uid) or not exists("shifts", rec.shift_uid):
                raise StoreError("The reception or the shift does not exist.")

    # -- named wrappers ------------------------------------------------------------
    def list_receptions(self) -> list[ReceptionRec]: return self.list("reception")
    def get_reception(self, uid) -> ReceptionRec: return self.get("reception", uid)
    def add_reception(self, name: str) -> ReceptionRec: return self.add("reception", name=name)
    def rename_reception(self, uid, name: str) -> ReceptionRec: return self.update("reception", uid, name=name)
    def delete_reception(self, uid, force=False) -> None: self.delete("reception", uid, force)

    def list_targets(self) -> list[TargetRec]: return self.list("target")
    def get_target(self, uid) -> TargetRec: return self.get("target", uid)
    def add_target(self, **kw) -> TargetRec: return self.add("target", **kw)
    def update_target(self, uid, **kw) -> TargetRec: return self.update("target", uid, **kw)
    def delete_target(self, uid) -> None: self.delete("target", uid)

    def list_shifts(self) -> list[ShiftRec]: return self.list("shift")
    def get_shift(self, uid) -> ShiftRec: return self.get("shift", uid)
    def add_shift(self, **kw) -> ShiftRec: return self.add("shift", **kw)
    def update_shift(self, uid, **kw) -> ShiftRec: return self.update("shift", uid, **kw)
    def delete_shift(self, uid, force=False) -> None: self.delete("shift", uid, force)

    def list_employees(self) -> list[EmployeeRec]: return self.list("employee")
    def get_employee(self, uid) -> EmployeeRec: return self.get("employee", uid)
    def add_employee(self, **kw) -> EmployeeRec: return self.add("employee", **kw)
    def update_employee(self, uid, **kw) -> EmployeeRec: return self.update("employee", uid, **kw)
    def delete_employee(self, uid) -> None: self.delete("employee", uid)

    def list_leave(self) -> list[LeaveRec]: return self.list("leave")
    def add_leave(self, **kw) -> LeaveRec: return self.add("leave", **kw)
    def update_leave(self, uid, **kw) -> LeaveRec: return self.update("leave", uid, **kw)
    def delete_leave(self, uid) -> None: self.delete("leave", uid)

    def list_fixed(self) -> list[FixedRec]: return self.list("fixed")
    def add_fixed(self, **kw) -> FixedRec: return self.add("fixed", **kw)
    def update_fixed(self, uid, **kw) -> FixedRec: return self.update("fixed", uid, **kw)
    def delete_fixed(self, uid) -> None: self.delete("fixed", uid)

    def list_demand(self) -> list[DemandRec]: return self.list("demand")
    def add_demand(self, **kw) -> DemandRec: return self.add("demand", **kw)
    def update_demand(self, uid, **kw) -> DemandRec: return self.update("demand", uid, **kw)
    def delete_demand(self, uid) -> None: self.delete("demand", uid)
