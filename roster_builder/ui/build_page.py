"""Build page: runs the solver for the selected month in a background thread (progress bar, elapsed time, stage
text, Cancel) and shows the result: a summary card, or the conflict list with jump links."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from ..i18n import count_phrase, tr, tr_msg
from .. import app as engine
from .base import Page
from .checks_panel import FindingRow, compute_findings
from .month_page import month_title

DEFAULT_TIME_LIMIT = 60
_RUNNING: list = []  # threads that are still running: never let Qt destroy a running QThread


class _Worker(QObject):
    progress = Signal(str)
    finished = Signal(object)

    def __init__(self, store, ym, time_limit: float, rebuild: bool, keep: bool, stop: threading.Event) -> None:
        super().__init__()
        self.store, self.ym, self.time_limit, self.rebuild, self.keep, self.stop = store, ym, time_limit, rebuild, keep, stop

    def run(self) -> None:
        try:
            res = engine.run_build_month(self.store, self.ym[0], self.ym[1], time_limit=self.time_limit,
                                         progress_cb=self.progress.emit, rebuild=self.rebuild, keep_edits=self.keep,
                                         stop=self.stop.is_set)
        except Exception as exc:  # noqa: BLE001 - run_build_month does not raise for expected problems
            res = engine.BuildOutcome(message="Unexpected error: " + str(exc), error=str(exc))
        self.finished.emit(res)


def fmt_clock(sec: float) -> str:
    s = int(sec)
    return f"{s // 60}:{s % 60:02d}"


class BuildPage(Page):
    key = "build"
    title = "Build"
    subtitle = "Build this month's roster from your inputs. The build runs in the background and can be cancelled."
    watches = frozenset({"settings", "reception", "target", "shift", "employee", "leave", "fixed", "demand"})

    built = Signal(object)  # BuildOutcome of a successful build

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.outcome = None
        self.thread: QThread | None = None
        self.worker: _Worker | None = None
        self.stop = threading.Event()
        self.t0 = 0.0
        self.limit = float(DEFAULT_TIME_LIMIT)
        self.n_problems = 0

        # ---- the month and the input problems
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        self.month_label = QLabel("")
        self.month_label.setProperty("role", "group")
        self.status_label = QLabel("")
        self.status_label.setProperty("role", "subtitle")
        top.addWidget(self.month_label)
        top.addWidget(self.status_label, 1)
        cl.addLayout(top)
        prob = QHBoxLayout()
        self.problem_badge = QLabel("0")
        self.problem_badge.setProperty("role", "badge-ok")
        self.problem_text = QLabel("")
        self.problem_text.setWordWrap(True)
        self.checks_btn = QPushButton(tr("Open Checks"))
        self.checks_btn.setProperty("flat-link", True)
        self.checks_btn.setToolTip(tr("See the problems found in this month's inputs."))
        self.checks_btn.clicked.connect(lambda: self.ctx.request_open_page.emit("checks"))
        prob.addWidget(self.problem_badge)
        prob.addWidget(self.problem_text, 1)
        prob.addWidget(self.checks_btn)
        cl.addLayout(prob)

        # ---- settings of the run + the big button
        opts = QHBoxLayout()
        opts.setSpacing(12)
        opts.addWidget(QLabel(tr("Time limit")))
        self.time_spin = QSpinBox()
        self.time_spin.setRange(5, 3600)
        self.time_spin.setValue(DEFAULT_TIME_LIMIT)
        self.time_spin.setSuffix(tr(" s"))
        self.time_spin.setToolTip(tr("The longest the solver may search. It stops earlier when it has found the best roster."))
        opts.addWidget(self.time_spin)
        self.keep_check = QCheckBox(tr("Keep my manual changes"))
        self.keep_check.setChecked(True)
        self.keep_check.setToolTip(tr("Pinned cells on the Roster page are kept exactly as you set them. "
                                   "Switch off to build everything from scratch."))
        opts.addWidget(self.keep_check)
        opts.addStretch(1)
        cl.addLayout(opts)
        self.build_btn = QPushButton(tr("Build roster"))
        self.build_btn.setProperty("primary", True)
        self.build_btn.setProperty("big", True)
        self.build_btn.setMinimumHeight(56)
        self.build_btn.clicked.connect(lambda: self.start_build())
        cl.addWidget(self.build_btn)

        # ---- progress
        self.run_box = QFrame()
        rb = QVBoxLayout(self.run_box)
        rb.setContentsMargins(0, 4, 0, 0)
        self.stage_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(14)
        row = QHBoxLayout()
        self.elapsed_label = QLabel("")
        self.elapsed_label.setProperty("role", "subtitle")
        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.setToolTip(tr("Stop the search. Nothing is changed."))
        self.cancel_btn.clicked.connect(self.cancel)
        row.addWidget(self.elapsed_label, 1)
        row.addWidget(self.cancel_btn)
        rb.addWidget(self.stage_label)
        rb.addWidget(self.progress_bar)
        rb.addLayout(row)
        cl.addWidget(self.run_box)
        self.run_box.hide()
        self.body.addWidget(card)

        # ---- result
        self.result_scroll = QScrollArea()
        self.result_scroll.setWidgetResizable(True)
        self.result_host = QWidget()
        self.result_host.setObjectName("pageBody")
        self.result_col = QVBoxLayout(self.result_host)
        self.result_col.setContentsMargins(0, 0, 8, 0)
        self.result_col.setSpacing(8)
        self.result_col.addStretch(1)
        self.result_scroll.setWidget(self.result_host)
        self.body.addWidget(self.result_scroll, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        return self.thread is not None

    def reload(self) -> None:
        mi = self.ctx.month_info
        if mi is None:
            self.month_label.setText(tr("No month yet"))
            self.status_label.setText(tr("Create a month first."))
            self.build_btn.setEnabled(False)
            return
        built = self.ctx.store.get_roster(mi.key) is not None
        self.month_label.setText(month_title(mi.key))
        self.status_label.setText((tr("Built") if mi.status != "draft" else tr("Draft")) + (tr(" – a roster exists") if built else tr(" – no roster yet")))
        self.build_btn.setText(tr("Rebuild roster") if built else tr("Build roster"))
        self.keep_check.setVisible(built or bool(self.ctx.store.get_locked(mi.key)))
        self.build_btn.setEnabled(not self.running)
        try:
            self.n_problems = len(compute_findings(self.ctx.store, mi.key))
        except Exception:  # noqa: BLE001 - the Checks page shows the real error
            self.n_problems = 0
        self.problem_badge.setText(str(self.n_problems))
        self.problem_badge.setProperty("role", "badge" if self.n_problems else "badge-ok")
        self.problem_badge.style().unpolish(self.problem_badge)
        self.problem_badge.style().polish(self.problem_badge)
        self.problem_text.setText(
            tr("{c} in the inputs. You can still build; conflicts the solver cannot resolve are listed with a link to the field.",
               c=count_phrase(self.n_problems, "problem")) if self.n_problems
            else tr("No problems found in this month's inputs."))

    def apply_read_only(self, ro: bool) -> None:  # building is allowed on a built month (it is a rebuild)
        pass

    # ------------------------------------------------------------------ run
    def start_build(self, rebuild: bool | None = None, keep_edits: bool | None = None, confirm: bool = True) -> bool:
        """Start the build of the selected month.  ``rebuild`` defaults to "the month already has a roster"."""
        if self.running:
            return False
        mi = self.ctx.month_info
        if mi is None:
            return False
        has_roster = self.ctx.store.get_roster(mi.key) is not None
        rebuild = has_roster if rebuild is None else rebuild
        keep = self.keep_check.isChecked() if keep_edits is None else keep_edits
        if keep_edits is not None:
            self.keep_check.setChecked(keep_edits)
        if has_roster and confirm:
            n = len(self.ctx.store.get_locked(mi.key))
            msg = (tr("Rebuild this month? The current roster is replaced. ")
                   + (tr("Your {c} will be kept.", c=count_phrase(n, "pinned change")) if (keep and n) else
                      (tr("Your manual changes will be discarded.") if n else "")))
            if not self.ctx.confirm(msg.strip()):
                return False
        self.flush()
        self.limit = float(self.time_spin.value())
        self.stop.clear()
        self.outcome = None
        self._clear_result()
        self.thread = QThread()
        _RUNNING.append(self.thread)
        self.thread.finished.connect(lambda th=self.thread: _RUNNING.remove(th) if th in _RUNNING else None)
        self.worker = _Worker(self.ctx.store, mi.key, self.limit, rebuild, keep, self.stop)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.run_box.show()
        self.stage_label.setText(tr("Starting …"))
        self.progress_bar.setValue(0)
        self.cancel_btn.setEnabled(True)
        self.build_btn.setEnabled(False)
        self.time_spin.setEnabled(False)
        self.keep_check.setEnabled(False)
        self.t0 = time.monotonic()
        self._tick()
        self.timer.start()
        self.thread.start()
        return True

    def shutdown(self, wait_ms: int = 15000) -> None:
        """The window is closing: stop a running build and wait for the worker (nothing is saved by a cancelled build)."""
        if self.thread is not None:
            self.stop.set()
            self.timer.stop()
            self.thread.quit()
            self.thread.wait(wait_ms)

    def cancel(self) -> None:
        """Stop the running build (the solver stops within a fraction of a second; nothing is saved)."""
        if self.running:
            self.stop.set()
            self.stage_label.setText(tr("Cancelling …"))
            self.cancel_btn.setEnabled(False)

    def _on_progress(self, text: str) -> None:
        if not self.stop.is_set():
            self.stage_label.setText(tr_msg(text))

    def _tick(self) -> None:
        el = time.monotonic() - self.t0
        self.elapsed_label.setText(tr('Elapsed {v0}  ·  up to {v1}', v0=fmt_clock(el), v1=fmt_clock(self.limit)))
        self.progress_bar.setValue(int(min(99, 100 * el / max(1.0, self.limit))))

    def _on_finished(self, res) -> None:
        self.timer.stop()
        th = self.thread
        self.thread = None
        if th is not None:
            th.quit()
            th.wait(5000)
        self.worker = None
        self.run_box.hide()
        self.time_spin.setEnabled(True)
        self.keep_check.setEnabled(True)
        self.outcome = res
        self._show_result(res)
        if res.ok:
            self.ctx.month_changed.emit()  # every page (Roster, month bar …) reloads from the store
            self.ctx.saved.emit("Roster built")
        self.reload()
        self.build_btn.setEnabled(True)
        if res.ok:
            self.built.emit(res)

    # ------------------------------------------------------------------ result card
    def _clear_result(self) -> None:
        while self.result_col.count() > 1:
            it = self.result_col.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _add(self, w: QWidget) -> None:
        self.result_col.insertWidget(self.result_col.count() - 1, w)

    def _show_result(self, res) -> None:
        self._clear_result()
        card = QFrame()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        head = QLabel()
        head.setProperty("role", "group")
        v.addWidget(head)
        self.result_card = card
        if res.cancelled:
            head.setText(tr("Build cancelled"))
            t = QLabel(tr("Nothing was changed. The roster you had before is still there."))
            t.setProperty("role", "subtitle")
            v.addWidget(t)
            self._add(card)
            return
        if res.ok:
            self._fill_ok(v, head, res)
            self._add(card)
            return
        # ---- no roster
        head.setText(tr("No roster could be built"))
        why = QLabel(tr_msg(res.message) if (res.error or not res.issues) else
                     tr("The rules below cannot all be met. Change the field behind a line (Show opens it) and build again."))
        why.setWordWrap(True)
        why.setProperty("role", "subtitle")
        v.addWidget(why)
        self._add(card)
        self.conflict_rows: list[FindingRow] = []
        lines = list(res.issues)
        for issue in lines:
            row = FindingRow(issue)
            row.clicked.connect(self._open_issue)
            self._add(row)
            self.conflict_rows.append(row)

    def _fill_ok(self, v: QVBoxLayout, head: QLabel, res) -> None:
        rep = res.report
        head.setText(tr("Roster built"))
        ot_total = sum(s.get("ot", 0.0) for s in rep.emp_stats.values()) if rep else 0.0
        stats = QHBoxLayout()
        stats.setSpacing(24)
        for big, small in ((str(res.n_shifts), tr("shifts")), (tr("{h:g} h", h=ot_total), tr("overtime")),
                           (str(res.n_check), tr("checks need attention"))):
            col = QVBoxLayout()
            col.setSpacing(0)
            n = QLabel(big)
            n.setProperty("role", "count")
            s = QLabel(small)
            s.setProperty("role", "subtitle")
            col.addWidget(n)
            col.addWidget(s)
            col.addStretch(1)
            stats.addLayout(col)
        stats.addStretch(1)
        self.open_btn = QPushButton(tr("Open roster"))
        self.open_btn.setProperty("primary", True)
        self.open_btn.setMinimumHeight(40)
        self.open_btn.clicked.connect(lambda: self.ctx.request_open_page.emit("roster"))
        stats.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(stats)
        if rep and rep.ot_by_rank:
            parts = []
            for r in sorted(rep.ot_by_rank, key=lambda x: (x is None, x or 0)):
                parts.append(tr("{k}: {h:g} h", k=tr("rank {r}", r=r) if r is not None else tr("unranked"), h=rep.ot_by_rank[r]))
            t = QLabel(tr("Overtime per priority group  –  ") + ";  ".join(parts))
            t.setWordWrap(True)
            v.addWidget(t)
        if res.n_locked:
            t = QLabel(tr("{c} kept.", c=count_phrase(res.n_locked, "pinned change")))
            t.setProperty("role", "subtitle")
            v.addWidget(t)
        bad = [r for r in (res.summary or []) if r.status == "CHECK"]
        notes = [r for r in (res.summary or []) if r.status in ("WARN", "INFO")]
        if notes:
            t = QLabel(tr("Manual changes:  ") + ";  ".join(f"{tr_msg(r.name)} ({r.count:g})" for r in notes))
            t.setWordWrap(True)
            t.setProperty("role", "subtitle")
            v.addWidget(t)
        if bad:
            t = QLabel(tr("Needs attention:  ") + ";  ".join(f"{tr_msg(r.name)} ({r.count:g})" for r in bad))
            t.setWordWrap(True)
            t.setProperty("role", "warn")
            v.addWidget(t)
        else:
            t = QLabel(tr("✓  Every rule check passes."))
            t.setProperty("role", "ok")
            v.addWidget(t)
        if res.history_used:
            t = QLabel(tr("Rest and days off continue from last month's roster."))
            t.setProperty("role", "subtitle")
            v.addWidget(t)

    def _open_issue(self, issue) -> None:
        if issue.where:
            self.ctx.request_jump.emit(issue.where[0], issue.text_for_user())


__all__ = ["BuildPage"]
