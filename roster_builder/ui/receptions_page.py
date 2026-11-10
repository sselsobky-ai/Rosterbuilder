"""Receptions screen: add, rename and delete reception desks."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from ..i18n import count_phrase, tr, tr_msg
from ..store import DuplicateError, InUseError, StoreError
from .base import Page
from .widgets import set_state


def in_use_message(what: str, name: str, exc: Exception, hint: str) -> str:
    """Friendly text for a delete that the store refused."""
    return tr("'{name}' can't be deleted because it is still in use. {v0}. {hint}", name=name, v0=tr_msg(str(exc)).rstrip("."), hint=tr(hint))


class ReceptionsPage(Page):
    key = "receptions"
    title = "Receptions"
    subtitle = "The desks that need staff. Renaming is safe: employees, coverage and shifts keep pointing at the right desk."
    watches = frozenset({"reception", "employee", "target"})

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.body.addLayout(row, 1)

        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setMinimumWidth(300)
        self.list.setMaximumWidth(380)
        self.list.currentItemChanged.connect(self._selected)
        left.addWidget(self.list, 1)
        self.add_btn = QPushButton(tr("Add reception"))
        self.add_btn.setProperty("primary", True)
        self.add_btn.clicked.connect(self.add_reception)
        left.addWidget(self.add_btn)
        row.addLayout(left)

        self.card = QFrame()
        self.card.setObjectName("card")
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(16, 16, 16, 16)
        cl.setSpacing(8)
        cl.addWidget(QLabel(tr("Name")))
        self.name = QLineEdit()
        self.name.setToolTip(tr("The reception's name as shown everywhere in the app."))
        self.name.setMaxLength(40)
        self.name.textEdited.connect(lambda _t: self._name_debounce.trigger())
        self.name.editingFinished.connect(self.flush)
        cl.addWidget(self.name)
        self.name_err = QLabel("")
        self.name_err.setProperty("role", "error")
        self.name_err.hide()
        cl.addWidget(self.name_err)
        self.usage = QLabel("")
        self.usage.setProperty("role", "hint")
        self.usage.setWordWrap(True)
        cl.addWidget(self.usage)
        cl.addSpacing(8)
        self.delete_btn = QPushButton(tr("Delete reception"))
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(self.delete_selected)
        cl.addWidget(self.delete_btn, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(self.card, 1, Qt.AlignmentFlag.AlignTop)
        self._name_debounce = self.debounce(self._commit_name, 500)
        self._current: int | None = None

    # -- data
    def reload(self, select: int | None = None) -> None:
        keep = select if select is not None else self._current
        self.list.blockSignals(True)
        self.list.clear()
        for r in self.ctx.view().list_receptions():
            it = QListWidgetItem(r.name)
            it.setData(Qt.ItemDataRole.UserRole, r.uid)
            self.list.addItem(it)
            if r.uid == keep:
                self.list.setCurrentItem(it)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        self._show_current()

    def apply_read_only(self, ro: bool) -> None:
        for w in (self.add_btn, self.name, self.delete_btn):
            w.setEnabled(not ro and (w is self.add_btn or self._current is not None))

    def _show_current(self) -> None:
        it = self.list.currentItem()
        self._current = it.data(Qt.ItemDataRole.UserRole) if it else None
        view = self.ctx.view()
        self.name.blockSignals(True)
        self.name.setText(it.text() if it else "")
        self.name.blockSignals(False)
        set_state(self.name, None)
        self.name_err.hide()
        if it is None:
            self.usage.setText(tr("No reception yet. Click 'Add reception' to create the first one."))
            self.name.setEnabled(False)
            self.delete_btn.setEnabled(False)
            return
        emps = [e for e in view.list_employees() if e.home_uid == self._current]
        helpers = [e for e in view.list_employees() if e.also_uid == self._current]
        rows = [t for t in view.list_targets() if t.rec_uid == self._current]
        self.usage.setText(tr("{a} work here, {b} can help out, {c}.", a=count_phrase(len(emps), "employee"), b=len(helpers),
                              c=count_phrase(len(rows), "coverage row")))
        ro = self.ctx.read_only
        self.name.setEnabled(not ro)
        self.delete_btn.setEnabled(not ro)

    def focus_record(self, record_id, field: str, message: str = "") -> None:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == record_id:
                self.list.setCurrentRow(i)
        set_state(self.name, message or "Please check this name.")

    def _selected(self, *_a) -> None:
        self.flush()
        self._show_current()
        self.show_message("")

    # -- actions
    def add_reception(self) -> None:
        names = {r.name.upper() for r in self.ctx.view().list_receptions()}
        n, name = 1, tr("New reception")
        while name.upper() in names:
            n += 1
            name = tr("New reception {n}", n=n)
        try:
            rec = self.ctx.add("reception", origin=self, name=name)
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self.reload(select=rec.uid)
        self.name.setFocus()
        self.name.selectAll()

    def _commit_name(self) -> None:
        if self._loading or self._current is None:
            return
        name = self.name.text().strip()
        if not name:
            set_state(self.name, tr("A reception needs a name."))
            self.name_err.setText(tr("A reception needs a name."))
            self.name_err.show()
            return
        try:
            self.ctx.update("reception", self._current, origin=self, name=name)
        except DuplicateError:
            msg = tr("There is already a reception called '{name}'.", name=name)
            set_state(self.name, msg)
            self.name_err.setText(msg)
            self.name_err.show()
            return
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        set_state(self.name, None)
        self.name_err.hide()
        it = self.list.currentItem()
        if it:
            it.setText(name)

    def delete_selected(self) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        self.flush()
        uid = it.data(Qt.ItemDataRole.UserRole)
        name = it.text()
        try:
            self.ctx.delete("reception", uid, origin=self)
        except InUseError as exc:
            self.show_message(in_use_message("reception", name, exc,
                                             tr("Move its employees to another reception and remove its coverage rows first.")))
            return
        except StoreError as exc:
            self.show_message(tr_msg(str(exc)))
            return
        self._current = None
        self.reload()
        self.show_message("")
