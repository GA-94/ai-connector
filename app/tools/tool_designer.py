"""Генератор инструментов: пользователь описывает задачу словами — модель
предлагает готовое определение инструмента (имя, описание, аргументы, код).

Используется кнопкой «Создать с ИИ…» в менеджере инструментов. Ничего не
сохраняет: возвращает словарь, который человек сначала видит и правит в
редакторе инструмента.
"""

import json
import re

_INSTRUCTION = """Ты помогаешь дополнить набор инструментов ИИ-агента.
По описанию пользователя придумай ОДИН инструмент и ответь ТОЛЬКО JSON-объектом:

{
  "name": "латиница_в_нижнем_регистре_3_40_символов",
  "description": "когда и зачем вызывать (1-3 предложения, по-английски или по-русски)",
  "kind": "python",
  "body": "код Python",
  "parameters": {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "что это"}},
    "required": ["path"]
  },
  "timeout_seconds": 120
}

Правила:
- kind="python": в коде аргументы уже лежат в словаре ARGS (ARGS["path"]).
  Печатай результат в stdout; код выполняется в папке проекта.
- kind="shell": body — команда оболочки, значения аргументов подставляются как {path}.
- Никаких markdown-обёрток, никаких пояснений до/после JSON.
- Только стандартная библиотека Python (никаких pip-пакетов).
- Код должен быть коротким (до 40 строк) и безопасным: без удаления файлов.
"""


def _client(settings):
    """OpenAI-совместимый клиент по текущим настройкам провайдера."""
    from openai import OpenAI
    from app.agent import providers

    key = providers.normalize(settings.get_provider())
    info = providers.get(key)
    api_key = settings.get_api_key_for(key) or ("local-no-key" if info.local else "")
    base_url = settings.get_base_url() or info.base_url
    if info.local and not base_url:
        base_url = "http://localhost:11434/v1"
    if not base_url:
        raise RuntimeError("Не задан Base URL провайдера (настройки чата)")
    return OpenAI(api_key=api_key or "local-no-key", base_url=base_url, timeout=120.0)


def _parse(text: str) -> dict:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("модель вернула не объект JSON")
    return data


def design(settings, request_text: str, model: str = "") -> dict:
    """Придумать инструмент по описанию. -> словарь (может быть неполным)."""
    from app.tools.custom_tools import normalize_tool

    client = _client(settings)
    target = (model or settings.get_master_model() or "").strip()
    if not target:
        raise RuntimeError("Не выбрана модель (настройки чата → модели)")
    messages = [
        {"role": "system", "content": _INSTRUCTION},
        {"role": "user", "content": str(request_text or "").strip()},
    ]
    response = client.chat.completions.create(
        model=target, messages=messages, temperature=0.2,
    )
    if not response.choices:
        raise RuntimeError("пустой ответ модели")
    text = response.choices[0].message.content or ""
    data = _parse(text)
    data.setdefault("kind", "python")
    data["created_by"] = "ai"
    # Не даём сохранить мусор: если не проходит проверка — пусть человек поправит.
    try:
        normalize_tool(data, strict=True)
    except Exception:  # noqa: BLE001 - редактор покажет, что не так
        pass
    return data
