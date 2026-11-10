"""Orchestration shared by the GUI and the CLI (no tkinter here, fully unit-testable).

``run_input_check_file`` -> :class:`CheckOutcome`; ``run_build`` -> :class:`BuildOutcome`.
Neither raises for expected problems: errors come back in ``message`` / ``error``.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from . import history as hist
from .inputs_from_store import build_inputs, previous_month_key, roster_from_dict, roster_to_dict
from .roster_edit import apply_locks, locked_cells
from .checks import CheckReport, CheckRow, run_checks
from .daysoff import plan_days_off
from .io_xlsx import load_inputs
from .model import Issue, Roster
from .report import export_pdf, write_roster_workbook
from .solver import solve
from .validate import copy_target, run_input_check, write_highlights

ProgressCb = Callable[[str], None]


@dataclass
class CheckOutcome:
    """Result of the input check."""

    findings: list[Issue] = field(default_factory=list)
    copy_path: Path | None = None  # highlighted copy (if requested)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class BuildOutcome:
    """Result of :func:`run_build`."""

    ok: bool = False
    message: str = ""
    status: str = ""
    error: str = ""  # unexpected exception text (empty for ordinary "no roster")
    findings: list[Issue] = field(default_factory=list)  # input-check findings
    issues: list[Issue] = field(default_factory=list)  # solver conflicts / notes
    roster_path: Path | None = None
    pdf_path: Path | None = None
    history_path: Path | None = None
    history_used: bool = False  # a previous-month record was applied
    n_shifts: int = 0
    n_check: int = 0
    summary: list[CheckRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # --- stored months (run_build_month)
    year: int = 0
    month: int = 0
    roster: Roster | None = None
    report: CheckReport | None = None
    inputs: object = None  # the RosterInputs used
    finding_texts: list[str] = field(default_factory=list)  # input-check findings, text for the user
    issue_texts: list[str] = field(default_factory=list)  # solver conflicts / notes, text for the user
    n_violations: int = 0  # rows of the checks that need attention
    cancelled: bool = False  # the user stopped the build; nothing was saved
    n_locked: int = 0  # pinned manual edits that were fed into the solver


def _say(cb: ProgressCb | None, msg: str) -> None:
    if cb:
        try:
            cb(msg)
        except Exception:  # a broken progress callback must never kill the build
            pass


def check_input_file(input_path, out_path=None, progress_cb: ProgressCb | None = None) -> CheckOutcome:
    """Run the input check; with ``out_path`` also write the highlighted copy (never touches the input)."""
    out = CheckOutcome()
    try:
        _say(progress_cb, "Reading the input sheet ...")
        inp = load_inputs(str(input_path))
        out.findings = list(inp.issues) + list(run_input_check(inp))
        if out_path is not None:
            real, _ = copy_target(input_path, out_path)
            if real.resolve() == Path(str(input_path)).resolve():
                out.error = "The output file is the input file; choose another name."
                return out
            write_highlights(str(input_path), str(out_path), out.findings)
            out.copy_path = real
    except Exception as exc:  # noqa: BLE001 - reported to the user
        out.error = f"{type(exc).__name__}: {exc}"
    return out


def run_build(input_path, out_dir=None, time_limit: float = 60, use_history: bool = True,
              progress_cb: ProgressCb | None = None, *, workers: int = 8, history_dir=None,
              out_xlsx=None, out_pdf=None, make_pdf: bool = True) -> BuildOutcome:
    """Check the input, solve the month, write roster workbook (+ PDF) and the history record.

    ``out_dir`` defaults to the input's folder; files are ``roster_YYYY-MM.xlsx`` / ``.pdf`` unless
    ``out_xlsx`` / ``out_pdf`` are given. With ``use_history`` the previous month's record is read
    from ``history_dir`` (default ``<output folder>/history``) and, after a successful build, this
    month's record is saved there. A failed build writes no files.
    """
    res = BuildOutcome()
    try:
        src = Path(str(input_path))
        _say(progress_cb, "Reading the input sheet ...")
        inp = load_inputs(str(src))
        res.findings = list(inp.issues) + list(run_input_check(inp))
        _say(progress_cb, f"Input check: {len(res.findings)} finding(s). Planning days off ...")
        folder = Path(str(out_dir)) if out_dir else (Path(str(out_xlsx)).parent if out_xlsx else src.parent)
        hdir = Path(str(history_dir)) if history_dir else folder / "history"
        prev = None
        if use_history and inp.settings.first_day is not None:
            prev = hist.load_previous(hdir, inp.settings.first_day)
            if prev is not None and not prev.is_previous_of(inp.settings.first_day):
                prev = None
            res.history_used = prev is not None
            _say(progress_cb, "Using last month's history." if prev else "No history for the previous month.")
        blocks, _ = plan_days_off(inp, history=prev)
        _say(progress_cb, f"Solving (time limit {time_limit:g} s) ...")
        roster = solve(inp, blocks, time_limit=time_limit, workers=workers, history=prev)
        res.status = roster.status
        res.issues = list(roster.issues)
        if roster.status not in ("OPTIMAL", "FEASIBLE") or not roster.assignments:
            res.message = f"No roster: solver status {roster.status}. Fix the listed conflicts in the INPUT sheet."
            return res
        _say(progress_cb, "Checking the roster ...")
        report = run_checks(inp, roster)
        month = inp.settings.first_day
        xlsx = Path(str(out_xlsx)) if out_xlsx else folder / f"roster_{month:%Y-%m}.xlsx"
        if xlsx.resolve() == src.resolve():
            res.message = "The output file is the input file; choose another name."
            return res
        xlsx.parent.mkdir(parents=True, exist_ok=True)
        _say(progress_cb, "Writing the roster workbook ...")
        write_roster_workbook(inp, roster, report, xlsx)
        res.roster_path = xlsx
        if make_pdf:
            pdf = Path(str(out_pdf)) if out_pdf else xlsx.with_suffix(".pdf")
            _say(progress_cb, "Writing the PDF ...")
            export_pdf(inp, roster, pdf)
            res.pdf_path = pdf
        if use_history:
            res.history_path = hist.save_history(hist.build_record(inp, roster, prev), hdir)
        res.n_shifts = len(roster.assignments)
        res.n_check = report.n_check
        res.summary = list(report.summary)
        res.notes = list(report.notes)
        res.ok = True
        res.message = f"Roster built ({roster.status}), {res.n_shifts} shifts, {res.n_check} check(s) need attention."
        _say(progress_cb, "Done.")
    except Exception as exc:  # noqa: BLE001 - the GUI shows it in a message box
        res.ok = False
        res.error = f"{type(exc).__name__}: {exc}"
        res.message = f"Unexpected error: {res.error}"
        res.notes.append(traceback.format_exc())
    return res


# ----------------------------------------------------------------------------- stored months
def previous_history(store, year: int, month: int, inp=None):
    """The stored history record of the month before ``year``-``month`` (``None`` when there is none or it does
    not directly precede the month)."""
    py, pm = previous_month_key(year, month)
    prev = None
    if store.has_month((py, pm)):
        rec = store.get_history((py, pm))
        if rec:
            try:
                prev = hist.MonthHistory.from_json(rec)
            except (KeyError, ValueError, TypeError):
                prev = None
    first = inp.settings.first_day if inp is not None else date(year, month, 1)
    if prev is not None and not prev.is_previous_of(first):
        prev = None
    return prev


def recompute_history(store, year: int, month: int, inp, roster) -> None:
    """Rebuild and store the month's history record from ``roster`` (used after manual edits, so that the next
    month starts from the EDITED last days: rest across the boundary, days-off spacing and fairness counters)."""
    record = hist.build_record(inp, roster, previous_history(store, year, month, inp))
    store.save_history((year, month), record.to_json())


def run_build_month(store, year: int, month: int, time_limit: float = 60, progress_cb: ProgressCb | None = None,
                    *, workers: int = 8, rebuild: bool = False, keep_edits: bool = True, stop=None) -> BuildOutcome:
    """Build the roster of a stored month: inputs from the store -> input check -> solve (with the
    previous month's history from the store) -> checks -> save roster + history in the month.

    A month that is already built is refused (``ok`` False, clear message) unless ``rebuild=True``;
    locked manual edits stay stored across a rebuild and (``keep_edits=True``, the default) are fed into the
    solver as fixed shifts, so the new roster contains them exactly; ``keep_edits=False`` drops them after a
    successful build. ``stop`` (callable returning True) cancels the solve cleanly: ``cancelled`` is True,
    nothing is saved.
    Never raises for expected problems. When no roster is possible ``ok`` is False, nothing is saved and
    ``issue_texts`` lists the conflicts in plain words with the app fields to change (``issues[i].where``).
    """
    res = BuildOutcome(year=year, month=month)
    try:
        if store.get_month((year, month)).status == "built" and not rebuild:
            res.message = (f"The month {year}-{month:02d} is already built and frozen. Re-open it or rebuild it "
                           "on purpose (rebuild=True); the built roster is kept until then.")
            return res
        _say(progress_cb, "Reading the inputs ...")
        inp = build_inputs(store, year, month)
        res.inputs = inp
        res.findings = list(run_input_check(inp))
        res.finding_texts = [f.text_for_user() for f in res.findings]
        _say(progress_cb, f"Input check: {len(res.findings)} finding(s). Planning days off ...")
        prev = previous_history(store, year, month, inp)
        res.history_used = prev is not None
        _say(progress_cb, "Using last month's history." if prev else "No history for the previous month.")
        blocks, _ = plan_days_off(inp, history=prev)
        if keep_edits:
            locks = locked_cells(store, inp, (year, month))
            inp.locked = tuple(locks)
            res.n_locked = len(locks)
            if locks:
                _say(progress_cb, f"Keeping {len(locks)} pinned change(s).")
        _say(progress_cb, f"Solving (time limit {time_limit:g} s) ...")
        roster = solve(inp, blocks, time_limit=time_limit, workers=workers, history=prev, stop=stop)
        res.status = roster.status
        if roster.status == "CANCELLED":
            res.cancelled = True
            res.message = "Build cancelled. Nothing was changed."
            return res
        res.issues = list(roster.issues)
        res.issue_texts = [i.text_for_user() for i in roster.issues]
        if roster.status not in ("OPTIMAL", "FEASIBLE") or not roster.assignments:
            res.message = f"No roster: solver status {roster.status}. Change the fields listed below."
            return res
        _say(progress_cb, "Checking the roster ...")
        report = run_checks(inp, roster)
        res.roster, res.report = roster, report
        record = hist.build_record(inp, roster, prev)
        store.save_roster((year, month), roster_to_dict(inp, roster), record.to_json())
        if not keep_edits:
            store.set_locked((year, month), [])
        res.n_shifts = len(roster.assignments)
        res.n_check = report.n_check
        res.n_violations = report.n_check
        res.summary = list(report.summary)
        res.notes = list(report.notes)
        res.ok = True
        res.message = f"Roster built ({roster.status}), {res.n_shifts} shifts, {res.n_check} check(s) need attention."
        _say(progress_cb, "Done.")
    except Exception as exc:  # noqa: BLE001 - shown to the user in a friendly dialog
        res.ok = False
        res.error = f"{type(exc).__name__}: {exc}"
        res.message = f"Unexpected error: {res.error}"
        res.notes.append(traceback.format_exc())
    return res


def load_month_roster(store, year: int, month: int):
    """``(inputs, roster)`` of a built month, or ``(inputs, None)`` when it has no roster yet.

    The roster is the built one WITH the user's pinned manual edits applied (what the Roster screen shows
    and what the exports use)."""
    inp = build_inputs(store, year, month)
    d = store.get_roster((year, month))
    if not d:
        return inp, None
    roster = roster_from_dict(inp, d)
    apply_locks(inp, roster, locked_cells(store, inp, (year, month)))
    return inp, roster


def export_month(store, year: int, month: int, xlsx_path=None, pdf_path=None) -> dict:
    """Write the stored roster of a built month to an Excel workbook and/or a PDF.
    Returns ``{"xlsx": Path|None, "pdf": Path|None}``; raises ``ValueError`` when the month is not built."""
    inp, roster = load_month_roster(store, year, month)
    if roster is None:
        raise ValueError(f"The month {year}-{month:02d} has no built roster yet.")
    out: dict = {"xlsx": None, "pdf": None}
    if xlsx_path:
        report = run_checks(inp, roster)
        Path(str(xlsx_path)).parent.mkdir(parents=True, exist_ok=True)
        write_roster_workbook(inp, roster, report, xlsx_path)
        out["xlsx"] = Path(str(xlsx_path))
    if pdf_path:
        Path(str(pdf_path)).parent.mkdir(parents=True, exist_ok=True)
        export_pdf(inp, roster, pdf_path)
        out["pdf"] = Path(str(pdf_path))
    return out
