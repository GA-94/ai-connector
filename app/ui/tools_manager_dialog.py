"""Менеджер инструментов агента.

Две вкладки:

  * «Встроенные» — какие группы инструментов отправлять модели (в Python-проекте
    java_* только жгут токены, а значит и деньги);
  * «Свои инструменты» — инструменты из data/custom_tools.json: добавить файлом,
    создать с помощью ИИ, отредактировать, удалить, выключить.

Свои инструменты сохраняются сразу (это файлы на диске), встроенные — по кнопке
«Сохранить».
"""

import json
from pathlib import Path

from app.qt_compat.QtCore import Qt, QThread, pyqtSignal
from app.qt_compat.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QCheckBox, QSpinBox, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget
)

from app.config.settings import AppSettings
from app.tools import custom_tools, tool_designer, tooling
from app.tools.registry import TOOLS


# --------------------------------------------------------------------- редактор

class CustomToolDialog(QDialog):
    """Редактор одного своего инструмента."""

    def __init__(self, tool=None, parent=None, project_root: str = ""):
        super().__init__(parent)
        self.tool = dict(tool or {})
        self.project_root = project_root
        self.setWindowTitle("Инструмент" if tool else "Новый инструмент")
        self.resize(760, 680)
        self._build_ui()
        self._fill()
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, self.windowTitle())

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.addWidget(QLabel("Имя:"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("read_logs — латиница, цифры, _")
        row.addWidget(self.name_edit, 1)
        row.addWidget(QLabel("Тип:"))
        self.kind_combo = QComboBox()
        for key in custom_tools.KINDS:
            self.kind_combo.addItem(custom_tools.KIND_LABELS.get(key, key), key)
        row.addWidget(self.kind_combo)
        row.addWidget(QLabel("Таймаут:"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, custom_tools.MAX_TIMEOUT)
        self.timeout_spin.setSuffix(" с")
        row.addWidget(self.timeout_spin)
        layout.addLayout(row)

        layout.addWidget(QLabel("Описание (по нему модель понимает, когда звать):"))
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText(
            "Читает последние ошибки из logs/latest.log")
        layout.addWidget(self.description_edit)

        layout.addWidget(QLabel("Тело (Python-код с ARGS или команда оболочки с {аргумент}):"))
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText(
            "print(open(ARGS['path'], encoding='utf-8').read()[:2000])")
        layout.addWidget(self.body_edit, 2)

        layout.addWidget(QLabel("Аргументы — JSON-Schema:"))
        self.params_edit = QPlainTextEdit()
        self.params_edit.setFixedHeight(110)
        layout.addWidget(self.params_edit)

        hint = QLabel(
            "Python: аргументы уже в словаре ARGS (например ARGS[\"path\"]), "
            "печатай результат в stdout. "
            "Оболочка: {path} подставится с экранированием.")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        buttons_row = QHBoxLayout()
        self.test_btn = QPushButton("Проверить запуск")
        self.test_btn.setToolTip("Выполнить инструмент с пустыми аргументами и показать вывод")
        self.test_btn.clicked.connect(self._test_run)
        buttons_row.addWidget(self.test_btn)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _fill(self):
        tool = self.tool or {}
        self.name_edit.setText(str(tool.get("name") or ""))
        index = self.kind_combo.findData(str(tool.get("kind") or "python"))
        self.kind_combo.setCurrentIndex(index if index >= 0 else 0)
        self.timeout_spin.setValue(int(tool.get("timeout_seconds")
                                       or custom_tools.DEFAULT_TIMEOUT))
        self.description_edit.setText(str(tool.get("description") or ""))
        self.body_edit.setPlainText(str(tool.get("body") or ""))
        params = tool.get("parameters") or {"type": "object", "properties": {},
                                            "required": []}
        self.params_edit.setPlainText(json.dumps(params, ensure_ascii=False, indent=2))

    def _collect(self):
        """Собрать словарь из полей (может бросить ToolError)."""
        return custom_tools.validate({
            "name": self.name_edit.text().strip(),
            "description": self.description_edit.text().strip(),
            "kind": self.kind_combo.currentData() or "python",
            "body": self.body_edit.toPlainText(),
            "parameters": self.params_edit.toPlainText(),
            "timeout_seconds": self.timeout_spin.value(),
            "enabled": bool(self.tool.get("enabled", True)),
            "created_by": self.tool.get("created_by") or "user",
        })

    def _test_run(self):
        try:
            tool = self._collect()
        except custom_tools.ToolError as error:
            QMessageBox.warning(self, "Проверка", str(error))
            return
        result = custom_tools.run_tool(tool, {}, cwd=self.project_root, allow=True,
                                       timeout=tool.get("timeout_seconds"))
        QMessageBox.information(self, "Проверка инструмента", result[:4000])

    def _on_accept(self):
        try:
            self._collect()
        except custom_tools.ToolError as error:
            QMessageBox.warning(self, "Инструмент", str(error))
            return
        self.accept()

    def get_tool(self) -> dict:
        return self._collect()


# ------------------------------------------------------------ создание с ИИ

class DesignThread(QThread):
    """Фоновая генерация инструмента моделью (UI не замирает)."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, settings, text: str, model: str = ""):
        super().__init__()
        self.settings = settings
        self.text = text
        self.model = model

    def run(self):
        try:
            self.done.emit(tool_designer.design(self.settings, self.text, self.model))
        except Exception as error:  # noqa: BLE001 - показываем пользователю
            self.failed.emit(f"{type(error).__name__}: {error}")


# ---------------------------------------------------------------- менеджер

class ToolsManagerDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None, project_root: str = "",
                 session_id: int = 0):
        super().__init__(parent)
        self.settings = settings
        self.project_root = project_root or settings.get_project_root()
        self.session_id = int(session_id or 0)
        self.tools_dir = settings.get_tools_dir()
        self.custom = custom_tools.load_tools(self.tools_dir)
        self._loading = False
        self._design_thread = None
        # Модель текущего чата (ставит главное окно); пусто = модель из настроек.
        self.current_session_model = ""
        self.setWindowTitle("Инструменты ИИ-агента")
        self.resize(760, 680)
        self._build_ui()
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "Инструменты ИИ-агента")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_builtin(), "Встроенные")
        self.tabs.addTab(self._tab_remote(), "Удалённый доступ")
        self.tabs.addTab(self._tab_custom(), "Свои инструменты")
        layout.addWidget(self.tabs, 1)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _tab_builtin(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(
            "Выключенные инструменты не отправляются модели вообще: меньше токенов "
            "в каждом запросе — меньше расход. Группа «Файлы проекта» нужна всегда.")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        self.auto_check = QCheckBox(
            "Скрывать бесполезные группы автоматически (по типу проекта)")
        self.auto_check.setToolTip(
            "В Python-проекте инструменты java_* не нужны, а их схемы уходят "
            "модели в каждом запросе. Галочка экономит токены и деньги.")
        self.auto_check.setChecked(self.settings.get_tools_auto())
        layout.addWidget(self.auto_check)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Инструмент", "Группа"])
        self.tree.setColumnWidth(0, 340)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        self.suggest_btn = QPushButton("Подсказать по проекту")
        self.suggest_btn.setToolTip(
            "Посмотреть файлы проекта и предложить отключить лишнее "
            "(например Java в Python-проекте)")
        self.suggest_btn.clicked.connect(self._suggest)
        buttons.addWidget(self.suggest_btn)
        self.all_on_btn = QPushButton("Включить все")
        self.all_on_btn.clicked.connect(self._enable_all)
        buttons.addWidget(self.all_on_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._fill_tree()
        return page

    # ------------------------------------------------------- удалённый доступ
    def _remote_store(self):
        from app.tools import remote_ui_bridge
        from app.ui.remote_dialog import remote_db_path

        return remote_ui_bridge.store_for(remote_db_path(self.settings))

    def _tab_remote(self) -> QWidget:
        """Шестерня «настройки» для терминала и хранилищ — отдельно на каждый чат."""
        from app.ui.remote_dialog import RemoteConnectionsDialog, RemoteSecretsDialog

        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(
            "Подключения к удалённому терминалу (SSH) и файловым хранилищам "
            "(SMB, FTP, SFTP, Amazon S3, WebDAV, SCP) настраиваются отдельно для "
            "КАЖДОГО чата: другой чат их не видит и не использует.\n"
            "Пароли, приватные ключи и токены в базу не попадают — они лежат в "
            "реестре Windows или в файле конфигов Linux. Удалить их можно здесь "
            "или в глобальных настройках (вкладка «Хранилище»).")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        store = self._remote_store()
        if not self.session_id:
            warn = QLabel("Нет активного чата: открой или создай чат, тогда его "
                          "подключения можно будет настроить здесь.")
            warn.setWordWrap(True)
            layout.addWidget(warn)
        else:
            flags = store.get_chat_flags(self.session_id)
            terminal_box = QGroupBox("Терминал (SSH)")
            terminal_layout = QVBoxLayout(terminal_box)
            self.terminal_check = QCheckBox("Включить терминал в этом чате")
            self.terminal_check.setChecked(bool(flags["terminal_enabled"]))
            self.terminal_check.toggled.connect(
                lambda checked: store.set_chat_flags(
                    self.session_id, terminal_enabled=checked))
            terminal_layout.addWidget(self.terminal_check)
            terminals = store.list_connections(self.session_id, "terminal")
            terminal_info = QLabel(
                f"Подключений: {len(terminals)}"
                + (" — " + ", ".join(f"{i['name']} ({i['host']})" for i in terminals)
                   if terminals else " — пока ни одного"))
            terminal_info.setWordWrap(True)
            terminal_layout.addWidget(terminal_info)
            terminal_btn = QPushButton("⚙ Настроить терминал…")
            terminal_btn.setToolTip("Адрес, порт, логин, пароль или приватный ключ")
            terminal_btn.clicked.connect(
                lambda: self._open_connections(RemoteConnectionsDialog, "terminal"))
            terminal_layout.addWidget(terminal_btn)
            terminal_open_btn = QPushButton("🖥 Открыть терминал…")
            terminal_open_btn.setToolTip(
                "Окно удалённого терминала: вводите команды и видите ответ сервера")
            terminal_open_btn.clicked.connect(self._open_terminal)
            terminal_layout.addWidget(terminal_open_btn)
            layout.addWidget(terminal_box)

            storage_box = QGroupBox("Файловые хранилища (SMB / FTP / SFTP / S3 / WebDAV / SCP)")
            storage_layout = QVBoxLayout(storage_box)
            self.storage_check = QCheckBox("Включить хранилища в этом чате")
            self.storage_check.setChecked(bool(flags["storage_enabled"]))
            self.storage_check.toggled.connect(
                lambda checked: store.set_chat_flags(
                    self.session_id, storage_enabled=checked))
            storage_layout.addWidget(self.storage_check)
            storages = store.list_connections(self.session_id, "storage")
            storage_info = QLabel(
                f"Подключений: {len(storages)}"
                + (" — " + ", ".join(f"{i['name']} ({i['protocol']})" for i in storages)
                   if storages else " — пока ни одного"))
            storage_info.setWordWrap(True)
            storage_layout.addWidget(storage_info)
            storage_btn = QPushButton("⚙ Настроить хранилища…")
            storage_btn.setToolTip("Адреса, логины, пароли, папки и ключи доступа")
            storage_btn.clicked.connect(
                lambda: self._open_connections(RemoteConnectionsDialog, "storage"))
            storage_layout.addWidget(storage_btn)
            layout.addWidget(storage_box)

        secrets_box = QGroupBox("Сохранённые пароли и ключи")
        secrets_layout = QVBoxLayout(secrets_box)
        where = QLabel(f"Место хранения: {store.secrets.describe()}")
        where.setWordWrap(True)
        secrets_layout.addWidget(where)
        secrets_btn = QPushButton("🔑 Удалить конкретные пароли / все…")
        secrets_btn.clicked.connect(
            lambda: RemoteSecretsDialog(store, self).exec_())
        secrets_layout.addWidget(secrets_btn)
        layout.addWidget(secrets_box)

        layout.addStretch(1)
        return page

    def _open_connections(self, dialog_class, kind: str):
        dialog = dialog_class(self._remote_store(), self.session_id, kind, self)
        dialog.exec_()

    def _open_terminal(self):
        """Окно удалённого терминала для текущего чата."""
        from app.ui.remote_terminal_dialog import RemoteTerminalDialog

        if not self.session_id:
            return
        RemoteTerminalDialog(self._remote_store(), self.session_id, self).exec_()

    def _tab_custom(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(
            "Свои инструменты агент видит вместе со штатными. Тело инструмента — "
            "Python-код (аргументы в ARGS) или команда оболочки с {аргументами}. "
            f"Файл: {custom_tools.store_path(self.tools_dir)}")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        self.custom_tree = QTreeWidget()
        self.custom_tree.setHeaderLabels(["Имя", "Тип", "Вкл", "Описание"])
        self.custom_tree.setColumnWidth(0, 170)
        self.custom_tree.setColumnWidth(1, 110)
        self.custom_tree.setColumnWidth(2, 50)
        self.custom_tree.itemSelectionChanged.connect(self._update_custom_actions)
        self.custom_tree.itemDoubleClicked.connect(lambda *_: self._edit_custom())
        self.custom_tree.itemChanged.connect(self._on_custom_item_changed)
        layout.addWidget(self.custom_tree, 1)

        first = QHBoxLayout()
        self.add_file_btn = QPushButton("Добавить файл…")
        self.add_file_btn.setToolTip("Импорт инструмента из JSON-файла")
        self.add_file_btn.clicked.connect(self._add_from_file)
        first.addWidget(self.add_file_btn)
        self.add_ai_btn = QPushButton("Создать с ИИ…")
        self.add_ai_btn.setToolTip(
            "Описать задачу словами — модель предложит готовый инструмент, "
            "его можно поправить перед сохранением")
        self.add_ai_btn.clicked.connect(self._add_with_ai)
        first.addWidget(self.add_ai_btn)
        self.add_empty_btn = QPushButton("Создать вручную")
        self.add_empty_btn.clicked.connect(self._add_empty)
        first.addWidget(self.add_empty_btn)
        first.addStretch(1)
        layout.addLayout(first)

        second = QHBoxLayout()
        self.edit_btn = QPushButton("Редактировать…")
        self.edit_btn.clicked.connect(self._edit_custom)
        second.addWidget(self.edit_btn)
        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.setProperty("role", "danger")
        self.delete_btn.clicked.connect(self._delete_custom)
        second.addWidget(self.delete_btn)
        self.enable_btn = QPushButton("Включить / выключить")
        self.enable_btn.clicked.connect(self._toggle_custom)
        second.addWidget(self.enable_btn)
        second.addStretch(1)
        layout.addLayout(second)

        self._fill_custom_tree()
        return page

    # ------------------------------------------------------------ встроенные
    def _fill_tree(self):
        self.tree.clear()
        disabled = self.settings.get_disabled_tools()
        groups, tools = tooling.split_disabled(disabled)
        known = {(schema.get("function") or {}).get("name") for schema in TOOLS}

        for key, label, names in tooling.GROUPS:
            group_item = QTreeWidgetItem([label, key])
            group_item.setFlags(group_item.flags() | Qt.ItemIsUserCheckable)
            essential = key in tooling.ESSENTIAL
            group_item.setCheckState(0, Qt.Unchecked if key in groups else Qt.Checked)
            if essential:
                group_item.setDisabled(True)
                group_item.setText(1, key + " (обязательно)")
            self.tree.addTopLevelItem(group_item)
            for name in names:
                if name not in known:
                    continue
                child = QTreeWidgetItem([name, ""])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked if name in tools else Qt.Checked)
                group_item.addChild(child)
            group_item.setExpanded(False)

    def _suggest(self):
        path = self.project_root
        if not path:
            path = QFileDialog.getExistingDirectory(self, "Папка проекта")
        if not path:
            return
        suggested = tooling.suggest_for_project(path)
        if not suggested:
            QMessageBox.information(
                self, "Инструменты",
                "Проект выглядит смешанным или пустым — отключать нечего.")
            return
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.text(1).split()[0] in suggested:
                item.setCheckState(0, Qt.Unchecked)
        names = ", ".join(tooling.group_label(key) for key in suggested)
        QMessageBox.information(self, "Инструменты",
                                f"Предлагаю выключить: {names}.\nНажми «Сохранить».")

    def _enable_all(self):
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            item.setCheckState(0, Qt.Checked)
            for child_index in range(item.childCount()):
                item.child(child_index).setCheckState(0, Qt.Checked)

    # ------------------------------------------------------------------ свои
    def _fill_custom_tree(self, select: str = ""):
        self._loading = True
        try:
            self.custom_tree.clear()
            for tool in self.custom:
                item = QTreeWidgetItem([
                    tool.get("name", ""),
                    custom_tools.KIND_LABELS.get(tool.get("kind"), tool.get("kind", "")),
                    "",
                    str(tool.get("description") or "")[:120],
                ])
                item.setData(0, Qt.UserRole, tool.get("name"))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(2, Qt.Checked if tool.get("enabled", True)
                                   else Qt.Unchecked)
                self.custom_tree.addTopLevelItem(item)
                if select and tool.get("name") == select:
                    item.setSelected(True)
        finally:
            self._loading = False
        self._update_custom_actions()

    def _update_custom_actions(self):
        has_selection = self.custom_tree.currentItem() is not None
        for button in (getattr(self, "edit_btn", None), getattr(self, "delete_btn", None),
                       getattr(self, "enable_btn", None)):
            if button is not None:
                button.setEnabled(has_selection)

    def _selected_name(self) -> str:
        item = self.custom_tree.currentItem()
        return str(item.data(0, Qt.UserRole) or "") if item else ""

    def _on_custom_item_changed(self, item, column):
        if self._loading or column != 2:
            return
        name = str(item.data(0, Qt.UserRole) or "")
        tool = custom_tools.find_tool(self.custom, name)
        if tool is None:
            return
        tool["enabled"] = item.checkState(2) == Qt.Checked
        self._save_custom()

    def _save_custom(self):
        try:
            custom_tools.save_tools(self.tools_dir, self.custom)
            self.custom = custom_tools.load_tools(self.tools_dir)
            return True
        except OSError as error:
            QMessageBox.warning(self, "Инструменты",
                                f"Не удалось сохранить инструменты:\n{error}")
            return False

    def _add_empty(self):
        dialog = CustomToolDialog(None, self, self.project_root)
        if not dialog.exec_():
            return
        self._store_tool(dialog.get_tool())

    def _add_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Файл с инструментом", "",
            "Описание инструмента (*.json);;Все файлы (*)")
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "Инструменты", f"Не получилось прочитать JSON:\n{error}")
            return
        if isinstance(raw, dict) and isinstance(raw.get("tools"), list):
            items = raw["tools"]
        elif isinstance(raw, list):
            items = raw
        else:
            items = [raw]
        added, errors = [], []
        for item in items:
            try:
                tool = custom_tools.validate(item)
            except custom_tools.ToolError as error:
                errors.append(str(error))
                continue
            if custom_tools.find_tool(self.custom, tool["name"]):
                errors.append(f"{tool['name']}: такой инструмент уже есть")
                continue
            self.custom.append(tool)
            added.append(tool["name"])
        if self._save_custom():
            self._fill_custom_tree(select=added[-1] if added else "")
        message = []
        if added:
            message.append("Добавлено: " + ", ".join(added))
        if errors:
            message.append("Пропущено:\n- " + "\n- ".join(errors))
        QMessageBox.information(self, "Инструменты", "\n\n".join(message) or "Нечего добавить")

    def _add_with_ai(self):
        text, ok = QInputDialog.getMultiLineText(
            self, "Создать инструмент с ИИ",
            "Что должен делать инструмент?\n"
            "(например: «покажи последние 50 строк logs/latest.log»)")
        if not ok or not text.strip():
            return
        model = ""
        if self.current_session_model:
            model = self.current_session_model
        self.add_ai_btn.setEnabled(False)
        self.add_ai_btn.setText("Генерирую…")
        self._design_thread = DesignThread(self.settings, text.strip(), model)
        self._design_thread.done.connect(self._on_designed)
        self._design_thread.failed.connect(self._on_design_failed)
        from app.ui import threads
        threads.track(self._design_thread)
        self._design_thread.start()

    def _reset_design_button(self):
        try:
            self.add_ai_btn.setEnabled(True)
            self.add_ai_btn.setText("Создать с ИИ…")
        except RuntimeError:
            pass

    def _on_designed(self, data):
        self._reset_design_button()
        tool = dict(data or {})
        tool.setdefault("created_by", "ai")
        dialog = CustomToolDialog(tool, self, self.project_root)
        QMessageBox.information(
            self, "Инструмент от ИИ",
            "Модель предложила инструмент. Проверь код и описание — "
            "сохраняется только то, что подтвердишь.")
        if dialog.exec_():
            self._store_tool(dialog.get_tool())

    def _on_design_failed(self, message):
        self._reset_design_button()
        QMessageBox.warning(self, "Создать с ИИ",
                            f"Модель не ответила:\n{message}\n\n"
                            "Проверь провайдера и модель в настройках чата.")

    def _store_tool(self, tool: dict):
        existing = custom_tools.find_tool(self.custom, tool["name"])
        if existing is not None:
            reply = QMessageBox.question(
                self, "Инструменты",
                f"Инструмент {tool['name']} уже есть. Заменить?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            self.custom[self.custom.index(existing)] = tool
        else:
            self.custom.append(tool)
        if self._save_custom():
            self._fill_custom_tree(select=tool["name"])

    def _edit_custom(self):
        name = self._selected_name()
        tool = custom_tools.find_tool(self.custom, name)
        if tool is None:
            return
        dialog = CustomToolDialog(tool, self, self.project_root)
        if dialog.exec_():
            self._store_tool(dialog.get_tool())

    def _delete_custom(self):
        name = self._selected_name()
        if not name:
            return
        reply = QMessageBox.question(
            self, "Удалить инструмент",
            f"Удалить инструмент «{name}»?\n\n"
            f"Копия прошлого списка останется в data/{custom_tools.BACKUP_NAME}.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.custom = [tool for tool in self.custom
                       if str(tool.get("name")) != name]
        if self._save_custom():
            self._fill_custom_tree()

    def _toggle_custom(self):
        name = self._selected_name()
        tool = custom_tools.find_tool(self.custom, name)
        if tool is None:
            return
        tool["enabled"] = not bool(tool.get("enabled", True))
        if self._save_custom():
            self._fill_custom_tree(select=name)

    # ------------------------------------------------------------------ итог
    def done(self, result):
        """Перед закрытием гасим фоновые потоки: иначе Qt делает abort()."""
        from app.ui import threads
        threads.stop_for(self)
        super().done(result)

    def accept(self):
        disabled = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            key = item.text(1).split()[0]
            group_off = item.checkState(0) == Qt.Unchecked
            if group_off and key not in tooling.ESSENTIAL:
                disabled.append(key)
                continue
            if group_off:
                continue
            for child_index in range(item.childCount()):
                child = item.child(child_index)
                if child.checkState(0) == Qt.Unchecked:
                    disabled.append(child.text(0))
        self.settings.set_disabled_tools(disabled)
        self.settings.set_tools_auto(self.auto_check.isChecked())
        super().accept()
