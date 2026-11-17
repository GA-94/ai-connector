"""Глобальные настройки: вкладки вместо одной длинной колонки.

Раньше всё лежало в одном QVBoxLayout высотой 760 px — на ноутбуке низ
уезжал за экран. Теперь каждая группа в своей вкладке, содержимое вкладки
прокручивается, а кнопки «Сохранить/Отмена» всегда видны внизу.
"""

import os

from app.qt_compat.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QDialogButtonBox, QCheckBox, QGroupBox,
    QPushButton, QFileDialog, QMessageBox, QDoubleSpinBox,
    QSpinBox, QTabWidget, QScrollArea, QWidget, QApplication,
    QListWidget, QListWidgetItem
)

from app.config.settings import AppSettings
from app.config import secrets as secrets_mod
from app.runtime_env import normalize_path, open_in_file_manager
from app.tools.repo_status import check_repository
from app.tools import balance_tools
from app.ui import theme
from app.ui import threads
from app.ui.balance_widget import BalanceFetcher
from app.ui.dialog_fit import fit_dialog
from app.ui.model_probe import ModelsThread, OllamaProbeThread
from app.agent import ollama as ollama_mod
from app.agent import ollama_service
from app.agent import providers as providers_mod
from app.ui.ollama_control import (
    ServerStartThread, EmbedCheckThread, PullModelThread, StatusThread
)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AiConnector7 — Глобальные настройки")
        self._balance_fetcher = None
        self._models_thread = None
        self._build_ui()
        self._load_values()
        fit_dialog(self, width=920, height=680, min_width=640, min_height=430)
        # Рамка окна в цветах темы, как у главного окна.
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "AiConnector7 — глобальные настройки")

    # --------------------------------------------------------------------- UI

    @staticmethod
    def _scrolled(widget: QWidget) -> QScrollArea:
        """Вкладка с прокруткой: на низком экране ничего не обрезается."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(widget)
        return area

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.tabs.addTab(self._scrolled(self._tab_connection()), "Подключение")
        self.tabs.addTab(self._scrolled(self._tab_money()), "Баланс и деньги")
        self.tabs.addTab(self._scrolled(self._tab_models()), "Модели")
        self.tabs.addTab(self._scrolled(self._tab_ollama()), "Ollama (локально)")
        self.tabs.addTab(self._scrolled(self._tab_repo()), "Репозиторий")
        self.tabs.addTab(self._scrolled(self._tab_tools()), "Инструменты")
        self.tabs.addTab(self._scrolled(self._tab_remote()), "Удалённый доступ")
        self.tabs.addTab(self._scrolled(self._tab_look()), "Оформление")
        self.tabs.addTab(self._scrolled(self._tab_storage()), "Хранилище")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------- вкладки

    def _tab_connection(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        api_group = QGroupBox("Подключение к провайдеру")
        api_layout = QVBoxLayout(api_group)

        api_layout.addWidget(QLabel("Base URL (адрес сервера):"))
        self.base_url_edit = QLineEdit()
        api_layout.addWidget(self.base_url_edit)

        api_layout.addWidget(QLabel("API Key:"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText(
            "sk-... (ключ платный, хранится вне папки проекта)")
        api_layout.addWidget(self.api_key_edit)

        self.api_key_hint = QLabel()
        self.api_key_hint.setWordWrap(True)
        self.api_key_hint.setStyleSheet("color: #8a8f98;")
        api_layout.addWidget(self.api_key_hint)

        key_row = QHBoxLayout()
        self.open_key_btn = QPushButton("📂 Открыть папку с ключом")
        self.open_key_btn.setToolTip(
            "Показать в проводнике файл secrets.ini, где лежит ключ")
        self.open_key_btn.clicked.connect(self._open_key_folder)
        key_row.addWidget(self.open_key_btn)
        self.copy_key_path_btn = QPushButton("Копировать путь")
        self.copy_key_path_btn.clicked.connect(self._copy_key_path)
        key_row.addWidget(self.copy_key_path_btn)
        key_row.addStretch(1)
        api_layout.addLayout(key_row)

        page_layout.addWidget(api_group)
        page_layout.addWidget(QLabel(
            "Ключи разных сервисов хранятся отдельно: выбери сервис в "
            "«Глобальные настройки → Провайдеры и модели». Здесь — ключ "
            "текущего провайдера."))
        page_layout.addStretch(1)
        return page

    def _tab_money(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        balance_group = QGroupBox("Баланс аккаунта (GET /credits)")
        balance_layout = QVBoxLayout(balance_group)
        balance_layout.addWidget(QLabel(
            "URL баланса (пусто = Base URL + /credits, т.е. "
            "https://routerai.ru/api/v1/credits):"))
        self.balance_url_edit = QLineEdit()
        self.balance_url_edit.setPlaceholderText("https://routerai.ru/api/v1/credits")
        balance_layout.addWidget(self.balance_url_edit)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("Порог экономии:"))
        self.low_threshold_spin = QDoubleSpinBox()
        self.low_threshold_spin.setRange(0.0, 1_000_000.0)
        self.low_threshold_spin.setDecimals(2)
        self.low_threshold_spin.setSingleStep(10.0)
        self.low_threshold_spin.setToolTip(
            "Если остаток ниже этого значения, агент получает режим экономии. "
            "0 — режим экономии выключен.")
        threshold_row.addWidget(self.low_threshold_spin)
        threshold_row.addStretch(1)
        balance_layout.addLayout(threshold_row)

        self.share_balance_cb = QCheckBox(
            "Сообщать агенту остаток и включать режим экономии при низком балансе")
        balance_layout.addWidget(self.share_balance_cb)

        balance_buttons = QHBoxLayout()
        self.balance_check_btn = QPushButton("Проверить баланс сейчас")
        self.balance_check_btn.clicked.connect(self._check_balance)
        balance_buttons.addWidget(self.balance_check_btn)
        balance_buttons.addStretch(1)
        balance_layout.addLayout(balance_buttons)

        self.balance_result_label = QLabel("Баланс ещё не запрашивался.")
        self.balance_result_label.setWordWrap(True)
        self.balance_result_label.setStyleSheet("color: #8a8f98;")
        balance_layout.addWidget(self.balance_result_label)

        page_layout.addWidget(balance_group)
        page_layout.addWidget(QLabel(
            "У локальной модели (Ollama) баланса нет — программа не будет "
            "показывать рубли и остаток."))
        page_layout.addStretch(1)
        return page

    def _tab_models(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        model_group = QGroupBox("Модели ИИ")
        model_layout = QVBoxLayout(model_group)

        self.use_two_models_cb = QCheckBox("Использовать две модели (Master + Worker)")
        self.use_two_models_cb.stateChanged.connect(self._toggle_worker_model)
        model_layout.addWidget(self.use_two_models_cb)

        model_layout.addWidget(QLabel("Модель (Master):"))
        self.master_model = QComboBox()
        self.master_model.setEditable(True)
        self.master_model.addItems(["deepseek/deepseek-v4-pro-0813"])
        model_layout.addWidget(self.master_model)

        self.worker_label = QLabel("Модель (Worker):")
        model_layout.addWidget(self.worker_label)
        self.worker_model = QComboBox()
        self.worker_model.setEditable(True)
        self.worker_model.addItems(["deepseek/deepseek-v4-pro-0813"])
        model_layout.addWidget(self.worker_model)

        page_layout.addWidget(model_group)

        # --- что реально установлено / доступно у сервера ---
        installed_group = QGroupBox("Установленные модели (спросить сервер)")
        installed_layout = QVBoxLayout(installed_group)
        self.models_source_label = QLabel("Список ещё не запрашивался.")
        self.models_source_label.setWordWrap(True)
        self.models_source_label.setStyleSheet("color: #8a8f98;")
        installed_layout.addWidget(self.models_source_label)
        self.installed_list = QListWidget()
        self.installed_list.setMaximumHeight(150)
        self.installed_list.setToolTip(
            "Двойной клик — взять модель как основную (Master).\n"
            "Для Ollama здесь то, что реально скачано на этом ПК.")
        self.installed_list.itemDoubleClicked.connect(self._use_installed_model)
        installed_layout.addWidget(self.installed_list)
        installed_row = QHBoxLayout()
        self.probe_models_btn = QPushButton("Запросить список моделей")
        self.probe_models_btn.setToolTip(
            "Спросить сервер (или локальную Ollama): какие модели доступны сейчас.\n"
            "Ничего не вводи вручную — список придёт с самого сервера.")
        self.probe_models_btn.clicked.connect(self._probe_models)
        installed_row.addWidget(self.probe_models_btn)
        installed_row.addStretch(1)
        installed_layout.addLayout(installed_row)
        page_layout.addWidget(installed_group)
        page_layout.addStretch(1)
        return page

    def _tab_ollama(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        group = QGroupBox("Локальная модель: куда грузить и какой контекст")
        group_layout = QVBoxLayout(group)

        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("Сервер:"))
        self.ollama_host_edit = QLineEdit()
        self.ollama_host_edit.setPlaceholderText(ollama_mod.DEFAULT_HOST)
        host_row.addWidget(self.ollama_host_edit, 1)
        group_layout.addLayout(host_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Грузить в:"))
        self.ollama_mode_combo = QComboBox()
        for key, label in ollama_mod.MODE_CHOICES:
            self.ollama_mode_combo.addItem(label, key)
        mode_row.addWidget(self.ollama_mode_combo, 1)
        group_layout.addLayout(mode_row)

        ctx_row = QHBoxLayout()
        ctx_row.addWidget(QLabel("Длина контекста по умолчанию:"))
        self.ollama_ctx_spin = QSpinBox()
        self.ollama_ctx_spin.setRange(0, 262144)
        self.ollama_ctx_spin.setSingleStep(1024)
        self.ollama_ctx_spin.setSuffix(" токенов")
        self.ollama_ctx_spin.setSpecialValueText("как решит Ollama")
        ctx_row.addWidget(self.ollama_ctx_spin, 1)
        group_layout.addLayout(ctx_row)

        hint = QLabel(ollama_mod.PLACEMENT_HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8a8f98;")
        group_layout.addWidget(hint)

        diag_row = QHBoxLayout()
        self.ollama_diag_btn = QPushButton("Диагностика")
        self.ollama_diag_btn.setToolTip(
            "Проверить сервер, версию, модели и куда реально попала модель")
        self.ollama_diag_btn.clicked.connect(self._diagnose_ollama)
        diag_row.addWidget(self.ollama_diag_btn)
        self.ollama_unload_btn = QPushButton("Выгрузить модель")
        self.ollama_unload_btn.setToolTip(
            "Выгрузить модель из памяти, чтобы новые настройки применились")
        self.ollama_unload_btn.clicked.connect(self._unload_ollama)
        diag_row.addWidget(self.ollama_unload_btn)
        diag_row.addStretch(1)
        group_layout.addLayout(diag_row)

        self.ollama_diag_label = QLabel("Проверка ещё не запускалась.")
        self.ollama_diag_label.setWordWrap(True)
        self.ollama_diag_label.setStyleSheet("color: #8a8f98;")
        group_layout.addWidget(self.ollama_diag_label)

        page_layout.addWidget(group)

        # ------------------------------------------ сервер: запуск и флаги
        server_group = QGroupBox("Сервер Ollama: запуск, флаги, эмбеддинги")
        server_layout = QVBoxLayout(server_group)

        flags_row = QHBoxLayout()
        flags_row.addWidget(QLabel("Аргументы serve:"))
        self.ollama_flags_edit = QLineEdit()
        self.ollama_flags_edit.setPlaceholderText("пусто — обычный запуск")
        self.ollama_flags_edit.setToolTip(
            "Дополнительные аргументы команды `ollama serve`.\n"
            "ВАЖНО: в свежих сборках Ollama флага --embeddings уже НЕТ —\n"
            "сервер падает с «unknown flag: --embeddings». Эмбеддинги включаются\n"
            "скачиванием модели nomic-embed-text (кнопка ниже).")
        flags_row.addWidget(self.ollama_flags_edit, 1)
        server_layout.addLayout(flags_row)

        self.ollama_autostart_cb = QCheckBox(
            "Поднимать сервер при старте программы (если он не работает)")
        self.ollama_autostart_cb.setToolTip(
            "Ai-Connector запустит `ollama serve` в фоне, если сервер не отвечает.\n"
            "Работающий сервер не трогается.")
        server_layout.addWidget(self.ollama_autostart_cb)

        server_buttons = QHBoxLayout()
        self.ollama_restart_btn = QPushButton("Перезапустить с настройками")
        self.ollama_restart_btn.setToolTip(
            "Остановить сервер (вместе с треем Ollama) и запустить заново\n"
            "с аргументами выше, длиной контекста и временем удержания модели.")
        self.ollama_restart_btn.clicked.connect(
            lambda: self._start_ollama_server(force=True))
        server_buttons.addWidget(self.ollama_restart_btn)
        self.ollama_start_only_btn = QPushButton("Запустить, если лежит")
        self.ollama_start_only_btn.clicked.connect(
            lambda: self._start_ollama_server(force=False))
        server_buttons.addWidget(self.ollama_start_only_btn)
        server_buttons.addStretch(1)
        server_layout.addLayout(server_buttons)

        embed_row = QHBoxLayout()
        self.ollama_embed_btn = QPushButton("Проверить эмбеддинги")
        self.ollama_embed_btn.setToolTip(
            "Спросить /api/embed: годится ли сервер для поиска по смыслу")
        self.ollama_embed_btn.clicked.connect(self._check_embeddings)
        embed_row.addWidget(self.ollama_embed_btn)
        self.ollama_pull_embed_btn = QPushButton(
            f"Скачать {ollama_service.EMBED_MODEL} ({ollama_service.EMBED_MODEL_SIZE})")
        self.ollama_pull_embed_btn.setToolTip(
            "Модель эмбеддингов для поиска по смыслу в проекте.\n"
            "Маленькая, работает на процессоре — в том числе на Windows ARM64.")
        self.ollama_pull_embed_btn.clicked.connect(self._pull_embed_model)
        embed_row.addWidget(self.ollama_pull_embed_btn)
        embed_row.addStretch(1)
        server_layout.addLayout(embed_row)

        self.ollama_server_label = QLabel("Сервер: проверяю…")
        self.ollama_server_label.setWordWrap(True)
        self.ollama_server_label.setStyleSheet("color: #8a8f98;")
        server_layout.addWidget(self.ollama_server_label)

        page_layout.addWidget(server_group)
        page_layout.addStretch(1)
        return page

    def _tab_repo(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        repo_group = QGroupBox("Репозиторий (склад шаблонов, MDK и архивов)")
        repo_layout = QVBoxLayout(repo_group)
        repo_layout.addWidget(QLabel(
            "Папка, из которой ИИ берёт материалы инструментами repo_* "
            "(repo_list, repo_search, repo_extract для zip/tar):"))
        repo_row = QHBoxLayout()
        self.repo_edit = QLineEdit()
        self.repo_edit.setPlaceholderText(
            "C:/assets/repo  — например, сюда кладут MDK-архивы")
        repo_browse = QPushButton("...")
        repo_browse.clicked.connect(self._pick_repo)
        repo_row.addWidget(self.repo_edit)
        repo_row.addWidget(repo_browse)
        repo_layout.addLayout(repo_row)

        self.repo_status_label = QLabel()
        self.repo_status_label.setWordWrap(True)
        repo_layout.addWidget(self.repo_status_label)

        repo_buttons = QHBoxLayout()
        self.repo_check_btn = QPushButton("Проверить")
        self.repo_check_btn.clicked.connect(
            lambda: self._update_repo_status(verbose=True))
        repo_buttons.addWidget(self.repo_check_btn)
        self.repo_panel_btn = QPushButton("Открыть панель репозитория…")
        self.repo_panel_btn.clicked.connect(self._open_repo_panel)
        repo_buttons.addWidget(self.repo_panel_btn)
        repo_buttons.addStretch(1)
        repo_layout.addLayout(repo_buttons)

        self.repo_edit.textChanged.connect(self._update_repo_status)

        self.allow_network_cb = QCheckBox(
            "Разрешить скачивание архивов по ссылке (repo_fetch)")
        repo_layout.addWidget(self.allow_network_cb)
        repo_layout.addWidget(QLabel(
            "Распаковка всегда идёт только внутрь открытых проектов. "
            "В сам репозиторий программа не пишет."))

        page_layout.addWidget(repo_group)
        page_layout.addStretch(1)
        return page

    def _tab_tools(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        tools_group = QGroupBox("Инструменты агента")
        tools_layout = QVBoxLayout(tools_group)
        self.allow_python_cb = QCheckBox(
            "Разрешить запуск Python в папке проекта (python_run)")
        tools_layout.addWidget(self.allow_python_cb)
        tools_layout.addWidget(QLabel(
            "Агент запускает python -c / скрипт из проекта и получает stdout, "
            "stderr и код возврата. Отключи, если код исполняться не должен."))

        self.tools_manager_btn = QPushButton("Менеджер инструментов…")
        self.tools_manager_btn.setToolTip(
            "Какие инструменты доступны ИИ-агенту (файлы, Java, Python, Git…)")
        self.tools_manager_btn.clicked.connect(self._open_tools_manager)
        tools_layout.addWidget(self.tools_manager_btn)

        page_layout.addWidget(tools_group)
        page_layout.addStretch(1)
        return page

    def _tab_remote(self) -> QWidget:
        """Ввод данных для терминала и хранилищ: отдельно по каждому чату."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        hint = QLabel(
            "Данные подключений к удалённому терминалу (SSH) и файловым "
            "хранилищам (SMB, FTP, SFTP, Amazon S3, WebDAV, SCP).\n"
            "Подключения принадлежат КОНКРЕТНОМУ чату: другой чат их не видит, а "
            "инструменты агента работают только с подключениями своего чата.\n"
            "Логин и пароль (или приватный ключ) сохраняются в реестре Windows или "
            "в файле конфигов Linux — в базу чатов и в папку проекта они не "
            "попадают и дальше подставляются автоматически.")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        page_layout.addWidget(hint)

        chat_group = QGroupBox("Чат")
        chat_layout = QVBoxLayout(chat_group)
        chat_row = QHBoxLayout()
        chat_row.addWidget(QLabel("Настраиваем для:"))
        self.remote_chat_combo = QComboBox()
        self.remote_chat_combo.setMinimumWidth(300)
        self.remote_chat_combo.currentIndexChanged.connect(
            lambda *_: self._refresh_remote_lists())
        chat_row.addWidget(self.remote_chat_combo, 1)
        chat_refresh = QPushButton("⟳")
        chat_refresh.setToolTip("Обновить список чатов")
        chat_refresh.clicked.connect(self._reload_remote_chats)
        chat_row.addWidget(chat_refresh)
        chat_layout.addLayout(chat_row)
        self.remote_chat_state = QLabel()
        self.remote_chat_state.setWordWrap(True)
        self.remote_chat_state.setObjectName("mutedLabel")
        chat_layout.addWidget(self.remote_chat_state)
        page_layout.addWidget(chat_group)

        terminal_group = QGroupBox("Удалённый терминал (SSH)")
        terminal_layout = QVBoxLayout(terminal_group)
        self.remote_terminal_check = QCheckBox(
            "Включить терминал в выбранном чате (инструменты remote_terminal_*)")
        self.remote_terminal_check.setToolTip(
            "Пока галочка снята, ИИ не получает инструменты терминала в этом чате "
            "— меньше токенов в каждом запросе. Окно терминала работает всегда.")
        self.remote_terminal_check.toggled.connect(
            lambda checked: self._set_remote_flag("terminal", checked))
        terminal_layout.addWidget(self.remote_terminal_check)
        self.remote_terminal_list = QListWidget()
        self.remote_terminal_list.setMaximumHeight(96)
        terminal_layout.addWidget(self.remote_terminal_list)
        terminal_row = QHBoxLayout()
        terminal_setup = QPushButton("⚙ Ввести данные терминала…")
        terminal_setup.setToolTip(
            "Адрес (IP/хост), порт, логин, пароль или приватный ключ SSH")
        terminal_setup.clicked.connect(
            lambda: self._open_remote_connections("terminal"))
        terminal_row.addWidget(terminal_setup)
        terminal_open = QPushButton("🖥 Открыть терминал…")
        terminal_open.setToolTip(
            "Окно с приглашением: печатаете команду — видите вывод сервера")
        terminal_open.clicked.connect(self._open_remote_terminal)
        terminal_row.addWidget(terminal_open)
        terminal_row.addStretch(1)
        terminal_layout.addLayout(terminal_row)
        page_layout.addWidget(terminal_group)

        storage_group = QGroupBox(
            "Файловые хранилища (SMB, FTP/FTPS, SFTP, SCP, Amazon S3, WebDAV)")
        storage_layout = QVBoxLayout(storage_group)
        self.remote_storage_check = QCheckBox(
            "Включить хранилища в выбранном чате (инструменты remote_storage_*)")
        self.remote_storage_check.setToolTip(
            "Скачивание, загрузка, переименование, удаление и список файлов на "
            "удалённом хранилище этого чата.")
        self.remote_storage_check.toggled.connect(
            lambda checked: self._set_remote_flag("storage", checked))
        storage_layout.addWidget(self.remote_storage_check)
        self.remote_storage_list = QListWidget()
        self.remote_storage_list.setMaximumHeight(96)
        storage_layout.addWidget(self.remote_storage_list)
        storage_row = QHBoxLayout()
        storage_setup = QPushButton("⚙ Ввести данные хранилищ…")
        storage_setup.setToolTip(
            "Сервер, логин, пароль, папка, bucket/key для S3, URL для WebDAV")
        storage_setup.clicked.connect(
            lambda: self._open_remote_connections("storage"))
        storage_row.addWidget(storage_setup)
        storage_row.addStretch(1)
        storage_layout.addLayout(storage_row)
        page_layout.addWidget(storage_group)

        keys_group = QGroupBox("Сохранённые пароли и ключи")
        keys_layout = QVBoxLayout(keys_group)
        self.remote_tab_secrets_label = QLabel(self._remote_secrets_info())
        self.remote_tab_secrets_label.setWordWrap(True)
        self.remote_tab_secrets_label.setObjectName("mutedLabel")
        keys_layout.addWidget(self.remote_tab_secrets_label)
        keys_row = QHBoxLayout()
        delete_keys = QPushButton("🔑 Удалить конкретный пароль или все…")
        delete_keys.clicked.connect(self._open_remote_secrets_from_tab)
        keys_row.addWidget(delete_keys)
        keys_row.addStretch(1)
        keys_layout.addLayout(keys_row)
        page_layout.addWidget(keys_group)

        page_layout.addStretch(1)
        self._reload_remote_chats()
        return page

    # ------------------------------------------------- удалённые подключения
    def _reload_remote_chats(self):
        """Список чатов: у каждого свои подключения и свои галочки."""
        from app.config.chat_store import ChatStore

        previous = None
        try:
            previous = self.remote_chat_combo.currentData()
        except AttributeError:
            previous = None
        sessions = []
        error_text = ""
        try:
            sessions = ChatStore(self._remote_db_path()).list_sessions()
        except Exception as error:  # noqa: BLE001
            error_text = f"Не удалось прочитать список чатов: {error}"
        self.remote_chat_combo.blockSignals(True)
        self.remote_chat_combo.clear()
        for session in sessions:
            title = str(session.get("title") or "без названия")
            self.remote_chat_combo.addItem(f"{title}  (#{session['id']})",
                                           int(session["id"]))
        if previous is None:
            previous = getattr(self.parent(), "current_session", 0) or 0
        index = self.remote_chat_combo.findData(int(previous or 0))
        if index >= 0:
            self.remote_chat_combo.setCurrentIndex(index)
        self.remote_chat_combo.blockSignals(False)
        if error_text:
            self.remote_chat_state.setText(error_text)
        self._refresh_remote_lists()

    def _remote_db_path(self) -> str:
        from app.ui.remote_dialog import remote_db_path

        return remote_db_path(self.settings)

    def _remote_chat_id(self) -> int:
        try:
            return int(self.remote_chat_combo.currentData() or 0)
        except (AttributeError, TypeError, ValueError):
            return 0

    def _refresh_remote_lists(self):
        """Показать подключения и галочки выбранного чата."""
        session_id = self._remote_chat_id()
        has_chat = bool(session_id)
        for widget in (self.remote_terminal_check, self.remote_storage_check,
                       self.remote_terminal_list, self.remote_storage_list):
            widget.setEnabled(has_chat)
        self.remote_terminal_list.clear()
        self.remote_storage_list.clear()
        if not has_chat:
            self.remote_chat_state.setText(
                "Чатов пока нет: создайте чат в главном окне — подключения "
                "хранятся отдельно для каждого чата.")
            return
        try:
            store = self._remote_store()
            from app.config.remote_store import PROTOCOL_LABELS as protocol_labels

            flags = store.get_chat_flags(session_id)
            terminals = store.list_connections(session_id, "terminal")
            storages = store.list_connections(session_id, "storage")
        except Exception as error:  # noqa: BLE001
            self.remote_chat_state.setText(f"Хранилище подключений недоступно: {error}")
            return
        for check, value in ((self.remote_terminal_check, flags["terminal_enabled"]),
                             (self.remote_storage_check, flags["storage_enabled"])):
            check.blockSignals(True)
            check.setChecked(bool(value))
            check.blockSignals(False)
        for item in terminals:
            self.remote_terminal_list.addItem(
                f"{item['name']} — {protocol_labels.get(item['protocol'], item['protocol'])} "
                f"{item['host']}:{item['port']}  [{item['username'] or 'без логина'}]  "
                f"секреты: {item['secret_mask']}")
        if not terminals:
            self.remote_terminal_list.addItem(
                "Пока пусто — нажмите «⚙ Ввести данные терминала…»")
        for item in storages:
            self.remote_storage_list.addItem(
                f"{item['name']} — {protocol_labels.get(item['protocol'], item['protocol'])} "
                f"{item['host']}:{item['port']}  [{item['username'] or 'без логина'}]  "
                f"секреты: {item['secret_mask']}")
        if not storages:
            self.remote_storage_list.addItem(
                "Пока пусто — нажмите «⚙ Ввести данные хранилищ…»")
        self.remote_chat_state.setText(
            f"Чат #{session_id}: терминалов — {len(terminals)}, хранилищ — {len(storages)}. "
            "Пароли и ключи хранятся " + store.secrets.describe() + ".")

    def _set_remote_flag(self, kind: str, checked: bool):
        session_id = self._remote_chat_id()
        if not session_id:
            return
        try:
            store = self._remote_store()
            if kind == "terminal":
                store.set_chat_flags(session_id, terminal_enabled=checked)
            else:
                store.set_chat_flags(session_id, storage_enabled=checked)
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "Удалённый доступ",
                                f"Не удалось сохранить настройку: {error}")

    def _open_remote_connections(self, kind: str):
        from app.ui.remote_dialog import RemoteConnectionsDialog

        session_id = self._remote_chat_id()
        if not session_id:
            QMessageBox.information(
                self, "Удалённые подключения",
                "Сначала создайте или откройте чат: подключения принадлежат чату.")
            return
        try:
            store = self._remote_store()
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "Удалённые подключения",
                                f"Хранилище подключений недоступно: {error}")
            return
        RemoteConnectionsDialog(store, session_id, kind, self).exec_()
        self._refresh_remote_lists()
        if hasattr(self, "remote_secrets_label"):
            self.remote_secrets_label.setText(self._remote_secrets_info())
        if hasattr(self, "remote_tab_secrets_label"):
            self.remote_tab_secrets_label.setText(self._remote_secrets_info())

    def _open_remote_terminal(self):
        from app.ui.remote_terminal_dialog import RemoteTerminalDialog

        session_id = self._remote_chat_id()
        if not session_id:
            QMessageBox.information(
                self, "Терминал",
                "Сначала создайте или откройте чат: подключения принадлежат чату.")
            return
        try:
            store = self._remote_store()
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "Терминал", f"Хранилище подключений недоступно: {error}")
            return
        RemoteTerminalDialog(store, session_id, self).exec_()
        self._refresh_remote_lists()

    def _open_remote_secrets_from_tab(self):
        self._open_remote_secrets()
        if hasattr(self, "remote_tab_secrets_label"):
            self.remote_tab_secrets_label.setText(self._remote_secrets_info())

    def _tab_look(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        look_group = QGroupBox("Оформление")
        look_layout = QVBoxLayout(look_group)

        look_layout.addWidget(QLabel("Тема интерфейса:"))
        self.theme_combo = QComboBox()
        for key in theme.THEME_ORDER:
            self.theme_combo.addItem(theme.label(key), key)
        look_layout.addWidget(self.theme_combo)

        look_layout.addWidget(QLabel("Скругление углов:"))
        self.corner_combo = QComboBox()
        for mode in theme.CORNER_ORDER:
            self.corner_combo.addItem(theme.CORNER_LABELS[mode], mode)
        self.corner_combo.currentIndexChanged.connect(self._sync_corner_checkboxes)
        self.corner_combo.setToolTip(
            "1 — все углы закруглённые\n"
            "2 — закруглены только кнопки\n"
            "3 — закруглены только панели\n"
            "4 — всё скруглено по максимуму")
        look_layout.addWidget(self.corner_combo)

        self.corner_buttons_cb = QCheckBox("Углы скруглены у кнопок")
        self.corner_panels_cb = QCheckBox("Углы скруглены у панелей")
        for box in (self.corner_buttons_cb, self.corner_panels_cb):
            box.setEnabled(False)
            box.setToolTip(
                "Состояние задаётся выбранным режимом скругления: "
                "в режимах 1 и 4 галочки сняты.")
            look_layout.addWidget(box)

        self.console_square_cb = QCheckBox("Прямые углы у консоли")
        self.console_square_cb.setToolTip(
            "Консоль не скругляется даже в режимах 1, 3 и 4 — как в терминале")
        look_layout.addWidget(self.console_square_cb)

        self.frameless_cb = QCheckBox(
            "Своя рамка окна в цветах темы (вместо системной полосы заголовка)")
        self.frameless_cb.setToolTip(
            "Сними галочку, если безрамочное окно мешает (например, не работает "
            "перетаскивание в твоём оконном менеджере). Изменение применится "
            "сразу после сохранения настроек.")
        look_layout.addWidget(self.frameless_cb)

        self.show_thinking_cb = QCheckBox(
            "По умолчанию показывать мысли агента (что модель думает)")
        self.show_thinking_cb.setToolTip(
            "Значение по умолчанию для новых чатов; в каждом чате его можно "
            "переопределить в настройках чата")
        look_layout.addWidget(self.show_thinking_cb)

        look_layout.addWidget(QLabel("Фоновое изображение (необязательно):"))
        bg_row = QHBoxLayout()
        self.bg_edit = QLineEdit()
        self.bg_edit.setPlaceholderText(
            "C:/Pictures/car.jpg — картинка на фон окна (пусто = градиент темы)")
        bg_browse = QPushButton("...")
        bg_browse.clicked.connect(self._pick_background)
        bg_clear = QPushButton("Убрать")
        bg_clear.clicked.connect(lambda: self.bg_edit.setText(""))
        bg_row.addWidget(self.bg_edit)
        bg_row.addWidget(bg_browse)
        bg_row.addWidget(bg_clear)
        look_layout.addLayout(bg_row)

        dim_row = QHBoxLayout()
        dim_row.addWidget(QLabel("Затемнение фона:"))
        self.bg_dim_spin = QSpinBox()
        self.bg_dim_spin.setRange(0, 90)
        self.bg_dim_spin.setSuffix(" %")
        self.bg_dim_spin.setToolTip(
            "Чем больше затемнение, тем легче читать текст поверх картинки")
        dim_row.addWidget(self.bg_dim_spin)
        dim_row.addStretch(1)
        look_layout.addLayout(dim_row)

        page_layout.addWidget(look_group)
        page_layout.addStretch(1)
        return page

    def _tab_storage(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 2, 2)
        page_layout.setSpacing(8)

        keys_group = QGroupBox("Где лежит API-ключ")
        keys_layout = QVBoxLayout(keys_group)
        self.key_location_label = QLabel()
        self.key_location_label.setWordWrap(True)
        self.key_location_label.setTextInteractionFlags(Qt_TextSelectable())
        keys_layout.addWidget(self.key_location_label)
        keys_layout.addWidget(QLabel(
            "На Windows: %APPDATA%\\AI-Connector\\secrets.ini\n"
            "На Linux/macOS: ~/.config/AI-Connector/secrets.ini\n"
            "Переменная окружения AI_CONNECTOR_API_KEY имеет приоритет; "
            "AI_CONNECTOR_SECRETS задаёт свой путь к файлу."))

        keys_row = QHBoxLayout()
        open_keys = QPushButton("📂 Открыть папку с ключом")
        open_keys.clicked.connect(self._open_key_folder)
        keys_row.addWidget(open_keys)
        copy_keys = QPushButton("Копировать путь")
        copy_keys.clicked.connect(self._copy_key_path)
        keys_row.addWidget(copy_keys)
        keys_row.addStretch(1)
        keys_layout.addLayout(keys_row)
        page_layout.addWidget(keys_group)

        remote_group = QGroupBox(
            "Пароли удалённых подключений (SSH, SMB, FTP, SFTP, S3, WebDAV, SCP)")
        remote_layout = QVBoxLayout(remote_group)
        self.remote_secrets_label = QLabel()
        self.remote_secrets_label.setWordWrap(True)
        self.remote_secrets_label.setText(self._remote_secrets_info())
        remote_layout.addWidget(self.remote_secrets_label)
        remote_row = QHBoxLayout()
        remote_btn = QPushButton("🔑 Управление паролями…")
        remote_btn.setToolTip(
            "Посмотреть, удалить конкретный сохранённый пароль/ключ, удалить "
            "«осиротевшие» записи или стереть все сразу")
        remote_btn.clicked.connect(self._open_remote_secrets)
        remote_row.addWidget(remote_btn)
        remote_row.addStretch(1)
        remote_layout.addLayout(remote_row)
        page_layout.addWidget(remote_group)

        data_group = QGroupBox("Файлы программы")
        data_layout = QVBoxLayout(data_group)
        self.paths_label = QLabel()
        self.paths_label.setWordWrap(True)
        data_layout.addWidget(self.paths_label)
        data_row = QHBoxLayout()
        open_project = QPushButton("📂 Папка программы")
        open_project.clicked.connect(
            lambda: self._open_folder(self.settings.project_dir))
        data_row.addWidget(open_project)
        open_data = QPushButton("📂 Папка data (база чатов)")
        open_data.clicked.connect(
            lambda: self._open_folder(os.path.join(self.settings.project_dir, "data")))
        data_row.addWidget(open_data)
        data_row.addStretch(1)
        data_layout.addLayout(data_row)
        page_layout.addWidget(data_group)

        page_layout.addStretch(1)
        return page

    # ---------------------------------------------------------------- helpers

    # ------------------------------------------------- удалённые подключения
    def _remote_store(self):
        from app.tools import remote_ui_bridge
        from app.ui.remote_dialog import remote_db_path

        return remote_ui_bridge.store_for(remote_db_path(self.settings))

    def _remote_secrets_info(self) -> str:
        try:
            store = self._remote_store()
            return (f"Место хранения: {store.secrets.describe()}\n"
                    f"Сохранено записей: {store.secrets.count()}. "
                    "В базу чатов и в папку проекта пароли не попадают — данные "
                    "подключений вводятся на вкладке «Удалённый доступ» "
                    "(и в «Инструменты → Удалённый доступ»).")
        except Exception as error:  # noqa: BLE001
            return f"Не удалось прочитать хранилище паролей: {error}"

    def _open_remote_secrets(self):
        from app.ui.remote_dialog import RemoteSecretsDialog

        try:
            store = self._remote_store()
        except Exception as error:  # noqa: BLE001
            QMessageBox.warning(self, "Пароли подключений",
                                f"Хранилище недоступно: {error}")
            return
        RemoteSecretsDialog(store, self).exec_()
        self.remote_secrets_label.setText(self._remote_secrets_info())

    def _secret_path(self) -> str:
        try:
            return self.settings.secrets.path
        except AttributeError:
            return secrets_mod.default_secrets_path()

    def _open_folder(self, path: str):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "Папка не найдена",
                                "Путь не существует:\n" + str(path))
            return
        ok, error = open_in_file_manager(folder)
        if not ok:
            QMessageBox.warning(self, "Не удалось открыть", error)

    def _open_key_folder(self):
        self._open_folder(self._secret_path())

    def _copy_key_path(self):
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(self._secret_path())
        self.console_note = "Путь к ключу скопирован"

    def _toggle_worker_model(self, state):
        is_checked = state == 2
        self.worker_label.setVisible(is_checked)
        self.worker_model.setVisible(is_checked)

    def _sync_corner_checkboxes(self, *args):
        """Режим задаёт скругление; галочки 2 и 3 только показывают состояние."""
        mode = int(self.corner_combo.currentData() or 1)
        self.corner_buttons_cb.setChecked(mode == 2)
        self.corner_panels_cb.setChecked(mode == 3)

    # --------------------------------------- что установлено: запрос моделей

    def _probe_models(self):
        """Спросить сервер: какие модели доступны (вместо догадок по памяти)."""
        if threads.is_running(getattr(self, "_models_thread", None)):
            return
        self.installed_list.clear()
        provider = providers_mod.normalize(self.settings.get_provider())
        info = providers_mod.get(provider)
        base_url = self.base_url_edit.text().strip() or info.base_url
        if info.local:
            host = self.ollama_host_edit.text().strip() or ollama_mod.DEFAULT_HOST
            self.models_source_label.setText(
                "Спрашиваю локальный Ollama: "
                + ollama_mod.normalize_host(host) + "…")
            self.models_source_label.setStyleSheet("color: #8a8f98;")
            thread = OllamaProbeThread(host, "auto", self)
            thread.done.connect(self._on_ollama_models)
        else:
            key = (self.api_key_edit.text().strip()
                   or self.settings.get_api_key_for(provider))
            self.models_source_label.setText("Спрашиваю сервер: " + str(base_url) + "…")
            self.models_source_label.setStyleSheet("color: #8a8f98;")
            thread = ModelsThread(base_url, key, self)
            thread.done.connect(
                lambda ok, result: self._on_server_models(ok, result, base_url))
        self._models_thread = threads.track(thread)
        thread.start()

    def _on_server_models(self, ok, result, base_url):
        self.installed_list.clear()
        if not ok:
            self.models_source_label.setText(
                "Не удалось получить список с " + str(base_url) + ": " + str(result))
            self.models_source_label.setStyleSheet("color: #e05a4f;")
            return
        names = [str(name) for name in result]
        for name in names:
            self.installed_list.addItem(QListWidgetItem(name))
        self.models_source_label.setText(
            f"Сервер вернул моделей: {len(names)}. Двойной клик — взять как основную.")
        self.models_source_label.setStyleSheet("color: #8a8f98;")
        self._fill_model_combos(names)

    def _on_ollama_models(self, data):
        models = list((data or {}).get("models") or [])
        names = [entry.get("name") for entry in models if entry.get("name")]
        self.installed_list.clear()
        for entry in models:
            text = f"{entry['name']} — {float(entry.get('size') or 0):.1f} ГБ"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry["name"])
            self.installed_list.addItem(item)
        if not (data or {}).get("running"):
            self.models_source_label.setText(
                "Ollama не отвечает (" + str(((data or {}).get("host") or "")) + "). "
                + ollama_mod.start_hint())
            self.models_source_label.setStyleSheet("color: #e05a4f;")
            return
        self.models_source_label.setText(
            f"Установлено в Ollama моделей: {len(names)}. "
            "Двойной клик — взять как основную.")
        self.models_source_label.setStyleSheet("color: #8a8f98;")
        self._fill_model_combos(names)

    def _fill_model_combos(self, names):
        names = [str(name).strip() for name in names if str(name).strip()]
        if not names:
            return
        master = self.master_model.currentText().strip()
        worker = self.worker_model.currentText().strip()
        self.master_model.clear()
        self.master_model.addItems(names)
        self.worker_model.clear()
        self.worker_model.addItems(names)
        if master:
            self.master_model.setEditText(master)
        if worker:
            self.worker_model.setEditText(worker)

    def _use_installed_model(self, item):
        name = str(item.data(Qt.UserRole) or item.text() or "")
        name = name.split(" — ")[0].strip()
        if not name:
            return
        self.master_model.setEditText(name)
        self.worker_model.setEditText(name)

    # ------------------------------------------------------------------ Ollama

    def _ollama_host(self) -> str:
        text = self.ollama_host_edit.text().strip()
        return text or ollama_mod.DEFAULT_HOST

    def _diagnose_ollama(self):
        text = ollama_mod.diagnose(
            self._ollama_host(),
            self.ollama_mode_combo.currentData() or "auto",
            self.ollama_ctx_spin.value(),
        )
        self.ollama_diag_label.setText(text)
        self.ollama_diag_label.setStyleSheet("color: #8a8f98;")

    # ------------------------------------------- сервер Ollama (запуск/флаги)

    def _ollama_flags(self) -> str:
        edit = getattr(self, "ollama_flags_edit", None)
        return edit.text().strip() if edit is not None else ""

    def _refresh_ollama_server(self):
        """Сводка о сервере — в фоновом потоке (сеть не блокирует окно)."""
        if threads.is_running(getattr(self, "_status_thread", None)):
            return
        thread = StatusThread(self._ollama_host(), self._ollama_flags(), self)
        thread.done.connect(self._on_server_status)
        self._status_thread = threads.track(thread)
        thread.start()

    def _on_server_status(self, data):
        data = data or {}
        if data.get("error"):
            self.ollama_server_label.setText("Проверка не удалась: " + str(data["error"]))
            self.ollama_server_label.setStyleSheet("color: #e05a4f;")
            return
        lines = []
        if not data.get("installed"):
            lines.append("Ollama не установлена. " + ollama_mod.start_hint())
        else:
            lines.append(f"Программа: {data.get('exe')} (версия {data.get('version') or '?'})")
            if data.get("running"):
                lines.append("Сервер: запущен" + (" (через трей Ollama)"
                                                 if data.get("tray") else ""))
                lines.append("Команда: " + (data.get("cmdline") or "не видна"))
                if data.get("missing_flags"):
                    lines.append("В команде нет аргументов: "
                                 + " ".join(data.get("missing_flags") or []))
                lines.append("Эмбеддинги: " + str(data.get("embeddings_text") or "?"))
            else:
                lines.append("Сервер: не отвечает")
        self.ollama_server_label.setText("\n".join(lines))
        self.ollama_server_label.setStyleSheet(
            "color: #2e9e5b" if data.get("running") else "color: #8a8f98;")

    def _start_ollama_server(self, force: bool = False):
        """Поднять/перезапустить сервер с настройками (в фоне)."""
        if threads.is_running(getattr(self, "_server_thread", None)):
            return
        host = self._ollama_host()
        self.ollama_server_label.setText(
            "Перезапускаю сервер Ollama…" if force else "Запускаю сервер Ollama…")
        self.ollama_server_label.setStyleSheet("color: #8a8f98;")
        if force:
            answer = QMessageBox.question(
                self, "Сервер Ollama",
                "Перезапустить сервер Ollama?\n\n"
                "Текущий сервер (и значок Ollama в трее) будет остановлен, "
                "затем запустится заново с настройками программы. "
                "Загруженные модели выгрузятся из памяти.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        # Настройки сохраняем заранее: сервер стартует с тем, что в полях.
        self.settings.set_ollama_host(host)
        self.settings.set_ollama_flags(self._ollama_flags())
        self.settings.set_ollama_keep_alive(self._keep_alive_text())
        self.settings.set_ollama_num_ctx(self.ollama_ctx_spin.value())
        thread = ServerStartThread(
            host, self._ollama_flags(),
            keep_alive=self._keep_alive_text(),
            context_length=self.ollama_ctx_spin.value(),
            force=force, parent=self)
        thread.log.connect(lambda text: self.ollama_server_label.setText(str(text)))
        thread.done.connect(self._on_server_started)
        self._server_thread = threads.track(thread)
        thread.start()

    def _on_server_started(self, ok, text):
        self.ollama_server_label.setText(str(text or ""))
        self.ollama_server_label.setStyleSheet(
            "color: #2e9e5b" if ok else "color: #e05a4f;")
        ollama_mod.clear_cache()

    def _check_embeddings(self):
        """Проверить /api/embed в фоне."""
        if threads.is_running(getattr(self, "_embed_thread", None)):
            return
        self.ollama_server_label.setText("Проверяю эмбеддинги…")
        self.ollama_server_label.setStyleSheet("color: #8a8f98;")
        thread = EmbedCheckThread(self._ollama_host(), self)
        thread.done.connect(self._on_embeddings_checked)
        self._embed_thread = threads.track(thread)
        thread.start()

    def _on_embeddings_checked(self, ok, text):
        self.ollama_server_label.setText(("Эмбеддинги: " if ok else "Эмбеддинги: ")
                                        + str(text or ""))
        self.ollama_server_label.setStyleSheet(
            "color: #2e9e5b" if ok else "color: #e05a4f;")

    def _pull_embed_model(self):
        """Скачать модель эмбеддингов (274 МБ) в фоне, с прогрессом в метке."""
        if threads.is_running(getattr(self, "_pull_thread", None)):
            return
        name = ollama_service.EMBED_MODEL
        self.ollama_pull_embed_btn.setEnabled(False)
        self.ollama_server_label.setText(f"Скачиваю {name}…")
        self.ollama_server_label.setStyleSheet("color: #8a8f98;")
        thread = PullModelThread(self._ollama_host(), name, self)
        thread.progress.connect(
            lambda percent, status: self.ollama_server_label.setText(
                f"{name}: {percent}% · {status}"))
        thread.done.connect(self._on_embed_model_pulled)
        self._pull_thread = threads.track(thread)
        thread.start()

    def _on_embed_model_pulled(self, ok, text):
        self.ollama_pull_embed_btn.setEnabled(True)
        self.ollama_server_label.setText(str(text or ""))
        self.ollama_server_label.setStyleSheet(
            "color: #2e9e5b" if ok else "color: #e05a4f;")
        if ok:
            self.settings.set_ollama_embed_model(ollama_service.EMBED_MODEL)
            ollama_mod.clear_cache()
            self._check_embeddings()

    def _keep_alive_text(self) -> str:
        """Сколько держать модель в памяти (из общих настроек)."""
        return self.settings.get_ollama_keep_alive()

    def _unload_ollama(self):
        host = self._ollama_host()
        ok, models = ollama_mod.list_models(host)
        if not ok:
            QMessageBox.warning(self, "Ollama", str(models))
            return
        names = [entry["name"] for entry in models]
        self.settings.set_ollama_host(host)
        self.settings.set_ollama_mode(self.ollama_mode_combo.currentData() or "auto")
        if not names:
            QMessageBox.information(self, "Ollama", "Моделей нет — нечего выгружать.")
            return
        name = names[0]
        if len(names) > 1:
            from PyQt5.QtWidgets import QInputDialog
            name, accepted = QInputDialog.getItem(
                self, "Ollama", "Какую модель выгрузить?", names, 0, False)
            if not accepted:
                return
        ok, text = ollama_mod.unload(host, name)
        QMessageBox.information(self, "Ollama", text)
        self._diagnose_ollama()

    # ------------------------------------------------------------------ баланс

    def _check_balance(self):
        if self._balance_fetcher is not None and self._balance_fetcher.isRunning():
            return
        api_key = self.api_key_edit.text().strip()
        if not api_key:
            self.balance_result_label.setText("Сначала введи API Key выше.")
            self.balance_result_label.setStyleSheet("color: #e05a4f;")
            return
        self.balance_result_label.setText("Запрашиваю...")
        self.balance_result_label.setStyleSheet("color: #8a8f98;")
        self.balance_check_btn.setEnabled(False)
        self._balance_fetcher = BalanceFetcher(
            api_key,
            self.base_url_edit.text().strip(),
            self.balance_url_edit.text().strip(),
            fresh=True,
            parent=self,
        )
        self._balance_fetcher.done.connect(self._on_balance_checked)
        self._balance_fetcher.finished.connect(
            lambda: self.balance_check_btn.setEnabled(True))
        from app.ui import threads
        threads.track(self._balance_fetcher)
        self._balance_fetcher.start()

    def done(self, result):
        """Перед закрытием гасим фоновые потоки: иначе Qt делает abort()."""
        from app.ui import threads
        threads.stop_for(self)
        super().done(result)

    def _on_balance_checked(self, balance):
        threshold = self.low_threshold_spin.value()
        if balance is not None and balance.ok:
            low = balance.is_low(threshold)
            text = (f"Остаток: {balance.amount_text()} "
                    f"(проверено {balance.time_text()})")
            if threshold > 0:
                text += (" — НИЖЕ порога, агент будет экономить" if low
                         else f" — выше порога {threshold:g}")
            self.balance_result_label.setText(text)
            self.balance_result_label.setStyleSheet(
                "color: #e05a4f;" if low else "color: #2e9e5b;")
        else:
            message = balance.message if balance is not None else "неизвестная ошибка"
            self.balance_result_label.setText("Не удалось получить баланс: " + message)
            self.balance_result_label.setStyleSheet("color: #e05a4f;")

    # ------------------------------------------------------------------ файлы

    def _pick_repo(self):
        path = QFileDialog.getExistingDirectory(self, "Выберите папку репозитория")
        if path:
            self.repo_edit.setText(normalize_path(path))

    def _pick_background(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Фоновое изображение", "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.webp *.gif)")
        if path:
            self.bg_edit.setText(normalize_path(path))

    def _update_repo_status(self, *args, verbose: bool = False):
        status = check_repository(self.repo_edit.text())
        lines = []
        if status.ok:
            lines.append("✔ " + status.short())
        else:
            lines.extend("✖ " + item for item in status.problems)
        lines.extend("• " + item for item in status.warnings)
        lines.extend("→ " + item for item in status.hints)
        self.repo_status_label.setText("\n".join(lines))
        self.repo_status_label.setStyleSheet(
            "color: #2e9e5b;" if status.ok else "color: #e05a4f;")
        if verbose:
            QMessageBox.information(self, "Репозиторий", status.as_text())

    def _open_repo_panel(self):
        self.settings.set_repository_root(self.repo_edit.text().strip())
        self.settings.set_allow_network(self.allow_network_cb.isChecked())
        from app.ui.repository_dialog import RepositoryDialog
        RepositoryDialog(self.settings, self).exec_()
        self.repo_edit.setText(self.settings.get_repository_root())
        self._update_repo_status()

    def _open_tools_manager(self):
        from app.ui.tools_manager_dialog import ToolsManagerDialog
        ToolsManagerDialog(self.settings, self, self.settings.get_project_root()).exec_()

    # ------------------------------------------------------------- значения

    def _load_values(self):
        self.base_url_edit.setText(self.settings.get_base_url())
        self.api_key_edit.setText(self.settings.get_api_key())
        self.api_key_hint.setText(
            f"Ключ хранится: {self.settings.get_api_key_location()}. "
            "Приоритет у переменной окружения AI_CONNECTOR_API_KEY.")
        self.key_location_label.setText(
            "Ключ хранится: " + self.settings.get_api_key_location())
        self.paths_label.setText(
            f"settings.ini: {self.settings.settings_path}\n"
            f"база чатов: {os.path.join(self.settings.project_dir, 'data', 'chats.db')}")
        self.master_model.setEditText(self.settings.get_master_model())
        self.worker_model.setEditText(self.settings.get_worker_model())
        self.repo_edit.setText(self.settings.get_repository_root())
        self.allow_network_cb.setChecked(self.settings.get_allow_network())
        self.allow_python_cb.setChecked(self.settings.get_allow_python())
        self.balance_url_edit.setText(self.settings.get_balance_url())
        self.low_threshold_spin.setValue(self.settings.get_low_balance_threshold())
        self.share_balance_cb.setChecked(self.settings.get_share_balance())
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(theme.normalize(self.settings.get_theme()))))
        self.bg_edit.setText(self.settings.get_background_image())
        self.bg_dim_spin.setValue(self.settings.get_background_dim())
        self.corner_combo.setCurrentIndex(max(
            0, self.corner_combo.findData(self.settings.get_corner_mode())))
        self._sync_corner_checkboxes()
        self.console_square_cb.setChecked(self.settings.get_square_console())
        self.frameless_cb.setChecked(self.settings.get_frameless())
        self.show_thinking_cb.setChecked(self.settings.get_show_thinking())
        self.ollama_host_edit.setText(self.settings.get_ollama_host())
        mode_index = self.ollama_mode_combo.findData(
            ollama_mod.normalize_mode(self.settings.get_ollama_mode()))
        self.ollama_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.ollama_ctx_spin.setValue(int(self.settings.get_ollama_num_ctx()))
        self.ollama_flags_edit.setText(self.settings.get_ollama_flags())
        self.ollama_autostart_cb.setChecked(self.settings.get_ollama_autostart())
        # Статус сервера спрашиваем в фоне: окно рисуется сразу.
        self._refresh_ollama_server()
        self._show_cached_balance()
        self._update_repo_status()

        use_two = self.settings.get_use_two_models()
        self.use_two_models_cb.setChecked(use_two)
        self._toggle_worker_model(2 if use_two else 0)

    def _show_cached_balance(self):
        """Показать уже известный остаток, не делая сетевой запрос."""
        cached = balance_tools.cached_balance(
            self.settings.get_api_key(),
            self.settings.get_base_url(),
            self.settings.get_balance_url(),
        )
        if cached is None:
            self.balance_result_label.setText(
                "Баланс ещё не запрашивался — нажми «Проверить баланс сейчас» "
                "или кнопку «Баланс» в главном окне.")
            self.balance_result_label.setStyleSheet("color: #8a8f98;")
            return
        if cached.ok:
            self.balance_result_label.setText(
                f"Остаток: {cached.amount_text()} (данные на {cached.time_text()})")
            self.balance_result_label.setStyleSheet("color: #2e9e5b;")
        else:
            self.balance_result_label.setText(
                "Последняя попытка не удалась: " + cached.message)
            self.balance_result_label.setStyleSheet("color: #e05a4f;")

    def _save(self):
        self.settings.set_base_url(self.base_url_edit.text().strip())
        self.settings.set_api_key(self.api_key_edit.text().strip())
        self.settings.set_master_model(self.master_model.currentText().strip())
        self.settings.set_worker_model(self.worker_model.currentText().strip())
        self.settings.set_repository_root(self.repo_edit.text().strip())
        self.settings.set_allow_network(self.allow_network_cb.isChecked())
        self.settings.set_allow_python(self.allow_python_cb.isChecked())
        self.settings.set_balance_url(self.balance_url_edit.text().strip())
        self.settings.set_low_balance_threshold(self.low_threshold_spin.value())
        self.settings.set_share_balance(self.share_balance_cb.isChecked())
        self.settings.set_use_two_models(self.use_two_models_cb.isChecked())
        self.settings.set_theme(self.theme_combo.currentData() or "dark")
        self.settings.set_corner_mode(self.corner_combo.currentData() or 1)
        self.settings.set_square_console(self.console_square_cb.isChecked())
        self.settings.set_frameless(self.frameless_cb.isChecked())
        self.settings.set_show_thinking(self.show_thinking_cb.isChecked())
        self.settings.set_background_image(self.bg_edit.text().strip())
        self.settings.set_background_dim(self.bg_dim_spin.value())
        self.settings.set_ollama_host(self._ollama_host())
        self.settings.set_ollama_mode(str(self.ollama_mode_combo.currentData() or "auto"))
        self.settings.set_ollama_num_ctx(self.ollama_ctx_spin.value())
        self.settings.set_ollama_flags(self.ollama_flags_edit.text().strip())
        self.settings.set_ollama_autostart(self.ollama_autostart_cb.isChecked())
        self.accept()


def Qt_TextSelectable():
    """Флаг «текст можно выделить» (импорт Qt держим локально)."""
    from PyQt5.QtCore import Qt
    return Qt.TextSelectableByMouse
