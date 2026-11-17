"""AiConnector7 - запуск с консолью (видно все ошибки).

Двойной клик по этому файлу или в терминале:
    python start_debug.py

Отличается от start.pyw только тем, что окно консоли остаётся открытым, а
текст ошибки виден целиком. Если start.pyw "молчит", запусти этот файл -
он сразу скажет, чего не хватает.
"""

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    os.chdir(ROOT)
    os.makedirs(os.path.join(ROOT, "crash_reports"), exist_ok=True)
    log = open(os.path.join(ROOT, "crash_reports", "boot.log"), "a",
               buffering=1, encoding="utf-8", errors="replace")
    log.write("\n=== start_debug.py ===\n")
except OSError:
    log = None


def say(text):
    print(text)
    if log is not None:
        try:
            log.write(str(text) + "\n")
        except OSError:
            pass


say("python: %s" % (sys.executable or "?"))
say("версия: %s" % sys.version.split()[0])
say("папка: %s" % ROOT)
say("консоль: " + ("есть" if sys.stdout is not None else "нет"))

# У пользователя .py/.pyw привязаны к free-threaded Python (python3.14t.exe),
# а PyQt6 в нём падает с 0xC0000005 - без окна и без отчёта. Перезапускаемся
# обычным интерпретатором до импорта PyQt6.
try:
    from app.interp_guard import ensure as guard_ensure, explain as guard_explain, is_free_threaded

    if guard_ensure(script=os.path.abspath(__file__), purpose="start_debug.py", say=say):
        sys.exit(0)
    if is_free_threaded():
        say(guard_explain())
        try:
            input("\nНажми Enter, чтобы закрыть...")
        except EOFError:
            pass
        sys.exit(1)
except SystemExit:
    raise
except Exception as guard_error:  # noqa: BLE001
    say("сторож интерпретатора не сработал: %r" % (guard_error,))

code = 1
try:
    try:
        import app.qt_compat  # noqa: F401

        say("мост PyQt5->PyQt6 загружен")
    except Exception as bridge_error:  # noqa: BLE001
        say("мост PyQt5->PyQt6 не загрузился: %r" % (bridge_error,))

    from app.crash import install

    install("start_debug.py")
    say("краш-репорты включены")

    from app.qt_compat.QtWidgets import QApplication

    say("PyQt6: ок (мост совместимости включён)")
    from app.ui.main_window import MainWindow

    say("интерфейс импортирован")
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    say("окно открыто, работаем")
    code = int(app.exec())
    say("цикл завершён, код %d" % code)
except BaseException:
    text = traceback.format_exc()
    say("ОШИБКА:\n" + text)
    try:
        from app.crash import capture

        capture(*sys.exc_info(), title="Ошибка при запуске (start_debug)",
                source="start_debug.py")
    except Exception:
        pass

if sys.stdout is not None:
    try:
        input("\nГотово. Нажми Enter, чтобы закрыть...")
    except EOFError:
        pass
sys.exit(code)
