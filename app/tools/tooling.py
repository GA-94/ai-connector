"""Группы инструментов агента: что можно включать и выключать.

Зачем: если проект на Python, инструменты java_* только жгут токены
(и деньги) — их можно выключить одной галочкой, и они перестанут
попадать в запрос к модели.
"""

import os

GROUPS = (
    ("files", "Файлы проекта", (
        "list_files", "read_file", "write_file", "edit_file",
        "deactivate_file", "backup_and_remove_dir", "batch_write_files",
        "read_entire_project_via_cli")),
    ("java", "Java / JVM проект", (
        "java_scan", "java_read_class", "java_find", "java_build",
        "java_parse_log")),
    ("python", "Python", ("python_info", "python_run")),
    ("search", "Поиск по смыслу (эмбеддинги)", ("semantic_search",)),
    ("repo", "Репозиторий шаблонов", (
        "repo_list", "repo_search", "repo_archive_list", "repo_read_file",
        "repo_import_file", "repo_extract", "repo_fetch", "repo_status")),
    ("balance", "Баланс счёта", ("account_balance",)),
    ("git", "Git / GitHub", ("git_status", "git_commit", "github_publish")),
    ("terminal", "Удалённый терминал (SSH)", (
        "remote_terminal_list", "remote_terminal_open", "remote_terminal_exec",
        "remote_terminal_close")),
    ("storage", "Удалённые хранилища (SMB/FTP/SFTP/S3/WebDAV/SCP)", (
        "remote_storage_list", "remote_storage_ls", "remote_storage_download",
        "remote_storage_upload", "remote_storage_mkdir", "remote_storage_rename",
        "remote_storage_delete", "remote_storage_info")),
)

# Группы, без которых агент не сможет работать с кодом — их не выключаем.
ESSENTIAL = {"files"}


def group_of(tool_name: str) -> str:
    for key, _label, names in GROUPS:
        if tool_name in names:
            return key
    return ""


def group_label(key: str) -> str:
    for group_key, label, _names in GROUPS:
        if group_key == key:
            return label
    return key


def group_tools(key: str) -> tuple:
    for group_key, _label, names in GROUPS:
        if group_key == key:
            return names
    return ()


def all_group_keys() -> list:
    return [key for key, _label, _names in GROUPS]


def split_disabled(disabled) -> tuple:
    """Разделить сохранённый список на (группы, отдельные инструменты)."""
    groups, tools = set(), set()
    for item in disabled or []:
        key = str(item or "").strip()
        if not key:
            continue
        if key in all_group_keys():
            if key not in ESSENTIAL:
                groups.add(key)
        else:
            tools.add(key)
    return groups, tools


def is_tool_enabled(tool_name: str, disabled) -> bool:
    groups, tools = split_disabled(disabled)
    if tool_name in tools:
        return False
    group = group_of(tool_name)
    if group and group in groups:
        return False
    return True


def filter_tools(tools: list, disabled) -> list:
    """Оставить в схеме только разрешённые инструменты (экономим токены)."""
    if not disabled:
        return list(tools)
    groups, names = split_disabled(disabled)
    if not groups and not names:
        return list(tools)
    result = []
    for schema in tools:
        name = (schema.get("function") or {}).get("name", "")
        if not name:
            continue
        if name in names:
            continue
        group = group_of(name)
        if group and group in groups and group not in ESSENTIAL:
            continue
        result.append(schema)
    return result


def describe_disabled(disabled) -> str:
    groups, tools = split_disabled(disabled)
    if not groups and not tools:
        return "включены все инструменты"
    parts = [group_label(key) for key in sorted(groups)]
    parts += sorted(tools)
    return "выключено: " + ", ".join(parts)


def suggest_for_project(path: str) -> list:
    """Подсказка «какая группа лишняя»: например, Java в Python-проекте."""
    disabled = []
    if not path or not os.path.isdir(path):
        return disabled
    has_java = False
    has_python = False
    try:
        for name in os.listdir(path):
            if name in ("build.gradle", "build.gradle.kts", "pom.xml", "settings.gradle"):
                has_java = True
            if name in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile"):
                has_python = True
            if name.endswith(".java"):
                has_java = True
            if name.endswith(".py"):
                has_python = True
    except OSError:
        return disabled
    if has_python and not has_java:
        disabled.append("java")
    if has_java and not has_python:
        disabled.append("python")
    return disabled


# --------------------------------------------------------------- авто-отбор

JAVA_MARKERS = ("build.gradle", "build.gradle.kts", "settings.gradle",
                "settings.gradle.kts", "pom.xml", "gradlew", "mvnw",
                "gradle.properties")
PYTHON_MARKERS = ("requirements.txt", "pyproject.toml", "setup.py",
                  "setup.cfg", "Pipfile", "poetry.lock")
JAVA_EXT = (".java", ".kt", ".kts")
PYTHON_EXT = (".py", ".pyw")
_SCAN_LIMIT = 400
_SUBDIRS = ("src", "app", "lib", "tests", "test", "common", "fabric", "forge")


def _scan_dir(path: str, kinds: dict):
    """Отметить, что нашлось в одной папке (без рекурсии по всему диску)."""
    try:
        entries = sorted(os.listdir(path))[:_SCAN_LIMIT]
    except OSError:
        return
    if ".git" in entries:
        kinds["git"] = True
    for name in entries:
        if name in JAVA_MARKERS:
            kinds["java"] = True
        elif name in PYTHON_MARKERS:
            kinds["python"] = True
        low = name.lower()
        if low.endswith(JAVA_EXT):
            kinds["java"] = True
        elif low.endswith(PYTHON_EXT):
            kinds["python"] = True


def detect_kinds(project_root: str = "") -> dict:
    """Что за проект: java / python / git. Смотрим корень и пару папок внутри."""
    kinds = {"java": False, "python": False, "git": False}
    if not project_root or not os.path.isdir(project_root):
        return kinds
    _scan_dir(project_root, kinds)
    for name in _SUBDIRS:
        sub = os.path.join(project_root, name)
        if os.path.isdir(sub):
            _scan_dir(sub, kinds)
    return kinds


def repository_has_assets(repository_root: str = "") -> bool:
    """В репозитории есть хоть что-то, кроме пустой папки?"""
    if not repository_root or not os.path.isdir(repository_root):
        return False
    try:
        for name in os.listdir(repository_root):
            if not name.startswith("."):
                return True
    except OSError:
        return False
    return False


def auto_disable(project_root: str = "", repository_root: str = "",
                 has_github: bool = False) -> list:
    """Группы инструментов, бесполезные в этом проекте (экономия токенов).

    В Python-проекте пять java_* инструментов — это несколько килобайт схемы
    в КАЖДОМ запросе к модели, то есть деньги на каждой итерации агента.
    Ручные настройки пользователя при этом не отменяются: результат
    складывается с уже выключенными группами.
    """
    if not project_root or not os.path.isdir(project_root):
        return []          # непонятно, что за проект — ничего не скрываем
    kinds = detect_kinds(project_root)
    disabled = []
    if not kinds["java"]:
        disabled.append("java")
    if kinds["java"] and not kinds["python"]:
        disabled.append("python")
    if not repository_has_assets(repository_root):
        disabled.append("repo")
    if not kinds["git"] and not has_github:
        disabled.append("git")
    return disabled


def describe_selection(schemas, disabled) -> str:
    """Строка для консоли: сколько инструментов реально уходит в запрос."""
    kept = filter_tools(schemas, disabled)
    groups, names = split_disabled(disabled)
    if not groups and not names:
        return f"в запросе {len(kept)} инструментов"
    hidden = [group_label(key) for key in sorted(groups)] + sorted(names)
    return f"в запросе {len(kept)} инструментов (скрыто: {', '.join(hidden)})"


# ------------------------------------------------- журнал инструментов (кратко)

# Аргументы, которые реально объясняют, что делает инструмент.
_SUMMARY_KEYS = ("path", "dest", "archive", "member", "pattern", "query",
                 "command", "script", "url", "model", "message", "repo_name",
                 "tasks", "name", "project", "start_line", "connection", "remote")


def _clip(text, limit: int) -> str:
    """Схлопнуть пробелы и обрезать строку, чтобы журнал не разъезжался."""
    value = " ".join(str(text).split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)] + "…"


def short_tool_call(name: str, args: dict, limit: int = 72) -> str:
    """Короткая строка журнала: «[Tool] python_run · код 214 симв.: import…».

    Раньше в консоль уходил JSON аргументов на несколько строк, и по журналу
    нельзя было понять, что происходит. Здесь только суть: путь, команда или
    размер кода. Полный дамп остаётся доступен кнопкой «Полный» в панели.
    """
    try:
        data = dict(args or {})
    except Exception:  # noqa: BLE001
        return f"[Tool] {name}"

    parts = []
    code = data.get("code")
    if isinstance(code, str) and code.strip():
        first_line = code.strip().splitlines()[0]
        parts.append(f"код {len(code)} симв.: {_clip(first_line, 34)}")
    files = data.get("files")
    if isinstance(files, list) and files:
        parts.append(f"{len(files)} файл(ов)")
    build = data.get("tasks") or data.get("extra_args")
    if isinstance(build, str) and build.strip() and name == "java_build":
        parts.append(_clip(build, 34))
    for key in _SUMMARY_KEYS:
        if len(parts) >= 3:
            break
        value = data.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "script" and code:
            continue
        parts.append(_clip(f"{key}={value}", 34))
    if not parts:
        for key, value in list(data.items())[:2]:
            if value in (None, "", [], {}):
                continue
            parts.append(_clip(f"{key}={value}", 28))
    summary = ", ".join(parts) if parts else "без аргументов"
    return f"[Tool] {name} · " + _clip(summary, limit)
