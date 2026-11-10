"""Input check panel: runs the input check on the selected month, groups the findings by screen, jumps to the
field of a finding and offers the safe auto-fix as a button (never applied without a click)."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..i18n import count_phrase, tr, tr_msg
from ..inputs_from_store import build_inputs
from ..locate import SCREEN_LABEL, FieldSet
from ..store import StoreError
from ..validate import run_input_check
from .base import Page

SCREEN_ORDER = ["settings", "receptions", "shifts", "coverage", "employees", "leave", "fixed_shifts", "demand"]
KIND_OF_SCREEN = {"receptions": "reception", "shifts": "shift", "coverage": "target", "employees": "employee",
                  "leave": "leave", "fixed_shifts": "fixed", "demand": "demand"}
SLOW_SECONDS = 0.3  # a check slower than this runs in a worker thread from then on
DEBOUNCE_MS = 1000


class _Done(QObject):
    done = Signal(int, object, float)  # generation, issues (or an Exception), seconds


class _Job(QRunnable):
    def __init__(self, store, ym, gen: int, sig: _Done) -> None:
        super().__init__()
        self.store, self.ym, self.gen, self.sig = store, ym, gen, sig

    def run(self) -> None:
        t = time.perf_counter()
        try:
            res: object = compute_findings(self.store, self.ym)
        except Exception as exc:  # noqa: BLE001 - reported in the panel, not as a crash
            res = exc
        self.sig.done.emit(self.gen, res, time.perf_counter() - t)


def compute_findings(store, ym) -> list:
    """The input-check findings of the stored month ``ym`` (field locators and fixes included)."""
    return list(run_input_check(build_inputs(store, *ym)))


class FindingRow(QFrame):
    """One finding: the sentence, a Show button and (when a safe auto-fix exists) a Fix button."""

    clicked = Signal(object)
    fix_clicked = Signal(object)

    def __init__(self, issue) -> None:
        super().__init__()
        self.issue = issue
        self.setObjectName("finding")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.text = QLabel(issue.text_for_user())
        self.text.setWordWrap(True)
        self.text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        col.addWidget(self.text)
        self.where = QLabel(issue.where_text())  # small grey second line: where to look
        self.where.setProperty("role", "hint")
        self.where.setWordWrap(True)
        self.where.setVisible(bool(issue.where))
        self.where.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        col.addWidget(self.where)
        self.fix_text = QLabel((tr("Suggested fix: ") + tr_msg(issue.fix or "")) if issue.fix_set else "")  # (the sentence above already says it)
        self.fix_text.hide()
        h.addLayout(col, 1)
        self.show_btn = QPushButton(tr("Show"))
        self.show_btn.setToolTip(tr("Open the page and highlight the field."))
        self.show_btn.setEnabled(bool(issue.where))
        self.show_btn.clicked.connect(lambda: self.clicked.emit(self.issue))
        h.addWidget(self.show_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.fix_btn = QPushButton(tr("Fix"))
        self.fix_btn.setProperty("primary", True)
        self.fix_btn.setToolTip((tr_msg(issue.fix)[:1].upper() + tr_msg(issue.fix)[1:] + ". " if issue.fix else "") + tr("You can undo it with Ctrl+Z."))
        self.fix_btn.setVisible(bool(issue.fix_set))
        self.fix_btn.clicked.connect(lambda: self.fix_clicked.emit(self.issue))
        h.addWidget(self.fix_btn, 0, Qt.AlignmentFlag.AlignTop)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton and self.issue.where:
            self.clicked.emit(self.issue)
        super().mousePressEvent(ev)


class ChecksPage(Page):
    key = "checks"
    title = "Checks"
    subtitle = ("Problems found in this month's inputs, before you build. Click a finding to open the page and see the field; "
                "'Fix' applies a safe correction and can be undone.")
    watches = frozenset({"settings", "reception", "target", "shift", "employee", "leave", "fixed", "demand"})

    count_changed = Signal(int)

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.issues: list = []
        self.rows: list[FindingRow] = []
        self.count = 0
        self.last_seconds = 0.0
        self.checked_at = ""
        self._gen = 0
        self._busy = False
        self._use_thread = False
        self._sig = _Done(self)
        self._sig.done.connect(self._finished)
        top = QHBoxLayout()
        self.run_btn = QPushButton(tr("Check inputs"))
        self.run_btn.setProperty("primary", True)
        self.run_btn.setToolTip(tr("Check this month's inputs now. This also happens by itself a second after every change."))
        self.run_btn.clicked.connect(lambda: self.run_now())
        top.addWidget(self.run_btn)
        self.status = QLabel("")
        self.status.setProperty("role", "subtitle")
        top.addWidget(self.status, 1)
        self.body.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self.host.setObjectName("pageBody")
        self.col = QVBoxLayout(self.host)
        self.col.setContentsMargins(0, 0, 8, 0)
        self.col.setSpacing(6)
        self.col.addStretch(1)
        self.scroll.setWidget(self.host)
        self.body.addWidget(self.scroll, 1)
        self._debounce = self.debounce(lambda: self.run_now(auto=True), DEBOUNCE_MS)
        ctx.changed.connect(lambda _k, _o: self._debounce.trigger())

    # ------------------------------------------------------------------ running
    def reload(self) -> None:
        # the selected month changed (or the window was refreshed): check it soon
        self._render()
        self._debounce.trigger()

    def apply_read_only(self, ro: bool) -> None:
        for r in self.rows:
            r.fix_btn.setEnabled(not ro)
            r.fix_btn.setToolTip(tr("This month is built and locked. Re-open it to apply fixes.") if ro else
                                 (r.issue.fix + ". " if r.issue.fix else "") + tr("You can undo it with Ctrl+Z."))

    def run_now(self, auto: bool = False) -> None:
        """Run the check.  ``auto`` runs in a worker thread when the last run was slow; otherwise it runs at once."""
        self._debounce.cancel()
        ym = self.ctx.month if self.ctx.month_info else None
        if ym is None:
            self.issues = []
            self._set_count(0)
            self._render()
            return
        self._gen += 1
        if auto and self._use_thread:
            self._busy = True
            self.status.setText(tr("Checking …"))
            QThreadPool.globalInstance().start(_Job(self.ctx.store, ym, self._gen, self._sig))
            return
        t = time.perf_counter()
        try:
            res: object = compute_findings(self.ctx.store, ym)
        except Exception as exc:  # noqa: BLE001
            res = exc
        self._finished(self._gen, res, time.perf_counter() - t)

    def _finished(self, gen: int, res, seconds: float) -> None:
        if gen != self._gen:  # an older run that was overtaken
            return
        self._busy = False
        self.last_seconds = seconds
        self._use_thread = seconds > SLOW_SECONDS
        if isinstance(res, Exception):
            self.issues = []
            self.status.setText(tr('The check could not run: {res}', res=res))
            self._set_count(0)
            self._render()
            return
        self.issues = list(res)
        from datetime import datetime

        self.checked_at = f"{datetime.now():%H:%M:%S}"
        self._set_count(len(self.issues))
        self._render()

    def _set_count(self, n: int) -> None:
        self.count = n
        self.count_changed.emit(n)

    # ------------------------------------------------------------------ view
    def _group_of(self, issue) -> str:
        return issue.where[0].screen if issue.where else "general"

    def _render(self) -> None:
        while self.col.count() > 1:
            it = self.col.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.rows = []
        mi = self.ctx.month_info
        if mi is None:
            self.status.setText(tr("There is no month yet - create one to check its inputs."))
            self._info(tr("Nothing to check yet."))
            return
        if self._busy:
            return
        n = len(self.issues)
        when = tr(" Checked at {checked_at}.", checked_at=self.checked_at) if self.checked_at else ""
        if not n:
            self.status.setText(tr("No problems found.") + when)
            self._info(tr("✓  No problems found in this month's inputs."), ok=True)
            return
        self.status.setText(count_phrase(n, "finding") + "." + when)
        groups: dict[str, list] = {}
        for i in self.issues:
            groups.setdefault(self._group_of(i), []).append(i)
        order = [g for g in SCREEN_ORDER if g in groups] + [g for g in groups if g not in SCREEN_ORDER]
        pos = 0
        for g in order:
            head = QLabel(f"{tr(SCREEN_LABEL.get(g, 'Other'))}  ({len(groups[g])})")
            head.setProperty("role", "group")
            self.col.insertWidget(pos, head)
            pos += 1
            for issue in groups[g]:
                row = FindingRow(issue)
                row.clicked.connect(self.open_finding)
                row.fix_clicked.connect(self.apply_fix)
                self.col.insertWidget(pos, row)
                pos += 1
                self.rows.append(row)
        self.apply_read_only(self.ctx.read_only)

    def _info(self, text: str, ok: bool = False) -> None:
        lab = QLabel(text)
        lab.setProperty("role", "ok" if ok else "subtitle")
        lab.setWordWrap(True)
        self.col.insertWidget(0, lab)

    # ------------------------------------------------------------------ actions
    def open_finding(self, issue) -> None:
        """Go to the page of the first locator of the finding and mark the field."""
        if not issue.where:
            return
        self.ctx.request_jump.emit(issue.where[0], issue.text_for_user())

    def apply_fix(self, issue) -> bool:
        """Apply the finding's safe auto-fix through the store (one undo step), then check again."""
        if not issue.fix_set or self.ctx.read_only:
            return False
        ctx = self.ctx
        ctx.stack.beginMacro("Apply fix")
        try:
            for fs in issue.fix_set:
                self._apply_fieldset(fs)
            ok = True
        except (StoreError, KeyError) as exc:
            self.show_message(tr('The fix could not be applied: {exc}', exc=tr_msg(str(exc))))
            ok = False
        finally:
            ctx.stack.endMacro()
        if ok:
            self.show_message("")
        self.run_now()
        return ok

    def _apply_fieldset(self, fs: FieldSet) -> None:
        loc = fs.locator
        ctx = self.ctx
        if loc.screen == "settings":
            ctx.update_settings(**{loc.field: fs.value})
            return
        kind = KIND_OF_SCREEN[loc.screen]
        uid = loc.record_id
        if fs.op == "add":
            view = ctx.view()
            shift = next((s for s in view.list_shifts() if s.code.strip().upper() == str(fs.value).strip().upper()), None)
            if shift is None:
                raise KeyError(f"shift '{fs.value}' no longer exists")
            cur = list(getattr(view.get(kind, uid), loc.field))
            if shift.uid not in cur:
                cur.append(shift.uid)
            ctx.update(kind, uid, **{loc.field: cur})
            return
        ctx.update(kind, uid, **{loc.field: fs.value})
