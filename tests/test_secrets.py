import os

import pytest
from PyQt5.QtCore import QSettings

from app.config.secrets import ENV_API_KEY, SecretStore, mask_key, redact_text
from app.config.settings import AppSettings


# ------------------------------------------------------------------ маскировка

def test_mask_keeps_only_tail():
    masked = mask_key("sk-1tk2KikLcOWmYeSUCFyb8RIFC9OWzRmI")
    assert masked.startswith("sk-***")
    assert masked.endswith("zRmI")
    assert "1tk2KikL" not in masked
    assert mask_key("") == "***"


def test_redact_removes_own_key():
    key = "sk-abcdef1234567890"
    text = f"Incorrect API key provided: {key}. Check your account."
    out = redact_text(text, key)
    assert key not in out
    assert "***" in out
    assert "Check your account" in out


def test_redact_catches_unknown_keys():
    out = redact_text("Authorization: Bearer sk-other9876543210 sent")
    assert "sk-other9876543210" not in out

    out2 = redact_text('{"api_key": "sk-third1111111111"}')
    assert "sk-third1111111111" not in out2


def test_redact_is_safe_on_empty():
    assert redact_text(None) == ""
    assert redact_text("обычный текст без ключей", "") == "обычный текст без ключей"


# -------------------------------------------------------------------- хранилище

def test_env_has_priority_over_file(tmp_path, monkeypatch):
    store = SecretStore(str(tmp_path / "secrets.ini"))
    store.set("sk-fromfile111111")
    monkeypatch.setenv(ENV_API_KEY, "sk-fromenv222222")

    assert store.get() == "sk-fromenv222222"
    assert store.source() == "env"


def test_key_written_outside_project(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    secrets_path = tmp_path / "profile" / "secrets.ini"
    store = SecretStore(str(secrets_path))
    store.set("sk-stored3333333")

    assert secrets_path.exists()
    assert store.get() == "sk-stored3333333"
    assert store.source() == "file"


# -------------------------------------------------------- миграция старого ключа

def test_legacy_key_migrated_out_of_project(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    settings_path = tmp_path / "settings.ini"

    legacy = QSettings(str(settings_path), QSettings.IniFormat)
    legacy.setValue("api/key", "sk-legacy3333333333")
    legacy.sync()

    app = AppSettings(settings_path=str(settings_path),
                      secrets_path=str(tmp_path / "secrets.ini"))
    assert app.get_api_key() == "sk-legacy3333333333"

    # ключ ушёл в профиль и вычищен из файла внутри проекта
    assert (tmp_path / "secrets.ini").exists()
    check = QSettings(str(settings_path), QSettings.IniFormat)
    assert not check.value("api/key", "")
    assert "sk-legacy3333333333" not in settings_path.read_text(encoding="utf-8")


def test_set_api_key_never_writes_project_file(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    settings_path = tmp_path / "settings.ini"
    app = AppSettings(settings_path=str(settings_path),
                      secrets_path=str(tmp_path / "secrets.ini"))

    app.set_api_key("sk-new444444444")

    assert app.get_api_key() == "sk-new444444444"
    # файл настроек может вообще не создаться (ключ в него больше не пишется)
    text = settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""
    assert "sk-new444444444" not in text
