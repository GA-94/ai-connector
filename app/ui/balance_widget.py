"""Кнопка «Баланс» в главном окне.

Запрос идёт в отдельном QThread: сеть не должна подвешивать интерфейс.
Кнопка показывает остаток, при низком балансе краснеет, по клику
перезапрашивает (fresh=True). Тултип объясняет ошибку, если баланс не получен.
"""
from app.qt_compat.QtCore import QThread, pyqtSignal
from app.qt_compat.QtWidgets import QPushButton

from app.tools import balance_tools


class BalanceFetcher(QThread):
    """Один запрос баланса в фоне."""

    done = pyqtSignal(object)

    def __init__(self, api_key: str, base_url: str = "", url: str = "",
                 fresh: bool = False, timeout: float = balance_tools.DEFAULT_TIMEOUT,
                 parent=None):
        super().__init__(parent)
        self.api_key = api_key
        self.base_url = base_url
        self.url = url
        self.fresh = fresh
        self.timeout = timeout

    def run(self):
        try:
            result = balance_tools.fetch_balance(
                self.api_key, self.base_url, self.url,
                timeout=self.timeout, fresh=self.fresh,
            )
        except Exception as error:  # инструмент не должен ронять окно
            result = balance_tools.Balance(
                ok=False, message=f"{type(error).__name__}: {error}")
        self.done.emit(result)


class BalanceButton(QPushButton):
    """Компактный индикатор остатка: «Баланс: 123.45»."""

    def __init__(self, settings, parent=None):
        super().__init__("Баланс: —", parent)
        self.settings = settings
        self._balance = None
        self._fetcher = None
        self.setToolTip("Нажми, чтобы запросить баланс по текущему API-ключу")
        self.clicked.connect(lambda: self.refresh(fresh=True))
        self.setMinimumWidth(140)

    # ------------------------------------------------------------------ state

    def balance(self):
        return self._balance

    def apply(self, balance):
        """Принять значение, полученное извне (например, из воркера)."""
        if balance is None:
            return
        self._balance = balance
        self._update_visual()

    def refresh(self, fresh: bool = False):
        if self._fetcher is not None and self._fetcher.isRunning():
            return
        api_key = self.settings.get_api_key()
        if not api_key:
            self._balance = balance_tools.Balance(
                ok=False, message="API-ключ не задан: «Глобальные настройки» -> API Key.")
            self._update_visual()
            return
        self.setText("Баланс: ...")
        self.setEnabled(False)
        self._fetcher = BalanceFetcher(
            api_key,
            self.settings.get_base_url(),
            self.settings.get_balance_url(),
            fresh=fresh,
            parent=self,
        )
        self._fetcher.done.connect(self._on_done)
        self._fetcher.finished.connect(self._on_thread_finished)
        from app.ui import threads
        threads.track(self._fetcher)
        self._fetcher.start()

    # ----------------------------------------------------------------- events

    def _on_done(self, balance):
        self._balance = balance
        self._update_visual()

    def _on_thread_finished(self):
        self.setEnabled(True)
        self._fetcher = None

    def _set_state(self, state: str):
        """Цвет кнопки берётся из темы (QSS: QPushButton[state=...])."""
        if self.property("state") == state:
            return
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def _update_visual(self):
        balance = self._balance
        threshold = self.settings.get_low_balance_threshold()
        if balance is None:
            self.setText("Баланс: —")
            self._set_state("")
            return
        if balance.ok:
            self.setText("Баланс: " + balance.amount_text())
            low = balance.is_low(threshold)
            self._set_state("low" if low else "ok")
            extra = (f"\nВНИМАНИЕ: остаток ниже порога {threshold:g}"
                     if low else "")
            self.setToolTip(
                f"{balance.as_text(threshold)}{extra}\n\n"
                f"URL: {balance.url}\nНажми, чтобы обновить.")
        else:
            self.setText("Баланс: ?")
            self._set_state("error")
            self.setToolTip(f"{balance.as_text(threshold)}\n\nНажми, чтобы повторить.")
