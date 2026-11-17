#!/usr/bin/env python3
"""Стартер AI-Connector для Linux (Arch, Debian, Ubuntu, Fedora, openSUSE...).

Отличия от start.pyw (Windows):
  * никакого ctypes.windll и UAC - на Linux права администратора программе
    не нужны и не запрашиваются;
  * пути нормализуются: убирается символ ^M (\r), обратные слэши заменяются
    на обычные "/", чтобы ничего не ломалось;
  * рабочая папка = папка проекта, поэтому data/chats.db всегда лежит рядом
    с программой, а не там, откуда её запустили;
  * понятные подсказки по установке зависимостей для pacman/apt/dnf/zypper.

Запуск:
    ./start.sh              (или bash start.sh)
    python3 start_linux.py
    python3 start_linux.py --check    только диагностика, без GUI
"""

import os
import shutil
import signal
import sys
import traceback

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# зависимости: модуль -> подсказки по установке
DEPENDENCIES = {
    "openai": [
        "pip install --user openai",
    ],
    # PyQt6, а не PyQt5: Qt 6 — единственный вариант, который собирается и
    # ставится не только под x86_64, но и под ARM64 (в том числе Windows 11
    # ARM64 и Raspberry Pi).
    "PyQt6": [
        "Debian/Ubuntu/Mint: sudo apt install python3-pyqt6",
        "Arch/Manjaro:       sudo pacman -S python-pyqt6",
        "Fedora:             sudo dnf install python3-pyqt6",
        "openSUSE:           sudo zypper install python3-PyQt6",
        "или везде:          pip install --user PyQt6",
    ],
    "pygments": [
        "pip install --user pygments",
    ],
}


def _clean(value: str) -> str:
    """^M (\r) и обратные слэши ломают пути на Linux - вычищаем."""
    return value.replace("\x00", "").replace("\r", "").replace("\\", "/")


def normalize_sys_path():
    """Нормализовать sys.path: без ^M, с обычными слэшами."""
    fixed = []
    for entry in sys.path:
        if not entry:
            fixed.append(entry)
            continue
        cleaned = _clean(entry)
        if cleaned and cleaned not in fixed:
            fixed.append(cleaned)
    sys.path[:] = fixed
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)


def normalize_environment():
    """Вычистить ^M из переменных, с которыми работают пути."""
    for key in ("PYTHONPATH", "PWD", "OLDPWD", "HOME", "AI_CONNECTOR_HOME"):
        value = os.environ.get(key)
        if value:
            os.environ[key] = _clean(value)
    # Явный признак того, каким стартером запущена программа.
    os.environ["AI_CONNECTOR_OS"] = "linux"
    os.environ["AI_CONNECTOR_LAUNCHER"] = "start_linux.py"
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


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


def print_dependency_help(missing) -> None:
    print("Не хватает библиотек: " + ", ".join(missing))
    for name in missing:
        print("\n" + name + ":")
        for hint in DEPENDENCIES[name]:
            print("    " + hint)
    print("\nПосле установки запусти: ./start.sh")
    print("Готовый список: pip install --user -r docs/requirements.txt")


def display_problem() -> str:
    """Пустой DISPLAY/WAYLAND_DISPLAY = графику показать некуда."""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return ""
    if os.environ.get("AI_CONNECTOR_ALLOW_HEADLESS") == "1":
        return ""
    return ("Не найден графический сервер: переменные DISPLAY и WAYLAND_DISPLAY пусты.\n"
            "Программа с интерфейсом не может работать без X11/Wayland.\n"
            "Если ты в SSH/контейнере: ssh -X, либо запусти на рабочем столе.")


def print_diagnostics(missing) -> None:
    import platform
    import struct

    print("AiConnector7: диагностика запуска")
    print("  ОС:           " + platform.platform())
    print("  Архитектура:  " + (platform.machine() or "?") + ", Python "
          + str(struct.calcsize("P") * 8) + "-бит")
    print("  Python:       " + sys.version.split()[0] + " (" + sys.executable + ")")
    print("  Папка:        " + PROJECT_DIR)
    print("  DISPLAY:      " + (os.environ.get("DISPLAY") or "(нет)"))
    print("  WAYLAND:      " + (os.environ.get("WAYLAND_DISPLAY") or "(нет)"))
    print("  Сессия:       " + (os.environ.get("XDG_SESSION_TYPE") or "неизвестно"))
    print("  Зависимости:  " + ("все на месте" if not missing else "нет: " + ", ".join(missing)))
    print("  xdg-open:     " + (shutil.which("xdg-open") or "(нет, папки не будут открываться)"))
    if sys.path:
        print("  sys.path[0]:  " + (sys.path[0] or "(cwd)"))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    normalize_sys_path()
    normalize_environment()

    # Qt6 иногда не дружит с Wayland - даём аварийный переключатель.
    if "--xcb" in argv:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    elif os.environ.get("AI_CONNECTOR_QT_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = os.environ["AI_CONNECTOR_QT_PLATFORM"]

    missing = check_dependencies()
    if "--check" in argv:
        print_diagnostics(missing)
        return 0 if not missing else 2
    if missing:
        print_dependency_help(missing)
        return 2

    problem = display_problem()
    if problem:
        print(problem)
        print("Подсказка: python3 start_linux.py --check покажет окружение.")
        return 3

    # Ctrl+C в терминале должен закрывать программу, а не висеть.
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    except (ValueError, OSError):
        pass

    # data/chats.db и относительные пути считаются от папки проекта.
    try:
        os.chdir(PROJECT_DIR)
    except OSError as error:
        print("Не удалось перейти в папку проекта: " + str(error))

    # Краш-репорты включаем ДО импорта PyQt6: падение на импорте интерфейса
    # тоже должно оставить отчёт (в 0.6.3 папка после вылета была пустой).
    # Мост PyQt5->PyQt6 — ещё раньше: внутри crash.py есть ленивые импорты Qt.
    try:
        import app.qt_compat  # noqa: F401
    except Exception as bridge_error:
        print("Мост PyQt5->PyQt6 не загрузился: " + str(bridge_error),
              file=sys.stderr)
    try:
        from app.crash import install as install_crash
        install_crash("start_linux.py")
    except Exception as crash_error:
        print("Краш-репорты недоступны: " + str(crash_error), file=sys.stderr)

    try:
        from app.qt_compat.QtWidgets import QApplication
        from app.ui.main_window import MainWindow
    except Exception:
        print("Ошибка импорта интерфейса:")
        traceback.print_exc()
        try:
            from app import crash
            crash.capture(*sys.exc_info(), title="Ошибка импорта интерфейса",
                          source="start_linux.py")
        except Exception:
            pass
        return 1

    try:
        try:
            from app.safe_app import SafeApplication
            app = SafeApplication(sys.argv)
        except Exception:
            app = QApplication(sys.argv)
        app.setApplicationName("AiConnector7")
        window = MainWindow()
        window.show()
        return int(app.exec())
    except Exception:
        print("Критическая ошибка при запуске:")
        traceback.print_exc()
        try:
            from app import crash
            crash.capture(*sys.exc_info(), title="Критическая ошибка запуска",
                          source="start_linux.py")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
