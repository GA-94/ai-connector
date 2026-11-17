"""Фоновые запросы «что установлено»: Ollama и облачные серверы.

Зачем отдельный модуль: раньше окна (настройки чата, провайдеры) спрашивали
Ollama и nvidia-smi прямо в главном потоке. Пока сервер молчал или драйвер
думал, окно не перерисовывалось — открытие «подвисало» на 10-15 секунд.
Теперь всё, что ходит в сеть или к железу, живёт в QThread, а интерфейс
показывает статус и обновляет его, когда придёт ответ.
"""

from app.qt_compat.QtCore import QThread, pyqtSignal

from app.agent import ollama, providers


class OllamaProbeThread(QThread):
    """Проверка Ollama целиком: сервер, версия, модели, железо, размещение."""

    done = pyqtSignal(object)  # dict из ollama.probe()

    def __init__(self, host: str = "", mode: str = "auto", parent=None):
        super().__init__(parent)
        self.host = host
        self.mode = mode

    def run(self):
        try:
            result = ollama.probe(self.host, self.mode)
        except Exception as error:  # noqa: BLE001 - поток не должен падать молча
            result = {"host": ollama.normalize_host(self.host), "running": False,
                      "version": "", "models": [], "placement": "",
                      "hardware": {}, "hardware_text": "",
                      "error": f"{type(error).__name__}: {error}"}
        self.done.emit(result)


class ToolsProbeThread(QThread):
    """Проверка «модель принимает инструменты» (Ollama, в фоне).

    Загружает модель и делает два коротких запроса, поэтому в главном потоке
    её запускать нельзя: на 27B это десятки секунд.
    """

    done = pyqtSignal(object, str)  # (True/False/None, текст)

    def __init__(self, host: str = "", model: str = "", num_ctx: int = 0,
                 force: bool = False, parent=None):
        super().__init__(parent)
        self.host = host
        self.model = model
        self.num_ctx = int(num_ctx or 0)
        self.force = bool(force)

    def run(self):
        try:
            ok, text = ollama.tools_supported(self.host, self.model, self.num_ctx,
                                              force=self.force)
        except Exception as error:  # noqa: BLE001 - поток не должен падать молча
            ok, text = None, f"Не удалось проверить: {type(error).__name__}: {error}"
        self.done.emit(ok, text)


class ModelsThread(QThread):
    """Список моделей у OpenAI-совместимого сервера (облако или локальный)."""

    done = pyqtSignal(bool, object)  # (успех, список имён либо текст ошибки)

    def __init__(self, base_url: str, api_key: str = "", parent=None):
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key

    def run(self):
        try:
            ok, result = providers.fetch_models(self.base_url, self.api_key)
        except Exception as error:  # noqa: BLE001
            ok, result = False, f"{type(error).__name__}: {error}"
        self.done.emit(ok, result)
