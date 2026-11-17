"""Провайдеры ИИ: адреса API, страницы ключей/баланса и списки моделей.

Все перечисленные сервисы (RouterAI, OpenAI, xAI/Grok, Gemini, DeepSeek,
Mistral, OpenRouter) отдают OpenAI-совместимый API, а локальная Ollama тем
более. Поэтому переключение между ними сводится к трём вещам:
base_url + ключ + название модели. Отдельно хранится ссылка на страницу,
где пользователь смотрит расход/баланс: у каждого провайдера она своя.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    base_url: str
    models: tuple = ()
    balance_url: str = ""   # страница, где видно остаток/расход
    keys_url: str = ""      # страница, где получить ключ
    needs_key: bool = True
    local: bool = False
    note: str = ""


PROVIDERS = (
    Provider(
        "routerai", "RouterAI (агрегатор)", "https://routerai.ru/api/v1",
        ("deepseek/deepseek-v4-pro-0813", "openai/gpt-4o", "openai/gpt-4o-mini",
         "anthropic/claude-3-5-sonnet", "google/gemini-2.0-flash"),
        balance_url="https://routerai.ru/", keys_url="https://routerai.ru/",
    ),
    Provider(
        "openai", "OpenAI (ChatGPT)", "https://api.openai.com/v1",
        ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3-mini"),
        balance_url="https://platform.openai.com/usage",
        keys_url="https://platform.openai.com/api-keys",
    ),
    Provider(
        "xai", "xAI (Grok)", "https://api.x.ai/v1",
        ("grok-2-1212", "grok-beta", "grok-2-vision-1212"),
        balance_url="https://console.x.ai/", keys_url="https://console.x.ai/",
    ),
    Provider(
        "gemini", "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        ("gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"),
        balance_url="https://aistudio.google.com/",
        keys_url="https://aistudio.google.com/apikey",
    ),
    Provider(
        "deepseek", "DeepSeek", "https://api.deepseek.com/v1",
        ("deepseek-chat", "deepseek-reasoner"),
        balance_url="https://platform.deepseek.com/usage",
        keys_url="https://platform.deepseek.com/api_keys",
    ),
    Provider(
        "mistral", "Mistral", "https://api.mistral.ai/v1",
        ("codestral-latest", "mistral-large-latest", "open-mistral-nemo"),
        balance_url="https://console.mistral.ai/",
        keys_url="https://console.mistral.ai/api-keys",
    ),
    Provider(
        "openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
        ("openai/gpt-4o", "anthropic/claude-3.5-sonnet",
         "meta-llama/llama-3.1-70b-instruct"),
        balance_url="https://openrouter.ai/settings/credits",
        keys_url="https://openrouter.ai/keys",
    ),
    Provider(
        "ollama", "Ollama (локально, без ключа)", "http://localhost:11434/v1",
        (), balance_url="", keys_url="https://ollama.com/download",
        needs_key=False, local=True,
        note="Модели крутятся на этом ПК: ключ не нужен, деньги не тратятся.",
    ),
    Provider(
        "custom", "Свой OpenAI-совместимый сервер", "", (),
        note="Впиши Base URL вручную (например http://192.168.1.10:1234/v1).",
    ),
)

_BY_KEY = {item.key: item for item in PROVIDERS}
DEFAULT_PROVIDER = "routerai"


def normalize(key) -> str:
    text = str(key or "").strip().lower()
    return text if text in _BY_KEY else DEFAULT_PROVIDER


def get(key) -> Provider:
    return _BY_KEY[normalize(key)]


def label_for(key) -> str:
    return get(key).label


def choices() -> list:
    """[(ключ, подпись)] — для выпадающих списков."""
    return [(item.key, item.label) for item in PROVIDERS]


def balance_url_for(key, custom: str = "") -> str:
    """Своя ссылка из настроек важнее стандартной для провайдера."""
    custom = str(custom or "").strip()
    if custom:
        return custom
    return get(key).balance_url


def default_model(key) -> str:
    models = get(key).models
    return models[0] if models else ""


def guess_provider(base_url: str) -> str:
    """Определить провайдера по адресу — на случай, если настройки старые."""
    url = str(base_url or "").strip().lower()
    if not url:
        return DEFAULT_PROVIDER
    for item in PROVIDERS:
        if item.base_url and url.startswith(item.base_url.lower().rstrip("/")):
            return item.key
    if is_ollama_url(url):
        return "ollama"
    if "openai" in url:
        return "openai"
    if "x.ai" in url:
        return "xai"
    if "gemini" in url or "googleapis" in url:
        return "gemini"
    if "deepseek" in url:
        return "deepseek"
    if "openrouter" in url:
        return "openrouter"
    if "routerai" in url:
        return "routerai"
    return "custom"


def is_ollama_url(base_url: str) -> bool:
    """Это адрес Ollama? У неё стандартный порт 11434 и свой /api/chat.

    Нужно, чтобы даже при выбранном «свой сервер» с адресом Ollama программа
    работала через нативный клиент Ollama (иначе num_ctx/num_gpu не доходят
    и модель грузится в RAM с дефолтным контекстом).
    """
    url = str(base_url or "").strip().lower()
    if not url:
        return False
    return "11434" in url or "ollama" in url


def models_url(base_url: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/openai"):
        return base + "/models"
    return base + "/models"


def fetch_models(base_url: str, api_key: str = "", timeout: float = 15.0) -> tuple:
    """Спросить у сервера реальный список моделей.

    Возвращает (True, [id, ...]) либо (False, текст ошибки).
    Ключ может быть пустым — для Ollama он не нужен.
    """
    url = models_url(base_url)
    if not url.startswith("http"):
        return False, "Base URL не задан"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    key = str(api_key or "").strip()
    if key:
        request.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code} при запросе {url}"
    except urllib.error.URLError as error:
        return False, f"Сервер недоступен ({url}): {error.reason}"
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return False, f"Ответ не JSON: {error}"
    except Exception as error:  # noqa: BLE001 - сеть бывает любой
        return False, f"{type(error).__name__}: {error}"

    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return False, "Ответ сервера не содержит списка моделей"
    names = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("id") or item.get("name") or item.get("model")
            if name:
                names.append(str(name))
        elif isinstance(item, str):
            names.append(item)
    names.sort()
    if not names:
        return False, "Сервер вернул пустой список моделей"
    return True, names
