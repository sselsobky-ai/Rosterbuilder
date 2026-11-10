# Capabilities

## At a glance

| Area | What Roster Builder does |
|---|---|
| **Input** | Its own screens for every input: settings, receptions, coverage, shifts, employees, leave, fixed shifts and demand. Fields use drop-downs, time and date pickers and weekday chips. Each input is checked as you type and saved automatically, with undo and redo. |
| **Excel import** | One-time **Import from Excel** from the old roster workbook (INPUT sheet), with a report of what was imported and skipped. Re-importing asks before replacing; built months are never touched. |
| **Months** | Each month keeps its own copy of the inputs. **New month** copies the current setup and carries over overlapping leave. A built month is locked until re-opened. |
| **Input check** | Lists mistakes grouped by screen, in plain words. **Show** jumps to the field and outlines it in red; **Fix** applies a safe correction only on click. |
| **Builder** | An optimising solver (Google OR-Tools CP-SAT) builds the whole month in seconds, with progress and Cancel. If no roster is possible, it explains which inputs conflict and links to them. |
| **Roster screen** | A colour-coded grid of days × staff grouped by reception, with a legend, zoom and a coverage strip. Edit any cell and the rules are re-checked at once. Edits are pinned and kept on **Rebuild keeping my changes**. |
| **Checks** | Independent rule checks after every build and every edit: coverage, the men-only window, rest, weekly hours, overtime, leave, fixed shifts, days off, idle days, and whether the overtime priority was respected. |
| **Overtime priority** | Rank staff **1–10**. Overtime of every kind (full duty, partial cover, extended hours) goes strictly in rank order. Ranked staff work their days off as overtime before anyone lower is used. The Settings limits still apply. |
| **History** | Rest and the days-off pattern carry correctly from one month into the next. |
| **Export** | PDF (A3/A4 landscape, colour-coded, legend) and Excel, with sheets for the schedule, checks, hours, coverage and a timeline. Printing is also available. Manual edits are included. |
| **Languages** | English and **Arabic**. Arabic mirrors the whole layout right-to-left, including problem messages and the user guide. Arabic can be typed in any field, and Arabic names print correctly in the PDF. |
| **Data and safety** | Everything is stored locally in `%APPDATA%\RosterBuilder\roster.db`. A backup is made on every start (the last 10 are kept), and Backup / Restore move data between PCs. Errors are logged, never shown as raw text. |
| **Install** | A per-user Windows installer (no admin rights) with Start-menu and desktop shortcuts, an uninstaller that keeps your data, and an English or Arabic setup window. It is built automatically by GitHub Actions. |

## Scheduling rules enforced

**Hard rules.** These are never broken:

1. Coverage targets: at least *min* people every hour, *min men* where required, never more than *max*.
2. The **men-only window** (for example 10 PM–6 AM): only men work in it, extensions included.
3. Leave and fixed shifts are respected exactly.
4. At most one shift per person per day, and only from the person's **allowed shifts**, at their home reception or a reception they can also cover.
5. Minimum **rest** between shifts and inside split shifts, and a maximum shift length.
6. Weekly normal hours stay within the limit. Extra hours count as overtime, within the weekly overtime limit.
7. **Days off:** a fixed block of consecutive days each week, with at least 5 working days between blocks, carried across month boundaries. A day off can only be replaced by overtime, never by a normal shift.
8. Overtime and extensions only for people allowed overtime, and only when the master switch is on. Extensions are whole hours, up to the longest extension.
9. Optionally, keep one free day per week.

**Goals.** The builder optimises these in order:

1. Fill every needed hour.
2. Meet the demand rows.
3. No idle days.
4. Avoid overtime when a colleague without overtime can cover. When overtime is needed, use the **overtime priority ranks** first, then the cheapest kind: an extension, then an overtime shift, then a day off worked.
5. Keep shifts consistent, prefer preferred shifts, and spread overtime, nights and Fridays fairly.

## Technology

Python 3.11+ (tested on 3.14) · PySide6 (Qt) · Google OR-Tools CP-SAT · SQLite · openpyxl · reportlab with arabic-reshaper and python-bidi · PyInstaller · Inno Setup 6 · GitHub Actions (Windows build).
