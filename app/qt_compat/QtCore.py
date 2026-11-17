"""PyQt6.QtCore + имена в стиле PyQt5.

Использование (как раньше, только модуль другой):

    from app.qt_compat.QtCore import Qt, QTimer, QSize, QRect, pyqtSignal

Подробности про плоские константы и возвращённые имена — в
app/qt_compat/_bridge.py.
"""

import PyQt6.QtCore as _base

from . import _bridge

_bridge.install()
_bridge.mirror(globals(), _base)

#: Qt с плоскими именами констант (Qt.AlignLeft и т. п.).
Qt = _bridge.Qt
#: В Qt6 QRegExp уехал в Qt5Compat — здесь рабочая замена.
QRegExp = _bridge.QRegExp

_bridge.alias_legacy("PyQt5.QtCore", __name__)

del _base
