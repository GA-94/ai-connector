"""Python interpreter access for the agent.

Two tools:
  * python_info - which interpreter is used (path, version, platform);
  * python_run  - run inline code or a script from the project, with the
                  project folder as the working directory, capturing
                  stdout/stderr and the exit code.

Nothing runs when the user turns the switch off (Настройки → «Инструменты
агента» → Разрешить запуск Python). Only project folders may be used as cwd
or as the script location; the timeout is capped and the output truncated.
"""
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.runtime_env import silent_run

from .safety import is_inside

MAX_OUTPUT_CHARS = 20_000
MAX_TIMEOUT = 600
DEFAULT_TIMEOUT = 120


def _interpreter() -> str:
    """python.exe even when the app itself runs under pythonw.exe."""
    executable = sys.executable or ""
    if executable:
        name = os.path.basename(executable).lower()
        if name.startswith("pythonw"):
            candidate = os.path.join(os.path.dirname(executable), "python.exe")
            if os.path.isfile(candidate):
                return candidate
        if os.path.isfile(executable):
            return executable
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def python_info() -> str:
    executable = _interpreter()
    out = ["PYTHON",
           f"  версия: {sys.version.splitlines()[0]}",
           f"  интерпретатор: {executable or 'не найден'}",
           f"  платформа: {sys.platform}"]
    if not executable:
        out.append("  ВНИМАНИЕ: python не найден в PATH — python_run не сработает.")
    elif os.path.normcase(executable) == os.path.normcase(sys.executable or ""):
        out.append("  Это тот же интерпретатор, на котором запущен AI-Connector.")
    out.append("  Запуск кода включает пользователь: Настройки → Инструменты агента.")
    return "\n".join(out)


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... обрезано, всего {len(text)} символов"


def python_run(root: str = ".", code: str = "", script: str = "", args=None,
               timeout_seconds: int = DEFAULT_TIMEOUT, allow: bool = True,
               env=None) -> str:
    if not allow:
        return ("Запуск Python отключён в настройках "
                "(«Глобальные настройки» → «Инструменты агента» → "
                "«Разрешить запуск Python»). Включи переключатель, если это нужно.")

    executable = _interpreter()
    if not executable:
        return "Error: интерпретатор Python не найден (sys.executable и PATH пусты)."

    root_path = Path(root or ".")
    try:
        root_path = root_path.resolve()
    except OSError:
        pass
    if not root_path.is_dir():
        return f"Error: папка проекта не найдена: {root_path}"

    code = code or ""
    script = (script or "").strip()
    if bool(code.strip()) == bool(script):
        return ("Error: передай ЧТО-ТО ОДНО — code (код) или script "
                "(путь к файлу внутри проекта).")

    argv = [executable]
    if script:
        target = Path(script)
        if not target.is_absolute():
            target = root_path / script
        try:
            target = target.resolve()
        except OSError:
            pass
        if not is_inside(target, root_path):
            return f"Error: script вне проекта ({target}). Разрешено только внутри проекта."
        if not target.is_file():
            return f"Error: скрипт не найден: {target}"
        argv.append(str(target))
        shown = f"python {target.name}"
    else:
        argv += ["-c", code]
        shown = "python -c <code>"

    if isinstance(args, str):
        extra = shlex.split(args) if args.strip() else []
    else:
        extra = [str(item) for item in (args or [])]
    argv += extra

    environment = os.environ.copy()
    environment.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    for key, value in (env or {}).items():
        environment[str(key)] = str(value)

    try:
        timeout = int(timeout_seconds or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(1, min(timeout, MAX_TIMEOUT))

    started = time.time()
    try:
        # silent_run: на Windows без окна консоли — иначе поверх игры
        # выскакивает чёрное окно и забирает фокус.
        process = silent_run(
            argv,
            cwd=str(root_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        partial = ""
        for stream in (error.stdout, error.stderr):
            if stream:
                partial += stream if isinstance(stream, str) else stream.decode("utf-8", "replace")
        return (f"TIMEOUT после {timeout}s: процесс остановлен.\n"
                f"КОМАНДА: {shown}\n"
                f"РАБОЧАЯ ПАПКА: {root_path}\n"
                f"--- ЧАСТИЧНЫЙ ВЫВОД ---\n{_clip(partial)}")
    except OSError as error:
        return f"Error launching python: {error}"

    elapsed = time.time() - started
    out = _clip(process.stdout or "")
    err = _clip(process.stderr or "")
    parts = [f"КОМАНДА: {shown}",
             f"РАБОЧАЯ ПАПКА: {root_path}",
             f"КОД ВОЗВРАТА: {process.returncode} ({elapsed:.1f}s)",
             "--- STDOUT ---",
             out.strip() if out.strip() else "(пусто)",
             "--- STDERR ---",
             err.strip() if err.strip() else "(пусто)"]
    if process.returncode != 0:
        parts.append("Подсказка: код завершился с ошибкой — читай STDERR выше.")
    return "\n".join(parts)
