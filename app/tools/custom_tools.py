"""Пользовательские инструменты ИИ-агента: хранение, схема, запуск.

Инструмент = имя + описание + JSON-схема аргументов + тело:

  * python — код на Python. Аргументы приходят словарями ARGS / args:
        ARGS["path"]  (ARGS и args — одно и то же)
    результат — то, что напечатано в stdout;
  * shell  — команда оболочки, в которую подставляются значения аргументов
    через {имя} (значения экранируются автоматически).

Где лежит: <папка программы>/data/custom_tools.json (общий список для всех
чатов). Создавать и менять инструменты может человек — в менеджере
инструментов, и сам ИИ — но только если в настройках чата включено
«Разрешить ИИ изменять, добавлять, удалять инструменты».
"""

import json
import os
import re
import shlex
import subprocess

from app.runtime_env import silent_run
import sys
from pathlib import Path

STORE_NAME = "custom_tools.json"
BACKUP_NAME = "custom_tools_backup.json"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
KINDS = ("python", "shell")
KIND_LABELS = {"python": "Python-код", "shell": "Команда оболочки"}
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 900
MAX_OUTPUT = 20000

PY_PREAMBLE = (
    "import json as _json, os as _os, sys as _sys\n"
    "ARGS = _json.loads(_os.environ.get('AIC_TOOL_ARGS') or '{}')\n"
    "args = ARGS\n"
    "TOOL_NAME = _os.environ.get('AIC_TOOL_NAME') or ''\n"
)


class ToolError(ValueError):
    """Ошибка с текстом, который можно показать пользователю как есть."""


# ------------------------------------------------------------------ storage

def store_path(project_dir) -> Path:
    base = Path(project_dir or ".").expanduser()
    return base / "data" / STORE_NAME


def load_tools(project_dir) -> list:
    """Прочитать список инструментов (битый файл = пустой список)."""
    path = store_path(project_dir)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):
        raw = raw.get("tools") or []
    if not isinstance(raw, list):
        return []
    tools = []
    for item in raw:
        tool = normalize_tool(item, strict=False)
        if tool:
            tools.append(tool)
    return tools


def save_tools(project_dir, tools) -> None:
    """Записать список инструментов (с резервной копией прошлой версии)."""
    path = store_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = [tool for tool in (normalize_tool(item, strict=False) for item in tools) if tool]
    if path.is_file():
        try:
            backup = path.with_name(BACKUP_NAME)
            backup.write_text(path.read_text(encoding="utf-8", errors="replace"),
                              encoding="utf-8")
        except OSError:
            pass
    payload = json.dumps({"version": 1, "tools": clean}, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(str(tmp), str(path))


# --------------------------------------------------------------- нормализация

def _norm_parameters(raw):
    """Привести схему аргументов к виду JSON-Schema (object)."""
    if raw is None or raw == "":
        return {"type": "object", "properties": {}, "required": []}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {"type": "object", "properties": {}, "required": []}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as error:
            raise ToolError(f"Схема аргументов — не JSON: {error}")
    if isinstance(raw, list):
        properties, required = {}, []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            properties[name] = {
                "type": str(item.get("type") or "string"),
                "description": str(item.get("description") or ""),
            }
            if item.get("required"):
                required.append(name)
        return {"type": "object", "properties": properties, "required": required}
    if isinstance(raw, dict):
        properties = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
        required = raw.get("required") if isinstance(raw.get("required"), list) else []
        clean_props = {}
        for name, spec in properties.items():
            spec = spec if isinstance(spec, dict) else {}
            clean_props[str(name)] = {
                "type": str(spec.get("type") or "string"),
                "description": str(spec.get("description") or ""),
            }
        return {"type": "object", "properties": clean_props,
                "required": [str(item) for item in required]}
    raise ToolError("Схема аргументов должна быть объектом JSON-Schema")


def normalize_tool(raw, strict: bool = True):
    """Словарь инструмента в каноническом виде (None, если он битый)."""
    if not isinstance(raw, dict):
        if strict:
            raise ToolError("Инструмент должен быть объектом JSON")
        return None
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        if strict:
            raise ToolError(
                "Имя инструмента: латиница в нижнем регистре, цифры и _, 3-40 символов "
                "(например read_logs)")
        return None
    kind = str(raw.get("kind") or "python").strip().lower()
    if kind not in KINDS:
        if strict:
            raise ToolError("kind должен быть 'python' или 'shell'")
        kind = "python"
    body = str(raw.get("body") or raw.get("code") or raw.get("command") or "").strip("\n")
    if strict and not body.strip():
        raise ToolError("Пустое тело инструмента (body)")
    description = str(raw.get("description") or "").strip()
    if strict and not description:
        raise ToolError("Нужно описание: по нему модель понимает, когда звать инструмент")
    try:
        timeout = int(raw.get("timeout_seconds") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(5, min(MAX_TIMEOUT, timeout))
    try:
        parameters = _norm_parameters(raw.get("parameters"))
    except ToolError:
        if strict:
            raise
        parameters = {"type": "object", "properties": {}, "required": []}
    return {
        "name": name,
        "description": description,
        "kind": kind,
        "body": body,
        "timeout_seconds": timeout,
        "parameters": parameters,
        "enabled": bool(raw.get("enabled", True)),
        "created_by": str(raw.get("created_by") or "user"),
        "note": str(raw.get("note") or ""),
    }


def validate(raw) -> dict:
    """Проверить инструмент и вернуть нормализованный словарь (или ToolError)."""
    return normalize_tool(raw, strict=True)


# ------------------------------------------------------------------ schemas

def schema_for(tool: dict) -> dict:
    """Схема инструмента для запроса к модели (OpenAI function calling)."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name") or "custom_tool",
            "description": str(tool.get("description") or "")[:1024],
            "parameters": tool.get("parameters")
            or {"type": "object", "properties": {}, "required": []},
        },
    }


def find_tool(tools, name: str):
    low = str(name or "").strip().lower()
    for tool in tools or []:
        if str(tool.get("name") or "").lower() == low:
            return tool
    return None


def describe_tools(tools) -> str:
    """Короткое описание списка для модели и для консоли."""
    tools = list(tools or [])
    if not tools:
        return "Пользовательских инструментов нет."
    lines = [f"ПОЛЬЗОВАТЕЛЬСКИЕ ИНСТРУМЕНТЫ ({len(tools)}):"]
    for tool in tools:
        state = "вкл" if tool.get("enabled", True) else "ВЫКЛ"
        args = ", ".join((tool.get("parameters") or {}).get("properties", {}).keys())
        lines.append(f"  - {tool['name']} [{tool.get('kind')}, {state}] "
                     f"аргументы: {args or '(нет)'} — {tool.get('description', '')[:160]}")
    return "\n".join(lines)


# -------------------------------------------------------------------- запуск

def _quote(value) -> str:
    text = str(value if value is not None else "")
    if os.name == "nt":
        return '"' + text.replace('"', '\\"') + '"'
    return shlex.quote(text)


def _format_shell(body: str, args: dict) -> str:
    text = str(body or "")
    for key, value in (args or {}).items():
        text = text.replace("{" + str(key) + "}", _quote(value))
    return text


def _truncate(text: str) -> str:
    text = text or ""
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + f"\n… (обрезано, всего {len(text)} символов)"


def run_tool(tool: dict, args: dict, cwd: str = "", allow: bool = True,
             timeout=None) -> str:
    """Выполнить инструмент и вернуть текст результата (для модели)."""
    name = str(tool.get("name") or "custom_tool")
    if not allow:
        return (f"[{name}] Запуск кода запрещён: включи «Разрешить запуск Python» "
                "в настройках («Инструменты»).")
    kind = str(tool.get("kind") or "python")
    body = str(tool.get("body") or "")
    if not body.strip():
        return f"[{name}] Ошибка: пустое тело инструмента."
    try:
        limit = int(timeout or tool.get("timeout_seconds") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        limit = DEFAULT_TIMEOUT
    limit = max(5, min(MAX_TIMEOUT, limit))

    env = os.environ.copy()
    try:
        env["AIC_TOOL_ARGS"] = json.dumps(args or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        env["AIC_TOOL_ARGS"] = "{}"
    env["AIC_TOOL_NAME"] = name
    env.setdefault("PYTHONIOENCODING", "utf-8")

    if kind == "shell":
        command = _format_shell(body, args or {})
        shell = True
        argv = command
    else:
        argv = [sys.executable or "python", "-c", PY_PREAMBLE + body]
        shell = False

    workdir = str(cwd or "") or None
    try:
        proc = silent_run(
            argv, shell=shell, cwd=workdir, env=env, timeout=limit,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"[{name}] Превышен таймаут {limit} с — команда остановлена."
    except FileNotFoundError as error:
        return f"[{name}] Не найдена программа: {error}"
    except Exception as error:  # noqa: BLE001 - показываем модели как есть
        return f"[{name}] Ошибка запуска: {type(error).__name__}: {error}"

    out = proc.stdout or ""
    err = proc.stderr or ""
    parts = [f"[{name}] код возврата: {proc.returncode}"]
    if out.strip():
        parts.append("STDOUT:\n" + _truncate(out))
    if err.strip():
        parts.append("STDERR:\n" + _truncate(err))
    if not out.strip() and not err.strip():
        parts.append("(пустой вывод)")
    return "\n".join(parts)


# --------------------------------------------------- управление из агента (ИИ)

def _store_dir(project_dir) -> str:
    if not project_dir:
        raise ToolError("Не задана папка программы: инструменты хранить негде")
    return str(project_dir)


def create_tool(project_dir, payload: dict) -> tuple:
    """Добавить инструмент. -> (ok, сообщение)."""
    try:
        tool = validate(payload)
        tool["created_by"] = str(payload.get("created_by") or "ai")
        tools = load_tools(project_dir)
        if find_tool(tools, tool["name"]):
            return False, (f"Инструмент {tool['name']} уже есть. "
                           "Для изменения используй tool_update.")
        tools.append(tool)
        save_tools(project_dir, tools)
        return True, f"Создан инструмент {tool['name']} ({tool['kind']})."
    except ToolError as error:
        return False, f"Не создан: {error}"
    except OSError as error:
        return False, f"Не удалось сохранить инструмент: {error}"


def update_tool(project_dir, name: str, changes: dict) -> tuple:
    try:
        tools = load_tools(project_dir)
        tool = find_tool(tools, name)
        if tool is None:
            return False, f"Инструмент {name} не найден. Список: tool_list."
        merged = dict(tool)
        for key, value in (changes or {}).items():
            if value is not None and key in merged:
                merged[key] = value
        fixed = validate(merged)
        fixed["created_by"] = tool.get("created_by") or "ai"
        index = tools.index(tool)
        tools[index] = fixed
        save_tools(project_dir, tools)
        return True, f"Инструмент {fixed['name']} обновлён."
    except ToolError as error:
        return False, f"Не обновлён: {error}"
    except OSError as error:
        return False, f"Не удалось сохранить: {error}"


def delete_tool(project_dir, name: str) -> tuple:
    try:
        tools = load_tools(project_dir)
        tool = find_tool(tools, name)
        if tool is None:
            return False, f"Инструмент {name} не найден."
        tools.remove(tool)
        save_tools(project_dir, tools)
        return True, (f"Инструмент {name} удалён "
                      f"(копия прошлого списка: data/{BACKUP_NAME}).")
    except OSError as error:
        return False, f"Не удалось удалить: {error}"


def management_schemas() -> list:
    """Инструменты, которыми ИИ управляет своим набором (только с разрешения)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "tool_list",
                "description": (
                    "Показывает пользовательские инструменты (созданные человеком или "
                    "тобой) и их аргументы. Вызови, если не уверен, что уже есть."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tool_create",
                "description": (
                    "Создаёт НОВЫЙ инструмент, если штатных не хватает. kind='python' — "
                    "код, где аргументы лежат в словаре ARGS (например ARGS['path']); "
                    "kind='shell' — команда с подстановками {имя_аргумента}. "
                    "Создавай только то, что реально нужно для задачи пользователя, "
                    "и не дублируй существующие инструменты."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string",
                                 "description": "латиница в нижнем регистре, 3-40 символов"},
                        "description": {"type": "string",
                                        "description": "когда и зачем вызывать"},
                        "kind": {"type": "string", "enum": ["python", "shell"]},
                        "body": {"type": "string",
                                 "description": "код Python или команда оболочки"},
                        "parameters": {
                            "type": "object",
                            "description": ("JSON-Schema аргументов: "
                                            "{\"type\":\"object\",\"properties\":{...},"
                                            "\"required\":[...]}"),
                        },
                        "timeout_seconds": {"type": "integer"},
                    },
                    "required": ["name", "description", "kind", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tool_update",
                "description": "Меняет существующий пользовательский инструмент.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "kind": {"type": "string", "enum": ["python", "shell"]},
                        "body": {"type": "string"},
                        "parameters": {"type": "object"},
                        "timeout_seconds": {"type": "integer"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "tool_delete",
                "description": ("Удаляет пользовательский инструмент. Только по явной "
                                "просьбе пользователя."),
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
    ]
