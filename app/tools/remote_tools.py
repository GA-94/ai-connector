"""Инструменты удалённого доступа для агента: SSH-терминал и хранилища.

Ключевая идея: подключения берутся из настроек КОНКРЕТНОГО чата. Два чата
работают независимо: у каждого свои адреса, логины, пароли и сессии. Пароли
лежат в реестре Windows или в файле конфигов Linux (см. app/config/remote_store.py).
"""

import os
import posixpath

from app.config.remote_store import PROTOCOL_LABELS
from app.tools import remote_terminal
from app.tools.remote_common import RemoteError
from app.tools.remote_storage import make_storage

# ------------------------------------------------------------------- схемы

_CONNECTION_ARG = {
    "type": "string",
    "description": "Имя (или id) подключения из настроек этого чата. "
                   "Пусто = первое подключение этого чата.",
}

TERMINAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remote_terminal_list",
            "description": "Список SSH-подключений, настроенных для ТЕКУЩЕГО чата "
                           "(имя, адрес, логин, какие секреты заданы). Пароли не показываются.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_terminal_open",
            "description": "Подключиться к удалённому серверу по SSH из настроек этого чата "
                           "и открыть интерактивный терминал. Логин/пароль/ключ подставляются "
                           "автоматически из хранилища секретов.",
            "parameters": {
                "type": "object",
                "properties": {"connection": _CONNECTION_ARG},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_terminal_exec",
            "description": "Выполнить команду в удалённом терминале (SSH): оболочка одна на чат, "
                           "поэтому cd/export сохраняются между вызовами. Возвращает код возврата, "
                           "stdout и stderr. Соединение открывается автоматически.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "Команда оболочки, например 'ls -la /var/log'"},
                    "connection": _CONNECTION_ARG,
                    "timeout_seconds": {"type": "integer",
                                        "description": "Таймаут ожидания, по умолчанию 60"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_terminal_close",
            "description": "Закрыть SSH-соединение этого чата (все или конкретное).",
            "parameters": {
                "type": "object",
                "properties": {"connection": _CONNECTION_ARG},
                "required": [],
            },
        },
    },
]

_STORAGE_BASE = {
    "connection": _CONNECTION_ARG,
    "project": {"type": "string",
                "description": "Проект для локальной стороны (если открыто несколько)"},
}

STORAGE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remote_storage_list",
            "description": "Список удалённых хранилищ (SMB, FTP, SFTP, S3, WebDAV, SCP), "
                           "настроенных для ТЕКУЩЕГО чата.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_storage_ls",
            "description": "Показать содержимое папки в удалённом хранилище (любой протокол).",
            "parameters": {
                "type": "object",
                "properties": dict(_STORAGE_BASE, path={
                    "type": "string",
                    "description": "Папка в хранилище, по умолчанию '.' (корень)"}),
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_storage_download",
            "description": "Скачать файл из удалённого хранилища в проект (локальный путь только "
                           "внутри папок проекта).",
            "parameters": {
                "type": "object",
                "properties": dict(
                    _STORAGE_BASE,
                    remote={"type": "string", "description": "Путь файла в хранилище"},
                    dest={"type": "string",
                          "description": "Куда сохранить в проекте, например 'downloads/log.txt'"},
                    overwrite={"type": "boolean",
                               "description": "Перезаписать локальный файл, по умолчанию да"}),
                "required": ["remote", "dest"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_storage_upload",
            "description": "Загрузить файл из проекта в удалённое хранилище.",
            "parameters": {
                "type": "object",
                "properties": dict(
                    _STORAGE_BASE,
                    src={"type": "string", "description": "Локальный файл в проекте"},
                    remote={"type": "string",
                            "description": "Путь в хранилище или папка (тогда имя файла сохраняется)"}),
                "required": ["src", "remote"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_storage_mkdir",
            "description": "Создать папку в удалённом хранилище.",
            "parameters": {
                "type": "object",
                "properties": dict(_STORAGE_BASE, path={
                    "type": "string", "description": "Путь новой папки"}),
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_storage_rename",
            "description": "Переименовать или переместить файл/папку в удалённом хранилище.",
            "parameters": {
                "type": "object",
                "properties": dict(
                    _STORAGE_BASE,
                    path={"type": "string", "description": "Откуда"},
                    new_path={"type": "string", "description": "Куда"}),
                "required": ["path", "new_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_storage_delete",
            "description": "Удалить файл или папку в удалённом хранилище. Папка удаляется "
                           "рекурсивно только при recursive=true (удаление необратимо).",
            "parameters": {
                "type": "object",
                "properties": dict(
                    _STORAGE_BASE,
                    path={"type": "string", "description": "Что удалить"},
                    recursive={"type": "boolean",
                               "description": "Удалить папку со всем содержимым"}),
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remote_storage_info",
            "description": "Информация о файле/папке в удалённом хранилище: существует ли, "
                           "размер, тип, время изменения.",
            "parameters": {
                "type": "object",
                "properties": dict(_STORAGE_BASE, path={
                    "type": "string", "description": "Путь"}),
                "required": ["path"],
            },
        },
    },
]

REMOTE_TOOLS = TERMINAL_TOOLS + STORAGE_TOOLS
TERMINAL_NAMES = tuple(schema["function"]["name"] for schema in TERMINAL_TOOLS)
STORAGE_NAMES = tuple(schema["function"]["name"] for schema in STORAGE_TOOLS)
ALL_NAMES = TERMINAL_NAMES + STORAGE_NAMES


def _human_size(size) -> str:
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return "0 Б"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} ТБ"


class RemoteContext:
    """Связка «чат + его подключения» для инструментов агента."""

    def __init__(self, store, session_id, workspace=None, log=None, cancel_check=None):
        self.store = store
        self.session_id = int(session_id or 0)
        self.workspace = workspace
        self.log = log or (lambda msg: None)
        self.cancel_check = cancel_check or (lambda: False)

    # ------------------------------------------------------------- утилиты
    def _flags(self) -> dict:
        if self.store is None:
            return {"terminal_enabled": False, "storage_enabled": False}
        return self.store.get_chat_flags(self.session_id)

    def _profile(self, kind: str, ref) -> dict:
        if self.store is None:
            raise RemoteError("удалённые подключения недоступны: нет базы чатов")
        flags = self._flags()
        if kind == "terminal" and not flags["terminal_enabled"]:
            raise RemoteError(
                "удалённый терминал выключен для этого чата: открой "
                "«Инструменты → Удалённый доступ» и поставь галочку "
                "«Включить терминал в этом чате»")
        if kind == "storage" and not flags["storage_enabled"]:
            raise RemoteError(
                "удалённые хранилища выключены для этого чата: открой "
                "«Инструменты → Удалённый доступ» и поставь галочку "
                "«Включить хранилища в этом чате»")
        try:
            return self.store.resolve(self.session_id, kind, ref)
        except LookupError as exc:
            raise RemoteError(str(exc)) from exc

    def _local(self, path: str, for_write: bool, project=None):
        if self.workspace is None:
            raise RemoteError("не задан проект: скачивать/загружать файлы некуда")
        if not str(path or "").strip():
            raise RemoteError("не указан локальный путь")
        return self.workspace.resolve(path, project=project,
                                      must_exist=not for_write, for_write=for_write)

    # ------------------------------------------------------------- диспетчер
    def handle(self, name: str, args: dict) -> str:
        args = dict(args or {})
        try:
            if name in TERMINAL_NAMES:
                return self._terminal(name, args)
            if name in STORAGE_NAMES:
                return self._storage(name, args)
            return f"[Ошибка] неизвестный инструмент {name}"
        except RemoteError as exc:
            return f"[Ошибка] {name}: {exc}"
        except Exception as exc:  # noqa: BLE001 - агент должен видеть причину
            return f"[Ошибка] {name}: {type(exc).__name__}: {exc}"

    # -------------------------------------------------------------- терминал
    def _terminal(self, name: str, args: dict) -> str:
        if name == "remote_terminal_list":
            items = self.store.list_connections(self.session_id, "terminal")
            flags = self._flags()
            if not items:
                return ("Для этого чата не настроен ни один SSH-терминал. Открой "
                        "«Инструменты → Удалённый доступ» и добавь адрес, логин и пароль.")
            rows = ["Терминал "
                    + ("включён" if flags["terminal_enabled"] else "ВЫКЛЮЧЕН") + ":"]
            for item in items:
                rows.append(
                    f"  #{item['id']} {item['name']} — {item['protocol']} "
                    f"{item['username'] or '—'}@{item['host']}:{item['port']} "
                    f"(секреты: {item['secret_mask']})")
            return "\n".join(rows)

        if name == "remote_terminal_open":
            profile = self._profile("terminal", args.get("connection"))
            creds = self.store.credentials(profile["id"])
            if not (creds.get("password") or creds.get("private_key")):
                raise RemoteError(
                    f"для подключения «{profile['name']}» не задан пароль или ключ. "
                    "Введи его в «Инструменты → Удалённый доступ»")
            session = remote_terminal.open_session(
                self.session_id, profile["id"], profile, creds,
                timeout=float(profile.get("timeout") or 20), log=self.log)
            check = session.run("uname -a 2>/dev/null || ver", timeout=20)
            lines = (check.get("stdout") or "").strip().splitlines()
            system = lines[0] if lines else "не определена"
            return (f"Подключено: {session.describe()} "
                    f"(подключение «{profile['name']}»).\n"
                    f"Система: {system}\n"
                    "Дальше — remote_terminal_exec с командами оболочки.")

        if name == "remote_terminal_exec":
            command = str(args.get("command") or "").strip()
            if not command:
                raise RemoteError("пустая команда")
            profile = self._profile("terminal", args.get("connection"))
            session = remote_terminal.get_session(self.session_id, profile["id"])
            auto = session is None
            if auto:
                creds = self.store.credentials(profile["id"])
                session = remote_terminal.open_session(
                    self.session_id, profile["id"], profile, creds,
                    timeout=float(profile.get("timeout") or 20), log=self.log)
            timeout = float(args.get("timeout_seconds") or 60)
            result = session.run(command, timeout=timeout)
            parts = [f"[{session.describe()}] $ {command}"]
            if auto:
                parts.append("(соединение открыто автоматически)")
            stdout = (result.get("stdout") or "").rstrip()
            stderr = (result.get("stderr") or "").rstrip()
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append("[stderr] " + stderr)
            if not stdout and not stderr:
                parts.append("(нет вывода)")
            parts.append(f"код возврата: {result.get('exit_code')}")
            if result.get("note"):
                parts.append("[!] " + str(result["note"]))
            return "\n".join(parts)

        if name == "remote_terminal_close":
            ref = args.get("connection")
            if ref:
                profile = self._profile("terminal", ref)
                closed = remote_terminal.close_session(self.session_id, profile["id"])
                if closed:
                    return f"Соединение «{profile['name']}» закрыто"
                return f"Соединение «{profile['name']}» и не было открыто"
            count = remote_terminal.close_chat(self.session_id)
            return f"Закрыто соединений: {count}"

        raise RemoteError(f"неизвестная операция терминала {name}")

    # ------------------------------------------------------------- хранилища
    def _storage(self, name: str, args: dict) -> str:
        if name == "remote_storage_list":
            items = self.store.list_connections(self.session_id, "storage")
            flags = self._flags()
            if not items:
                return ("Для этого чата не настроено ни одно удалённое хранилище. Открой "
                        "«Инструменты → Удалённый доступ» и добавь SMB/FTP/SFTP/S3/WebDAV/SCP.")
            rows = ["Хранилища "
                    + ("включены" if flags["storage_enabled"] else "ВЫКЛЮЧЕНЫ") + ":"]
            for item in items:
                rows.append(
                    f"  #{item['id']} {item['name']} — "
                    f"{PROTOCOL_LABELS.get(item['protocol'], item['protocol'])} "
                    f"{item['host']}:{item['port']} ({item['username'] or '—'})")
            return "\n".join(rows)

        profile = self._profile("storage", args.get("connection"))
        creds = self.store.credentials(profile["id"])
        backend = make_storage(profile, creds, timeout=float(profile.get("timeout") or 20))
        title = f"{profile['name']} ({profile['protocol']} {profile['host']})"

        if name == "remote_storage_ls":
            path = str(args.get("path") or ".")
            with backend as opened:
                rows = opened.list(path)
            if not rows:
                return f"{title}: папка {path} пуста"
            lines = [f"{title}: {path} ({len(rows)}):"]
            for row in rows[:400]:
                kind = "папка" if row["dir"] else _human_size(row["size"])
                suffix = "/" if row["dir"] else ""
                lines.append(f"  {row['name']}{suffix}  [{kind}]")
            if len(rows) > 400:
                lines.append(f"  ... ещё {len(rows) - 400} объектов")
            return "\n".join(lines)

        if name == "remote_storage_download":
            remote_path = str(args.get("remote") or "").strip()
            if not remote_path:
                raise RemoteError("не указан путь файла в хранилище (remote)")
            dest = self._local(str(args.get("dest") or ""), True, args.get("project"))
            overwrite = args.get("overwrite")
            overwrite = True if overwrite is None else bool(overwrite)
            if os.path.exists(dest) and not overwrite:
                raise RemoteError(f"файл уже существует: {dest} (передай overwrite=true)")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with backend as opened:
                size = opened.download(remote_path, str(dest))
            return f"{title}: скачано {remote_path} -> {dest} ({_human_size(size)})"

        if name == "remote_storage_upload":
            src = self._local(str(args.get("src") or ""), True, args.get("project"))
            if not src.is_file():
                raise RemoteError(f"локальный файл не найден: {src}")
            remote_target = str(args.get("remote") or "").strip() or src.name
            if remote_target.endswith("/"):
                remote_target = posixpath.join(remote_target, src.name)
            with backend as opened:
                size = opened.upload(str(src), remote_target)
            return f"{title}: загружено {src.name} -> {remote_target} ({_human_size(size)})"

        if name == "remote_storage_mkdir":
            path = str(args.get("path") or "").strip()
            if not path:
                raise RemoteError("не указан путь папки")
            with backend as opened:
                opened.mkdir(path)
            return f"{title}: папка создана: {path}"

        if name == "remote_storage_rename":
            old = str(args.get("path") or "").strip()
            new = str(args.get("new_path") or "").strip()
            if not old or not new:
                raise RemoteError("нужны path и new_path")
            with backend as opened:
                opened.rename(old, new)
            return f"{title}: {old} -> {new}"

        if name == "remote_storage_delete":
            path = str(args.get("path") or "").strip()
            if not path or path in ("/", "."):
                raise RemoteError("нельзя удалить корень хранилища")
            recursive = bool(args.get("recursive"))
            with backend as opened:
                opened.delete(path, recursive=recursive)
            return (f"{title}: удалено {path}"
                    + (" (со всем содержимым)" if recursive else ""))

        if name == "remote_storage_info":
            path = str(args.get("path") or ".")
            with backend as opened:
                info = opened.stat(path)
            kind = "папка" if info.get("dir") else "файл"
            mtime = info.get("mtime") or "—"
            return (f"{title}: {path} — {kind}, "
                    f"размер {_human_size(info.get('size'))}, изменён {mtime}")

        raise RemoteError(f"неизвестная операция хранилища {name}")
