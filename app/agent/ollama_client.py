"""Нативный клиент Ollama (/api/chat).

Зачем он нужен: OpenAI-совместимый /v1 у Ollama НЕ принимает параметры запуска
модели. Поэтому num_ctx (длина контекста) и num_gpu (грузить в видеопамять или
в оперативную) через него не доходят — модель грузилась в ОЗУ и с дефолтным
контекстом. Здесь тот же интерфейс, что у OpenAI SDK
(chat.completions.create), но запрос уходит на /api/chat с options и keep_alive.
"""

import json
import urllib.error
import urllib.request

from app.agent import ollama as ollama_mod


class OllamaError(RuntimeError):
    """Понятная ошибка вместо сырого URLError."""


class _Function:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: str):
        self.id = call_id
        self.type = "function"
        self.function = _Function(name, arguments)


class _Message:
    """Похож на ChatCompletionMessage: ядро агента читает те же поля."""

    def __init__(self, content="", tool_calls=None, thinking="", role="assistant"):
        self.role = role
        self.content = content or ""
        self.tool_calls = list(tool_calls or [])
        self.thinking = thinking or ""
        # ядро ищет размышления по этим именам
        self.reasoning_content = thinking or ""


class _Usage:
    def __init__(self, prompt_tokens=0, completion_tokens=0):
        self.prompt_tokens = int(prompt_tokens or 0)
        self.completion_tokens = int(completion_tokens or 0)
        self.total_tokens = self.prompt_tokens + self.completion_tokens


class _Choice:
    def __init__(self, message):
        self.index = 0
        self.message = message
        self.finish_reason = "stop"


class _Response:
    def __init__(self, message, usage, model):
        self.model = model
        self.choices = [_Choice(message)]
        self.usage = usage


def _content_to_text(content) -> str:
    """У OpenAI content бывает списком частей — приводим к строке."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            elif item:
                parts.append(str(item))
        return "\n".join(part for part in parts if part.strip())
    return "" if content is None else str(content)


def _tool_calls_payload(tool_calls) -> list:
    """Наши tool_calls -> формат Ollama: {"function": {name, arguments: объект}}."""
    out = []
    for call in tool_calls or []:
        function = getattr(call, "function", None)
        if function is None and isinstance(call, dict):
            function = call.get("function")
        if function is None:
            continue
        name = getattr(function, "name", None)
        args = getattr(function, "arguments", None)
        if name is None and isinstance(function, dict):
            name = function.get("name")
            args = function.get("arguments")
        if not name:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        out.append({"function": {"name": str(name),
                                 "arguments": args if isinstance(args, dict) else {}}})
    return out


def _convert_messages(messages) -> list:
    """История из ядра агента -> сообщения /api/chat.

    Ollama не знает tool_call_id, зато понимает tool_name: запоминаем
    соответствие id -> имя функции, пока идём по истории.
    """
    converted = []
    names = {}
    for item in messages or []:
        if isinstance(item, dict):
            role = str(item.get("role") or "user")
            entry = {"role": role, "content": _content_to_text(item.get("content"))}
            if role == "tool":
                name = item.get("tool_name") or names.get(item.get("tool_call_id"))
                if name:
                    entry["tool_name"] = str(name)
            else:
                calls = item.get("tool_calls")
                if calls:
                    entry["tool_calls"] = _tool_calls_payload(calls)
            if "images" in item:
                entry["images"] = item["images"]
            converted.append(entry)
            continue

        # объект ответа модели (assistant с tool_calls)
        calls = getattr(item, "tool_calls", None)
        for call in calls or []:
            function = getattr(call, "function", None)
            name = getattr(function, "name", "") if function is not None else ""
            if name:
                names[getattr(call, "id", "")] = name
        entry = {
            "role": getattr(item, "role", "assistant") or "assistant",
            "content": _content_to_text(getattr(item, "content", "")),
        }
        payload = _tool_calls_payload(calls)
        if payload:
            entry["tool_calls"] = payload
        converted.append(entry)
    return converted


def _parse(body: dict, model: str) -> _Response:
    message = body.get("message") or {}
    calls = []
    for index, item in enumerate(message.get("tool_calls") or []):
        if not isinstance(item, dict):
            continue
        function = item.get("function") or {}
        name = str(function.get("name") or "")
        if not name:
            continue
        args = function.get("arguments")
        if isinstance(args, (dict, list)):
            args = json.dumps(args, ensure_ascii=False)
        else:
            args = str(args or "{}")
        calls.append(_ToolCall(f"call_{index}", name, args))
    thinking = str(message.get("thinking") or "")
    usage = _Usage(body.get("prompt_eval_count"), body.get("eval_count"))
    return _Response(_Message(_content_to_text(message.get("content")), calls, thinking),
                     usage, model)


class OllamaClient:
    """Мини-клиент с интерфейсом openai.OpenAI (используется только create)."""

    def __init__(self, host: str = "", mode: str = "auto", num_ctx: int = 0,
                 keep_alive: str = "30m", num_thread: int = 0,
                 timeout: float = 900.0, think=None,
                 response_format=None):
        self.host = ollama_mod.normalize_host(host)
        self.mode = ollama_mod.normalize_mode(mode)
        self.options = ollama_mod.load_options(self.mode, num_ctx, num_thread)
        self.keep_alive = str(keep_alive or "30m")
        self.timeout = float(timeout or 900.0)
        # Необязательные поля /api/chat из библиотеки ollama-python:
        # think (True/False/"low"/"medium"/"high") — разрешить размышления,
        # response_format ("json" или схема) — ответ строго по структуре.
        self.think = think
        self.response_format = response_format
        # Модели, для которых уже применены num_gpu/num_ctx: Ollama не меняет
        # параметры у загруженной копии, поэтому перед первым запросом её
        # выгружаем (иначе «VRAM» остаётся только на бумаге).
        self._prepared = set()
        self.last_note = ""
        # имитация структуры openai.OpenAI -> chat.completions.create
        self.chat = self
        self.completions = self

    def _prepare(self, model: str) -> None:
        if self.mode == "auto" or not model or model in self._prepared:
            return
        self._prepared.add(model)
        ok, text = ollama_mod.unload(self.host, model)
        self.last_note = text if ok else ("параметры применятся при загрузке: " + text)

    # ------------------------------------------------------------- создание

    def create(self, model: str = "", messages=None, tools=None, **kwargs):
        self._prepare(model)
        payload = {
            "model": model,
            "messages": _convert_messages(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if self.options:
            payload["options"] = dict(self.options)
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        if self.think is not None:
            payload["think"] = self.think
        if self.response_format:
            payload["format"] = self.response_format
        body = self._post("/api/chat", payload)
        if isinstance(body, dict) and body.get("error"):
            raise OllamaError(str(body["error"]))
        if not isinstance(body, dict):
            raise OllamaError("Ollama вернула неожиданный ответ")
        return _parse(body, model)

    # ---------------------------------------------------------------- сеть

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.host + path, data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", "replace")[:300].strip()
            raise OllamaError(f"HTTP {error.code}: {text or error.reason}")
        except urllib.error.URLError as error:
            raise OllamaError(
                f"Ollama не отвечает ({self.host}): {error.reason}. "
                + ollama_mod.start_hint())
        except TimeoutError:
            raise OllamaError(
                "Ollama не ответила вовремя: модель слишком большая для этого "
                "контекста. Уменьши «Длину контекста» в настройках чата.")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise OllamaError("Ollama вернула не JSON: " + raw[:200])

    def describe(self) -> str:
        """Короткое описание режима — уходит в консоль при старте запроса."""
        parts = [f"сервер {self.host}"]
        ctx = self.options.get("num_ctx")
        parts.append(f"контекст {ctx}" if ctx else "контекст по умолчанию Ollama")
        mode = {"vram": "видеокарта (VRAM)",
                "ram": "оперативная память (RAM)"}.get(self.mode, "авто")
        parts.append("размещение: " + mode)
        return ", ".join(parts)
