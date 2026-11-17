"""Оформление AI-Connector: палитры и глобальный stylesheet (QSS).

Одна точка правды по цветам и скруглениям:
  * build_stylesheet() - стили всех виджетов Qt;
  * chat_colors()      - палитра для HTML-рендера сообщений;
  * apply_theme()      - применить тему ко всему QApplication.

Темы: dark (графит), midnight (полночь), light (светлая).
glass=True включается, когда на фоне стоит картинка: панели становятся
полупрозрачными и изображение просвечивает сквозь интерфейс.

Скругления (4 режима, см. CORNER_LABELS):
  1 - все углы закруглённые;
  2 - закруглены только кнопки (панели/поля прямые);
  3 - закруглены только панели (кнопки/поля прямые);
  4 - всё скруглено по максимуму.
Дополнительно флаг console_square делает углы консоли прямыми.
"""

from string import Template

from app.qt_compat.QtGui import QColor, QPalette

THEME_ORDER = ("dark", "gray", "midnight", "light")

THEME_LABELS = {
    "dark": "Графит (тёмная)",
    "gray": "Серый (как Discord)",
    "midnight": "Полночь",
    "light": "Светлая",
}

CORNER_ORDER = (1, 2, 3, 4)

CORNER_LABELS = {
    1: "1. Все углы закруглённые",
    2: "2. Скруглены только кнопки",
    3: "3. Скруглены только панели",
    4: "4. Всё скруглено (максимум)",
}

BASE = {
    "dark": {
        "window": "#12151c",
        "window_solid": "#12151c",
        "surface": "#1a1f2b",
        "surface_alt": "#232a38",
        "input_bg": "#151a24",
        "chat_bg": "#151a26",
        "hover_bg": "#252d3d",
        "pressed_bg": "#2b3446",
        "accent_soft": "#25324a",
        "scroll_handle": "#39445a",
        "border": "#2b3344",
        "text": "#e7eaf1",
        "text_muted": "#8d97ab",
        "accent": "#4c8dff",
        "accent_hover": "#6ba1ff",
        "accent_pressed": "#3a78e0",
        "accent_text": "#0b1220",
        "danger": "#e46257",
        "success": "#42c08a",
        "selection": "#31497a",
        "user_bubble": "#1f2d46",
        "agent_bubble": "#1a1f2b",
        "thought_bg": "#191d27",
        "code_bg": "#11151f",
        "code_text": "#d7dceb",
        "link": "#7fb0ff",
        "heading": "#ffffff",
        "bg_grad_from": "#141925",
        "bg_grad_to": "#0b0e14",
        "glow": "#2b4b8a",
        "pygments": "monokai",
    },
    # Нейтральный серый без «вырви-глаза»: как в Discord — фон #313338,
    # панели #2b2d31, акцент #5865f2. Глазам спокойно и днём, и вечером.
    "gray": {
        "window": "#313338",
        "window_solid": "#313338",
        "surface": "#2b2d31",
        "surface_alt": "#383a40",
        "input_bg": "#1e1f22",
        "chat_bg": "#313338",
        "hover_bg": "#3f4147",
        "pressed_bg": "#45474d",
        "accent_soft": "#404675",
        "scroll_handle": "#4e5058",
        "border": "#26272b",
        "text": "#f2f3f5",
        "text_muted": "#b5bac1",
        "accent": "#5865f2",
        "accent_hover": "#6b76ff",
        "accent_pressed": "#4752c4",
        "accent_text": "#ffffff",
        "danger": "#da373c",
        "success": "#23a55a",
        "selection": "#404675",
        "user_bubble": "#3f4147",
        "agent_bubble": "#2b2d31",
        "thought_bg": "#2e3035",
        "code_bg": "#2b2d31",
        "code_text": "#dbdee1",
        "link": "#00a8fc",
        "heading": "#ffffff",
        "bg_grad_from": "#34363c",
        "bg_grad_to": "#2b2d31",
        "glow": "#3b3f63",
        "pygments": "monokai",
    },
    "midnight": {
        "window": "#07080c",
        "window_solid": "#07080c",
        "surface": "#0e1118",
        "surface_alt": "#161b25",
        "input_bg": "#0b0e15",
        "chat_bg": "#090b11",
        "hover_bg": "#1a2130",
        "pressed_bg": "#212a3d",
        "accent_soft": "#241f4a",
        "scroll_handle": "#2b3347",
        "border": "#1d2431",
        "text": "#e9ecf5",
        "text_muted": "#7f879b",
        "accent": "#8b6cff",
        "accent_hover": "#a48cff",
        "accent_pressed": "#7452f0",
        "accent_text": "#0b0716",
        "danger": "#ef6b60",
        "success": "#3fd39a",
        "selection": "#3a2f6e",
        "user_bubble": "#1b1b3a",
        "agent_bubble": "#0e1118",
        "thought_bg": "#11131c",
        "code_bg": "#0a0c13",
        "code_text": "#d5d9e8",
        "link": "#b6a3ff",
        "heading": "#ffffff",
        "bg_grad_from": "#0e1119",
        "bg_grad_to": "#05060a",
        "glow": "#4a2f8f",
        "pygments": "monokai",
    },
    "light": {
        "window": "#f2f5fa",
        "window_solid": "#f2f5fa",
        "surface": "#ffffff",
        "surface_alt": "#eef1f7",
        "input_bg": "#ffffff",
        "chat_bg": "#ffffff",
        "hover_bg": "#e8eefc",
        "pressed_bg": "#dde6fa",
        "accent_soft": "#e3edff",
        "scroll_handle": "#c3ccdd",
        "border": "#d8dee9",
        "text": "#1b2130",
        "text_muted": "#6d778c",
        "accent": "#2f6fed",
        "accent_hover": "#4783f5",
        "accent_pressed": "#2559c4",
        "accent_text": "#ffffff",
        "danger": "#d0453a",
        "success": "#1e9e5f",
        "selection": "#cfe0ff",
        "user_bubble": "#eef4ff",
        "agent_bubble": "#ffffff",
        "thought_bg": "#f4f6fb",
        "code_bg": "#f4f6fb",
        "code_text": "#1f2534",
        "link": "#1f5fd8",
        "heading": "#0f172a",
        "bg_grad_from": "#f7f9fd",
        "bg_grad_to": "#e6ecf7",
        "glow": "#c9dbff",
        "pygments": "friendly",
    },
}


def normalize(name: str) -> str:
    """Неизвестное имя темы -> dark (чтобы не падать на битом settings.ini)."""
    name = (name or "").strip().lower()
    return name if name in BASE else "dark"


def label(name: str) -> str:
    return THEME_LABELS.get(normalize(name), THEME_LABELS["dark"])


# ------------------------------------------------------------------ скругления

def normalize_corners(value) -> int:
    """Привести режим скругления к 1..4 (по умолчанию 1)."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 1
    return number if number in CORNER_LABELS else 1


def corner_label(value) -> str:
    return CORNER_LABELS[normalize_corners(value)]


def radii_for(corners=1, console_square: bool = False) -> dict:
    """Радиусы для QSS по режиму скругления.

    panel  - панели, чат, текстовые области;
    box    - список чатов, меню, группы, таблицы;
    button - кнопки и тулбары;
    input  - поля ввода;
    item   - элементы списков и пункты меню;
    console- консоль (может быть прямой отдельным флагом).
    """
    mode = normalize_corners(corners)
    panel, box, button, fill, item = 16, 12, 10, 10, 8
    if mode == 1:
        panel, box, button, fill, item = 16, 12, 12, 12, 8
    elif mode == 2:
        panel, box, button, fill, item = 0, 0, 12, 12, 8
    elif mode == 3:
        panel, box, button, fill, item = 16, 14, 0, 0, 0
    else:  # 4 - всё скруглено
        panel, box, button, fill, item = 22, 18, 16, 16, 12
    return {
        "panel": panel,
        "box": box,
        "button": button,
        "input": fill,
        "item": item,
        "console": 0 if console_square else panel,
    }


def _rgba(hex_color: str, alpha: float) -> str:
    color = QColor(hex_color)
    if not color.isValid():
        return hex_color
    return "rgba({0}, {1}, {2}, {3:g})".format(
        color.red(), color.green(), color.blue(), alpha)


def colors_for(name: str = "dark", glass: bool = False) -> dict:
    """Полный набор токенов для QSS/фона. glass -> полупрозрачные панели."""
    base = BASE[normalize(name)]
    colors = dict(base)
    colors["console_bg"] = base["input_bg"] if not glass else _rgba(base["input_bg"], 0.80)
    colors["chat_bg"] = base["chat_bg"] if not glass else "transparent"
    colors["title_bg"] = base["surface_alt"]
    # Меню и кнопки верхней панели: в режиме картинки фон полупрозрачный,
    # чтобы обои просвечивали (просьба: убрать плотный фон у меню).
    colors["menu_bg"] = base["surface_alt"] if not glass else _rgba(base["surface_alt"], 0.92)
    colors["menu_button_bg"] = base["surface_alt"] if not glass else _rgba(base["surface_alt"], 0.55)
    if glass:
        colors["window"] = "transparent"
        colors["surface"] = _rgba(base["surface"], 0.84)
        colors["input_bg"] = _rgba(base["input_bg"], 0.82)
        colors["title_bg"] = _rgba(base["surface_alt"], 0.90)
    return colors


def chat_colors(name: str = "dark", glass: bool = False) -> dict:
    """Палитра для HTML-рендера сообщений (см. markdown_render)."""
    base = BASE[normalize(name)]
    return {
        "text": base["text"],
        "muted": base["text_muted"],
        "accent": base["accent"],
        "success": base["success"],
        "link": base["link"],
        "heading": base["heading"],
        "border": base["border"],
        "code_bg": base["code_bg"],
        "code_text": base["code_text"],
        "user_bubble": base["user_bubble"],
        "agent_bubble": base["agent_bubble"],
        "thought_bg": base["thought_bg"],
        "quote_bg": base["surface_alt"],
        "table_head": base["surface_alt"],
        "pygments": base["pygments"],
    }


_QSS = Template("""
QWidget {
    color: $text;
    font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif;
    font-size: 10pt;
    selection-background-color: $selection;
    selection-color: $text;
}
QMainWindow {
    background-color: $window;
}
QDialog {
    background-color: $window_solid;
}
QWidget#windowRoot {
    background-color: $window;
}
QWidget#topPanel {
    background: transparent;
    border: none;
}
QWidget#topPanel[flat="false"] {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: ${r_panel}px;
}
QLabel#appTitle {
    color: $text;
    font-size: 15pt;
    font-weight: 700;
}
QLabel#appVersion {
    color: $text_muted;
    font-size: 9pt;
    font-weight: 600;
}
QToolButton#menuButton {
    background-color: $menu_button_bg;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_button}px;
    padding: 6px 14px;
    font-size: 10pt;
}
QToolButton#menuButton:hover {
    background-color: $hover_bg;
    border-color: $accent;
}
QToolButton#menuButton:pressed {
    background-color: $pressed_bg;
}
QToolButton#menuButton[flat="true"] {
    background: transparent;
    border: 1px solid transparent;
}
QToolButton#menuButton[flat="true"]:hover {
    background-color: $hover_bg;
    border-color: $accent;
}
QWidget#sidePanel {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: ${r_panel}px;
}
QWidget#sidePanel QListWidget {
    background: transparent;
    border: none;
    padding: 0px;
}
QLabel {
    background: transparent;
    color: $text;
}
QLabel#mutedLabel {
    color: $text_muted;
    font-size: 8pt;
    font-weight: 700;
    letter-spacing: 1px;
}
QListWidget {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: ${r_box}px;
    padding: 6px;
    outline: none;
}
QListWidget::item {
    padding: 9px 12px;
    border-radius: ${r_item}px;
    margin: 1px 0px;
}
QListWidget::item:hover {
    background-color: $hover_bg;
}
QListWidget::item:selected {
    background-color: $accent_soft;
    color: $text;
}
QMenuBar {
    background: transparent;
    color: $text;
    padding: 2px;
}
QMenuBar::item {
    background: transparent;
    color: $text;
    padding: 6px 12px;
    border-radius: ${r_item}px;
}
QMenuBar::item:selected {
    background-color: $hover_bg;
    color: $text;
}
QMenuBar::item:pressed {
    background-color: $pressed_bg;
    color: $text;
}
QMenu {
    background-color: $menu_bg;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_box}px;
    padding: 6px;
}
QMenu::item {
    color: $text;
    padding: 7px 26px 7px 14px;
    border-radius: ${r_item}px;
}
QMenu::item:selected {
    background-color: $accent_soft;
    color: $text;
}
QMenu::item:disabled {
    color: $text_muted;
}
QMenu::separator {
    height: 1px;
    background-color: $border;
    margin: 6px 8px;
}
QPushButton {
    background-color: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_button}px;
    padding: 8px 16px;
}
QPushButton:hover {
    background-color: $hover_bg;
    border-color: $accent;
}
QPushButton:pressed {
    background-color: $pressed_bg;
}
QPushButton:disabled {
    color: $text_muted;
    border-color: $border;
    background-color: $surface;
}
QPushButton[role="accent"] {
    background-color: $accent;
    color: $accent_text;
    border: 1px solid $accent;
    font-weight: 600;
}
QPushButton[role="accent"]:hover {
    background-color: $accent_hover;
    border-color: $accent_hover;
}
QPushButton[role="accent"]:pressed {
    background-color: $accent_pressed;
}
QPushButton[role="accent"]:disabled {
    background-color: $surface_alt;
    color: $text_muted;
    border-color: $border;
}
QPushButton[role="danger"] {
    background-color: $danger;
    color: #ffffff;
    border: 1px solid $danger;
    font-weight: 600;
}
QPushButton[role="danger"]:hover {
    background-color: $danger;
}
QPushButton[role="ghost"] {
    background-color: transparent;
}
QPushButton[state="ok"] {
    color: $success;
    border-color: $success;
}
QPushButton[state="low"] {
    color: $danger;
    border-color: $danger;
    font-weight: 600;
}
QPushButton[state="error"] {
    color: $danger;
}
QToolButton {
    background-color: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_button}px;
    padding: 6px 10px;
    font-size: 12pt;
}
QToolButton:hover {
    background-color: $hover_bg;
    border-color: $accent;
}
QToolButton:pressed {
    background-color: $pressed_bg;
}
QWidget#titleBar {
    background-color: $title_bg;
    border-bottom: 1px solid $border;
}
QLabel#titleText {
    color: $text;
    font-weight: 600;
}
QLabel#titleIcon {
    color: $accent;
    font-size: 12pt;
}
QToolButton#titleButton {
    background: transparent;
    color: $text;
    border: none;
    border-radius: ${r_item}px;
    padding: 2px;
    font-size: 11pt;
}
QToolButton#titleButton:hover {
    background-color: $hover_bg;
}
QToolButton#titleButton[role="close"]:hover {
    background-color: $danger;
    color: #ffffff;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: $input_bg;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_input}px;
    padding: 8px 12px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid $accent;
}
QLineEdit:disabled {
    color: $text_muted;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_box}px;
    selection-background-color: $accent_soft;
    selection-color: $text;
    padding: 4px;
}
QTextBrowser, QPlainTextEdit, QTextEdit {
    background-color: $console_bg;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_panel}px;
    padding: 6px;
}
QTextBrowser#chatView {
    background-color: $chat_bg;
    border: 1px solid $border;
    border-radius: ${r_panel}px;
    padding: 8px;
}
QPlainTextEdit#consoleView {
    background-color: $console_bg;
    color: $text;
    border-radius: ${r_console}px;
    font-family: "Cascadia Mono", "Consolas", "Noto Sans Mono", "Courier New", monospace;
    font-size: 9pt;
}
QPlainTextEdit#promptInput {
    background-color: $input_bg;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_input}px;
    padding: 6px 10px;
}
QPlainTextEdit#promptInput:focus {
    border: 1px solid $accent;
}
QTextBrowser#chatView:focus, QPlainTextEdit#consoleView:focus {
    border: 1px solid $accent;
}
QCheckBox {
    color: $text;
    spacing: 8px;
    background: transparent;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: ${r_item}px;
    border: 1px solid $border;
    background-color: $input_bg;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: $accent;
}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: $accent;
    border-color: $accent;
}
QGroupBox {
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_box}px;
    margin-top: 18px;
    padding: 16px 12px 12px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0px 6px;
    color: $text_muted;
    background-color: $window_solid;
}
QSplitter::handle {
    background: transparent;
}
QSplitter::handle:horizontal {
    width: 12px;
}
QSplitter::handle:vertical {
    height: 12px;
}
QSplitter::handle:hover {
    background-color: $accent_soft;
    border-radius: 4px;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $scroll_handle;
    border-radius: 4px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover {
    background: $accent;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: $scroll_handle;
    border-radius: 4px;
    min-width: 32px;
}
QScrollBar::handle:horizontal:hover {
    background: $accent;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
QToolTip {
    background-color: $surface_alt;
    color: $text;
    border: 1px solid $border;
    padding: 6px 8px;
}
QTableWidget, QTableView, QTreeWidget, QTreeView {
    background-color: $surface;
    alternate-background-color: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: ${r_box}px;
    gridline-color: $border;
    selection-background-color: $accent_soft;
    selection-color: $text;
}
QHeaderView::section {
    background-color: $surface_alt;
    color: $text_muted;
    border: none;
    border-bottom: 1px solid $border;
    padding: 7px 8px;
}
QDialogButtonBox QPushButton {
    min-width: 96px;
}
QFrame#actionBar {
    background-color: $surface;
    border: 1px solid $border;
    border-radius: ${r_panel}px;
}
QTabWidget::pane {
    border: 1px solid $border;
    border-radius: ${r_box}px;
    background: transparent;
    top: -1px;
}
QTabBar::tab {
    background-color: $surface_alt;
    color: $text_muted;
    border: 1px solid $border;
    border-bottom: none;
    border-top-left-radius: ${r_item}px;
    border-top-right-radius: ${r_item}px;
    padding: 7px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: $surface;
    color: $text;
    font-weight: 600;
}
QTabBar::tab:hover {
    color: $text;
}
QScrollArea {
    background: transparent;
    border: none;
}
QStatusBar {
    background: transparent;
    color: $text_muted;
}
""")


def build_stylesheet(name: str = "dark", glass: bool = False,
                     corners=1, console_square: bool = False) -> str:
    values = colors_for(name, glass)
    radii = radii_for(corners, console_square)
    for key, value in radii.items():
        values["r_" + key] = value
    return _QSS.substitute(values)


def apply_theme(app, name: str = "dark", glass: bool = False,
                corners=1, console_square: bool = False) -> dict:
    """Применить тему к QApplication: stylesheet + палитра."""
    name = normalize(name)
    base = BASE[name]
    colors = colors_for(name, glass)
    app.setStyleSheet(build_stylesheet(name, glass, corners, console_square))

    palette = app.palette()
    palette.setColor(QPalette.Window, QColor(base["window_solid"]))
    palette.setColor(QPalette.WindowText, QColor(base["text"]))
    palette.setColor(QPalette.Base, QColor(base["input_bg"]))
    palette.setColor(QPalette.AlternateBase, QColor(base["surface_alt"]))
    palette.setColor(QPalette.Text, QColor(base["text"]))
    palette.setColor(QPalette.Button, QColor(base["surface_alt"]))
    palette.setColor(QPalette.ButtonText, QColor(base["text"]))
    palette.setColor(QPalette.Highlight, QColor(base["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor(base["accent_text"]))
    palette.setColor(QPalette.ToolTipBase, QColor(base["surface_alt"]))
    palette.setColor(QPalette.ToolTipText, QColor(base["text"]))
    palette.setColor(QPalette.PlaceholderText, QColor(base["text_muted"]))
    app.setPalette(palette)
    # Радиус рамки окна: безрамочные диалоги берут его отсюда (title_bar).
    try:
        app.setProperty("frame_radius",
                        int(radii_for(corners, console_square)["panel"]))
    except (KeyError, TypeError, ValueError):
        pass
    return colors
