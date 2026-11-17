"""Общее для удалённых подключений: ленивые импорты, SSH-клиент, пути.

paramiko / boto3 / smbclient НЕ импортируются на уровне модуля: на ARM
устройствах и в минимальных установках этих пакетов может не быть, а сама
программа обязана запускаться и работать с остальными инструментами.
Поэтому каждый бэкенд подтягивает зависимость в момент подключения и
объясняет человеку, что поставить, если её нет.
"""

import importlib
import io
import posixpath


class RemoteError(RuntimeError):
    """Ошибка подключения/обмена с текстом, который можно показать человеку."""


HINTS = {
    "paramiko": "pip install paramiko  (чистый Python + cryptography, есть колеса под ARM)",
    "boto3": "pip install boto3  (Amazon S3 и совместимые хранилища)",
    "smbclient": "pip install smbclient  (чистый Python через smbprotocol, годится для ARM)",
    "smbprotocol": "pip install smbprotocol",
}


def need(module: str):
    """Импортировать модуль по требованию или объяснить, чего не хватает."""
    try:
        return importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - любая причина = «нет модуля»
        hint = HINTS.get(module, "pip install " + module)
        raise RemoteError(
            f"не установлен модуль {module} ({type(exc).__name__}: {exc}). "
            f"Установите: {hint}") from exc


def has_module(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------- SSH

def load_private_key(paramiko_module, text: str, passphrase: str = ""):
    """Разобрать приватный ключ из текста (OpenSSH или PEM, любой тип)."""
    password = (passphrase or "").strip() or None
    errors = []
    for name in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey"):
        cls = getattr(paramiko_module, name, None)
        if cls is None:
            continue
        try:
            return cls.from_private_key(io.StringIO(text), password=password)
        except Exception as exc:  # noqa: BLE001 - пробуем следующий тип
            errors.append(f"{name}: {exc}")
    raise RemoteError(
        "не удалось прочитать приватный ключ. Проверьте, что вставлен ключ целиком "
        "(от -----BEGIN ... KEY----- до -----END ... KEY-----) и верный пароль ключа. "
        + (" | " + " ; ".join(errors[-2:]) if errors else ""))


def target_of(profile: dict, default_port: int = 22) -> str:
    host = str((profile or {}).get("host") or "").strip() or "?"
    port = int((profile or {}).get("port") or default_port)
    return f"{host}:{port}"


def ssh_client(profile: dict, credentials: dict, timeout: float = 20.0):
    """Подключиться по SSH и вернуть paramiko.SSHClient.

    Пароль и ключ берутся ТОЛЬКО из хранилища секретов (реестр Windows или
    файл конфигов) — в базе чатов их нет.
    """
    paramiko = need("paramiko")
    profile = dict(profile or {})
    credentials = dict(credentials or {})
    host = str(profile.get("host") or "").strip()
    if not host:
        raise RemoteError("в настройках подключения не задан адрес сервера (host)")
    username = str(profile.get("username") or "").strip()
    if not username:
        raise RemoteError("в настройках подключения не задан логин (username)")
    port = int(profile.get("port") or 22)
    options = dict(profile.get("options") or {})

    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
    except Exception:  # noqa: BLE001 - на некоторых системах файла просто нет
        pass
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    kwargs = dict(
        hostname=host, port=port, username=username, timeout=timeout,
        banner_timeout=timeout, auth_timeout=timeout,
        allow_agent=bool(options.get("allow_agent", False)),
        look_for_keys=False,
    )
    password = str(credentials.get("password") or "")
    key_text = str(credentials.get("private_key") or "").strip()
    if key_text:
        kwargs["pkey"] = load_private_key(
            paramiko, key_text, str(credentials.get("passphrase") or ""))
        if password:
            kwargs["password"] = password
    elif password:
        kwargs["password"] = password
    else:
        raise RemoteError(
            "нет ни пароля, ни приватного ключа: открой «Инструменты → "
            "Удалённый доступ» и введите их для этого чата")

    try:
        client.connect(**kwargs)
    except paramiko.AuthenticationException as exc:
        raise RemoteError(f"сервер {host}:{port} отклонил логин/ключ ({exc})") from exc
    except Exception as exc:  # noqa: BLE001
        raise RemoteError(f"не удалось подключиться к {host}:{port}: {exc}") from exc
    return client


# -------------------------------------------------------------------- пути

def clean_path(path: str) -> str:
    """Нормализовать удалённый путь в posix-вид ('/' разделители, без '..')."""
    text = str(path or "").replace("\\", "/").strip()
    if not text:
        return "/"
    absolute = text.startswith("/")
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    result = "/".join(parts)
    if absolute:
        result = "/" + result
    return result or ("/" if absolute else ".")


def join_remote(base: str, path: str) -> str:
    """Склеить корень хранилища (options.root) с относительным путём."""
    base = str(base or "").strip()
    path = str(path or "").strip()
    if path.startswith("/") or not base:
        return clean_path(path)
    return clean_path(posixpath.join(base.replace("\\", "/"), path))
