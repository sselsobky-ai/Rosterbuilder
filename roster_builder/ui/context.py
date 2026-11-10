"""Shared state of the window: the store, the selected month, undo/redo and change notification.

Every write of a page goes through :class:`AppContext`.  It writes to the store at once (auto-save) and
pushes a ``QUndoCommand`` that knows the previous values, so Ctrl+Z restores them in the store too.
Edits are written to the *master data*, which the store propagates to every draft month; a built month is
shown read-only from its own snapshot.
"""

from __future__ import annotations

import copy
import dataclasses
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..i18n import tr, tr_msg, yes_no_box
from ..store import MonthFrozenError, Scope, Store, StoreError

MONTH_KINDS = frozenset({"leave", "fixed", "demand"})
ALL_KINDS = frozenset({"settings", "reception", "target", "shift", "employee", "leave", "fixed", "demand"})
_REF_FIELDS = ("home_uid", "also_uid", "rec_uid", "shift_uid", "emp_uid")
_MERGE_SECONDS = 2.0


def record_fields(rec) -> dict:
    """Editable fields of a store record (no id, no generated code)."""
    return {f.name: copy.deepcopy(getattr(rec, f.name)) for f in dataclasses.fields(rec) if f.name not in ("uid", "code")}


class AppContext(QObject):
    """Store + current month + undo stack + signals."""

    changed = Signal(object, object)  # (frozenset of kinds, origin widget or None)
    month_changed = Signal()
    saved = Signal(str)
    message = Signal(str, str)  # text, level ("info" / "error")
    request_new_month = Signal()
    request_delete_month = Signal()
    request_reopen_month = Signal()
    request_jump = Signal(object, str)  # Locator, message
    request_open_page = Signal(str)  # page key ("roster", "checks" …)
    request_rebuild = Signal(bool)  # Roster page -> Build page: rebuild this month (argument: keep my manual changes)

    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.stack = QUndoStack(self)
        self.stack.setUndoLimit(300)
        self.alias: dict[int, int] = {}
        self.month: tuple[int, int] | None = None
        self.confirm = self._default_confirm
        ms = store.list_months()
        if ms:
            self.month = ms[-1].key

    # ------------------------------------------------------------------ month / scopes
    @property
    def month_info(self):
        return self.store.get_month(self.month) if self.month and self.store.has_month(self.month) else None

    @property
    def read_only(self) -> bool:
        mi = self.month_info
        return bool(mi and mi.status != "draft")

    def view(self) -> Scope:
        """What the pages show: the selected month's data, or the master data when there is no month."""
        return self.store.scope(self.month if self.month_info else None)

    def writer(self, kind: str | None = None) -> Scope:
        """Where edits go: the master data for setup records (the store passes them on to draft months), the
        selected month for leave, fixed shifts and demand rows."""
        if self.read_only:
            raise MonthFrozenError("This month is built – inputs are locked. Re-open to edit.")
        if kind in MONTH_KINDS:
            if not self.month_info:
                raise StoreError("There is no month yet – create one first.")
            return self.store.scope(self.month)
        return self.store.master

    def set_month(self, ym) -> None:
        self.month = tuple(ym) if ym else None
        self.stack.clear()  # undo steps belong to one month
        self.month_changed.emit()

    def set_store(self, store: Store) -> None:
        self.store = store
        self.stack.clear()
        self.alias.clear()
        ms = store.list_months()
        self.month = ms[-1].key if ms else None
        self.month_changed.emit()

    def reopen_month(self) -> None:
        if self.month:
            self.store.reopen_month(self.month)
            self.stack.clear()
            self.month_changed.emit()
            self.saved.emit("Month re-opened")

    def rid(self, uid):
        """Current id of a record after undo/redo re-created it under a new id."""
        seen = 0
        while uid in self.alias and seen < 100:
            uid = self.alias[uid]
            seen += 1
        return uid

    def _remap(self, fields: dict) -> dict:
        out = dict(fields)
        for k in _REF_FIELDS:
            if out.get(k) is not None:
                out[k] = self.rid(out[k])
        for k in ("allowed", "pref"):
            if k in out:
                out[k] = [self.rid(x) for x in out[k]]
        return out

    def _default_confirm(self, text: str) -> bool:
        return yes_no_box(None, tr("Please confirm"), text)

    # ------------------------------------------------------------------ notifications
    def notify(self, kinds, origin=None, text: str = "Saved") -> None:
        self.changed.emit(frozenset(kinds), origin)
        self.saved.emit(tr(text))

    def error(self, text: str) -> None:
        self.message.emit(text, "error")

    # ------------------------------------------------------------------ writes (each = store write + undo command)
    def update(self, kind: str, uid: int, /, origin=None, **fields):
        """Change fields of a record.  Raises :class:`StoreError` (e.g. DuplicateError) without changing anything."""
        uid = self.rid(uid)
        w = self.writer(kind)
        old = self.view().get(kind, uid)
        old_vals = {k: copy.deepcopy(getattr(old, k)) for k in fields}
        if all(old_vals[k] == v for k, v in fields.items()):
            return old
        rec = w.update(kind, uid, **fields)
        self.stack.push(_UpdateCmd(self, kind, uid, old_vals, dict(fields)))
        self.notify({kind}, origin)
        return rec

    def update_settings(self, origin=None, **fields):
        w = self.writer()
        old = self.view().settings()
        old_vals = {k: getattr(old, k) for k in fields}
        if all(old_vals[k] == v for k, v in fields.items()):
            return old
        rec = w.update_settings(**fields)
        self.stack.push(_SettingsCmd(self, old_vals, dict(fields)))
        self.notify({"settings"}, origin)
        return rec

    def add(self, kind: str, /, origin=None, **fields):
        w = self.writer(kind)
        rec = w.add(kind, **fields)
        self.stack.push(_AddCmd(self, kind, record_fields(rec), rec.uid))
        self.notify({kind}, origin)
        return rec

    def delete(self, kind: str, uid: int, /, origin=None) -> None:
        """Delete (blocked with :class:`InUseError` while the record is in use)."""
        uid = self.rid(uid)
        w = self.writer(kind)
        old = self.view().get(kind, uid)
        w.delete(kind, uid)
        self.stack.push(_DeleteCmd(self, kind, record_fields(old), uid))
        kinds = ({kind} | ({"employee"} if kind in ("shift", "reception") else set())
                 | ({"target"} if kind == "reception" else set())
                 | (MONTH_KINDS if kind in ("employee", "shift", "reception") else set()))
        self.notify(kinds, origin)


# ----------------------------------------------------------------------------- commands
class _Cmd(QUndoCommand):
    def __init__(self, ctx: AppContext, text: str) -> None:
        super().__init__(text)
        self.ctx = ctx
        self._first = True
        self.kinds: set[str] = set()
        self.t = time.monotonic()

    def redo(self) -> None:
        if self._first:  # the write was already done by AppContext (so errors reach the caller)
            self._first = False
            return
        self._run(self.do)

    def undo(self) -> None:
        self._run(self.undo_)

    def _run(self, fn) -> None:
        try:
            fn()
        except StoreError as exc:
            self.ctx.error(tr('Could not {v0}: {exc}', v0=self.text().lower(), exc=tr_msg(str(exc))))
        self.ctx.notify(self.kinds or ALL_KINDS, None, tr("Saved (undo/redo)"))

    def do(self) -> None: ...
    def undo_(self) -> None: ...


class _UpdateCmd(_Cmd):
    def __init__(self, ctx, kind, uid, old, new) -> None:
        super().__init__(ctx, tr("Edit {kind}", kind=tr(kind)))
        self.kind, self.uid, self.old, self.new = kind, uid, old, new
        self.kinds = {kind}

    def do(self) -> None:
        self.ctx.writer(self.kind).update(self.kind, self.ctx.rid(self.uid), **self.ctx._remap(self.new))

    def undo_(self) -> None:
        self.ctx.writer(self.kind).update(self.kind, self.ctx.rid(self.uid), **self.ctx._remap(self.old))

    def id(self) -> int:  # noqa: A003
        return hash((self.kind, self.uid, tuple(sorted(self.new)))) & 0x7FFFFFFF

    def mergeWith(self, other) -> bool:  # noqa: N802
        if (not isinstance(other, _UpdateCmd) or (other.kind, other.uid) != (self.kind, self.uid)
                or set(other.new) != set(self.new) or other.t - self.t > _MERGE_SECONDS):
            return False
        self.new = dict(other.new)
        self.t = other.t
        return True


class _SettingsCmd(_Cmd):
    def __init__(self, ctx, old, new) -> None:
        super().__init__(ctx, "Edit settings")
        self.old, self.new = old, new
        self.kinds = {"settings"}

    def do(self) -> None:
        self.ctx.writer().update_settings(**self.new)

    def undo_(self) -> None:
        self.ctx.writer().update_settings(**self.old)

    def id(self) -> int:  # noqa: A003
        return hash(("settings", tuple(sorted(self.new)))) & 0x7FFFFFFF

    def mergeWith(self, other) -> bool:  # noqa: N802
        if not isinstance(other, _SettingsCmd) or set(other.new) != set(self.new) or other.t - self.t > _MERGE_SECONDS:
            return False
        self.new = dict(other.new)
        self.t = other.t
        return True


class _AddCmd(_Cmd):
    def __init__(self, ctx, kind, fields, uid) -> None:
        super().__init__(ctx, tr("Add {kind}", kind=tr(kind)))
        self.kind, self.fields, self.uid0 = kind, fields, uid
        self.kinds = {kind}

    def do(self) -> None:
        rec = self.ctx.writer(self.kind).add(self.kind, **self.ctx._remap(self.fields))
        self.ctx.alias[self.uid0] = rec.uid

    def undo_(self) -> None:
        self.ctx.writer(self.kind).delete(self.kind, self.ctx.rid(self.uid0))


class _DeleteCmd(_Cmd):
    def __init__(self, ctx, kind, fields, uid) -> None:
        super().__init__(ctx, tr("Delete {kind}", kind=tr(kind)))
        self.kind, self.fields, self.uid0 = kind, fields, uid
        self.kinds = {kind, "employee", "target"} | set(MONTH_KINDS)

    def do(self) -> None:
        self.ctx.writer(self.kind).delete(self.kind, self.ctx.rid(self.uid0))

    def undo_(self) -> None:
        rec = self.ctx.writer(self.kind).add(self.kind, **self.ctx._remap(self.fields))
        self.ctx.alias[self.uid0] = rec.uid
