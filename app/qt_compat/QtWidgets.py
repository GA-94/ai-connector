"""PyQt6.QtWidgets + имена в стиле PyQt5.

Использование (как раньше, только модуль другой):

    from app.qt_compat.QtWidgets import QApplication, QWidget, QDialog

Возвращены имена, которые Qt6 перенёс или убрал:
  * QAction / QShortcut / QActionGroup — были в QtWidgets, в Qt6 в QtGui;
  * QDesktopWidget — в Qt6 его нет (экраны берутся у QGuiApplication);
  * exec_() у QApplication/QDialog/QMenu/QProgressDialog;
  * setResizeMode() у QHeaderView, setFilterRegExp() у QSortFilterProxyModel.
"""

import PyQt6.QtWidgets as _base

from . import _bridge

_bridge.install()
_bridge.mirror(globals(), _base)

# Дублируем явно: даже если сборка PyQt6 запретила подмену имён в модуле,
# импорт QAction/QShortcut/QDesktopWidget из этого модуля всё равно работает.
from PyQt6.QtGui import QAction, QActionGroup, QShortcut  # noqa: E402,F401
from ._bridge import QDesktopWidget  # noqa: E402,F401

_bridge.alias_legacy("PyQt5.QtWidgets", __name__)

del _base
