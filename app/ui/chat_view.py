"""Окно чата: сообщения рендерятся как карточки (Markdown -> HTML).

Цвета берутся из app.ui.theme, поэтому при смене темы чат перерисовывается
без перезагрузки истории.
"""

from app.qt_compat.QtCore import Qt, QTimer
from app.qt_compat.QtGui import QTextCursor
from app.qt_compat.QtWidgets import QTextBrowser

import html

from app.ui import theme
from app.ui.markdown_render import render_message

MAX_THOUGHT_CHARS = 1500


class ChatView(QTextBrowser):
    def __init__(self, parent=None, theme_name: str = "dark"):
        super().__init__(parent)
        self.setObjectName("chatView")
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setFrameShape(QTextBrowser.NoFrame)
        self.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(12)

        self._theme = theme.normalize(theme_name)
        self._glass = False
        self._messages = []
        self._placeholder = False
        self._render_all()

    # ------------------------------------------------------------------- тема

    def set_theme(self, name: str, glass: bool = False):
        name = theme.normalize(name)
        if name == self._theme and glass == self._glass:
            return
        self._theme = name
        self._glass = glass
        self._render_all()

    def _colors(self) -> dict:
        return theme.chat_colors(self._theme, self._glass)

    # --------------------------------------------------------------- история

    def clear_chat(self):
        """Очищает поле чата для загрузки новой сессии."""
        self._messages = []
        self._render_all()

    def append_user(self, text: str):
        self._append("user", text)

    def append_agent(self, text: str):
        self._append("assistant", text)

    def append_thought(self, text: str):
        """Мысли/план агента по ходу задачи.

        В историю чата не пишутся: это живой прогресс, а не сообщение.
        """
        text = (text or "").strip()
        if not text:
            return
        if len(text) > MAX_THOUGHT_CHARS:
            text = text[:MAX_THOUGHT_CHARS].rstrip() + "…"
        self._messages.append(("thought", text))
        self._render_all()

    def _append(self, role: str, text: str):
        # Перерисовываем весь документ из истории: так вставка таблицы-карточки
        # никогда не попадает внутрь предыдущей карточки (грабли QTextDocument).
        self._messages.append((role, text))
        self._render_all()

    def _render_all(self):
        self.clear()
        if not self._messages:
            self._insert_html(self._placeholder_html())
            self._placeholder = True
            return
        colors = self._colors()
        self.setHtml("<html><body>" + "".join(
            self._render_thought(text) if role == "thought"
            else render_message(role, text, colors)
            for role, text in self._messages
        ) + "</body></html>")
        self._placeholder = False
        self._scroll_to_bottom()

    def _render_thought(self, text: str) -> str:
        """Серая карточка «что агент думает» — без Markdown, только текст."""
        colors = self._colors()
        body = html.escape(text or "").replace("\n", "<br>")
        return (
            '<table width="100%" cellspacing="0" cellpadding="10" '
            'style="margin:0 0 8px 0;">'
            f'<tr><td bgcolor="{colors["thought_bg"]}">'
            '<p style="margin:0 0 4px 0; font-size:8pt; font-weight:700; '
            f'color:{colors["muted"]};">&#128173; ДУМАЕТ</p>'
            f'<p style="margin:0; font-size:9pt; color:{colors["muted"]};">{body}</p>'
            '</td></tr></table>')

    # ---------------------------------------------------------------- служебное

    def _placeholder_html(self) -> str:
        colors = self._colors()
        return ('<table width="100%" cellspacing="0" cellpadding="18"><tr><td>'
                '<p style="margin:0; font-size:13pt; font-weight:600; '
                f'color:{colors["text"]};">Готов к работе</p>'
                '<p style="margin:6px 0 0 0; '
                f'color:{colors["muted"]};">Опиши задачу — агент прочитает проект, '
                'внесёт правки и покажет, что изменил.</p></td></tr></table>')

    def _insert_html(self, html_text: str):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(html_text)

    def _scroll_to_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
