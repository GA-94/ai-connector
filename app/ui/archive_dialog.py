"""Архив чатов: скрытые из основного списка диалоги.

Чат не удаляется безвозвратно — его можно вернуть или стереть окончательно.
"""

from app.qt_compat.QtCore import Qt
from app.qt_compat.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QDialogButtonBox, QMessageBox
)

from app.agent import pricing


class ArchiveDialog(QDialog):
    def __init__(self, chat_store, parent=None):
        super().__init__(parent)
        self.chat_store = chat_store
        self.restored = []
        self.setWindowTitle("Архив чатов")
        self.resize(560, 480)
        self._build_ui()
        from app.ui.title_bar import apply_dialog_frame
        apply_dialog_frame(self, "Архив чатов")
        self._reload()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        hint = QLabel("Архивные чаты не видны в основной колонке, но всё ещё в базе.")
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.restore_btn = QPushButton("Восстановить")
        self.restore_btn.clicked.connect(self._restore)
        row.addWidget(self.restore_btn)
        self.delete_btn = QPushButton("Удалить навсегда")
        self.delete_btn.clicked.connect(self._delete)
        row.addWidget(self.delete_btn)
        row.addStretch(1)
        layout.addLayout(row)

        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)

    def _reload(self):
        self.list.clear()
        sessions = self.chat_store.list_archived()
        for session in sessions:
            cost = pricing.format_rub(session.get("cost_rub") or 0)
            text = f"{session['title']}   ·   {session.get('updated_at') or ''}   ·   {cost}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, session["id"])
            self.list.addItem(item)
        if not sessions:
            self.list.addItem(QListWidgetItem("Архив пуст"))

    def _selected_id(self):
        item = self.list.currentItem()
        if not item:
            return None
        return item.data(Qt.UserRole)

    def _restore(self):
        session_id = self._selected_id()
        if not session_id:
            return
        self.chat_store.set_archived(session_id, False)
        self.restored.append(session_id)
        self._reload()

    def _delete(self):
        session_id = self._selected_id()
        if not session_id:
            return
        answer = QMessageBox.question(
            self, "Удаление", "Удалить чат из архива безвозвратно?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.chat_store.delete_session(session_id)
        self._reload()
