"""Фоновые потоки для управления сервером Ollama.

Сеть и запуск процессов не должны идти в главном потоке: окно настроек
открывалось с задержкой 10-15 секунд, когда запросы к Ollama шли синхронно.
Здесь всё то же самое, но в QThread — интерфейс остаётся отзывчивым.
"""

from app.qt_compat.QtCore import QThread, pyqtSignal

from app.agent import ollama_service


class ServerStartThread(QThread):
    """Поднять или перезапустить Ollama с нужными настройками."""

    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, host: str, flags: str, keep_alive: str = "",
                 context_length: int = 0, force: bool = False, parent=None):
        super().__init__(parent)
        self.host = host
        self.flags = flags
        self.keep_alive = keep_alive
        self.context_length = int(context_length or 0)
        self.force = bool(force)

    def run(self):
        try:
            ok, text = ollama_service.ensure_with_flags(
                self.host, self.flags, keep_alive=self.keep_alive,
                context_length=self.context_length, force=self.force,
                log=self.log.emit)
        except Exception as error:  # noqa: BLE001 - окно не должно падать
            ok, text = False, f"{type(error).__name__}: {error}"
        self.done.emit(ok, text)


class StatusThread(QThread):
    """Сводка о сервере Ollama (процессы, версия, эмбеддинги) — в фоне."""

    done = pyqtSignal(object)

    def __init__(self, host: str, flags: str = "", parent=None):
        super().__init__(parent)
        self.host = host
        self.flags = flags

    def run(self):
        try:
            data = ollama_service.status(self.host, self.flags)
        except Exception as error:  # noqa: BLE001
            data = {"error": f"{type(error).__name__}: {error}"}
        self.done.emit(data)


class EmbedCheckThread(QThread):
    """Проверить, считает ли сервер эмбеддинги (/api/embed)."""

    done = pyqtSignal(bool, str)

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.host = host

    def run(self):
        try:
            ok, text = ollama_service.supports_embeddings(self.host)
        except Exception as error:  # noqa: BLE001
            ok, text = False, f"{type(error).__name__}: {error}"
        self.done.emit(ok, text)


class PullModelThread(QThread):
    """Скачать модель (например nomic-embed-text, 274 МБ)."""

    progress = pyqtSignal(int, str)
    done = pyqtSignal(bool, str)

    def __init__(self, host: str, name: str, parent=None):
        super().__init__(parent)
        self.host = host
        self.name = name

    def run(self):
        try:
            ok, text = ollama_service.pull_embed_model(
                self.host, name=self.name,
                progress=lambda percent, status: self.progress.emit(percent, status))
        except Exception as error:  # noqa: BLE001
            ok, text = False, f"{type(error).__name__}: {error}"
        self.done.emit(ok, text)
