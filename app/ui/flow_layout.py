"""Раскладка «потоком»: элементы переносятся на новую строку целиком.

Почему не QGridLayout: при нехватке ширины он сжимает ячейки до одинакового
размера, а виджеты с minimumWidth вылезают за свою ячейку и наезжают друг на
друга (текст поверх кнопок). Здесь каждый элемент получает свой фактический
sizeHint, поэтому наложение невозможно, а перенос идёт целыми виджетами.
"""

from app.qt_compat.QtCore import QPoint, QRect, QSize, Qt
from app.qt_compat.QtWidgets import QLayout


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, spacing: int = 6):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(max(0, int(spacing)))
        self._items = []

    def __del__(self):
        while self.count():
            self.takeAt(0)

    # ------------------------------------------------- QLayout: обязательное

    def addItem(self, item):  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802 - Qt API
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):  # noqa: N802 - Qt API
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802 - Qt API
        # Qt6: Qt.Orientations больше нет, флаги направлений — это Qt.Orientation.
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width):  # noqa: N802 - Qt API
        return self._do_layout(QRect(0, 0, int(width), 0), dry_run=True)

    def setGeometry(self, rect):  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._do_layout(rect, dry_run=False)

    def sizeHint(self):  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    # ------------------------------------------------------------ внутреннее

    def _visible_items(self):
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            yield item

    def _do_layout(self, rect: QRect, dry_run: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        available = max(1, area.width())
        spacing = self.spacing()
        x, y = area.x(), area.y()
        line_height = 0

        for item in self._visible_items():
            hint = item.sizeHint()
            # Шире доступного — прижимаем к доступному (текст обрежется, но
            # не налезет на соседа).
            width = min(hint.width(), available)
            height = hint.height()

            if x + width > area.x() + available and x > area.x():
                x = area.x()
                y = y + line_height + spacing
                line_height = 0

            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, height)))
            x = x + width + spacing
            line_height = max(line_height, height)

        return y + line_height - rect.y() + margins.bottom()
