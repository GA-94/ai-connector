"""Удалённые подключения: профили в базе чатов, пароли — вне базы.

Правила (важны для безопасности и для совместимости с прежними базами):
  * адрес, порт, логин, протокол и прочие параметры лежат в базе ЧАТОВ, у
    каждого чата свой набор подключений — чаты не видят друг друга;
  * пароли, приватные ключи и токены в базу НЕ попадают:
      Windows       HKEY_CURRENT_USER\\Software\\Ai-Connector7\\RemoteSecrets
      Linux/macOS   ~/.config/Ai-Connector7/remote_secrets.ini (права 0600)
      переопределить: переменная окружения AI_CONNECTOR_REMOTE_SECRETS
  * таблицы создаются через CREATE TABLE IF NOT EXISTS, поэтому базы прежних
    версий (v0.5.x / v0.6.x) открываются как есть: у старых чатов просто нет
    подключений, никакой миграции данных не требуется.
"""

import configparser
import json
import os
import re
import sqlite3
from pathlib import Path

ENV_REMOTE_SECRETS = "AI_CONNECTOR_REMOTE_SECRETS"
APP_DIR_NAME = "AiConnector7"
REG_SUBKEY = r"Software\Ai-Connector7\RemoteSecrets"
FILE_SECTION = "remote"

TERMINAL_PROTOCOLS = ("ssh",)
STORAGE_PROTOCOLS = ("sftp", "scp", "ftp", "smb", "s3", "webdav")

DEFAULT_PORTS = {"ssh": 22, "sftp": 22, "scp": 22, "ftp": 21,
                 "smb": 445, "s3": 443, "webdav": 443}

# поля секретов: в базе их нет, только в реестре/файле конфигов
SECRET_FIELDS = ("password", "private_key", "passphrase",
                 "access_key", "secret_key", "token")

FIELD_LABELS = {
    "password": "пароль",
    "private_key": "приватный ключ",
    "passphrase": "пароль ключа",
    "access_key": "access key",
    "secret_key": "secret key",
    "token": "токен",
}

PROTOCOL_LABELS = {
    "ssh": "SSH (терминал)",
    "sftp": "SFTP",
    "scp": "SCP",
    "ftp": "FTP / FTPS",
    "smb": "SMB / CIFS",
    "s3": "Amazon S3",
    "webdav": "WebDAV",
}


def protocols_for(kind: str) -> tuple:
    return TERMINAL_PROTOCOLS if str(kind or "").lower() == "terminal" else STORAGE_PROTOCOLS


def default_port(protocol: str) -> int:
    return DEFAULT_PORTS.get(str(protocol or "").lower(), 22)


def _safe_name(name: str) -> str:
    """Имя секрета, пригодное и для реестра (без '\\'), и для ini-файла."""
    text = str(name or "").strip()
    text = re.sub(r"[^0-9A-Za-z_.\-]+", "_", text)
    return text.strip("_") or "secret"


def default_secrets_path() -> str:
    override = (os.environ.get(ENV_REMOTE_SECRETS) or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME, "remote_secrets.ini")


# ------------------------------------------------------------------- секреты

class RemoteSecrets:
    """Пароли/ключи удалённых подключений вне базы и вне папки проекта.

    Windows — реестр (as requested), Linux/macOS — файл конфигов с правами
    0600. Если реестр недоступен (нет прав, нестандартная сборка), молча
    переходим на файл: работать важнее, чем соблюсти букву.
    """

    def __init__(self, path: str = ""):
        self.path = os.path.abspath(os.path.expanduser(path)) if path else default_secrets_path()
        # Явный путь (аргумент или AI_CONNECTOR_REMOTE_SECRETS) = «храни в файле»:
        # это и перенос данных, и предсказуемое поведение в тестах.
        self.force_file = bool(path) or bool((os.environ.get(ENV_REMOTE_SECRETS) or "").strip())
        self._registry_ok = None

    # --- реестр
    def _winreg(self):
        try:
            import winreg

            return winreg
        except Exception:  # noqa: BLE001
            return None

    def _open_key(self, write: bool = False, create: bool = False):
        """Открыть ключ реестра: (модуль winreg, PyHKEY) или (None, None).

        Модуль и дескриптор возвращаются вместе: winreg-API — это функции
        модуля, принимающие ключ (winreg.QueryValueEx(key, ...)), поэтому
        путать их нельзя. Закрывать ключ — только winreg.CloseKey(key).
        """
        if self.force_file or os.name != "nt" or self._registry_ok is False:
            return None, None
        winreg = self._winreg()
        if winreg is None:
            self._registry_ok = False
            return None, None
        access = winreg.KEY_READ | (winreg.KEY_WRITE if write else 0)
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0, access)
        except FileNotFoundError:
            if not write or not create:
                # реестр есть, ключа пока нет: писать сюда нечего
                self._registry_ok = True
                return None, None
            try:
                key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0,
                                         winreg.KEY_READ | winreg.KEY_WRITE)
            except OSError:
                self._registry_ok = False
                return None, None
        except OSError:
            self._registry_ok = False
            return None, None
        self._registry_ok = True
        return winreg, key

    @staticmethod
    def _close(winreg, key) -> None:
        if winreg is None or key is None:
            return
        try:
            winreg.CloseKey(key)
        except Exception:  # noqa: BLE001
            try:
                key.Close()
            except Exception:  # noqa: BLE001
                pass

    def _registry_available(self) -> bool:
        if self.force_file or os.name != "nt":
            return False
        if self._registry_ok is not None:
            return bool(self._registry_ok)
        winreg, key = self._open_key(write=False)
        self._close(winreg, key)
        return self._registry_ok is not False

    @property
    def backend(self) -> str:
        return "registry" if self._registry_available() else "file"

    def describe(self) -> str:
        if self._registry_available():
            return "реестр Windows: HKEY_CURRENT_USER\\" + REG_SUBKEY
        return "файл: " + self.path

    # --- ini-файл (Linux/macOS и запасной вариант)
    def _read_ini(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        parser = configparser.ConfigParser()
        try:
            parser.read(self.path, encoding="utf-8")
        except Exception:  # noqa: BLE001
            return {}
        if not parser.has_section(FILE_SECTION):
            return {}
        return {key: value for key, value in parser.items(FILE_SECTION)}

    def _write_ini(self, data: dict) -> None:
        parser = configparser.ConfigParser()
        parser[FILE_SECTION] = {key: str(value) for key, value in data.items()}
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            parser.write(handle)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # --- публичный интерфейс
    def names(self) -> list:
        winreg, key = self._open_key()
        if key is not None:
            items = []
            try:
                try:
                    count = int(winreg.QueryInfoKey(key)[1])
                except OSError:
                    count = 0
                for index in range(count):
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    if str(value or "").strip():
                        items.append(name)
            finally:
                self._close(winreg, key)
            return sorted(items)
        return sorted(key for key, value in self._read_ini().items() if str(value).strip())

    def get(self, name: str, default: str = "") -> str:
        safe = _safe_name(name)
        winreg, key = self._open_key()
        if key is not None:
            try:
                try:
                    value, _kind = winreg.QueryValueEx(key, safe)
                except OSError:
                    return default
                return str(value or "") or default
            finally:
                self._close(winreg, key)
        return str(self._read_ini().get(safe, "") or "") or default

    def set(self, name: str, value) -> None:
        safe = _safe_name(name)
        text = str(value or "")
        if not text:
            self.delete(name)
            return
        if os.name == "nt" and not self.force_file and self._registry_ok is not False:
            winreg = self._winreg()
            if winreg is not None:
                key = None
                try:
                    key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_SUBKEY, 0,
                                             winreg.KEY_READ | winreg.KEY_WRITE)
                    winreg.SetValueEx(key, safe, 0, winreg.REG_SZ, text)
                    self._registry_ok = True
                    return
                except OSError:
                    self._registry_ok = False
                finally:
                    self._close(winreg, key)
        data = self._read_ini()
        data[safe] = text
        self._write_ini(data)

    def delete(self, name: str) -> bool:
        safe = _safe_name(name)
        removed = False
        winreg, key = self._open_key(write=True)
        if key is not None:
            try:
                winreg.DeleteValue(key, safe)
                removed = True
            except OSError:
                pass
            finally:
                self._close(winreg, key)
        data = self._read_ini()
        if safe in data:
            data.pop(safe, None)
            self._write_ini(data)
            removed = True
        return removed

    def delete_all(self) -> int:
        count = 0
        for name in self.names():
            if self.delete(name):
                count += 1
        return count

    def count(self) -> int:
        return len(self.names())


# ---------------------------------------------------------------- профили БД

class RemoteStore:
    """Профили подключений конкретного чата + привязка секретов."""

    def __init__(self, db_path: str, secrets: RemoteSecrets = None):
        self.db_path = str(db_path or "")
        self.secrets = secrets or RemoteSecrets()
        if self.db_path and self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------- схема
    def _connect(self):
        conn = sqlite3.connect(self.db_path or ":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS remote_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'terminal',
                    name TEXT NOT NULL DEFAULT '',
                    protocol TEXT NOT NULL DEFAULT 'ssh',
                    host TEXT DEFAULT '',
                    port INTEGER DEFAULT 0,
                    username TEXT DEFAULT '',
                    options TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_remote_conn_session "
                         "ON remote_connections(session_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS remote_chat_flags (
                    session_id INTEGER PRIMARY KEY,
                    terminal_enabled INTEGER DEFAULT 0,
                    storage_enabled INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    # --------------------------------------------------------- подключения
    @staticmethod
    def _row(row) -> dict:
        data = dict(row)
        try:
            data["options"] = json.loads(data.get("options") or "{}")
        except (json.JSONDecodeError, TypeError):
            data["options"] = {}
        if not isinstance(data["options"], dict):
            data["options"] = {}
        data["port"] = int(data.get("port") or default_port(data.get("protocol")))
        # Таймаут живёт в options (чтобы старые базы не требовали миграции),
        # но наружу отдаём его отдельным полем — так удобнее всем инструментам.
        try:
            data["timeout"] = int(data["options"].get("timeout") or 20)
        except (TypeError, ValueError):
            data["timeout"] = 20
        return data

    def secret_name(self, conn_id: int, field: str) -> str:
        return _safe_name(f"conn{int(conn_id)}_{field}")

    def list_connections(self, session_id, kind: str = None) -> list:
        sql = "SELECT * FROM remote_connections WHERE session_id = ?"
        params = [int(session_id or 0)]
        if kind:
            sql += " AND kind = ?"
            params.append(str(kind))
        sql += " ORDER BY kind, name COLLATE NOCASE, id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            data = self._row(row)
            data["secret_mask"] = self.describe_secrets(data["id"], data["protocol"])
            result.append(data)
        return result

    def get_connection(self, conn_id) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM remote_connections WHERE id = ?",
                               (int(conn_id or 0),)).fetchone()
        if not row:
            return None
        data = self._row(row)
        data["secret_mask"] = self.describe_secrets(data["id"], data["protocol"])
        return data

    def save_connection(self, data: dict) -> int:
        """Создать или обновить профиль; секреты пишутся отдельно."""
        data = dict(data or {})
        session_id = int(data.get("session_id") or 0)
        kind = str(data.get("kind") or "terminal").lower()
        protocol = str(data.get("protocol") or ("ssh" if kind == "terminal" else "sftp")).lower()
        name = str(data.get("name") or "").strip() or f"{PROTOCOL_LABELS.get(protocol, protocol)}"
        host = str(data.get("host") or "").strip()
        port = int(data.get("port") or default_port(protocol))
        username = str(data.get("username") or "").strip()
        options = data.get("options") or {}
        if not isinstance(options, dict):
            options = {}
        try:
            if data.get("timeout"):
                options["timeout"] = int(data["timeout"])
        except (TypeError, ValueError):
            pass
        payload = json.dumps(options, ensure_ascii=False)
        conn_id = int(data.get("id") or 0)
        with self._connect() as conn:
            if conn_id:
                conn.execute(
                    "UPDATE remote_connections SET session_id = ?, kind = ?, name = ?, "
                    "protocol = ?, host = ?, port = ?, username = ?, options = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (session_id, kind, name, protocol, host, port, username, payload, conn_id))
            else:
                cur = conn.execute(
                    "INSERT INTO remote_connections (session_id, kind, name, protocol, "
                    "host, port, username, options) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, kind, name, protocol, host, port, username, payload))
                conn_id = int(cur.lastrowid)
        for field in SECRET_FIELDS:
            if field in data.get("secrets", {}) or field in data:
                value = (data.get("secrets") or {}).get(field, data.get(field))
                if value is not None:
                    self.set_secret(conn_id, field, value)
        return conn_id

    def delete_connection(self, conn_id) -> bool:
        conn_id = int(conn_id or 0)
        if not conn_id:
            return False
        for field in SECRET_FIELDS:
            self.secrets.delete(self.secret_name(conn_id, field))
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM remote_connections WHERE id = ?", (conn_id,))
        return bool(cur.rowcount)

    def resolve(self, session_id, kind: str, ref=None) -> dict:
        """Найти подключение по id или имени (пусто — первое в этом чате)."""
        items = self.list_connections(session_id, kind)
        if not items:
            raise LookupError(
                "для этого чата не настроены подключения "
                f"({PROTOCOL_LABELS.get('ssh') if kind == 'terminal' else 'хранилища'}). "
                "Открой «Инструменты → Удалённый доступ» и добавь подключение")
        text = str(ref or "").strip()
        if not text:
            return items[0]
        if text.isdigit():
            for item in items:
                if int(item["id"]) == int(text):
                    return item
        low = text.lower()
        for item in items:
            if str(item["name"]).lower() == low:
                return item
        for item in items:
            if low in str(item["name"]).lower():
                return item
        raise LookupError(
            f"подключение {text!r} не найдено в этом чате. Есть: "
            + ", ".join(str(item["name"]) for item in items))

    # ------------------------------------------------------------- секреты
    def set_secret(self, conn_id, field: str, value) -> None:
        self.secrets.set(self.secret_name(conn_id, field), value)

    def get_secret(self, conn_id, field: str, default: str = "") -> str:
        return self.secrets.get(self.secret_name(conn_id, field), default)

    def credentials(self, conn_id) -> dict:
        """Все секреты подключения (только в память, никогда в базу)."""
        return {field: self.get_secret(conn_id, field) for field in SECRET_FIELDS}

    def has_secret(self, conn_id, field: str) -> bool:
        return bool(self.get_secret(conn_id, field))

    def describe_secrets(self, conn_id, protocol: str = "") -> str:
        """Короткая строка для таблицы: какие секреты уже введены."""
        protocol = str(protocol or "").lower()
        if protocol == "s3":
            fields = ("access_key", "secret_key")
        elif protocol == "webdav":
            fields = ("password", "token")
        else:
            fields = ("password", "private_key")
        present = [FIELD_LABELS[f] for f in fields if self.get_secret(conn_id, f)]
        return ", ".join(present) if present else "не задано"

    def list_secrets(self) -> list:
        """Что вообще лежит в реестре/файле: для окна глобальных настроек."""
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM remote_connections").fetchall()
        except sqlite3.Error:
            rows = []
        by_id = {int(row["id"]): self._row(row) for row in rows}
        items = []
        for name in self.secrets.names():
            match = re.match(r"conn(\d+)_(.+)$", name)
            conn_id, field = 0, name
            if match:
                conn_id = int(match.group(1))
                field = match.group(2)
            conn = by_id.get(conn_id)
            if conn:
                label = (f"Чат #{conn['session_id']} · "
                         f"{PROTOCOL_LABELS.get(conn['protocol'], conn['protocol'])} "
                         f"«{conn['name']}» · {FIELD_LABELS.get(field, field)}")
            else:
                label = f"{name} (подключение удалено)"
            items.append({"name": name, "conn_id": conn_id, "field": field,
                          "label": label, "orphan": conn is None})
        return items

    def delete_secret(self, name: str) -> bool:
        return self.secrets.delete(name)

    def delete_orphan_secrets(self) -> int:
        removed = 0
        for item in self.list_secrets():
            if item.get("orphan") and self.secrets.delete(item["name"]):
                removed += 1
        return removed

    def delete_all_secrets(self) -> int:
        return self.secrets.delete_all()

    # --------------------------------------------------------- флаги чата
    def get_chat_flags(self, session_id) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM remote_chat_flags WHERE session_id = ?",
                               (int(session_id or 0),)).fetchone()
        if not row:
            return {"session_id": int(session_id or 0),
                    "terminal_enabled": False, "storage_enabled": False}
        return {"session_id": int(row["session_id"]),
                "terminal_enabled": bool(row["terminal_enabled"]),
                "storage_enabled": bool(row["storage_enabled"])}

    def set_chat_flags(self, session_id, terminal_enabled=None, storage_enabled=None) -> None:
        session_id = int(session_id or 0)
        current = self.get_chat_flags(session_id)
        terminal = current["terminal_enabled"] if terminal_enabled is None else bool(terminal_enabled)
        storage = current["storage_enabled"] if storage_enabled is None else bool(storage_enabled)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO remote_chat_flags (session_id, terminal_enabled, storage_enabled) "
                "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "terminal_enabled = excluded.terminal_enabled, "
                "storage_enabled = excluded.storage_enabled, "
                "updated_at = CURRENT_TIMESTAMP",
                (session_id, int(terminal), int(storage)))

    def describe_chat(self, session_id) -> str:
        """Строка для системного промпта: что доступно в этом чате."""
        flags = self.get_chat_flags(session_id)
        lines = []
        terminals = self.list_connections(session_id, "terminal")
        storages = self.list_connections(session_id, "storage")
        if terminals:
            state = "включён" if flags["terminal_enabled"] else "выключен"
            items = "; ".join(
                f"{item['name']} ({item['protocol']} {item['host']}:{item['port']}, "
                f"логин {item['username'] or '—'}, секреты: {item['secret_mask']})"
                for item in terminals)
            lines.append(f"терминал {state}: {items}")
        if storages:
            state = "включён" if flags["storage_enabled"] else "выключен"
            items = "; ".join(
                f"{item['name']} ({item['protocol']} {item['host']}:{item['port']}, "
                f"логин {item['username'] or '—'})" for item in storages)
            lines.append(f"хранилища {state}: {items}")
        return " | ".join(lines)

    def close_chat(self, session_id) -> None:
        """Убрать всё, что относилось к чату (при его удалении)."""
        for item in self.list_connections(session_id):
            self.delete_connection(item["id"])
        with self._connect() as conn:
            conn.execute("DELETE FROM remote_chat_flags WHERE session_id = ?",
                         (int(session_id or 0),))
