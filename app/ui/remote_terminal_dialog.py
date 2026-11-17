"""Окно удалённого терминала: печатаешь команды — видишь ответ сервера.

Подключается по SSH к профилю ЭТОГО чата (данные берутся оттуда же, куда их
вводят в «Инструменты → Удалённый доступ» и в глобальных настройках на вкладке
«Удалённый доступ»). Сессия живёт в памяти процесса на пару (чат, подключение),
поэтому окно и инструменты агента remote_terminal_* работают с одним и тем же
shell: текущая папка и переменные окружения общие.

Логин и пароль вводятся один раз в настройках подключения и дальше подставляются
автоматически (лежат в реестре Windows / файле конфигов Linux, не в базе чатов).
"""

from app.qt_compat.QtCore import Qt, QThread, pyqtSignal
from app.qt_compat.QtGui import QFont, QTextCursor
from app.qt_compat.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from app.config.remote_store import PROTOCOL_LABELS
from app.tools import remote_terminal
from app.tools.remote_common import RemoteError


def _frame(dialog, title: str):
    try:
        from app.ui.title_bar import apply_dialog_frame

        apply_dialog_frame(dialog, title)
    except Exception:  # noqa: BLE001 - без темы окно всё равно работает
        dialog.setWindowTitle(title)


class _OpenThread(QThread):
    """Подключение в фоне: на медленном сервере окно не подвисает."""

    ok = pyqtSignal(str, str)      # подпись соединения, текущая папка
    failed = pyqtSignal(str)

    def __init__(self, store, session_id: int, profile: dict, parent=None):
        super().__init__(parent)
        self.store = store
        self.session_id = int(session_id or 0)
        self.profile = dict(profile or {})

    def run(self):
        try:
            conn_id = int(self.profile.get("id") or 0)
            credentials = self.store.credentials(conn_id)
            session = remote_terminal.open_session(
                self.session_id, conn_id, self.profile, credentials,
                timeout=float(self.profile.get("timeout") or 20))
            cwd = ""
            try:
                result = session.run("pwd", timeout=10)
                lines = [line.strip() for line in
                         str(result.get("stdout") or "").splitlines() if line.strip()]
                cwd = lines[-1] if lines else ""
            except Exception:  # noqa: BLE001 - pwd не критичен
                cwd = ""
            self.ok.emit(session.describe(), cwd)
        except RemoteError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class _CommandThread(QThread):
    """Одна команда в фоне: оболочка может думать сколько угодно."""

    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, session, command: str, timeout: float, parent=None):
        super().__init__(parent)
        self.session = session
        self.command = str(command or "")
        self.timeout = float(timeout or 60.0)

    def run(self):
        try:
            self.done.emit(self.session.run(self.command, timeout=self.timeout))
        except RemoteError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class _CommandInput(QLineEdit):
    """Строка ввода с историей команд (стрелки вверх/вниз)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = []
        self._index = 0

    def remember(self, command: str):
        command = str(command or "")
        if not command:
            return
        if not self.history or self.history[-1] != command:
            self.history.append(command)
        if len(self.history) > 300:
            del self.history[0]
        self._index = len(self.history)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Up:
            if self.history:
                self._index = max(0, self._index - 1)
                self.setText(self.history[self._index])
            return
        if key == Qt.Key_Down:
            if self.history:
                self._index = min(len(self.history), self._index + 1)
                self.setText(self.history[self._index]
                             if self._index < len(self.history) else "")
            return
        super().keyPressEvent(event)


class RemoteTerminalDialog(QDialog):
    """Интерактивный SSH-терминал одного чата."""

    def __init__(self, store, session_id: int, parent=None):
        super().__init__(parent)
        self.store = store
        self.session_id = int(session_id or 0)
        self.session = None
        self.prompt = "$ "
        self._open_thread = None
        self._cmd_thread = None
        self._pending = ""
        self._busy = False
        self.resize(900, 580)
        self._build()
        self._reload_connections()
        # Рамку ставим после сборки окна: apply_dialog_frame оборачивает диалог
        # в свой layout, и при вызове раньше наш layout не установится —
        # окно осталось бы пустым.
        _frame(self, "Удалённый терминал (SSH)")

    # ------------------------------------------------------------------ UI
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Подключение:"))
        self.conn_combo = QComboBox()
        self.conn_combo.setMinimumWidth(280)
        self.conn_combo.currentIndexChanged.connect(self._on_connection_changed)
        top.addWidget(self.conn_combo, 1)
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setToolTip("Обновить список подключений этого чата")
        self.refresh_btn.clicked.connect(self._reload_connections)
        top.addWidget(self.refresh_btn)
        self.add_btn = QPushButton("Добавить…")
        self.add_btn.setToolTip("Новое SSH-подключение: адрес, логин, пароль или ключ")
        self.add_btn.clicked.connect(self._add_connection)
        top.addWidget(self.add_btn)
        self.edit_btn = QPushButton("Изменить…")
        self.edit_btn.setToolTip("Адрес, порт, логин, пароль или приватный ключ")
        self.edit_btn.clicked.connect(self._edit_connection)
        top.addWidget(self.edit_btn)
        layout.addLayout(top)

        row = QHBoxLayout()
        self.connect_btn = QPushButton("Подключиться")
        self.connect_btn.clicked.connect(self._connect)
        row.addWidget(self.connect_btn)
        self.disconnect_btn = QPushButton("Отключиться")
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.disconnect_btn.setEnabled(False)
        row.addWidget(self.disconnect_btn)
        self.interrupt_btn = QPushButton("⛔ Ctrl+C")
        self.interrupt_btn.setToolTip("Прервать выполняющуюся команду на сервере")
        self.interrupt_btn.clicked.connect(self._interrupt)
        self.interrupt_btn.setEnabled(False)
        row.addWidget(self.interrupt_btn)
        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.clicked.connect(lambda: self.output.clear())
        row.addWidget(self.clear_btn)
        self.copy_btn = QPushButton("Копировать вывод")
        self.copy_btn.clicked.connect(self._copy_output)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        self.status_label = QLabel("не подключено")
        self.status_label.setObjectName("mutedLabel")
        row.addWidget(self.status_label)
        layout.addLayout(row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(10)
        self.output.setFont(font)
        self.output.setPlaceholderText(
            "Здесь будет вывод команд. Выберите подключение и нажмите "
            "«Подключиться» (или просто введите команду — подключится само).")
        layout.addWidget(self.output, 1)

        bottom = QHBoxLayout()
        self.input = _CommandInput()
        self.input.setFont(font)
        self.input.setPlaceholderText(
            "команда (Enter — выполнить, ↑/↓ — история, Ctrl+L — очистить)")
        self.input.returnPressed.connect(self._send)
        bottom.addWidget(self.input, 1)
        self.send_btn = QPushButton("Выполнить")
        self.send_btn.clicked.connect(self._send)
        bottom.addWidget(self.send_btn)
        layout.addLayout(bottom)

        self.input.setFocus()

    # ------------------------------------------------------------- профили
    def _reload_connections(self):
        current = self.conn_combo.currentData()
        self.conn_combo.blockSignals(True)
        self.conn_combo.clear()
        try:
            items = self.store.list_connections(self.session_id, "terminal")
        except Exception as error:  # noqa: BLE001
            items = []
            self._write(f"[ошибка] не удалось прочитать подключения: {error}")
        for item in items:
            label = (f"{item['name']} — {PROTOCOL_LABELS.get(item['protocol'], item['protocol'])} "
                     f"{item['host']}:{item['port']}")
            self.conn_combo.addItem(label, int(item["id"]))
        restored = False
        if current:
            index = self.conn_combo.findData(int(current))
            if index >= 0:
                self.conn_combo.setCurrentIndex(index)
                restored = True
        self.conn_combo.blockSignals(False)
        if not items:
            self.status_label.setText("нет подключений — нажмите «Добавить…»")
        elif not restored:
            self.status_label.setText("не подключено")
        if not items:
            self.prompt = "$ "

    def _current_profile(self):
        conn_id = int(self.conn_combo.currentData() or 0)
        if not conn_id:
            return None
        try:
            return self.store.get_connection(conn_id)
        except Exception:  # noqa: BLE001
            return None

    def _on_connection_changed(self, *_):
        if self.session is not None:
            self._disconnect()
        self.status_label.setText("не подключено")

    def _add_connection(self):
        from app.ui.remote_dialog import RemoteConnectionEditor

        if not self.session_id:
            QMessageBox.information(
                self, "Терминал",
                "Нет активного чата: подключения хранятся отдельно для каждого "
                "чата, поэтому сначала открой или создай чат.")
            return
        editor = RemoteConnectionEditor(self.store, self.session_id, "terminal",
                                        None, self)
        if editor.exec_():
            self._reload_connections()

    def _edit_connection(self):
        from app.ui.remote_dialog import RemoteConnectionEditor

        profile = self._current_profile()
        if not profile:
            QMessageBox.information(self, "Терминал",
                                    "Сначала добавьте подключение (кнопка «Добавить…»).")
            return
        editor = RemoteConnectionEditor(self.store, self.session_id, "terminal",
                                        profile, self)
        if editor.exec_():
            self._reload_connections()
            if self.session is not None:
                QMessageBox.information(
                    self, "Терминал",
                    "Данные подключения изменены. Нажмите «Отключиться», затем "
                    "«Подключиться», чтобы применить их к открытой сессии.")

    # ---------------------------------------------------------- соединение
    def _connect(self):
        if self._open_thread is not None and self._open_thread.isRunning():
            return
        profile = self._current_profile()
        if not profile:
            QMessageBox.information(
                self, "Терминал",
                "В этом чате нет SSH-подключений. Нажмите «Добавить…» и введите "
                "адрес, логин и пароль — они сохранятся в реестре Windows или в "
                "файле конфигов Linux.")
            return
        self.status_label.setText("подключаюсь…")
        self.connect_btn.setEnabled(False)
        self._open_thread = _OpenThread(self.store, self.session_id, profile, self)
        self._open_thread.ok.connect(self._on_open_ok)
        self._open_thread.failed.connect(self._on_open_failed)
        self._open_thread.start()

    def _on_open_ok(self, description: str, cwd: str):
        self.connect_btn.setEnabled(True)
        conn_id = int(self.conn_combo.currentData() or 0)
        self.session = remote_terminal.get_session(self.session_id, conn_id)
        self.prompt = f"{description}{(' ' + cwd) if cwd else ''}$ "
        self.status_label.setText("подключено: " + description)
        self.disconnect_btn.setEnabled(True)
        self.interrupt_btn.setEnabled(True)
        self._write(f"[подключено] {description}")
        self._write("Печатайте команды внизу и нажимайте Enter. "
                    "cd и export сохраняются между командами.")
        if self._pending:
            command, self._pending = self._pending, ""
            self._run_command(command)
        else:
            self.input.setFocus()

    def _on_open_failed(self, text: str):
        self.connect_btn.setEnabled(True)
        self.session = None
        self._pending = ""
        self.status_label.setText("не подключено")
        self._write(f"[ошибка подключения] {text}")
        box = QMessageBox(self)
        box.setWindowTitle("Подключение")
        box.setIcon(QMessageBox.Warning)
        box.setText("Не удалось подключиться.")
        box.setInformativeText(text)
        fix_btn = box.addButton("Настроить подключение…", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec_()
        if box.clickedButton() is fix_btn:
            self._edit_connection()

    def _disconnect(self):
        conn_id = int(self.conn_combo.currentData() or 0)
        if conn_id:
            remote_terminal.close_session(self.session_id, conn_id)
        self.session = None
        self.disconnect_btn.setEnabled(False)
        self.interrupt_btn.setEnabled(False)
        self.status_label.setText("не подключено")
        self._write("[отключено]")

    def _interrupt(self):
        conn_id = int(self.conn_combo.currentData() or 0)
        if not conn_id:
            return
        if remote_terminal.interrupt_session(self.session_id, conn_id):
            self._write("[Ctrl+C отправлен]")
        else:
            self._write("[Ctrl+C] активной сессии нет")

    # ------------------------------------------------------------- команды
    def _send(self):
        command = self.input.text().strip()
        if not command:
            return
        self.input.remember(command)
        self.input.clear()
        if self._busy:
            self._write("[подождите] предыдущая команда ещё выполняется")
            return
        self._write(self.prompt + command)
        if self.session is None or not self.session.alive:
            self._pending = command
            self._connect()
            return
        self._run_command(command)

    def _run_command(self, command: str):
        profile = self._current_profile() or {}
        timeout = float(profile.get("timeout") or 120)
        self._last_command = command
        self._busy = True
        self.send_btn.setEnabled(False)
        self._cmd_thread = _CommandThread(self.session, command, timeout, self)
        self._cmd_thread.done.connect(self._on_command_done)
        self._cmd_thread.failed.connect(self._on_command_failed)
        self._cmd_thread.start()

    def _on_command_done(self, result: dict):
        self._busy = False
        self.send_btn.setEnabled(True)
        text = str(result.get("stdout") or "")
        # Интерактивный shell повторяет введённую строку — убираем дубликат.
        lines = text.splitlines()
        echo = str(getattr(self, "_last_command", "") or "").strip()
        if lines and echo and lines[0].strip() == echo:
            lines = lines[1:]
        body = "\n".join(lines).rstrip()
        if body:
            self._write(body)
        error_text = str(result.get("stderr") or "").strip()
        if error_text:
            self._write(error_text)
        code = int(result.get("exit_code") or 0)
        if result.get("note"):
            self._write(f"[{result['note']}]")
        elif code not in (0,):
            self._write(f"[код возврата: {code}]")
        if self.session is None or not self.session.alive:
            self.status_label.setText("соединение закрыто")
            self.disconnect_btn.setEnabled(False)
            self.interrupt_btn.setEnabled(False)
        self.input.setFocus()

    def _on_command_failed(self, text: str):
        self._busy = False
        self.send_btn.setEnabled(True)
        self._write(f"[ошибка] {text}")
        self.input.setFocus()

    # ------------------------------------------------------------ мелочи
    def _write(self, text: str):
        if text is None:
            return
        self.output.moveCursor(QTextCursor.End)
        self.output.appendPlainText(str(text))
        self.output.moveCursor(QTextCursor.End)

    def _copy_output(self):
        QApplication.clipboard().setText(self.output.toPlainText())
        self.status_label.setText("вывод скопирован в буфер обмена")

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key_L and event.modifiers() & Qt.ControlModifier):
            self.output.clear()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        # Сессию не закрываем: её же использует ИИ-агент этого чата.
        for thread in (self._cmd_thread, self._open_thread):
            if thread is not None and thread.isRunning():
                thread.wait(3000)
        super().closeEvent(event)
