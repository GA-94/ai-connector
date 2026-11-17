"""Общая настройка тестов.

Интерфейс программы работает на PyQt6, а совместимость со старыми импортами
(from PyQt5.QtCore import QSettings) даёт мост app.qt_compat: он подставляет
алиасы в sys.modules. Часть тестов и модулей импортируют PyQt5 напрямую,
поэтому мост надо поднять ДО сбора тестов — иначе сбор падает с
ModuleNotFoundError: No module named 'PyQt5', хотя виноват только порядок
импортов.

Заодно выставляем offscreen-платформу: тесты не должны открывать окна.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import app.qt_compat  # noqa: E402,F401 - мост PyQt5 -> PyQt6
