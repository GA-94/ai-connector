"""Repository panel.

The repository is read-only for the AI agent, but a human needs to manage it:
download an archive by link, rename, edit a text file, delete (reversible, to
`_trash/`), save a file into the repository and unpack an archive straight into
a project folder.
"""
import os
import subprocess
import urllib.parse

from app.qt_compat.QtCore import Qt
from app.qt_compat.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout
)

from app.config.settings import AppSettings
from app.tools import repo_admin, repo_status


def _open_path(path: str):
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except AttributeError:
        subprocess.Popen(["xdg-open", path])
    except OSError as error:
        QMessageBox.warning(None, "Репозиторий", f"Не удалось открыть:\n{error}")


class _TextDialog(QDialog):
    """Editor for a text file inside the repository."""

    def __init__(self, repo: str, rel: str, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.rel = rel
        self.setWindowTitle(f"AI-Connector — {rel}")
        self.resize(760, 560)

        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        layout.addWidget(self.editor)
        hint = QLabel("Кодировка и переводы строк сохраняются как в файле.")
        hint.setStyleSheet("color: #8a8f98;")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        try:
            self.editor.setPlainText(repo_admin.read_entry(repo, rel))
        except repo_admin.RepoAdminError as error:
            self.editor.setReadOnly(True)
            QMessageBox.warning(self, "Репозиторий", str(error))

        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, self.windowTitle())

    def _save(self):
        try:
            message = repo_admin.write_entry(self.repo, self.rel,
                                             self.editor.toPlainText())
        except repo_admin.RepoAdminError as error:
            QMessageBox.warning(self, "Репозиторий", str(error))
            return
        QMessageBox.information(self, "Репозиторий", message)
        self.accept()


class _PreviewDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, title)


class RepositoryDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.rel = ""
        self.status = repo_status.check_repository(settings.get_repository_root())
        self.setWindowTitle("AI-Connector — Репозиторий")
        self.resize(880, 640)
        self._build_ui()
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "AiConnector7 — Репозиторий")
        self.refresh()

    # ------------------------------------------------------------------- build
    def _build_ui(self):
        layout = QVBoxLayout(self)

        path_group = QGroupBox("Папка репозитория (склад шаблонов, MDK и архивов)")
        path_layout = QVBoxLayout(path_group)

        row = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(self.path_label, 1)
        self.change_btn = QPushButton("Изменить папку…")
        self.change_btn.clicked.connect(self._change_root)
        row.addWidget(self.change_btn)
        self.explorer_btn = QPushButton("Проводник")
        self.explorer_btn.clicked.connect(self._open_in_explorer)
        row.addWidget(self.explorer_btn)
        path_layout.addLayout(row)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        path_layout.addWidget(self.status_label)

        self.details_btn = QPushButton("Что не настроено")
        self.details_btn.clicked.connect(self._show_details)
        path_layout.addWidget(self.details_btn, alignment=Qt.AlignLeft)

        self.network_cb = QCheckBox("Разрешить скачивание архивов по ссылке")
        self.network_cb.setChecked(self.settings.get_allow_network())
        self.network_cb.stateChanged.connect(self._toggle_network)
        path_layout.addWidget(self.network_cb)

        layout.addWidget(path_group)

        nav = QHBoxLayout()
        self.up_btn = QPushButton("Вверх")
        self.up_btn.clicked.connect(self._go_up)
        nav.addWidget(self.up_btn)
        self.crumb_label = QLabel("/")
        self.crumb_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        nav.addWidget(self.crumb_label, 1)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self.refresh)
        nav.addWidget(self.refresh_btn)
        layout.addLayout(nav)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._open_item)
        self.list_widget.itemSelectionChanged.connect(self._update_actions)
        layout.addWidget(self.list_widget, 1)

        first_row = QHBoxLayout()
        self.download_btn = QPushButton("Скачать по ссылке…")
        self.download_btn.clicked.connect(self._download)
        first_row.addWidget(self.download_btn)
        self.save_btn = QPushButton("Сохранить в репозиторий…")
        self.save_btn.clicked.connect(self._save_from_disk)
        first_row.addWidget(self.save_btn)
        self.mkdir_btn = QPushButton("Создать папку")
        self.mkdir_btn.clicked.connect(self._mkdir)
        first_row.addWidget(self.mkdir_btn)
        layout.addLayout(first_row)

        second_row = QHBoxLayout()
        self.open_btn = QPushButton("Открыть / Изменить")
        self.open_btn.clicked.connect(self._open_selected)
        second_row.addWidget(self.open_btn)
        self.rename_btn = QPushButton("Переименовать")
        self.rename_btn.clicked.connect(self._rename)
        second_row.addWidget(self.rename_btn)
        self.delete_btn = QPushButton("Удалить в корзину")
        self.delete_btn.clicked.connect(self._delete)
        second_row.addWidget(self.delete_btn)
        self.extract_btn = QPushButton("Распаковать в проект…")
        self.extract_btn.clicked.connect(self._extract_to_project)
        second_row.addWidget(self.extract_btn)
        layout.addLayout(second_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ state
    def refresh(self):
        self.status = repo_status.check_repository(self.settings.get_repository_root())
        self.path_label.setText(f"<b>{self.status.path or '—'}</b>")
        color = "#2e9e5b" if self.status.ok else "#e05a4f"
        self.status_label.setText(
            f"<span style='color:{color}'>{self.status.short()}</span>")
        self.list_widget.clear()

        if not self.status.ok:
            self.rel = ""
            self.crumb_label.setText("/")
            self._update_actions()
            return

        try:
            entries = repo_admin.list_entries(self.settings.get_repository_root(), self.rel)
        except repo_admin.RepoAdminError:
            self.rel = ""
            try:
                entries = repo_admin.list_entries(self.settings.get_repository_root())
            except repo_admin.RepoAdminError:
                entries = []

        self.crumb_label.setText("/" + self.rel if self.rel else "/")
        for entry in entries:
            item = QListWidgetItem(self._format(entry))
            item.setData(Qt.UserRole, entry)
            self.list_widget.addItem(item)
        self._update_actions()

    def _format(self, entry: dict) -> str:
        if entry["is_dir"]:
            return f"[DIR]  {entry['name']}/"
        size = repo_status.human_size(entry["size"])
        if entry["is_archive"]:
            return f"[ARCHIVE] {entry['name']}  ({size})  — можно распаковать в проект"
        return f"[FILE] {entry['name']}  ({size})"

    def _current(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _update_actions(self):
        entry = self._current()
        has_repo = self.status.ok
        self.download_btn.setEnabled(has_repo)
        self.save_btn.setEnabled(has_repo)
        self.mkdir_btn.setEnabled(has_repo)
        self.open_btn.setEnabled(bool(entry))
        self.rename_btn.setEnabled(bool(entry))
        self.delete_btn.setEnabled(bool(entry))
        self.extract_btn.setEnabled(bool(entry and entry["is_archive"]))
        self.up_btn.setEnabled(bool(self.rel))
        self.change_btn.setEnabled(True)
        self.explorer_btn.setEnabled(bool(self.status.path))

    # ---------------------------------------------------------------- actions
    def _guard(self, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except repo_admin.RepoAdminError as error:
            QMessageBox.warning(self, "Репозиторий", str(error))
        except Exception as error:  # noqa: BLE001 - shown to the user as is
            QMessageBox.critical(self, "Ошибка", f"{type(error).__name__}: {error}")
        return None

    def _change_root(self):
        path = QFileDialog.getExistingDirectory(self, "Выберите папку репозитория",
                                               self.settings.get_repository_root())
        if not path:
            return
        self.settings.set_repository_root(path.replace("\\", "/"))
        self.rel = ""
        self.refresh()

    def _toggle_network(self, _state=None):
        self.settings.set_allow_network(self.network_cb.isChecked())

    def _show_details(self):
        QMessageBox.information(self, "Репозиторий", self.status.as_text())

    def _open_in_explorer(self):
        if not self.status.path:
            QMessageBox.information(self, "Репозиторий",
                                    "Папка репозитория ещё не указана.")
            return
        if not os.path.isdir(self.status.path):
            QMessageBox.warning(self, "Репозиторий",
                                f"Папка не существует:\n{self.status.path}")
            return
        _open_path(self.status.path)

    def _go_up(self):
        if not self.rel:
            return
        parent = "/".join(self.rel.split("/")[:-1])
        self.rel = parent
        self.refresh()

    def _open_item(self, item=None):
        entry = item.data(Qt.UserRole) if item is not None else self._current()
        if not entry:
            return
        repo = self.settings.get_repository_root()
        if entry["is_dir"]:
            self.rel = entry["rel"]
            self.refresh()
            return
        if entry["is_archive"]:
            text = self._guard(repo_admin.archive_preview, repo, entry["rel"])
            if text:
                _PreviewDialog(f"AI-Connector — {entry['name']}", text, self).exec_()
            return
        try:
            repo_admin.read_entry(repo, entry["rel"])
        except repo_admin.RepoAdminError as error:
            QMessageBox.information(
                self, "Репозиторий",
                f"{error}.\nМожно открыть папку в проводнике и посмотреть вручную.")
            return
        _TextDialog(repo, entry["rel"], self).exec_()
        self.refresh()

    def _open_selected(self):
        self._open_item()

    def _download(self):
        repo = self.settings.get_repository_root()
        url, ok = QInputDialog.getText(self, "Скачать в репозиторий",
                                       "Ссылка (http/https) на архив:")
        if not ok or not url.strip():
            return
        default = os.path.basename(urllib.parse.urlparse(url.strip()).path) or "asset.zip"
        name, ok = QInputDialog.getText(self, "Скачать в репозиторий",
                                        "Сохранить как:", text=default)
        if not ok:
            return
        message = self._guard(repo_admin.download_into_repo, repo, url.strip(),
                              name.strip(), False, self.network_cb.isChecked())
        if message and "Уже скачано" in message:
            reply = QMessageBox.question(self, "Репозиторий", message + "\nПерекачать?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                message = self._guard(repo_admin.download_into_repo, repo, url.strip(),
                                      name.strip(), True, self.network_cb.isChecked())
        if message:
            QMessageBox.information(self, "Репозиторий", message)
            self.refresh()

    def _save_from_disk(self):
        repo = self.settings.get_repository_root()
        path, _ = QFileDialog.getOpenFileName(self, "Файл, который нужно положить в репозиторий")
        if not path:
            return
        message = self._guard(repo_admin.save_file, repo, path, self.rel, False)
        if message and "Уже существует" in message:
            reply = QMessageBox.question(self, "Репозиторий", message + "\nЗаменить?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                message = self._guard(repo_admin.save_file, repo, path, self.rel, True)
        if message:
            QMessageBox.information(self, "Репозиторий", message)
            self.refresh()

    def _mkdir(self):
        repo = self.settings.get_repository_root()
        name, ok = QInputDialog.getText(self, "Новая папка", "Имя папки:")
        if not ok or not name.strip():
            return
        message = self._guard(repo_admin.create_dir, repo, self.rel, name.strip())
        if message:
            QMessageBox.information(self, "Репозиторий", message)
            self.refresh()

    def _rename(self):
        entry = self._current()
        if not entry:
            return
        repo = self.settings.get_repository_root()
        name, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:",
                                        text=entry["name"])
        if not ok or not name.strip():
            return
        message = self._guard(repo_admin.rename_entry, repo, entry["rel"], name.strip())
        if message:
            QMessageBox.information(self, "Репозиторий", message)
            self.refresh()

    def _delete(self):
        entry = self._current()
        if not entry:
            return
        repo = self.settings.get_repository_root()
        reply = QMessageBox.question(
            self, "Удаление",
            f"Удалить «{entry['name']}»?\n\n"
            f"Файл переместится в служебную папку _trash и его можно вернуть.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        message = self._guard(repo_admin.delete_entry, repo, entry["rel"])
        if message:
            QMessageBox.information(self, "Репозиторий", message)
            self.refresh()

    def _extract_to_project(self):
        entry = self._current()
        if not entry or not entry["is_archive"]:
            QMessageBox.information(self, "Репозиторий", "Выбери архив (.zip/.jar/.tar.gz).")
            return
        repo = self.settings.get_repository_root()
        destination = QFileDialog.getExistingDirectory(
            self, "Папка проекта, куда распаковать архив")
        if not destination:
            return
        overwrite = QMessageBox.question(
            self, "Распаковка",
            "Перезаписать файлы, которые уже есть в проекте?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

        plan = self._guard(repo_admin.extract_to_project, repo, entry["rel"],
                           destination, overwrite, False, True)
        if not plan:
            return
        reply = QMessageBox.question(self, "План распаковки",
                                     plan[:2000] + "\n\nРаспаковать?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        result = self._guard(repo_admin.extract_to_project, repo, entry["rel"],
                             destination, overwrite, False, False)
        if result:
            QMessageBox.information(self, "Репозиторий", result[:3000])
