"""Отладчик графического интерфейса.

Инструмент для разработки: показывает дерево виджетов живого окна с геометрией,
размерами, политикой размеров, objectName и стилями, умеет подсветить контуры
всех виджетов поверх окна (видно, что именно распирает или обрезает раскладку)
и сохранить отчёт в файл.

Открывается из меню «Вид -> Отладчик GUI…».
"""

import datetime
import os

from app.qt_compat.QtCore import QPoint, QRect, Qt, QTimer
from app.qt_compat.QtGui import QColor, QPainter, QPen
from app.qt_compat.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget
)

from app.runtime_env import normalize_path

MAX_DEPTH = 40


def _short(text, limit=60) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def widget_line(widget: QWidget) -> str:
    """Одна строка описания виджета для дерева/отчёта."""
    geometry = widget.geometry()
    name = widget.objectName() or "-"
    parts = [
        type(widget).__name__,
        f"name={name}",
        f"geo={geometry.x()},{geometry.y()} {geometry.width()}x{geometry.height()}",
        f"hint={widget.sizeHint().width()}x{widget.sizeHint().height()}",
        f"min={widget.minimumWidth()}x{widget.minimumHeight()}",
        f"max={widget.maximumWidth()}x{widget.maximumHeight()}",
    ]
    if not widget.isVisible():
        parts.append("hidden")
    role = widget.property("role")
    if role:
        parts.append(f"role={role}")
    if widget.layout() is not None:
        margins = widget.layout().contentsMargins()
        parts.append(
            f"margins={margins.left()},{margins.top()},{margins.right()},{margins.bottom()}"
            f" spacing={widget.layout().spacing()}")
    if widget.styleSheet():
        parts.append(f"qss({len(widget.styleSheet())}b)")
    return "  ".join(parts)


def widget_report(widget: QWidget, max_depth: int = MAX_DEPTH) -> str:
    """Текстовый отчёт по всему дереву виджетов."""
    lines = [
        "GUI-отчёт AI-Connector",
        "время: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "экран: %dx%d, devicePixelRatio=%.1f" % (
            widget.width(), widget.height(), widget.devicePixelRatioF()),
        "",
    ]

    def walk(node: QWidget, depth: int):
        if depth > max_depth:
            return
        lines.append("  " * depth + widget_line(node))
        for child in node.children():
            if isinstance(child, QWidget):
                walk(child, depth + 1)

    walk(widget, 0)
    return "\n".join(lines)


def _measure_lines(widget: QWidget) -> list:
    """Краткая сводка по листовым виджетам — сразу видно, кто самый большой."""
    rows = []
    for node in widget.findChildren(QWidget):
        if not node.isVisible() or node.width() < 60 or node.height() < 20:
            continue
        rows.append((node.width() * node.height(), type(node).__name__,
                     node.objectName() or "-", node.width(), node.height()))
    rows.sort(reverse=True)
    return [f"{area/1000:8.1f}k px  {name:<18} {w}x{h}  {cls}"
            for area, cls, name, w, h in rows[:15]]


class _HighlightOverlay(QWidget):
    """Прозрачный слой поверх окна: рисует рамки вокруг виджетов."""

    def __init__(self, target: QWidget):
        super().__init__(target)
        self._target = target
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("guiDebugOverlay")
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._sync)

    def start(self):
        self._sync()
        self.show()
        self.raise_()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _sync(self):
        if self._target is None:
            return
        self.setGeometry(0, 0, self._target.width(), self._target.height())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        green = QPen(QColor(80, 220, 140), 1)
        red = QPen(QColor(230, 90, 80), 1)
        skipped = ("guiDebugOverlay", "qt_", "QScrollBar", "QSizeGrip")
        for node in self._target.findChildren(QWidget):
            name = node.objectName() or ""
            if any(name.startswith(prefix) for prefix in skipped):
                continue
            if not node.isVisible():
                continue
            top_left = node.mapTo(self._target, QPoint(0, 0))
            rect = QRect(top_left, node.size())
            if rect.width() <= 2 or rect.height() <= 2:
                continue
            painter.setPen(red if (rect.width() < 24 or rect.height() < 18) else green)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.end()


class GuiDebugDialog(QDialog):
    """Окно отладчика: дерево виджетов + подробности + подсветка."""

    def __init__(self, target: QWidget = None, parent=None):
        super().__init__(parent)
        self._target = target or (parent.window() if parent is not None else None)
        self._overlay = None
        self.setWindowTitle("AI-Connector — отладчик GUI")
        self.resize(1000, 680)
        self._build_ui()
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "AiConnector7 — отладчик GUI")
        self.refresh()

    # ------------------------------------------------------------------- UI

    def _build_ui(self):
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.refresh_btn = QPushButton("Обновить (F5)")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)

        self.highlight_cb = QCheckBox("Подсветить контуры виджетов")
        self.highlight_cb.toggled.connect(self._toggle_highlight)
        top.addWidget(self.highlight_cb)

        self.copy_btn = QPushButton("Копировать отчёт")
        self.copy_btn.clicked.connect(self._copy_report)
        top.addWidget(self.copy_btn)

        self.save_btn = QPushButton("Сохранить в файл…")
        self.save_btn.clicked.connect(self._save_report)
        top.addWidget(self.save_btn)
        top.addStretch(1)
        layout.addLayout(top)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("mutedLabel")
        layout.addWidget(self.hint)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["Виджет", "Геометрия", "Размеры", "objectName / role"])
        self.tree.setColumnWidth(0, 240)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 190)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        splitter.addWidget(self.tree)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setObjectName("consoleView")
        splitter.addWidget(self.details)
        splitter.setSizes([640, 360])
        layout.addWidget(splitter, 1)

    # --------------------------------------------------------------- данные

    def refresh(self):
        self.tree.clear()
        target = self._target
        if target is None:
            self.details.setPlainText("Нет активного окна для отладки.")
            return
        self._fill_tree(self.tree.invisibleRootItem(), target, 0)
        lines = [
            "Окно: " + type(target).__name__ + "  " + (target.windowTitle() or "-"),
            "Размер: %dx%d (минимум %dx%d)" % (
                target.width(), target.height(),
                target.minimumWidth(), target.minimumHeight()),
            "Тема QSS: " + str(len(QApplication.instance().styleSheet() if QApplication.instance() else ""))
            + " байт",
            "",
            "Самые большие видимые виджеты:",
        ] + _measure_lines(target)
        self.hint.setText(
            "Дерево обновляется по кнопке. Подсветка показывает контуры: зелёный — "
            "нормальный размер, красный — виджет сжат до нуля/обрезки.")
        self.details.setPlainText("\n".join(lines))

    def _fill_tree(self, parent_item, widget: QWidget, depth: int):
        if depth > MAX_DEPTH:
            return
        text = f"{type(widget).__name__} {_short(widget.objectName(), 28)}"
        geometry = widget.geometry()
        item = QTreeWidgetItem([
            text,
            f"{geometry.x()},{geometry.y()} {geometry.width()}x{geometry.height()}",
            f"{widget.minimumWidth()}x{widget.minimumHeight()} … "
            f"{widget.maximumWidth()}x{widget.maximumHeight()}",
            f"{widget.objectName() or '-'} / {widget.property('role') or '-'}",
        ])
        item.setData(0, Qt.UserRole, widget)
        if not widget.isVisible():
            for column in range(4):
                item.setForeground(column, QColor("#8a8f98"))
        parent_item.addChild(item)
        for child in widget.children():
            if isinstance(child, QWidget):
                self._fill_tree(item, child, depth + 1)

    def _on_selection(self):
        items = self.tree.selectedItems()
        if not items:
            return
        widget = items[0].data(0, Qt.UserRole)
        if widget is None:
            return
        font = widget.font()
        lines = [
            widget_line(widget),
            "",
            "класс:    " + type(widget).__name__,
            "родитель: " + type(widget.parent()).__name__,
            "visible:  " + str(widget.isVisible()),
            "enabled:  " + str(widget.isEnabled()),
            "sizeHint: %dx%d" % (widget.sizeHint().width(), widget.sizeHint().height()),
            "policy:   %s / %s" % (widget.sizePolicy().horizontalPolicy(),
                                   widget.sizePolicy().verticalPolicy()),
            "шрифт:    %s %dpt" % (font.family(), font.pointSize()),
            "стили:    " + (widget.styleSheet() or "(наследуются из темы)"),
        ]
        self.details.setPlainText("\n".join(lines))

    # ------------------------------------------------------------ отчёт/файл

    def _report(self) -> str:
        if self._target is None:
            return "GUI-отчёт: нет активного окна"
        return widget_report(self._target)

    def _copy_report(self):
        QApplication.clipboard().setText(self._report())
        self.details.setPlainText("Отчёт скопирован в буфер обмена.")

    def _save_report(self):
        default_dir = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "data")
        os.makedirs(default_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        suggested = normalize_path(os.path.join(default_dir, f"gui-report-{stamp}.txt"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить GUI-отчёт", suggested, "Текст (*.txt)")
        if not path:
            return
        try:
            with open(normalize_path(path), "w", encoding="utf-8", newline="\n") as handle:
                handle.write(self._report())
        except OSError as error:
            QMessageBox.warning(self, "Отладчик GUI", f"Не удалось сохранить: {error}")
            return
        self.details.setPlainText("Отчёт сохранён: " + normalize_path(path))

    # --------------------------------------------------------------- подсветка

    def _toggle_highlight(self, enabled: bool):
        if self._target is None:
            return
        if enabled:
            if self._overlay is None:
                self._overlay = _HighlightOverlay(self._target)
            self._overlay.start()
        elif self._overlay is not None:
            self._overlay.stop()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F5:
            self.refresh()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self._overlay is not None:
            self._overlay.stop()
        super().closeEvent(event)
