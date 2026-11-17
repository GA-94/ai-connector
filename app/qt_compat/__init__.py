"""Совместимость PyQt5 -> PyQt6 (Windows 11 ARM64).

Зачем этот пакет
----------------
Под Windows 11 ARM64 колёс PyQt5 не существует: Qt 5 не собирается под ARM64,
поэтому `pip install PyQt5` там просто не находит пакет. Работающий вариант —
PyQt6 (Qt 6.7+ выпускает колёса win_arm64), но в Qt 6 изменился доступ к
константам и часть API убрана.

Модули-двойники этого пакета повторяют PyQt5 по именам, но внутри — настоящий
PyQt6:

    from app.qt_compat.QtCore import Qt, QTimer, pyqtSignal
    from app.qt_compat.QtGui import QColor, QPainter
    from app.qt_compat.QtWidgets import QApplication, QWidget, QAction

Что даёт мост (подробности — в app/qt_compat/_bridge.py):
  * Qt.AlignLeft, Qt.WA_TranslucentBackground — плоские имена работают;
  * QListWidget.Adjust, QFont.Bold, QDialogButtonBox.Ok — тоже;
  * QAction/QShortcut импортируются из QtWidgets, как раньше;
  * exec_(), event.pos(), QFontMetrics.width(), QRegExp, QDesktopWidget —
    работают.

Ничего не «эмулируется» на уровне типов: виджеты остаются настоящими
классами PyQt6, поэтому isinstance, findChildren и стили ведут себя как в
обычном PyQt6-приложении.
"""

from . import _bridge
from ._bridge import Qt, QDesktopWidget, QRegExp, report  # noqa: F401

_bridge.install()

__all__ = ["Qt", "QRegExp", "QDesktopWidget", "report"]
