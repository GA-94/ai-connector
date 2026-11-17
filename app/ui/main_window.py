import os
import time

from app.qt_compat.QtCore import Qt, QTimer, QSize, QRect, QEvent
from app.qt_compat.QtGui import QFontMetrics
from app.qt_compat.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QVBoxLayout,
    QHBoxLayout, QPushButton, QAction, QMessageBox, QDialog,
    QListWidget, QListWidgetItem, QMenu, QInputDialog, QLabel,
    QFileDialog, QToolButton, QApplication, QSizeGrip, QLineEdit,
    QDialogButtonBox, QCheckBox, QTextEdit, QFormLayout,
    QFrame, QSizePolicy
)



from app import __version__, APP_NAME
from app import crash
from app import runtime_env
from app.agent import providers
from app.agent import pricing
from app.agent import ollama
from app.config.settings import AppSettings
from app.config.chat_store import ChatStore, clamp_title, TITLE_LIMIT
from app.config import db_import
from app.ui import theme
from app.ui.background import BackgroundWidget
from app.ui.chat_view import ChatView
from app.ui.console_view import ConsoleView
from app.ui.prompt_input import PromptInput
from app.ui.flow_layout import FlowLayout
from app.ui.settings_dialog import SettingsDialog
from app.ui.chat_settings_dialog import ChatSettingsDialog
from app.ui.repository_dialog import RepositoryDialog
from app.ui.gui_debug import GuiDebugDialog
from app.ui.providers_dialog import ProvidersDialog
from app.ui.tools_manager_dialog import ToolsManagerDialog
from app.ui.archive_dialog import ArchiveDialog
from app.ui.top_bar import TopBar
from app.ui.title_bar import (
    apply_window_mask, attach_title_bar, detach_title_bar, install_resize_edges
)
from app.ui.worker import AgentWorker
from app.ui.balance_widget import BalanceButton
from app.ui.token_widget import TokenCounterButton
from app.ui.ollama_control import ServerStartThread
from app.ui import threads
from app.tools import tooling
from app.tools.repo_status import check_repository


#: Служебные тексты интерфейса, которые НЕ являются ответом модели.
#: Их нельзя отправлять обратно в историю: провайдер ругается на пустой или
#: служебный content, а модель начинает «продолжать» текст ошибки вместо
#: задачи — следующий ход снова ломался.
TECH_REPLY_PREFIXES = ("[ERROR", "[UNKNOWN", "[Отменено", "[Cost]", "[Wait]")


class ChatListWidget(QListWidget):
    """Колонка чатов: текст переносится, высота строки подстраивается.

    Раньше название обрезалось («половину видно, половину нет»), потому что
    список не умел переносить строки при узкой панели.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setUniformItemSizes(False)
        self.setResizeMode(QListWidget.Adjust)
        self.setTextElideMode(Qt.ElideNone)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSpacing(1)
        # Пересчёт высот строк — дорогая операция: пока тянут ползунок, окно
        # дёргается. Поэтому считаем один раз, когда движение закончилось.
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(140)
        self._relayout_timer.timeout.connect(self.relayout)

    def schedule_relayout(self):
        """Отложить пересчёт: вызывается из resizeEvent и движения сплиттера."""
        self._relayout_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_relayout()


    def relayout(self):
        width = max(60, self.viewport().width() - 12)
        metrics = QFontMetrics(self.font())
        for index in range(self.count()):
            item = self.item(index)
            rect = metrics.boundingRect(
                QRect(0, 0, width, 10000), Qt.TextWordWrap, item.text())
            height = max(30, rect.height() + 12)
            if item.sizeHint().height() != height or item.sizeHint().width() != width:
                item.setSizeHint(QSize(width, height))

    def addItem(self, item):
        super().addItem(item)
        self.relayout()


class ActionBar(QFrame):
    """Панель кнопок под консолью: та же ширина, что у консоли.

    Кнопки лежат в собственной рамке и раскладываются по строкам целыми
    виджетами (FlowLayout). Высота панели всегда равна высоте содержимого,
    поэтому кнопки не могут вылезти за рамку ни при какой ширине.
    """

    MIN_HEIGHT = 56
    MAX_MIN_WIDTH = 340

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("actionBar")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.flow = FlowLayout(self, margin=8, spacing=6)
        self._widgets = []
        self._applying = False
        # Минимальная высота зависит от текста на кнопках (баланс, контекст
        # меняются на ходу), поэтому пересчёт склеиваем таймером: иначе он
        # запускался бы внутри resizeEvent и раскладка мигала.
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(30)
        self._sync_timer.timeout.connect(self._sync_limits)
        # Минимальная ширина — по самому широкому элементу: панель не должна
        # диктовать минимальный размер сплиттеру и «съедать» колонку чата.
        self.setMinimumWidth(180)
        self.setMinimumHeight(self.MIN_HEIGHT)

    def add_widgets(self, widgets):
        self._widgets = [widget for widget in widgets if widget is not None]
        for widget in self._widgets:
            self.flow.addWidget(widget)
            # Заголовок/текст кнопки поменялся -> hints изменились -> высота.
            widget.installEventFilter(self)
        self._sync_limits()

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if event.type() in (QEvent.LayoutRequest, QEvent.Resize, QEvent.Show):
            if not self._applying:
                self._sync_timer.start()
        return False

    def _rows_height(self, width: int) -> int:
        if not self._widgets:
            return self.MIN_HEIGHT
        return max(self.MIN_HEIGHT, self.flow.heightForWidth(max(1, int(width))))

    def _sync_limits(self):
        """Подогнать минимальную высоту под фактическое число строк кнопок."""
        if self._applying or not self._widgets:
            return
        widest = max((w.sizeHint().width() for w in self._widgets), default=180)
        minimum_width = min(self.MAX_MIN_WIDTH, max(180, widest + 26))
        needed = self._rows_height(max(1, self.contentsRect().width()))
        # Высота ровно по содержимому: панель не может оказаться короче своих
        # кнопок, поэтому текст больше не наезжает на кнопки.
        if (self.minimumWidth() == minimum_width
                and self.minimumHeight() == needed
                and self.maximumHeight() == needed):
            return
        self._applying = True
        try:
            self.setMinimumWidth(minimum_width)
            self.setFixedHeight(needed)
        finally:
            self._applying = False

    def resizeEvent(self, event):  # noqa: N802 -Qt API
        super().resizeEvent(event)
        # Ничего не скрываем и не пересоздаём: только правим минимумы, иначе
        # при каждом изменении размера кнопки мигали.
        self._sync_timer.start()


class GithubPublishDialog(QDialog):

    """Подтверждение публикации: имя проекта, автор, описание, «открыть после»."""

    def __init__(self, request: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Публикация в GitHub")
        self.setMinimumWidth(520)
        self._request = dict(request or {})

        layout = QVBoxLayout(self)
        warn = QLabel("ИИ-агент хочет выложить код проекта на GitHub.")
        warn.setWordWrap(True)
        warn.setStyleSheet("font-weight: bold;")
        layout.addWidget(warn)

        path_label = QLabel("Папка: " + str(self._request.get("path") or ""))
        path_label.setWordWrap(True)
        path_label.setObjectName("mutedLabel")
        layout.addWidget(path_label)

        form = QFormLayout()
        self.repo_edit = QLineEdit(str(self._request.get("repo_name") or ""))
        self.repo_edit.setPlaceholderText("my-cool-project — латиницей, без пробелов")
        form.addRow("Название проекта:", self.repo_edit)

        self.author_edit = QLineEdit(str(self._request.get("author") or ""))
        self.author_edit.setPlaceholderText("автор (подпись коммита)")
        form.addRow("Автор:", self.author_edit)

        self.description_edit = QTextEdit()
        self.description_edit.setPlainText(str(self._request.get("description") or ""))
        self.description_edit.setFixedHeight(70)
        form.addRow("Описание:", self.description_edit)
        layout.addLayout(form)

        self.private_cb = QCheckBox("Приватный репозиторий")
        self.private_cb.setChecked(bool(self._request.get("private")))
        layout.addWidget(self.private_cb)

        self.open_cb = QCheckBox("Открыть репозиторий в браузере после публикации")
        self.open_cb.setChecked(bool(self._request.get("open_after", True)))
        layout.addWidget(self.open_cb)

        buttons = QDialogButtonBox()
        push = buttons.addButton("Выложить", QDialogButtonBox.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.RejectRole)
        push.setProperty("role", "accent")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "Публикация в GitHub")

    def _on_accept(self):
        name = self.repo_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "GitHub", "Впиши название репозитория.")
            return
        if " " in name:
            QMessageBox.warning(self, "GitHub", "Название без пробелов (можно дефисы).")
            return
        self.accept()

    def answer(self) -> dict:
        return {
            "repo_name": self.repo_edit.text().strip(),
            "author": self.author_edit.text().strip(),
            "description": self.description_edit.toPlainText().strip(),
            "private": self.private_cb.isChecked(),
            "open_after": self.open_cb.isChecked(),
        }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Атрибуты нужны до первого resizeEvent/показа окна.
        self._title_bar = None
        self._size_grip = None
        self.frameless = False
        self.corner_mode = 1
        self.square_console = False
        self._corner_actions = {}
        self._cost_base = 0.0
        # Маску скругления пересчитывать на каждый пиксель ресайза дорого:
        # делаем это один раз, когда движение окна закончилось.
        self._mask_timer = QTimer(self)
        self._mask_timer.setSingleShot(True)
        self._mask_timer.setInterval(80)
        self._mask_timer.timeout.connect(lambda: self._update_window_mask())
        # Ширину колонки чатов сохраняем не на каждый пиксель, а когда
        # движение ползунка закончилось (Qt5 не отдаёт «отпускание» сплиттера).
        self._split_save_timer = QTimer(self)
        self._split_save_timer.setSingleShot(True)
        self._split_save_timer.setInterval(320)
        self._split_save_timer.timeout.connect(lambda: self._on_splitter_released())



        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1400, 820)

        self.settings = AppSettings()
        # База чатов всегда рядом с программой, а не там, откуда её запустили:
        # на Linux запуск из другого каталога иначе плодит пустые data/chats.db.
        self.data_dir = os.path.join(self.settings.project_dir, "data")
        self.chat_store = ChatStore(os.path.join(self.data_dir, "chats.db"))
        # Профили удалённых подключений (SSH/хранилища) лежат в этой же базе —
        # каждый чат видит только свои. Пароли там не хранятся (реестр/файл конфигов).
        self.remote_store = None
        self._remote_store_error = ""
        self._bind_remote_store()
        self.current_session = None
        self.worker = None
        self.history = []
        self._show_thinking = self.settings.get_show_thinking()
        # Быстрый режим (без рассуждений модели): значение по умолчанию для
        # новых чатов, у каждого чата своё (хранится в базе).
        self._fast_mode = self.settings.get_fast_mode()
        # Сторож интерфейса: «Отправить» не должна залипать заблокированной,
        # если поток агента завершился, а сигнал finished потерялся.
        self._run_active = False
        self._guard_strikes = 0

        self.theme_name = theme.normalize(self.settings.get_theme())
        self.background_path = self.settings.get_background_image()
        self.corner_mode = theme.normalize_corners(self.settings.get_corner_mode())
        self.square_console = self.settings.get_square_console()
        self.frameless = self.settings.get_frameless()

        self._build_ui()
        self._apply_appearance()
        self._apply_window_frame()
        self._load_sessions()
        self.console_view.log("[Repo] " + check_repository(
            self.settings.get_repository_root()).short())
        self.console_view.log(
            f"[OS] {runtime_env.distro_name()} | пути: '/' | python: "
            f"{runtime_env.PYTHON_NAME}")
        self.console_view.log(
            f"[AI] провайдер: {providers.label_for(self.settings.get_provider())} | "
            + tooling.describe_disabled(self.settings.get_disabled_tools()))
        # Баланс запрашиваем в фоне сразу после появления окна.
        QTimer.singleShot(800, lambda: self.balance_btn.refresh())
        # Ollama: если включено «поднимать при старте», поднимаем сервер в фоне
        # (и не трогаем его, если он уже работает). Интерфейс не ждёт.
        if self.settings.get_ollama_autostart():
            QTimer.singleShot(1500, self._ensure_ollama_server)
        # Отчёт о предыдущем сбое (если был) — сразу в консоль, чтобы не искать
        # папку crash_reports вручную.
        QTimer.singleShot(1200, self._report_previous_crash)

        if not self._select_first_session():
            QTimer.singleShot(100, self._start_without_sessions)

        # Сторож: раз в 1.5 с проверяем, что кнопка «Отправить» доступна.
        self._ui_guard = QTimer(self)
        self._ui_guard.setInterval(1500)
        self._ui_guard.timeout.connect(self._ensure_send_enabled)
        self._ui_guard.start()

    # ------------------------------------------------------------- оформление

    def _glass_active(self) -> bool:
        # Галочка «Показывать фон» выключает и картинку, и прозрачность панелей.
        if not self.settings.get_show_background():
            return False
        return bool(self.background_path) and os.path.exists(self.background_path)

    def _apply_appearance(self):
        app = QApplication.instance()
        glass = self._glass_active()
        theme.apply_theme(app, self.theme_name, glass=glass,
                          corners=self.corner_mode,
                          console_square=self.square_console)
        self.background.set_appearance(
            theme.colors_for(self.theme_name, glass),
            self.background_path if glass else "",
            self.settings.get_background_dim(),
        )
        self.chat_view.set_theme(self.theme_name, glass=glass)
        # Иконки кнопок окна нарисованы белым — перекрашиваем под тему.
        if self._title_bar is not None:
            self._title_bar.refresh_icons()
        self._update_appearance_controls()
        self._update_window_mask()

    def _update_window_mask(self):
        """Скруглить углы безрамочного окна под выбранный режим."""
        if not self.frameless:
            apply_window_mask(self, 0)
            return
        radius = theme.radii_for(self.corner_mode, self.square_console)["panel"]
        self.frame_radius = radius
        apply_window_mask(self, radius)

    def _apply_window_frame(self):
        """Своя полоса заголовка в цветах темы вместо белой системной рамки."""
        was_visible = self.isVisible()
        # Диалоги берут флаг «своя рамка» отсюда (title_bar.apply_dialog_frame).
        app = QApplication.instance()
        if app is not None:
            app.setProperty("frameless_windows", bool(self.frameless))
            # Диалоги читают этот же флаг: кнопки окна прячутся везде.
            app.setProperty("hide_window_buttons",
                            bool(self.settings.get_hide_window_buttons()))
        if self.frameless:
            if self._title_bar is None:
                # Название не дублируем: оно уже в верхней панели (TopBar),
                # в полосе окна остаются только кнопки и место для перетаскивания.
                self._title_bar = attach_title_bar(self, show_title=False)
                if self._title_bar is None:
                    self.frameless = False
                    self.console_view.log(
                        "[UI] безрамочное окно недоступно — остаётся системная рамка")
                    self._update_window_mask()
                    return
                self._size_grip = QSizeGrip(self)
                self._size_grip.setToolTip("Потяни, чтобы изменить размер окна")
                self._title_bar.refresh_icons()
                # Края и углы окна: тянуть размер мышкой.
                install_resize_edges(self)
            else:
                try:
                    self.setWindowFlag(Qt.FramelessWindowHint, True)
                except Exception:  # noqa: BLE001
                    pass
                self._title_bar.show()
                install_resize_edges(self)
            self._size_grip.show()
            self._size_grip.raise_()
        elif self._title_bar is not None:
            detach_title_bar(self, self._title_bar)
            if self._size_grip is not None:
                self._size_grip.hide()
        # Смена оконных флагов скрывает окно — возвращаем его на место.
        if was_visible and not self.isVisible():
            self.show()
        if self._title_bar is not None:
            self._title_bar.apply_button_visibility()
        self._update_window_mask()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Маска скругления — один раз после окончания ресайза (без лагов).
        self._mask_timer.start()


        if self._size_grip is not None:
            self._size_grip.move(self.width() - self._size_grip.width() - 2,
                                 self.height() - self._size_grip.height() - 2)
            self._size_grip.raise_()

    def _update_appearance_controls(self):
        for key, action in self._theme_actions.items():
            action.setChecked(key == self.theme_name)
        for key, action in self._corner_actions.items():
            action.setChecked(key == self.corner_mode)
        # Галочки «Показывать фон» и «Скрывать кнопки окна» в меню «Вид».
        show_bg = getattr(self, "_show_bg_action", None)
        if show_bg is not None:
            show_bg.setChecked(self.settings.get_show_background())
        hide_buttons = getattr(self, "_hide_buttons_action", None)
        if hide_buttons is not None:
            hide_buttons.setChecked(self.settings.get_hide_window_buttons())
        transparent_bar = getattr(self, "_transparent_bar_action", None)
        if transparent_bar is not None:
            transparent_bar.setChecked(self.settings.get_transparent_top_bar())
        if getattr(self, "log_btn", None) is not None:
            self._sync_log_button()

    @staticmethod
    def _repolish(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_theme(self, name: str):
        self.theme_name = theme.normalize(name)
        self.settings.set_theme(self.theme_name)
        self._apply_appearance()
        self.console_view.log(f"[UI] тема: {theme.label(self.theme_name)}")

    def _cycle_theme(self):
        order = list(theme.THEME_ORDER)
        try:
            index = (order.index(self.theme_name) + 1) % len(order)
        except ValueError:
            index = 0
        self._set_theme(order[index])

    def _set_corner_mode(self, mode):
        """Режим скругления углов: 1 всё, 2 кнопки, 3 панели, 4 максимум."""
        self.corner_mode = theme.normalize_corners(mode)
        self.settings.set_corner_mode(self.corner_mode)
        self._apply_appearance()
        self.console_view.log(
            "[UI] скругление: " + theme.corner_label(self.corner_mode))

    def _choose_background(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Фоновое изображение", "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.webp *.gif)")
        if not path:
            return
        self.background_path = runtime_env.normalize_path(path)
        self.settings.set_background_image(self.background_path)
        self._apply_appearance()
        self.console_view.log(f"[UI] фон: {self.background_path}")

    def _clear_background(self):
        self.background_path = ""
        self.settings.set_background_image("")
        self._apply_appearance()
        self.console_view.log("[UI] фон убран")

    def _choose_dim(self):
        value, ok = QInputDialog.getInt(
            self, "Фоновое изображение", "Затемнение фона, %:",
            self.settings.get_background_dim(), 0, 90, 5)
        if ok:
            self.settings.set_background_dim(value)
            self._apply_appearance()

    # ----------------------------------------------------------------- меню

    def _build_menu(self):
        # Меню больше не лежит в системной полосе QMenuBar: наверху название
        # программы с версией, а пункты — кнопками в рамках (TopBar).
        file_menu = QMenu("Файл", self)
        new_chat_action = QAction("Новый чат", self)
        new_chat_action.triggered.connect(self._create_new_chat)
        file_menu.addAction(new_chat_action)
        open_folder_action = QAction("Открыть папку проекта", self)
        open_folder_action.triggered.connect(self._open_project_folder)
        file_menu.addAction(open_folder_action)
        open_repo_action = QAction("Открыть папку репозитория", self)
        open_repo_action.triggered.connect(self._open_repository_folder)
        file_menu.addAction(open_repo_action)
        file_menu.addSeparator()
        import_db_action = QAction("Импорт базы данных чатов…", self)
        import_db_action.setToolTip(
            "Заменить data/chats.db выбранной базой; старая копия останется в data/backups/")
        import_db_action.triggered.connect(self._import_database)
        file_menu.addAction(import_db_action)
        file_menu.addSeparator()
        crash_action = QAction("Папка краш-репортов…", self)
        crash_action.setToolTip(
            "Отчёты о падениях: crash_reports/<дата>/report.txt")
        crash_action.triggered.connect(self._open_crash_reports)
        file_menu.addAction(crash_action)
        file_menu.addSeparator()
        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # --- Вид: сюда переехали кнопки «Репозиторий», «Тема», «Картинка» ---
        view_menu = QMenu("Вид", self)
        themes_menu = view_menu.addMenu("Тема оформления")
        self._theme_actions = {}
        for key in theme.THEME_ORDER:
            action = QAction(theme.label(key), self)
            action.setCheckable(True)
            action.setChecked(key == self.theme_name)
            action.triggered.connect(
                lambda _checked=False, name=key: self._set_theme(name))
            themes_menu.addAction(action)
            self._theme_actions[key] = action

        bg_action = QAction("Фоновое изображение…", self)
        bg_action.triggered.connect(self._choose_background)
        view_menu.addAction(bg_action)
        clear_bg_action = QAction("Убрать фон", self)
        clear_bg_action.triggered.connect(self._clear_background)
        view_menu.addAction(clear_bg_action)
        dim_action = QAction("Затемнение фона…", self)
        dim_action.triggered.connect(self._choose_dim)
        view_menu.addAction(dim_action)

        # Фон можно выключить целиком: остаётся чистая тема без обоев.
        self._show_bg_action = QAction("Показывать фон (картинка и обои)", self)
        self._show_bg_action.setCheckable(True)
        self._show_bg_action.setChecked(self.settings.get_show_background())
        self._show_bg_action.setToolTip(
            "Снятая галочка — окно рисуется только темой, без фоновой картинки")
        self._show_bg_action.triggered.connect(self._set_show_background)
        view_menu.addAction(self._show_bg_action)

        # Верхняя панель как часть окна: без плашки и рамки, сквозь неё видно
        # фоновое изображение (название и меню остаются сверху).
        self._transparent_bar_action = QAction(
            "Прозрачная верхняя панель (видно фон)", self)
        self._transparent_bar_action.setCheckable(True)
        self._transparent_bar_action.setChecked(
            self.settings.get_transparent_top_bar())
        self._transparent_bar_action.setToolTip(
            "Панель с названием программы и меню рисуется без фона и рамки —\n"
            "сквозь неё видно фоновую картинку, и она выглядит частью окна")
        self._transparent_bar_action.triggered.connect(
            self._set_transparent_top_bar)
        view_menu.addAction(self._transparent_bar_action)

        repo_panel_action = QAction("Репозиторий (файлы и архивы)…", self)
        repo_panel_action.triggered.connect(self._open_repository_dialog)
        view_menu.addAction(repo_panel_action)

        view_menu.addSeparator()
        corners_menu = view_menu.addMenu("Скругление углов")
        self._corner_actions = {}
        for mode in theme.CORNER_ORDER:
            corner_action = QAction(theme.CORNER_LABELS[mode], self)
            corner_action.setCheckable(True)
            corner_action.setChecked(mode == self.corner_mode)
            corner_action.triggered.connect(
                lambda _checked=False, value=mode: self._set_corner_mode(value))
            corners_menu.addAction(corner_action)
            self._corner_actions[mode] = corner_action

        # Кнопки свернуть/развернуть/закрыть: тайловым WM на Linux они мешают,
        # поэтому их можно спрятать (действия остаются в правом клике).
        self._hide_buttons_action = QAction(
            "Скрывать кнопки окна (свернуть, развернуть, закрыть)", self)
        self._hide_buttons_action.setCheckable(True)
        self._hide_buttons_action.setChecked(self.settings.get_hide_window_buttons())
        self._hide_buttons_action.setToolTip(
            "Для тайловых оконных менеджеров Linux: кнопки убираются, а "
            "свернуть/развернуть/закрыть остаются в правом клике по заголовку")
        self._hide_buttons_action.triggered.connect(self._set_hide_window_buttons)
        view_menu.addAction(self._hide_buttons_action)

        view_menu.addSeparator()
        archive_action = QAction("Архив чатов…", self)
        archive_action.triggered.connect(self._open_archive)
        view_menu.addAction(archive_action)
        debug_action = QAction("Отладчик GUI…", self)
        debug_action.setShortcut("F12")
        debug_action.setToolTip("Дерево виджетов, геометрия, подсветка контуров")
        debug_action.triggered.connect(self._open_gui_debug)
        view_menu.addAction(debug_action)

        # --- Глобальные настройки ---
        settings_menu = QMenu("Глобальные настройки", self)
        general_action = QAction("Общие настройки…", self)
        general_action.triggered.connect(self._open_settings)
        settings_menu.addAction(general_action)
        providers_action = QAction("Провайдеры и модели (включая Ollama)…", self)
        providers_action.triggered.connect(self._open_providers)
        settings_menu.addAction(providers_action)
        key_location_action = QAction("Где хранится API-ключ…", self)
        key_location_action.setToolTip(
            "Показать путь к secrets.ini и открыть папку с ним")
        key_location_action.triggered.connect(self._show_key_location)
        settings_menu.addAction(key_location_action)

        tools_action = QAction("Инструменты ИИ-агента…", self)
        tools_action.triggered.connect(self._open_tools_manager)
        settings_menu.addAction(tools_action)
        economy_action = QAction("Экономный режим", self)
        economy_action.setCheckable(True)
        economy_action.setChecked(self.settings.get_economy_mode())
        economy_action.triggered.connect(self._set_economy)
        settings_menu.addAction(economy_action)
        self._economy_action = economy_action

        # Меню отдаём верхней панели: кнопки «Файл / Вид / Глобальные настройки».
        self.menu_file = file_menu
        self.menu_view = view_menu
        self.menu_settings = settings_menu

    # --------------------------------------------------------------------- UI

    def _build_ui(self):
        self._build_menu()

        # Верхняя панель: сверху название программы с версией, ниже — кнопки
        # меню в рамках. Фон панели прозрачный, поэтому видно обои.
        self.top_bar = TopBar(APP_NAME, __version__)
        self.top_bar.add_menu_button("Файл", self.menu_file)
        self.top_bar.add_menu_button("Вид", self.menu_view)
        self.top_bar.add_action_button(
            "Инструменты", self._open_tools_manager,
            "Инструменты ИИ-агента: включить, отключить, добавить, изменить, удалить")
        self.top_bar.add_action_button(
            "🖥 Терминал", self._open_remote_terminal,
            "Удалённый терминал (SSH) этого чата: вводите команды и видите вывод")
        self.top_bar.add_menu_button("Глобальные настройки", self.menu_settings)
        self.top_bar.add_stretch()
        self.top_bar.set_hint(runtime_env.distro_name())
        # Панель — часть окна: по умолчанию без плашки и рамки, сквозь неё
        # видно фоновое изображение (переключается в меню «Вид»).
        self.top_bar.set_transparent(self.settings.get_transparent_top_bar())

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        # --- левая панель со списком чатов (ширину можно двигать)
        self.side_panel = QWidget()
        self.side_panel.setObjectName("sidePanel")
        self.side_panel.setMinimumWidth(170)
        self.side_panel.setMaximumWidth(620)
        side_layout = QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(10)

        chats_label = QLabel("ЧАТЫ")
        chats_label.setObjectName("mutedLabel")
        side_layout.addWidget(chats_label)

        self.new_chat_btn = QPushButton("+  Новый чат")
        self.new_chat_btn.setProperty("role", "accent")
        self.new_chat_btn.setMinimumHeight(38)
        self.new_chat_btn.clicked.connect(self._create_new_chat)
        side_layout.addWidget(self.new_chat_btn)

        self.sessions_list = ChatListWidget()
        self.sessions_list.itemClicked.connect(self._on_session_selected)
        # Выбор чата стрелками и программно (setCurrentRow/setCurrentItem) тоже
        # обязан открывать чат. Раньше обработчик висел только на клике мышью:
        # после удаления, архивации, импорта базы или перезапуска
        # current_session оставался None, и «Отправить» молча ничего не делал
        # (тот самый баг «не могу отправить следующее сообщение»).
        self.sessions_list.currentItemChanged.connect(
            lambda current, _previous: self._on_session_selected(current))
        self.sessions_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sessions_list.customContextMenuRequested.connect(self._show_context_menu)
        side_layout.addWidget(self.sessions_list)

        self.archive_btn = QPushButton("🗄 Архив чатов")
        self.archive_btn.setToolTip("Чаты, убранные из списка, но не удалённые")
        self.archive_btn.clicked.connect(self._open_archive)
        side_layout.addWidget(self.archive_btn)

        # --- чат + консоль: две колонки, ширины выровнены ------------------
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.center_splitter = QSplitter(Qt.Horizontal)
        self.center_splitter.setChildrenCollapsible(False)
        # Пока тянут — только «резинка», перестройка после отпускания.
        self.center_splitter.setOpaqueResize(False)

        # Колонка чата: поле запроса лежит прямо под чатом, поэтому ширина
        # поля и чата всегда одинаковая.
        self.chat_column = QWidget()
        chat_column_layout = QVBoxLayout(self.chat_column)
        chat_column_layout.setContentsMargins(0, 0, 0, 0)
        chat_column_layout.setSpacing(8)
        self.chat_view = ChatView(theme_name=self.theme_name)
        chat_column_layout.addWidget(self.chat_view, 1)

        # Адаптивное поле: растёт по высоте до 8 строк (Enter — отправить,
        # Shift+Enter — новая строка) и не отъедает место у чата целиком.
        self.input_edit = PromptInput(min_height=46, max_lines=8)
        self.input_edit.submitted.connect(self._send)
        chat_column_layout.addWidget(self.input_edit, 0)

        # Колонка консоли: под ней отдельная рамка с кнопками, её ширина всегда
        # равна ширине консоли, а высота — ровно по числу строк кнопок.
        self.console_column = QWidget()
        console_layout = QVBoxLayout(self.console_column)
        console_layout.setContentsMargins(0, 0, 0, 0)
        console_layout.setSpacing(8)
        self.console_view = ConsoleView()
        self.action_bar = ActionBar()
        console_layout.addWidget(self.console_view, 1)
        console_layout.addWidget(self.action_bar, 0)

        self.center_splitter.addWidget(self.chat_column)
        self.center_splitter.addWidget(self.console_column)
        self.center_splitter.setStretchFactor(0, 1)
        self.center_splitter.setStretchFactor(1, 0)
        self.center_splitter.setSizes([760, 400])
        right_layout.addWidget(self.center_splitter, 1)


        self.open_folder_btn = QPushButton("📁 Папка")
        self.open_folder_btn.setMinimumHeight(40)
        self.open_folder_btn.setToolTip("Открыть директорию текущего проекта")
        self.open_folder_btn.clicked.connect(self._open_project_folder)

        # Остаток по текущему API-ключу: обновляется по клику и после каждого
        # ответа агента (внутри значения кэшируется на минуту).
        self.balance_btn = BalanceButton(self.settings)
        self.balance_btn.setMinimumHeight(40)

        # Счётчик токенов контекста: показывается ВМЕСТО баланса, когда модель
        # локальная (в Ollama платить не за что, зато контекст кончается).
        self.token_btn = TokenCounterButton()
        self.token_btn.setMinimumHeight(40)
        self.token_btn.hide()

        # Быстрый режим: модель не рассуждает вслух, отвечает сразу.
        self.fast_btn = QPushButton("⚡ Быстро")
        self.fast_btn.setCheckable(True)
        self.fast_btn.setMinimumHeight(40)
        self.fast_btn.setToolTip(
            "Быстрый режим: отключить рассуждения модели.\n"
            "У локальной Ollama — think=false (ответ приходит сразу),\n"
            "у облачных — пониженное усилие рассуждений.\n"
            "Настройка запоминается для текущего чата.")
        self.fast_btn.toggled.connect(self._set_fast_mode)
        self._sync_fast_button()

        # Стоимость чата в рублях и счётчик контекста в нижней панели убраны:
        # считались приблизительно и только мешали. Расход по-прежнему
        # пишется в базу (виден в подсказке чата и в архиве), а занятый
        # контекст выводится строкой «[Context] …» в консоль действий.

        # Экономный режим: меньше итераций, без лишних сборок и тестов.
        self.economy_btn = QPushButton("🐢 Эконом")
        self.economy_btn.setCheckable(True)
        self.economy_btn.setMinimumHeight(40)
        self.economy_btn.setToolTip(
            "Экономный режим: агент не гоняет лишние тесты и сборки, "
            "правит файлы пачками — меньше расход по API")
        self.economy_btn.setChecked(self.settings.get_economy_mode())
        self.economy_btn.toggled.connect(self._set_economy)
        self._sync_economy_button()

        # Журнал инструментов: «Кратко» (путь/команда) или полный дамп аргументов.
        self.log_btn = QPushButton("📜 Кратко")
        self.log_btn.setCheckable(True)
        self.log_btn.setMinimumHeight(40)
        self.log_btn.setToolTip(
            "Вид журнала инструментов в консоли:\n"
            "«Кратко» — что именно делает инструмент (файл, команда, размер кода);\n"
            "«Полный» — все аргументы вызова (JSON), как раньше")
        self.log_btn.toggled.connect(self._set_verbose_tools)
        self._sync_log_button()

        # Инструменты агента: быстрый доступ к менеджеру прямо из панели.
        # Здесь же выключаются лишние группы — иначе в Python-проекте висят
        # java_* инструменты, а это токены в каждом запросе (то есть деньги).
        self.tools_btn = QPushButton("🧰 Инструменты")
        self.tools_btn.setMinimumHeight(40)
        self.tools_btn.setToolTip(
            "Что умеет ИИ-агент в этом чате: включить или выключить группы\n"
            "инструментов (Java, Python, Git, Репозиторий, Баланс).\n"
            "Выключенные инструменты не уходят модели — экономятся токены.")
        self.tools_btn.clicked.connect(self._open_tools_manager)

        # Мысли агента: показывать «что он думает» в чате или только в консоли.
        self.thinking_btn = QPushButton("💭 Мысли")
        self.thinking_btn.setCheckable(True)
        self.thinking_btn.setMinimumHeight(40)
        self.thinking_btn.setToolTip(
            "Показывать в чате, что агент думает и планирует перед каждым шагом.\n"
            "Выключено — мысли идут только в консоль. Настройка запоминается "
            "для текущего чата.")
        self.thinking_btn.toggled.connect(self._set_show_thinking)
        self._sync_thinking_button()

        self.send_btn = QPushButton("Отправить")
        self.send_btn.setProperty("role", "accent")
        self.send_btn.setMinimumHeight(40)
        self.send_btn.setMinimumWidth(130)
        self.send_btn.clicked.connect(self._send)

        # Отдельная кнопка «Стоп»: видна только во время работы агента.
        self.stop_btn = QPushButton("■ Стоп")
        self.stop_btn.setProperty("role", "danger")
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setMinimumWidth(96)
        self.stop_btn.setToolTip("Остановить агента (отмена после текущего шага)")
        self.stop_btn.clicked.connect(self._stop_agent)
        # Кнопка «Стоп» всегда на месте (просто неактивна вне работы), чтобы
        # она не «пропадала» из интерфейса.
        self.stop_btn.setEnabled(False)

        # Кнопки — в собственной рамке под консолью: ширина рамки равна ширине
        # консоли, а высоту панели можно тянуть сплиттером.
        self.action_bar.add_widgets([
            self.balance_btn,
            self.token_btn,
            self.open_folder_btn, self.economy_btn, self.fast_btn, self.log_btn,
            self.tools_btn, self.thinking_btn,
            self.stop_btn, self.send_btn,
        ])



        # Колонки чатов и рабочей области — в сплиттере, ширину можно менять.
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        # Резинка вместо живой перестройки: интерфейс не тормозит при движении.
        self.main_splitter.setOpaqueResize(False)
        self.main_splitter.addWidget(self.side_panel)
        self.main_splitter.addWidget(right_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        width = self.settings.get_sidebar_width()
        self.main_splitter.setSizes([width, max(600, self.width() - width)])
        self.main_splitter.splitterMoved.connect(self._on_splitter_moved)


        main_layout.addWidget(self.main_splitter)

        self.background = BackgroundWidget()
        column = QVBoxLayout()
        column.setContentsMargins(14, 10, 14, 14)
        column.setSpacing(8)
        column.addWidget(self.top_bar, 0)
        column.addLayout(main_layout, 1)
        self.background.setLayout(column)
        self.setCentralWidget(self.background)

    def _on_splitter_moved(self, _pos, _index):
        """Пока тянут ползунок — только откладываем пересчёт (без лагов)."""
        self.sessions_list.schedule_relayout()
        self._split_save_timer.start()

    def _on_splitter_released(self, _pos=0, _index=0):
        """Ползунок отпустили: сохраняем ширину и пересчитываем строки чатов."""
        self.settings.set_sidebar_width(self.side_panel.width())
        self.sessions_list.relayout()



    # --------------------------------------------------------------- экономия

    def _sync_economy_button(self):
        on = self.settings.get_economy_mode()
        self.economy_btn.setProperty("role", "danger" if on else "")
        self._repolish(self.economy_btn)
        if hasattr(self, "_economy_action"):
            self._economy_action.setChecked(on)

    def _set_economy(self, value):
        enabled = bool(value)
        self.settings.set_economy_mode(enabled)
        if self.economy_btn.isChecked() != enabled:
            self.economy_btn.blockSignals(True)
            self.economy_btn.setChecked(enabled)
            self.economy_btn.blockSignals(False)
        self._sync_economy_button()
        self.console_view.log(
            "[Money] экономный режим " + ("включён" if enabled else "выключен"))

    # -------------------------------------------- журнал инструментов (кратко)

    def _sync_log_button(self):
        verbose = self.settings.get_verbose_tool_log()
        if self.log_btn.isChecked() != verbose:
            self.log_btn.blockSignals(True)
            self.log_btn.setChecked(verbose)
            self.log_btn.blockSignals(False)
        self.log_btn.setText("📜 Полный" if verbose else "📜 Кратко")

    def _set_verbose_tools(self, value):
        """Кнопка в правом нижнем углу: краткий или полный журнал вызовов."""
        verbose = bool(value)
        self.settings.set_verbose_tool_log(verbose)
        self._sync_log_button()
        self.console_view.log("[Tools] журнал: " + (
            "полные аргументы вызовов" if verbose else "краткие подписи"))

    # ---------------------------------------------- мысли агента (тумблер внизу)

    def _sync_thinking_button(self):
        """Кнопка «💭 Мысли» отражает настройку текущего чата."""
        button = getattr(self, "thinking_btn", None)
        if button is None:
            return
        on = bool(getattr(self, "_show_thinking", True))
        if button.isChecked() != on:
            button.blockSignals(True)
            button.setChecked(on)
            button.blockSignals(False)
        button.setText("💭 Мысли" if on else "💭 Скрыто")
        button.setProperty("role", "accent" if on else "")
        self._repolish(button)

    def _set_show_thinking(self, value):
        """Тумблер: показывать в чате, что ИИ думает, или не показывать."""
        enabled = bool(value)
        self._show_thinking = enabled
        self.settings.set_show_thinking(enabled)
        if self.current_session:
            self.chat_store.set_show_thinking(self.current_session, enabled)
        self._sync_thinking_button()
        self.console_view.log(
            "[Chat] мысли агента: " + ("показывать в чате" if enabled
                                      else "скрыть (только консоль)"))

    # ------------------------------------------- быстрый режим (без рассуждений)

    def _sync_fast_button(self):
        """Кнопка «⚡ Быстро» отражает настройку текущего чата."""
        button = getattr(self, "fast_btn", None)
        if button is None:
            return
        on = bool(getattr(self, "_fast_mode", False))
        if button.isChecked() != on:
            button.blockSignals(True)
            button.setChecked(on)
            button.blockSignals(False)
        button.setText("⚡ Быстро" if on else "⚡ Обычно")
        button.setProperty("role", "accent" if on else "")
        self._repolish(button)

    def _set_fast_mode(self, value):
        """Тумблер: отключить рассуждения модели ради скорости."""
        enabled = bool(value)
        self._fast_mode = enabled
        self.settings.set_fast_mode(enabled)
        if self.current_session:
            self.chat_store.set_fast_mode(self.current_session, enabled)
        self._sync_fast_button()
        local = bool(providers.get(self._current_provider_key()).local)
        if enabled:
            note = ("think=false у Ollama" if local
                    else "пониженное усилие рассуждений у провайдера")
            self.console_view.log(f"[Fast] быстрый режим включён ({note})")
        else:
            self.console_view.log("[Fast] быстрый режим выключен: модель думает как обычно")

    # ------------------------------------------------ фон и кнопки окна (Вид)

    def _set_show_background(self, value=None):
        """Галочка «Показывать фон»: картинка обоев и прозрачность панелей."""
        enabled = self.settings.get_show_background() if value is None else bool(value)
        self.settings.set_show_background(enabled)
        self._apply_appearance()
        self.console_view.log("[UI] фон " + ("включён" if enabled else "выключен"))

    def _set_transparent_top_bar(self, value=None):
        """Панель с названием и меню: прозрачная (видно фон) или плашка."""
        enabled = (self.settings.get_transparent_top_bar()
                   if value is None else bool(value))
        self.settings.set_transparent_top_bar(enabled)
        self.top_bar.set_transparent(enabled)
        self.console_view.log("[UI] верхняя панель: " + (
            "прозрачная, видно фон" if enabled else "плотная плашка темы"))

    def _set_hide_window_buttons(self, value=None):
        """Тумблер для тайловых WM: убрать кнопки свернуть/развернуть/закрыть."""
        hidden = (self.settings.get_hide_window_buttons()
                  if value is None else bool(value))
        self.settings.set_hide_window_buttons(hidden)
        app = QApplication.instance()
        if app is not None:
            app.setProperty("hide_window_buttons", hidden)
        if self._title_bar is not None:
            self._title_bar.apply_button_visibility()
        self.console_view.log("[UI] кнопки окна " + (
            "скрыты (правый клик по заголовку)" if hidden else "показаны"))

    def _open_crash_reports(self):
        """Файл -> Папка краш-репортов: где лежат отчёты о падениях."""
        root = crash.reports_root()
        self.console_view.log(f"[Crash] папка отчётов: {root}")
        try:
            runtime_env.open_in_file_manager(str(root))
        except Exception:  # noqa: BLE001
            pass

    def _report_previous_crash(self):
        """Сказать в консоли, что в папке есть свежий отчёт о падении."""
        try:
            reports = crash.last_reports(3)
        except Exception:  # noqa: BLE001
            return
        fresh = []
        for path in reports:
            try:
                age = os.path.getmtime(os.path.join(path, "report.txt"))
                if (time.time() - age) < 3 * 24 * 3600:
                    fresh.append(path)
            except Exception:  # noqa: BLE001
                continue
        if not fresh:
            return
        self.console_view.log(
            f"[Crash] найден отчёт о предыдущем сбое: {fresh[0]}")
        if len(fresh) > 1:
            self.console_view.log(f"[Crash] всего свежих отчётов: {len(fresh)}")

    # ------------------------------------------------------------ контекст чата

    def _show_context_menu(self, pos):
        item = self.sessions_list.itemAt(pos)
        if not item:
            return
        session_id = item.data(Qt.UserRole)
        menu = QMenu()
        settings_action = menu.addAction("Настройки чата")
        rename_action = menu.addAction("Переименовать")
        archive_action = menu.addAction("В архив")
        delete_action = menu.addAction("Удалить")
        action = menu.exec_(self.sessions_list.mapToGlobal(pos))
        if action == settings_action:
            self._edit_chat_settings(session_id)
        elif action == rename_action:
            self._rename_session(session_id, item)
        elif action == archive_action:
            self.chat_store.set_archived(session_id, True)
            if self.current_session == session_id:
                self.current_session = None
                self.chat_view.clear_chat()
                self.history = []
            self._load_sessions()
            if not self._select_first_session():
                self._create_new_chat()
            self.console_view.log("[Chats] чат убран в архив")
        elif action == delete_action:
            self._delete_session(session_id)

    def _edit_chat_settings(self, session_id):
        session = self.chat_store.get_session(session_id)
        if not session:
            return
        dlg = ChatSettingsDialog(
            settings=self.settings,
            parent=self,
            title=session["title"],
            project_root=session["project_root"],
            instructions=session["instructions"],
            master_model=session.get("master_model", ""),
            worker_model=session.get("worker_model", ""),
            use_two=session.get("use_two", False),
            max_iterations=session.get("max_iterations", 15),
            project_roots=session.get("project_roots") or [],
            show_thinking=session.get("show_thinking", True),
            provider=session.get("provider") or self.settings.get_provider(),
            balance_url=session.get("balance_url") or "",
            num_ctx=session.get("num_ctx"),
            ollama_mode=session.get("ollama_mode") or self.settings.get_ollama_mode(),
            ai_tools=session.get("ai_tools", False),
            fast_mode=session.get("fast_mode", self.settings.get_fast_mode()),
        )
        if dlg.exec_():
            data = dlg.get_data()
            self._persist_chat_api(data)
            self.chat_store.update_session_settings(
                session_id, data["project_root"], data["instructions"],
                data["master_model"], data["worker_model"], data["use_two"],
                data["max_iterations"], project_roots=data["project_roots"],
                show_thinking=data["show_thinking"],
                provider=data["provider"], balance_url=data["balance_url"],
                num_ctx=data["num_ctx"],
                ollama_mode=data.get("ollama_mode", ""),
                ai_tools=bool(data.get("ai_tools", False)),
                fast_mode=bool(data.get("fast_mode", False)),
            )
            self.settings.set_fast_mode(bool(data.get("fast_mode")))

            self.chat_store.update_session_title(session_id, data["title"])
            self._load_sessions()
            if self.current_session == session_id:
                self.current_session = None
                self._on_session_selected(self.sessions_list.currentItem())

    def _persist_chat_api(self, data: dict):
        """Ключ/адрес провайдера, ссылка на баланс и логин GitHub."""
        provider = providers.normalize(data.get("provider") or self.settings.get_provider())
        self.settings.set_provider(provider)
        if data.get("base_url"):
            self.settings.set_base_url(data["base_url"])
        key = data.get("api_key") or ""
        if key:
            self.settings.set_api_key_for(provider, key)
        if data.get("balance_url") is not None:
            self.settings.set_balance_url_for(provider, data.get("balance_url") or "")
        if data.get("github_token"):
            self.settings.set_github_token(data["github_token"])
        self.settings.set_github_author(data.get("github_author") or "")
        self.settings.set_github_open_after(bool(data.get("github_open_after", True)))
        # Локальная модель: размещение (VRAM/RAM), сервер и длина контекста.
        if data.get("ollama_host"):
            self.settings.set_ollama_host(data["ollama_host"])
        if data.get("ollama_mode"):
            self.settings.set_ollama_mode(data["ollama_mode"])
        if data.get("num_ctx") is not None:
            self.settings.set_ollama_num_ctx(data["num_ctx"])

    def _rename_session(self, session_id: int, item: QListWidgetItem):
        current_title = item.text()
        new_title, ok = QInputDialog.getText(
            self, "Переименовать чат",
            f"Новое название (до {TITLE_LIMIT} символов):", text=current_title)
        if ok and new_title.strip():
            title = clamp_title(new_title)
            self.chat_store.update_session_title(session_id, title)
            item.setText(title)
            item.setToolTip(title)
            self.sessions_list.relayout()

    def _delete_session(self, session_id: int):
        reply = QMessageBox.question(self, "Удаление",
                                     "Вы уверены, что хотите удалить этот чат?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.chat_store.delete_session(session_id)
            # Удалённый доступ чата убираем вместе с ним: закрываем SSH-сессии и
            # стираем профили подключений (пароли в реестре/файле остаются, но
            # попадают в «осиротевшие» — их можно снести в настройках).
            try:
                from app.tools import remote_terminal

                remote_terminal.close_chat(session_id)
                if self.remote_store is not None:
                    self.remote_store.close_chat(session_id)
            except Exception as error:  # noqa: BLE001
                self.console_view.log(f"[Remote] не удалось очистить подключения: {error}")
            if self.current_session == session_id:
                self.current_session = None
                self.chat_view.clear_chat()
                self.history = []
            self._load_sessions()
            if self.sessions_list.count() == 0:
                self._create_new_chat()
            elif self.current_session is None:
                self._select_first_session()

    def _select_first_session(self) -> bool:
        """Выбрать первый чат списка и реально его открыть.

        setCurrentRow() сам по себе чат не открывает (сигнал currentItemChanged
        приходит, но полагаться только на него мало: после clear()+addItem
        текущая строка может остаться невалидной). Поэтому открываем чат явно.
        Возвращает True, если активный чат в итоге есть.
        """
        if self.sessions_list.count() == 0:
            return False
        self.sessions_list.setCurrentRow(0)
        item = self.sessions_list.item(0)
        if item is not None:
            self._on_session_selected(item)
        return bool(self.current_session)

    def _load_sessions(self):
        self.sessions_list.clear()
        for session in self.chat_store.list_sessions():
            title = clamp_title(session["title"])
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, session["id"])
            item.setToolTip(f"{title}\nОбновлён: {session.get('updated_at') or ''}\n"
                            f"Потрачено: {pricing.format_rub(session.get('cost_rub') or 0)}")
            self.sessions_list.addItem(item)
            if session["id"] == self.current_session:
                item.setSelected(True)
        self.sessions_list.relayout()

    def _create_new_chat(self):
        dlg = ChatSettingsDialog(
            settings=self.settings, parent=self, title="Новый чат",
            project_root=self.settings.get_project_root(),
            master_model=self.settings.get_master_model(),
            worker_model=self.settings.get_worker_model(),
            use_two=self.settings.get_use_two_models(),
            show_thinking=self.settings.get_show_thinking(),
            provider=self.settings.get_provider(),
            balance_url=self.settings.get_balance_url_for(self.settings.get_provider()),
            num_ctx=self.settings.get_ollama_num_ctx(),
            ollama_mode=self.settings.get_ollama_mode(),
            ai_tools=self.settings.get_ai_can_edit_tools(),
            fast_mode=self.settings.get_fast_mode(),
        )
        if dlg.exec_():
            data = dlg.get_data()
            self._persist_chat_api(data)
            self.settings.set_show_thinking(data["show_thinking"])
            self._show_thinking = bool(data["show_thinking"])
            self._sync_thinking_button()
            self.settings.set_fast_mode(bool(data.get("fast_mode")))
            self._fast_mode = bool(data.get("fast_mode"))
            self._sync_fast_button()
            session_id = self.chat_store.create_session(
                title=data["title"], project_root=data["project_root"],
                instructions=data["instructions"], master_model=data["master_model"],
                worker_model=data["worker_model"], use_two=data["use_two"],
                max_iterations=data["max_iterations"],
                project_roots=data["project_roots"],
                show_thinking=data["show_thinking"],
                provider=data["provider"], balance_url=data["balance_url"],
                num_ctx=data["num_ctx"],
                ollama_mode=data.get("ollama_mode", ""),
                ai_tools=bool(data.get("ai_tools", False)),
                fast_mode=bool(data.get("fast_mode", False)),
            )

            self.current_session = session_id
            self.history = []
            self.chat_view.clear_chat()
            self._load_sessions()
            for i in range(self.sessions_list.count()):
                item = self.sessions_list.item(i)
                if item.data(Qt.UserRole) == self.current_session:
                    self.sessions_list.setCurrentItem(item)
                    break
            self._update_cost_label()
            self._apply_provider_visibility()

    def _on_session_selected(self, item):
        if not item:
            return
        session_id = item.data(Qt.UserRole)
        if session_id == self.current_session and self.history:
            return
        self.current_session = session_id
        self.chat_view.clear_chat()
        messages = self.chat_store.get_messages(self.current_session)
        self.history = []
        for message in messages:
            self.history.append({"role": message["role"], "content": message["content"]})
            if message["role"] == "user":
                self.chat_view.append_user(message["content"])
            elif message["role"] == "assistant":
                self.chat_view.append_agent(message["content"])
        session = self.chat_store.get_session(self.current_session)
        if session is not None:
            # Быстрый режим и счётчик токенов у каждого чата свои.
            self._fast_mode = bool(session.get("fast_mode", False))
            self._sync_fast_button()
        self._update_cost_label()
        self._apply_provider_visibility()

    def _current_provider_key(self) -> str:
        """Провайдер текущего чата (в чате он может быть своим)."""
        provider = self.settings.get_provider()
        if self.current_session:
            session = self.chat_store.get_session(self.current_session)
            if session and session.get("provider"):
                provider = session["provider"]
        return providers.normalize(provider)

    def _apply_provider_visibility(self):
        """Локальная модель: баланс в рублях не нужен — показываем токены."""
        info = providers.get(self._current_provider_key())
        local = bool(info.local) or providers.is_ollama_url(self.settings.get_base_url())
        self.balance_btn.setVisible(not local)
        token_btn = getattr(self, "token_btn", None)
        if token_btn is not None:
            token_btn.setVisible(local)
            if local:
                self._load_token_stats()

    def _load_token_stats(self):
        """Поднять счётчик токенов чата из базы (не сбрасывается)."""
        token_btn = getattr(self, "token_btn", None)
        if token_btn is None:
            return
        if not self.current_session:
            token_btn.clear()
            self._tokens_total_seen = 0
            return
        stats = self.chat_store.get_token_stats(self.current_session)
        # total приходит из ядра накопительно за ход — запоминаем, чтобы
        # прибавлять к базе только прирост.
        self._tokens_total_seen = int(stats.get("total", 0) or 0)
        token_btn.set_stats(stats.get("used", 0), stats.get("limit", 0),
                            stats.get("total", 0))

    def _on_context_ready(self, used, limit, total=0):
        """Занятый контекст: в консоль и в счётчик (с сохранением в базу)."""
        self.console_view.log("[Context] " + ollama.format_tokens(used, limit))
        token_btn = getattr(self, "token_btn", None)
        if token_btn is not None:
            token_btn.set_stats(used, limit, total)
        if self.current_session:
            # Всего токенов считаем по приросту: total приходит накопительно.
            previous = int(getattr(self, "_tokens_total_seen", 0) or 0)
            add = max(0, int(total or 0) - previous) if total else 0
            self._tokens_total_seen = int(total or 0)
            self.chat_store.save_token_stats(
                self.current_session, used=used, limit=limit, add_tokens=add)

    def _update_cost_label(self):
        """Запоминаем потраченное в чате (в интерфейсе счётчик больше не виден)."""
        if not self.current_session:
            self._cost_base = 0.0
            return
        session = self.chat_store.get_session(self.current_session)
        self._cost_base = float(session.get("cost_rub") or 0) if session else 0.0

    # -------------------------------------------------------------- открытие

    def _open_project_folder(self):
        if not self.current_session:
            return
        session = self.chat_store.get_session(self.current_session)
        project_root = session.get("project_root", "") if session else ""
        if not project_root:
            QMessageBox.warning(self, "Внимание",
                                "Для этого чата не задана директория проекта.")
            return
        ok, error = runtime_env.open_in_file_manager(project_root)
        if not ok:
            QMessageBox.warning(self, "Ошибка", error)

    def _open_repository_folder(self):
        status = check_repository(self.settings.get_repository_root())
        if not status.ok:
            QMessageBox.warning(self, "Репозиторий", status.as_text())
            return
        ok, error = runtime_env.open_in_file_manager(status.path)
        if not ok:
            QMessageBox.warning(self, "Репозиторий", error)

    def _open_repository_dialog(self):
        RepositoryDialog(self.settings, self).exec_()
        self.console_view.log("[Repo] " + check_repository(
            self.settings.get_repository_root()).short())

    def _open_gui_debug(self):
        """Отладчик GUI: дерево виджетов, геометрия, подсветка контуров."""
        GuiDebugDialog(target=self, parent=self).exec_()

    # --------------------------------------------------- сервер Ollama

    def _ensure_ollama_server(self, force: bool = False):
        """Поднять/перезапустить сервер Ollama с настройками программы.

        Всё в фоновом потоке: `ollama serve` может стартовать несколько секунд,
        а интерфейс ждать не должен. Если сервер уже работает — не трогаем.
        """
        if threads.is_running(getattr(self, "_ollama_thread", None)):
            return
        from app.agent import ollama_service

        if not ollama_service.find_exe():
            self.console_view.log("[Ollama] программа Ollama не найдена — сервер не запускаю")
            return
        thread = ServerStartThread(
            self.settings.get_ollama_host(),
            self.settings.get_ollama_flags(),
            keep_alive=self.settings.get_ollama_keep_alive(),
            context_length=self.settings.get_ollama_num_ctx(),
            force=force, parent=self,
        )
        thread.log.connect(self.console_view.log)
        thread.done.connect(self._on_ollama_server_done)
        self._ollama_thread = threads.track(thread)
        thread.start()

    def _on_ollama_server_done(self, ok, text):
        self.console_view.log("[Ollama] " + str(text or "").replace("\n", " | "))
        if not ok:
            self.console_view.log(
                "[Ollama] сервер не поднят — проверь «Глобальные настройки → "
                "Ollama (локально)»")
        else:
            ollama.clear_cache()

    def _open_providers(self):
        if ProvidersDialog(self.settings, self).exec_():
            self.console_view.log(
                "[AI] провайдер: " + providers.label_for(self.settings.get_provider())
                + " | " + self.settings.get_base_url())
            self._apply_provider_visibility()
            if not providers.get(self._current_provider_key()).local:
                self.balance_btn.refresh(fresh=True)

    def _open_tools_manager(self):

        project_root = self.settings.get_project_root()
        if self.current_session:
            session = self.chat_store.get_session(self.current_session)
            if session and session.get("project_root"):
                project_root = session["project_root"]
        if ToolsManagerDialog(self.settings, self, project_root,
                              session_id=self.current_session or 0).exec_():
            self.console_view.log(
                "[Tools] " + tooling.describe_disabled(self.settings.get_disabled_tools()))
            self.console_view.log(
                "[Remote] " + self._remote_summary())

    def _open_remote_terminal(self):
        """Окно удалённого терминала (SSH) для текущего чата."""
        from app.ui.remote_terminal_dialog import RemoteTerminalDialog

        if self.remote_store is None:
            self._bind_remote_store()
        if self.remote_store is None:
            QMessageBox.warning(
                self, "Терминал",
                "Профили подключений недоступны: "
                + (self._remote_store_error or "нет базы чатов"))
            return
        if not self.current_session:
            QMessageBox.information(
                self, "Терминал",
                "Открой или создай чат: подключения к серверам принадлежат "
                "конкретному чату, чтобы чаты не мешали друг другу.")
            return
        RemoteTerminalDialog(self.remote_store, self.current_session, self).exec_()
        self.console_view.log("[Remote] " + self._remote_summary())

    def _bind_remote_store(self):
        """Привязать профили удалённых подключений к актуальной базе чатов."""
        self.remote_store = None
        self._remote_store_error = ""
        try:
            from app.config.remote_store import RemoteStore

            self.remote_store = RemoteStore(self.chat_store.db_path)
        except Exception as error:  # noqa: BLE001 - без профилей программа работает
            self._remote_store_error = f"{type(error).__name__}: {error}"

    def _remote_summary(self) -> str:
        """Строка в консоль: что настроено для удалённого доступа в этом чате."""
        if self.remote_store is None:
            return ("профили подключений недоступны: "
                    + (self._remote_store_error or "нет базы чатов"))
        if not self.current_session:
            return "нет активного чата"
        try:
            text = self.remote_store.describe_chat(self.current_session)
        except Exception as error:  # noqa: BLE001
            return f"ошибка чтения профилей: {error}"
        return text or "в этом чате нет подключений (Инструменты → Удалённый доступ)"

    def _show_key_location(self):
        """Где лежит API-ключ: путь + кнопки «открыть папку» и «копировать»."""
        location = self.settings.get_api_key_location()
        path = getattr(getattr(self.settings, "secrets", None), "path", "") or ""
        box = QMessageBox(self)
        box.setWindowTitle("API-ключ")
        box.setText("API-ключ хранится:")
        box.setInformativeText(
            f"{location}\n\n"
            "Windows: %APPDATA%\\AI-Connector\\secrets.ini\n"
            "Linux: ~/.config/AI-Connector/secrets.ini\n"
            "Переменная окружения AI_CONNECTOR_API_KEY имеет приоритет.")
        open_btn = box.addButton("Открыть папку", QMessageBox.ActionRole)
        copy_btn = box.addButton("Копировать путь", QMessageBox.ActionRole)
        box.addButton("Закрыть", QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is open_btn:
            folder = os.path.dirname(path) if path else ""
            if not folder:
                QMessageBox.information(self, "API-ключ", location)
                return
            ok, error = runtime_env.open_in_file_manager(folder)
            if not ok:
                QMessageBox.warning(self, "API-ключ", error)
        elif clicked is copy_btn:
            app = QApplication.instance()
            if app is not None:
                app.clipboard().setText(path or location)
            self.console_view.log("[Secret] путь скопирован: " + (path or location))

    def _open_archive(self):
        dlg = ArchiveDialog(self.chat_store, self)

        dlg.exec_()
        self._load_sessions()
        if dlg.restored:
            self._select_first_session()

    # ------------------------------------------------------- импорт базы

    def _import_database(self):
        """Импорт базы чатов: выбрали файл -> проверили -> подменили с бэкапом."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт базы данных чатов", "",
            "База SQLite (*.db *.sqlite *.sqlite3);;Все файлы (*)")
        if not path:
            return
        source = runtime_env.normalize_path(path)
        info = db_import.inspect_database(source)
        if not info["ok"]:
            QMessageBox.warning(self, "Импорт базы данных", info["message"])
            return
        answer = QMessageBox.question(
            self, "Импорт базы данных",
            f"{info['message']}\n\nТекущая база будет заменена. Её копия "
            "останется в data/backups/.\n\nПродолжить?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        result = db_import.import_database(source, self.chat_store.db_path)
        if not result["ok"]:
            QMessageBox.warning(self, "Импорт базы данных", result["message"])
            return
        self.console_view.log("[DB] " + result["message"])
        if result["backup"]:
            self.console_view.log("[DB] резервная копия: " + result["backup"])
        self.current_session = None
        self.history = []
        self.chat_view.clear_chat()
        self.chat_store = ChatStore(self.chat_store.db_path)
        self._bind_remote_store()
        self._load_sessions()
        self._select_first_session()
        QMessageBox.information(self, "Импорт базы данных", result["message"])

    # --------------------------------------- база чатов от другой версии

    def _find_foreign_databases(self) -> list:
        """Базы чатов других версий рядом с программой (сначала самые полные)."""
        own = os.path.abspath(self.chat_store.db_path)
        try:
            parent = os.path.dirname(os.path.abspath(self.settings.project_dir))
            names = sorted(os.listdir(parent))
        except OSError:
            return []
        found = []
        for name in names:
            db_path = os.path.join(parent, name, "data", "chats.db")
            if os.path.abspath(db_path) == own or not os.path.isfile(db_path):
                continue
            info = db_import.inspect_database(db_path)
            if not info.get("ok"):
                continue
            try:
                mtime = os.path.getmtime(db_path)
            except OSError:
                mtime = 0.0
            found.append((int(info.get("sessions") or 0), mtime, db_path, info))
        found.sort(reverse=True)
        return found

    def _find_foreign_database(self):
        """Самая полная база другой версии (или None)."""
        found = self._find_foreign_databases()
        if not found:
            return None
        _sessions, mtime, db_path, info = found[0]
        return (mtime, db_path, info)

    def _offer_database_import(self) -> bool:
        """Пустая база, а рядом лежит база от другой версии — предложить импорт.

        Именно так «пропадают все чаты» после переезда на новую версию:
        программа создаёт рядом пустой data/chats.db, а старая база остаётся
        в папке предыдущей версии.
        """
        try:
            if self.chat_store.list_sessions():
                return False
        except Exception:
            return False
        found = self._find_foreign_databases()
        if not found:
            return False
        _sessions, mtime, db_path, info = found[0]
        marker = f"{int(mtime)}|{db_path}"
        if self.settings.get_declined_db() == marker:
            return False          # про эту базу уже спрашивали и отказались
        others = ""
        if len(found) > 1:
            others = "\n\nЕщё базы рядом:\n" + "\n".join(
                f"  • {item[2]} — {item[3]['message']}" for item in found[1:4])
        answer = QMessageBox.question(
            self, "Найдена база чатов другой версии",
            f"Рядом с программой есть база чатов:\n{db_path}\n\n"
            f"{info['message']}\n{others}\n\nИмпортировать её? Текущая (пустая) "
            "база будет заменена, копия останется в data/backups/.\n\n"
            "Другие базы можно импортировать вручную: Файл → Импорт базы данных.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            self.settings.set_declined_db(marker)
            return False
        result = db_import.import_database(db_path, self.chat_store.db_path)
        self.console_view.log("[DB] " + result["message"])
        if not result["ok"]:
            return False
        self.chat_store = ChatStore(self.chat_store.db_path)
        self._bind_remote_store()
        self._load_sessions()
        return self.sessions_list.count() > 0

    def _start_without_sessions(self):
        """Первый запуск без чатов: сначала ищем базу предыдущей версии."""
        if not self._offer_database_import():
            self._create_new_chat()

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec_():
            self.theme_name = theme.normalize(self.settings.get_theme())
            self.background_path = self.settings.get_background_image()
            self.corner_mode = theme.normalize_corners(self.settings.get_corner_mode())
            self.square_console = self.settings.get_square_console()
            self.frameless = self.settings.get_frameless()
            self._apply_appearance()
            self._apply_window_frame()
            self._sync_economy_button()
            self.console_view.log("[Settings] Глобальные настройки сохранены")
            # Ollama: если включено «поднимать при старте» — проверим сервер
            # в фоне (работающий не трогаем, поднимаем только лежащий).
            if self.settings.get_ollama_autostart():
                self._ensure_ollama_server()
            self._apply_provider_visibility()

    # --------------------------------------------------------------- отправка

    def _send(self):
        # Пока агент работает, Enter и «Отправить» не запускают новый запрос:
        # раньше здесь вызывалась отмена, и сообщение молча пропадало.
        if self.worker and self.worker.isRunning():
            self.console_view.log(
                "[Wait] агент ещё отвечает — дождись ответа или нажми «Стоп»")
            return

        text = self.input_edit.text().strip()
        if not text:
            return

        # Активного чата может не быть (удалён, убран в архив, импорт базы).
        # Раньше тут стоял молчаливый return — кнопка «Отправить» выглядела
        # сломанной. Теперь открываем первый чат или прямо говорим, что делать.
        if not self.current_session:
            self._select_first_session()
        if not self.current_session:
            self.console_view.log(
                "[Chats] нет активного чата: нажми «+ Новый чат» или выбери чат слева")
            return

        session = self.chat_store.get_session(self.current_session)
        if not session:
            self.current_session = None
            self._select_first_session()
            session = (self.chat_store.get_session(self.current_session)
                       if self.current_session else None)
            if not session:
                self.console_view.log(
                    "[Chats] не удалось открыть чат — выбери его в списке слева")
                return

        project_roots = session.get("project_roots") or []
        if not project_roots and session.get("project_root"):
            project_roots = [session["project_root"]]
        project_root = project_roots[0] if project_roots else ""
        instructions = session.get("instructions", "")
        model = session.get("master_model", "deepseek/deepseek-v4-pro-0813")
        max_iter = session.get("max_iterations", 15)
        self._show_thinking = bool(session.get("show_thinking", True))
        self._sync_thinking_button()
        provider = providers.normalize(session.get("provider") or self.settings.get_provider())
        info = providers.get(provider)
        # Локальная модель: длина контекста из настроек чата (0 = как решит Ollama).
        num_ctx = int(session.get("num_ctx") or 0)
        # Размещение (VRAM/RAM) у каждого чата своё; если не задано — глобальное.
        ollama_mode = ollama.normalize_mode(
            session.get("ollama_mode") or self.settings.get_ollama_mode())
        # Право ИИ менять набор инструментов — настройка конкретного чата.
        ai_tools_allowed = bool(session.get("ai_tools", False))
        self._apply_provider_visibility()


        api_key = self.settings.get_api_key_for(provider)
        base_url = self.settings.get_base_url() or info.base_url
        if info.local:
            base_url = base_url or "http://localhost:11434/v1"
        if info.needs_key and not api_key:
            self.console_view.log(
                f"[ERROR] Нет ключа для {info.label}: открой «Провайдеры и модели» "
                "или настройки чата")
            return
        if not base_url:
            self.console_view.log("[ERROR] Не задан Base URL провайдера")
            return
        if not project_root:
            self.console_view.log("[ERROR] Для этого чата не задана директория проекта")
            return

        self.input_edit.clear()
        self.chat_view.append_user(text)
        self.chat_store.add_message(self.current_session, "user", text)
        self.history.append({"role": "user", "content": text})

        messages = self.chat_store.get_messages(self.current_session)
        if len(messages) == 1 and session["title"] in ("Новый чат", ""):
            self.chat_store.update_session_title(self.current_session, clamp_title(text))
            self._load_sessions()

        self.console_view.log(
            f"[Request] provider={provider} model={model} | max_iter={max_iter} | "
            f"base_url={base_url}"
            + (" | экономный режим" if self.settings.get_economy_mode() else ""))

        self.worker = AgentWorker(
            api_key=api_key, base_url=base_url, model=model,
            project_root=project_root, instructions=instructions,
            max_iterations=max_iter, messages=self._request_messages(),
            project_roots=project_roots,
            repository_root=self.settings.get_repository_root(),
            allow_network=self.settings.get_allow_network(),
            allow_python=self.settings.get_allow_python(),
            # Баланс запрашивается по API-эндпоинту провайдера (не по странице
            # в браузере: туда уходит GET за JSON, а не за HTML).
            balance_url=self.settings.get_balance_url(),
            low_balance_threshold=self.settings.get_low_balance_threshold(),
            share_balance=self.settings.get_share_balance(),
            provider=provider,
            economy=self.settings.get_economy_mode(),
            disabled_tools=self.settings.get_disabled_tools(),
            tools_auto=self.settings.get_tools_auto(),
            github_token=self.settings.get_github_token(),
            github_author=self.settings.get_github_author(),
            github_open_after=self.settings.get_github_open_after(),
            rub_per_usd=self.settings.get_rub_per_usd(),
            price_override=self.settings.get_price_override(),
            num_ctx=num_ctx,
            ollama_mode=ollama_mode,
            ollama_host=self.settings.get_ollama_host(),
            keep_alive=self.settings.get_ollama_keep_alive(),
            # Свои инструменты (data/custom_tools.json) и право ими управлять.
            tools_dir=self.settings.get_tools_dir(),
            allow_tools_edit=ai_tools_allowed,
            verbose_tools=self.settings.get_verbose_tool_log(),
            # Удалённый доступ: профили подключений лежат в базе ЭТОГО чата.
            session_id=self.current_session or 0,
            remote_db_path=self.chat_store.db_path,
            # Быстрый режим: без рассуждений модели (у Ollama think=false).
            fast_mode=bool(getattr(self, "_fast_mode", False)),
        )

        if len(project_roots) > 1:
            self.console_view.log(
                f"[Projects] {len(project_roots)} проекта(ов): "
                + ", ".join(os.path.basename(p.rstrip("/\\")) or p for p in project_roots))
        repo_state = check_repository(self.settings.get_repository_root())
        self.console_view.log(f"[Repo] {repo_state.short()}")
        self.console_view.log("[Python] запуск " +
                              ("разрешён" if self.settings.get_allow_python() else "запрещён"))
        if info.local:
            self.console_view.log(
                f"[Ollama] размещение: {ollama_mode} | "
                f"контекст: {num_ctx or 'по умолчанию Ollama'} | "
                f"сервер: {self.settings.get_ollama_host()}")
        self.console_view.log("[Tools] журнал: " + (
            "полные аргументы" if self.settings.get_verbose_tool_log() else "кратко")
            + " | свои инструменты: " + ("ИИ может менять" if ai_tools_allowed
                                         else "только чтение"))

        self.worker.message_ready.connect(self._on_reply)
        self.worker.log_ready.connect(self.console_view.log)
        self.worker.thinking_ready.connect(self._on_thinking)
        self.worker.error_ready.connect(lambda e: self.console_view.log(f"[ERROR] {e}"))
        self.worker.balance_ready.connect(self._on_balance_ready)
        self.worker.cost_ready.connect(self._on_cost_ready)
        self.worker.context_ready.connect(self._on_context_ready)
        self.worker.confirm_required.connect(self._on_confirm_required)
        self.worker.finished.connect(self._on_worker_finished)
        # Поток на учёте: при выходе его сначала остановят, а не дадут Qt
        # уничтожить работающий QThread (это abort() без traceback).
        threads.track(self.worker)
        self.worker.start()
        crash.log_line(f"[app] агент запущен: {provider}/{model}")

        # Пока агент работает: кнопка «Отправить» заблокирована, «Стоп» видна.
        self._run_active = True
        self._guard_strikes = 0
        self.send_btn.setEnabled(False)
        self.stop_btn.setText("■ Стоп")
        self.stop_btn.setEnabled(True)
        self.stop_btn.show()
        self.input_edit.setEnabled(False)

    def _request_messages(self) -> list:
        """История для запроса к модели — без служебных и пустых реплик.

        В чат служебные строки вида «[ERROR 429] …» или «[Отменено
        пользователем]» выводить полезно, но в историю запроса они попадать не
        должны: провайдер отклоняет пустой content, а модель принимает текст
        ошибки за свой прошлый ответ и продолжает «разговор с ошибкой».
        """
        prepared = []
        for message in self.history:
            role = message.get("role")
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            if role == "assistant" and content.startswith(TECH_REPLY_PREFIXES):
                continue
            prepared.append({"role": role, "content": content})
        return prepared

    def _stop_agent(self):
        """Кнопка «Стоп»: агент остановится после текущего шага."""
        if self.worker is None or not self.worker.isRunning():
            return
        self.worker.cancel()
        self.stop_btn.setText("Остановка…")
        self.stop_btn.setEnabled(False)
        self.console_view.log(
            "[Cancel] Запрошена отмена — агент остановится после текущего шага")

    def _restore_send_ui(self):
        """Вернуть кнопки и поле ввода в рабочее состояние."""
        self._run_active = False
        self._guard_strikes = 0
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Отправить")
        self.send_btn.setProperty("role", "accent")
        self._repolish(self.send_btn)
        self.stop_btn.show()
        self.stop_btn.setText("■ Стоп")
        self.stop_btn.setEnabled(False)
        self.input_edit.setEnabled(True)

    def _ensure_send_enabled(self):
        """Сторож интерфейса (таймер): «Отправить» не должна залипать.

        Если поток агента уже завершился, а сигнал finished до интерфейса не
        дошёл, кнопка оставалась заблокированной до перезапуска программы —
        отсюда жалоба «не могу отправить следующее сообщение». Ждём два тика
        подряд, чтобы не поймать момент старта потока.
        """
        if not getattr(self, "_run_active", False):
            return
        if self.worker is not None and self.worker.isRunning():
            self._guard_strikes = 0
            return
        self._guard_strikes += 1
        if self._guard_strikes < 2:
            return
        self.console_view.log(
            "[UI] агент завершился, а интерфейс остался заблокирован — разблокирую")
        self._restore_send_ui()
        self.balance_btn.refresh()

    def _on_worker_finished(self):
        # Возвращаем кнопки в исходное состояние
        self._restore_send_ui()
        # Остаток мог измениться за время работы — обновляем (из кэша, без лишней сети).
        self.balance_btn.refresh()

    def _on_thinking(self, text: str):
        """Мысли/план агента: в консоль всегда, в чат — если включено в чате."""
        compact = " ".join(str(text or "").split())
        if compact:
            self.console_view.log("[Think] " + compact[:400])
        if self._show_thinking:
            self.chat_view.append_thought(text)

    def _on_balance_ready(self, balance):
        """Баланс, полученный воркером перед запросом к модели."""
        if balance is None:
            return
        self.balance_btn.apply(balance)
        threshold = self.settings.get_low_balance_threshold()
        if balance.ok:
            note = " — режим экономии" if balance.is_low(threshold) else ""
            self.console_view.log(f"[Balance] {balance.amount_text()}{note}")
        else:
            self.console_view.log(f"[Balance] не получен: {balance.message}")

    def _on_cost_ready(self, payload):
        """Стоимость хода: (всего за ход, за последний запрос)."""
        try:
            total, _last = payload
        except (TypeError, ValueError):
            total = 0.0
        display = self._cost_base + float(total or 0)
        if self.current_session:
            self.chat_store.set_cost(self.current_session, display)

    def _on_confirm_required(self, request):
        """GitHub: «ИИ агент хочет выложить код» — спрашиваем пользователя."""
        request = dict(request or {})
        if request.get("action") != "github_publish":
            if self.worker:
                self.worker.resolve_confirm(None)
            return
        self.console_view.log(
            "[GitHub] агент просит разрешение на публикацию: "
            + str(request.get("repo_name") or ""))
        dlg = GithubPublishDialog(request, self)
        if dlg.exec_():
            answer = dlg.answer()
            if answer.get("author"):
                self.settings.set_github_author(answer["author"])
            if self.worker:
                self.worker.resolve_confirm(answer)
        else:
            if self.worker:
                self.worker.resolve_confirm({"action": "cancel"})

    def _on_reply(self, text: str):
        self.chat_view.append_agent(text)
        # Чат могли удалить или убрать в архив, пока агент работал: без этой
        # проверки запись в несуществующую сессию валила слот с исключением.
        if not self.current_session:
            return
        self.chat_store.add_message(self.current_session, "assistant", text)
        self.history.append({"role": "assistant", "content": text})

    # ------------------------------------------------------------------ выход

    def closeEvent(self, event):  # noqa: N802 - Qt API
        """Закрытие окна: сначала останавливаем агента и фоновые потоки.

        Без этого Qt уничтожал работающий AgentWorker и вызывал abort() —
        программа исчезала без Python-исключения (отсюда «вылетело, а папка
        crash_reports пустая»). Теперь: спросить, отменить, дождаться.
        """
        worker = getattr(self, "worker", None)
        if worker is not None and threads.is_running(worker):
            answer = QMessageBox.question(
                self, "ИИ-агент работает",
                "Агент ещё выполняет задачу.\n\n"
                "Остановить его и закрыть программу?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.console_view.log("[App] закрытие: останавливаю агента")
            try:
                worker.cancel()
            except Exception:
                pass
        crash.log_line("[app] закрытие окна")
        for name in ("_ui_guard", "_mask_timer", "_split_save_timer"):
            timer = getattr(self, name, None)
            try:
                if timer is not None:
                    timer.stop()
            except Exception:  # noqa: BLE001
                pass
        running = threads.live_threads()
        survivors = threads.stop_threads(running, 8000)
        if survivors:
            self.console_view.log(
                f"[App] {len(survivors)} поток(ов) не завершились — выходим принудительно")
            threads.hard_exit(0)
        event.accept()
