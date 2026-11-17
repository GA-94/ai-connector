"""QApplication, который не вылетает от исключения в обработчике интерфейса.

PyQt6 (как и PyQt5) при необработанном Python-исключении внутри слота вызывает
qFatal() -> abort(): программа исчезает, в pythonw не остаётся даже traceback
(в 0.6.3 это и выглядело как «вылетело и папка краш-репортов пустая»).

Здесь:
  * notify() обёрнут в try/except — через него проходят события и доставка
    сигналов, поэтому исключение попадает в краш-отчёт, а интерфейс живёт;
  * при выходе все фоновые потоки останавливаются и ожидаются (иначе Qt
    делает abort при уничтожении работающего QThread).
"""

import sys
import traceback

from app.qt_compat.QtWidgets import QApplication

from app import crash
from app.ui import threads

APP_TITLE = "AiConnector7"


class SafeApplication(QApplication):
    """QApplication с защитой от падения на исключениях в слотах."""

    def __init__(self, argv):
        super().__init__(argv)
        self._error_count = 0
        try:
            self.setApplicationName(APP_TITLE)
        except Exception:  # noqa: BLE001
            pass
        try:
            from app import __version__
            self.setApplicationVersion(__version__)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.aboutToQuit.connect(self._on_about_to_quit)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ events

    def notify(self, receiver, event):  # noqa: N802 - Qt API
        try:
            return super().notify(receiver, event)
        except Exception:  # noqa: BLE001 - любое исключение из слота/события
            self._error_count += 1
            exc_type, exc, tb = sys.exc_info()
            try:
                crash.capture(exc_type, exc, tb,
                              title="Исключение в обработчике интерфейса",
                              source="QApplication.notify")
            except Exception:  # noqa: BLE001
                pass
            try:
                sys.stderr.write("Исключение в интерфейсе (программа продолжает работу):\n")
                sys.stderr.write("".join(traceback.format_exception(exc_type, exc, tb)))
            except Exception:  # noqa: BLE001
                pass
            return False

    # ------------------------------------------------------------------- выход

    def _on_about_to_quit(self) -> None:
        """Не дать Qt уничтожить работающие потоки: сначала останавливаем."""
        try:
            running = threads.live_threads()
            if running:
                crash.log_line(f"[app] выход: останавливаю потоков — {len(running)}")
            survivors = threads.stop_threads(running, 4000)
            if survivors:
                crash.log_line(
                    f"[app] {len(survivors)} поток(ов) не завершились — аварийный выход")
                threads.hard_exit(0)
        except Exception:  # noqa: BLE001
            pass
