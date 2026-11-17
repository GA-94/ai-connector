"""PyQt6.QtGui + имена в стиле PyQt5.

Использование (как раньше, только модуль другой):

    from app.qt_compat.QtGui import QColor, QFont, QPainter, QPixmap

В Qt6 в QtGui переехали QAction и QShortcut — здесь они доступны и отсюда,
и из app.qt_compat.QtWidgets, как было в PyQt5.
"""

import PyQt6.QtGui as _base

from . import _bridge

_bridge.install()
_bridge.mirror(globals(), _base)

_bridge.alias_legacy("PyQt5.QtGui", __name__)

del _base
