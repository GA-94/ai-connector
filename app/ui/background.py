"""Фон главного окна: картинка (или градиент по теме) + затемнение.

Слой рисуется вручную в paintEvent, а панели интерфейса в режиме glass
полупрозрачные - поэтому картинка видна сквозь них.
"""

from app.qt_compat.QtCore import QRect, QSize, Qt
from app.qt_compat.QtGui import (
    QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient
)
from app.qt_compat.QtWidgets import QWidget


class BackgroundWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("centralRoot")
        self.setAutoFillBackground(False)
        # Фон закрывает всю площадь виджета целиком — пусть Qt не стирает его
        # перед paintEvent (это заметно уменьшает мигание при перестройке).
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._colors = {}
        self._pixmap = QPixmap()
        self._scaled = QPixmap()
        self._scaled_size = QSize()
        self._path = ""
        self._dim = 45

    # ------------------------------------------------------------------ setup

    def set_appearance(self, colors: dict, image_path: str = "", dim: int = 45):
        self._colors = dict(colors or {})
        self._dim = max(0, min(90, int(dim)))
        image_path = (image_path or "").strip()
        if image_path != self._path:
            self._path = image_path
            pixmap = QPixmap(image_path) if image_path else QPixmap()
            self._pixmap = pixmap if not pixmap.isNull() else QPixmap()
            # Масштабированный кэш больше не актуален.
            self._scaled = QPixmap()
            self._scaled_size = QSize()
        self.update()

    def has_image(self) -> bool:
        return not self._pixmap.isNull()

    def _scaled_pixmap(self, size: QSize) -> QPixmap:
        """Кэш масштабированной картинки.

        Без него SmoothTransformation выполнялся при каждой перерисовке окна, а
        при наборе текста окно перерисовывается постоянно — отсюда рывки.
        """
        if self._pixmap.isNull():
            return QPixmap()
        if (not self._scaled.isNull() and self._scaled_size == size):
            return self._scaled
        self._scaled = self._pixmap.scaled(
            size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self._scaled_size = QSize(size)
        return self._scaled

    # ------------------------------------------------------------------ paint

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        rect = self.rect()

        if not self._pixmap.isNull():
            scaled = self._scaled_pixmap(rect.size())
            source = QRect(
                max(0, (scaled.width() - rect.width()) // 2),
                max(0, (scaled.height() - rect.height()) // 2),
                rect.width(), rect.height())
            painter.drawPixmap(rect, scaled, source)
            overlay = QColor(self._colors.get("window_solid", "#12151c"))
            overlay.setAlpha(int(255 * self._dim / 100.0))
            painter.fillRect(rect, overlay)
        else:
            gradient = QLinearGradient(0, 0, rect.width(), rect.height())
            gradient.setColorAt(0.0, QColor(self._colors.get("bg_grad_from", "#141925")))
            gradient.setColorAt(1.0, QColor(self._colors.get("bg_grad_to", "#0b0e14")))
            painter.fillRect(rect, gradient)

            glow = QColor(self._colors.get("glow", "#2b4b8a"))
            glow.setAlpha(70)
            transparent = QColor(glow)
            transparent.setAlpha(0)
            radial = QRadialGradient(
                rect.width() * 0.18, rect.height() * 0.08,
                max(rect.width(), rect.height()) * 0.75)
            radial.setColorAt(0.0, glow)
            radial.setColorAt(1.0, transparent)
            painter.fillRect(rect, radial)

        painter.end()
