"""Сервер Ollama: где установлен, как запустить/перезапустить, что он умеет.

Зачем отдельный модуль: настройками сервера управляют переменные окружения и
аргументы `ollama serve`. Ai-Connector умеет поднять сервер сам — с нужной
длиной контекста (OLLAMA_CONTEXT_LENGTH), временем удержания модели
(OLLAMA_KEEP_ALIVE) и адресом (OLLAMA_HOST), — а не надеяться на то, что
пользователь запустил всё правильно.

На Windows сервер обычно поднимает трей «Ollama app.exe»: он запускает
`ollama.exe serve` БЕЗ аргументов и сам перезапускает его, если процесс убить.
Поэтому «перезапуск с настройками» = остановить и трей, и serve, а потом
запустить serve самостоятельно (вывод пишем в data/ollama_serve.log).

Важно про эмбеддинги: в свежих сборках Ollama (проверено на 0.40.16, Windows
ARM64) флага --embeddings уже НЕТ — `ollama serve --embeddings` падает с
«unknown flag: --embeddings». Эмбеддинги работают, как только скачана
embedding-модель (nomic-embed-text). Поэтому флаги по умолчанию пустые.

Только stdlib: модуль вызывается и из фоновых потоков интерфейса.
"""

import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request

from app.agent import ollama
from app.runtime_env import silent_popen, silent_run

# Дополнительные аргументы `ollama serve`. Пусто — обычный запуск.
# (Флаг --embeddings из старых версий в новых сборках удалён.)
DEFAULT_FLAGS = ""

# Модель эмбеддингов: маленькая (274 МБ), нормально работает на CPU.
# На Windows ARM64 это обычная ggml-модель без CUDA — запускается.
EMBED_MODEL = "nomic-embed-text"
EMBED_MODEL_SIZE = "около 274 МБ"

# Сколько ждать, пока сервер поднимется / опустится.
START_TIMEOUT = 30.0
STOP_TIMEOUT = 12.0


def _decode_kwargs() -> dict:
    """Вывод Windows-консолей бывает в cp866 — читаем без падений."""
    return {"capture_output": True, "text": True,
            "encoding": "utf-8", "errors": "replace"}


# --------------------------------------------------------------- где лежит

def find_exe() -> str:
    """Полный путь к ollama(.exe) или "" если не установлена."""
    found = shutil.which("ollama")
    if found:
        return found
    system = platform.system().lower()
    candidates = []
    if system.startswith("win"):
        local = os.environ.get("LOCALAPPDATA") or ""
        program = os.environ.get("ProgramFiles") or ""
        program86 = os.environ.get("ProgramFiles(x86)") or ""
        for base in (local, program, program86):
            if not base:
                continue
            candidates.append(os.path.join(base, "Programs", "Ollama", "ollama.exe"))
            candidates.append(os.path.join(base, "Ollama", "ollama.exe"))
    elif system == "darwin":
        candidates += [
            "/Applications/Ollama.app/Contents/Resources/ollama",
            "/opt/homebrew/bin/ollama",
            "/usr/local/bin/ollama",
        ]
    else:
        candidates += ["/usr/local/bin/ollama", "/usr/bin/ollama",
                       "/snap/bin/ollama", os.path.expanduser("~/.local/bin/ollama")]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def installed_version(exe: str = "") -> str:
    """Версия установленной Ollama («0.40.16») или текст ошибки."""
    path = exe or find_exe()
    if not path:
        return ""
    try:
        result = silent_run([path, "--version"], timeout=25, **_decode_kwargs())
        text = (result.stdout or result.stderr or "").strip()
    except Exception as error:  # noqa: BLE001
        return f"не удалось узнать версию: {error}"
    # вывод вида «ollama version is 0.40.16»
    for token in text.replace("\n", " ").split():
        if token[:1].isdigit() and "." in token:
            return token
    return text[:120]


def log_path() -> str:
    """Куда писать вывод нашего `ollama serve` (data/ollama_serve.log)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "ollama_serve.log")


# --------------------------------------------------------------- процессы

def processes() -> list:
    """Все процессы Ollama: [{'pid', 'name', 'cmdline'}]."""
    result = []
    if platform.system().lower().startswith("win"):
        script = ("$ErrorActionPreference='SilentlyContinue'; "
                  "Get-CimInstance Win32_Process | "
                  "Where-Object { $_.Name -like 'ollama*' } | "
                  "ForEach-Object { \"{0}|{1}|{2}\" -f $_.ProcessId, $_.Name, "
                  "$_.CommandLine }")
        try:
            out = silent_run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                timeout=40, **_decode_kwargs()).stdout or ""
        except Exception:  # noqa: BLE001 - без списка процессов работаем как раньше
            out = ""
        for line in out.splitlines():
            line = line.strip()
            if line.count("|") < 2:
                continue
            raw_pid, name, command = line.split("|", 2)
            try:
                pid = int(raw_pid.strip())
            except (TypeError, ValueError):
                continue
            result.append({"pid": pid, "name": name.strip(), "cmdline": command.strip()})
        return result

    try:
        out = silent_run(["ps", "-eo", "pid=,args="], timeout=20,
                         **_decode_kwargs()).stdout or ""
    except Exception:  # noqa: BLE001
        return result
    for line in out.splitlines():
        line = line.strip()
        if "ollama" not in line.lower():
            continue
        parts = line.split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except (TypeError, ValueError):
            continue
        command = parts[1] if len(parts) > 1 else ""
        name = os.path.basename(command.split()[0]) if command else "ollama"
        result.append({"pid": pid, "name": name, "cmdline": command})
    return result


def _split_processes(items) -> tuple:
    """(serve, tray, прочие) — трей на Windows держит сервер и мешает настройкам."""
    serve, tray, other = [], [], []
    for item in items or []:
        name = str(item.get("name") or "").lower()
        command = str(item.get("cmdline") or "").lower()
        if name.startswith("ollama app") or name.startswith("ollama-app"):
            tray.append(item)
        elif " serve" in (" " + command) or command.endswith(" serve"):
            serve.append(item)
        else:
            other.append(item)
    return serve, tray, other


def server_cmdline() -> str:
    """Командная строка работающего сервера ("" если не запущен)."""
    serve, tray, _other = _split_processes(processes())
    if serve:
        return str(serve[0].get("cmdline") or "")
    return ""


def _kill(pid: int) -> bool:
    try:
        if platform.system().lower().startswith("win"):
            silent_run(["taskkill", "/F", "/PID", str(pid)], timeout=20,
                       **_decode_kwargs())
        else:
            os.kill(int(pid), signal.SIGTERM)
        return True
    except Exception:  # noqa: BLE001
        return False


def _tail(path: str, limit: int = 700) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", "replace").strip()
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------- подъём и остановка

def wait_running(host: str = "", timeout: float = START_TIMEOUT) -> bool:
    """Ждать, пока /api/tags начнёт отвечать."""
    end = time.time() + max(1.0, float(timeout))
    while time.time() < end:
        if ollama.is_running(host, timeout=1.0):
            return True
        time.sleep(0.3)
    return False


def stop(host: str = "", timeout: float = STOP_TIMEOUT) -> tuple:
    """Остановить сервер и трей (иначе трей поднимет serve без наших настроек)."""
    serve, tray, other = _split_processes(processes())
    targets = tray + serve
    if not targets:
        return True, "Сервер Ollama не запущен — останавливать нечего"
    killed = []
    for item in targets:
        if _kill(item["pid"]):
            killed.append(f"{item['name'] or 'ollama'} (PID {item['pid']})")
    if not killed:
        return False, "Не удалось остановить процессы Ollama (нет прав?)"
    end = time.time() + max(2.0, float(timeout))
    while time.time() < end:
        if not ollama.is_running(host, timeout=0.6):
            text = "Остановлено: " + ", ".join(killed)
            if other:
                text += ("\nОстались другие процессы Ollama (их не трогаем): "
                         + ", ".join(f"PID {item['pid']}" for item in other))
            return True, text
        time.sleep(0.3)
    return False, ("Процессы остановлены, но порт всё ещё отвечает: возможно, "
                   "Ollama работает службой Windows. Останови её в службах "
                   "или перезапусти компьютер.")


def server_env(host: str = "", keep_alive: str = "", context_length: int = 0,
               base: dict = None) -> dict:
    """Переменные окружения для сервера: адрес, контекст, удержание модели."""
    env = dict(base if base is not None else os.environ)
    key = ollama.normalize_host(host)
    if key and key not in ("http://127.0.0.1:11434", "http://localhost:11434"):
        env["OLLAMA_HOST"] = key.replace("http://", "").replace("https://", "")
    if keep_alive:
        env["OLLAMA_KEEP_ALIVE"] = str(keep_alive)
    try:
        ctx = int(context_length or 0)
    except (TypeError, ValueError):
        ctx = 0
    if ctx > 0:
        env["OLLAMA_CONTEXT_LENGTH"] = str(ctx)
    return env


def start(host: str = "", flags: str = DEFAULT_FLAGS, keep_alive: str = "",
          context_length: int = 0) -> tuple:
    """Запустить `ollama serve` в фоне (без окна консоли)."""
    exe = find_exe()
    if not exe:
        return False, "Не найдена программа Ollama. " + ollama.start_hint()
    tokens = shlex.split(str(flags or ""))
    argv = [exe, "serve"] + tokens
    env = server_env(host, keep_alive=keep_alive, context_length=context_length)
    path = log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        handle = open(path, "ab", buffering=0)
    except Exception:  # noqa: BLE001 - без лога тоже можно жить
        handle = subprocess.DEVNULL
    kwargs = {}
    if platform.system().lower().startswith("win"):
        # DETACHED_PROCESS: сервер живёт после закрытия Ai-Connector,
        # CREATE_NEW_PROCESS_GROUP: его не убивает Ctrl+C в консоли.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    try:
        process = silent_popen(argv, stdin=subprocess.DEVNULL, stdout=handle,
                               stderr=handle, env=env, **kwargs)
    except Exception as error:  # noqa: BLE001
        return False, f"Не удалось запустить ollama serve: {error}"
    if not wait_running(host, START_TIMEOUT):
        tail = _tail(path)
        return False, (f"ollama serve не поднялся за {int(START_TIMEOUT)} с.\n"
                       f"Команда: {' '.join(argv)}\n"
                       + (f"Лог ({path}):\n{tail}" if tail else f"Лог: {path}"))
    version = ollama.version(host)
    details = []
    ctx = int(context_length or 0)
    details.append(f"контекст по умолчанию {ctx} токенов" if ctx > 0
                   else "контекст по умолчанию: решает Ollama")
    if keep_alive:
        details.append(f"модель держится {keep_alive}")
    return True, (f"Ollama запущена{' с флагами: ' + ' '.join(tokens) if tokens else ''}"
                  f"\nPID {getattr(process, 'pid', '?')} | версия {version or '?'}"
                  f"\n{'; '.join(details)}"
                  f"\nЛог: {path}")


def restart(host: str = "", flags: str = DEFAULT_FLAGS, keep_alive: str = "",
            context_length: int = 0) -> tuple:
    """Остановить (вместе с треем) и запустить заново."""
    ok, text = stop(host)
    if not ok:
        return False, text
    ok, started = start(host, flags, keep_alive=keep_alive,
                        context_length=context_length)
    return ok, text + "\n" + started


def ensure_with_flags(host: str = "", flags: str = DEFAULT_FLAGS,
                      keep_alive: str = "", context_length: int = 0,
                      force: bool = False, log=None) -> tuple:
    """Сервер работает с нужными настройками? Если нет — перезапустить."""
    def say(message: str):
        if log:
            try:
                log(message)
            except Exception:  # noqa: BLE001
                pass

    if not find_exe():
        return False, "Не найдена программа Ollama. " + ollama.start_hint()
    needed = shlex.split(str(flags or ""))
    serve, tray, _other = _split_processes(processes())
    running = ollama.is_running(host, timeout=2.0)
    if (serve or tray) and running and not force:
        command = str((serve or tray)[0].get("cmdline") or "")
        missing = [flag for flag in needed if flag not in command]
        if not missing:
            return True, "Ollama уже работает:\n" + command
        say("Ollama работает без флагов " + " ".join(missing) + " — перезапускаю")
        return restart(host, flags, keep_alive=keep_alive,
                       context_length=context_length)
    if force or serve or tray:
        say("Останавливаю текущий сервер Ollama")
        ok, text = stop(host)
        if not ok:
            return False, text
        ok, started = start(host, flags, keep_alive=keep_alive,
                            context_length=context_length)
        return ok, text + "\n" + started
    say("Ollama не запущена — поднимаю")
    return start(host, flags, keep_alive=keep_alive, context_length=context_length)


# ------------------------------------------------------ что сервер умеет

def embed_installed(host: str = "") -> str:
    """Имя скачанной модели эмбеддингов ("" если её нет)."""
    return ollama.pick_embed_model(host)


def supports_embeddings(host: str = "") -> tuple:
    """Есть ли у сервера /api/embed: (True/False, пояснение).

    Пробуем только модель эмбеддингов: если подсунуть чат-модель, Ollama
    сначала начнёт грузить её в память (5 ГБ и десятки секунд), и только потом
    скажет, что эмбеддинги не её профиль. Нет модели эмбеддингов — сообщаем,
    что её надо скачать.
    """
    ok, models = ollama.list_models(host)
    if not ok:
        return False, ("Ollama не отвечает: " + str(models))
    probe = ollama.pick_embed_model(host)
    if not probe:
        return False, ("Модель эмбеддингов не скачана. Скачай "
                       f"{EMBED_MODEL} ({EMBED_MODEL_SIZE}) — после этого "
                       "поиск по смыслу заработает.")
    url = ollama.normalize_host(host) + "/api/embed"
    data = json.dumps({"model": probe, "input": "проверка"}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200].strip()
        if error.code == 501 or "does not support embeddings" in detail.lower():
            return False, ("Сервер не умеет эмбеддинги: " + detail)
        return False, f"HTTP {error.code}: {detail or error.reason}"
    except Exception as error:  # noqa: BLE001
        return False, f"Проверка не удалась: {type(error).__name__}: {error}"
    return True, f"Эмбеддинги работают (модель {probe})"


def model_context_length(host: str = "", model: str = "", num_ctx: int = 0) -> int:
    """Фактический лимит контекста: num_ctx чата → загруженная модель → сервер.

    Раньше возвращалось число из карточки модели (131072), и счётчик токенов
    показывал «осталось 127k», хотя Ollama считала с num_ctx = 4096 и уже на
    третьем сообщении резала промпт (в её логе это truncated = 1). Теперь
    порядок такой:

    1. num_ctx из настроек чата — если он задан, лимит именно такой;
    2. /api/ps для загруженной модели — её реальный контекст (самый точный);
    3. OLLAMA_CONTEXT_LENGTH текущего процесса, если задан;
    4. контекст модели, но не больше DEFAULT_NUM_CTX (4096): столько Ollama
       берёт по умолчанию, и обещать больше нечестно.
    """
    name = str(model or "").strip()
    if not name:
        return 0
    limit = 0
    try:
        limit = int(num_ctx or 0)
    except (TypeError, ValueError):
        limit = 0
    if limit > 0:
        return limit

    ok, items = ollama.running_models(host)
    if ok and isinstance(items, list):
        short = name.split(":")[0]
        for item in items:
            loaded = str(item.get("name") or "")
            if loaded != name and loaded.split(":")[0] != short:
                continue
            ctx = int(item.get("context") or 0)
            if ctx > 0:
                return ctx

    try:
        env_ctx = int(os.environ.get("OLLAMA_CONTEXT_LENGTH") or 0)
    except (TypeError, ValueError):
        env_ctx = 0
    if env_ctx > 0:
        return env_ctx

    card = _card_context_length(host, name)
    if card > 0:
        return min(card, ollama.DEFAULT_NUM_CTX)
    return 0


def _card_context_length(host: str = "", model: str = "") -> int:
    """Контекст из карточки модели (/api/show): сколько она умеет всего."""
    url = ollama.normalize_host(host) + "/api/show"
    data = json.dumps({"model": model}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - не смогли — лимит просто неизвестен
        return 0
    for key, value in (body.get("model_info") or {}).items():
        if str(key).endswith(".context_length"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def status(host: str = "", flags: str = DEFAULT_FLAGS) -> dict:
    """Короткая сводка для окна настроек (без перезапуска чего-либо)."""
    exe = find_exe()
    serve, tray, other = _split_processes(processes())
    running = ollama.is_running(host, timeout=2.0)
    ok_embed, embed_text = (supports_embeddings(host) if running
                            else (False, "сервер не запущен"))
    command = str((serve or tray or [{}])[0].get("cmdline") or "")
    missing = [flag for flag in shlex.split(str(flags or "")) if flag not in command]
    return {
        "exe": exe,
        "installed": bool(exe),
        "version": installed_version(exe) if exe else "",
        "running": running,
        "cmdline": command,
        "tray": bool(tray),
        "serve": bool(serve),
        "other": other,
        "flags_ok": bool(running and not missing),
        "missing_flags": missing,
        "embeddings": ok_embed,
        "embeddings_text": embed_text,
        "log": log_path(),
        "embed_model": EMBED_MODEL,
        "embed_model_size": EMBED_MODEL_SIZE,
    }


def describe(host: str = "", flags: str = DEFAULT_FLAGS) -> str:
    """status() человеческим текстом — для консоли и подсказок."""
    data = status(host, flags)
    if not data["installed"]:
        return "Ollama не установлена. " + ollama.start_hint()
    lines = [f"Программа: {data['exe']} (версия {data['version'] or '?'})"]
    if data["running"]:
        lines.append("Сервер: запущен" + (" (через трей Ollama)" if data["tray"] else ""))
        lines.append("Команда: " + (data["cmdline"] or "не видна"))
        lines.append("Флаги: " + ("на месте" if data["flags_ok"]
                                  else "не хватает " + " ".join(data["missing_flags"])))
    else:
        lines.append("Сервер: не отвечает")
    lines.append("Эмбеддинги: " + data["embeddings_text"])
    lines.append("Лог сервера: " + data["log"])
    return "\n".join(lines)


def pull_embed_model(host: str = "", progress=None, name: str = EMBED_MODEL) -> tuple:
    """Скачать модель эмбеддингов (nomic-embed-text, ~274 МБ)."""
    return ollama.pull(host, name, progress=progress)
