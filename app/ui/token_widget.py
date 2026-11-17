"""Счётчик токенов контекста — вместо баланса в рублях.

Зачем: у локальной Ollama платить не за что (запросы бесплатные), зато есть
лимит контекста. Когда история перестаёт влезать в num_ctx, модель начинает
«терять» начало разговора — и это видно только по счётчику. Поэтому на месте
кнопки «Баланс» показываем: сколько токенов занял последний запрос, сколько
всего влезает и сколько осталось.

Значение хранится в базе чата (chat_store.save_token_stats), поэтому счётчик
не сбрасывается при перезапуске программы и у каждого чата свой.
"""

from app.qt_compat.QtWidgets import QPushButton


def short_tokens(value) -> str:
    """«1.2k», «812» — компактная запись числа токенов."""
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return "0"
    if abs(number) >= 1000:
        text = f"{number / 1000:.1f}k"
        return text.replace(".0k", "k")
    return str(number)


def counter_text(used, limit, total=0) -> str:
    """Текст кнопки: «🧮 ост. 7k из 8.2k» (сколько токенов ещё влезает)."""
    used_value = int(used or 0)
    limit_value = int(limit or 0)
    if limit_value > 0:
        remaining = max(0, limit_value - used_value)
        return f"🧮 ост. {short_tokens(remaining)} из {short_tokens(limit_value)}"
    if used_value or total:
        return f"🧮 {short_tokens(used_value or total)}"
    return "🧮 —"


class TokenCounterButton(QPushButton):
    """Индикатор «занято / лимит» для локальной модели."""

    def __init__(self, parent=None):
        super().__init__("🧮 —", parent)
        self._used = 0
        self._limit = 0
        self._total = 0
        self.setMinimumWidth(175)
        self._apply()
        self.clicked.connect(self._show_details)

    # ------------------------------------------------------------------ данные

    def stats(self) -> dict:
        return {"used": self._used, "limit": self._limit, "total": self._total}

    def set_stats(self, used=0, limit=0, total=0):
        try:
            self._used = max(0, int(used or 0))
            self._limit = max(0, int(limit or 0))
            self._total = max(0, int(total or 0))
        except (TypeError, ValueError):
            return
        self._apply()

    def clear(self):
        self._used = 0
        self._limit = 0
        self._total = 0
        self._apply()

    # ---------------------------------------------------------------- отрисовка

    def remaining(self) -> int:
        if self._limit <= 0:
            return 0
        return max(0, self._limit - self._used)

    def _apply(self):
        self.setText(counter_text(self._used, self._limit, self._total))
        remaining = self.remaining()
        percent = (int(round(self._used * 100.0 / self._limit))
                   if self._limit > 0 else 0)
        lines = ["Контекст локальной модели (не деньги: запросы бесплатны)."]
        if self._limit > 0:
            lines.append(f"Занято: {self._used:,} из {self._limit:,} токенов "
                         f"({percent}%)".replace(",", " "))
            lines.append(f"Осталось: {remaining:,} токенов".replace(",", " "))
            if percent >= 90:
                lines.append("Почти всё: скоро модель начнёт забывать начало "
                             "разговора — начни новый чат или увеличь контекст.")
        else:
            lines.append(f"Занято последним запросом: {self._used:,} токенов".replace(",", " "))
            lines.append("Лимит неизвестен: задай «Длину контекста» в настройках чата.")
        if self._total:
            lines.append(f"Всего израсходовано токенов в этом чате: "
                         f"{self._total:,}".replace(",", " "))
        lines.append("\nСчётчик хранится в базе чата и не сбрасывается.")
        self.setToolTip("\n".join(lines))
        if self._limit > 0 and percent >= 90:
            self._set_state("low")
        elif self._used or self._total:
            self._set_state("ok")
        else:
            self._set_state("")

    def _set_state(self, state: str):
        if self.property("state") == state:
            return
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def _show_details(self):
        """Клик — то же, что подсказка: подробности в консоль главного окна."""
        window = self.window()
        log = getattr(window, "console_view", None)
        if log is None:
            return
        stats = self.stats()
        log.log("[Tokens] " + self.toolTip().split("\n\n")[0].replace("\n", " | "))
        if stats["limit"] > 0:
            log.log(f"[Tokens] осталось {self.remaining()} из {stats['limit']}")
