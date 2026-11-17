r"""Среда выполнения AI-Connector: какая ОС, как хранить пути, как открывать папки.

Одна точка правды для двух важных вещей:

1. ПУТИ. На Linux/BSD обратный слэш (\) путями не считается, а символ возврата
   каретки (\r, он же ^M) приезжает из Windows-файлов и настроек и ломает
   открытие папок. Поэтому внутри программы путь всегда нормализуется:
   прямые слэши "/", без ^M/NUL/BOM, без двойных и хвостовых слэшей.
2. ОС. И программа, и ИИ-агент должны знать, где они работают. Для агента
   есть готовый блок describe_for_prompt() - он добавляется в system prompt.

Модуль намеренно не импортирует PyQt6: его использует и стартер, и тесты.
"""

import os
import re
import shutil
import subprocess
import sys

_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
_MULTI_SLASH_RE = re.compile(r"/{2,}")
_JUNK = ("\x00", "\r", "\n", "\ufeff")


def detect_os() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("darwin"):
        return "macos"
    if sys.platform.startswith(("linux", "freebsd", "openbsd", "netbsd", "dragonfly")):
        return "linux"
    return "posix"


OS_NAME = detect_os()
IS_WINDOWS = OS_NAME == "windows"
IS_LINUX = OS_NAME == "linux"
IS_MACOS = OS_NAME == "macos"
IS_POSIX = os.name == "posix"

OS_LABEL = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}.get(OS_NAME, OS_NAME)
PATH_SEPARATOR = "/"
PYTHON_NAME = "python" if IS_WINDOWS else "python3"
SHELL_NAME = "cmd/PowerShell" if IS_WINDOWS else "sh/bash"


# --------------------------------------------------------------------- ОС

def distro_name() -> str:
    """Человеческое имя дистрибутива (Debian, Arch, Fedora...) для отчётов."""
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "macOS"
    try:
        with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return OS_LABEL


def env_flags() -> dict:
    """Флаги, которые стартер выставляет процессу: по ним видно, кто запустил."""
    return {
        "AI_CONNECTOR_OS": OS_NAME,
        "AI_CONNECTOR_LAUNCHER": os.environ.get("AI_CONNECTOR_LAUNCHER", ""),
    }


def architecture() -> str:
    """Архитектура интерпретатора: "arm64, 64-бит", "amd64, 64-бит" и т. п.

    Нужна и человеку (диагностика), и агенту: под Windows 11 ARM64 другой
    интерпретатор, другие команды сборки и другие колёса пакетов.
    """
    try:
        import platform
        import struct

        return "%s, %d-бит" % ((platform.machine() or "?").lower(),
                               struct.calcsize("P") * 8)
    except Exception:  # noqa: BLE001
        return "неизвестна"


def is_arm() -> bool:
    """ARM-процессор (Windows 11 ARM64, Apple Silicon, Raspberry Pi)?"""
    try:
        import platform

        return (platform.machine() or "").lower() in (
            "arm64", "aarch64", "arm", "armv7l", "armv8l")
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------- запуск процессов без окна консоли

# На Windows каждый subprocess без флагов открывает своё окно консоли. Когда
# агент запускает python_run или gradlew, поверх игры выскакивает чёрное окно,
# и игра теряет фокус. CREATE_NO_WINDOW + SW_HIDE убирают это окно совсем
# (на Linux/macOS таких окон нет, поэтому там kwargs пустые).
CREATE_NO_WINDOW = 0x08000000
_SW_HIDE = 0


def silent_kwargs() -> dict:
    """Аргументы subprocess, чтобы дочерний процесс не открывал окно."""
    if not IS_WINDOWS:
        return {}
    kwargs = {"creationflags": CREATE_NO_WINDOW}
    startupinfo_class = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_class is not None:
        try:
            startupinfo = startupinfo_class()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
            startupinfo.wShowWindow = _SW_HIDE
            kwargs["startupinfo"] = startupinfo
        except Exception:  # noqa: BLE001 - старые/урезанные сборки Python
            pass
    return kwargs


def silent_run(argv, **kwargs):
    """subprocess.run без окна консоли (см. silent_kwargs)."""
    merged = silent_kwargs()
    merged.update(kwargs)
    return subprocess.run(argv, **merged)


def silent_popen(argv, **kwargs):
    """subprocess.Popen без окна консоли; работает и с shell=True."""
    merged = silent_kwargs()
    merged.update(kwargs)
    return subprocess.Popen(argv, **merged)


# ------------------------------------------------------------------- пути

def clean_path_text(value) -> str:
    """Убрать ^M, NUL, BOM, переводы строк и обрамляющие кавычки."""
    if value is None:
        return ""
    text = str(value)
    for junk in _JUNK:
        text = text.replace(junk, "")
    text = text.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in ("\"", "'"):
        text = text[1:-1].strip()
    return text


def normalize_path(value, base: str = "") -> str:
    """Путь в стиле программы: прямые слэши, без ^M, без лишних слэшей.

    normalize_path("C:\\tmp\\a.txt")      -> "C:/tmp/a.txt"
    normalize_path("a/b/\\r")             -> "a/b"
    normalize_path("src", base="/proj")   -> "/proj/src"
    """
    text = clean_path_text(value)
    if not text:
        return ""
    text = text.replace("\\", "/")
    unc = text.startswith("//")
    if unc:
        text = text[2:]
    text = _MULTI_SLASH_RE.sub("/", text)
    if base and not text.startswith("/") and not _DRIVE_RE.match(text):
        head = normalize_path(base).rstrip("/")
        text = _MULTI_SLASH_RE.sub("/", head + "/" + text)
    if len(text) > 2 and text.endswith("/") and not _DRIVE_RE.match(text):
        text = text.rstrip("/")
    return ("//" if unc else "") + text


def normalize_roots(values) -> list:
    """Список путей без дублей, в нормализованном виде."""
    if values is None:
        return []
    if isinstance(values, (str, os.PathLike)):
        values = [values]
    out, seen = [], set()
    for value in values:
        path = normalize_path(value)
        if not path:
            continue
        key = path.lower() if IS_WINDOWS else path
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def is_absolute_path(value) -> bool:
    text = normalize_path(value)
    if not text:
        return False
    return text.startswith("/") or bool(_DRIVE_RE.match(text))


def clean_text(value) -> str:
    """Многострочный текст (инструкции, пути) без ^M."""
    if value is None:
        return ""
    return str(value).replace("\x00", "").replace("\r\n", "\n").replace("\r", "")


def shorten_path(value, limit: int = 64) -> str:
    text = normalize_path(value)
    if len(text) <= limit:
        return text
    return "…" + text[-(limit - 1):]


# ------------------------------------------------------- открытие папок

def open_in_file_manager(path) -> tuple:
    """Открыть папку/файл системным менеджером. -> (ok, сообщение об ошибке)."""
    target = normalize_path(path)
    if not target:
        return False, "Путь не задан"
    if not os.path.exists(target):
        return False, f"Путь не существует: {target}"
    try:
        if IS_WINDOWS:
            os.startfile(target)  # type: ignore[attr-defined]
            return True, ""
        opener = "open" if IS_MACOS else "xdg-open"
        if shutil.which(opener) is None:
            return False, f"Не найдена команда {opener} (поставь xdg-utils)"
        subprocess.Popen(
            [opener, target],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, ""
    except Exception as error:  # noqa: BLE001 - любая ОС, любой менеджер
        return False, f"{type(error).__name__}: {error}"


def preferred_python() -> str:
    """Интерпретатор без консольного окна там, где это возможно."""
    if IS_WINDOWS:
        base = getattr(sys, "base_prefix", None) or getattr(sys, "prefix", None)
        if base:
            for name in ("pythonw.exe", "python.exe"):
                candidate = os.path.join(base, name)
                if os.path.exists(candidate):
                    return candidate
    return sys.executable or PYTHON_NAME


# --------------------------------------------------------------- промпт

def describe_for_prompt() -> str:
    """Блок в system prompt: агент знает ОС и правила записи путей."""
    if IS_WINDOWS:
        lines = [
            "ОПЕРАЦИОННАЯ СИСТЕМА (определена стартером): Windows (os.name=nt).",
            "  - В инструменты пиши пути с прямыми слэшами: C:/proj/src/main.py.",
            "  - Обратные слэши тоже понимаются, но в ответах и в коде используй прямой слэш.",
            "  - Команды оболочки: cmd/PowerShell; интерпретатор: python.",
            "  - Перевод строки в файлах: CRLF допустим, но не пихай ^M в пути.",
            f"  - Архитектура: {architecture()}" + (
                " (Windows on ARM: нативные сборки ARM64, x64 идёт через эмуляцию;"
                " PyQt5 здесь недоступен, интерфейс на PyQt6)."
                if is_arm() else ""),
        ]
    else:
        lines = [
            f"ОПЕРАЦИОННАЯ СИСТЕМА (определена стартером): {distro_name()} "
            f"(os.name={os.name}, платформа={OS_NAME}).",
            "  - Пути ТОЛЬКО с прямыми слэшами: src/main.py, /home/user/proj.",
            "  - Обратный слэш и буквы дисков (C:) на этой ОС путями не считаются:",
            "    такие пути не откроются, даже если файл существует.",
            "  - Символ ^M (\\r) в путях и именах запрещён: он ломает открытие файлов.",
            "  - Домашняя папка: ~ (os.path.expanduser), корень: /.",
            "  - Команды оболочки: sh/bash; интерпретатор: python3, а не python.",
            "  - Windows-API (os.startfile, winreg, ctypes.windll) здесь нет.",
        ]
    lines.append(
        "  - В аргументах инструментов путь пишется как src/main.py, без экранирования слэшей.")
    return "\n".join(lines)
