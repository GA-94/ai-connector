"""Баланс (кредиты) аккаунта провайдера — RouterAI credits API.

HTTP-запрос:
    GET https://routerai.ru/api/v1/credits
    Authorization: Bearer <API-ключ>
(тот же ключ, что и для чата; документация
https://routerai.ru/docs/reference#tag/credits/GET/v1/credits)

Зачем это в программе:
  * пользователь видит остаток в главном окне (кнопка «Баланс») и в настройках;
  * агент получает остаток в системном промпте и инструментом `account_balance`,
    а при остатке ниже порога включается режим экономии.

Ключ нигде не печатается: и текст ошибки провайдера, и сырой ответ проходят
через redact_text(). Сетевой слой изолирован в _http_get(), чтобы тесты
подменяли его без реальных запросов.

Ответ провайдера может отличаться от ожидаемого, поэтому разбор терпимый:
ищется любое числовое поле с именем balance/credits/remaining/... на любой
глубине вложенности, а если такого нет — единственное число в ответе.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request

from app.config.secrets import redact_text

DEFAULT_CREDITS_PATH = "/credits"
DEFAULT_TIMEOUT = 10
DEFAULT_CACHE_TTL = 60.0
DEFAULT_LOW_THRESHOLD = 50.0
MAX_RAW_SHOWN = 600

_CACHE = {}

# Порядок важен: чем раньше ключ в списке, тем охотнее берём его значение.
_KEY_PRIORITY = (
    "balance", "credit", "remaining", "available", "amount",
    "left", "rest", "quota", "total", "value",
)
_BAD_WORDS = (
    "used", "spent", "consumed", "charge", "price", "rate",
    "date", "time", "count", "currency", "id",
)
CURRENCY_KEYS = ("currency", "cur", "unit", "symbol")

_NUMBER_RE = re.compile(r"-?\d[\d\s\u00a0.,]*")

ECONOMY_RULES = (
    "Не читай один и тот же файл дважды: запомни содержимое и правь через edit_file.",
    "Не запускай read_entire_project_via_cli на большом проекте без необходимости: "
    "сначала точечные read_file / java_read_class.",
    "Несколько файлов пиши одним batch_write_files, а не серией write_file.",
    "Не проверяй сборку после каждой строки: закончи правку и вызови java_build один раз.",
    "python_run используй только для проверки уже написанного кода, а не для догадок.",
    "Отвечай коротко: не повторяй в чате код, который уже записан в файлы.",
    "Если задача большая — предложи план и спроси, продолжать ли, до массовых правок.",
)


# ------------------------------------------------------------------ helpers

def _key_tag(api_key: str) -> str:
    """Короткий отпечаток ключа для кэша — сам ключ в памяти кэша не лежит."""
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:12]


def credits_url(base_url: str = "", url: str = "") -> str:
    """Адрес баланса: явный URL, иначе Base URL + '/credits'."""
    explicit = (url or "").strip()
    if explicit:
        return explicit
    base = (base_url or "").strip()
    if not base:
        return ""
    base = base.rstrip("/")
    if base.lower().endswith(DEFAULT_CREDITS_PATH):
        return base
    return base + DEFAULT_CREDITS_PATH


def _to_number(value):
    """Число из int/float/строки вида '1 234,56 руб'. None, если не число."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    digits = match.group(0).replace("\u00a0", "").replace(" ", "").strip(".,")
    if not digits or digits in ("-", "+"):
        return None
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        head, _, tail = digits.rpartition(",")
        digits = f"{head}.{tail}" if len(tail) in (1, 2) else digits.replace(",", "")
    try:
        return float(digits)
    except ValueError:
        return None


def _score(key: str):
    """Приоритет имени поля; None — поле не похоже на баланс (id, used, date)."""
    low = (key or "").strip().lower()
    if not low:
        return None
    if any(word in low for word in _BAD_WORDS):
        return None
    for index, hint in enumerate(_KEY_PRIORITY):
        if hint in low:
            return index
    return None


def _candidates(payload, depth: int = 0):
    """Все числовые поля ответа, похожие на баланс: (score, имя, значение)."""
    if depth > 6:
        return
    if isinstance(payload, dict):
        for name, value in payload.items():
            if isinstance(value, (dict, list)):
                yield from _candidates(value, depth + 1)
                continue
            number = _to_number(value)
            if number is None:
                continue
            score = _score(str(name))
            if score is None:
                continue
            yield (score * 10 + depth, str(name), number)
    elif isinstance(payload, list):
        for item in payload[:20]:
            yield from _candidates(item, depth + 1)


def _numbers(payload, depth: int = 0):
    """Все числа ответа без учёта имён — запасной вариант разбора."""
    if depth > 6:
        return
    if isinstance(payload, bool) or payload is None:
        return
    if isinstance(payload, (int, float)):
        yield float(payload)
    elif isinstance(payload, str):
        number = _to_number(payload)
        if number is not None:
            yield number
    elif isinstance(payload, dict):
        for value in payload.values():
            yield from _numbers(value, depth + 1)
    elif isinstance(payload, list):
        for item in payload[:20]:
            yield from _numbers(item, depth + 1)


def _find_currency(payload, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(payload, dict):
        for name, value in payload.items():
            if str(name).strip().lower() in CURRENCY_KEYS and isinstance(value, str):
                value = value.strip()
                if value and len(value) <= 8:
                    return value
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = _find_currency(value, depth + 1)
                if found:
                    return found
    elif isinstance(payload, list):
        for item in payload[:20]:
            found = _find_currency(item, depth + 1)
            if found:
                return found
    return ""


def parse_balance(payload):
    """(amount, currency) из ответа провайдера. amount=None, если не нашли."""
    best = None
    for score, name, number in _candidates(payload):
        if best is None or score < best[0]:
            best = (score, name, number)
    amount = best[2] if best else None
    if amount is None:
        unique = list(_numbers(payload))
        if len(unique) == 1:
            amount = unique[0]
    return amount, _find_currency(payload)


# -------------------------------------------------------------------- model

class Balance:
    """Результат запроса баланса: цифра, валюта, причина ошибки, время."""

    def __init__(self, ok: bool = False, amount=None, currency: str = "",
                 message: str = "", url: str = "", status: int = 0,
                 raw: str = "", cached: bool = False, fetched_at: float = 0.0):
        self.ok = bool(ok)
        self.amount = amount
        self.currency = currency or ""
        self.message = message or ""
        self.url = url or ""
        self.status = int(status or 0)
        self.raw = raw or ""
        self.cached = bool(cached)
        self.fetched_at = float(fetched_at or time.time())

    @property
    def age(self) -> float:
        return max(0.0, time.time() - self.fetched_at)

    def amount_text(self) -> str:
        if self.amount is None:
            return "нет данных"
        text = f"{self.amount:,.2f}".replace(",", " ")
        return f"{text} {self.currency}".strip()

    def short(self) -> str:
        return self.amount_text() if self.ok else "ошибка"

    def is_low(self, threshold=DEFAULT_LOW_THRESHOLD) -> bool:
        try:
            limit = float(threshold or 0)
        except (TypeError, ValueError):
            limit = 0.0
        if not self.ok or self.amount is None or limit <= 0:
            return False
        return self.amount <= limit

    def time_text(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.fetched_at))

    def as_text(self, threshold=DEFAULT_LOW_THRESHOLD) -> str:
        """Подробный отчёт: для инструмента account_balance и для тултипа."""
        out = ["БАЛАНС АККАУНТА"]
        out.append(f"  URL: {self.url or '(не задан)'}")
        if self.ok:
            out.append(f"  Остаток: {self.amount_text()}")
            try:
                limit = float(threshold or 0)
            except (TypeError, ValueError):
                limit = 0.0
            if limit > 0:
                state = "РЕЖИМ ЭКОНОМИИ ВКЛЮЧЁН" if self.is_low(limit) else "режим обычный"
                out.append(f"  Порог экономии: {limit:g} -> {state}")
            out.append(f"  Проверено: {self.time_text()}" + (" (из кэша)" if self.cached else ""))
            if self.is_low(threshold):
                out.append("  РЕКОМЕНДАЦИИ:")
                out.extend("    - " + rule for rule in ECONOMY_RULES)
        else:
            out.append("  Состояние: НЕ УДАЛОСЬ ПОЛУЧИТЬ БАЛАНС")
            out.append(f"  Причина: {self.message}")
            out.append("  Не выдумывай число баланса — сообщи пользователю причину.")
        if self.raw:
            out.append("  ОТВЕТ ПРОВАЙДЕРА: " + self.raw[:MAX_RAW_SHOWN])
        return "\n".join(out)


# ------------------------------------------------------------------ network

def _http_get(url: str, api_key: str, timeout: float = DEFAULT_TIMEOUT):
    """GET с Bearer-ключом. Вынесено отдельно, чтобы тесты подменяли сеть."""
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "AI-Connector",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
        return response.getcode(), body


def _http_message(code: int, detail: str, url: str) -> str:
    detail = (detail or "").strip()
    tail = f" Ответ провайдера: {detail[:300]}" if detail else ""
    if code in (401, 403):
        return (f"ключ не принят (HTTP {code}). Проверь API Key и что он от этого "
                f"провайдера.{tail}")
    if code == 404:
        return (f"эндпоинт не найден (HTTP 404) по адресу {url}. Проверь Base URL "
                f"(обычно https://routerai.ru/api/v1) или задай «URL баланса» в "
                f"настройках.{tail}")
    if code == 429:
        return f"слишком много запросов к API (HTTP 429). Повтори позже.{tail}"
    if 500 <= code < 600:
        return f"ошибка на стороне провайдера (HTTP {code}). Повтори позже.{tail}"
    return f"провайдер вернул HTTP {code}.{tail}"


def fetch_balance(api_key: str, base_url: str = "", url: str = "",
                  timeout: float = DEFAULT_TIMEOUT, fresh: bool = False,
                  cache_ttl: float = DEFAULT_CACHE_TTL) -> Balance:
    """Остаток по ключу. Свежее значение кэшируется на cache_ttl секунд.

    Никогда не бросает исключений: любая проблема возвращается как
    Balance(ok=False, message=...), чтобы UI и агент показывали причину.
    """
    target = credits_url(base_url, url)
    key = (api_key or "").strip()
    if not key:
        return Balance(ok=False, url=target,
                       message="API-ключ не задан: «Глобальные настройки» -> API Key.")
    if not target:
        return Balance(ok=False,
                       message="не задан Base URL: не из чего собрать адрес /credits. "
                               "Заполни Base URL или «URL баланса» в настройках.")

    cache_key = (target, _key_tag(key))
    if not fresh:
        hit = _CACHE.get(cache_key)
        if hit is not None and (time.time() - hit.fetched_at) < max(0.0, float(cache_ttl)):
            hit.cached = True
            return hit

    try:
        status, body = _http_get(target, key, timeout)
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        return Balance(ok=False, url=target, status=error.code,
                       message=_http_message(error.code, detail, target),
                       raw=redact_text(detail, key)[:MAX_RAW_SHOWN])
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        return Balance(ok=False, url=target,
                       message=f"сетевая ошибка: {reason}. Проверь интернет и Base URL.")
    except Exception as error:  # pragma: no cover - защита от необычных сбоев
        return Balance(ok=False, url=target,
                       message=f"{type(error).__name__}: {error}")

    text = (body or "").strip()
    payload = None
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None

    amount, currency = parse_balance(payload) if payload is not None else (None, "")
    if amount is None and payload is None:
        prefix = text.lstrip("\ufeff").strip()
        if prefix[:1] in ("{", "["):
            return Balance(ok=False, url=target, status=status,
                           message="ответ провайдера не разобран как JSON — "
                                   "смотри «ОТВЕТ ПРОВАЙДЕРА» ниже.",
                           raw=redact_text(text, key)[:MAX_RAW_SHOWN])
        amount = _to_number(prefix)
        if amount is None:
            return Balance(ok=False, url=target, status=status,
                           message="в ответе не найдено число баланса.",
                           raw=redact_text(text, key)[:MAX_RAW_SHOWN])

    if amount is None:
        return Balance(ok=False, url=target, status=status,
                       message="ответ получен, но поля с балансом в нём нет "
                               "(ни balance, ни credits, ни remaining).",
                       raw=redact_text(text, key)[:MAX_RAW_SHOWN])

    result = Balance(ok=True, amount=amount, currency=currency, url=target,
                     status=status, raw=redact_text(text, key)[:MAX_RAW_SHOWN],
                     cached=False)
    _CACHE[cache_key] = result
    return result


def cached_balance(api_key: str, base_url: str = "", url: str = ""):
    """Уже известное значение без сети (для диалогов), или None."""
    target = credits_url(base_url, url)
    if not target:
        return None
    return _CACHE.get((target, _key_tag((api_key or "").strip())))


def clear_cache() -> None:
    _CACHE.clear()


# ------------------------------------------------------------------- prompt

def build_balance_block(balance, threshold=DEFAULT_LOW_THRESHOLD) -> str:
    """Текст для системного промпта агента."""
    if balance is None:
        return ""
    try:
        limit = float(threshold or 0)
    except (TypeError, ValueError):
        limit = 0.0

    lines = ["БАЛАНС АККАУНТА (получен программой автоматически, не придумывай цифры):"]
    if balance.ok:
        lines.append(f"  остаток: {balance.amount_text()}")
        if limit > 0:
            lines.append(f"  порог экономии: {limit:g}")
        lines.append(f"  проверено: {balance.time_text()}" +
                     (" (из кэша)" if balance.cached else ""))
        if balance.is_low(limit):
            lines.append("")
            lines.append("РЕЖИМ ЭКОНОМИИ: ВКЛЮЧЁН — остаток ниже порога, "
                         "каждый лишний вызов стоит денег.")
            lines.extend("  - " + rule for rule in ECONOMY_RULES)
        else:
            lines.append("")
            lines.append("Режим: обычный. Но запросы платные — избегай лишних вызовов "
                         "и повторов одного и того же чтения.")
    else:
        lines.append(f"  остаток получить не удалось: {balance.message}")
        lines.append("  Не выдумывай баланс. Если он важен для решения — "
                     "вызови account_balance или скажи пользователю причину.")
    return "\n".join(lines)
