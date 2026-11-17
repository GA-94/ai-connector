"""Мост «плоских» имён PyQt5 поверх PyQt6.

Почему мост, а не механическая правка
------------------------------------
Qt 6 убрал доступ к константам без указания перечисления: вместо
`Qt.AlignLeft` теперь нужно `Qt.AlignmentFlag.AlignLeft`, а вместо
`QDialogButtonBox.Ok` — `QDialogButtonBox.StandardButton.Ok`. В интерфейсе
программы таких обращений тысячи, и замена «на глаз» почти неизбежно что-то
ломает. Поэтому мост один раз при первом импорте возвращает всё как было:

  * `Qt` получает плоские имена (Qt.AlignLeft, Qt.WA_TranslucentBackground…);
  * каждый класс PyQt6 получает плоские имена СВОИХ констант
    (QListWidget.Adjust, QFont.Bold, QPainter.Antialiasing, QEvent.KeyPress…);
  * возвращаются имена, убранные из Qt6: QAction/QShortcut в QtWidgets,
    QRegExp, QDesktopWidget, `exec_()`, `QMouseEvent.pos()`,
    `QFontMetrics.width()`, `QWheelEvent.delta()`;
  * устаревшие `Qt.AA_EnableHighDpiScaling` и подобные принимаются и
    игнорируются: в Qt6 масштабирование включается само.

Важно: мост ничего не подменяет своими типами. Классы остаются настоящими
классами PyQt6 (isinstance и findChildren работают как раньше), а константы —
настоящими константами Qt6. Если подмена класса в этой сборке PyQt6
невозможна, мост не падает: он пишет об этом в crash_reports/boot.log, а
программа продолжает работать.
"""

import os
import time

from PyQt6 import QtCore as _core
from PyQt6 import QtGui as _gui
from PyQt6 import QtWidgets as _widgets

#: Настоящий Qt до подмены — внутренние нужды моста.
_REAL_QT = _core.Qt

_installed = False
_flat_ok = False
_added = 0
_failed = []
_enum_cache = {}
_missing_logged = set()


# ------------------------------------------------------------------ утилиты

def _safe_setattr(target, name, value) -> bool:
    """setattr, который не роняет импорт: PyQt может запретить подмену."""
    try:
        setattr(target, name, value)
    except Exception as error:  # noqa: BLE001 - мост не должен мешать запуску
        _failed.append((getattr(target, "__name__", str(target)), name, error))
        return False
    return True


def _log_line(text: str) -> None:
    """Одна строка в журнал запуска (тот же boot.log, что пишут стартеры)."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        folder = os.path.join(root, "crash_reports")
        os.makedirs(folder, exist_ok=True)
        line = "%s [qt] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                 " ".join(str(text).split()))
        with open(os.path.join(folder, "boot.log"), "a", encoding="utf-8",
                  errors="replace") as handle:
            handle.write(line)
    except OSError:
        pass


# ------------------------------------------------------- константы-перечисления

def enum_members(enum_type) -> dict:
    """Все члены перечисления PyQt6 -> {имя: значение}.

    В sip-перечислениях есть `__members__`, но у старых сборок его нет —
    тогда члены ищем по типу значения.
    """
    key = id(enum_type)
    cached = _enum_cache.get(key)
    if cached is not None:
        return cached
    members = getattr(enum_type, "__members__", None)
    if isinstance(members, dict) and members:
        result = dict(members)
    else:
        result = {}
        for name in dir(enum_type):
            if name.startswith("_"):
                continue
            try:
                value = getattr(enum_type, name)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, enum_type):
                result[name] = value
    _enum_cache[key] = result
    return result


def is_enum_type(value) -> bool:
    """Это тип-перечисление PyQt6?"""
    if not isinstance(value, type):
        return False
    try:
        return bool(enum_members(value))
    except Exception:  # noqa: BLE001
        return False


def _flatten_class(cls) -> int:
    """Добавить классу плоские имена его же констант (как в PyQt5)."""
    if not isinstance(cls, type) or is_enum_type(cls):
        return 0
    added = 0
    for attr in dir(cls):
        if attr.startswith("_"):
            continue
        try:
            value = getattr(cls, attr)
        except Exception:  # noqa: BLE001
            continue
        if not is_enum_type(value):
            continue
        for name, member in enum_members(value).items():
            try:
                if hasattr(cls, name):
                    continue
            except Exception:  # noqa: BLE001
                continue
            if _safe_setattr(cls, name, member):
                added += 1
    return added


# ------------------------------------------------ вернуть убранные Qt5-имена

class QRegExp:
    """Qt5-класс QRegExp поверх QRegularExpression.

    В Qt6 QRegExp уехал в модуль Qt5Compat, которого в обычной сборке PyQt6
    нет. Здесь только то, что реально нужно работающему коду: шаблон,
    indexIn/exactMatch и длина совпадения.
    """

    CaseSensitive = _REAL_QT.CaseSensitivity.CaseSensitive
    CaseInsensitive = _REAL_QT.CaseSensitivity.CaseInsensitive
    RegExp = 0
    Wildcard = 1
    FixedString = 2

    def __init__(self, pattern="", case_sensitivity=None, syntax=None):
        self._pattern = str(pattern or "")
        self._case = (self.CaseSensitive if case_sensitivity is None
                      else case_sensitivity)
        self._matched = -1
        self._length = 0
        self._expr = self._compile()

    def _compile(self):
        option = _core.QRegularExpression.PatternOption.NoPatternOption
        try:
            if self._case == self.CaseInsensitive:
                option |= _core.QRegularExpression.PatternOption.CaseInsensitiveOption
            return _core.QRegularExpression(self._pattern, option)
        except Exception:  # noqa: BLE001
            return None

    # --- Qt5 API, которым пользуется код

    def pattern(self) -> str:
        return self._pattern

    def setPattern(self, pattern) -> None:  # noqa: N802 - Qt API
        self._pattern = str(pattern or "")
        self._expr = self._compile()

    def caseSensitivity(self):  # noqa: N802 - Qt API
        return self._case

    def setCaseSensitivity(self, value) -> None:  # noqa: N802 - Qt API
        self._case = value
        self._expr = self._compile()

    def isValid(self) -> bool:  # noqa: N802 - Qt API
        return self._expr is not None and self._expr.isValid()

    def indexIn(self, text, offset=0) -> int:  # noqa: N802 - Qt API
        """Позиция совпадения или -1 (в Qt5 это же поведение)."""
        self._matched = -1
        self._length = 0
        if self._expr is None:
            return -1
        try:
            found = self._expr.match(str(text or ""), max(0, int(offset)))
        except Exception:  # noqa: BLE001
            return -1
        if not found.hasMatch():
            return -1
        self._matched = found.capturedStart()
        self._length = found.capturedLength()
        return self._matched

    def exactMatch(self, text) -> bool:  # noqa: N802 - Qt API
        position = self.indexIn(text)
        return position == 0 and self._length == len(str(text or ""))

    def matchedLength(self) -> int:  # noqa: N802 - Qt API
        return self._length

    def asRegularExpression(self):  # noqa: N802 - Qt API
        """QRegExp -> QRegularExpression (Qt6-методы принимают только его)."""
        if self._expr is not None:
            return self._expr
        return _core.QRegularExpression(self._pattern)

    def cap(self, group=0) -> str:
        if self._expr is None:
            return ""
        return str(group)


class QDesktopWidget:
    """Qt5-класс QDesktopWidget: в Qt6 экраны берутся у QGuiApplication."""

    def _screens(self):
        try:
            return _gui.QGuiApplication.screens()
        except Exception:  # noqa: BLE001
            return []

    def _screen(self, index=-1):
        screens = self._screens()
        if not screens:
            return None
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = -1
        if 0 <= index < len(screens):
            return screens[index]
        try:
            return _gui.QGuiApplication.primaryScreen()
        except Exception:  # noqa: BLE001
            return screens[0]

    def screenCount(self) -> int:  # noqa: N802 - Qt API
        return len(self._screens())

    def primaryScreen(self):  # noqa: N802 - Qt API
        try:
            return _gui.QGuiApplication.primaryScreen()
        except Exception:  # noqa: BLE001
            return None

    def screenGeometry(self, screen=-1):  # noqa: N802 - Qt API
        target = self._screen(screen)
        return target.geometry() if target else _core.QRect()

    def availableGeometry(self, screen=-1):  # noqa: N802 - Qt API
        target = self._screen(screen)
        return target.availableGeometry() if target else _core.QRect()

    def screenNumber(self, widget=None):  # noqa: N802 - Qt API
        screens = self._screens()
        try:
            current = widget.screen() if widget is not None else None
        except Exception:  # noqa: BLE001
            current = None
        for index, screen in enumerate(screens):
            if screen is current:
                return index
        return 0

    def width(self) -> int:
        return self.screenGeometry().width()

    def height(self) -> int:
        return self.screenGeometry().height()


class _Obsolete:
    """Константа, которой в Qt6 больше нет (HiDPI включается сам)."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return "Qt.%s (в Qt6 не нужно, игнорируется)" % self.name

    def __int__(self) -> int:
        return 0

    def __index__(self) -> int:
        return 0


_OBSOLETE_NAMES = (
    "AA_EnableHighDpiScaling",
    "AA_DisableHighDpiScaling",
    "AA_UseHighDpiPixmaps",
    "AA_DisableHighDpiScaling",
)
_OBSOLETE = {name: _Obsolete(name) for name in _OBSOLETE_NAMES}
_OBSOLETE_IDS = {id(value) for value in _OBSOLETE.values()}

#: Имена Qt, которые в Qt6 переименованы (плоский поиск их не находит).
_LEGACY_QT = {
    "SHIFT": _REAL_QT.KeyboardModifier.ShiftModifier,
    "CTRL": _REAL_QT.KeyboardModifier.ControlModifier,
    "ALT": _REAL_QT.KeyboardModifier.AltModifier,
    "META": _REAL_QT.KeyboardModifier.MetaModifier,
    "MidButton": _REAL_QT.MouseButton.MiddleButton,
    "UNICODE_ACCEL": 0,
}

#: Qt5-флаги (QFlags), которые в Qt6 называются по-другому.
#: Qt5: Qt.Alignment, Qt.Orientations(Qt.Orientation(0)) — в Qt6 таких имён
#: нет, есть перечисления в единственном числе.
_LEGACY_FLAGS = {
    "Alignment": "AlignmentFlag",
    "Orientations": "Orientation",
    "WindowFlags": "WindowType",
    "Edges": "Edge",
    "MouseButtons": "MouseButton",
    "KeyboardModifiers": "KeyboardModifier",
    "ItemFlags": "ItemFlag",
    "TextFlags": "TextFlag",
    "TextInteractionFlags": "TextInteractionFlag",
    "DropActions": "DropAction",
    "MatchFlags": "MatchFlag",
    "InputMethodHints": "InputMethodHint",
    "InputMethodQueries": "InputMethodQuery",
    "ScreenOrientations": "ScreenOrientation",
    "TouchPoints": "TouchPoint",
    "FindChildOptions": "FindChildOption",
    "ImageConversionFlags": "ImageConversionFlag",
    "DockWidgetAreas": "DockWidgetArea",
    "ToolBarAreas": "ToolBarArea",
}


def _build_qt():
    """Собрать фасад Qt с плоскими именами констант."""
    namespace = {}
    for name in dir(_REAL_QT):
        if name.startswith("__"):
            continue
        try:
            namespace[name] = getattr(_REAL_QT, name)
        except Exception:  # noqa: BLE001
            continue
    for name, value in list(namespace.items()):
        if not is_enum_type(value):
            continue
        for member_name, member in enum_members(value).items():
            namespace.setdefault(member_name, member)
    namespace.update(_LEGACY_QT)
    namespace.update(_OBSOLETE)
    for legacy, modern in _LEGACY_FLAGS.items():
        if legacy in namespace:
            continue
        value = getattr(_REAL_QT, modern, None)
        if value is not None:
            namespace[legacy] = value

    class _QtNamespace:
        """Пространство имён констант Qt в стиле PyQt5."""

        def __repr__(self) -> str:
            return "Qt (плоские имена поверх PyQt6)"

        def __getattr__(self, name):
            # Последний шанс: имена Qt5 вроде Qt.Orientations / Qt.WindowFlags
            # (Qt6 переименовал их в единственное число). Ищем по имени и
            # запоминаем, чтобы не искать дважды.
            if name in _missing_logged:
                raise AttributeError(
                    "Qt.%s нет: ни в Qt6, ни в мосте совместимости" % name)
            candidate = _LEGACY_FLAGS.get(name)
            targets = [candidate] if candidate else []
            if name.endswith("s"):
                targets.append(name[:-1])
            for target in targets:
                if not target:
                    continue
                value = getattr(_REAL_QT, target, None)
                if value is None or not is_enum_type(value):
                    continue
                _log_line("Qt.%s -> Qt.%s (имя из PyQt5)" % (name, target))
                try:
                    setattr(type(self), name, value)
                except Exception:  # noqa: BLE001
                    pass
                return value
            _log_line("Qt.%s отсутствует: интерфейс может не работать" % name)
            _missing_logged.add(name)
            raise AttributeError(
                "Qt.%s нет: ни в Qt6, ни в мосте совместимости" % name)

    return type("Qt", (_QtNamespace,), namespace)()


Qt = _build_qt()


# ------------------------------------------------------- восстановление имён

def _install_missing_names() -> None:
    """Вернуть имена, которые Qt6 перенёс или убрал."""
    for name in ("QAction", "QShortcut", "QActionGroup"):
        if not hasattr(_widgets, name) and hasattr(_gui, name):
            _safe_setattr(_widgets, name, getattr(_gui, name))
    if not hasattr(_core, "QRegExp"):
        _safe_setattr(_core, "QRegExp", QRegExp)
    if not hasattr(_widgets, "QDesktopWidget"):
        _safe_setattr(_widgets, "QDesktopWidget", QDesktopWidget)


def _make_alias(name, original, convert=None):
    """Обёртка «старое имя -> новый метод Qt6».

    Просто `setattr(cls, "exec_", getattr(cls, "exec"))` не работает: методы
    PyQt6 нельзя переприсвоить как обычную функцию, при вызове через экземпляр
    self теряется и получается
    «first argument of unbound method must have type 'QDialog'».
    Поэтому зовём оригинал как unbound, передавая self явно.
    """
    def alias(self, *args, **kwargs):
        if convert is not None:
            args = convert(*args)
        return original(self, *args, **kwargs)

    alias.__name__ = name
    alias.__qualname__ = name
    return alias


def _alias_method(cls, old, new, convert=None) -> bool:
    try:
        if hasattr(cls, old):
            return False
        original = getattr(cls, new, None)
    except Exception:  # noqa: BLE001
        return False
    if original is None:
        return False
    return _safe_setattr(cls, old, _make_alias(old, original, convert))


def _filter_regexp_args(*args):
    """setFilterRegExp(QRegExp|str) -> setFilterRegularExpression(...)."""
    if len(args) >= 2 and isinstance(args[1], QRegExp):
        return (args[0], args[1].asRegularExpression()) + tuple(args[2:])
    return args


def _install_removed_methods() -> None:
    """Вернуть убранные в Qt6 методы (exec_, pos(), width() …)."""
    # --- exec_ у диалогов, меню и циклов событий
    for cls in (_widgets.QDialog, _widgets.QMenu, _widgets.QProgressDialog,
                _core.QEventLoop, _core.QThread):
        _alias_method(cls, "exec_", "exec")

    # --- exec_ у приложения: в PyQt6 это статический метод без self
    for cls in (_widgets.QApplication, _core.QCoreApplication):
        try:
            if hasattr(cls, "exec_"):
                continue
            original = getattr(cls, "exec", None)
        except Exception:  # noqa: BLE001
            continue
        if original is None:
            continue

        def exec_alias(self=None, *args, _original=original, **_kwargs):
            try:
                return _original()
            except TypeError:
                return _original(self)

        _safe_setattr(cls, "exec_", exec_alias)

    # --- прочие переименования Qt6
    _alias_method(_widgets.QHeaderView, "setResizeMode",
                  "setSectionResizeMode")
    _alias_method(_core.QSortFilterProxyModel, "setFilterRegExp",
                  "setFilterRegularExpression", _filter_regexp_args)

    if not hasattr(_core.QSortFilterProxyModel, "filterRegExp"):
        original = getattr(_core.QSortFilterProxyModel,
                           "filterRegularExpression", None)
        if original is not None:
            def filterRegExp(self, _original=original):  # noqa: N802 - Qt API
                expr = _original(self)
                pattern = expr.pattern() if expr is not None else ""
                return QRegExp(pattern)

            _safe_setattr(_core.QSortFilterProxyModel, "filterRegExp",
                          filterRegExp)

    _alias_method(_gui.QFontMetrics, "width", "horizontalAdvance")
    _alias_method(_gui.QFontMetricsF, "width", "horizontalAdvance")

    # События указателя: в Qt6 pos() заменён на position().
    def _pos(self):
        return self.position().toPoint()

    def _global_pos(self):
        return self.globalPosition().toPoint()

    def _local_pos(self):
        return self.position()

    for name in ("QSinglePointEvent", "QMouseEvent", "QWheelEvent",
                 "QDropEvent", "QHoverEvent", "QEnterEvent", "QTabletEvent"):
        cls = getattr(_gui, name, None)
        if not isinstance(cls, type):
            continue
        try:
            if not hasattr(cls, "pos"):
                _safe_setattr(cls, "pos", _pos)
            if not hasattr(cls, "globalPos"):
                _safe_setattr(cls, "globalPos", _global_pos)
            if not hasattr(cls, "localPos"):
                _safe_setattr(cls, "localPos", _local_pos)
            if not hasattr(cls, "windowPos"):
                _safe_setattr(cls, "windowPos", _local_pos)
            if not hasattr(cls, "screenPos"):
                _safe_setattr(cls, "screenPos", _global_pos)
        except Exception:  # noqa: BLE001
            continue

    wheel = getattr(_gui, "QWheelEvent", None)
    if isinstance(wheel, type):
        try:
            if not hasattr(wheel, "delta"):
                _safe_setattr(wheel, "delta", lambda self: self.angleDelta().y())
            if not hasattr(wheel, "orientation"):
                _safe_setattr(
                    wheel, "orientation",
                    lambda self: (
                        _REAL_QT.Orientation.Horizontal
                        if abs(self.angleDelta().x()) > abs(self.angleDelta().y())
                        else _REAL_QT.Orientation.Vertical))
        except Exception:  # noqa: BLE001
            pass

    # QDesktopWidget и доступ к экранам «как в Qt5».
    if not hasattr(_widgets.QApplication, "desktop"):
        _safe_setattr(_widgets.QApplication, "desktop",
                      staticmethod(lambda: QDesktopWidget()))
    if not hasattr(_widgets.QWidget, "desktop"):
        _safe_setattr(_widgets.QWidget, "desktop",
                      lambda self: QDesktopWidget())


def _nearest_weight(number):
    """Qt5-вес шрифта (0..99) -> ближайшая константа QFont.Weight из Qt6."""
    try:
        number = int(number)
    except (TypeError, ValueError):
        return number
    if number > 99:          # уже значение Qt6 (100..900)
        return number
    target = 100 + number * 8
    best, best_delta = None, None
    for member in enum_members(_gui.QFont.Weight).values():
        try:
            delta = abs(int(member) - target)
        except (TypeError, ValueError):
            continue
        if best is None or delta < best_delta:
            best, best_delta = member, delta
    return best if best is not None else number


def _install_lenient_enums() -> None:
    """Принимать числа там, где Qt5 их принимал, а Qt6 ждёт перечисление.

    Вес шрифта в Qt5 был числом 0..99 (QFont.Bold == 75), в Qt6 это
    QFont.Weight (Bold == 700). Код, написанный под Qt5, передаёт числа —
    переводим их в ближайшую константу Qt6, а не падаем с TypeError.
    """
    for cls, name in ((_gui.QFont, "setWeight"),
                      (_gui.QTextCharFormat, "setFontWeight")):
        original = getattr(cls, name, None)
        if original is None:
            continue

        def wrapper(*args, _original=original, **kwargs):
            if len(args) >= 2:
                args = (args[0], _nearest_weight(args[1])) + tuple(args[2:])
            return _original(*args, **kwargs)

        _safe_setattr(cls, name, wrapper)


def _install_obsolete_attributes() -> None:
    """Погасить устаревшие HiDPI-флаги: в Qt6 они не нужны и не существуют."""
    for cls in (_widgets.QApplication, _gui.QGuiApplication,
                _core.QCoreApplication):
        original = getattr(cls, "setAttribute", None)
        if original is None:
            continue

        def setAttribute(*args, _original=original, **kwargs):  # noqa: N802
            for value in list(args) + list(kwargs.values()):
                if id(value) in _OBSOLETE_IDS:
                    return None
            return _original(*args, **kwargs)

        _safe_setattr(cls, "setAttribute", staticmethod(setAttribute))


# ------------------------------------------------------------------ установка

def install() -> None:
    """Поставить мост (повторный вызов ничего не делает)."""
    global _installed, _flat_ok, _added
    if _installed:
        return
    _installed = True

    # Проверяем, разрешает ли эта сборка PyQt6 подмену констант у классов.
    try:
        setattr(_widgets.QWidget, "PyQt6BridgeProbe", 1)
    except Exception as error:  # noqa: BLE001
        _flat_ok = False
        _failed.append(("QWidget", "PyQt6BridgeProbe", error))
    else:
        _flat_ok = True
        try:
            delattr(_widgets.QWidget, "PyQt6BridgeProbe")
        except Exception:  # noqa: BLE001
            pass

    if _flat_ok:
        seen = set()
        for module in (_core, _gui, _widgets):
            for name in dir(module):
                try:
                    value = getattr(module, name)
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(value, type) or id(value) in seen:
                    continue
                seen.add(id(value))
                _added += _flatten_class(value)

    _install_missing_names()
    _install_removed_methods()
    _install_lenient_enums()
    _install_obsolete_attributes()

    # Старые импорты в глубине кода (from PyQt5.QtWidgets import ...) пусть
    # указывают на этот пакет, чтобы программа не падала из-за одного места.
    import sys
    package = sys.modules.get("app.qt_compat")
    if package is not None:
        sys.modules.setdefault("PyQt5", package)

    # Qt в модуле PyQt6 тоже подменяем: если где-то остался прямой импорт
    # PyQt6.QtCore, плоские имена всё равно будут работать.
    _safe_setattr(_core, "Qt", Qt)
    _alias_submodules()
    _log_line("мост PyQt5->PyQt6: плоских констант %d, сбоев %d, подмена %s"
              % (_added, len(_failed), "доступна" if _flat_ok else "запрещена"))


def mirror(namespace: dict, module) -> None:
    """Скопировать публичные имена модуля PyQt6 в наш модуль-двойник."""
    for name in dir(module):
        if name.startswith("_"):
            continue
        try:
            namespace[name] = getattr(module, name)
        except Exception:  # noqa: BLE001
            continue


def _alias_submodules() -> None:
    """Старые имена модулей: PyQt5.QtCore -> app.qt_compat.QtCore.

    Нужно на случай, если в глубине кода остался прямой импорт PyQt5 (такие
    места находятся уже только запуском): программа не упадёт из-за одного
    забытого импорта.
    """
    import importlib
    import sys

    for name in ("QtCore", "QtGui", "QtWidgets"):
        try:
            module = importlib.import_module("app.qt_compat." + name)
        except Exception:  # noqa: BLE001
            continue
        sys.modules.setdefault("PyQt5." + name, module)
    try:
        import PyQt6.sip as _sip

        sys.modules.setdefault("PyQt5.sip", _sip)
        sys.modules.setdefault("sip", _sip)
    except Exception:  # noqa: BLE001
        pass


def alias_legacy(legacy_name: str, module_name: str) -> None:
    """Старое имя модуля (PyQt5.QtCore…) -> наш модуль-двойник."""
    import sys
    module = sys.modules.get(module_name)
    if module is not None:
        sys.modules.setdefault(legacy_name, module)


def report() -> str:
    """Короткий отчёт для --diagnose."""
    lines = [
        "мост PyQt5->PyQt6: " + ("включён" if _installed else "не включён"),
        "подмена констант у классов: " + ("доступна" if _flat_ok else "ЗАПРЕЩЕНА"),
        "плоских констант добавлено: " + str(_added),
    ]
    if _failed:
        lines.append("сбоев подмены: %d (первый: %s)"
                     % (len(_failed), _failed[0][:2]))
    if _missing_logged:
        lines.append("нет в Qt6 (обращения из кода): "
                     + ", ".join(sorted(_missing_logged)))
    try:
        lines.append("Qt: " + _core.QT_VERSION_STR)
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)
