"""Окна удалённого доступа: профили подключений и управление паролями.

Здесь три диалога:
  RemoteConnectionEditor   — форма одного подключения (адрес, логин, пароль…);
  RemoteConnectionsDialog  — список подключений ОДНОГО чата + галочка «включено»;
  RemoteSecretsDialog      — глобальное управление сохранёнными паролями/ключами
                             (удалить один, все или «осиротевшие»).

Пароли в форму возвращаются только при явном нажатии «Показать»: в списках и
подсказках они всегда скрыты.
"""

import os

from app.qt_compat.QtCore import Qt, QThread, QTimer, pyqtSignal
from app.qt_compat.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.config.remote_store import (
    PROTOCOL_LABELS, default_port, protocols_for,
)
from app.tools import remote_ui_bridge


def _frame(dialog, title: str):
    """Единое оформление окна (как в остальных диалогах программы)."""
    try:
        from app.ui.title_bar import apply_dialog_frame

        apply_dialog_frame(dialog, title)
    except Exception:  # noqa: BLE001 - без темы окно всё равно работает
        dialog.setWindowTitle(title)


class _TestThread(QThread):
    """Проверка подключения в фоне, чтобы окно не подвисало на таймауте."""

    done = pyqtSignal(str)

    def __init__(self, store, profile: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.profile = dict(profile or {})

    def run(self):
        try:
            result = remote_ui_bridge.test_connection(self.store, self.profile)
        except Exception as exc:  # noqa: BLE001
            result = f"Ошибка: {type(exc).__name__}: {exc}"
        self.done.emit(result)


class RemoteConnectionEditor(QDialog):
    """Форма одного подключения. Секреты сразу уходят в реестр/файл конфигов."""

    def __init__(self, store, session_id: int, kind: str, profile: dict = None,
                 parent=None):
        super().__init__(parent)
        self.store = store
        self.session_id = int(session_id or 0)
        self.kind = "terminal" if kind == "terminal" else "storage"
        self.profile = dict(profile or {})
        self.resize(560, 620)
        self._build()
        self._load()
        # Рамку ставим ПОСЛЕ сборки окна: apply_dialog_frame оборачивает диалог
        # в свой layout, и если вызвать его раньше, наш layout уже не
        # установится — окно окажется пустым (только полоса заголовка).
        _frame(self, "Подключение" if profile else "Новое подключение")

    # ------------------------------------------------------------------ UI
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("например: прод-сервер")
        form.addRow("Название:", self.name_edit)

        self.protocol_combo = QComboBox()
        for protocol in protocols_for(self.kind):
            self.protocol_combo.addItem(PROTOCOL_LABELS.get(protocol, protocol), protocol)
        self.protocol_combo.currentIndexChanged.connect(self._sync_fields)
        form.addRow("Протокол:", self.protocol_combo)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("192.168.1.10 или server.example.com")
        form.addRow("Адрес (IP/хост):", self.host_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setSpecialValueText("по умолчанию")
        form.addRow("Порт:", self.port_spin)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("логин на сервере")
        form.addRow("Логин:", self.user_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("пароль (хранится вне проекта)")
        self.show_password = QCheckBox("Показать")
        self.show_password.toggled.connect(
            lambda checked: self.password_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password))
        pass_row = QWidget()
        pass_layout = QHBoxLayout(pass_row)
        pass_layout.setContentsMargins(0, 0, 0, 0)
        pass_layout.addWidget(self.password_edit, 1)
        pass_layout.addWidget(self.show_password)
        form.addRow("Пароль:", pass_row)
        layout.addLayout(form)

        # --- приватный ключ (SSH/SFTP/SCP)
        self.key_group = QGroupBox("Приватный ключ (вместо пароля)")
        key_layout = QVBoxLayout(self.key_group)
        self.key_edit = QPlainTextEdit()
        self.key_edit.setPlaceholderText(
            "-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----")
        self.key_edit.setFixedHeight(110)
        key_layout.addWidget(self.key_edit)
        self.passphrase_edit = QLineEdit()
        self.passphrase_edit.setEchoMode(QLineEdit.Password)
        self.passphrase_edit.setPlaceholderText("пароль ключа, если он есть")
        key_layout.addWidget(self.passphrase_edit)
        layout.addWidget(self.key_group)

        # --- SMB
        self.smb_group = QGroupBox("SMB")
        smb_form = QFormLayout(self.smb_group)
        self.share_edit = QLineEdit()
        self.share_edit.setPlaceholderText("имя общей папки (share)")
        smb_form.addRow("Общая папка:", self.share_edit)
        layout.addWidget(self.smb_group)

        # --- S3
        self.s3_group = QGroupBox("Amazon S3 / совместимое хранилище")
        s3_form = QFormLayout(self.s3_group)
        self.bucket_edit = QLineEdit()
        self.bucket_edit.setPlaceholderText("имя bucket")
        s3_form.addRow("Bucket:", self.bucket_edit)
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setPlaceholderText("https://s3.amazonaws.com (пусто = AWS)")
        s3_form.addRow("Endpoint:", self.endpoint_edit)
        self.region_edit = QLineEdit()
        self.region_edit.setPlaceholderText("us-east-1")
        s3_form.addRow("Регион:", self.region_edit)
        self.access_edit = QLineEdit()
        self.access_edit.setPlaceholderText("access key id")
        s3_form.addRow("Access key:", self.access_edit)
        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.Password)
        self.secret_edit.setPlaceholderText("secret access key")
        s3_form.addRow("Secret key:", self.secret_edit)
        layout.addWidget(self.s3_group)

        # --- WebDAV
        self.dav_group = QGroupBox("WebDAV")
        dav_form = QFormLayout(self.dav_group)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://cloud.example.com/remote.php/dav/files/user")
        dav_form.addRow("URL:", self.url_edit)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("токен (если вход по токену, а не по паролю)")
        dav_form.addRow("Токен:", self.token_edit)
        self.dav_insecure = QCheckBox("Не проверять сертификат (самоподписанный)")
        dav_form.addRow("", self.dav_insecure)
        layout.addWidget(self.dav_group)

        # --- общие опции
        extra_group = QGroupBox("Ещё")
        extra_form = QFormLayout(extra_group)
        self.root_edit = QLineEdit()
        self.root_edit.setPlaceholderText("папка внутри хранилища, необязательно")
        extra_form.addRow("Корневая папка:", self.root_edit)
        self.tls_check = QCheckBox("FTP через TLS (FTPS)")
        extra_form.addRow("", self.tls_check)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(3, 300)
        self.timeout_spin.setSuffix(" с")
        extra_form.addRow("Таймаут:", self.timeout_spin)
        self.clear_secret_check = QCheckBox(
            "Удалить сохранённые пароли/ключи этого подключения")
        extra_form.addRow("", self.clear_secret_check)
        layout.addWidget(extra_group)

        self.state_label = QLabel("")
        self.state_label.setObjectName("mutedLabel")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        buttons = QDialogButtonBox()
        self.test_btn = QPushButton("Проверить подключение")
        self.test_btn.clicked.connect(self._test)
        buttons.addButton(self.test_btn, QDialogButtonBox.ActionRole)
        buttons.addButton(QDialogButtonBox.Save)
        buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -------------------------------------------------------------- данные
    def _protocol(self) -> str:
        return str(self.protocol_combo.currentData() or "ssh")

    def _sync_fields(self):
        protocol = self._protocol()
        previous = getattr(self, "_last_protocol", None)
        self._last_protocol = protocol
        ssh = protocol in ("ssh", "sftp", "scp")
        self.key_group.setVisible(ssh)
        self.smb_group.setVisible(protocol == "smb")
        self.s3_group.setVisible(protocol == "s3")
        self.dav_group.setVisible(protocol == "webdav")
        self.tls_check.setVisible(protocol == "ftp")
        self.root_edit.setVisible(protocol in ("sftp", "scp", "ftp", "smb", "s3", "webdav"))
        # Порт подставляем по протоколу, но только если пользователь его не менял
        # вручную (то есть там ещё прежнее значение по умолчанию или пусто).
        value = self.port_spin.value()
        if previous is None:
            if not value:
                self.port_spin.setValue(default_port(protocol))
        elif value in (0, default_port(previous)):
            self.port_spin.setValue(default_port(protocol))

    def _load(self):
        data = self.profile
        protocol = str(data.get("protocol") or protocols_for(self.kind)[0])
        index = self.protocol_combo.findData(protocol)
        self.protocol_combo.setCurrentIndex(max(0, index))
        self.name_edit.setText(str(data.get("name") or ""))
        self.host_edit.setText(str(data.get("host") or ""))
        self.port_spin.setValue(int(data.get("port") or default_port(protocol)))
        self.user_edit.setText(str(data.get("username") or ""))
        options = dict(data.get("options") or {})
        self.share_edit.setText(str(options.get("share") or ""))
        self.bucket_edit.setText(str(options.get("bucket") or ""))
        self.endpoint_edit.setText(str(options.get("endpoint") or ""))
        self.region_edit.setText(str(options.get("region") or ""))
        self.url_edit.setText(str(options.get("url") or ""))
        self.root_edit.setText(str(options.get("root") or ""))
        self.tls_check.setChecked(bool(options.get("tls")))
        self.dav_insecure.setChecked(bool(options.get("insecure")))
        self.timeout_spin.setValue(int(data.get("timeout") or 20))

        conn_id = int(data.get("id") or 0)
        if conn_id:
            secrets = self.store.credentials(conn_id)
            self.password_edit.setPlaceholderText(
                "пароль сохранён — введите новый, чтобы заменить" if secrets.get("password")
                else "пароль не задан")
            self.passphrase_edit.setPlaceholderText(
                "пароль ключа сохранён" if secrets.get("passphrase") else "пароль ключа, если есть")
            self.access_edit.setText(secrets.get("access_key") or "")
            if secrets.get("secret_key"):
                self.secret_edit.setPlaceholderText("secret key сохранён — введите новый")
            if secrets.get("token"):
                self.token_edit.setPlaceholderText("токен сохранён — введите новый")
            if secrets.get("private_key"):
                self.key_edit.setPlaceholderText(
                    "приватный ключ сохранён (сотрите поле и сохраните, чтобы удалить)")
        else:
            self.access_edit.setText("")
        self.state_label.setText(
            "Пароли хранятся " + self.store.secrets.describe() +
            "; в базу чатов они не попадают.")
        self._sync_fields()

    def _save(self):
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "Подключение", "Укажите адрес сервера (IP или имя).")
            return
        protocol = self._protocol()
        options = {}
        if protocol == "smb":
            options["share"] = self.share_edit.text().strip()
            if not options["share"]:
                QMessageBox.warning(self, "Подключение",
                                    "Для SMB нужно имя общей папки (share).")
                return
        if protocol == "s3":
            options["bucket"] = self.bucket_edit.text().strip()
            options["endpoint"] = self.endpoint_edit.text().strip()
            options["region"] = self.region_edit.text().strip() or "us-east-1"
            if not options["bucket"]:
                QMessageBox.warning(self, "Подключение", "Для S3 нужен bucket.")
                return
        if protocol == "webdav":
            options["url"] = self.url_edit.text().strip()
            options["insecure"] = self.dav_insecure.isChecked()
        if protocol in ("sftp", "scp", "ftp", "smb", "s3", "webdav"):
            options["root"] = self.root_edit.text().strip()
        if protocol == "ftp":
            options["tls"] = self.tls_check.isChecked()

        secrets = {}
        if self.clear_secret_check.isChecked():
            secrets = {field: "" for field in ("password", "private_key", "passphrase",
                                               "access_key", "secret_key", "token")}
        if self.password_edit.text():
            secrets["password"] = self.password_edit.text()
        if self.passphrase_edit.text():
            secrets["passphrase"] = self.passphrase_edit.text()
        if self.key_edit.toPlainText().strip():
            secrets["private_key"] = self.key_edit.toPlainText().strip()
        if self.access_edit.text().strip():
            secrets["access_key"] = self.access_edit.text().strip()
        if self.secret_edit.text():
            secrets["secret_key"] = self.secret_edit.text()
        if self.token_edit.text():
            secrets["token"] = self.token_edit.text()

        payload = {
            "id": int(self.profile.get("id") or 0),
            "session_id": self.session_id,
            "kind": self.kind,
            "name": self.name_edit.text().strip(),
            "protocol": protocol,
            "host": host,
            "port": int(self.port_spin.value() or default_port(protocol)),
            "username": self.user_edit.text().strip(),
            "options": options,
            "timeout": int(self.timeout_spin.value()),
            "secrets": secrets,
        }
        try:
            self.conn_id = self.store.save_connection(payload)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Подключение", f"Не удалось сохранить: {exc}")
            return
        self.accept()

    # --------------------------------------------------------------- тест
    def _test(self):
        conn_id = int(self.profile.get("id") or 0)
        if not conn_id:
            QMessageBox.information(
                self, "Проверка",
                "Сначала сохраните подключение (кнопка «Save»), затем проверяйте.")
            return
        profile = self.store.get_connection(conn_id)
        if not profile:
            QMessageBox.warning(self, "Проверка", "Подключение не найдено в базе.")
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Проверяю…")
        self._thread = _TestThread(self.store, profile, self)
        self._thread.done.connect(self._on_test_done)
        self._thread.start()

    def _on_test_done(self, text: str):
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Проверить подключение")
        self.state_label.setText(text)


class RemoteConnectionsDialog(QDialog):
    """Подключения одного чата: список, добавление, правка, проверка."""

    def __init__(self, store, session_id: int, kind: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.session_id = int(session_id or 0)
        self.kind = "terminal" if kind == "terminal" else "storage"
        title = ("Удалённый терминал (SSH)" if self.kind == "terminal"
                 else "Удалённые хранилища")
        self.setWindowTitle(title)
        self.resize(760, 480)
        self._build()
        self._reload()
        # Рамка — строго после сборки UI (см. комментарий в RemoteConnectionEditor).
        _frame(self, title)
        # Если подключений ещё нет, сразу открываем форму нового: человек
        # ожидает увидеть поля адреса/логина/пароля, а не пустой список.
        self._auto_add = not self.store.list_connections(self.session_id, self.kind)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_auto_add", False):
            self._auto_add = False
            QTimer.singleShot(0, self._add)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.session_label = QLabel()
        self.session_label.setWordWrap(True)
        self.session_label.setObjectName("mutedLabel")
        layout.addWidget(self.session_label)

        self.enable_check = QCheckBox(
            "Включить терминал в этом чате" if self.kind == "terminal"
            else "Включить хранилища в этом чате")
        self.enable_check.setToolTip(
            "Пока галочка снята, инструменты удалённого доступа этого вида не "
            "отправляются модели в этом чате — экономия токенов.")
        self.enable_check.toggled.connect(self._on_enable)
        layout.addWidget(self.enable_check)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(lambda *_: self._edit())
        layout.addWidget(self.list_widget, 1)

        row1 = QHBoxLayout()
        add_btn = QPushButton("Добавить…")
        add_btn.clicked.connect(self._add)
        row1.addWidget(add_btn)
        self.edit_btn = QPushButton("Изменить…")
        self.edit_btn.clicked.connect(self._edit)
        row1.addWidget(self.edit_btn)
        self.test_btn = QPushButton("Проверить")
        self.test_btn.clicked.connect(self._test)
        row1.addWidget(self.test_btn)
        self.del_btn = QPushButton("Удалить")
        self.del_btn.setProperty("role", "danger")
        self.del_btn.clicked.connect(self._delete)
        row1.addWidget(self.del_btn)
        row1.addStretch(1)
        layout.addLayout(row1)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setObjectName("mutedLabel")
        layout.addWidget(self.info_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    # -------------------------------------------------------------- данные
    def _reload(self):
        flags = self.store.get_chat_flags(self.session_id)
        enabled = flags["terminal_enabled"] if self.kind == "terminal" else flags["storage_enabled"]
        self.enable_check.blockSignals(True)
        self.enable_check.setChecked(bool(enabled))
        self.enable_check.blockSignals(False)
        self.session_label.setText(
            f"Чат #{self.session_id}: здесь хранятся только его подключения — "
            "другие чаты их не видят. Пароли лежат " + self.store.secrets.describe() + ".")
        self.list_widget.clear()
        items = self.store.list_connections(self.session_id, self.kind)
        for item in items:
            address = f"{item['host']}:{item['port']}"
            text = (f"{item['name']} — {PROTOCOL_LABELS.get(item['protocol'], item['protocol'])}  "
                    f"{address}  [{item['username'] or 'без логина'}]  "
                    f"секреты: {item['secret_mask']}")
            entry = QListWidgetItem(text)
            entry.setData(Qt.UserRole, int(item["id"]))
            self.list_widget.addItem(entry)
        if not items:
            self.list_widget.addItem(QListWidgetItem(
                "Подключений пока нет — нажмите «Добавить…», откроется форма: "
                "адрес (IP или имя), порт, логин, пароль."))
        self.info_label.setText(
            "Проверка идёт по сохранённым данным. Пустое поле пароля = оставить прежний.")

    def _current_id(self) -> int:
        item = self.list_widget.currentItem()
        if item is None:
            return 0
        return int(item.data(Qt.UserRole) or 0)

    def _on_enable(self, checked: bool):
        if self.kind == "terminal":
            self.store.set_chat_flags(self.session_id, terminal_enabled=checked)
        else:
            self.store.set_chat_flags(self.session_id, storage_enabled=checked)

    def _add(self):
        editor = RemoteConnectionEditor(self.store, self.session_id, self.kind, None, self)
        if editor.exec_():
            self._reload()

    def _edit(self):
        conn_id = self._current_id()
        if not conn_id:
            return
        profile = self.store.get_connection(conn_id)
        if not profile:
            QMessageBox.warning(self, "Подключение", "Запись не найдена — обновите список.")
            return
        editor = RemoteConnectionEditor(self.store, self.session_id, self.kind, profile, self)
        if editor.exec_():
            self._reload()

    def _delete(self):
        conn_id = self._current_id()
        if not conn_id:
            return
        profile = self.store.get_connection(conn_id)
        name = profile["name"] if profile else f"#{conn_id}"
        if QMessageBox.question(
                self, "Удалить подключение",
                f"Удалить «{name}» вместе с сохранёнными паролями этого подключения?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.store.delete_connection(conn_id)
        self._reload()

    def _test(self):
        conn_id = self._current_id()
        if not conn_id:
            return
        profile = self.store.get_connection(conn_id)
        if not profile:
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Проверяю…")
        self._thread = _TestThread(self.store, profile, self)
        self._thread.done.connect(self._on_test_done)
        self._thread.start()

    def _on_test_done(self, text: str):
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Проверить")
        self.info_label.setText(text)


class RemoteSecretsDialog(QDialog):
    """Глобальные настройки: удалить конкретные пароли/ключи или все сразу."""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.where_label = QLabel()
        self.where_label.setWordWrap(True)
        layout.addWidget(self.where_label)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        row = QHBoxLayout()
        refresh_btn = QPushButton("Обновить")
        refresh_btn.clicked.connect(self._reload)
        row.addWidget(refresh_btn)
        delete_btn = QPushButton("Удалить выбранный")
        delete_btn.clicked.connect(self._delete_one)
        row.addWidget(delete_btn)
        orphans_btn = QPushButton("Удалить «осиротевшие»")
        orphans_btn.setToolTip("Секреты от подключений, которых уже нет в базе чатов")
        orphans_btn.clicked.connect(self._delete_orphans)
        row.addWidget(orphans_btn)
        self.delete_all_btn = QPushButton("Удалить ВСЕ пароли и ключи")
        self.delete_all_btn.setProperty("role", "danger")
        self.delete_all_btn.clicked.connect(self._delete_all)
        row.addWidget(self.delete_all_btn)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self._reload()
        # Рамка — после сборки (иначе layout не установится и окно будет пустым).
        _frame(self, "Сохранённые пароли удалённых подключений")

    def _reload(self):
        self.where_label.setText(
            "Место хранения: " + self.store.secrets.describe() +
            "\nЗдесь только пароли, приватные ключи и токены удалённых подключений. "
            "API-ключи провайдеров и токен GitHub лежат отдельно (вкладка «Хранилище»).")
        self.list_widget.clear()
        items = self.store.list_secrets()
        for item in items:
            entry = QListWidgetItem(item["label"])
            entry.setData(Qt.UserRole, item["name"])
            if item.get("orphan"):
                entry.setText(item["label"] + "  — можно удалить")
            self.list_widget.addItem(entry)
        if not items:
            self.list_widget.addItem(QListWidgetItem("Сохранённых паролей нет."))

    def _selected_name(self) -> str:
        item = self.list_widget.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.UserRole) or "")

    def _delete_one(self):
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, "Пароли", "Выберите строку в списке.")
            return
        if QMessageBox.question(
                self, "Удалить пароль",
                "Удалить этот сохранённый пароль/ключ? Подключение останется, но "
                "потребует ввести пароль заново.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.store.delete_secret(name)
        self._reload()

    def _delete_orphans(self):
        removed = self.store.delete_orphan_secrets()
        self._reload()
        QMessageBox.information(self, "Пароли", f"Удалено записей: {removed}")

    def _delete_all(self):
        total = self.store.secrets.count()
        if not total:
            QMessageBox.information(self, "Пароли", "Удалять нечего.")
            return
        if QMessageBox.question(
                self, "Удалить все пароли",
                f"Удалить ВСЕ сохранённые пароли и ключи удалённых подключений ({total})?\n"
                "Придётся вводить их заново. Действие необратимо.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        removed = self.store.delete_all_secrets()
        self._reload()
        QMessageBox.information(self, "Пароли", f"Удалено записей: {removed}")


def remote_db_path(settings) -> str:
    """Путь к базе чатов (в ней же лежат профили подключений)."""
    base = getattr(settings, "project_dir", "") or os.getcwd()
    return os.path.join(base, "data", "chats.db")
