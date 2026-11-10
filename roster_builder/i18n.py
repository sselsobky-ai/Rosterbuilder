"""English / Arabic text support (plain Python; Qt is only touched by :func:`apply_language`).

* ``tr(text, **kw)``      the label / sentence of the screen: ``text`` is the English source string and is also the
  catalog key (``i18n_ar.CATALOG``: English -> Modern Standard Arabic).  ``kw`` fills ``{placeholders}``; in Arabic every
  text value is wrapped in Unicode isolates (U+2068 .. U+2069) so a Latin or Arabic name inside a sentence keeps its own
  direction and never reorders the words around it.
* ``tr_msg(text)``        the *problem messages* the engine writes in English (input check, solver conflicts, roster
  checks, manual-edit warnings, import report, store errors): translated sentence by sentence with the regular
  expression templates in ``i18n_ar.TEMPLATES``.
* helpers for counts (``count_phrase``), weekdays / months / dates (``weekday``, ``month_name``, ``strf``), shift codes
  and cell texts (``disp_code``, ``disp_cell``) and the message boxes / fonts / layout direction.

The current language is ``"en"`` (default) or ``"ar"``.  It is stored in ``QSettings`` under ``ui/language``
(:func:`load_language` / :func:`save_language`); the environment variable ``ROSTER_LANG`` overrides it for one run.
Exports (PDF / Excel) are always English; Arabic text typed by the user is exported unchanged.
"""

from __future__ import annotations

import os
import re
from datetime import date

LANG_KEY = "ui/language"
LANGS = ("en", "ar")
ISO_START, ISO_END = "⁨", "⁩"  # first-strong isolate / pop directional isolate
RLM, LRM = "‏", "‎"
ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

_lang = "en"
_cat: dict | None = None


# ----------------------------------------------------------------------------- state
def lang() -> str:
    return _lang


def is_ar() -> bool:
    return _lang == "ar"


def is_rtl() -> bool:
    return _lang == "ar"


def set_language(code: str) -> str:
    """Make ``code`` (``"en"`` / ``"ar"``) the current language (anything else = English).  Returns the code in use."""
    global _lang
    _lang = code if code in LANGS else "en"
    return _lang


def load_language(settings=None) -> str:
    """Language from ``ROSTER_LANG`` or the saved setting (default English); sets and returns it."""
    env = (os.environ.get("ROSTER_LANG") or "").strip().lower()
    if env in LANGS:
        return set_language(env)
    try:
        if settings is None:
            from PySide6.QtCore import QSettings

            settings = QSettings("RosterBuilder", "RosterBuilder")
        return set_language(str(settings.value(LANG_KEY, "en")))
    except Exception:  # noqa: BLE001 - a broken settings store must never stop the app
        return set_language("en")


def save_language(settings, code: str) -> None:
    try:
        settings.setValue(LANG_KEY, code)
        settings.sync()
    except Exception:  # noqa: BLE001
        pass


def _catalog():
    global _cat
    if _cat is None:
        from . import i18n_ar

        _cat = i18n_ar
    return _cat


# ----------------------------------------------------------------------------- isolates / text helpers
_AR_MARKS = re.compile("[\u064B-\u065F\u0670\u0640]")  # tashkeel and tatweel
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})


def fold(s: str) -> str:
    """Search form of a text: lower case, Arabic marks removed, alef / yeh / teh-marbuta variants unified, so that
    ``أحمد`` is found by ``احمد`` and ``فاطِمة`` by ``فاطمه``."""
    return _AR_MARKS.sub("", (s or "")).translate(_AR_MAP).lower()


def has_arabic(s: str) -> bool:
    return bool(ARABIC_RE.search(s or ""))


def iso(s) -> str:
    """Wrap a name (or any foreign-direction text) in isolates - only in Arabic mode and only when it has letters."""
    t = str(s)
    if _lang != "ar" or not t or not re.search(r"[^\W\d_]", t):
        return t
    if re.search(r"[A-Za-z]", t) and has_arabic(t):  # mixed ("MAIN, 02 م - 10 م"): lay it out right to left, as the sentence
        return "\u2067" + t + ISO_END
    return ISO_START + t + ISO_END


def strip_iso(s: str) -> str:
    return (s or "").replace(ISO_START, "").replace("\u2067", "").replace(ISO_END, "")


def _fmt_kw(kw: dict) -> dict:
    if _lang != "ar":
        return kw
    return {k: (iso(v) if isinstance(v, str) else v) for k, v in kw.items()}


# ----------------------------------------------------------------------------- tr
def tr(text: str, **kw) -> str:
    """Translate the English source ``text`` (catalog key).  Unknown text is returned unchanged (English)."""
    s = text
    if _lang == "ar" and text:
        c = _catalog()
        s = c.CATALOG.get(text)
        if s is None:
            s = c.CATALOG.get(text.strip())
            if s is not None:  # keep the original padding: "  (copy)"
                s = text[:len(text) - len(text.lstrip())] + s + text[len(text.rstrip()):]
        if s is None:
            s = tr_msg(text, _strict=False)
    if kw:
        try:
            return s.format(**_fmt_kw(kw))
        except (KeyError, IndexError, ValueError):
            return s
    return s


def ptr(text: str, **kw) -> str:
    """Like :func:`tr` but never touches the text in English (used for strings that are only English by accident)."""
    return tr(text, **kw)


# ----------------------------------------------------------------------------- counts
def count_phrase(n, noun: str) -> str:
    """``"1 problem"`` / ``"3 problems"`` in English; Arabic singular / dual / plural agreement (``مشكلة واحدة``,
    ``مشكلتان``, ``3 مشاكل``, ``12 مشكلة``).  ``noun`` is the English singular, a key of ``i18n_ar.NOUNS`` = ``(English plural, Arabic 1, 2, 3-10, 11+)``."""
    n = int(n) if float(n) == int(n) else n
    if _lang != "ar":
        pl = _catalog().NOUNS.get(noun, (noun + "s",))[0]
        return f"{n} {noun if n == 1 else pl}"
    sing, dual, plur, acc = _catalog().NOUNS[noun][1:5] if noun in _catalog().NOUNS else (noun, noun, noun, noun)
    k = int(n)
    if k == 1:
        return sing
    if k == 2:
        return dual
    if 3 <= k <= 10:
        return f"{k} {plur}"
    return f"{k} {acc}"


# ----------------------------------------------------------------------------- weekdays, months, dates
_WD_EN = ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")
_WD_FULL_EN = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
_WD_ABBR_EN = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
_MON_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
           "December")


def weekday(code: str, short: bool = True) -> str:
    """Display name of a weekday code (``"SUN"`` ..).  English: the code itself (short) or ``Sunday`` (long)."""
    u = (code or "").strip().upper()
    if u not in _WD_EN:
        return code
    i = _WD_EN.index(u)
    if _lang == "ar":
        c = _catalog()
        return c.WEEKDAYS_AR[i] if not short else c.WEEKDAYS_AR_SHORT[i]
    return u if short else _WD_FULL_EN[i]


def weekday_search_names(code: str) -> list[str]:
    """Every spelling of a weekday a search may use (code, English and Arabic names)."""
    i = _WD_EN.index(code.upper())
    c = _catalog()
    return [_WD_EN[i], _WD_FULL_EN[i], c.WEEKDAYS_AR[i], c.WEEKDAYS_AR_SHORT[i]]


def month_name(m: int) -> str:
    if _lang == "ar":
        return _catalog().MONTHS_AR[m - 1]
    return _MON_EN[m - 1]


def month_abbr(m: int) -> str:
    return _catalog().MONTHS_AR[m - 1] if _lang == "ar" else _MON_EN[m - 1][:3]


def strf(d: date, fmt: str) -> str:
    """``d.strftime(fmt)`` with Arabic weekday / month names in Arabic mode (``%a %A %b %B`` only)."""
    if _lang != "ar":
        return d.strftime(fmt)
    out = (fmt.replace("%A", "\x01").replace("%a", "\x02").replace("%B", "\x03").replace("%b", "\x04"))
    out = d.strftime(out)
    wd = (d.weekday() + 1) % 7
    c = _catalog()
    return (out.replace("\x01", c.WEEKDAYS_AR[wd]).replace("\x02", c.WEEKDAYS_AR[wd])
            .replace("\x03", c.MONTHS_AR[d.month - 1]).replace("\x04", c.MONTHS_AR[d.month - 1]))


def ym_title(year: int, month: int) -> str:
    """``November 2026`` / ``نوفمبر 2026``."""
    return f"{month_name(month)} {year}"


# ----------------------------------------------------------------------------- times, shift codes, cell texts
def disp_hour(txt: str) -> str:
    """``08 AM`` -> ``08 ص``, ``04 PM`` -> ``04 م``, ``12 MN`` -> ``12 منتصف الليل`` (Arabic mode only)."""
    if _lang != "ar":
        return txt
    c = _catalog()
    return (re.sub(r"\b(?:12\s*)?MN\b", c.MIDNIGHT_AR, txt).replace("AM", c.AM_AR).replace("PM", c.PM_AR))


_CODE_TIME = re.compile(r"\b(\d{1,2}(?::\d{2})?)\s*(AM|PM|MN)\b")


def disp_code(code: str) -> str:
    """Display form of a shift code / any text holding 12-hour times; the stored code never changes."""
    if _lang != "ar" or not code:
        return code
    c = _catalog()
    unit = {"AM": c.AM_AR, "PM": c.PM_AR, "MN": c.MIDNIGHT_AR}

    def sub(m):
        return f"{m.group(1)} {unit[m.group(2)]}" if m.group(2) != "MN" else unit["MN"]

    out = re.sub(r"\b12\s*MN\b", c.MIDNIGHT_AR, code)
    out = _CODE_TIME.sub(sub, out)
    return out


def disp_cell(text: str) -> str:
    """Roster cell text (``08 AM - 04 PM [OT]``, ``OFF``, ``LEAVE`` ..) for the screen."""
    if _lang != "ar" or not text:
        return text
    c = _catalog()
    t = text.strip()
    if t == "OFF":
        return c.OFF_AR
    if t == "LEAVE":
        return c.LEAVE_AR
    out = disp_code(text)
    out = out.replace("[OT]", "[" + c.OT_TAG_AR + "]")
    out = re.sub(r"\(EXT \+(\d+)h OT\)", lambda m: "(" + c.EXT_AR.format(h=m.group(1)) + ")", out)
    return out


# ----------------------------------------------------------------------------- problem messages
_TPL_CACHE: list | None = None
_PH = re.compile(r"\{(\w+)(?::(\w+))?\}")
_KINDS = {"": r".+?", "n": r"-?\d+(?:\.\d+)?", "w": r"\S+?", "d": r"\w{3} \d{1,2} \w{3}(?: \d{4})?"}


def _compile_templates() -> list:
    global _TPL_CACHE
    if _TPL_CACHE is None:
        out = []
        for en, ar in sorted(_catalog().TEMPLATES, key=lambda t: -len(_PH.sub("", t[0]))):
            pos, rx = 0, ""
            for m in _PH.finditer(en):
                rx += re.escape(en[pos:m.start()])
                rx += f"(?P<{m.group(1)}>{_KINDS.get(m.group(2) or '', _KINDS[''])})"
                pos = m.end()
            rx += re.escape(en[pos:])
            out.append((re.compile(rx + r"\Z", re.S), ar))
        _TPL_CACHE = out
    return _TPL_CACHE


_SENT_SPLIT = re.compile(r"(?<!e\.g\.)(?<=[.!?])(\s+)(?=[A-Z'‘\"(])")
_DATE_RE = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b(?: (\d{4}))?")
_ABBR_MON = tuple(m[:3] for m in _MON_EN)
_WORD_DAY = re.compile(r"\b(SUN|MON|TUE|WED|THU|FRI|SAT)\b")
_QUOTED = re.compile(r"'([^'\n]{2,60})'")


def _loc_dates(s: str) -> str:
    c = _catalog()

    def sub(m):
        wd = c.WEEKDAYS_AR[_WD_ABBR_EN.index(m.group(1))]
        mon = c.MONTHS_AR[_ABBR_MON.index(m.group(3))]
        return f"{wd} {m.group(2)} {mon}" + (f" {m.group(4)}" if m.group(4) else "")

    return _DATE_RE.sub(sub, s)


def _loc_value(v: str) -> str:
    """A value captured from an English message: dates, weekday codes, shift codes, field labels -> Arabic; names stay."""
    c = _catalog()
    if v in c.CATALOG:
        return c.CATALOG[v]
    t = _loc_dates(v)
    t = re.sub(r"\(row (\d+)\)", lambda m: "(الصف " + m.group(1) + ")", t)
    t = re.sub(r"\bWEEKDAYS\b", "أيام الأسبوع", re.sub(r"\bALL\b", "كل الأيام", t))
    t = re.sub(r"(\d) h\b", lambda m: m.group(1) + " س", t)
    t = _WORD_DAY.sub(lambda m: c.WEEKDAYS_AR_SHORT[_WD_EN.index(m.group(1))], t)
    t = disp_code(t) if _CODE_TIME.search(t) or "MN" in t else t
    return t


def _quoted(s: str) -> str:
    """Quoted field / screen labels inside an Arabic sentence: ``'Overtime allowed'`` -> ``'السماح بالعمل الإضافي'``."""
    c = _catalog()

    def sub(m):
        inner = m.group(1)
        return "'" + c.CATALOG[inner] + "'" if inner in c.CATALOG else m.group(0)

    return _QUOTED.sub(sub, s)


def _translate_sentence(s: str) -> str | None:
    c = _catalog()
    st = s.strip()
    if st in c.CATALOG:
        return c.CATALOG[st]
    for rx, ar in _compile_templates():
        m = rx.match(st)
        if m:
            vals = {k: (_loc_value(v) if not re.fullmatch(r"-?[\d.]+", v) else v) for k, v in m.groupdict().items()}
            # numbers stay as they are; text values get isolates (names inside the Arabic sentence)
            vals = {k: (iso(v) if re.search(r"[^\W\d_]", v) and k not in c.NO_ISO else v) for k, v in vals.items()}
            for k in list(vals):  # {r...}: a reason made of "; "-joined clauses, translated one by one
                if k.startswith("r"):
                    vals[k] = "؛ ".join(_translate_sentence(x) or _fallback(x) for x in m.group(k).split("; "))
            try:
                return ar.format(**vals)
            except (KeyError, IndexError, ValueError):
                return None
    if "; " in st:  # a list of clauses
        parts = [_translate_sentence(x) for x in st.split("; ")]
        if all(p is not None for p in parts):
            return "؛ ".join(parts)
    if st.startswith("- ") and len(st) > 2:  # a bullet line of a report
        t = _translate_sentence(st[2:])
        if t is not None:
            return "- " + t
    if ": " in st:  # "<name or place>: <message>"
        a, b = st.split(": ", 1)
        tb = _translate_sentence(b)
        if tb is not None:
            ta = _translate_sentence(a)
            return (ta if ta is not None else (iso(_loc_value(a)) if re.search(r"[^\W\d_]", a) else a)) + ": " + tb
    return None


def tr_msg(text: str, _strict: bool = True) -> str:
    """Translate an engine / store message (English) into Arabic, sentence by sentence.  English mode: unchanged.

    A sentence without a template stays English (the i18n test lists those)."""
    if _lang != "ar" or not text:
        return text
    t = text
    whole = _translate_sentence(t)
    if whole is not None:
        return _quoted(whole)
    parts = _SENT_SPLIT.split(t)
    out = []
    hit = False
    for i, p in enumerate(parts):
        if i % 2:  # the whitespace between sentences
            out.append(p)
            continue
        lines = p.split("\n")
        done = []
        for ln in lines:
            if not ln.strip():
                done.append(ln)
                continue
            tr_ = _translate_sentence(ln)
            if tr_ is None:
                tr_ = _fallback(ln)
            else:
                hit = True
            done.append(tr_)
        out.append("\n".join(done))
    res = "".join(out)
    if not hit and _strict is False:
        return text
    return _quoted(res)


def _fallback(s: str) -> str:
    """No template: only the language-independent parts are localised (dates, weekday codes, 12-hour times)."""
    return _loc_dates(s) if _lang == "ar" else s


def tr_label(parts: str, sep: str = " › ") -> str:
    """``Employees › FAHAD › Overtime allowed`` -> each part translated (record names stay)."""
    if _lang != "ar":
        return parts
    return sep.join((lambda x: x if x != p else _loc_value(p))(tr(p)) for p in parts.split(sep))


# ----------------------------------------------------------------------------- Qt: direction, fonts, standard buttons
FONT_AR = ["Segoe UI", "Tahoma", "Arial", "Noto Naskh Arabic", "Noto Sans Arabic", "Noto Sans", "DejaVu Sans", "sans-serif"]
FONT_EN = ["Segoe UI", "Noto Sans", "Inter", "DejaVu Sans", "Arial", "sans-serif"]


def font_families() -> list[str]:
    return FONT_AR if _lang == "ar" else FONT_EN


_translators: list = []


def apply_language(app, code: str | None = None) -> None:
    """Set the layout direction, the font, the Qt standard-button translations and the style sheet on ``app``."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator, Qt
    from PySide6.QtGui import QFont

    if code is not None:
        set_language(code)
    ar = is_ar()
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if ar else Qt.LayoutDirection.LeftToRight)
    for t in _translators:
        app.removeTranslator(t)
    _translators.clear()
    if ar:
        for name in ("qtbase_ar", "qt_ar"):
            t = QTranslator(app)
            path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
            if t.load(name, path):
                app.installTranslator(t)
                _translators.append(t)
    # Western digits everywhere, also in the Arabic locale
    loc = QLocale(QLocale.Language.Arabic, QLocale.Country.UnitedArabEmirates) if ar else QLocale(QLocale.Language.English)
    loc.setNumberOptions(QLocale.NumberOption.OmitGroupSeparator)
    QLocale.setDefault(loc)
    f = QFont()
    f.setFamilies(font_families())
    f.setPointSize(10)
    app.setFont(f)
    from .ui.theme import stylesheet

    app.setStyleSheet(stylesheet())
    _install_filter(app)


def _lookup(s: str) -> str | None:
    c = _catalog().CATALOG
    if not s:
        return None
    for k in (s, s.strip()):
        if k in c:
            return c[k] if k is s else s[:len(s) - len(s.lstrip())] + c[k] + s[len(s.rstrip()):]
    if "&" in s:  # a menu / button mnemonic: "&Cancel"
        plain = s.replace("&", "")
        if plain in c:
            return c[plain]
    return None


def retranslate_tree(root) -> None:
    """Safety net (Arabic mode): every label, button, tooltip, placeholder, group title and combo entry under ``root`` whose
    text is exactly a catalog key gets its Arabic text.  Most texts are translated where they are created; this catches the
    standard buttons of dialogs and any static text that was missed."""
    if _lang != "ar":
        return
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QTableWidget, QWidget)

    for w in [root] + root.findChildren(QWidget):
        try:
            tip = _lookup(w.toolTip())
            if tip and tip != w.toolTip():
                w.setToolTip(tip)
            if isinstance(w, (QLabel, QAbstractButton)):
                t = _lookup(w.text())
                if t and t != w.text():
                    w.setText(t)
            elif isinstance(w, QGroupBox):
                t = _lookup(w.title())
                if t and t != w.title():
                    w.setTitle(t)
            elif isinstance(w, QLineEdit):
                t = _lookup(w.placeholderText())
                if t and t != w.placeholderText():
                    w.setPlaceholderText(t)
            elif isinstance(w, QComboBox):
                for i in range(w.count()):
                    t = _lookup(w.itemText(i))
                    if t and t != w.itemText(i):
                        w.setItemText(i, t)
            elif isinstance(w, QTableWidget):
                for i in range(w.columnCount()):
                    it = w.horizontalHeaderItem(i)
                    t = _lookup(it.text()) if it else None
                    if t:
                        it.setText(t)
        except RuntimeError:  # a widget that was deleted meanwhile
            pass


_filter_obj = None


def _install_filter(app) -> None:
    """Translate every window and dialog right before it is first shown (Arabic mode)."""
    global _filter_obj
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QDialog, QMainWindow

    class _Show(QObject):
        def eventFilter(self, obj, ev):  # noqa: N802
            if _lang == "ar" and ev.type() == QEvent.Type.Show and isinstance(obj, (QDialog, QMainWindow)) \
                    and not obj.property("_ar_done"):
                obj.setProperty("_ar_done", True)
                retranslate_tree(obj)
            return False

    if _filter_obj is None:
        _filter_obj = _Show(app)
        app.installEventFilter(_filter_obj)


def yes_no_box(parent, title: str, text: str) -> bool:
    """A Yes / No question with translated buttons (the answer is ``True`` for Yes)."""
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(QMessageBox.Icon.Question, title, text, QMessageBox.StandardButton.NoButton, parent)
    yes = box.addButton(tr("Yes"), QMessageBox.ButtonRole.YesRole)
    box.addButton(tr("No"), QMessageBox.ButtonRole.NoRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


def info_box(parent, title: str, text: str, icon=None) -> None:
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(icon or QMessageBox.Icon.Warning, title, text, QMessageBox.StandardButton.NoButton, parent)
    box.addButton(tr("OK"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()
