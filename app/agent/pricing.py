"""Стоимость запроса в рублях.

Провайдеры берут деньги в долларах за миллион токенов (локальная Ollama —
бесплатно). Таблица ниже — ориентир: любую модель можно переоценить вручную
(«₽ за 1M входных / выходных»), тогда используется ручная цена.
"""

DEFAULT_RUB_PER_USD = 95.0

# (подстрока в названии модели, $ за 1M входных, $ за 1M выходных)
_PRICES_USD = (
    ("deepseek-reasoner", 0.55, 2.19),
    ("deepseek", 0.27, 1.10),
    ("grok", 2.00, 10.00),
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4.1", 2.00, 8.00),
    ("gpt-4o", 2.50, 10.00),
    ("o3-mini", 1.10, 4.40),
    ("gemini-2.0-flash", 0.10, 0.40),
    ("gemini-1.5-flash", 0.075, 0.30),
    ("gemini", 1.25, 5.00),
    ("claude-3-5-haiku", 0.80, 4.00),
    ("claude", 3.00, 15.00),
    ("mistral-large", 2.00, 6.00),
    ("codestral", 0.30, 0.90),
    ("mistral", 0.25, 0.25),
    ("llama", 0.0, 0.0),
    ("qwen", 0.0, 0.0),
    ("phi", 0.0, 0.0),
    ("gemma", 0.0, 0.0),
)


def price_usd(model: str):
    """(вход, выход) в долларах за 1M токенов, либо None если модель неизвестна."""
    name = str(model or "").strip().lower()
    if not name:
        return None
    for needle, cost_in, cost_out in _PRICES_USD:
        if needle in name:
            return cost_in, cost_out
    return None


def parse_manual_price(text: str):
    """Ручная цена из настроек: '120, 480' = ₽ за 1M входных/выходных токенов.

    Принимает точку и запятую; пустое поле = ручной цены нет.
    """
    raw = str(text or "").replace(";", " ").replace(",", ".").split()
    numbers = []
    for part in raw:
        try:
            numbers.append(abs(float(part)))
        except ValueError:
            continue
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return None


def estimate_rub(model: str, prompt_tokens: int, completion_tokens: int,
                 rub_per_usd: float = DEFAULT_RUB_PER_USD,
                 manual=None, local: bool = False) -> float:
    """Сколько рублей стоил один запрос. local=True (Ollama) — всегда 0."""
    if local:
        return 0.0
    if manual:
        cost_in, cost_out = manual
    else:
        usd = price_usd(model)
        if usd is None:
            return 0.0
        try:
            rate = float(rub_per_usd or DEFAULT_RUB_PER_USD)
        except (TypeError, ValueError):
            rate = DEFAULT_RUB_PER_USD
        cost_in, cost_out = usd[0] * rate, usd[1] * rate
    tokens_in = max(0, int(prompt_tokens or 0))
    tokens_out = max(0, int(completion_tokens or 0))
    return (tokens_in * float(cost_in) + tokens_out * float(cost_out)) / 1_000_000.0


def format_rub(value) -> str:
    """'1,83 ₽' — компактно и читаемо в статусной строке."""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return "0 ₽"
    if amount < 0.01:
        return "<0,01 ₽"
    return f"{amount:.2f}".replace(".", ",") + " ₽"
