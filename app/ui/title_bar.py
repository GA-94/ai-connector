"""Своя полоса заголовка в цветах темы вместо белой системной рамки.

Windows и почти все Linux-WM рисуют рамку окна цветом системы: на светлой
теме программы рамка остаётся белой и выпадает из интерфейса. Поэтому окно
создаётся без системной рамки, а заголовок, кнопки «свернуть / развернуть /
закрыть», «якорь» для перетаскивания и полоски изменения размера по краям
реализованы здесь - цвета берёт QSS из темы.

Перетаскивание. Раньше окно двигалось только через QWindow.startSystemMove():
на Windows и части X11-WM он возвращает True, но окно при этом стоит на месте -
казалось, что перетаскивание пропало. Теперь геометрия меняется вручную
(QWidget.move), а системный запрос остаётся только для Wayland, где клиент
не имеет права двигать окно сам.

Изменение размера. Вокруг окна лежат восемь невидимых полосок (4 края и
4 угла): курсор меняется по краю, тянет размер. На Wayland — снова системный
запрос, иначе QWidget.setGeometry.

Вся работа обёрнута в try/except: если WM или PyQt6 не умеют безрамочное
окно, программа должна запуститься с обычной системной рамкой, а не упасть.
"""

import os
from pathlib import Path

from app.qt_compat.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt
from app.qt_compat.QtGui import (
    QColor, QIcon, QPainter, QPainterPath, QPalette, QPixmap, QRegion
)
from app.qt_compat.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu, QToolButton,
    QVBoxLayout, QWidget
)

TITLE_BAR_HEIGHT = 38
ICON_SIZE = 15
RESIZE_MARGIN = 6      # толщина невидимой полосы у края окна
CORNER_SIZE = 18       # квадрат в углу окна под диагональный курсор
GRIP_SIZE = (30, 22)   # «якорь» на полосе заголовка

# Картинки лежат в app/img рядом с этим файлом.
ICON_FILES = {
    "min": "minus.png",
    "max": "cvadrat.png",
    "close": "crest.png",
}


def icon_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "img"


def load_button_icon(filename: str, color: str, size: int = ICON_SIZE) -> QIcon:
    """Загрузить PNG кнопки и перекрасить его в цвет темы.

    Картинки нарисованы белым по прозрачному фону, поэтому на светлой теме
    они были бы невидимы: цвет заменяется на нужный, альфа-канал сохраняется.
    Если файла нет или он битый — вернётся пустая иконка, и кнопка останется
    с текстовым символом.
    """
    try:
        path = icon_dir() / filename
        if not path.is_file():
            return QIcon()
        source = QPixmap(str(path))
        if source.isNull():
            return QIcon()
        tinted = QPixmap(source.size())
        tinted.fill(Qt.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, source)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(color))
        painter.end()
        scaled = tinted.scaled(size, size, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        return QIcon(scaled)
    except Exception:  # noqa: BLE001 - без иконки программа должна работать
        return QIcon()


def theme_text_color() -> str:
    """Цвет иконок по текущей палитре (её ставит apply_theme)."""
    app = QApplication.instance()
    if app is None:
        return "#ffffff"
    return app.palette().color(QPalette.WindowText).name()


# --------------------------------------------------------------- платформа

def platform_name() -> str:
    app = QApplication.instance()
    return (app.platformName() if app is not None else "").lower()


def is_wayland() -> bool:
    """Wayland: клиент не может сам двигать и растягивать окно."""
    return "wayland" in platform_name() or bool(os.environ.get("WAYLAND_DISPLAY"))


# ------------------------------------------------------- общая геометрия

def toggle_maximize(window):
    """Развернуть / восстановить окно (для главного окна и диалогов)."""
    try:
        if window.windowState() & Qt.WindowMaximized:
            window.showNormal()
        else:
            window.showMaximized()
    except Exception:  # noqa: BLE001
        pass


class WindowDragMixin:
    """Перетаскивание окна мышью — общее для полосы заголовка и «якоря».

    Порядок: на Wayland просим WM (startSystemMove), в остальных случаях
    двигаем окно вручную — так работает и на Windows, и на X11.
    """

    _window = None
    _drag_offset = None

    def _system_move(self) -> bool:
        handle = self._window.windowHandle()
        if handle is None or not hasattr(handle, "startSystemMove"):
            return False
        try:
            return bool(handle.startSystemMove())
        except Exception:  # noqa: BLE001 - WM может не уметь
            return False

    def _drag_start(self, event) -> bool:
        if event.button() != Qt.LeftButton or self._window is None:
            return False
        if self._window.windowState() & Qt.WindowMaximized:
            return False
        if is_wayland() and self._system_move():
            return True
        self._drag_offset = (event.globalPos()
                             - self._window.frameGeometry().topLeft())
        try:
            self.setCursor(Qt.ClosedHandCursor)
        except Exception:  # noqa: BLE001
            pass
        return True

    def _drag_move(self, event) -> bool:
        if self._drag_offset is None:
            return False
        if not (event.buttons() & Qt.LeftButton):
            self._drag_offset = None
            return False
        self._window.move(event.globalPos() - self._drag_offset)
        return True

    def _drag_end(self) -> bool:
        active = self._drag_offset is not None
        self._drag_offset = None
        if active:
            try:
                self.unsetCursor()
            except Exception:  # noqa: BLE001
                pass
        return active


# ------------------------------------------------- изменение размера окон

_EDGE_FLAGS = {
    "l": Qt.LeftEdge,
    "r": Qt.RightEdge,
    "t": Qt.TopEdge,
    "b": Qt.BottomEdge,
}

_EDGE_CURSORS = {
    ("t",): Qt.SizeVerCursor,
    ("b",): Qt.SizeVerCursor,
    ("l",): Qt.SizeHorCursor,
    ("r",): Qt.SizeHorCursor,
    ("t", "l"): Qt.SizeFDiagCursor,
    ("b", "r"): Qt.SizeFDiagCursor,
    ("t", "r"): Qt.SizeBDiagCursor,
    ("b", "l"): Qt.SizeBDiagCursor,
}

EDGE_SETS = (("t",), ("b",), ("l",), ("r",),
             ("t", "l"), ("t", "r"), ("b", "l"), ("b", "r"))


def _qt_edges(edges):
    flags = 0
    for edge in edges:
        flags |= _EDGE_FLAGS.get(edge, 0)
    return flags


def frame_radius() -> int:
    """Радиус скругления окна, который посчитала тема при apply_theme."""
    app = QApplication.instance()
    if app is None:
        return 0
    try:
        return int(app.property("frame_radius") or 0)
    except (TypeError, ValueError):
        return 0


def refresh_mask(window):
    """Пересчитать маску скругления (только там, где это включено)."""
    if not getattr(window, "_frame_auto_mask", False):
        return
    radius = int(getattr(window, "frame_radius", 0) or 0)
    size = (window.width(), window.height(), radius)
    # setMask сам может вызвать Resize -> бесконечный цикл, из-за которого
    # содержимое окна обрезалось по краям. Одинаковый размер не пересчитываем.
    if getattr(window, "_frame_mask_size", None) == size:
        return
    window._frame_mask_size = size
    apply_window_mask(window, radius)


class WindowResizer:
    """Ручное изменение размера безрамочного окна за края и углы."""

    def __init__(self, window):
        self.window = window
        self._edges = ()
        self._start_pos = QPoint()
        self._start_geom = QRect()
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def _system_resize(self, edges) -> bool:
        handle = self.window.windowHandle()
        if handle is None or not hasattr(handle, "startSystemResize"):
            return False
        try:
            return bool(handle.startSystemResize(_qt_edges(edges)))
        except Exception:  # noqa: BLE001 - WM может не уметь
            return False

    def begin(self, edges, global_pos) -> bool:
        edges = tuple(edges)
        if not edges or self.window is None:
            return False
        try:
            if self.window.isMaximized() or self.window.isFullScreen():
                return False
        except Exception:  # noqa: BLE001
            pass
        if is_wayland() and self._system_resize(edges):
            return True
        self._edges = edges
        self._start_pos = QPoint(global_pos)
        self._start_geom = QRect(self.window.geometry())
        self._active = True
        return True

    def _limits(self):
        window = self.window
        min_w = max(320, window.minimumWidth())
        min_h = max(200, window.minimumHeight())
        max_w = window.maximumWidth()
        max_h = window.maximumHeight()
        return min_w, min_h, max_w, max_h

    def update(self, global_pos) -> bool:
        if not self._active:
            return False
        dx = global_pos.x() - self._start_pos.x()
        dy = global_pos.y() - self._start_pos.y()
        min_w, min_h, max_w, max_h = self._limits()
        start = self._start_geom
        target = QRect(start)

        if "l" in self._edges:
            target.setLeft(min(start.left() + dx, start.right() - min_w + 1))
        if "r" in self._edges:
            target.setRight(max(start.right() + dx, start.left() + min_w - 1))
        if "t" in self._edges:
            target.setTop(min(start.top() + dy, start.bottom() - min_h + 1))
        if "b" in self._edges:
            target.setBottom(max(start.bottom() + dy, start.top() + min_h - 1))

        # Ограничение сверху (его ставит fit_dialog под размер экрана).
        if max_w < 16777215 and target.width() > max_w:
            if "l" in self._edges:
                target.setLeft(target.right() - max_w + 1)
            else:
                target.setRight(target.left() + max_w - 1)
        if max_h < 16777215 and target.height() > max_h:
            if "t" in self._edges:
                target.setTop(target.bottom() - max_h + 1)
            else:
                target.setBottom(target.top() + max_h - 1)

        if target != self.window.geometry():
            self.window.setGeometry(target)
            refresh_mask(self.window)
        return True

    def end(self):
        if self._active:
            self._active = False
            self._edges = ()
            refresh_mask(self.window)


class EdgeGrabber(QWidget):
    """Невидимая полоса у края окна: тянет размер, курсор по краю."""

    def __init__(self, window, edges, parent=None):
        super().__init__(parent if parent is not None else window)
        self._window = window
        self._edges = tuple(edges)
        self.setObjectName("edgeGrabber")
        # Тема красит все QWidget — гасим фон, чтобы полоска была прозрачной.
        self.setStyleSheet(
            "QWidget#edgeGrabber { background: transparent; border: none; }")
        self.setCursor(_EDGE_CURSORS.get(self._edges, Qt.ArrowCursor))
        self.setToolTip("Потяни, чтобы изменить размер окна")
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, event):  # noqa: N802 -Qt API
        pass  # полоска должна быть невидимой

    def _resizer(self):
        return getattr(self._window, "_frame_resizer", None)

    def mousePressEvent(self, event):  # noqa: N802 -Qt API
        if event.button() == Qt.LeftButton:
            resizer = self._resizer()
            if resizer is not None and resizer.begin(self._edges,
                                                     event.globalPos()):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 -Qt API
        resizer = self._resizer()
        if (resizer is not None and resizer.active
                and (event.buttons() & Qt.LeftButton)):
            resizer.update(event.globalPos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 -Qt API
        resizer = self._resizer()
        if resizer is not None and resizer.active:
            resizer.update(event.globalPos())
            resizer.end()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802 -Qt API
        # Двойной клик по краю не должен разворачивать окно.
        event.accept()


class _FrameWatcher(QObject):
    """Держит полоски на краях окна при изменении размера."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window

    def eventFilter(self, obj, event):  # noqa: N802 -Qt API
        try:
            if obj is self._window:
                if event.type() == QEvent.Resize:
                    layout_grabbers(self._window)
                    refresh_mask(self._window)
                elif event.type() == QEvent.Show:
                    layout_grabbers(self._window)
        except Exception:  # noqa: BLE001 - геометрия не критична
            pass
        return False


def layout_grabbers(window):
    """Расставить полоски по краям окна (вызывается при каждом resize)."""
    if not getattr(window, "_frame_edges_enabled", False):
        return
    grabbers = getattr(window, "_frame_grabbers", None)
    if not grabbers:
        return
    width, height = max(1, window.width()), max(1, window.height())
    margin = RESIZE_MARGIN
    corner = min(CORNER_SIZE, max(8, min(width, height) // 3))
    boxes = {
        ("t",): QRect(margin, 0, max(1, width - 2 * margin), margin),
        ("b",): QRect(margin, height - margin, max(1, width - 2 * margin),
                      margin),
        ("l",): QRect(0, margin, margin, max(1, height - 2 * margin)),
        ("r",): QRect(width - margin, margin, margin,
                      max(1, height - 2 * margin)),
        ("t", "l"): QRect(0, 0, corner, corner),
        ("t", "r"): QRect(width - corner, 0, corner, corner),
        ("b", "l"): QRect(0, height - corner, corner, corner),
        ("b", "r"): QRect(width - corner, height - corner, corner, corner),
    }
    for edges, grabber in grabbers.items():
        box = boxes.get(edges)
        if box is None:
            continue
        grabber.setGeometry(box)
        grabber.show()
        grabber.raise_()


def install_resize_edges(window):
    """Навесить полоски изменения размера по краям безрамочного окна."""
    try:
        if getattr(window, "_frame_resizer", None) is None:
            window._frame_resizer = WindowResizer(window)
            window._frame_grabbers = {
                edges: EdgeGrabber(window, edges) for edges in EDGE_SETS
            }
            window._frame_watcher = _FrameWatcher(window)
            window.installEventFilter(window._frame_watcher)
        window._frame_edges_enabled = True
        layout_grabbers(window)
        return window._frame_grabbers
    except Exception:  # noqa: BLE001 - без этого окно просто не растягивается
        return {}


def remove_resize_edges(window):
    """Спрятать полоски — когда возвращается системная рамка."""
    window._frame_edges_enabled = False
    for grabber in (getattr(window, "_frame_grabbers", {}) or {}).values():
        try:
            grabber.hide()
        except Exception:  # noqa: BLE001
            pass


def window_buttons_hidden() -> bool:
    """Скрывать ли кнопки окна (свернуть/развернуть/закрыть).

    Флаг ставит главное окно (app.setProperty): тайловые оконные менеджеры на
    Linux не умеют работать с кнопками внутри клиентской области.
    """
    app = QApplication.instance()
    if app is not None:
        value = app.property("hide_window_buttons")
        if value is not None:
            return bool(value)
    return False


class TitleButton(QToolButton):
    def __init__(self, text, tooltip="", role="", parent=None, icon_file=""):
        super().__init__(parent)
        self._glyph = text
        self._icon_file = icon_file
        self._icon_color = ""
        # У «крестика» при наведении фон становится красным — иконка белая.
        self._hover_color = "#ffffff" if role == "close" else ""
        self.setToolTip(tooltip)
        self.setObjectName("titleButton")
        if role:
            self.setProperty("role", role)
        self.setFixedSize(34, 26)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.ArrowCursor)
        if icon_file:
            self.set_icon_color()

    def set_icon_color(self, color: str = ""):
        """Перекрасить картинку под тему; пустой цвет — взять из палитры."""
        if not self._icon_file:
            return
        self._icon_color = color or theme_text_color()
        icon = load_button_icon(self._icon_file, self._icon_color)
        if icon.isNull():
            self.setIcon(QIcon())
            self.setText(self._glyph)
            return
        self.setText("")
        self.setIcon(icon)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))

    def enterEvent(self, event):  # noqa: N802 -Qt API
        if self._icon_file and self._hover_color:
            icon = load_button_icon(self._icon_file, self._hover_color)
            if not icon.isNull():
                self.setIcon(icon)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 -Qt API
        if self._icon_file and self._icon_color:
            icon = load_button_icon(self._icon_file, self._icon_color)
            if not icon.isNull():
                self.setIcon(icon)
        super().leaveEvent(event)


class DragGrip(WindowDragMixin, QWidget):
    """«Якорь» на полосе заголовка: за него зажимают и тащат окно."""

    def __init__(self, window, parent=None):
        super().__init__(parent if parent is not None else window)
        self._window = window
        self._drag_offset = None
        self.setObjectName("titleGrip")
        self.setFixedSize(*GRIP_SIZE)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(
            "Потяни мышкой, чтобы переместить окно\n"
            "Двойной клик — развернуть / восстановить")
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(
            "QWidget#titleGrip { background: transparent; border: none; }")

    def paintEvent(self, event):  # noqa: N802 -Qt API
        painter = QPainter(self)
        try:
            color = self.palette().color(QPalette.WindowText)
            color.setAlpha(120)
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            center_x = self.width() / 2.0
            center_y = self.height() / 2.0
            for shift in (-3.5, 3.5):
                for row in (-4.0, 0.0, 4.0):
                    painter.drawEllipse(
                        QPointF(center_x + shift, center_y + row), 1.5, 1.5)
        finally:
            painter.end()

    def mousePressEvent(self, event):  # noqa: N802 -Qt API
        if self._drag_start(event):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 -Qt API
        if self._drag_move(event):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 -Qt API
        if self._drag_end():
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802 -Qt API
        if event.button() == Qt.LeftButton:
            toggle_maximize(self._window)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class TitleBar(WindowDragMixin, QWidget):
    """Полоса заголовка: иконка, название, якорь, свернуть/развернуть/закрыть."""

    def __init__(self, window, title: str = "", show_title: bool = True):
        super().__init__(window)
        self.setObjectName("titleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self._window = window
        # Название в полосе окна: у главного окна выключено, потому что оно уже
        # есть в верхней панели (TopBar) — иначе дублируется в левом верхнем углу.
        self._show_title = bool(show_title)
        self._drag_offset = None
        # Курсор-«рука» подсказывает: за эту полосу окно можно тянуть мышкой.
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Зажми и тяни, чтобы переместить окно\n"
                        "Двойной клик — развернуть / восстановить")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 6, 4)
        layout.setSpacing(6)

        self.icon_label = QLabel("◈")
        self.icon_label.setObjectName("titleIcon")
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(title or window.windowTitle() or "AiConnector7")
        self.title_label.setObjectName("titleText")
        self.title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        layout.addWidget(self.title_label, 1)

        if not self._show_title:
            # Название живёт в верхней панели — здесь оставляем пустую полосу
            # (место для перетаскивания окна), без иконки и текста.
            self.icon_label.setVisible(False)
            self.title_label.setText("")
            self.title_label.setToolTip("")

        # «Якорь» убран: окно тянут за любое свободное место полосы заголовка.

        self.min_button = TitleButton("—", "Свернуть",
                                      icon_file=ICON_FILES["min"])
        self.min_button.clicked.connect(window.showMinimized)
        self.max_button = TitleButton("□", "Развернуть",
                                      icon_file=ICON_FILES["max"])
        self.max_button.clicked.connect(self.toggle_maximize)
        self.close_button = TitleButton("✕", "Закрыть", role="close",
                                        icon_file=ICON_FILES["close"])
        self.close_button.clicked.connect(window.close)
        for button in (self.min_button, self.max_button, self.close_button):
            layout.addWidget(button)

        # Клик по названию/иконке не должен «съедать» перетаскивание.
        for label in (self.icon_label, self.title_label):
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        window.windowTitleChanged.connect(self.set_title)
        self.apply_button_visibility()

    # ------------------------------------------------------------------- вид

    def set_title(self, title: str):
        if not self._show_title:
            return
        self.title_label.setText(title or "AiConnector7")

    def refresh_icons(self, color: str = ""):
        """Перекрасить картинки кнопок — вызывается после смены темы."""
        for button in (self.min_button, self.max_button, self.close_button):
            button.set_icon_color(color)

    def refresh_state(self):
        maximized = bool(self._window.windowState() & Qt.WindowMaximized)
        if self.max_button.icon().isNull():
            self.max_button.setText("❐" if maximized else "□")
        self.max_button.setToolTip("Восстановить" if maximized else "Развернуть")

    def apply_button_visibility(self):
        """Показать/скрыть кнопки окна (тумблер «Скрывать кнопки окна»).

        Когда кнопки скрыты, те же действия остаются в правом клике по полосе
        заголовка и в меню «Файл» — окно всегда можно закрыть.
        """
        hidden = window_buttons_hidden()
        for button in (self.min_button, self.max_button, self.close_button):
            button.setVisible(not hidden)
        if hidden:
            self.setToolTip(
                "Зажми и тяни, чтобы переместить окно\n"
                "Двойной клик — развернуть / восстановить\n"
                "Правый клик — свернуть / развернуть / закрыть")
        else:
            self.setToolTip("Зажми и тяни, чтобы переместить окно\n"
                            "Двойной клик — развернуть / восстановить")

    def contextMenuEvent(self, event):  # noqa: N802 -Qt API
        """Правый клик по полосе заголовка: те же действия, что у кнопок."""
        try:
            maximized = bool(self._window.windowState() & Qt.WindowMaximized)
            menu = QMenu(self)
            min_action = menu.addAction("Свернуть")
            max_action = menu.addAction("Восстановить" if maximized else "Развернуть")
            menu.addSeparator()
            close_action = menu.addAction("Закрыть")
            chosen = menu.exec_(event.globalPos())
            if chosen is min_action:
                self._window.showMinimized()
            elif chosen is max_action:
                self.toggle_maximize()
            elif chosen is close_action:
                self._window.close()
        except Exception:  # noqa: BLE001 - меню не должно ронять окно
            pass
        event.accept()

    def toggle_maximize(self):
        toggle_maximize(self._window)
        self.refresh_state()

    # --------------------------------------------------------- перетаскивание

    def mousePressEvent(self, event):  # noqa: N802 -Qt API
        if self._drag_start(event):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 -Qt API
        if self._drag_move(event):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 -Qt API
        if self._drag_end():
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802 -Qt API
        if event.button() == Qt.LeftButton:
            self.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------- интеграция

def attach_title_bar(window, title: str = "", show_title: bool = True):
    """Убрать системную рамку и вставить свою полосу заголовка.

    show_title=False — полоса без названия и иконки: только место для
    перетаскивания и кнопки окна (название уже показано в верхней панели).
    """
    try:
        window.setWindowFlag(Qt.FramelessWindowHint, True)
        if isinstance(window, QMainWindow):
            central = window.centralWidget()
            root = QWidget(window)
            root.setObjectName("windowRoot")
            layout = QVBoxLayout(root)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            bar = TitleBar(window, title, show_title)
            layout.addWidget(bar)
            if central is not None:
                layout.addWidget(central, 1)
            window.setCentralWidget(root)
            return bar

        previous = window.layout()
        holder = QWidget(window)
        holder.setObjectName("windowRoot")
        if previous is not None:
            holder.setLayout(previous)  # layout переезжает к новому родителю
        outer = QVBoxLayout(window)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = TitleBar(window, title)
        outer.addWidget(bar)
        outer.addWidget(holder, 1)
        window.setLayout(outer)
        return bar
    except Exception:  # noqa: BLE001 - безрамочный режим не критичен
        return None


def dialogs_frameless() -> bool:
    """Включена ли «своя рамка окна» (флаг ставит главное окно)."""
    app = QApplication.instance()
    if app is not None:
        value = app.property("frameless_windows")
        if value is not None:
            return bool(value)
    return True


def apply_dialog_frame(dialog, title: str = "", resizable: bool = True):
    """Своя рамка + перетаскивание + изменение размера для диалога.

    Даёт диалоговому окну то же поведение, что у главного: заголовок в цветах
    темы, «якорь» для перемещения, полоски по краям для размера и маска
    скругления под выбранный режим углов.
    """
    if not dialogs_frameless():
        return None
    try:
        bar = attach_title_bar(dialog, title or dialog.windowTitle())
        if bar is None:
            return None
        dialog._title_bar = bar
        dialog._frame_auto_mask = True
        dialog.frame_radius = frame_radius()
        if resizable:
            try:
                dialog.setMinimumSize(max(360, dialog.minimumWidth()),
                                      max(200, dialog.minimumHeight()))
            except Exception:  # noqa: BLE001
                pass
            install_resize_edges(dialog)
        load_screen_limits(dialog)
        refresh_mask(dialog)
        return bar
    except Exception:  # noqa: BLE001
        return None


def load_screen_limits(window):
    """Не дать окну оказаться больше экрана (по ширине и высоте)."""
    try:
        from app.ui.dialog_fit import fit_dialog
    except Exception:  # noqa: BLE001
        return
    try:
        fit_dialog(window, window.width(), window.height(),
                   min_width=min(560, window.width()),
                   min_height=min(360, window.height()))
    except Exception:  # noqa: BLE001
        pass


def apply_window_mask(window, radius: int):
    """Скруглить углы безрамочного окна (radius<=0 — прямые углы)."""
    try:
        if radius <= 0 or window.isMaximized() or window.isFullScreen():
            window.clearMask()
            return
        width, height = window.width(), window.height()
        if width < 4 or height < 4:
            return
        path = QPainterPath()
        path.addRoundedRect(0, 0, width, height, radius, radius)
        window.setMask(QRegion(path.toFillPolygon().toPolygon()))
    except Exception:  # noqa: BLE001
        pass


def detach_title_bar(window, bar):
    """Вернуть системную рамку, не удаляя виджет заголовка.

    Виджет только прячется: удалять его во время работы окна рискованно
    (Qt может уронить процесс при разрушении окна), а вернуть рамку обратно
    можно одним show().
    """
    try:
        window.setWindowFlag(Qt.FramelessWindowHint, False)
        window.clearMask()
        remove_resize_edges(window)
        if bar is not None:
            bar.hide()
    except Exception:  # noqa: BLE001
        pass
