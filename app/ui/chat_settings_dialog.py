import os

from app.qt_compat.QtCore import Qt, QUrl
from app.qt_compat.QtGui import QDesktopServices
from app.qt_compat.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QFileDialog, QDialogButtonBox, QHBoxLayout,
    QGroupBox, QComboBox, QCheckBox, QSpinBox, QListWidget,
    QListWidgetItem, QAbstractItemView, QMessageBox, QScrollArea,
    QWidget, QProgressDialog
)

from app.agent import providers
from app.agent import ollama
from app.config.chat_store import TITLE_LIMIT
from app.config.settings import AppSettings
from app.runtime_env import normalize_path
from app.tools import balance_tools
from app.tools import git_tools
from app.tools.repo_status import check_repository
from app.ui import threads
from app.ui.dialog_fit import fit_dialog
from app.ui.model_probe import OllamaProbeThread, ToolsProbeThread
from app.ui.providers_dialog import PullThread


class ChatSettingsDialog(QDialog):
    """Настройки чата: две колонки, прокрутка и размер под экран.

    Раньше окно было 680x900 и на невысоком экране кнопки «Сохранить» уезжали
    за нижнюю границу. Теперь содержимое в двух колонках и в QScrollArea:
    сначала урезается высота, а освободившееся место добирается шириной.
    """

    def __init__(self, settings: AppSettings, parent=None, title="", project_root="",
                 instructions="", master_model="", worker_model="", use_two=False,
                 max_iterations=15, project_roots=None, show_thinking=None,
                 provider="", balance_url="", curated_model="", num_ctx=None,
                 ollama_mode="", ai_tools=None, fast_mode=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Настройки чата")
        self._pull_thread = None
        self._probe_thread = None
        self._probe_data = None
        self._probe_purpose = "silent"
        self._ollama_rows = {}
        self._build_ui(title, project_root, instructions, master_model, worker_model,
                       use_two, max_iterations, project_roots, show_thinking,
                       provider, balance_url, num_ctx, ollama_mode, ai_tools, fast_mode)
        fit_dialog(self, width=1240, height=740, min_width=880, min_height=440)
        # Своя рамка + «якорь» перетаскивания + тянущиеся края и углы.
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "AiConnector7 — настройки чата")

    # --------------------------------------------------------------------- UI

    def _build_ui(self, title, project_root, instructions, master_model, worker_model,
                  use_two, max_iterations, project_roots, show_thinking,
                  provider, balance_url, num_ctx, ollama_mode, ai_tools=None,
                  fast_mode=None):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # Если места мало — появится горизонтальная прокрутка, а не обрезанные
        # по краям поля (так было при ширине 680 px).
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        columns = QHBoxLayout(content)
        columns.setContentsMargins(2, 2, 2, 2)
        columns.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(8)
        right = QVBoxLayout()
        right.setSpacing(8)
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # ------------------------------------------------- подключение к ИИ
        api_group = QGroupBox("Подключение к ИИ")
        api_layout = QVBoxLayout(api_group)

        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("Сервис:"))
        self.provider_combo = QComboBox()
        for key, label in providers.choices():
            self.provider_combo.addItem(label, key)
        current = provider or self.settings.get_provider()
        index = self.provider_combo.findData(providers.normalize(current))
        self.provider_combo.setCurrentIndex(index if index >= 0 else 0)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_row.addWidget(self.provider_combo, 1)
        api_layout.addLayout(provider_row)

        # Адрес и ключ: для локальной модели прячется целиком (не нужны).
        self.api_block = QWidget()
        api_block_layout = QVBoxLayout(self.api_block)
        api_block_layout.setContentsMargins(0, 0, 0, 0)
        api_block_layout.setSpacing(4)

        api_block_layout.addWidget(QLabel("Base URL:"))
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setText(self.settings.get_base_url())
        api_block_layout.addWidget(self.base_url_edit)

        self.api_key_label = QLabel("API Key (хранится вне папки проекта):")
        api_block_layout.addWidget(self.api_key_label)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        api_block_layout.addWidget(self.api_key_edit)

        key_row = QHBoxLayout()
        self.open_key_btn = QPushButton("📂 Где хранится ключ")
        self.open_key_btn.setToolTip(
            "Открыть папку с файлом secrets.ini, где лежит ключ "
            "(на Windows — %APPDATA%\\AI-Connector, на Linux — ~/.config/AI-Connector)")
        self.open_key_btn.clicked.connect(self._open_key_folder)
        key_row.addWidget(self.open_key_btn)
        key_row.addStretch(1)
        api_block_layout.addLayout(key_row)
        api_layout.addWidget(self.api_block)

        models_row = QHBoxLayout()
        self.key_link = QLabel()
        self.key_link.setOpenExternalLinks(True)
        self.key_link.setObjectName("mutedLabel")
        models_row.addWidget(self.key_link, 1)
        self.load_models_btn = QPushButton("Загрузить модели")
        self.load_models_btn.clicked.connect(self._load_models)
        models_row.addWidget(self.load_models_btn)
        api_layout.addLayout(models_row)

        # Баланс: только для платных провайдеров.
        self.balance_block = QWidget()
        balance_block_layout = QVBoxLayout(self.balance_block)
        balance_block_layout.setContentsMargins(0, 0, 0, 0)
        balance_block_layout.setSpacing(4)
        self.balance_label = QLabel()
        self.balance_label.setWordWrap(True)
        balance_block_layout.addWidget(self.balance_label)
        balance_block_layout.addWidget(QLabel("Ссылка на баланс этого ключа:"))
        balance_row = QHBoxLayout()
        self.balance_url_edit = QLineEdit()
        self.balance_url_edit.setPlaceholderText(
            "например https://platform.openai.com/usage — откроется в браузере")
        balance_row.addWidget(self.balance_url_edit, 1)
        self.open_balance_btn = QPushButton("Открыть")
        self.open_balance_btn.clicked.connect(self._open_balance_page)
        balance_row.addWidget(self.open_balance_btn)
        balance_block_layout.addLayout(balance_row)
        api_layout.addWidget(self.balance_block)
        left.addWidget(api_group)

        # -------------------------------------------------- локальная Ollama
        self.ollama_group = QGroupBox("Локальная модель (Ollama): RAM или VRAM")
        ol = QVBoxLayout(self.ollama_group)
        self._ollama_layout = ol

        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("Сервер:"))
        self.ollama_host_edit = QLineEdit(self.settings.get_ollama_host())
        host_row.addWidget(self.ollama_host_edit, 1)
        self.ollama_check_btn = QPushButton("Проверить")
        self.ollama_check_btn.setToolTip("Спросить Ollama: запущена ли она и что скачано")
        self.ollama_check_btn.clicked.connect(self._check_ollama)
        host_row.addWidget(self.ollama_check_btn)
        ol.addLayout(host_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Грузить в:"))
        self.ollama_mode_combo = QComboBox()
        for key, label in ollama.MODE_CHOICES:
            self.ollama_mode_combo.addItem(label, key)
        mode_index = self.ollama_mode_combo.findData(
            ollama.normalize_mode(ollama_mode or self.settings.get_ollama_mode()))
        self.ollama_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        self.ollama_mode_combo.setToolTip(
            "Видеокарта (VRAM) — все слои на GPU. Оперативная память (RAM) — "
            "только процессор. Авто — как решит сама Ollama.")
        mode_row.addWidget(self.ollama_mode_combo, 1)
        ol.addLayout(mode_row)
        self.ollama_mode_combo.currentIndexChanged.connect(
            lambda _index: self._on_ollama_mode_changed())

        ctx_row = QHBoxLayout()
        ctx_row.addWidget(QLabel("Длина контекста:"))
        self.num_ctx_spin = QSpinBox()
        self.num_ctx_spin.setRange(0, 262144)
        self.num_ctx_spin.setSingleStep(1024)
        self.num_ctx_spin.setSuffix(" токенов")
        self.num_ctx_spin.setSpecialValueText("как решит Ollama")
        if num_ctx in (None, ""):
            value = self.settings.get_ollama_num_ctx()
        else:
            try:
                value = int(num_ctx or 0)
            except (TypeError, ValueError):
                value = self.settings.get_ollama_num_ctx()
        self.num_ctx_spin.setValue(max(0, min(262144, int(value))))
        self.num_ctx_spin.setToolTip(
            "num_ctx. Больше контекста — больше памяти: если модель не влезает "
            "в видеопамять, Ollama выгружает часть в RAM. 8k — разумный старт.")
        ctx_row.addWidget(self.num_ctx_spin, 1)
        ol.addLayout(ctx_row)

        self.ollama_info_label = QLabel("Проверяю Ollama в фоне…")
        self.ollama_info_label.setWordWrap(True)
        self.ollama_info_label.setStyleSheet("color: #8a8f98;")
        ol.addWidget(self.ollama_info_label)

        # Какая модель уйдёт в чат: подпись + кнопки-индикаторы в списке.
        # Отдельный блок «Модели для этого чата» для локальной модели скрыт,
        # поэтому выбор делается прямо здесь.
        self.ollama_choice_label = QLabel("Модель для чата не выбрана.")
        self.ollama_choice_label.setWordWrap(True)
        self.ollama_choice_label.setToolTip(
            "Эта модель записывается в настройки чата — именно она будет "
            "отвечать. Поле «Модели для этого чата» для локальной модели "
            "скрыто, чтобы не выбирать модель дважды.")
        ol.addWidget(self.ollama_choice_label)

        self._ollama_list_label = QLabel(
            "Модели на этом компьютере. Зелёная кнопка = выбрана для чата, "
            "серая = не выбрана:")
        ol.addWidget(self._ollama_list_label)
        self.ollama_models_list = QListWidget()
        self.ollama_models_list.setFixedHeight(142)
        self.ollama_models_list.setToolTip(
            "Список того, что уже скачано в Ollama. Пусто — значит моделей нет "
            "или сервер не отвечает.")
        self.ollama_models_list.itemDoubleClicked.connect(self._pick_ollama_model)
        ol.addWidget(self.ollama_models_list)

        ollama_buttons = QHBoxLayout()
        self.ollama_scan_btn = QPushButton("Обновить список")
        self.ollama_scan_btn.setToolTip(
            "Спросить Ollama: запущена ли она и какие модели скачаны")
        self.ollama_scan_btn.clicked.connect(self._check_ollama)
        ollama_buttons.addWidget(self.ollama_scan_btn)
        self.ollama_recommend_btn = QPushButton("Что скачать")
        self.ollama_recommend_btn.setToolTip(
            "Подобрать модели под это железо (RAM и видеопамять)")
        self.ollama_recommend_btn.clicked.connect(self._show_ollama_recommendations)
        ollama_buttons.addWidget(self.ollama_recommend_btn)
        self.ollama_pull_btn = QPushButton("Скачать")
        self.ollama_pull_btn.setToolTip("Скачать выбранную модель через Ollama")
        self.ollama_pull_btn.clicked.connect(self._pull_ollama_model)
        ollama_buttons.addWidget(self.ollama_pull_btn)
        ollama_buttons.addStretch(1)
        ol.addLayout(ollama_buttons)

        ollama_buttons2 = QHBoxLayout()
        self.ollama_start_btn = QPushButton("Запустить сервер")
        self.ollama_start_btn.setToolTip("ollama serve в фоне (если установлена)")
        self.ollama_start_btn.clicked.connect(self._start_ollama)
        ollama_buttons2.addWidget(self.ollama_start_btn)
        self.ollama_placement_btn = QPushButton("Где модель сейчас")
        self.ollama_placement_btn.setToolTip(
            "Показать, сколько гигабайт модели в видеопамяти, а сколько в RAM")
        self.ollama_placement_btn.clicked.connect(self._show_placement)
        ollama_buttons2.addWidget(self.ollama_placement_btn)
        self.ollama_unload_btn = QPushButton("Выгрузить модель")
        self.ollama_unload_btn.setToolTip(
            "Выгрузить модель из памяти, чтобы новые RAM/VRAM и контекст "
            "применились сразу")
        self.ollama_unload_btn.clicked.connect(self._unload_ollama)
        ollama_buttons2.addWidget(self.ollama_unload_btn)
        self.ollama_diag_btn = QPushButton("Диагностика")
        self.ollama_diag_btn.clicked.connect(self._diagnose_ollama)
        ollama_buttons2.addWidget(self.ollama_diag_btn)
        ollama_buttons2.addStretch(1)
        ol.addLayout(ollama_buttons2)

        # Проверка «а модель вообще видит инструменты?». Отдельная кнопка,
        # потому что проверка грузит модель (27B — это десятки секунд) и
        # делать её молча при открытии окна нельзя.
        tools_row = QHBoxLayout()
        self.ollama_tools_btn = QPushButton("Проверить инструменты")
        self.ollama_tools_btn.setToolTip(
            "Ollama молча выбрасывает список инструментов, если у модели нет "
            "шаблона tools. Тогда модель отвечает «файл создан», хотя ничего "
            "не делала — на этом уже теряли время. Кнопка проверяет это "
            "опытом (два коротких запроса) и запоминает ответ.")
        self.ollama_tools_btn.clicked.connect(self._check_tools_support)
        tools_row.addWidget(self.ollama_tools_btn)
        self.ollama_tools_reset_btn = QPushButton("Сбросить результат")
        self.ollama_tools_reset_btn.setToolTip("Проверить заново, не глядя в кэш")
        self.ollama_tools_reset_btn.clicked.connect(
            lambda: self._check_tools_support(force=True))
        tools_row.addWidget(self.ollama_tools_reset_btn)
        tools_row.addStretch(1)
        ol.addLayout(tools_row)

        self.ollama_tools_label = QLabel("")
        self.ollama_tools_label.setWordWrap(True)
        self.ollama_tools_label.setStyleSheet("color: #8a8f98;")
        ol.addWidget(self.ollama_tools_label)

        hint = QLabel(ollama.PLACEMENT_HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8a8f98;")
        ol.addWidget(hint)
        left.addWidget(self.ollama_group)

        # ------------------------------------------------------------- модели
        self.model_group = QGroupBox("Модели для этого чата")
        model_group = self.model_group
        model_layout = QVBoxLayout(model_group)
        self._model_layout = model_layout
        self.use_two_models_cb = QCheckBox("Использовать две модели (Master + Worker)")
        self.use_two_models_cb.setChecked(use_two)
        self.use_two_models_cb.stateChanged.connect(self._toggle_worker_model)
        model_layout.addWidget(self.use_two_models_cb)
        model_layout.addWidget(QLabel("Модель (Master):"))
        self.master_model_combo = QComboBox()
        self.master_model_combo.setEditable(True)
        self.master_model_combo.setEditText(master_model)
        model_layout.addWidget(self.master_model_combo)
        self.worker_label = QLabel("Модель (Worker):")
        self.worker_model_combo = QComboBox()
        self.worker_model_combo.setEditable(True)
        self.worker_model_combo.setEditText(worker_model)
        model_layout.addWidget(self.worker_label)
        model_layout.addWidget(self.worker_model_combo)
        left.addWidget(model_group)
        left.addStretch(1)

        # -------------------------------------------------- параметры проекта
        chat_group = QGroupBox("Параметры проекта")
        chat_layout = QVBoxLayout(chat_group)
        chat_layout.addWidget(QLabel(f"Название чата (до {TITLE_LIMIT} символов):"))
        self.title_edit = QLineEdit(title)
        self.title_edit.setMaxLength(TITLE_LIMIT)
        chat_layout.addWidget(self.title_edit)

        chat_layout.addWidget(QLabel(
            "Проекты, к которым имеет доступ ИИ (первый в списке — основной):"))
        self.roots_list = QListWidget()
        self.roots_list.setFixedHeight(96)
        self.roots_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.roots_list.setToolTip(
            "ИИ сможет читать и править файлы во всех перечисленных проектах.\n"
            "Относительные пути по умолчанию разрешаются в основном (верхнем) проекте.")
        chat_layout.addWidget(self.roots_list)

        buttons_row = QHBoxLayout()
        self.add_root_btn = QPushButton("Добавить папку")
        self.add_root_btn.clicked.connect(self._add_root)
        self.remove_root_btn = QPushButton("Убрать")
        self.remove_root_btn.clicked.connect(self._remove_root)
        self.primary_root_btn = QPushButton("Сделать основным")
        self.primary_root_btn.clicked.connect(self._make_primary)
        buttons_row.addWidget(self.add_root_btn)
        buttons_row.addWidget(self.remove_root_btn)
        buttons_row.addWidget(self.primary_root_btn)
        chat_layout.addLayout(buttons_row)

        self.repo_label = QLabel()
        self._update_repo_label()
        self.repo_label.setWordWrap(True)
        self.repo_label.setStyleSheet("color: #8a8f98;")
        chat_layout.addWidget(self.repo_label)

        chat_layout.addWidget(QLabel("Инструкции для ИИ в этом чате:"))
        self.instructions_edit = QTextEdit()
        self.instructions_edit.setPlainText(instructions)
        self.instructions_edit.setFixedHeight(70)
        chat_layout.addWidget(self.instructions_edit)

        it_layout = QHBoxLayout()
        it_layout.addWidget(QLabel("Лимит итераций:"))
        self.iterations_spin = QSpinBox()
        self.iterations_spin.setRange(5, 500)
        self.iterations_spin.setValue(max_iterations)
        it_layout.addWidget(self.iterations_spin)
        chat_layout.addLayout(it_layout)

        default_thinking = (self.settings.get_show_thinking()
                            if show_thinking is None else bool(show_thinking))
        self.show_thinking_cb = QCheckBox(
            "Показывать мысли агента (что модель думает, пока решает задачу)")
        self.show_thinking_cb.setChecked(default_thinking)
        self.show_thinking_cb.setToolTip(
            "Галочка снята — шаги размышлений скрыты, в чат попадает только "
            "готовый ответ. В консоли шаги видны всегда.")
        chat_layout.addWidget(self.show_thinking_cb)

        # Быстрый режим: модель не рассуждает вслух, отвечает сразу.
        default_fast = (self.settings.get_fast_mode()
                        if fast_mode is None else bool(fast_mode))
        self.fast_mode_cb = QCheckBox(
            "⚡ Быстрый режим: не рассуждать, отвечать сразу")
        self.fast_mode_cb.setChecked(default_fast)
        self.fast_mode_cb.setToolTip(
            "Включено — у локальной Ollama запрос идёт с think=false (модель не\n"
            "тратит время на «размышления»), у облачных провайдеров — пониженное\n"
            "усилие рассуждений. Ответ приходит заметно быстрее, но сложные\n"
            "задачи модель решает хуже. Настройка своя для каждого чата.")
        chat_layout.addWidget(self.fast_mode_cb)

        # Разрешение менять набор инструментов именно в этом чате: без него
        # агент не может создать/править/удалить свой инструмент.
        default_ai_tools = (self.settings.get_ai_can_edit_tools()
                            if ai_tools is None else bool(ai_tools))
        self.ai_tools_cb = QCheckBox(
            "Разрешить ИИ изменять, добавлять, удалять инструменты")
        self.ai_tools_cb.setChecked(default_ai_tools)
        self.ai_tools_cb.setToolTip(
            "Включено — если штатных инструментов не хватает, агент создаст свой\n"
            "(tool_create / tool_update / tool_delete). Созданный инструмент\n"
            "сразу виден в «Инструменты ИИ-агента».\n"
            "Выключено — набор менять нельзя: агент попросит разрешение у тебя.\n"
            "Ограничение не даёт ИИ бесконтрольно плодить инструменты.")
        chat_layout.addWidget(self.ai_tools_cb)
        right.addWidget(chat_group)

        # ------------------------------------------------------------ GitHub
        github_group = QGroupBox("GitHub (для инструмента публикации)")
        github_layout = QVBoxLayout(github_group)
        self.github_state_label = QLabel()
        self.github_state_label.setWordWrap(True)
        github_layout.addWidget(self.github_state_label)

        github_layout.addWidget(QLabel(
            "Personal access token (права repo). Хранится в secrets.ini вне проекта:"))
        self.github_token_edit = QLineEdit()
        self.github_token_edit.setEchoMode(QLineEdit.Password)
        self.github_token_edit.setPlaceholderText("ghp_… / github_pat_…")
        github_layout.addWidget(self.github_token_edit)

        github_row = QHBoxLayout()
        self.github_check_btn = QPushButton("Проверить вход")
        self.github_check_btn.clicked.connect(self._check_github)
        github_row.addWidget(self.github_check_btn)
        github_row.addWidget(QLabel("Автор:"))
        self.github_author_edit = QLineEdit(self.settings.get_github_author())
        self.github_author_edit.setPlaceholderText("как подписывать коммиты")
        github_row.addWidget(self.github_author_edit, 1)
        github_layout.addLayout(github_row)

        self.github_open_cb = QCheckBox(
            "Открыть репозиторий в браузере после публикации")
        self.github_open_cb.setChecked(self.settings.get_github_open_after())
        github_layout.addWidget(self.github_open_cb)
        right.addWidget(github_group)
        right.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._load_roots(project_root, project_roots)
        self.balance_url_edit.setText(
            balance_url or self.settings.get_balance_url_for(self._current_provider()))
        self._on_provider_changed(self.provider_combo.currentIndex())
        self._update_github_state()

    # ---------------------------------------------------------------- helpers

    def _current_provider(self) -> str:
        return str(self.provider_combo.currentData() or "custom")

    def _is_local(self) -> bool:
        return bool(providers.get(self._current_provider()).local)

    def _ollama_host(self) -> str:
        text = self.ollama_host_edit.text().strip() if hasattr(self, "ollama_host_edit") else ""
        return text or self.settings.get_ollama_host()

    def _apply_local_visibility(self):
        """Локальная модель: баланс, ключ и Base URL не нужны — прячем."""
        local = self._is_local()
        self.api_block.setVisible(not local)
        self.balance_block.setVisible(not local)
        self.ollama_group.setVisible(local)
        # Для локальной Ollama блок «Модели для этого чата» скрыт целиком:
        # раньше двойной клик по списку писал имя в одно поле, а работало
        # другое, и чат молча переставал отвечать. Теперь модель выбирается
        # только в списке «Модели на этом компьютере».
        self.model_group.setVisible(not local)
        self._place_two_models_checkbox(local)
        if local:
            # Никакой сети в главном потоке: статус приходит из фонового потока.
            self._start_ollama_probe("silent")
        self._refresh_ollama_selection()

    def _place_two_models_checkbox(self, local: bool):
        """Галочка «две модели» переезжает в блок Ollama, когда он показан.

        Прятать её вместе с блоком нельзя: тогда для локальной модели нельзя
        было бы включить Master + Worker.
        """
        checkbox = self.use_two_models_cb
        target = self._ollama_layout if local else self._model_layout
        if target.indexOf(checkbox) >= 0:
            return
        if local:
            index = self._ollama_layout.indexOf(self._ollama_list_label)
            if index < 0:
                self._ollama_layout.addWidget(checkbox)
            else:
                self._ollama_layout.insertWidget(index, checkbox)
        else:
            self._model_layout.insertWidget(0, checkbox)

    def _on_provider_changed(self, _index):
        key = self._current_provider()
        info = providers.get(key)
        saved = self.settings.get_api_key_for(key)
        self.api_key_edit.setText(saved)
        self.api_key_edit.setEnabled(info.needs_key)
        self.api_key_edit.setPlaceholderText(
            "не нужен для локальной модели" if not info.needs_key else "ключ провайдера")
        if self.settings.get_provider() != key or not self.base_url_edit.text().strip():
            self.base_url_edit.setText(info.base_url or self.base_url_edit.text())
        if not self.balance_url_edit.text().strip():
            self.balance_url_edit.setText(self.settings.get_balance_url_for(key))
        self.key_link.setText(
            f'<a href="{info.keys_url}">Где взять ключ</a>' if info.keys_url else "")
        fill_models = []
        if key == "ollama":
            # Уже известные модели — из кэша; свежий список придёт из фона.
            fill_models = [entry["name"] for entry in ollama.cached_models(self._ollama_host())]
        if not fill_models:
            fill_models = list(info.models)
        self._fill_models(fill_models)
        self._apply_local_visibility()
        self._update_balance_label()

    def _fill_models(self, names):
        current_master = self.master_model_combo.currentText().strip()
        current_worker = self.worker_model_combo.currentText().strip()
        self.master_model_combo.clear()
        self.worker_model_combo.clear()
        self.master_model_combo.addItems(names)
        self.worker_model_combo.addItems(names)
        # addItems сам подставляет первую модель в редактируемое поле — берём
        # её только как значение по умолчанию, а для Ollama ещё и пропускаем
        # модели эмбеддингов: они не умеют отвечать в чате.
        master = current_master or self._first_chat_model(names)
        if master:
            self.master_model_combo.setEditText(master)
        else:
            self.master_model_combo.setCurrentIndex(-1)
            self.master_model_combo.clearEditText()
        if current_worker:
            self.worker_model_combo.setEditText(current_worker)
        else:
            self.worker_model_combo.setCurrentIndex(-1)
            self.worker_model_combo.clearEditText()
        # Списки изменились — обновляем «выбрана / выбрать» у локальных моделей.
        self._refresh_ollama_selection()

    def _load_models(self):
        key = self._current_provider()
        api_key = self.api_key_edit.text().strip()
        base_url = self.base_url_edit.text().strip()
        if key == "ollama":
            # Список скачанных моделей спрашиваем в фоне — окно не подвисает.
            self._start_ollama_probe("models", force=True)
            return
        ok, result = providers.fetch_models(base_url, api_key)
        if not ok:
            QMessageBox.warning(self, "Модели", str(result))
            return
        self._fill_models(result)

    # ------------------------------------------------------------- Ollama

    def _ollama_mode(self) -> str:
        return ollama.normalize_mode(str(self.ollama_mode_combo.currentData() or "auto"))

    def _update_ollama_info(self, data=None):
        """Статус из уже полученных данных. Сеть и nvidia-smi не трогает.

        Раньше здесь стояли is_running/version/placement_text/describe — по
        5-8 секунд на каждую строку, и всё это в главном потоке.
        """
        if not hasattr(self, "ollama_info_label"):
            return
        data = data if data is not None else self._probe_data
        if not data:
            self.ollama_info_label.setText("Проверяю Ollama в фоне…")
            return
        lines = ["Железо: " + (data.get("hardware_text") or "не определено")]
        if not data.get("running"):
            lines.append("Сервер не отвечает. " + ollama.start_hint())
        else:
            ver = data.get("version") or ""
            lines.append("Сервер отвечает" + (f" (версия {ver})" if ver else ""))
            placement = data.get("placement") or ""
            if placement:
                lines.append(placement)
            if data.get("error"):
                lines.append(str(data["error"]))
        self.ollama_info_label.setText("\n".join(lines))

    def _fill_ollama_list(self, entries, installed=True):
        """Список моделей с кнопкой-индикатором «выбрана / выбрать».

        Показываем именно то, что реально есть на компьютере, и рядом с каждой
        строкой — состояние выбора. Кнопка пишет имя модели в поле чата, а
        подпись сверху подтверждает, что записалось.
        """
        self.ollama_models_list.clear()
        self._ollama_rows = {}
        self._ollama_entries = list(entries)
        self._ollama_installed = bool(installed)
        for entry in entries:
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            text = name
            size = float(entry.get("size") or 0)
            if size:
                text += f" — {size:.1f} ГБ"
            for key in ("where", "about"):
                if entry.get(key):
                    text += " · " + str(entry[key])
            if not installed:
                text += " · не скачана"
            elif self._is_embed_model(name):
                text += " · только эмбеддинги (для чата не годится)"
            else:
                # Результат проверки инструментов: модель без шаблона tools
                # не выполнит ни одной команды — предупреждаем прямо в списке.
                tools_ok, _note = ollama.cached_tools_support(self._ollama_host(), name)
                if tools_ok is False:
                    text += " · НЕ принимает инструменты"
            item = QListWidgetItem()
            item.setData(Qt.UserRole, name)
            item.setData(Qt.UserRole + 1, bool(installed))
            item.setToolTip(text)
            self.ollama_models_list.addItem(item)

            # Обычный QWidget не рисует background-color из style sheet, пока
            # не выставлен WA_StyledBackground: без этого зелёная подсветка
            # выбранной строки просто не появлялась.
            row = QWidget()
            row.setAttribute(Qt.WA_StyledBackground, True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(6, 1, 6, 1)
            row_layout.setSpacing(6)
            row_layout.addWidget(QLabel(text), 1)
            button = QPushButton("выбрать")
            # 132, а не 124: жирный «✔ выбрана» вплотную подходил к границе и
            # на другом шрифте/масштабе мог обрезаться.
            button.setFixedWidth(132)
            button.setEnabled(bool(installed))
            button.clicked.connect(
                lambda _checked=False, model=name: self._select_ollama_model(model))
            row_layout.addWidget(button)
            self.ollama_models_list.setItemWidget(item, row)
            item.setSizeHint(row.sizeHint())
            self._ollama_rows[name] = (item, button, row)
        self._refresh_ollama_selection()

    def _select_ollama_model(self, name):
        """Записать модель в поле чата («Модели для этого чата»).

        Для локальной Ollama это единственная точка выбора: и кнопка в списке,
        и двойной клик по строке приходят сюда.
        """
        name = str(name or "").strip()
        if not name:
            return
        two = self.use_two_models_cb.isChecked()
        master = self.master_model_combo.currentText().strip()
        worker = self.worker_model_combo.currentText().strip()
        if not two or not master:
            self.master_model_combo.setEditText(name)
        elif name == master:
            # Клик по уже выбранной модели меняет Master и Worker местами.
            if worker and worker != name:
                self.master_model_combo.setEditText(worker)
                self.worker_model_combo.setEditText(name)
        elif name == worker:
            self.master_model_combo.setEditText(name)
            self.worker_model_combo.setEditText(master)
        elif not worker:
            self.worker_model_combo.setEditText(name)
        else:
            # Оба места заняты — заменяем Master: он и отвечает в чате.
            self.master_model_combo.setEditText(name)
        row = self._ollama_rows.get(name)
        if row:
            self.ollama_models_list.setCurrentItem(row[0])
        self._refresh_ollama_selection()

    @staticmethod
    def _is_embed_model(name: str) -> bool:
        """Модель эмбеддингов: считать вектор умеет, в чате не ответит."""
        low = str(name or "").lower()
        if "embed" in low:
            return True
        return any(low.startswith(str(item).lower())
                   for item in ollama.EMBED_CATALOG)

    @classmethod
    def _first_chat_model(cls, names):
        """Первая модель, годная для чата: эмбеддинги для ответов не подходят.

        Пусто — если годных нет: тогда лучше честно «модель не выбрана», чем
        подставить эмбеддинги и получить непонятную ошибку при первом вопросе.
        """
        for name in names or []:
            if not cls._is_embed_model(name):
                return name
        return ""

    # Залитая зелёная кнопка и подсвеченная строка у выбранной модели.
    # Красить только текст нельзя: на сером прямоугольнике разницы почти не
    # видно, и человек, глядящий в список вполглаза, не понимает, что выбрано.
    _PICK_STYLE_SELECTED = (
        "QPushButton {"
        " background-color: #2e9e5b;"
        " color: #ffffff;"
        " font-weight: bold;"
        " border: 1px solid #248049;"
        " border-radius: 4px;"
        " padding: 2px 6px;"
        "}"
        "QPushButton:hover { background-color: #35b268; }"
        "QPushButton:pressed { background-color: #23743f; }"
        "QPushButton:disabled { background-color: #6f7a72; border-color: #5c665f; }"
    )
    _ROW_STYLE_SELECTED = (
        "background-color: rgba(46, 158, 91, 0.22);"
        "border-left: 3px solid #2e9e5b;"
        "border-radius: 4px;"
    )

    def _refresh_ollama_selection(self):
        """Обновить индикаторы и подпись «Модель для чата: …»."""
        if not hasattr(self, "ollama_choice_label"):
            return
        master = self.master_model_combo.currentText().strip()
        worker = self.worker_model_combo.currentText().strip()
        two = self.use_two_models_cb.isChecked()
        for name, row_data in getattr(self, "_ollama_rows", {}).items():
            # Раньше здесь было (item, button); строку тоже помним, чтобы
            # подсветить её целиком. Кортеж из двух — совместимость на случай
            # старых данных в _ollama_rows.
            if len(row_data) == 3:
                item, button, row = row_data
            else:
                item, button = row_data[0], row_data[1]
                row = None
            if not item.data(Qt.UserRole + 1):
                button.setText("не скачана")
                button.setStyleSheet("")
                if row is not None:
                    row.setStyleSheet("")
                button.setToolTip("Этой модели нет на компьютере — сначала скачай её")
                continue
            if two and name == worker and name != master:
                label = "✔ Worker"
            elif name == master:
                label = "✔ Master" if two else "✔ выбрана"
            else:
                label = "выбрать"
            chosen = label.startswith("✔")
            button.setText(label)
            button.setStyleSheet(self._PICK_STYLE_SELECTED if chosen else "")
            if row is not None:
                row.setStyleSheet(self._ROW_STYLE_SELECTED if chosen else "")
            button.setToolTip(
                "Эта модель уже выбрана для чата." if chosen
                else "Сделать моделью этого чата")
        if not master:
            text = ("Модель для чата не выбрана — нажми «выбрать» у нужной "
                    "модели в списке выше.")
        elif two and worker:
            text = f"Модель для чата: {master} (Master) + {worker} (Worker)"
        else:
            text = f"Модель для чата: {master}"
        if master and self._probe_data and not self._probe_data.get("running"):
            text += " — но сервер Ollama не отвечает, пока не заработает, чат не ответит"
        self.ollama_choice_label.setText(text)
        self.ollama_choice_label.setStyleSheet(
            "color: #2e9e5b;" if master and not (
                self._probe_data and not self._probe_data.get("running"))
            else "color: #e05a4f;")
        self._refresh_tools_label()

    # ------------------------------------------- Ollama: только через фоновый поток

    def _start_ollama_probe(self, purpose="silent", force=False):
        """Спросить Ollama в фоне. purpose: silent | check | models.

        Мгновенно показывает, что известно из кэша, и обновляет статус, когда
        фоновый поток вернёт ответ. Интерфейс не ждёт сеть.
        """
        if not hasattr(self, "ollama_info_label"):
            return
        host = self._ollama_host()
        cached = ollama.cached_probe(host)
        if cached and not force:
            self._on_ollama_probe(cached, "silent")
        if threads.is_running(self._probe_thread):
            # Уже спрашиваем — ответ придёт и обновит всё, что нужно.
            return
        if not cached:
            self.ollama_info_label.setText("Проверяю Ollama в фоне…")
        self._probe_purpose = purpose
        thread = OllamaProbeThread(host, self._ollama_mode(), self)
        self._probe_thread = threads.track(thread)
        thread.done.connect(self._on_probe_done)
        thread.start()

    def _on_probe_done(self, data):
        self._on_ollama_probe(data, self._probe_purpose)

    def _on_ollama_mode_changed(self):
        """Сменили «VRAM/RAM» — размещение в статусе надо переспросить."""
        self._update_ollama_info()
        if self._is_local():
            self._start_ollama_probe("silent", force=True)

    def _on_ollama_probe(self, data, purpose="silent"):
        self._probe_data = data
        models = list(data.get("models") or [])
        self._fill_ollama_list(models)
        self._update_ollama_info(data)
        names = [entry["name"] for entry in models]
        if names:
            # Установленные модели сразу попадают в выпадающие списки — не надо
            # вспоминать, что скачивал раньше.
            self._fill_models(names)
            # Модель для чата не выбрана — берём первую установленную, чтобы
            # чат работал сразу после сохранения настроек.
            if self._is_local() and not self.master_model_combo.currentText().strip():
                self._select_ollama_model(self._first_chat_model(names))
        if purpose == "check":
            if not data.get("running"):
                QMessageBox.warning(self, "Ollama",
                                    "Сервер не отвечает.\n\n" + ollama.start_hint())
            elif not names:
                QMessageBox.information(
                    self, "Ollama",
                    "Сервер работает, но моделей нет.\n"
                    "Нажми «Что скачать» — программа подберёт модель под железо.")
            else:
                QMessageBox.information(
                    self, "Ollama",
                    f"Сервер отвечает. Скачано моделей: {len(names)}.\n"
                    + (data.get("placement") or ""))
        elif purpose == "models" and data.get("running") and not names:
            QMessageBox.information(
                self, "Ollama",
                "Сервер работает, но моделей нет. Скачай их в "
                "«Провайдеры и модели» или кнопкой «Что скачать».")

    def _check_ollama(self):
        """Кнопка «Проверить»: спрашиваем в фоне, ответ покажем в окне."""
        self._start_ollama_probe("check", force=True)

    # ------------------------------------- Ollama: видит ли модель инструменты

    def _refresh_tools_label(self):
        """Подпись под кнопкой проверки инструментов (только из кэша)."""
        if not hasattr(self, "ollama_tools_label"):
            return
        name = ""
        if hasattr(self, "master_model_combo"):
            name = self.master_model_combo.currentText().strip()
        ok, text = (None, "")
        if name:
            ok, text = ollama.cached_tools_support(self._ollama_host(), name)
        if ok is None:
            text = (f"Инструменты {name}: не проверялись. Если у модели нет "
                    "шаблона tools, она не прочитает и не создаст ни один файл, "
                    "что бы ни написала в ответе — нажми «Проверить инструменты»."
                    if name else "Инструменты: модель для чата ещё не выбрана.")
            color = "#8a8f98"
        else:
            color = "#2e9e5b" if ok else "#e05a4f"
        self.ollama_tools_label.setText(text)
        self.ollama_tools_label.setStyleSheet(f"color: {color};")

    def _check_tools_support(self, force=False):
        """Спросить у Ollama в фоне, доходят ли до модели инструменты."""
        name = self.master_model_combo.currentText().strip()
        if not name:
            QMessageBox.information(self, "Проверка инструментов",
                                    "Сначала выбери модель для чата в списке выше.")
            return
        thread = getattr(self, "_tools_thread", None)
        if threads.is_running(thread):
            return
        try:
            ctx = int(self.num_ctx_spin.value() or 0)
        except (TypeError, ValueError):
            ctx = 0
        self.ollama_tools_btn.setEnabled(False)
        self.ollama_tools_reset_btn.setEnabled(False)
        self.ollama_tools_label.setStyleSheet("color: #8a8f98;")
        self.ollama_tools_label.setText(
            f"Проверяю {name}: загружаю модель и сравниваю размер промпта со "
            "схемой инструмента и без неё. Для больших моделей это до минуты.")
        thread = ToolsProbeThread(self._ollama_host(), name, ctx, force, self)
        self._tools_thread = threads.track(thread)
        thread.done.connect(self._on_tools_checked)
        thread.finished.connect(self._on_tools_thread_done)
        thread.start()

    def _on_tools_thread_done(self):
        self.ollama_tools_btn.setEnabled(True)
        self.ollama_tools_reset_btn.setEnabled(True)

    def _on_tools_checked(self, ok, text):
        self.ollama_tools_label.setText(text)
        self.ollama_tools_label.setStyleSheet(
            "color: #2e9e5b;" if ok
            else ("color: #e05a4f;" if ok is False else "color: #8a8f98;"))
        # Пометки в списке моделей тоже надо обновить: там видно, какие модели
        # заведомо не годятся для работы с файлами.
        entries = getattr(self, "_ollama_entries", None)
        if entries is not None:
            self._fill_ollama_list(entries, bool(getattr(self, "_ollama_installed", True)))

    def _show_ollama_recommendations(self):
        """Подобрать модели под железо (учитываем и RAM, и видеопамять)."""
        info = ollama.hardware()
        entries = ollama.recommend(info)
        # Это ещё не скачанные модели — кнопки выбора у них нет.
        self._fill_ollama_list(entries, installed=False)
        vram = float(info.get("vram_gb") or 0)
        if vram:
            note = (f"Видеопамять: {vram} ГБ — модели с пометкой VRAM пойдут "
                    "на видеокарту.")
        else:
            note = ("Видеокарта не определена — модели будут считать на "
                    "процессоре (RAM).")
        if entries and vram:
            first = entries[0]
            suggested = ollama.fit_context(first.get("size", 0), vram)
            if suggested:
                note += (f"\nДля {first['name']} разумная длина контекста: "
                         f"{suggested} токенов — иначе модель уедет в RAM.")
        self.ollama_info_label.setText(
            "Железо: " + ollama.describe(info) + "\n" + note
            + "\nВыбери модель в списке и нажми «Скачать».")

    def _pick_ollama_model(self, item):
        """Двойной клик по строке — то же, что кнопка «выбрать»."""
        self._select_ollama_model(str(item.data(Qt.UserRole) or ""))

    def _pull_ollama_model(self):
        item = self.ollama_models_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Ollama", "Выбери модель в списке.")
            return
        name = str(item.data(Qt.UserRole) or "").strip()
        host = self._ollama_host()
        known = self._probe_data or {}
        if not known.get("running"):
            # Проверяли в фоне: если сервер лежит — сразу говорим, что делать.
            if known:
                QMessageBox.warning(self, "Ollama", ollama.start_hint())
                return
            ok, text = ollama.start_server()
            if not ok:
                QMessageBox.warning(self, "Ollama", text)
                return
        progress = QProgressDialog(f"Скачивание {name}…", "", 0, 100, self)
        progress.setWindowTitle("Ollama")
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setCancelButton(None)
        thread = PullThread(host, name, self)
        self._pull_thread = thread
        thread.progress.connect(
            lambda percent, status: (progress.setValue(percent),
                                     progress.setLabelText(f"{status} {percent}%")))
        thread.done.connect(
            lambda ok, text: (progress.close(), self._on_pull_done(ok, text, name)))
        threads.track(thread)
        thread.start()
        progress.exec_()

    def _on_pull_done(self, ok, text, name):
        if not ok:
            QMessageBox.warning(self, "Ollama", text)
            return
        QMessageBox.information(self, "Ollama", text)
        self._select_ollama_model(name)
        self._start_ollama_probe("silent", force=True)

    def _start_ollama(self):
        ok, text = ollama.start_server()
        QMessageBox.information(self, "Ollama", text)
        self._start_ollama_probe("silent", force=True)

    def _unload_ollama(self):
        name = self.master_model_combo.currentText().strip()
        if not name:
            QMessageBox.information(self, "Ollama", "Сначала выбери модель.")
            return
        ok, text = ollama.unload(self._ollama_host(), name)
        QMessageBox.information(self, "Ollama", text)
        self._start_ollama_probe("silent", force=True)

    def _diagnose_ollama(self):
        QMessageBox.information(
            self, "Ollama",
            ollama.diagnose(self._ollama_host(), self._ollama_mode(),
                            self.num_ctx_spin.value(),
                            self.master_model_combo.currentText().strip()))
        self._update_ollama_info()

    def _open_key_folder(self):
        path = getattr(getattr(self.settings, "secrets", None), "path", "") or ""
        folder = os.path.dirname(path) if path else ""
        if not folder:
            QMessageBox.information(self, "API-ключ",
                                    self.settings.get_api_key_location())
            return
        from app.runtime_env import open_in_file_manager
        ok, error = open_in_file_manager(folder)
        if not ok:
            QMessageBox.warning(self, "API-ключ", error)

    def _show_placement(self):
        QMessageBox.information(
            self, "Ollama", ollama.placement_text(self._ollama_host(), self._ollama_mode()))

    # -------------------------------------------------------------- баланс

    def _open_balance_page(self):
        url = self.balance_url_edit.text().strip()
        if not url:
            url = self.settings.get_balance_url_for(self._current_provider())
        if not url:
            QMessageBox.information(self, "Баланс",
                                    "У этого провайдера нет стандартной ссылки — впиши свою.")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _toggle_worker_model(self, state):
        visible = state == Qt.Checked or state == 2
        self.worker_label.setVisible(visible)
        self.worker_model_combo.setVisible(visible)
        self._refresh_ollama_selection()

    def _load_roots(self, project_root, project_roots):
        initial = []
        if project_roots:
            initial.extend([str(r) for r in project_roots if r])
        elif project_root:
            initial.append(project_root)
        seen = set()
        for path in initial:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            self.roots_list.addItem(QListWidgetItem(path))

    def _update_balance_label(self):
        if not hasattr(self, "balance_label"):
            return
        if self._is_local():
            return
        api_key = self.api_key_edit.text().strip()
        cached = balance_tools.cached_balance(
            api_key, self.base_url_edit.text().strip(), "")
        if cached is None:
            self.balance_label.setText(
                "Баланс: ещё не запрашивался — нажми кнопку баланса в главном окне.")
            self.balance_label.setStyleSheet("color: #8a8f98;")
            return
        threshold = self.settings.get_low_balance_threshold()
        if cached.ok:
            text = f"Баланс: {cached.amount_text()} (данные на {cached.time_text()})"
            if cached.is_low(threshold):
                text += f" — ниже порога {threshold:g}: агент будет экономить"
                self.balance_label.setStyleSheet("color: #e05a4f;")
            else:
                self.balance_label.setStyleSheet("color: #2e9e5b;")
        else:
            text = "Баланс: не удалось получить — " + cached.message
            self.balance_label.setStyleSheet("color: #e05a4f;")
        self.balance_label.setText(text)

    def _update_repo_label(self):
        status = check_repository(self.settings.get_repository_root())
        lines = [f"Репозиторий (инструменты repo_*): {status.path or '(не задан)'}"]
        if status.ok:
            lines.append(status.short())
        else:
            lines.extend("✖ " + item for item in status.problems)
            lines.extend("→ " + item for item in status.hints[:2])
        self.repo_label.setText("\n".join(lines))
        self.repo_label.setStyleSheet("color: #8a8f98;" if status.ok else "color: #e05a4f;")

    def _update_github_state(self):
        token = self.settings.get_github_token()
        if token:
            self.github_state_label.setText(
                "GitHub: токен сохранён. Чтобы агент мог выложить код, он спросит "
                "подтверждение в отдельном окне.")
            self.github_state_label.setStyleSheet("color: #2e9e5b;")
        else:
            self.github_state_label.setText(
                "GitHub: не выполнен вход. Вставь токен — иначе кнопка публикации "
                "у агента работать не будет.")
            self.github_state_label.setStyleSheet("color: #8a8f98;")

    def _check_github(self):
        token = self.github_token_edit.text().strip() or self.settings.get_github_token()
        ok, text = git_tools.check_token(token)
        if ok:
            if not self.github_author_edit.text().strip():
                self.github_author_edit.setText(text)
            QMessageBox.information(self, "GitHub", f"Вход выполнен: {text}")
        else:
            QMessageBox.warning(self, "GitHub", str(text))

    def _add_root(self):
        path = QFileDialog.getExistingDirectory(self, "Выберите папку проекта")
        if not path:
            return
        normalized = normalize_path(path)
        for index in range(self.roots_list.count()):
            if self.roots_list.item(index).text().lower() == normalized.lower():
                self.roots_list.setCurrentRow(index)
                return
        self.roots_list.addItem(QListWidgetItem(normalized))
        self.roots_list.setCurrentRow(self.roots_list.count() - 1)

    def _remove_root(self):
        for item in self.roots_list.selectedItems():
            self.roots_list.takeItem(self.roots_list.row(item))

    def _make_primary(self):
        item = self.roots_list.currentItem()
        if not item:
            return
        row = self.roots_list.row(item)
        if row == 0:
            return
        self.roots_list.takeItem(row)
        self.roots_list.insertItem(0, item)
        self.roots_list.setCurrentRow(0)

    def _pick_dir(self):
        self._add_root()

    def get_roots(self) -> list:
        return [self.roots_list.item(i).text().strip()
                for i in range(self.roots_list.count())
                if self.roots_list.item(i).text().strip()]

    def get_data(self):
        roots = self.get_roots()
        return {
            "title": self.title_edit.text().strip() or "Новый чат",
            "project_root": roots[0] if roots else "",
            "project_roots": roots,
            "instructions": self.instructions_edit.toPlainText().strip(),
            "provider": self._current_provider(),
            "base_url": self.base_url_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "balance_url": self.balance_url_edit.text().strip(),
            "master_model": self.master_model_combo.currentText().strip(),
            "worker_model": self.worker_model_combo.currentText().strip(),
            "use_two": self.use_two_models_cb.isChecked(),
            "max_iterations": self.iterations_spin.value(),
            "show_thinking": self.show_thinking_cb.isChecked(),
            "fast_mode": self.fast_mode_cb.isChecked(),
            "ai_tools": self.ai_tools_cb.isChecked(),
            "github_token": self.github_token_edit.text().strip(),
            "github_author": self.github_author_edit.text().strip(),
            "github_open_after": self.github_open_cb.isChecked(),
            # локальная модель: куда грузить и какой длины контекст
            "ollama_mode": str(self.ollama_mode_combo.currentData() or "auto"),
            "ollama_host": self._ollama_host(),
            "num_ctx": int(self.num_ctx_spin.value()),
        }

    def done(self, result):
        """Перед закрытием гасим фоновые потоки: иначе Qt делает abort()."""
        threads.stop_for(self)
        super().done(result)

    def accept(self):
        if self._is_local() and not self.master_model_combo.currentText().strip():
            QMessageBox.warning(
                self, "Модель не выбрана",
                "Для локальной модели нужно выбрать, кто будет отвечать:\n"
                "в списке «Модели на этом компьютере» нажми «выбрать» у нужной "
                "модели (если список пуст — сервер Ollama не отвечает или "
                "модели не скачаны).")
            return
        roots = self.get_roots()
        if not roots:
            QMessageBox.warning(self, "Проект не выбран",
                                "Добавь хотя бы одну папку проекта — иначе ИИ не сможет "
                                "работать с файлами.")
            return
        existing = [path for path in roots if os.path.isdir(path)]
        if not existing:
            QMessageBox.warning(self, "Папка не найдена",
                                "Ни одна из указанных папок не существует:\n"
                                + "\n".join(roots))
            return
        missing = [path for path in roots if path not in existing]
        if missing:
            QMessageBox.warning(self, "Папка не найдена",
                                "Эти папки не существуют и будут убраны из списка:\n"
                                + "\n".join(missing))
            self.roots_list.clear()
            for path in existing:
                self.roots_list.addItem(QListWidgetItem(path))
        super().accept()
