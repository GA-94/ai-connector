"""Мост между интерфейсом и модулем удалённых подключений.

Держит один RemoteStore на всё приложение (путь к базе чатов), чтобы окна
настроек и агент смотрели в одни и те же данные, и умеет аккуратно проверить
подключение из окна (закрывая всё, что открыло).
"""

from app.config.remote_store import RemoteSecrets, RemoteStore
from app.tools.remote_common import RemoteError
from app.tools.remote_storage import make_storage
from app.tools import remote_terminal

_STORES = {}


def store_for(db_path: str) -> RemoteStore:
    """Один экземпляр RemoteStore на путь к базе."""
    key = str(db_path or "")
    if key not in _STORES:
        _STORES[key] = RemoteStore(key, RemoteSecrets())
    return _STORES[key]


def test_connection(store: RemoteStore, profile: dict) -> str:
    """Проверить подключение и вернуть текст результата для человека."""
    if store is None or not profile:
        return "Нет данных для проверки."
    creds = store.credentials(profile.get("id") or 0)
    kind = str(profile.get("kind") or "terminal").lower()
    name = profile.get("name") or profile.get("host") or "?"
    try:
        if kind == "terminal":
            if not (creds.get("password") or creds.get("private_key")):
                return "Не задан пароль или приватный ключ."
            session = remote_terminal.open_session(
                0, profile.get("id") or 0, profile, creds,
                timeout=float(profile.get("timeout") or 15))
            result = session.run("uname -a 2>/dev/null || ver", timeout=15)
            line = (result.get("stdout") or "").strip().splitlines()
            return "OK: " + (line[0] if line else session.describe())
        backend = make_storage(profile, creds, timeout=float(profile.get("timeout") or 15))
        with backend as opened:
            rows = opened.list(".")
        return f"OK: в корне видно объектов: {len(rows)}"
    except RemoteError as exc:
        return "Ошибка: " + str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"Ошибка: {type(exc).__name__}: {exc}"
    finally:
        if kind == "terminal":
            remote_terminal.close_session(0, profile.get("id") or 0)
