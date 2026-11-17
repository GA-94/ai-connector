"""Краш-репорты: папка crash_reports рядом с программой.

Почему в 0.6.3 папка после вылета оставалась пустой:

  * отчёт о жёстком падении создавался только при СЛЕДУЮЩЕМ запуске
    (`_harvest_previous`), то есть сразу после вылета в папке было пусто;
  * `faulthandler` писал в файл сессии, но если процесс убивали через
    TerminateProcess / OOM-киллер / SIGKILL, файл оставался нулевого размера;
  * перехват включался ПОСЛЕ импорта PyQt6 и создания QApplication в
    main.pyw, а `standalone`-импорт `app.ui.main_window` падал вообще без
    отчёта;
  * необработанное исключение в слоте PyQt6 приводит к `qFatal()` -> `abort()`,
    а обработчик фатальных сообщений Qt писал отчёт только при `mode >= 3`,
    что в PyQt6 не всегда срабатывало.

Что сделано в 0.6.4:

  * при старте сразу создаётся папка сессии `crash_reports/session_*` с
    файлом состояния и дампом faulthandler — после падения она уже содержит
    стек, отчёт не нужно ждать до следующего запуска;
  * журнал (последние строки) сбрасывается на диск не реже раза в секунду;
  * рядом работает сторож — отдельный процесс, который ждёт смерти основного
    и превращает папку сессии в отчёт `crash_*` даже при SIGKILL/OOM
    (сообщает код завершения);
  * нормальный выход помечается маркером `.closed_*`, поэтому штатное
    закрытие не считается падением;
  * перехватываются `sys.excepthook`, `threading.excepthook`,
    `sys.unraisablehook`, фатальные сообщения Qt и SIGSEGV/SIGABRT;
  * одинаковые исключения (одна ошибка в цикле отрисовки) не создают сотню
    папок — отчёт один, дальше только строки журнала.

Ни один путь в этом модуле не должен ронять программу: всё в try/except.
"""

import atexit
import collections
import datetime
import faulthandler
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

REPORT_DIRNAME = "crash_reports"
SESSION_PREFIX = "session_"
STATE_NAME = "state.txt"
FAULT_NAME = "faulthandler.txt"
JOURNAL_NAME = "journal.txt"
BOOT_NAME = "boot.log"
CLEAN_PREFIX = ".closed_"
BREADCRUMBS = 200
TAIL_CHARS = 20000
FLUSH_INTERVAL = 0.4
DEDUPE_WINDOW = 20.0

_lock = threading.Lock()
_bread = collections.deque(maxlen=BREADCRUMBS)
_installed = False
_fault_handle = None
_session_dir = None
_clean_path = None
_system_block = ""
_last_flush = 0.0
_journal_handle = None
_dedupe = {}
_shown = False
_watchdog = None


# ------------------------------------------------------------------ пути

def project_dir() -> Path:
    """Папка программы (app/crash.py -> корень проекта)."""
    return Path(__file__).resolve().parent.parent


def reports_root() -> Path:
    root = project_dir() / REPORT_DIRNAME
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path(os.path.abspath(REPORT_DIRNAME))
    return root


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _new_folder(prefix: str = "crash_") -> Path:
    root = reports_root()
    base = f"{prefix}{_stamp()}_{os.getpid()}"
    folder = root / base
    index = 2
    while True:
        try:
            folder.mkdir(parents=True, exist_ok=False)
            return folder
        except FileExistsError:
            folder = root / f"{base}_{index}"
            index += 1
        except OSError:
            return root


# ------------------------------------------------------------- «хлебные крошки»

def log_line(text) -> None:
    """Запомнить строку журнала: она попадёт в следующий краш-репорт."""
    try:
        clean = " ".join(str(text or "").split())
        if not clean:
            return
        line = datetime.datetime.now().strftime("%H:%M:%S") + " " + clean[:600]
        _bread.append(line)
        # Журнал пишем в файл построчно: файл не держит состояние в памяти, и
        # после SIGKILL / TerminateProcess / OOM последние шаги остаются на
        # диске. Дописывание одной строки дешевле, чем перезапись состояния.
        _write_journal(line)
    except Exception:  # noqa: BLE001 - журнал не должен мешать работать
        pass


def _flush_state(force: bool = False) -> None:
    """Записать состояние сессии на диск (переживает любую смерть процесса)."""
    global _last_flush
    if not _session_dir:
        return
    if not force and time.time() - _last_flush < FLUSH_INTERVAL / 2:
        return
    with _lock:
        _last_flush = time.time()
        try:
            text = "\n".join([
                (_system_block or "СИСТЕМА: (нет данных)"),
                "",
                "ПОСЛЕДНИЕ СТРОКИ ЖУРНАЛА",
                breadcrumbs(),
            ]) + "\n"
            tmp = Path(str(_session_dir)) / (STATE_NAME + ".tmp")
            tmp.write_text(text, encoding="utf-8", errors="replace")
            os.replace(str(tmp), str(Path(str(_session_dir)) / STATE_NAME))
        except Exception:  # noqa: BLE001
            pass


def breadcrumbs() -> str:
    if not _bread:
        return "  (журнал пуст)"
    return "\n".join("  " + line for line in list(_bread))


# ------------------------------------------------------- журнал на диске

def _open_journal() -> None:
    """Открыть построчный журнал в папке сессии (дописывание, сброс на каждой строке)."""
    global _journal_handle
    _close_journal()
    if not _session_dir:
        return
    try:
        _journal_handle = open(str(Path(str(_session_dir)) / JOURNAL_NAME), "a",
                               encoding="utf-8", errors="replace", buffering=1)
    except Exception:  # noqa: BLE001
        _journal_handle = None


def _close_journal() -> None:
    global _journal_handle
    handle = _journal_handle
    _journal_handle = None
    if handle is None:
        return
    try:
        handle.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        handle.close()
    except Exception:  # noqa: BLE001
        pass


def _write_journal(line: str) -> None:
    handle = _journal_handle
    if handle is None:
        return
    try:
        handle.write(line + "\n")
        handle.flush()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- информация

def system_info() -> str:
    lines = [
        "СИСТЕМА",
        "  время: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "  ОС: " + platform.platform(),
        "  python: " + sys.version.split()[0] + " (" + (sys.executable or "?") + ")",
        "  папка: " + str(project_dir()),
        "  pid: " + str(os.getpid()),
        "  argv: " + " ".join(sys.argv),
    ]
    try:
        from app import __version__, APP_NAME
        lines.insert(1, f"  программа: {APP_NAME} v{__version__}")
    except Exception:  # noqa: BLE001
        pass
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "QT_QPA_PLATFORM",
                "AI_CONNECTOR_OS", "AI_CONNECTOR_LAUNCHER", "PYTHONIOENCODING",
                "LANG", "LC_ALL"):
        lines.append(f"  {key}={os.environ.get(key, '(нет)')}")
    try:
        from app import runtime_env
        lines.append("  дистрибутив: " + runtime_env.distro_name())
        lines.append("  платформа: " + runtime_env.OS_NAME)
    except Exception:  # noqa: BLE001
        pass
    try:
        from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        from PyQt5.QtWidgets import QApplication
        lines.append(f"  Qt: {QT_VERSION_STR} | PyQt: {PYQT_VERSION_STR}")
        app = QApplication.instance()
        if app is not None:
            lines.append("  Qt-платформа: " + (app.platformName() or "?"))
    except Exception:  # noqa: BLE001
        lines.append("  Qt: не загружен")
    try:
        import threading as _threading
        lines.append("  потоков: " + str(_threading.active_count()))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def _threads_dump() -> str:
    """Дамп стека всех потоков — на случай, если faulthandler недоступен."""
    out = []
    try:
        for thread_id, frame in sys._current_frames().items():  # noqa: SLF001
            out.append(f"--- поток {thread_id} ---")
            out.extend(traceback.format_stack(frame))
    except Exception:  # noqa: BLE001
        return "(дамп потоков недоступен)"
    return "".join(out)


# ------------------------------------------------------------------ отчёты

def _read_session_file(name: str) -> str:
    try:
        if not _session_dir:
            return ""
        path = Path(str(_session_dir)) / name
        if not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        return text
    except Exception:  # noqa: BLE001
        return ""


def _boot_tail() -> str:
    """Хвост журнала запуска: start.pyw пишет его ещё до включения краш-системы."""
    try:
        path = reports_root() / BOOT_NAME
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[-TAIL_CHARS:]
    except Exception:  # noqa: BLE001 - отчёт не должен падать из-за журнала
        return ""


def _finalize_dir(folder_dir, title: str, body: str = "", source: str = "",
                  prefix: str = "crash_") -> str:
    """Из папки сессии сделать папку отчёта crash_* с report.txt."""
    folder = _new_folder(prefix)
    try:
        state_text = ""
        fault_text = ""
        journal_text = ""
        try:
            source_dir = Path(str(folder_dir))
            if source_dir.is_dir():
                for name in (STATE_NAME, FAULT_NAME, JOURNAL_NAME):
                    path = source_dir / name
                    if path.is_file():
                        text = path.read_text(encoding="utf-8", errors="replace")
                        if name == STATE_NAME:
                            state_text = text
                        elif name == FAULT_NAME:
                            fault_text = text
                        else:
                            journal_text = text
                        try:
                            shutil.copy2(str(path), str(folder / name))
                        except OSError:
                            pass
        except Exception:  # noqa: BLE001
            pass

        parts = [
            "AI-CONNECTOR: ОТЧЁТ О СБОЕ",
            "=" * 60,
            "событие: " + (title or "сбой"),
        ]
        if source:
            parts.append("источник: " + source)
        parts.append("")
        parts.append(_system_block or system_info())
        parts.append("")
        parts.append("СОСТОЯНИЕ НА МОМЕНТ СБОЯ")
        parts.append(state_text.strip() or "  (файл состояния не сохранился)")
        parts.append("")
        parts.append("ПОСЛЕДНИЕ ДЕЙСТВИЯ ПРОГРАММЫ (журнал на диске)")
        if journal_text.strip():
            parts.append(journal_text.strip()[-TAIL_CHARS:])
        else:
            parts.append("  (журнал не сохранился)")
        if body:
            parts.append("")
            parts.append("ПОДРОБНОСТИ")
            text = str(body)
            if len(text) > TAIL_CHARS:
                text = "…\n" + text[-TAIL_CHARS:]
            parts.append(text)
        parts.append("")
        parts.append("ЖУРНАЛ ЗАПУСКА (crash_reports/boot.log)")
        boot_text = _boot_tail()
        if boot_text.strip():
            parts.append(boot_text.strip()[-TAIL_CHARS:])
        else:
            parts.append("  (файла boot.log нет)")
        parts.append("")
        parts.append("ДАМП faulthandler (стек на момент сбоя)")
        if fault_text.strip():
            parts.append(fault_text[-TAIL_CHARS:])
        else:
            parts.append("  (faulthandler ничего не записал — процесс убит извне:")
            parts.append("   TerminateProcess, OOM-киллер или SIGKILL. Стек недоступен.)")
        parts.append("")
        parts.append("ЧТО ДЕЛАТЬ")
        parts.append("  1. Открой этот файл целиком — сверху система, ниже последние")
        parts.append("     действия программы перед сбоем.")
        parts.append("  2. Если в конце есть строка 'QThread: Destroyed while thread is")
        parts.append("     still running' — падение при закрытии окна во время работы агента.")
        parts.append("  3. Пришли файл разработчику; папку crash_reports можно удалять.")
        text = "\n".join(parts) + "\n"
        (folder / "report.txt").write_text(text, encoding="utf-8", errors="replace")
        return str(folder)
    except Exception:  # noqa: BLE001 - отчёт не должен падать сам
        try:
            return str(folder)
        except Exception:  # noqa: BLE001
            return ""


def _close_fault() -> None:
    """Закрыть дамп faulthandler: на Windows открытый файл нельзя удалить."""
    global _fault_handle
    handle = _fault_handle
    _fault_handle = None
    if handle is None:
        return
    try:
        faulthandler.disable()
    except Exception:  # noqa: BLE001
        pass
    try:
        handle.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        time.sleep(0.05)
    except Exception:  # noqa: BLE001
        pass


def _open_fault() -> None:
    """(Пере)открыть дамп faulthandler в текущей папке сессии."""
    global _fault_handle
    if not _session_dir:
        return
    try:
        _fault_handle = open(str(Path(str(_session_dir)) / FAULT_NAME), "a",
                             encoding="utf-8", errors="replace")
        faulthandler.enable(file=_fault_handle, all_threads=True)
    except Exception:  # noqa: BLE001
        _fault_handle = None


def _start_new_session() -> None:
    """Начать новую папку сессии (после того как отчёт уже записан)."""
    global _session_dir, _system_block
    try:
        _session_dir = (reports_root() / (SESSION_PREFIX + _stamp() + "_"
                                          + str(os.getpid())))
        _session_dir.mkdir(parents=True, exist_ok=True)
        if not _system_block:
            _system_block = system_info()
    except Exception:  # noqa: BLE001
        _session_dir = None
    _open_fault()
    _open_journal()
    _flush_state(force=True)


def _finalize_session(title: str, body: str = "", source: str = "",
                      prefix: str = "crash_") -> str:
    """Превратить текущую папку сессии в отчёт и начать новую сессию."""
    global _session_dir
    folder_dir = _session_dir
    _session_dir = None
    # Сначала закрываем дамп: Windows не удаляет открытый файл, из-за этого
    # папка сессии оставалась и превращалась в ложный «сбой» при следующем
    # запуске.
    _close_fault()
    _close_journal()
    result = _finalize_dir(folder_dir, title, body, source, prefix)
    if folder_dir:
        try:
            shutil.rmtree(str(folder_dir), ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
    # Перехват продолжает работать: следующая ошибка тоже попадёт в отчёт.
    try:
        _start_new_session()
    except Exception:  # noqa: BLE001
        pass
    return result


def write_report(title: str, body: str = "", source: str = "") -> str:
    """Создать папку отчёта и записать report.txt. -> путь к папке ('')."""
    try:
        return _finalize_dir(_session_dir, title, body, source)
    except Exception:  # noqa: BLE001
        return ""


def capture(exc_type, exc, tb, title: str = "Необработанное исключение",
            source: str = "") -> str:
    """Записать исключение в отчёт. -> путь к папке отчёта."""
    try:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:  # noqa: BLE001
        text = f"{exc_type}: {exc}"
    name = getattr(exc_type, "__name__", "?")
    message = str(exc)
    log_line(f"[crash] {title}: {name}: {message}")
    try:
        _flush_state(force=True)
    except Exception:  # noqa: BLE001
        pass
    # Одна и та же ошибка в цикле отрисовки не должна создавать 100 папок.
    key = (name, message[:200], source)
    now = time.time()
    previous = _dedupe.get(key)
    if previous and now - previous < DEDUPE_WINDOW:
        return ""
    _dedupe[key] = now
    return _finalize_session(title, text, source)


def _show_message(path: str, title: str, summary: str) -> None:
    """Показать окно с сообщением (только если есть GUI и мы в главном потоке)."""
    global _shown
    if _shown:
        return
    _shown = True
    try:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("не главный поток")
        from PyQt5.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("нет QApplication")
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(title)
        box.setText("Программа обнаружила сбой.")
        box.setInformativeText(
            (summary + "\n\n" if summary else "") +
            ("Отчёт сохранён:\n" + path if path else
             "Отчёт сохранить не удалось — смотри консоль."))
        open_btn = box.addButton("Открыть папку отчётов", QMessageBox.ActionRole)
        box.addButton("Закрыть", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is open_btn:
            try:
                from app.runtime_env import open_in_file_manager
                open_in_file_manager(path or str(reports_root()))
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        _shown = False


# ------------------------------------------------------------------ установка

def _excepthook(exc_type, exc, tb) -> None:
    folder = capture(exc_type, exc, tb, source="sys.excepthook")
    try:
        print("".join(traceback.format_exception(exc_type, exc, tb)),
              file=sys.stderr)
        if folder:
            print("Отчёт о сбое: " + folder, file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass
    _show_message(folder, "AiConnector7 — сбой",
                  f"{getattr(exc_type, '__name__', 'Ошибка')}: {exc}")


def _thread_excepthook(args) -> None:
    if getattr(args, "exc_type", None) is None:
        return
    folder = capture(args.exc_type, args.exc_value, args.exc_traceback,
                     title="Исключение в фоновом потоке",
                     source=f"threading ({getattr(args.thread, 'name', '?')})")
    try:
        print(f"Сбой в потоке {getattr(args.thread, 'name', '?')}: {args.exc_value}",
              file=sys.stderr)
        if folder:
            print("Отчёт о сбое: " + folder, file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def _unraisablehook(args) -> None:
    """Исключения, которые Python не может пробросить (в __del__, в деструкторе)."""
    exc_type = getattr(args, "exc_type", None) or RuntimeError
    exc_value = getattr(args, "exc_value", None)
    exc_tb = getattr(args, "exc_traceback", None)
    where = getattr(args, "object", None)
    folder = capture(exc_type, exc_value, exc_tb,
                     title="Ошибка в деструкторе (unraisable)",
                     source=f"sys.unraisablehook ({where!r})")
    try:
        print(f"Необрабатываемая ошибка: {exc_value}", file=sys.stderr)
        if folder:
            print("Отчёт о сбое: " + folder, file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def _install_qt_handler() -> None:
    try:
        from PyQt5.QtCore import qInstallMessageHandler
    except Exception:  # noqa: BLE001 - без PyQt5 программа всё равно стартует
        return

    def handler(mode, _context, message):
        text = str(message or "")
        log_line("[qt] " + text)
        # QtFatalMsg == 3: дальше Qt вызывает abort(). Отчёт пишем ДО этого.
        try:
            if int(mode) >= 3:
                _flush_state(force=True)
                _finalize_session("Фатальное сообщение Qt", text,
                                  source="qInstallMessageHandler")
        except Exception:  # noqa: BLE001
            pass
        return None

    try:
        qInstallMessageHandler(handler)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ сторож

def _wait_process(pid: int, max_seconds: float = 12 * 3600) -> str:
    """Ждать смерти процесса. -> человекочитаемая причина/код завершения."""
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            QUERY = 0x1000
            handle = kernel32.OpenProcess(SYNCHRONIZE | QUERY, False, int(pid))
            if not handle:
                return "не удалось открыть процесс (возможно, уже завершился)"
            try:
                kernel32.WaitForSingleObject(handle, int(max_seconds * 1000))
                code = ctypes.c_ulong(0)
                kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return f"код завершения процесса: {code.value}"
            finally:
                kernel32.CloseHandle(handle)
        except Exception as error:  # noqa: BLE001
            return f"не удалось дождаться процесса: {error}"
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return "процесс завершился (код на Linux не читается)"
        except PermissionError:
            pass
        except OSError:
            return "процесс завершился"
        time.sleep(1.0)
    return "ожидание истекло (процесс жив 12 часов)"


def _watchdog_main(argv) -> int:
    """Отдельный процесс: ждёт смерть основного и пишет отчёт, если она не штатная.

    argv: pid, папка отчётов, файл-маркер нормального выхода.
    Папок сессии у одного pid может быть несколько (после перехваченного
    исключения начинается новая), поэтому ищем их по шаблону session_*_<pid>.
    """
    try:
        pid = int(argv[0])
        root = Path(argv[1])
        clean_marker = Path(argv[2])
    except Exception:  # noqa: BLE001
        return 2
    reason = _wait_process(pid)
    time.sleep(0.5)
    try:
        if clean_marker.exists():
            clean_marker.unlink()
            return 0
    except Exception:  # noqa: BLE001
        pass
    try:
        folders = [item for item in root.glob(SESSION_PREFIX + "*_" + str(pid))
                   if item.is_dir()]
    except Exception:  # noqa: BLE001
        folders = []
    for folder in folders:
        _finalize_dir(folder, "Жёсткое падение (сработал сторож)",
                      body=("Программа завершилась аварийно и не успела записать "
                            "отчёт сама.\nСторож (отдельный процесс) зафиксировал: "
                            + reason + "\n"),
                      source="app.crash --watch", prefix="crash_hard_")
        try:
            shutil.rmtree(str(folder), ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
    return 0


def _start_watchdog() -> None:
    """Запустить сторожа (не критично: без него отчёт появится при следующем старте)."""
    global _watchdog
    if os.environ.get("AI_CONNECTOR_NO_WATCHDOG") == "1":
        return
    if not _session_dir or not _clean_path:
        return
    try:
        exe = sys.executable
        if not exe or not os.path.exists(exe):
            return
        script = str(Path(__file__).resolve())
        args = [exe, script, "--watch", str(os.getpid()),
                str(reports_root()), str(_clean_path)]
        kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, close_fds=True)
        if os.name == "nt":
            # CREATE_NO_WINDOW | DETACHED_PROCESS: никакой консоли поверх игры.
            kwargs["creationflags"] = 0x08000000 | 0x00000008
        else:
            kwargs["start_new_session"] = True
        env = dict(os.environ)
        env["AI_CONNECTOR_NO_WATCHDOG"] = "1"
        root = str(project_dir())
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"]
                                    if env.get("PYTHONPATH") else "")
        _watchdog = subprocess.Popen(args, cwd=root, env=env, **kwargs)
    except Exception:  # noqa: BLE001
        _watchdog = None


# ------------------------------------------------------------- сбор прошлого

def _clean_marker_for(pid) -> bool:
    """Есть ли маркер нормального выхода для этого pid."""
    try:
        return any(reports_root().glob(CLEAN_PREFIX + str(pid) + "_*"))
    except Exception:  # noqa: BLE001
        return False


def _harvest_previous() -> None:
    """Незакрытые сессии прошлых запусков -> папки отчётов."""
    try:
        root = reports_root()
    except Exception:  # noqa: BLE001
        return
    current = str(_session_dir or "")
    try:
        for item in sorted(root.glob(SESSION_PREFIX + "*")):
            if not item.is_dir() or str(item) == current:
                continue
            # Процесс вышел нормально: остатки его папок - это уже
            # обработанные ошибки, отчёт по ним не нужен (иначе одна и та же
            # ошибка давала по 6 папок crash_hard_* на один pid).
            try:
                pid = int(item.name.rsplit("_", 1)[1])
            except (IndexError, ValueError):
                pid = None
            if pid is not None and _clean_marker_for(pid):
                try:
                    shutil.rmtree(str(item), ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass
                continue
            _finalize_dir(item, "Прошлый запуск завершился аварийно",
                          body=("Маркер нормального выхода не найден: программа "
                                "завершилась без atexit (жёсткое падение, "
                                "TerminateProcess или OOM).\n"),
                          source="app.crash (при следующем запуске)",
                          prefix="crash_hard_")
            try:
                shutil.rmtree(str(item), ignore_errors=True)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
    # Старые маркеры «нормального выхода» можно убрать.
    try:
        for mark in root.glob(CLEAN_PREFIX + "*"):
            try:
                if time.time() - mark.stat().st_mtime > 86400:
                    mark.unlink()
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


def mark_clean() -> None:
    """Отметить выход нормальным (для принудительного os._exit)."""
    global _fault_handle
    try:
        _flush_state(force=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        if _clean_path:
            Path(str(_clean_path)).write_text("clean " + _stamp(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    _close_fault()
    _close_journal()
    # Убираем все папки сессий этого pid: после перехваченной ошибки могла
    # начаться новая сессия, и старая осталась бы мусором.
    try:
        for item in reports_root().glob(SESSION_PREFIX + "*_" + str(os.getpid())):
            shutil.rmtree(str(item), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        if _session_dir:
            shutil.rmtree(str(_session_dir), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _cleanup() -> None:
    """Нормальный выход: помечаем сессию закрытой и убираем её папку."""
    mark_clean()


def install(source: str = "app.crash") -> bool:
    """Включить перехват сбоев. Вызывать как можно раньше при старте."""
    global _installed, _fault_handle, _session_dir, _clean_path, _system_block
    if _installed:
        return True
    _installed = True
    try:
        _session_dir = (reports_root() / (SESSION_PREFIX + _stamp() + "_"
                                          + str(os.getpid())))
        _session_dir.mkdir(parents=True, exist_ok=True)
        _clean_path = reports_root() / (CLEAN_PREFIX + str(os.getpid()) + "_" + _stamp())
        _system_block = system_info()
    except Exception:  # noqa: BLE001
        _session_dir = None
        _clean_path = None
    try:
        _harvest_previous()
    except Exception:  # noqa: BLE001
        pass
    try:
        _open_fault()
    except Exception:  # noqa: BLE001
        _fault_handle = None
    _open_journal()
    try:
        sys.excepthook = _excepthook
    except Exception:  # noqa: BLE001
        pass
    try:
        threading.excepthook = _thread_excepthook
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.unraisablehook = _unraisablehook
    except Exception:  # noqa: BLE001
        pass
    _install_qt_handler()
    try:
        atexit.register(_cleanup)
    except Exception:  # noqa: BLE001
        pass
    log_line(f"[crash] перехват включён ({source}), папка отчётов: {reports_root()}")
    _flush_state(force=True)
    _start_watchdog()
    return True


# ------------------------------------------------------------------- прочее

def last_reports(limit: int = 5) -> list:
    """Последние папки отчётов (новые первыми)."""
    try:
        folders = [item for item in reports_root().iterdir()
                   if item.is_dir() and not item.name.startswith(SESSION_PREFIX)]
        folders.sort(key=lambda item: item.name, reverse=True)
        return [str(item) for item in folders[:max(1, limit)]]
    except OSError:
        return []


def dump_threads() -> str:
    """Ручной дамп стеков (для отладки и кнопки «Снять дамп»)."""
    _flush_state(force=True)
    return _threads_dump()


def session_folder() -> str:
    """Папка текущей сессии (для диагностики)."""
    return str(_session_dir or "")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--watch":
        return _watchdog_main(argv[1:])
    print("app.crash: служебный модуль краш-репортов.")
    print("  папка отчётов: " + str(reports_root()))
    reports = last_reports(5)
    if not reports:
        print("  отчётов нет")
    for item in reports:
        print("  " + item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
