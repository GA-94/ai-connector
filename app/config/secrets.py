"""Хранение и обезвреживание API-ключа.

Ключ платный, поэтому:
  * он больше не лежит в папке проекта (её легко закоммитить или отправить);
  * он маскируется во всех текстах, которые уходят в чат, консоль и логи.

Где хранится:
  Windows       %APPDATA%\\AI-Connector\\secrets.ini
  Linux/macOS   ~/.config/AI-Connector/secrets.ini
  любой случай  переменная окружения AI_CONNECTOR_API_KEY (приоритет выше файла)
                или путь из AI_CONNECTOR_SECRETS
"""

import os
import re

from app.qt_compat.QtCore import QSettings

ENV_API_KEY = "AI_CONNECTOR_API_KEY"
ENV_SECRETS_PATH = "AI_CONNECTOR_SECRETS"
APP_DIR_NAME = "AI-Connector"


# --------------------------------------------------------------- mask/redact

def mask_key(key: str) -> str:
    """Маска ключа: видно только хвост, по которому удобно опознать свой ключ."""
    key = (key or "").strip()
    if not key:
        return "***"
    prefix = "sk-" if key.startswith("sk-") else ""
    tail = key[-4:] if len(key) > 8 else ""
    return f"{prefix}***{tail}"


_SK_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{6,})")
_KV_RE = re.compile(
    r"(?i)((?:api[_-]?key|authorization|token)['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9._\-]{6,})"
)


def redact_text(text, key: str = "") -> str:
    """Вырезает ключ (свой и любой чужой по шаблону) из произвольного текста.

    Провайдеры любят отвечать на 401 строкой вида
    'Incorrect API key provided: sk-1tk2***RmI', поэтому тексты ошибок
    пропускаем через эту функцию перед показом пользователю.
    """
    if text is None:
        return ""
    result = str(text)

    key = (key or "").strip()
    if key:
        result = result.replace(key, mask_key(key))

    result = _SK_RE.sub(lambda m: mask_key(m.group(0)), result)
    result = _BEARER_RE.sub(lambda m: m.group(1) + mask_key(m.group(2)), result)
    result = _KV_RE.sub(lambda m: m.group(1) + mask_key(m.group(2)), result)
    return result


# ------------------------------------------------------------------- storage

def default_secrets_path() -> str:
    override = (os.environ.get(ENV_SECRETS_PATH) or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_DIR_NAME, "secrets.ini")


class SecretStore:
    """Файл с ключом вне папки проекта."""

    def __init__(self, path: str = ""):
        self.path = os.path.abspath(os.path.expanduser(path)) if path else default_secrets_path()

    def _settings(self) -> QSettings:
        return QSettings(self.path, QSettings.IniFormat)

    def get_env(self) -> str:
        return (os.environ.get(ENV_API_KEY) or "").strip()

    def get_file(self) -> str:
        if not os.path.exists(self.path):
            return ""
        try:
            value = self._settings().value("api/key", "")
        except Exception:
            return ""
        return str(value).strip() if value else ""

    def get(self) -> str:
        return self.get_env() or self.get_file()

    def source(self) -> str:
        if self.get_env():
            return "env"
        if self.get_file():
            return "file"
        return "none"

    def set(self, key: str) -> None:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        settings = self._settings()
        settings.setValue("api/key", (key or "").strip())
        settings.sync()
        self._harden()

    def clear(self) -> None:
        if not os.path.exists(self.path):
            return
        settings = self._settings()
        settings.remove("api/key")
        settings.sync()

    def _harden(self) -> None:
        """На POSIX закрывает файл от других пользователей (на Windows — no-op)."""
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def describe(self) -> str:
        source = self.source()
        if source == "env":
            return f"переменная окружения {ENV_API_KEY}"
        if source == "file":
            return self.path
        return "ключ не задан"

    # ------------------------------------------- именованные секреты
    # Один файл вне проекта хранит несколько секретов: ключи разных
    # провайдеров (api/openai, api/xai, ...) и токен GitHub (github/token).

    def get_value(self, name: str, default: str = "") -> str:
        name = str(name or "").strip()
        if not name:
            return default
        if name == "api/key":
            return self.get() or default
        env_name = "AI_CONNECTOR_" + name.upper().replace("/", "_").replace("-", "_")
        env_value = (os.environ.get(env_name) or "").strip()
        if env_value:
            return env_value
        if not os.path.exists(self.path):
            return default
        try:
            value = self._settings().value(name, "")
        except Exception:  # noqa: BLE001
            return default
        return str(value).strip() if value else default

    def set_value(self, name: str, value: str) -> None:
        name = str(name or "").strip()
        if not name:
            return
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        settings = self._settings()
        text = (value or "").strip()
        if text:
            settings.setValue(name, text)
        else:
            settings.remove(name)
        settings.sync()
        self._harden()

    def has_value(self, name: str) -> bool:
        return bool(self.get_value(name))
