"""Поле ввода запроса: растёт по высоте, не ломая раскладку чата.

QLineEdit не умеет расширяться, а большой QTextEdit съедает место у чата.
Здесь поле от 1 до N строк (по умолчанию 8): Enter — отправить,
Shift+Enter — новая строка.

Про мигание интерфейса при наборе текста (было реальной проблемой):
  1. Высота пересчитывалась на КАЖДУЮ букву и от каждого события документа
     (textChanged + documentSizeChanged + resizeEvent), то есть по 3 раза на
     символ — всё это дёргало раскладку внизу окна.
  2. Вертикальный ползунок включался «по необходимости»: он менял ширину
     текста, текст переставал/начинал переноситься, высота прыгала обратно —
     классическая петля «высота -> ползунок -> перенос -> высота».
Теперь высота считается по ЦЕЛОМУ числу визуальных строк, ползунок не
показывается вовсе (прокрутка колесом и стрелками работает), а пересчёт
откладывается и склеивается таймером.
"""

from app.qt_compat.QtCore import QSize, Qt, QTimer, pyqtSignal
from app.qt_compat.QtWidgets import QPlainTextEdit


class PromptInput(QPlainTextEdit):
    submitted = pyqtSignal()

    #: Пауза после последнего нажатия, чтобы не пересчитывать на каждую букву.
    DEBOUNCE_MS = 45

    def __init__(self, parent=None, min_height: int = 44, max_lines: int = 8):
        super().__init__(parent)
        self.setObjectName("promptInput")
        self.setPlaceholderText(
            "Опиши задачу…  (Enter — отправить, Shift+Enter — новая строка)")
        self.setTabChangesFocus(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Ползунок не показываем: иначе он отнимает ширину у текста, перенос
        # меняется и поле начинает дёргаться на границе высоты.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._min_height = max(32, int(min_height))
        self._max_lines = max(2, int(max_lines))
        self._line_height = max(12, self.fontMetrics().lineSpacing())
        self._max_height = (self._min_height
                            + self._line_height * (self._max_lines - 1))
        self._resizing = False
        self._last_width = 0

        self.setMinimumHeight(self._min_height)
        # Только setFixedHeight: setMaximumHeight после него СНИМАЕТ фиксацию,
        # и раскладка начинает сама растягивать поле (был скачок 46 -> 92).
        self.setFixedHeight(self._min_height)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self._run_adjust)

        self.textChanged.connect(self._schedule_adjust)
        try:
            self.document().documentLayout().documentSizeChanged.connect(
                self._schedule_adjust)
        except (AttributeError, RuntimeError):
            pass

    # ------------------------------------------------------------- геометрия

    def _refresh_metrics(self):
        """Пересчёт мерок после смены шрифта (тема, масштаб)."""
        line = self._doc_line_height()
        if int(round(line)) == int(round(self._line_height)):
            return
        self._line_height = line
        self._max_height = (self._min_height
                           + int(round(self._line_height * (self._max_lines - 1))))

    def _doc_line_height(self) -> float:
        """Высота ОДНОЙ строки так, как её считает сам документ."""
        block = self.document().firstBlock()
        try:
            layout = block.layout()
            if layout is not None and layout.lineCount() > 0:
                return max(1.0, float(layout.lineAt(0).height()))
        except (AttributeError, RuntimeError):
            pass
        return float(max(12, self.fontMetrics().lineSpacing()))

    def _visual_lines(self) -> int:
        """Сколько ВИЗУАЛЬНЫХ строк занимает текст (с учётом переноса)."""
        document = self.document()
        try:
            layout = document.documentLayout()
        except (AttributeError, RuntimeError):
            return 1
        line = max(1.0, self._line_height)
        total, guard = 0.0, 0
        block = document.begin()
        while block.isValid() and guard < 5000:
            rect = layout.blockBoundingRect(block)
            total += max(line, float(rect.height()))
            block = block.next()
            guard += 1
        if total <= 0:
            return 1
        return max(1, int(round(total / line)))

    def _target_height(self) -> int:
        lines = min(self._max_lines, self._visual_lines())
        target = self._min_height + int(round((lines - 1) * self._line_height))
        return max(self._min_height, min(self._max_height, target))


    def _schedule_adjust(self, *args):
        self._timer.start()

    def _run_adjust(self):
        self._refresh_metrics()
        self._adjust_height()

    def _adjust_height(self):
        if self._resizing:
            return
        target = self._target_height()
        if target == self.height():
            return
        self._resizing = True
        try:
            self.setFixedHeight(target)
        finally:
            self._resizing = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Реагируем только на смену ширины: иначе собственный setFixedHeight
        # снова и снова планирует пересчёт.
        width = event.size().width()
        changed = width != self._last_width
        self._last_width = width
        if changed and not self._resizing:
            self._schedule_adjust()

    def sizeHint(self):  # noqa: N802 - Qt API
        return QSize(super().sizeHint().width(), self.height())

    # ----------------------------------------------------------------- ввод

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and not (event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier))):
            self.timer_flush()
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def timer_flush(self):
        """Применить отложенный пересчёт немедленно (перед отправкой)."""
        if self._timer.isActive():
            self._timer.stop()
        self._run_adjust()

    # ---------------------------------------------- совместимость с QLineEdit

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value):  # noqa: N802 - API QLineEdit
        self.setPlainText(value or "")
        self._schedule_adjust()

    def clear(self):
        super().clear()
        self._schedule_adjust()
