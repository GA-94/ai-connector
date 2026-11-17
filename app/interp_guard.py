"""Сторож интерпретатора: защита от free-threaded сборки Python.

Почему 0.6.4 запускалась только из VS Code
------------------------------------------
Python 3.13+ ставится в двух вариантах: обычный (с GIL) и free-threaded
(без GIL). Файлы второго называются python3.14t.exe / pythonw3.14t.exe.
Windows открывает .pyw тем интерпретатором, который привязан к расширению,
и у пользователя это оказался pythonw3.14t.exe.

PyQt6 в free-threaded сборке НЕ работает: импорт PyQt6.QtCore падает с
нарушением доступа (код 0xC0000005). Это не исключение Python, а падение
процесса, поэтому не срабатывает НИЧЕГО: ни sys.excepthook, ни
threading.excepthook, ни faulthandler, ни краш-репорты. Наружу это выглядит
ровно так: "нажимаю start.pyw — ничего не происходит, папка отчётов пустая".
Из VS Code всё работало, потому что отладчик запускает обычный python.exe.

Замер на машине пользователя (start.pyw под pythonw3.14t.exe):
  шаг "from PyQt6.QtCore import QT_VERSION_STR" -> процесс убит, 0xC0000005,
  в boot.log последняя строка "права администратора: нет", папка сессии пустая.

Что делает модуль: ещё до любого импорта PyQt6 проверяет сборку и, если она
free-threaded, перезапускает программу обычным интерпретатором, который
реально может импортировать PyQt6. Проверка идёт ДОЧЕРНИМ процессом, поэтому
неудачный кандидат умирает сам и не роняет родителя. Удачный интерпретатор
запоминается в data/interp_cache.json, чтобы следующий запуск был быстрым.

Тот же сторож отсеивает 32-битный Python: под Windows 11 ARM64 и x86_64
колёс PyQt6 под 32 бита не выпускается (а под ARM64 нет и PyQt5), поэтому
проверка сборки и разрядности идёт одной пробой.

Модуль не должен импортировать ничего тяжёлого: только stdlib.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import time

CACHE_TTL = 7 * 24 * 3600
CREATE_NO_WINDOW = 0x08000000
MAX_PROBES = 10
FREE_NAME = re.compile(r"^python(w)?\d+\.\d+t(_d)?(\.exe)?$", re.I)
PROBE_CODE = "import PyQt6.QtCore as c; print(c.QT_VERSION_STR)"


# ------------------------------------------------------------------ журнал

def _root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _log(text, say=None) -> None:
    """Строка в журнал запуска (тот же boot.log, что пишет start.pyw)."""
    if say is not None:
        try:
            say(text)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        line = "%s [guard %s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                     os.getpid(), " ".join(str(text).split()))
        folder = os.path.join(_root(), "crash_reports")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "boot.log"), "a", encoding="utf-8",
                  errors="replace") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------- проверки

def is_free_threaded() -> bool:
    """Сборка без GIL? (python3.14t.exe / pythonw3.14t.exe)"""
    try:
        if int(sysconfig.get_config_var("Py_GIL_DISABLED") or 0):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        checker = getattr(sys, "_is_gil_enabled", None)
        if checker is not None and not checker():
            return True
    except Exception:  # noqa: BLE001
        pass
    return bool(FREE_NAME.match(os.path.basename(sys.executable or "")))


def architecture() -> str:
    """Архитектура и разрядность интерпретатора.

    Под Windows 11 ARM64 нужен Python ARM64 (или x64 под эмуляцией): колёс
    PyQt6 под 32-битный Python не выпускается вовсе, а PyQt5 под ARM64 нет.
    """
    try:
        import platform
        import struct

        return "%s, %d-бит" % ((platform.machine() or "?").lower(),
                               struct.calcsize("P") * 8)
    except Exception:  # noqa: BLE001
        return "неизвестна"


def is_64bit() -> bool:
    """64-битный интерпретатор? (только для него есть колёса PyQt6)"""
    try:
        import struct

        return struct.calcsize("P") * 8 == 64
    except Exception:  # noqa: BLE001
        return True


def _relaunch_count() -> int:
    try:
        return int(os.environ.get("AI_CONNECTOR_GUARD") or 0)
    except (TypeError, ValueError):
        return 0


def probe(exe: str, timeout: int = 45):
    """Может ли этот интерпретатор импортировать PyQt6? -> (да/нет, версия Qt).

    Проверка идёт дочерним процессом: free-threaded сборка убивает себя
    нарушением доступа, но родитель при этом остаётся жив.
    """
    params = [exe, "-c", PROBE_CODE]
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE, timeout=timeout)
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        done = subprocess.run(params, **kwargs)
    except Exception:  # noqa: BLE001 - нет файла, таймаут, отказ доступа
        return False, ""
    text = (done.stdout or b"").decode("utf-8", "replace").strip()
    return done.returncode == 0, text


# ------------------------------------------------------------- кандидаты

def candidates() -> list:
    """Интерпретаторы, которые стоит проверить (pythonw.exe первым)."""
    found = []

    def add(path):
        if not path:
            return
        try:
            path = os.path.abspath(str(path))
        except OSError:
            return
        if not os.path.isfile(path):
            return
        if os.path.normcase(path) == os.path.normcase(sys.executable or ""):
            return
        if any(os.path.normcase(path) == os.path.normcase(other) for other in found):
            return
        found.append(path)

    names = ("pythonw.exe", "python.exe") if os.name == "nt" else ("python3", "python")
    for folder in (os.path.dirname(sys.executable or ""),
                   getattr(sys, "base_prefix", "") or "",
                   getattr(sys, "prefix", "") or ""):
        for name in names:
            add(os.path.join(folder, name) if folder else name)
    for name in names:
        try:
            add(shutil.which(name))
        except Exception:  # noqa: BLE001
            pass

    if os.name == "nt":
        _add_from_registry(add)
        _add_from_launcher(add)
        for base in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python"),
                     os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "C:\\"):
            if not base or not os.path.isdir(base):
                continue
            try:
                entries = sorted(os.listdir(base), reverse=True)
            except OSError:
                continue
            for name in entries:
                if name.lower().startswith("python3"):
                    folder = os.path.join(base, name)
                    add(os.path.join(folder, "pythonw.exe"))
                    add(os.path.join(folder, "python.exe"))
    else:
        for base in ("/usr/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")):
            try:
                entries = sorted(os.listdir(base), reverse=True)
            except OSError:
                continue
            for name in entries:
                if re.match(r"^python3(\.\d+)?t?$", name):
                    add(os.path.join(base, name))
    return found


def _add_from_registry(add) -> None:
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return
    for hive_name in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
        hive = getattr(winreg, hive_name, None)
        if hive is None:
            continue
        try:
            with winreg.OpenKey(hive, r"Software\Python\PythonCore") as key:
                count = winreg.QueryInfoKey(key)[0]
                versions = [winreg.EnumKey(key, index) for index in range(count)]
        except OSError:
            continue
        for version in versions:
            path_key = r"Software\Python\PythonCore\%s\InstallPath" % version
            for value in ("WindowedExecutablePath", "ExecutablePath"):
                try:
                    with winreg.OpenKey(hive, path_key) as key:
                        add(winreg.QueryValueEx(key, value)[0])
                except OSError:
                    continue


def _add_from_launcher(add) -> None:
    """py.exe -0p печатает список установленных интерпретаторов."""
    try:
        done = subprocess.run(["py", "-0p"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=20, creationflags=CREATE_NO_WINDOW)
    except Exception:  # noqa: BLE001
        return
    text = (done.stdout or b"").decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if parts and os.path.isfile(parts[-1]):
            add(parts[-1])


# ------------------------------------------------------------------- кэш

def _cache_path() -> str:
    return os.path.join(_root(), "data", "interp_cache.json")


def _cache_key() -> str:
    return os.path.normcase(sys.executable or "")


def _load_cache() -> str:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    if data.get("key") != _cache_key():
        return ""
    if time.time() - float(data.get("time") or 0) > CACHE_TTL:
        return ""
    exe = str(data.get("exe") or "")
    return exe if exe and os.path.isfile(exe) else ""


def _save_cache(exe: str) -> None:
    try:
        folder = os.path.dirname(_cache_path())
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        with open(_cache_path(), "w", encoding="utf-8") as handle:
            json.dump({"key": _cache_key(), "exe": exe, "time": time.time()},
                      handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ------------------------------------------------------------- подбор и старт

def find_working(limit: int = MAX_PROBES, say=None):
    """Первый интерпретатор, который импортирует PyQt6. -> (путь, отчёт)."""
    tested = []
    probes = 0
    for exe in candidates():
        if probes >= limit:
            break
        # free-threaded по имени даже не проверяем: он гарантированно упадёт
        if FREE_NAME.match(os.path.basename(exe)):
            tested.append((exe, False, "free-threaded"))
            continue
        probes += 1
        works, version = probe(exe)
        tested.append((exe, works, version))
        _log("проверка %s -> %s" % (exe, ("Qt " + version) if works else "не годится"), say)
        if works:
            return exe, tested
    return "", tested


def _relaunch(exe: str, script: str, args) -> bool:
    """Перезапустить программу другим интерпретатором (дочерним процессом).

    os.execve на Windows здесь не годится: проверено - процесс завершается с
    кодом 0, а целевой скрипт не запускается вообще. Поэтому запускаем
    отдельный процесс и сразу выходим. Если консоли нет (двойной клик по
    .pyw), дочерний процесс отсоединяем (DETACHED_PROCESS), чтобы он жил
    после выхода родителя и не показывал чёрное окно.
    """
    params = [exe, script] + [str(item) for item in args]
    env = dict(os.environ)
    env["AI_CONNECTOR_GUARD"] = str(_relaunch_count() + 1)
    env.pop("AI_CONNECTOR_NO_WATCHDOG", None)
    folder = os.path.dirname(script) or "."
    try:
        os.chdir(folder)
    except OSError:
        pass
    kwargs = dict(cwd=folder, env=env, stdin=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        # sys.__stdout__ == None означает «настоящей консоли нет» (двойной клик
        # по .pyw): тогда отделяемся, чтобы окно жило после выхода родителя.
        if sys.__stdout__ is None:
            kwargs["creationflags"] = CREATE_NO_WINDOW | 0x00000008
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(params, **kwargs)
    except Exception as error:  # noqa: BLE001
        _log("перезапуск не удался: %r" % (error,))
        return False
    return True


def ensure(script=None, purpose: str = "", say=None) -> bool:
    """Перезапустить программу обычным Python, если сборка free-threaded.

    -> True, если программа перезапущена (вызывающий должен сразу выйти).
    Функция безопасна для обычной сборки: просто возвращает False.
    """
    try:
        if not is_free_threaded():
            return False
        if _relaunch_count() >= 2:
            _log("перезапуск уже был (%d) - останавливаюсь, чтобы не зациклиться"
                 % _relaunch_count(), say)
            return False
        script = os.path.abspath(script or sys.argv[0])
        _log("=== сторож интерпретатора (%s) ===" % (purpose or "запуск"), say)
        _log("интерпретатор: %s" % (sys.executable or "?"), say)
        _log("Python %s: сборка free-threaded (без GIL) - PyQt6 в ней падает "
             "с 0xC0000005" % sys.version.split()[0], say)
        exe = _load_cache()
        if exe:
            _log("из кэша подходит: " + exe, say)
        else:
            exe, tested = find_working(say=say)
            if exe:
                _save_cache(exe)
            else:
                _log("рабочий интерпретатор с PyQt6 не найден (проверено %d)"
                     % len(tested), say)
        if not exe:
            return False
        _log("перезапуск: %s %s" % (exe, script), say)
        return _relaunch(exe, script, sys.argv[1:])
    except Exception as error:  # noqa: BLE001 - сторож не должен мешать запуску
        _log("ошибка сторожа: %r" % (error,), say)
        return False


def explain() -> str:
    """Текст для окна: что не так и что делать."""
    suggested = ""
    try:
        for exe in candidates():
            if FREE_NAME.match(os.path.basename(exe)):
                continue
            if probe(exe, timeout=20)[0]:
                suggested = exe
                break
    except Exception:  # noqa: BLE001
        pass
    lines = [
        "Программа запущена интерпретатором, который не подходит для PyQt6.",
        "",
        "Сейчас используется:",
        "  " + (sys.executable or "?"),
        "  Python " + sys.version.split()[0] + " — сборка free-threaded (без GIL).",
        "",
        "PyQt6 в такой сборке падает с нарушением доступа (0xC0000005). Это не",
        "ошибка программы и кодом не лечится: нужен обычный Python (с GIL).",
        "Поэтому окно не открывается, а папка crash_reports остаётся пустой.",
    ]
    if suggested:
        lines += [
            "",
            "Найден подходящий интерпретатор:",
            "  " + suggested,
            "",
            "Проверь, что кнопка программы ведёт именно на него, либо запусти так:",
            '  "%s" "%s"' % (suggested, os.path.abspath(sys.argv[0])),
        ]
    else:
        lines += [
            "",
            "Подходящий интерпретатор на этом компьютере не найден.",
            "Что делать:",
            "  1. Установи обычный Python 3.11+ (64-бит: x64 либо ARM64 для",
            "     Windows 11 ARM64) и в установщике НЕ включай «free-threaded»",
            "     (сборка без GIL) и не выбирай 32-битную версию.",
            "  2. Затем: pip install -r docs/requirements.txt и запусти start.pyw.",
        ]
    lines += [
        "",
        "Как исправить привязку .pyw:",
        "  Правка → Свойства файла start.pyw → «Программа»: pythonw.exe обычной",
        "  сборки, а не pythonw3.14t.exe.",
    ]
    return "\n".join(lines)


def describe() -> str:
    """Короткий отчёт для --diagnose."""
    lines = [
        "интерпретатор: " + (sys.executable or "?"),
        "архитектура: " + architecture() + ("" if is_64bit()
                                            else "  <-- нужен 64-битный Python"),
        "сборка free-threaded: " + ("ДА (PyQt6 не работает)" if is_free_threaded()
                                    else "нет (обычная, с GIL)"),
        "перезапусков сторожа: " + str(_relaunch_count()),
        "кандидаты:",
    ]
    for exe in candidates()[:MAX_PROBES]:
        lines.append("  " + exe)
    cached = _load_cache()
    if cached:
        lines.append("из кэша: " + cached)
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--probe" in argv:
        for exe in candidates():
            works, version = probe(exe)
            print(("  [ok]   " if works else "  [нет]  ") + exe + " " + version)
        return 0
    print(describe())
    return 0


if __name__ == "__main__":
    sys.exit(main())
