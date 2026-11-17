"""Верхняя панель главного окна: название программы с версией и меню.

Раньше наверху была стандартная полоса QMenuBar: системный фон, пункты
«Файл / Вид / Глобальные настройки» без названия программы. Теперь:

  * сверху — «AiConnector v0.6.3» (название и версия из app/__init__.py);
  * ниже — кнопки действий в рамках (своё меню у каждой);
  * фон панели прозрачный, поэтому при включённой картинке обои видно сквозь
    неё, а не плотную серую полосу.
"""

from app.qt_compat.QtCore import Qt
from app.qt_compat.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget
)


def _repolish(widget):
    """Перерисовать виджет после смены динамического свойства (QSS)."""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


class TopBar(QWidget):
    """Шапка окна: заголовок с версией + кнопки меню в рамках."""

    def __init__(self, app_name: str = "AiConnector", version: str = "",
                 parent=None):
        super().__init__(parent)
        self.setObjectName("topPanel")
        self._transparent = True
        self.setProperty("flat", "true")
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # --- строка названия: имя программы и версия
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 4, 0)
        head.setSpacing(8)
        self.title_label = QLabel(str(app_name or "AiConnector"))
        self.title_label.setObjectName("appTitle")
        self.title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        head.addWidget(self.title_label)

        self.version_label = QLabel("v" + str(version or "0.0.0"))
        self.version_label.setObjectName("appVersion")
        self.version_label.setToolTip("Версия программы")
        head.addWidget(self.version_label)
        head.addStretch(1)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("mutedLabel")
        head.addWidget(self.hint_label)
        layout.addLayout(head)

        # --- строка кнопок: каждое меню — кнопка в своей рамке
        self.buttons_row = QHBoxLayout()
        self.buttons_row.setContentsMargins(0, 0, 0, 0)
        self.buttons_row.setSpacing(8)
        self.buttons_row.addStretch(0)
        layout.addLayout(self.buttons_row)

    # ------------------------------------------------------------------ вид

    def set_title(self, app_name: str, version: str = ""):
        self.title_label.setText(str(app_name or "AiConnector"))
        self.version_label.setText("v" + str(version or "0.0.0"))

    def set_hint(self, text: str):
        self.hint_label.setText(str(text or ""))

    def set_transparent(self, transparent: bool = True):
        """Прозрачная панель (часть окна) или плотная плашка в цвет темы.

        В прозрачном режиме у панели и у кнопок нет ни фона, ни рамки, поэтому
        сквозь шапку видно фоновую картинку. При наведении кнопка подсвечивается.
        """
        value = bool(transparent)
        self._transparent = value
        self.setProperty("flat", "true" if value else "false")
        for button in self.buttons():
            button.setProperty("flat", "true" if value else "false")
            _repolish(button)
        _repolish(self)

    def is_transparent(self) -> bool:
        return bool(getattr(self, "_transparent", True))

    def _button(self, title: str, tooltip: str = "") -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("menuButton")
        button.setProperty("flat", "true" if self._transparent else "false")
        button.setText(title)
        button.setToolTip(tooltip or title)
        button.setCursor(Qt.ArrowCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        return button

    def add_menu_button(self, title: str, menu, tooltip: str = ""):
        """Кнопка с выпадающим меню."""
        button = self._button(title + "  ▾", tooltip)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.InstantPopup)
        self.buttons_row.addWidget(button)
        return button

    def add_action_button(self, title: str, callback, tooltip: str = ""):
        """Кнопка-действие: сразу открывает окно (например «Инструменты»)."""
        button = self._button(title, tooltip)
        button.clicked.connect(lambda _checked=False: callback())
        self.buttons_row.addWidget(button)
        return button

    def add_stretch(self):
        self.buttons_row.addStretch(1)

    def buttons(self) -> list:
        items = []
        for index in range(self.buttons_row.count()):
            widget = self.buttons_row.itemAt(index).widget()
            if widget is not None:
                items.append(widget)
        return items
