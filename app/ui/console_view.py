from app.qt_compat.QtWidgets import QPlainTextEdit


class ConsoleView(QPlainTextEdit):
    """Консоль агента: журнал инструментов и служебные сообщения."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("consoleView")
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setPlaceholderText("Журнал работы агента")

    def log(self, message: str):
        # Каждая строка консоли попадает в журнал краш-репортов: если программа
        # вылетит, в отчёте будет видно, что агент делал в последние секунды.
        # (Журнал пишется построчно на диск, поэтому переживает даже SIGKILL.)
        try:
            from app import crash
            crash.log_line(message)
        except Exception:  # noqa: BLE001 - журнал не должен мешать работе
            pass
        self.appendPlainText(message)
