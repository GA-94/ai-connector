r"""AiConnector7 - запуск на Windows (pythonw, без окна консоли).

Зачем отдельный стартер, если есть main.pyw:
  * у .pyw нет консоли: при ошибке сообщение уходит в никуда — окно не
    появляется, в crash_reports пусто. Поэтому стартер сам показывает диалог
    с текстом ошибки;
  * первым действием пишется crash_reports/boot.log — журнал запуска по
    шагам, и по нему видно, на каком именно шаге всё встало;
  * проверяются зависимости (openai, PyQt6, pygments) и показывается готовая
    команда установки;
  * права администратора не запрашиваются, пока в settings.ini не включено
    [launch] require_admin=true (UAC на каждом запуске только мешает).

Порядок запуска:
  1. пути и рабочая папка = папка программы;
  2. переменные окружения (AI_CONNECTOR_LAUNCHER и прочие);
  3. сторож интерпретатора (python3.14t роняет PyQt6 без отчёта);
  4. журнал boot.log;
  5. мост PyQt5 -> PyQt6 и краш-репорты;
  6. проверка зависимостей;
  7. при необходимости — перезапуск с правами администратора;
  8. окно программы.

Запуск: двойной клик по файлу или
    python start.pyw            обычный запуск
    python start.pyw --check    только проверка окружения (без окна)
"""

import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    os.chdir(ROOT)
except OSError:
    pass

# Библиотеки, без которых программа не стартует: модуль -> как поставить.
DEPENDENCIES = {
    "openai": ["pip install openai"],
    "PyQt6": ["pip install PyQt6"],
    "pygments": ["pip install pygments"],
}

REQUIREMENTS_HINT = "pip install -r docs/requirements.txt"


# ------------------------------------------------------------------ журнал

def boot_dir() -> str:
    return os.path.join(ROOT, "crash_reports")


def say(text) -> None:
    """Строка в crash_reports/boot.log (консоли у .pyw нет).

    Формат тот же, что у сторожа интерпретатора: дата, pid, текст — по такому
    журналу видно, на каком шаге запуск остановился.
    """
    line = "%s [pid %s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                               os.getpid(), " ".join(str(text).split()))
    try:
        os.makedirs(boot_dir(), exist_ok=True)
        with open(os.path.join(boot_dir(), "boot.log"), "a",
                  encoding="utf-8", errors="replace") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    # В start_debug.py консоль есть — там полезно увидеть то же самое.
    try:
        if sys.stdout is not None:
            print(text)
    except Exception:  # noqa: BLE001 - в pythonw stdout может быть закрыт
        pass


def message(title: str, text: str, error: bool = False) -> None:
    """Показать диалог без Qt: Qt может быть как раз тем, что не работает."""
    say("%s: %s" % (title, text))
    if os.name != "nt":
        return
    try:
        import ctypes

        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), flags)
    except Exception:  # noqa: BLE001 - диалог не важнее запуска
        pass


# ------------------------------------------------------- окружение Windows

def prepare_environment() -> None:
    """Признаки того, кем и на чём запущена программа (их читает crash.py)."""
    os.environ["AI_CONNECTOR_OS"] = "windows"
    os.environ["AI_CONNECTOR_LAUNCHER"] = "start.pyw"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def prepare_windows_process() -> None:
    """DPI и идентификатор приложения: чёткий текст и своя иконка в панели задач."""
    if os.name != "nt":
        return
    try:
        import ctypes

        # PROCESS_PER_MONITOR_DPI_AWARE: иначе на масштабировании 125-150%
        # интерфейс получается мыльным.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001 - старые Windows
            ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AiConnector7")
    except Exception as error:  # noqa: BLE001
        say("настройки Windows не применились: %r" % (error,))


def check_dependencies() -> list:
    """-> список отсутствующих модулей."""
    import importlib.util

    missing = []
    for name in DEPENDENCIES:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def dependencies_text(missing) -> str:
    lines = ["Не хватает библиотек: " + ", ".join(missing), ""]
    for name in missing:
        for hint in DEPENDENCIES[name]:
            lines.append("    " + hint)
    lines.append("")
    lines.append("Все зависимости сразу:")
    lines.append("    " + REQUIREMENTS_HINT)
    lines.append("")
    lines.append("После установки запусти start.pyw ещё раз.")
    return "\n".join(lines)


# ------------------------------------------------- права администратора (UAC)

def is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def require_admin() -> bool:
    """Нужны ли права администратора (settings.ini -> [launch] require_admin)."""
    import configparser

    parser = configparser.ConfigParser()
    try:
        parser.read(os.path.join(ROOT, "settings.ini"), encoding="utf-8")
        return bool(parser.getboolean("launch", "require_admin", fallback=False))
    except (configparser.Error, OSError, ValueError):
        return False


def elevate() -> bool:
    """Перезапустить себя с правами администратора. True — запрос отправлен."""
    if os.name != "nt":
        return False
    if os.environ.get("AI_CONNECTOR_ELEVATED") == "1" or is_admin():
        return False
    executable = sys.executable or ""
    if not executable:
        return False
    try:
        import ctypes

        os.environ["AI_CONNECTOR_ELEVATED"] = "1"
        params = '"%s"' % os.path.abspath(__file__)
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, params, ROOT, 1)
        # ShellExecuteW возвращает код > 32 при успехе
        return int(result or 0) > 32
    except Exception as error:  # noqa: BLE001
        say("перезапуск с правами не удался: %r" % (error,))
        return False


# ------------------------------------------------------------------- запуск

def run_gui() -> int:
    """Открыть главное окно и работать до выхода."""
    try:
        # Мост PyQt5 -> PyQt6 до всего остального: краш-репорты лениво
        # импортируют Qt под старым именем.
        import app.qt_compat  # noqa: F401

        say("мост PyQt5->PyQt6 загружен")
    except Exception as bridge_error:  # noqa: BLE001
        say("мост PyQt5->PyQt6 не загрузился: %r" % (bridge_error,))

    try:
        from app.crash import install as install_crash

        install_crash("start.pyw")
        say("краш-репорты включены")
    except Exception as crash_error:  # noqa: BLE001 - без отчётов тоже работает
        say("краш-репорты недоступны: %r" % (crash_error,))

    try:
        from app.qt_compat.QtWidgets import QApplication
        from app.ui.main_window import MainWindow
    except Exception as error:
        text = traceback.format_exc()
        say("ОШИБКА импорта интерфейса:\n" + text)
        try:
            from app import crash

            crash.capture(*sys.exc_info(), title="Ошибка импорта интерфейса",
                          source="start.pyw")
        except Exception:  # noqa: BLE001
            pass
        missing = check_dependencies()
        hint = dependencies_text(missing) if missing else ""
        message("AiConnector7 не запустился",
                "Ошибка при загрузке интерфейса:\n\n%s\n\n%s\n"
                "Подробности: crash_reports\\boot.log" % (error, hint),
                error=True)
        return 1

    try:
        try:
            from app.safe_app import SafeApplication

            app = SafeApplication(sys.argv)
        except Exception:  # noqa: BLE001 - защита не должна мешать запуску
            app = QApplication(sys.argv)
        app.setApplicationName("AiConnector7")
        window = MainWindow()
        window.show()
        say("окно открыто")
        code = int(app.exec())
        say("работа завершена, код %d" % code)
        return code
    except BaseException:
        text = traceback.format_exc()
        say("КРИТИЧЕСКАЯ ОШИБКА:\n" + text)
        try:
            from app import crash

            crash.capture(*sys.exc_info(), title="Критическая ошибка запуска",
                          source="start.pyw")
        except Exception:  # noqa: BLE001
            pass
        message("AiConnector7: критическая ошибка",
                "Программа остановилась:\n\n%s\n\n"
                "Отчёт: crash_reports\\ (файл report.txt)" % text[-1500:],
                error=True)
        return 1


def print_check() -> int:
    """--check: показать окружение, ничего не открывая."""
    import platform
    import struct

    missing = check_dependencies()
    lines = ["AiConnector7: проверка запуска",
             "  ОС:           " + platform.platform(),
             "  Архитектура:  " + (platform.machine() or "?") + ", Python "
             + str(struct.calcsize("P") * 8) + "-бит",
             "  Python:       " + sys.version.split()[0] + " (" + (sys.executable or "?") + ")",
             "  Папка:        " + ROOT,
             "  Консоль:      " + ("есть" if sys.stdout is not None else "нет (pythonw)"),
             "  Права админа: " + ("есть" if is_admin() else "нет"),
             "  Нужны права (settings.ini): " + ("да" if require_admin() else "нет"),
             "  Зависимости:  " + ("все на месте" if not missing
                                   else "нет: " + ", ".join(missing))]
    text = "\n".join(lines)
    say(text)
    if sys.stdout is None:
        message("AiConnector7: проверка", text)
    return 0 if not missing else 2


def main() -> int:
    prepare_environment()

    # Не Windows (WSL, macOS, Linux) — там свой стартер со своими подсказками.
    if os.name != "nt":
        say("не Windows: передаю запуск start_linux.py")
        from start_linux import main as run_posix

        return int(run_posix())

    prepare_windows_process()
    say("=== старт start.pyw ===")
    say("argv: " + " ".join(sys.argv))
    say("python: %s, версия %s" % (sys.executable or "?", sys.version.split()[0]))

    # python3.14t.exe (free-threaded) роняет PyQt6 нарушением доступа — сторож
    # перезапускает обычным интерпретатором до импорта Qt.
    try:
        from app.interp_guard import ensure as guard_ensure

        if guard_ensure(script=os.path.abspath(__file__), purpose="start.pyw",
                        say=say):
            say("перезапуск обычным интерпретатором")
            return 0
    except SystemExit:
        raise
    except Exception as guard_error:  # noqa: BLE001
        say("сторож интерпретатора не сработал: %r" % (guard_error,))

    if "--check" in sys.argv[1:]:
        return print_check()

    missing = check_dependencies()
    if missing:
        text = dependencies_text(missing)
        say(text)
        message("AiConnector7: не хватает библиотек", text, error=True)
        return 2

    if require_admin() and not is_admin():
        say("запрошены права администратора (settings.ini [launch] require_admin=true)")
        if elevate():
            say("запущена копия с правами администратора, эта закрывается")
            return 0
        say("права не получены — продолжаю без администратора")

    return run_gui()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        text = traceback.format_exc()
        say("ОШИБКА ЗАПУСКА:\n" + text)
        try:
            from app import crash

            crash.capture(*sys.exc_info(), title="Ошибка запуска (start.pyw)",
                          source="start.pyw")
        except Exception:  # noqa: BLE001
            pass
        message("AiConnector7 не запустился", text[-2000:], error=True)
        sys.exit(1)
