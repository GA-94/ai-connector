"""Импорт базы данных чатов (data/chats.db).

Пользователь выбирает .db/.sqlite файл (например, базу с другого компьютера),
программа проверяет, что это действительно база AI-Connector, делает резервную
копию текущей базы и подменяет файл. После этого список чатов перечитывается.

Ничего не удаляется безвозвратно: старая база остаётся в data/backups/.
"""

import datetime
import os
import shutil
import sqlite3

from app.runtime_env import normalize_path

# таблица -> обязательные колонки
REQUIRED_COLUMNS = {
    "sessions": {"id", "title"},
    "messages": {"id", "session_id", "role", "content"},
}


def inspect_database(path: str) -> dict:
    """Проверить базу, ничего не меняя.

    -> {ok, message, path, sessions, messages}
    """
    target = normalize_path(path)
    result = {"ok": False, "message": "", "path": target, "sessions": 0, "messages": 0}
    if not target:
        result["message"] = "Путь к файлу не задан"
        return result
    if not os.path.isfile(target):
        result["message"] = "Файл не найден: " + target
        return result
    try:
        if os.path.getsize(target) == 0:
            result["message"] = "Файл пустой (0 байт)"
            return result
    except OSError as error:
        result["message"] = f"Не удалось прочитать файл: {error}"
        return result

    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    except sqlite3.Error as error:
        result["message"] = f"Это не база SQLite: {error}"
        return result

    try:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table, columns in REQUIRED_COLUMNS.items():
            if table not in names:
                result["message"] = (
                    f"В файле нет таблицы {table!r} — это не база AI-Connector. "
                    f"Найдено: {', '.join(sorted(names)) or 'ничего'}")
                return result
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            missing = columns - present
            if missing:
                result["message"] = (f"Таблица {table!r} не подходит: нет колонок "
                                     + ", ".join(sorted(missing)))
                return result
        result["sessions"] = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        result["messages"] = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        result["ok"] = True
        result["message"] = (f"База подходит: {result['sessions']} чат(ов), "
                             f"{result['messages']} сообщений")
        return result
    except sqlite3.Error as error:
        result["message"] = f"Не удалось прочитать базу: {error}"
        return result
    finally:
        conn.close()


def backup_database(db_path: str, backup_dir: str = "") -> str:
    """Скопировать текущую базу в data/backups/. -> путь к копии или ""."""
    source = normalize_path(db_path)
    if not source or not os.path.isfile(source):
        return ""
    folder = normalize_path(backup_dir) or normalize_path(
        os.path.join(os.path.dirname(source), "backups"))
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = os.path.splitext(os.path.basename(source))[0] or "chats"
    target = os.path.join(folder, f"{name}-{stamp}.db")
    shutil.copy2(source, target)
    return normalize_path(target)


def import_database(source: str, db_path: str, make_backup: bool = True) -> dict:
    """Подменить текущую базу выбранным файлом.

    -> {ok, message, backup, imported}
    """
    src = normalize_path(source)
    dst = normalize_path(db_path)
    info = inspect_database(src)
    if not info["ok"]:
        return {"ok": False, "message": info["message"], "backup": "", "imported": info}
    if os.path.abspath(src) == os.path.abspath(dst):
        return {"ok": False, "message": "Выбранный файл — это и есть текущая база.",
                "backup": "", "imported": info}

    backup = ""
    if make_backup:
        try:
            backup = backup_database(dst)
        except OSError as error:
            return {"ok": False,
                    "message": f"Не удалось сделать резервную копию: {error}",
                    "backup": "", "imported": info}

    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        # WAL/SHM от старой базы нельзя оставлять рядом с новой.
        for sidecar in (dst + "-wal", dst + "-shm", dst + "-journal"):
            if os.path.exists(sidecar):
                os.remove(sidecar)
        shutil.copy2(src, dst)
    except OSError as error:
        return {"ok": False, "message": f"Не удалось скопировать базу: {error}",
                "backup": backup, "imported": info}

    return {"ok": True,
            "message": (f"Импортировано: {info['sessions']} чат(ов), "
                        f"{info['messages']} сообщений"),
            "backup": backup, "imported": info}
