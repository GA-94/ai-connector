"""Выбор провайдера ИИ и моделей, включая локальные модели Ollama.

Здесь же — подбор модели под железо и скачивание её без выхода из программы.
"""

from app.qt_compat.QtCore import QThread, pyqtSignal, Qt
from app.qt_compat.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QGroupBox, QListWidget, QListWidgetItem, QMessageBox,
    QProgressDialog, QCheckBox, QDialogButtonBox, QSpinBox, QFormLayout,
    QApplication, QAbstractItemView, QPlainTextEdit, QInputDialog,
    QDoubleSpinBox
)

import time


from app.agent import providers, ollama
from app.config.settings import AppSettings
from app.ui import threads
from app.ui.model_probe import OllamaProbeThread


class PullThread(QThread):
    """Скачивание модели Ollama в фоне (прогресс в диалог)."""
    progress = pyqtSignal(int, str)
    done = pyqtSignal(bool, str)

    def __init__(self, host: str, name: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.name = name

    def run(self):
        ok, text = ollama.pull(self.host, self.name,
                               progress=lambda percent, status:
                               self.progress.emit(percent, status))
        self.done.emit(ok, text)


class CreateModelThread(QThread):
    """Сборка своей модели из базовой (/api/create) — в фоне."""
    progress = pyqtSignal(int, str)
    done = pyqtSignal(bool, str)

    def __init__(self, host: str, name: str, from_model: str, system: str,
                 parameters: dict, parent=None):
        super().__init__(parent)
        self.host = host
        self.name = name
        self.from_model = from_model
        self.system = system
        self.parameters = dict(parameters or {})

    def run(self):
        ok, text = ollama.create_model(
            self.host, name=self.name, from_model=self.from_model,
            system=self.system, parameters=self.parameters,
            progress=lambda percent, status: self.progress.emit(percent, status))
        self.done.emit(ok, text)


class ChatProbeThread(QThread):
    """Тестовый вопрос модели потоком (/api/chat со stream=True).

    Заодно показывает скорость: токены в секунду считает сама Ollama.
    """
    chunk = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, host: str, model: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.model = model

    def run(self):
        started = time.time()
        parts = []
        thinking = []
        tokens = 0
        duration_ns = 0
        error = ""
        for event in ollama.chat_stream(
                host=self.host, model=self.model,
                messages=[{"role": "user",
                           "content": "Ответь одной короткой фразой: модель работает."}],
                options={"num_predict": 64}, keep_alive="2m", timeout=300.0):
            if event.get("error"):
                error = str(event["error"])
                break
            delta = event.get("text") or ""
            if delta:
                parts.append(delta)
                self.chunk.emit(delta)
            if event.get("thinking"):
                thinking.append(str(event["thinking"]))
            if event.get("done"):
                tokens = int(event.get("eval_count") or 0)
                duration_ns = int(event.get("eval_duration") or 0)
        if error:
            self.done.emit(False, error)
            return
        answer = "".join(parts).strip()
        if not answer:
            self.done.emit(False, "Модель ответила пустым сообщением.")
            return
        speed = ""
        if tokens and duration_ns:
            speed = f" · {tokens / (duration_ns / 1e9):.1f} ток./с"
        note = f"Ответ за {time.time() - started:.1f} с{speed}"
        if thinking:
            note += f" · размышлений {len(''.join(thinking))} симв."
        self.done.emit(True, note + "\n\n" + answer[:800])


class ModelCreateDialog(QDialog):
    """Своя модель из базовой: system-промпт и параметры зашиваются в неё.

    Такая модель ведёт себя одинаково в любом чате — не надо помнить, что в
    этом чате был особый промпт и длина контекста.
    """

    def __init__(self, models, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Своя модель Ollama")
        self.resize(560, 520)
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "Своя модель Ollama")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("например my-coder:ctx8k")
        form.addRow("Имя новой модели:", self.name_edit)

        self.base_combo = QComboBox()
        self.base_combo.setEditable(True)
        for name in models:
            self.base_combo.addItem(name)
        form.addRow("Из базовой:", self.base_combo)

        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(0, 262144)
        self.ctx_spin.setSingleStep(1024)
        self.ctx_spin.setValue(8192)
        self.ctx_spin.setSpecialValueText("как у базовой")
        self.ctx_spin.setToolTip("Сколько токенов модель помнит. Чем больше, "
                                 "тем больше видеопамяти нужно.")
        form.addRow("Длина контекста:", self.ctx_spin)

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(0.3)
        self.temp_spin.setToolTip("0 — строго и предсказуемо, 1 — свободнее.")
        form.addRow("Температура:", self.temp_spin)
        layout.addLayout(form)

        layout.addWidget(QLabel("Системный промпт (характер модели):"))
        self.system_edit = QPlainTextEdit()
        self.system_edit.setPlaceholderText(
            "Например: ты строгий ревьюер Python-кода. Отвечай кратко, "
            "без похвалы, показывай только проблемы.")
        layout.addWidget(self.system_edit, 1)

        note = QLabel("Это копия базовой модели с зашитыми настройками: веса не "
                      "дублируются, но модель появится в списке как обычная и "
                      "удаляется так же.")
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        parameters = {}
        ctx = int(self.ctx_spin.value())
        if ctx > 0:
            parameters["num_ctx"] = ctx
        parameters["temperature"] = float(self.temp_spin.value())
        return {
            "name": self.name_edit.text().strip(),
            "from_model": self.base_combo.currentText().strip(),
            "system": self.system_edit.toPlainText().strip(),
            "parameters": parameters,
        }



class ProvidersDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Провайдеры и модели")
        self.resize(720, 760)
        self._pull_thread = None
        self._probe_thread = None
        self._create_thread = None
        self._test_thread = None
        self._build_ui()
        self._load_values()
        self._start_ollama_probe()
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "Провайдеры и модели")

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        layout = QVBoxLayout(self)

        provider_group = QGroupBox("Сервис (куда уходят запросы)")
        form = QFormLayout(provider_group)
        self.provider_combo = QComboBox()
        for key, label in providers.choices():
            self.provider_combo.addItem(label, key)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("Провайдер:", self.provider_combo)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("Base URL:", self.base_url_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("ключ этого провайдера (хранится вне проекта)")
        form.addRow("API Key:", self.api_key_edit)

        key_row = QHBoxLayout()
        self.keys_link = QLabel()
        self.keys_link.setOpenExternalLinks(True)
        self.keys_link.setObjectName("mutedLabel")
        key_row.addWidget(self.keys_link)
        key_row.addStretch(1)
        self.balance_link = QLabel()
        self.balance_link.setOpenExternalLinks(True)
        self.balance_link.setObjectName("mutedLabel")
        key_row.addWidget(self.balance_link)
        form.addRow(key_row)

        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setObjectName("mutedLabel")
        form.addRow(self.note_label)
        layout.addWidget(provider_group)

        # --- модели сервиса ---
        model_group = QGroupBox("Модели")
        model_layout = QVBoxLayout(model_group)
        self.models_list = QListWidget()
        self.models_list.setSelectionMode(QAbstractItemView.SingleSelection)
        model_layout.addWidget(self.models_list)
        models_buttons = QHBoxLayout()
        self.load_models_btn = QPushButton("Спросить сервер (список моделей)")
        self.load_models_btn.clicked.connect(self._load_models)
        models_buttons.addWidget(self.load_models_btn)
        self.use_model_btn = QPushButton("Сделать моделью по умолчанию")
        self.use_model_btn.clicked.connect(self._use_selected_model)
        models_buttons.addWidget(self.use_model_btn)
        models_buttons.addStretch(1)
        model_layout.addLayout(models_buttons)
        layout.addWidget(model_group, stretch=1)

        # --- Ollama ---
        ollama_group = QGroupBox("Локальные модели (Ollama)")
        ollama_layout = QVBoxLayout(ollama_group)
        host_row = QHBoxLayout()
        host_row.addWidget(QLabel("Адрес:"))
        self.host_edit = QLineEdit(ollama.DEFAULT_HOST)
        host_row.addWidget(self.host_edit, 1)
        self.check_btn = QPushButton("Проверить")
        self.check_btn.clicked.connect(self._check_ollama)
        host_row.addWidget(self.check_btn)
        self.start_btn = QPushButton("Запустить сервер")
        self.start_btn.clicked.connect(self._start_ollama)
        host_row.addWidget(self.start_btn)
        ollama_layout.addLayout(host_row)

        self.hardware_label = QLabel()
        self.hardware_label.setWordWrap(True)
        self.hardware_label.setObjectName("mutedLabel")
        ollama_layout.addWidget(self.hardware_label)

        self.local_list = QListWidget()
        self.local_list.setMaximumHeight(150)
        self.local_list.setToolTip(
            "Двойной клик — карточка модели (параметры, квантование, контекст).")
        self.local_list.itemDoubleClicked.connect(self._show_model_card)
        ollama_layout.addWidget(self.local_list)

        local_buttons = QHBoxLayout()
        self.recommend_btn = QPushButton("Что потянет железо")
        self.recommend_btn.clicked.connect(self._show_recommendations)
        local_buttons.addWidget(self.recommend_btn)
        self.pull_btn = QPushButton("Скачать выбранную")
        self.pull_btn.clicked.connect(self._pull_selected)
        local_buttons.addWidget(self.pull_btn)
        self.delete_btn = QPushButton("Удалить выбранную")
        self.delete_btn.setToolTip("Удалить модель с диска (освободит место)")
        self.delete_btn.clicked.connect(self._delete_selected)
        local_buttons.addWidget(self.delete_btn)
        local_buttons.addStretch(1)
        ollama_layout.addLayout(local_buttons)

        local_buttons2 = QHBoxLayout()
        self.copy_btn = QPushButton("Копировать модель")
        self.copy_btn.setToolTip("Сделать копию под другим именем (/api/copy) — "
                                 "веса не дублируются")
        self.copy_btn.clicked.connect(self._copy_selected)
        local_buttons2.addWidget(self.copy_btn)
        self.create_btn = QPushButton("Своя модель…")
        self.create_btn.setToolTip("Собрать модель из базовой: системный промпт, "
                                   "длина контекста, температура (/api/create)")
        self.create_btn.clicked.connect(self._create_model)
        local_buttons2.addWidget(self.create_btn)
        self.test_btn = QPushButton("Проверить модель")
        self.test_btn.setToolTip("Отправить тестовый вопрос и показать ответ "
                                 "потоком (/api/chat stream)")
        self.test_btn.clicked.connect(self._test_selected)
        local_buttons2.addWidget(self.test_btn)
        local_buttons2.addStretch(1)
        ollama_layout.addLayout(local_buttons2)
        layout.addWidget(ollama_group)


        # --- деньги ---
        money_group = QGroupBox("Деньги")
        money_form = QFormLayout(money_group)
        self.rub_spin = QSpinBox()
        self.rub_spin.setRange(1, 10000)
        self.rub_spin.setValue(95)
        self.rub_spin.setSuffix(" ₽ за $1")
        money_form.addRow("Курс доллара:", self.rub_spin)
        self.price_edit = QLineEdit()
        self.price_edit.setPlaceholderText("например 120, 480 — ₽ за 1M входных/выходных (пусто = по таблице)")
        money_form.addRow("Своя цена:", self.price_edit)
        layout.addWidget(money_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------- helpers

    def _load_values(self):
        current = self.settings.get_provider()
        index = self.provider_combo.findData(current)
        self.provider_combo.setCurrentIndex(index if index >= 0 else 0)
        self._on_provider_changed(self.provider_combo.currentIndex())
        self.host_edit.setText(self.settings.get_ollama_host())
        self.rub_spin.setValue(int(self.settings.get_rub_per_usd() or 95))
        self.price_edit.setText(self.settings.get_price_override())
        # Железо определяет фоновый поток: nvidia-smi не должен держать окно.
        cached = ollama.cached_hardware()
        self.hardware_label.setText(
            ("Железо: " + ollama.describe(cached)) if cached else "Железо: определяю…")

    def _current_provider(self) -> str:
        return str(self.provider_combo.currentData() or "custom")

    def _on_provider_changed(self, _index):
        key = self._current_provider()
        info = providers.get(key)
        saved_url = ""
        if key == self.settings.get_provider():
            saved_url = self.settings.get_base_url()
        self.base_url_edit.setText(saved_url or info.base_url)
        self.api_key_edit.setText(self.settings.get_api_key_for(key))
        self.api_key_edit.setEnabled(info.needs_key)
        self.keys_link.setText(
            f'<a href="{info.keys_url}">Где взять ключ</a>' if info.keys_url else "")
        balance = providers.balance_url_for(key, "")
        self.balance_link.setText(
            f'<a href="{balance}">Страница баланса</a>' if balance else "")
        self.note_label.setText(info.note)
        self.models_list.clear()
        for model in info.models:
            self.models_list.addItem(QListWidgetItem(model))
        self.use_model_btn.setEnabled(bool(info.models))

    def _load_models(self):
        key = self._current_provider()
        base_url = self.base_url_edit.text().strip()
        if not base_url:
            QMessageBox.warning(self, "Модели", "Сначала впиши Base URL.")
            return
        api_key = self.api_key_edit.text().strip()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        ok, result = providers.fetch_models(base_url, api_key)
        QApplication.restoreOverrideCursor()
        if not ok:
            QMessageBox.warning(self, "Модели", str(result))
            return
        self.models_list.clear()
        for name in result:
            self.models_list.addItem(QListWidgetItem(name))
        QMessageBox.information(self, "Модели", f"Сервер вернул {len(result)} моделей.")

    def _use_selected_model(self):
        item = self.models_list.currentItem()
        if not item:
            QMessageBox.information(self, "Модели", "Выбери модель в списке.")
            return
        self.settings.set_master_model(item.text())
        self.settings.set_worker_model(item.text())
        QMessageBox.information(
            self, "Модели", f"Модель по умолчанию: {item.text()}\n"
            "В уже созданных чатах модель меняется в настройках чата.")

    # -------------------------------------------------------------- Ollama

    def _start_ollama_probe(self):
        """Спросить Ollama в фоне: сервер, модели, железо."""
        if threads.is_running(self._probe_thread):
            return
        thread = OllamaProbeThread(self.host_edit.text().strip(), "auto", self)
        self._probe_thread = threads.track(thread)
        thread.done.connect(self._on_ollama_probe)
        thread.start()

    def _on_ollama_probe(self, data):
        models = list(data.get("models") or [])
        self.local_list.clear()
        for entry in models:
            text = f"{entry['name']} — {float(entry.get('size') or 0):.1f} ГБ"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry["name"])
            self.local_list.addItem(item)
        hardware_text = data.get("hardware_text") or ""
        if not data.get("running"):
            self.hardware_label.setText(
                (f"Железо: {hardware_text}\n" if hardware_text else "")
                + "Сервер Ollama не отвечает. " + ollama.start_hint())
        elif not models:
            self.hardware_label.setText(
                (f"Железо: {hardware_text}\n" if hardware_text else "")
                + "Сервер работает, но моделей нет. Нажми «Что потянет железо».")
        else:
            self.hardware_label.setText(
                (f"Железо: {hardware_text}\n" if hardware_text else "")
                + f"Скачано моделей: {len(models)}")

    def _check_ollama(self):
        self._start_ollama_probe()

    def _start_ollama(self):
        ok, text = ollama.start_server()
        QMessageBox.information(self, "Ollama", text)

    def _show_recommendations(self):
        info = ollama.hardware()
        self.hardware_label.setText("Железо: " + ollama.describe(info))
        self.local_list.clear()
        for entry in ollama.recommend(info):
            text = (f"{entry['name']} — нужно {entry['ram']} ГБ RAM, "
                    f"скачать ~{entry['size']} ГБ · {entry.get('where', '')} "
                    f"({entry['about']})")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry["name"])
            self.local_list.addItem(item)

    def _pull_selected(self):
        item = self.local_list.currentItem()
        if not item:
            QMessageBox.information(self, "Ollama", "Выбери модель в списке.")
            return
        name = str(item.data(Qt.UserRole) or "")
        host = self.host_edit.text().strip()
        if not ollama.is_running(host):
            QMessageBox.warning(self, "Ollama", ollama.start_hint())
            return
        progress = QProgressDialog(f"Скачивание {name}…", "Отмена", 0, 100, self)
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
        from app.ui import threads
        threads.track(thread)
        thread.start()
        progress.exec_()

    def _show_model_card(self, item):
        """Карточка модели: параметры, квантование, контекст (/api/show)."""
        name = str(item.data(Qt.UserRole) or "").strip()
        if not name:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        ok, text = ollama.show(self.host_edit.text().strip(), name)
        QApplication.restoreOverrideCursor()
        if ok:
            QMessageBox.information(self, "Ollama", text)
        else:
            QMessageBox.warning(self, "Ollama", str(text))

    def _delete_selected(self):
        """Удалить модель с диска: спрашиваем подтверждение, потом /api/delete."""
        item = self.local_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Ollama", "Выбери модель в списке.")
            return
        name = str(item.data(Qt.UserRole) or "").strip()
        if not name:
            return
        answer = QMessageBox.question(
            self, "Удалить модель?",
            f"Удалить {name} с диска?\n\n"
            "Модель придётся скачивать заново. Загруженной в память она быть не "
            "должна — иначе Ollama сначала её выгрузит.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        ok, text = ollama.delete(self.host_edit.text().strip(), name)
        if ok:
            QMessageBox.information(self, "Ollama", text)
            self._start_ollama_probe()
        else:
            QMessageBox.warning(self, "Ollama", str(text))

    def _copy_selected(self):
        """Копия модели под новым именем (/api/copy)."""
        item = self.local_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Ollama", "Выбери модель в списке.")
            return
        source = str(item.data(Qt.UserRole) or "").strip()
        if not source:
            return
        base, _, tag = source.partition(":")
        default = f"{base}-copy" + (f":{tag}" if tag else "")
        name, accepted = QInputDialog.getText(
            self, "Копировать модель", f"Имя копии для {source}:", text=default)
        if not accepted:
            return
        name = name.strip()
        if not name:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        ok, text = ollama.copy_model(self.host_edit.text().strip(), source, name)
        QApplication.restoreOverrideCursor()
        if ok:
            QMessageBox.information(self, "Ollama", text)
            self._start_ollama_probe()
        else:
            QMessageBox.warning(self, "Ollama", str(text))

    def _create_model(self):
        """Своя модель из базовой: system-промпт + параметры (/api/create)."""
        names = []
        for index in range(self.local_list.count()):
            entry = str(self.local_list.item(index).data(Qt.UserRole) or "").strip()
            if entry:
                names.append(entry)
        if not names:
            QMessageBox.information(
                self, "Ollama",
                "Список моделей пуст. Сначала нажми «Проверить» — программе нужно "
                "знать базовую модель.")
            return
        dialog = ModelCreateDialog(names, self)
        if not dialog.exec():
            return
        data = dialog.values()
        if not data["name"] or not data["from_model"]:
            QMessageBox.warning(self, "Ollama",
                                "Нужны имя новой модели и базовая модель.")
            return
        progress = QProgressDialog(f"Собираю {data['name']}…", "Отмена", 0, 100, self)
        progress.setWindowTitle("Ollama")
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setCancelButton(None)
        thread = CreateModelThread(self.host_edit.text().strip(), data["name"],
                                   data["from_model"], data["system"],
                                   data["parameters"], self)
        self._create_thread = threads.track(thread)
        thread.progress.connect(
            lambda percent, status: (progress.setValue(percent),
                                     progress.setLabelText(f"{status or 'собираю'} {percent}%")))
        thread.done.connect(
            lambda ok, text: (progress.close(), self._on_create_done(ok, text)))
        thread.start()
        progress.exec_()

    def _on_create_done(self, ok: bool, text: str):
        if ok:
            QMessageBox.information(self, "Ollama", str(text) + "\n\n"
                                    "Теперь её можно выбрать в списке моделей.")
            self._start_ollama_probe()
        else:
            QMessageBox.warning(self, "Ollama", str(text))

    def _test_selected(self):
        """Тестовый вопрос модели потоком: видно, что она отвечает и как быстро."""
        item = self.local_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Ollama", "Выбери модель в списке.")
            return
        name = str(item.data(Qt.UserRole) or "").strip()
        if not name:
            return
        progress = QProgressDialog(f"Проверяю {name}…", "Отмена", 0, 0, self)
        progress.setWindowTitle("Ollama")
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setCancelButton(None)
        thread = ChatProbeThread(self.host_edit.text().strip(), name, self)
        self._test_thread = threads.track(thread)
        thread.chunk.connect(
            lambda text: progress.setLabelText(f"{name}: {text[-160:]}"))
        thread.done.connect(
            lambda ok, text: (progress.close(), self._on_test_done(ok, text)))
        thread.start()
        progress.exec_()

    def _on_test_done(self, ok: bool, text: str):
        if ok:
            QMessageBox.information(self, "Ollama", str(text))
        else:
            QMessageBox.warning(self, "Ollama", str(text))

    def _on_pull_done(self, ok, text, name):
        if ok:
            QMessageBox.information(self, "Ollama", text)
            self._check_ollama()
        else:
            QMessageBox.warning(self, "Ollama", text)

    # -------------------------------------------------------------- сохранить

    def done(self, result):
        """Перед закрытием гасим фоновые потоки: иначе Qt делает abort()."""
        from app.ui import threads
        threads.stop_for(self)
        super().done(result)

    def accept(self):
        key = self._current_provider()
        info = providers.get(key)
        self.settings.set_provider(key)
        base_url = self.base_url_edit.text().strip()
        if base_url:
            self.settings.set_base_url(base_url)
        if info.needs_key:
            self.settings.set_api_key_for(key, self.api_key_edit.text().strip())
        self.settings.set_ollama_host(self.host_edit.text().strip() or ollama.DEFAULT_HOST)
        self.settings.set_rub_per_usd(float(self.rub_spin.value()))
        self.settings.set_price_override(self.price_edit.text().strip())
        super().accept()
