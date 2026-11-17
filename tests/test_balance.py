"""Тесты баланса: разбор ответа провайдера, сеть, кэш, промпт агента."""
import json
import urllib.error

import pytest

from app.tools import balance_tools as bt


@pytest.fixture(autouse=True)
def clean_cache():
    bt.clear_cache()
    yield
    bt.clear_cache()


# ------------------------------------------------------------------ endpoint

def test_credits_url_from_base():
    assert bt.credits_url("https://routerai.ru/api/v1") == \
        "https://routerai.ru/api/v1/credits"
    assert bt.credits_url("https://routerai.ru/api/v1/") == \
        "https://routerai.ru/api/v1/credits"
    assert bt.credits_url("https://routerai.ru/api/v1/credits") == \
        "https://routerai.ru/api/v1/credits"
    assert bt.credits_url("", "") == ""


def test_credits_url_explicit_wins():
    assert bt.credits_url("https://a/api/v1", "https://b/custom/credits") == \
        "https://b/custom/credits"


# -------------------------------------------------------------------- parsing

@pytest.mark.parametrize("payload,expected", [
    ({"balance": 12.5}, 12.5),
    ({"credits": 100}, 100.0),
    ({"credits": "1 234,56"}, 1234.56),
    ({"data": {"balance": 3}}, 3.0),
    ({"data": {"attributes": {"remaining_credits": 7.25}}}, 7.25),
    ([{"balance": 4.5}], 4.5),
    ({"total": "10.00"}, 10.0),
    (42, 42.0),
])
def test_parse_amount(payload, expected):
    amount, _ = bt.parse_balance(payload)
    assert amount == pytest.approx(expected)


def test_parse_ignores_used_and_ids():
    amount, _ = bt.parse_balance({"credits_used": 999, "balance": 5, "id": 1234})
    assert amount == 5.0


def test_parse_currency():
    _, currency = bt.parse_balance({"balance": 5, "currency": "RUB"})
    assert currency == "RUB"


def test_parse_unknown_payload():
    amount, currency = bt.parse_balance({"foo": "bar"})
    assert amount is None
    assert currency == ""


# -------------------------------------------------------------------- network

def _fake_get(status_body):
    calls = {"n": 0}

    def _inner(url, api_key, timeout=bt.DEFAULT_TIMEOUT):
        calls["n"] += 1
        status, body = status_body
        return status, body
    return _inner, calls


def test_fetch_ok(monkeypatch):
    fake, _ = _fake_get((200, json.dumps({"balance": 88.5})))
    monkeypatch.setattr(bt, "_http_get", fake)
    result = bt.fetch_balance("sk-test-1", "https://routerai.ru/api/v1")
    assert result.ok
    assert result.amount == 88.5
    assert result.url.endswith("/credits")


def test_fetch_plain_number_response(monkeypatch):
    fake, _ = _fake_get((200, "150.25"))
    monkeypatch.setattr(bt, "_http_get", fake)
    result = bt.fetch_balance("sk-test-1", "https://routerai.ru/api/v1")
    assert result.ok
    assert result.amount == pytest.approx(150.25)


def test_fetch_401_reports_key_problem(monkeypatch):
    def _raise(url, api_key, timeout=bt.DEFAULT_TIMEOUT):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(bt, "_http_get", _raise)
    result = bt.fetch_balance("sk-bad-123456", "https://routerai.ru/api/v1")
    assert not result.ok
    assert "401" in result.message
    assert "ключ" in result.message.lower()


def test_fetch_404_suggests_base_url(monkeypatch):
    def _raise(url, api_key, timeout=bt.DEFAULT_TIMEOUT):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(bt, "_http_get", _raise)
    result = bt.fetch_balance("sk-x-123456", "https://wrong.example/api/v1")
    assert not result.ok
    assert "Base URL" in result.message


def test_fetch_network_error(monkeypatch):
    def _raise(url, api_key, timeout=bt.DEFAULT_TIMEOUT):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(bt, "_http_get", _raise)
    result = bt.fetch_balance("sk-x-123456", "https://routerai.ru/api/v1")
    assert not result.ok
    assert "сетевая" in result.message


def test_fetch_without_key_does_not_touch_network(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("сеть не должна вызываться без ключа")
    monkeypatch.setattr(bt, "_http_get", _boom)
    result = bt.fetch_balance("", "https://routerai.ru/api/v1")
    assert not result.ok
    assert "API-ключ" in result.message


def test_fetch_missing_base_url():
    result = bt.fetch_balance("sk-x-123456", "", "")
    assert not result.ok
    assert "Base URL" in result.message


def test_fetch_hides_key_in_raw(monkeypatch):
    key = "sk-secret-abcdef123456"
    fake, _ = _fake_get((200, json.dumps({"balance": 1, "echo": key})))
    monkeypatch.setattr(bt, "_http_get", fake)
    result = bt.fetch_balance(key, "https://routerai.ru/api/v1")
    assert key not in result.raw
    assert "sk-" in result.raw  # маска осталась, чтобы понять, что это ключ


def test_response_without_balance_field(monkeypatch):
    fake, _ = _fake_get((200, json.dumps({"status": "ok", "user": "x"})))
    monkeypatch.setattr(bt, "_http_get", fake)
    result = bt.fetch_balance("sk-x-123456", "https://routerai.ru/api/v1")
    assert not result.ok
    assert "поля с балансом" in result.message


# ----------------------------------------------------------------------- cache

def test_cache_prevents_second_request(monkeypatch):
    fake, calls = _fake_get((200, json.dumps({"balance": 10})))
    monkeypatch.setattr(bt, "_http_get", fake)
    first = bt.fetch_balance("sk-cache-1", "https://routerai.ru/api/v1")
    second = bt.fetch_balance("sk-cache-1", "https://routerai.ru/api/v1")
    assert calls["n"] == 1
    assert second.cached is True
    assert second.amount == first.amount


def test_fresh_bypasses_cache(monkeypatch):
    fake, calls = _fake_get((200, json.dumps({"balance": 10})))
    monkeypatch.setattr(bt, "_http_get", fake)
    bt.fetch_balance("sk-cache-2", "https://routerai.ru/api/v1")
    bt.fetch_balance("sk-cache-2", "https://routerai.ru/api/v1", fresh=True)
    assert calls["n"] == 2


def test_cache_is_per_key(monkeypatch):
    fake, calls = _fake_get((200, json.dumps({"balance": 10})))
    monkeypatch.setattr(bt, "_http_get", fake)
    bt.fetch_balance("sk-key-a-111111", "https://routerai.ru/api/v1")
    bt.fetch_balance("sk-key-b-222222", "https://routerai.ru/api/v1")
    assert calls["n"] == 2


def test_cached_balance_without_network(monkeypatch):
    assert bt.cached_balance("sk-none-123456", "https://routerai.ru/api/v1") is None
    fake, _ = _fake_get((200, json.dumps({"balance": 5})))
    monkeypatch.setattr(bt, "_http_get", fake)
    bt.fetch_balance("sk-none-123456", "https://routerai.ru/api/v1")

    def _boom(*args, **kwargs):
        raise AssertionError("сеть не должна вызываться")
    monkeypatch.setattr(bt, "_http_get", _boom)
    cached = bt.cached_balance("sk-none-123456", "https://routerai.ru/api/v1")
    assert cached is not None and cached.amount == 5.0


# ------------------------------------------------------------------ thresholds

def test_is_low():
    assert bt.Balance(ok=True, amount=10).is_low(50)
    assert not bt.Balance(ok=True, amount=100).is_low(50)
    assert not bt.Balance(ok=True, amount=10).is_low(0)
    assert not bt.Balance(ok=False, amount=1).is_low(50)


def test_amount_text_formatting():
    assert bt.Balance(ok=True, amount=1234.5, currency="RUB").amount_text() == "1 234.50 RUB"
    assert bt.Balance(ok=True, amount=0).amount_text() == "0.00"


# ---------------------------------------------------------------------- prompt

def test_prompt_block_has_economy_mode():
    balance = bt.Balance(ok=True, amount=5.0, currency="RUB")
    block = bt.build_balance_block(balance, threshold=50)
    assert "РЕЖИМ ЭКОНОМИИ: ВКЛЮЧЁН" in block
    assert "5.00 RUB" in block
    assert "batch_write_files" in block


def test_prompt_block_normal_mode():
    balance = bt.Balance(ok=True, amount=500.0)
    block = bt.build_balance_block(balance, threshold=50)
    assert "Режим: обычный" in block
    assert "РЕЖИМ ЭКОНОМИИ: ВКЛЮЧЁН" not in block


def test_prompt_block_unknown_balance():
    balance = bt.Balance(ok=False, message="ключ не принят (HTTP 401)")
    block = bt.build_balance_block(balance, threshold=50)
    assert "не удалось" in block.lower()
    assert "Не выдумывай" in block


def test_agent_prompt_contains_balance_block():
    from app.agent.core import AgentCore

    core = AgentCore(
        api_key="sk-test-123456", base_url="https://routerai.ru/api/v1",
        model="deepseek/deepseek-v4-pro-0813",
        balance_text="БАЛАНС АККАУНТА: 7.00 (тест)",
    )
    assert "БАЛАНС АККАУНТА: 7.00 (тест)" in core.system_prompt
    assert core.request_count == 0


def test_account_balance_tool_is_registered():
    from app.tools.registry import TOOLS

    names = {tool["function"]["name"] for tool in TOOLS}
    assert "account_balance" in names
