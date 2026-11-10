"""Common base of the pages: title, auto-reload on changes, debounced auto-save, read-only mode."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..i18n import tr
from .context import AppContext


class Debounce(QObject):
    """Calls ``fn`` once, ``ms`` after the last ``trigger()``; ``flush()`` runs a pending call at once."""

    def __init__(self, fn: Callable[[], None], ms: int = 500, parent=None) -> None:
        super().__init__(parent)
        self.fn = fn
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(ms)
        self.timer.timeout.connect(self._fire)

    def trigger(self) -> None:
        self.timer.start()

    def pending(self) -> bool:
        return self.timer.isActive()

    def cancel(self) -> None:
        self.timer.stop()

    def _fire(self) -> None:
        self.fn()

    def flush(self) -> None:
        if self.timer.isActive():
            self.timer.stop()
            self.fn()


class Page(QWidget):
    """A screen.  Subclasses implement ``reload()`` (fill the widgets from ``ctx.view()``) and
    ``apply_read_only(ro)``; they set ``watches`` to the record kinds they show."""

    watches: frozenset = frozenset()
    key = ""
    title = ""
    subtitle = ""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.setObjectName("page")
        self.ctx = ctx
        self._loading = False
        self._debouncers: list[Debounce] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(8)
        head = self.head = QLabel(tr(self.title))
        head.setProperty("role", "title")
        sub = self.sub = QLabel(tr(self.subtitle))
        sub.setProperty("role", "subtitle")
        sub.setWordWrap(True)
        outer.addWidget(head)
        outer.addWidget(sub)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body, 1)
        self.message = QLabel("")
        self.message.setProperty("role", "msg")
        self.message.setWordWrap(True)
        self.message.hide()
        outer.addWidget(self.message)
        ctx.changed.connect(self._ctx_changed)
        ctx.month_changed.connect(self.refresh)

    # -- to override
    def reload(self) -> None: ...
    def apply_read_only(self, ro: bool) -> None: ...

    # -- plumbing
    def debounce(self, fn: Callable[[], None], ms: int = 500) -> Debounce:
        d = Debounce(fn, ms, self)
        self._debouncers.append(d)
        return d

    def flush(self) -> None:
        for d in self._debouncers:
            d.flush()

    def _ctx_changed(self, kinds, origin) -> None:
        if origin is self:
            return
        if origin is None or (set(kinds) & set(self.watches)):
            self.refresh()

    def refresh(self) -> None:
        for d in self._debouncers:
            d.cancel()
        self._loading = True
        try:
            self.reload()
        finally:
            self._loading = False
        self.apply_read_only(self.ctx.read_only)
        self.show_message("")

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))
