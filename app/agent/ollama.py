"""Локальные модели через Ollama: что установлено, что потянет железо, как скачать.

Только stdlib (urllib): если Ollama не запущена — возвращаем понятную
подсказку, а не исключение.
"""

import json
import os
import platform
import shutil
import subprocess
import time

from app.runtime_env import silent_popen, silent_run
import urllib.error
import urllib.request

DEFAULT_HOST = "http://localhost:11434"

# Кэш «железа»: nvidia-smi и чтение /proc/meminfo стоят сотни миллисекунд, а
# вызывать их при каждом открытии окна незачем — железо не меняется.
_HW_TTL = 180.0
_hw_cache = {"value": None, "at": 0.0}

# Кэш последней проверки Ollama: окно настроек рисует статус мгновенно, а
# обновляет его в фоне — сеть и nvidia-smi никогда не блокируют интерфейс.
_probe_cache = {}

# Кэш проверки «модель принимает инструменты» (см. tools_supported ниже).
_TOOLS_TTL = 7 * 24 * 3600.0
_tools_cache = {}

# Минимальный инструмент для проверки. Одно поле, одно обязательное значение.
_PING_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Проверочный инструмент: возвращает переданное число.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "integer", "description": "любое число"}},
            "required": ["value"],
        },
    },
}

# Контекст по умолчанию, если модель не загружена и Ollama ничего не сказала.
# Ollama 0.4x считает с num_ctx = 4096, сколько бы ни было в карточке модели.
DEFAULT_NUM_CTX = 4096

# Каталог моделей: name, минимальный объём RAM (ГБ), примерный размер (ГБ), про что она
CATALOG = (
    {"name": "qwen2.5-coder:1.5b", "ram": 4, "size": 1.0, "about": "код, очень быстрая"},
    {"name": "qwen2.5-coder:7b", "ram": 8, "size": 4.7, "about": "код, лучший баланс"},
    {"name": "llama3.2:3b", "ram": 6, "size": 2.0, "about": "чат, лёгкая"},
    {"name": "llama3.1:8b", "ram": 10, "size": 4.9, "about": "чат и текст"},
    {"name": "deepseek-coder-v2:16b", "ram": 20, "size": 8.9, "about": "код, сильная"},
    {"name": "qwen2.5-coder:14b", "ram": 18, "size": 9.0, "about": "код, сильная"},
    {"name": "gemma2:9b", "ram": 12, "size": 5.4, "about": "текст, аккуратная"},
    {"name": "mistral-nemo:12b", "ram": 16, "size": 7.1, "about": "текст, 128k контекст"},
    {"name": "qwen2.5-coder:32b", "ram": 32, "size": 20.0, "about": "код, тяжёлая"},
    {"name": "llama3.1:70b", "ram": 64, "size": 40.0, "about": "максимум, нужна большая RAM"},
)


def normalize_host(host: str = "") -> str:
    text = str(host or "").strip().rstrip("/")
    if not text:
        text = DEFAULT_HOST
    if not text.startswith("http"):
        text = "http://" + text
    if text.endswith("/v1"):
        text = text[:-3]
    text = text.rstrip("/")
    # «localhost» на Windows сначала идёт в IPv6 (::1), а Ollama слушает только
    # IPv4: каждый запрос висел ~2 секунды, пока не срабатывал таймаут. Именно
    # из-за этого окно настроек «подвисало» на 10-15 секунд (3-4 запроса подряд).
    # 127.0.0.1 — тот же сервер, но без этой задержки.
    lowered = text.lower()
    if lowered.startswith("http://localhost"):
        text = "http://127.0.0.1" + text[len("http://localhost"):]
    elif lowered.startswith("https://localhost"):
        text = "https://127.0.0.1" + text[len("https://localhost"):]
    return text


def _request(host: str, path: str, payload=None, timeout: float = 5.0):
    url = normalize_host(host) + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout)


def is_running(host: str = "", timeout: float = 2.0) -> bool:
    try:
        with _request(host, "/api/tags", timeout=timeout) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001 - нет сервера = False
        return False


def start_hint() -> str:
    """Как поднять сервер Ollama на этой ОС."""
    system = platform.system().lower()
    if system.startswith("win"):
        return ("Установи Ollama с https://ollama.com/download/windows и запусти "
                "«Ollama» из меню Пуск (или команду: ollama serve).")
    if system == "darwin":
        return "Установи Ollama (brew install ollama) и запусти: ollama serve"
    return ("Установи: curl -fsSL https://ollama.com/install.sh | sh, затем "
            "запусти службу: sudo systemctl start ollama "
            "(Arch: pacman -S ollama).")


def list_models(host: str = "") -> tuple:
    """(True, [{'name','size','modified'}]) либо (False, текст ошибки)."""
    try:
        with _request(host, "/api/tags", timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as error:  # noqa: BLE001
        return False, (f"Ollama не отвечает ({type(error).__name__}). "
                       + start_hint())
    models = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        models.append({
            "name": str(item.get("name") or ""),
            "size": float(item.get("size") or 0) / (1024 ** 3),
            "modified": str(item.get("modified_at") or ""),
        })
    models.sort(key=lambda entry: entry["name"])
    return True, models


def _ram_gb() -> float:
    if os.path.exists("/proc/meminfo"):
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
        except Exception:  # noqa: BLE001
            pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / (1024 ** 3)
    except (ValueError, OSError, AttributeError):
        pass
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / (1024 ** 3)
        except Exception:  # noqa: BLE001
            pass
    return 0.0


def _has_nvidia() -> bool:
    return shutil.which("nvidia-smi") is not None


def vram_info() -> dict:
    """Память видеокарты через nvidia-smi: {'name','total','free'} в ГБ.

    Без этого нельзя понять, влезет модель в видеопамять или Ollama начнёт
    выгружать слои в RAM (именно из-за этого «грузится ОЗУ, а не VRAM»).
    """
    result = {"name": "", "total": 0.0, "free": 0.0}
    exe = shutil.which("nvidia-smi")
    if not exe:
        return result
    try:
        completed = silent_run(
            [exe, "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
    except Exception:  # noqa: BLE001 - нет драйвера/нет утилиты
        return result
    text = (completed.stdout or "").strip()
    if completed.returncode != 0 or not text:
        return result
    parts = [part.strip() for part in text.splitlines()[0].split(",")]
    if len(parts) >= 3:
        result["name"] = parts[0]
        try:
            result["total"] = round(float(parts[1]) / 1024.0, 1)
            result["free"] = round(float(parts[2]) / 1024.0, 1)
        except ValueError:
            pass
    return result


def hardware(refresh: bool = False) -> dict:
    """Что за железо: RAM, VRAM, ядра, свободное место, видеокарта.

    Результат кэшируется на _HW_TTL секунд: без этого каждое открытие окна
    настроек ждало nvidia-smi (до 8 секунд) и окно «подвисало».
    """
    if not refresh:
        cached = cached_hardware()
        if cached is not None:
            return cached
    try:
        free_disk = shutil.disk_usage(os.path.expanduser("~")).free / (1024 ** 3)
    except OSError:
        free_disk = 0.0
    vram = vram_info()
    info = {
        "os": platform.system() or os.name,
        "cpu": platform.processor() or platform.machine() or "CPU",
        "cores": os.cpu_count() or 0,
        "ram_gb": round(_ram_gb(), 1),
        "free_disk_gb": round(free_disk, 1),
        "vram_gb": vram["total"],
        "vram_free_gb": vram["free"],
        "gpu": vram["name"] or ("NVIDIA" if _has_nvidia() else ""),
    }
    _hw_cache["value"] = dict(info)
    _hw_cache["at"] = time.time()
    return info


def cached_hardware(ttl: float = _HW_TTL):
    """Железо из кэша (None, если ещё не считали). Систему не опрашивает."""
    value = _hw_cache.get("value")
    if not value:
        return None
    if ttl and (time.time() - float(_hw_cache.get("at") or 0)) > float(ttl):
        return None
    return dict(value)


def describe(hardware_info=None) -> str:
    info = hardware_info or cached_hardware() or hardware()
    parts = [f"ОС: {info['os']}", f"RAM: {info['ram_gb']} ГБ"]
    if info.get("vram_gb"):
        parts.append(f"видеопамять: {info['vram_gb']} ГБ "
                     f"(свободно {info.get('vram_free_gb', 0)} ГБ)")
    parts.append(f"ядер: {info['cores']}")
    parts.append(f"свободно на диске: {info['free_disk_gb']} ГБ")
    if info.get("gpu"):
        parts.append("GPU: " + str(info["gpu"]))
    return " | ".join(parts)


def recommend(hardware_info=None, limit: int = 4) -> list:
    """Что скачать на это железо: приоритет — модели, влезающие в видеопамять."""
    info = hardware_info or hardware()
    ram = float(info.get("ram_gb") or 0)
    disk = float(info.get("free_disk_gb") or 0)
    vram = float(info.get("vram_gb") or 0)
    fits = []
    for item in CATALOG:
        # оставляем 1.5 ГБ системе, плюс запас на контекст
        if ram and item["ram"] > ram - 1.5:
            continue
        if disk and item["size"] > disk - 2:
            continue
        entry = dict(item)
        # Модель + кэш контекста должны влезть в видеопамять с запасом.
        entry["where"] = "VRAM" if vram and item["size"] <= vram * 0.85 else "RAM"
        fits.append(entry)
    if not fits:
        fits = [dict(CATALOG[0])]
        fits[0]["where"] = "RAM"
    # сначала те, что пойдут на видеокарту, потом по «весу»
    fits.sort(key=lambda entry: (entry.get("where") != "VRAM",
                                 -entry["ram"], entry["size"]))
    return fits[:limit]


def fit_context(model_size_gb: float, vram_gb: float) -> int:
    """Какую длину контекста (num_ctx) ставить, чтобы модель осталась на VRAM.

    Кэш контекста у 7B-модели — примерно 0.12 ГБ на 1024 токена: на веса уходит
    размер модели, остаток видеопамяти делим на этот коэффициент.
    """
    try:
        size = float(model_size_gb or 0)
        vram = float(vram_gb or 0)
    except (TypeError, ValueError):
        return 0
    if size <= 0 or vram <= 0:
        return 0
    free = max(0.0, vram * 0.92 - size)
    blocks = int(free / 0.12)
    return max(0, min(32768, blocks * 1024))


def pull(host: str, name: str, progress=None, timeout: float = 3600.0) -> tuple:
    """Скачать модель. progress(percent, status) вызывается по ходу загрузки."""
    name = str(name or "").strip()
    if not name:
        return False, "Не указано имя модели"
    payload = {"model": name, "stream": True}
    try:
        with _request(host, "/api/pull", payload=payload, timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if progress:
                    total = float(event.get("total") or 0)
                    done = float(event.get("completed") or 0)
                    percent = int(done * 100 / total) if total else 0
                    progress(percent, str(event.get("status") or ""))
                if event.get("error"):
                    return False, str(event["error"])
    except Exception as error:  # noqa: BLE001
        return False, f"Загрузка не удалась: {type(error).__name__}: {error}"
    return True, f"Модель {name} скачана"


def start_server() -> tuple:
    """Попробовать поднять 'ollama serve' в фоне (если бинарь есть в PATH)."""
    exe = shutil.which("ollama")
    if not exe:
        return False, "Команда ollama не найдена в PATH. " + start_hint()
    try:
        silent_popen([exe, "serve"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as error:  # noqa: BLE001
        return False, f"Не удалось запустить ollama serve: {error}"
    return True, "Сервер Ollama запускается в фоне"


# ------------------------------------------------ размещение модели: RAM/VRAM

# auto - как решит сама Ollama, vram - все слои на видеокарту, ram - только CPU
MODES = ("auto", "vram", "ram")

MODE_CHOICES = (
    ("auto", "Авто (Ollama решает сама)"),
    ("vram", "Видеокарта (VRAM)"),
    ("ram", "Оперативная память (RAM, процессор)"),
)

PLACEMENT_HINT = (
    "Если модель не влезает в видеопамять, Ollama выгружает часть слоёв в RAM — "
    "видеокарта простаивает, а память занята. Что делать: режим «Видеокарта "
    "(VRAM)», длина контекста поменьше (8k вместо 32k) или модель поменьше.\n"
    "Смена режима применится при следующем запросе: программа сама выгружает "
    "модель, чтобы новая настройка заработала, — иначе Ollama продолжает "
    "считать по-старому."
)


def normalize_mode(mode) -> str:
    text = str(mode or "").strip().lower()
    if text in ("gpu", "cuda", "videocard", "vram", "видеокарта"):
        return "vram"
    if text in ("cpu", "ram", "озу", "оперативка"):
        return "ram"
    return "auto"


def load_options(mode="auto", num_ctx=0, num_thread=0) -> dict:
    """Параметры запуска модели — именно они уходят в /api/chat.

    num_gpu = -1 -> все слои на видеокарту (сколько влезет),
    num_gpu = 0  -> только процессор, модель целиком в RAM,
    не задан     -> решает Ollama (как раньше).
    """
    options = {}
    mode = normalize_mode(mode)
    if mode == "vram":
        options["num_gpu"] = -1
    elif mode == "ram":
        options["num_gpu"] = 0
    try:
        ctx = int(num_ctx or 0)
    except (TypeError, ValueError):
        ctx = 0
    if ctx > 0:
        options["num_ctx"] = ctx
    try:
        threads = int(num_thread or 0)
    except (TypeError, ValueError):
        threads = 0
    if threads > 0:
        options["num_thread"] = threads
    return options


def running_models(host="") -> tuple:
    """Что сейчас загружено и куда: (True, [{'name','vram','ram','size'}]).

    Ollama отдаёт в /api/ps размер модели и размер её части в видеопамяти —
    по разнице видно, сколько утекло в RAM.
    """
    try:
        with _request(host, "/api/ps", timeout=4.0) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as error:  # noqa: BLE001 - нет сервера
        return False, f"Ollama не отвечает ({type(error).__name__})"
    items = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        total = float(item.get("size") or 0) / (1024 ** 3)
        vram = float(item.get("size_vram") or 0) / (1024 ** 3)
        items.append({
            "name": str(item.get("name") or item.get("model") or ""),
            "size": round(total, 2),
            "vram": round(vram, 2),
            "ram": round(max(0.0, total - vram), 2),
            # Фактический контекст загруженной копии. Это единственный честный
            # источник лимита: в карточке модели написано 131072, а считает
            # она с num_ctx из окружения сервера (обычно 4096).
            "context": int(item.get("context_length") or 0),
        })
    return True, items


def placement_text(host="", mode="") -> str:
    """Короткий отчёт: сколько гигабайт модели в видеокарте, сколько в памяти."""
    ok, items = running_models(host)
    if not ok:
        return str(items)
    if not items:
        return ("Сейчас ни одна модель не загружена — она загрузится при первом "
                "запросе.")
    parts = [f"{item['name']}: видеокарта {item['vram']} ГБ, "
             f"память {item['ram']} ГБ" for item in items]
    text = "Загружено — " + "; ".join(parts)
    if normalize_mode(mode) == "vram" and all(item["vram"] <= 0 for item in items):
        text += (
            "\n\nВнимание: модель считает на процессоре. Проверь, что Ollama "
            "видит видеокарту (nvidia-smi, свежие драйверы, версия Ollama с "
            "поддержкой CUDA/ROCm). Модель могла остаться загруженной со "
            "старыми настройками — нажми «Выгрузить модель».")
    return text


def show(host="", name: str = "") -> tuple:
    """Карточка модели (/api/show): семейство, параметры, квантование, контекст.

    Нужна, чтобы не гадать по имени: сколько параметров у модели, какой
    контекст она держит и умеет ли инструменты.
    """
    name = str(name or "").strip()
    if not name:
        return False, "Не указано имя модели"
    try:
        with _request(host, "/api/show", payload={"model": name}, timeout=15.0) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as error:  # noqa: BLE001
        return False, (f"Не удалось получить карточку модели: "
                       f"{type(error).__name__}: {error}")
    details = payload.get("details") or {}
    info = payload.get("model_info") or {}
    lines = [f"Модель: {name}"]
    for key, label in (("family", "Семейство"),
                       ("parameter_size", "Параметров"),
                       ("quantization_level", "Квантование"),
                       ("format", "Формат")):
        value = details.get(key)
        if value:
            lines.append(f"{label}: {value}")
    for key, value in info.items():
        if str(key).endswith(".context_length"):
            try:
                lines.append(f"Контекст модели: {int(value)} токенов")
            except (TypeError, ValueError):
                pass
            break
    capabilities = payload.get("capabilities") or []
    if capabilities:
        lines.append("Умеет: " + ", ".join(str(item) for item in capabilities))
    size = payload.get("size")
    if size:
        try:
            lines.append(f"Размер: {float(size) / (1024 ** 3):.1f} ГБ")
        except (TypeError, ValueError):
            pass
    return True, "\n".join(lines)


def _caps_path() -> str:
    """Файл с результатами проверок моделей (data/model_caps.json)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "data", "model_caps.json")


def _caps_load() -> dict:
    try:
        with open(_caps_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - нет файла или он битый: просто нет кэша
        return {}


def _caps_save(data: dict) -> None:
    path = _caps_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception:  # noqa: BLE001 - без файла проверка всё равно живёт в памяти
        pass


def _caps_key(host: str, model: str) -> str:
    return normalize_host(host) + "|" + str(model or "").strip()


def _chat_prompt_tokens(host: str, payload: dict, timeout: float) -> int:
    """Сколько токенов промпта посчитала Ollama (/api/chat, ответ не нужен)."""
    with _request(host, "/api/chat", payload=payload, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8", "replace"))
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(str(body["error"]))
    return int(body.get("prompt_eval_count") or 0)


def tools_supported(host: str = "", model: str = "", num_ctx: int = 0,
                    force: bool = False, timeout: float = 300.0) -> tuple:
    """Принимает ли модель список инструментов: (True / False / None, текст).

    Зачем: Ollama молча выбрасывает параметр tools, если в шаблоне модели нет
    блока инструментов (так бывает у моделей, пересобранных вручную). Ядро
    агента при этом уверено, что инструменты у модели есть, а модель их не
    видит — и сочиняет «файл создан», ничего не сделав. Поле capabilities в
    /api/show врёт: там «tools» стоит почти у всех.

    Проверка без гаданий: считаем токены промпта с инструментом и без него.
    Схема попала в промпт — токенов больше. Не попала — числа равны.
    """
    name = str(model or "").strip()
    if not name:
        return None, "Не указано имя модели"
    key = _caps_key(host, name)
    now = time.time()
    if not force:
        cached = _tools_cache.get(key)
        if cached and now - cached.get("at", 0) < _TOOLS_TTL:
            return cached["ok"], cached["text"]
        stored = _caps_load().get(key)
        if stored and now - float(stored.get("at") or 0) < _TOOLS_TTL:
            result = (bool(stored.get("ok")), str(stored.get("text") or ""))
            _tools_cache[key] = {"ok": result[0], "text": result[1], "at": now}
            return result

    base = {
        "model": name,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "think": False,
        # Один токен: проверяем не ответ, а размер промпта. Модель до этого
        # могла быть загружена с другим num_ctx — передаём тот же, что в чате,
        # чтобы проверка не меняла настройки работающей модели.
        "options": {"num_predict": 1},
        "keep_alive": "5m",
    }
    ctx = 0
    try:
        ctx = int(num_ctx or 0)
    except (TypeError, ValueError):
        ctx = 0
    if ctx > 0:
        base["options"]["num_ctx"] = ctx
    try:
        with_tools = _chat_prompt_tokens(host, dict(base, tools=[_PING_TOOL]), timeout)
        without = _chat_prompt_tokens(host, dict(base), timeout)
    except Exception as error:  # noqa: BLE001 - сервер молчит, модель не грузится
        return None, f"Не удалось проверить инструменты: {type(error).__name__}: {error}"
    if with_tools <= 0 or without <= 0:
        return None, "Ollama не сообщила размер промпта — проверить не получилось"
    ok = with_tools > without
    if ok:
        text = (f"Модель {name} принимает инструменты: схема добавляет "
                f"{with_tools - without} токенов к промпту ({without} → {with_tools}).")
    else:
        text = (f"Модель {name} НЕ принимает инструменты: промпт {without} токенов "
                f"и со схемой, и без неё — Ollama выбрасывает список tools. "
                "Файлы и команды такая модель не выполнит, что бы она ни "
                "написала в ответе. Лечится пересборкой модели с шаблоном "
                "инструментов (ollama create) или выбором другой модели.")
    _tools_cache[key] = {"ok": ok, "text": text, "at": now}
    data = _caps_load()
    data[key] = {"ok": bool(ok), "text": text, "at": now, "model": name}
    _caps_save(data)
    return ok, text


def cached_tools_support(host: str = "", model: str = "") -> tuple:
    """Что уже известно про инструменты модели: (True/False/None, текст).

    Только кэш, без сети: эту функцию зовёт ядро агента при старте чата, а
    грузить 27-гигабайтную модель ради проверки никто не просил.
    """
    name = str(model or "").strip()
    if not name:
        return None, ""
    key = _caps_key(host, name)
    entry = _tools_cache.get(key) or _caps_load().get(key)
    if not entry:
        return None, ""
    return bool(entry.get("ok")), str(entry.get("text") or "")


def delete(host="", name: str = "") -> tuple:
    """Удалить скачанную модель с диска (/api/delete).

    Спрашивать подтверждение должен вызывающий код: операция необратимая.
    """
    name = str(name or "").strip()
    if not name:
        return False, "Не указано имя модели"
    try:
        request = urllib.request.Request(
            normalize_host(host) + "/api/delete",
            data=json.dumps({"model": name}).encode("utf-8"),
            method="DELETE",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
    except Exception as error:  # noqa: BLE001
        return False, f"Не удалось удалить модель: {type(error).__name__}: {error}"
    _probe_cache.pop(normalize_host(host), None)
    # Проверка инструментов относилась к удалённой модели — забываем её.
    key = _caps_key(host, name)
    _tools_cache.pop(key, None)
    data = _caps_load()
    if data.pop(key, None) is not None:
        _caps_save(data)
    return True, f"Модель {name} удалена"


def version(host="") -> str:
    """Версия Ollama (полезно понять, поддерживает ли она GPU)."""
    try:
        with _request(host, "/api/version", timeout=4.0) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return ""
    return str(payload.get("version") or "")


def unload(host="", name: str = "") -> tuple:
    """Выгрузить модель из памяти, чтобы новые num_gpu/num_ctx применились.

    Без этого Ollama держит уже загруженную копию и продолжает считать с
    прежними параметрами — режим «VRAM» выглядит как «не работает».
    """
    name = str(name or "").strip()
    if not name:
        return False, "Не указано имя модели"
    try:
        request = urllib.request.Request(
            normalize_host(host) + "/api/generate",
            data=json.dumps({"model": name, "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except Exception as error:  # noqa: BLE001
        return False, f"Не удалось выгрузить модель: {type(error).__name__}: {error}"
    return True, f"Модель {name} выгружена из памяти"


def diagnose(host="", mode="auto", num_ctx=0, model="") -> str:
    """Полная проверка Ollama: сервер, версия, модели, железо, размещение.

    Нужна, чтобы не гадать «почему грузится ОЗУ»: отчёт показывает, видит ли
    Ollama видеокарту и куда реально попала модель.
    """
    info = hardware()
    lines = ["Железо: " + describe(info)]
    if not is_running(host):
        lines.append("Сервер: не отвечает.")
        lines.append(start_hint())
        return "\n".join(lines)
    lines.append("Сервер: отвечает (" + normalize_host(host) + ")")
    ver = version(host)
    if ver:
        lines.append("Версия Ollama: " + ver)
    ok, models = list_models(host)
    if ok:
        if models:
            lines.append(f"Скачано моделей: {len(models)} — "
                         + ", ".join(entry["name"] for entry in models))
        else:
            lines.append("Скачано моделей: нет. Нажми «Что скачать» — программа "
                         "подберёт модель под это железо.")
    else:
        lines.append("Список моделей: " + str(models))
    mode_label = {"vram": "видеокарта (VRAM)",
                  "ram": "оперативная память (RAM)"}.get(
                      normalize_mode(mode), "авто")
    lines.append(f"Режим размещения: {mode_label}")
    if num_ctx:
        lines.append(f"Длина контекста: {int(num_ctx)} токенов")
    lines.append(placement_text(host, mode))
    # Вердикт по конкретной модели: влезает в видеопамять или уедет в RAM.
    # Без этого «выбрал VRAM, а ОЗУ всё равно растёт» остаётся загадкой.
    chosen = str(model or "").strip()
    if chosen and ok:
        size = 0.0
        for entry in models:
            if entry.get("name") == chosen:
                size = float(entry.get("size") or 0)
                break
        vram = float(info.get("vram_gb") or 0)
        if size > 0:
            lines.append(f"Модель {chosen}: {size:.1f} ГБ")
            if vram > 0:
                if size <= vram * 0.85:
                    fit = fit_context(size, vram)
                    note = (f"  помещается в видеопамять ({vram:.1f} ГБ)."
                            if mode == "vram" else
                            f"  помещается в видеопамять ({vram:.1f} ГБ), но режим "
                            "сейчас не VRAM — переключи его.")
                    lines.append(note)
                    if fit:
                        lines.append(f"  рекомендуемая длина контекста: {fit} токенов")
                else:
                    need = size / 0.9
                    lines.append(
                        f"  НЕ помещается в видеопамять ({vram:.1f} ГБ): нужно "
                        f"~{need:.1f} ГБ с запасом. Ollama выгрузит часть слоёв "
                        "в оперативную память.")
                    lines.append("  Что делать: взять модель меньшего размера, "
                                 "уменьшить «Длину контекста» или оставить RAM.")
            else:
                lines.append("  Видеопамять не определена — модель пойдёт на CPU.")
    if not info.get("vram_gb"):
        lines.append("Видеопамять не определена: nvidia-smi не найден. Если "
                     "карта NVIDIA — поставь свежий драйвер, иначе Ollama "
                     "будет считать на процессоре.")
    elif info.get("vram_gb") and mode_label.startswith("видеокарта"):
        lines.append("Совет: если модель всё равно уходит в RAM — уменьши "
                     "«Длину контекста» или возьми модель поменьше.")
    return "\n".join(lines)


def format_tokens(count, limit=0) -> str:
    """«3.4k / 8k (42%)» — сколько контекста занято."""
    try:
        used = int(count or 0)
    except (TypeError, ValueError):
        used = 0
    try:
        cap = int(limit or 0)
    except (TypeError, ValueError):
        cap = 0
    if used <= 0 and cap <= 0:
        return "—"
    if cap > 0:
        percent = int(round(used * 100.0 / cap))
        return f"{_short_tokens(used)} / {_short_tokens(cap)} ({percent}%)"
    return _short_tokens(used)


def _short_tokens(value) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return "0"
    if number >= 1000:
        text = f"{number / 1000:.1f}k"
        return text.replace(".0k", "k")
    return str(number)


# ------------------------------------- проверка из фонового потока (без лагов)

def probe(host: str = "", mode: str = "auto", timeout: float = 2.0) -> dict:
    """Одна полная проверка Ollama — для фонового потока.

    Возвращает словарь (а не исключение): интерфейс просто покажет, что сервер
    не отвечает. Результат кладётся в кэш, чтобы следующее открытие окна
    нарисовало статус мгновенно, не дожидаясь сети.
    """
    key = normalize_host(host)
    info = hardware()
    result = {
        "host": key,
        "running": False,
        "version": "",
        "models": [],
        "error": "",
        "placement": "",
        "hardware": info,
        "hardware_text": describe(info),
        "at": time.time(),
    }
    if not is_running(key, timeout=timeout):
        result["error"] = "Сервер не отвечает."
        _probe_cache[key] = result
        return result
    result["running"] = True
    result["version"] = version(key)
    ok, models = list_models(key)
    if ok:
        result["models"] = models
    else:
        result["error"] = str(models)
    result["placement"] = placement_text(key, mode)
    _probe_cache[key] = result
    return result


def cached_probe(host: str = "", ttl: float = 60.0):
    """Прошлый результат probe (или None). Сеть не трогает."""
    data = _probe_cache.get(normalize_host(host))
    if not data:
        return None
    if ttl and (time.time() - float(data.get("at") or 0)) > float(ttl):
        return None
    return data


def cached_models(host: str = "", ttl: float = 3600.0) -> list:
    """Скачанные модели из кэша — чтобы заполнить список без запроса к серверу."""
    data = cached_probe(host, ttl=ttl)
    if not data:
        return []
    return list(data.get("models") or [])


def clear_cache() -> None:
    """Сбросить кэши (после скачивания или выгрузки модели)."""
    _probe_cache.clear()
    _hw_cache["value"] = None
    _hw_cache["at"] = 0.0


# ------------------------------------------------ функции библиотеки ollama
# Ниже — то, что есть в официальной библиотеке ollama-python, но чего не было
# в Ai-Connector: эмбеддинги, сборка своей модели из базовой, копирование,
# «сырое» дополнение текста и чат потоком. Интерфейс намеренно простой
# (кортеж «получилось/текст ошибки»), чтобы вызывать это из Qt-потоков.

# Модели эмбеддингов, которые чаще всего ставят рядом с чат-моделями.
EMBED_CATALOG = ("nomic-embed-text", "mxbai-embed-large", "all-minilm",
                 "snowflake-arctic-embed", "bge-m3", "paraphrase-multilingual")


def pick_embed_model(host: str = "", preferred: str = "") -> str:
    """Какая модель эмбеддингов есть на диске (или "" если ни одной).

    Имя модели знать не обязательно: у Ollama нет отдельного «списка моделей
    эмбеддингов», поэтому смотрим, что скачано, и ищем по известным именам.
    """
    name = str(preferred or "").strip()
    if name:
        return name
    ok, models = list_models(host)
    if not ok:
        return ""
    names = [str(entry.get("name") or "") for entry in models]
    for candidate in EMBED_CATALOG:
        for full in names:
            if full.split(":")[0] == candidate:
                return full
    for full in names:
        if "embed" in full.lower():
            return full
    return ""


def embed(host: str = "", model: str = "", texts=None, keep_alive: str = "",
          timeout: float = 180.0) -> tuple:
    """Векторы для текстов (/api/embed): (True, [[float, ...]]) или (False, текст).

    Нужно для поиска по проекту «по смыслу»: в коде может быть написано
    calculate_cost, а спросить хочется «где считается стоимость» — по словам
    не совпадёт, по вектору совпадёт.
    """
    items = ["" if text is None else str(text) for text in (texts or [])]
    if not items:
        return False, "Пустой список текстов"
    model = str(model or "").strip()
    if not model:
        return False, "Не выбрана модель эмбеддингов"
    payload = {"model": model, "input": items}
    if keep_alive:
        payload["keep_alive"] = str(keep_alive)
    try:
        with _request(host, "/api/embed", payload=payload, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200].strip()
        low = detail.lower()
        if "does not support embeddings" in low or "--embeddings" in low:
            return False, ("Сервер Ollama не умеет эмбеддинги. В старых версиях "
                           "помогал флаг --embeddings, но в свежих сборках "
                           "(проверено на 0.40.16) его уже нет — `ollama serve "
                           "--embeddings` падает с «unknown flag». Обнови Ollama "
                           "и скачай модель эмбеддингов: ollama pull "
                           "nomic-embed-text")
        return False, (f"{model}: HTTP {error.code} — {detail or error.reason}. "
                       "Похоже, это чат-модель, а нужна модель эмбеддингов: "
                       f"ollama pull {model if 'embed' in model.lower() else 'nomic-embed-text'}")
    except Exception as error:  # noqa: BLE001
        return False, (f"Модель эмбеддингов {model} не ответила "
                       f"({type(error).__name__}). Если её нет — скачай: "
                       f"ollama pull {model}")
    if isinstance(body, dict) and body.get("error"):
        return False, str(body["error"])
    vectors = (body or {}).get("embeddings") or []
    if not vectors:
        return False, f"{model}: пустой ответ на /api/embed"
    return True, [list(map(float, vector)) for vector in vectors]


def embeddings_ready(host: str = "") -> tuple:
    """Готова ли Ollama считать эмбеддинги: (True, модель) либо (False, причина).

    Проверка нужна заранее: сервер бывает запущен без флага --embeddings, и
    тогда /api/embed отвечает ошибкой даже при скачанной модели.
    """
    model = pick_embed_model(host)
    if not model:
        return False, ("Не найдена модель эмбеддингов. Скачай, например: "
                       "ollama pull nomic-embed-text (около 270 МБ)")
    ok, result = embed(host, model, ["проверка"], timeout=90.0)
    if not ok:
        return False, str(result)
    return True, model


def copy_model(host: str = "", source: str = "", destination: str = "") -> tuple:
    """Копия модели под новым именем (/api/copy).

    Веса не дублируются на диске — копия занимает почти ничего, поэтому можно
    спокойно экспериментировать.
    """
    source = str(source or "").strip()
    destination = str(destination or "").strip()
    if not source or not destination:
        return False, "Нужны имена: что копируем и как назвать"
    if source == destination:
        return False, "Имена совпадают — копировать некуда"
    try:
        request = urllib.request.Request(
            normalize_host(host) + "/api/copy",
            data=json.dumps({"source": source,
                             "destination": destination}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120.0) as response:
            response.read()
    except Exception as error:  # noqa: BLE001
        return False, f"Не удалось скопировать: {type(error).__name__}: {error}"
    _probe_cache.pop(normalize_host(host), None)
    return True, f"Модель {source} скопирована как {destination}"


def create_model(host: str = "", name: str = "", from_model: str = "",
                 system: str = "", parameters=None, template: str = "",
                 quantize: str = "", progress=None, timeout: float = 3600.0) -> tuple:
    """Своя модель из базовой (/api/create).

    Зачем: системный промпт и параметры (num_ctx, temperature) можно зашить в
    модель. Тогда они действуют всегда — а не только пока открыт этот чат,
    и их не надо помнить при создании нового чата.
    """
    name = str(name or "").strip()
    from_model = str(from_model or "").strip()
    if not name:
        return False, "Не указано имя новой модели"
    if not from_model:
        return False, "Не указана базовая модель"
    payload = {"model": name, "from": from_model, "stream": True}
    if system:
        payload["system"] = str(system)
    if parameters:
        payload["parameters"] = dict(parameters)
    if template:
        payload["template"] = str(template)
    if quantize:
        payload["quantize"] = str(quantize)
    last = ""
    try:
        with _request(host, "/api/create", payload=payload, timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    return False, str(event["error"])
                status = str(event.get("status") or "")
                if status:
                    last = status
                    if progress:
                        progress(100 if status == "success" else 50, status)
    except Exception as error:  # noqa: BLE001
        return False, f"Сборка модели не удалась: {type(error).__name__}: {error}"
    _probe_cache.pop(normalize_host(host), None)
    return True, f"Модель {name} собрана из {from_model} ({last or 'готово'})"


def generate(host: str = "", model: str = "", prompt: str = "", system: str = "",
             suffix: str = "", options=None, keep_alive: str = "",
             timeout: float = 900.0) -> tuple:
    """«Сырое» дополнение текста (/api/generate): (True, текст) или (False, ошибка).

    Отличие от чата: нет истории и ролей — один промпт. suffix нужен моделям с
    fill-in-middle: они видят и начало кода, и то, что идёт после курсора.
    """
    model = str(model or "").strip()
    if not model:
        return False, "Не указана модель"
    payload = {"model": model, "prompt": str(prompt or ""), "stream": False}
    if system:
        payload["system"] = str(system)
    if suffix:
        payload["suffix"] = str(suffix)
    if options:
        payload["options"] = dict(options)
    if keep_alive:
        payload["keep_alive"] = str(keep_alive)
    try:
        with _request(host, "/api/generate", payload=payload, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as error:  # noqa: BLE001
        return False, f"Ollama не ответила ({type(error).__name__}: {error})"
    if isinstance(body, dict) and body.get("error"):
        return False, str(body["error"])
    return True, str((body or {}).get("response") or "")


def chat_stream(host: str = "", model: str = "", messages=None, options=None,
                keep_alive: str = "", think=None, response_format=None,
                timeout: float = 900.0):
    """Чат потоком (/api/chat со stream=True) — генератор кусков ответа.

    Отдаёт словари: {"text", "thinking", "done", "eval_count", "eval_duration"}
    либо {"error": "..."} и на этом останавливается. Нужно там, где нельзя
    ждать весь ответ: индикатор «модель думает» и тест модели перед работой.
    """
    payload = {"model": str(model or "").strip(),
               "messages": list(messages or []),
               "stream": True}
    if options:
        payload["options"] = dict(options)
    if keep_alive:
        payload["keep_alive"] = str(keep_alive)
    if think is not None:
        payload["think"] = think
    if response_format:
        payload["format"] = response_format
    try:
        with _request(host, "/api/chat", payload=payload, timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    yield {"error": str(event["error"])}
                    return
                message = event.get("message") or {}
                yield {
                    "text": str(message.get("content") or ""),
                    "thinking": str(message.get("thinking") or ""),
                    "done": bool(event.get("done")),
                    "eval_count": int(event.get("eval_count") or 0),
                    "eval_duration": int(event.get("eval_duration") or 0),
                }
    except Exception as error:  # noqa: BLE001
        yield {"error": f"Ollama не ответила ({type(error).__name__}: {error}). "
                        + start_hint()}

