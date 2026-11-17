import threading

from app.qt_compat.QtCore import QThread, pyqtSignal

from app.agent.core import AgentCore
from app.agent import providers
from app.config.secrets import redact_text
from app.tools import balance_tools


class AgentWorker(QThread):
    message_ready = pyqtSignal(str)
    log_ready = pyqtSignal(str)
    error_ready = pyqtSignal(str)
    balance_ready = pyqtSignal(object)
    thinking_ready = pyqtSignal(str)
    cost_ready = pyqtSignal(object)      # (всего ₽ за чат, ₽ за последний запрос)
    context_ready = pyqtSignal(int, int, int)  # (занято, лимит, всего токенов)
    confirm_required = pyqtSignal(object)  # запрос подтверждения (GitHub)

    def __init__(self, api_key: str, base_url: str, model: str, project_root: str,
                 instructions: str, max_iterations: int, messages: list,
                 project_roots=None, repository_root: str = "", allow_network: bool = True,
                 allow_python: bool = True, balance_url: str = "",
                 low_balance_threshold: float = 0.0, share_balance: bool = True,
                 provider: str = "", economy: bool = False, disabled_tools=None,
                 github_token: str = "", github_author: str = "",
                 github_open_after: bool = True, rub_per_usd: float = 95.0,
                 price_override: str = "", num_ctx: int = 0,
                 ollama_mode: str = "auto", ollama_host: str = "",
                 keep_alive: str = "30m", tools_dir: str = "",
                 allow_tools_edit: bool = False, tools_auto: bool = True,
                 verbose_tools: bool = False, session_id: int = 0,
                 remote_db_path: str = "", fast_mode: bool = False):

        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.project_root = project_root
        self.instructions = instructions
        self.max_iterations = max_iterations
        self.messages = messages
        self.project_roots = list(project_roots or [])
        self.repository_root = repository_root
        self.allow_network = allow_network
        self.allow_python = allow_python
        self.balance_url = balance_url
        self.low_balance_threshold = float(low_balance_threshold or 0)
        self.share_balance = bool(share_balance)
        self.provider = providers.normalize(provider)
        self.economy = bool(economy)
        self.disabled_tools = list(disabled_tools or [])
        self.github_token = github_token
        self.github_author = github_author
        self.github_open_after = github_open_after
        self.rub_per_usd = rub_per_usd
        self.price_override = price_override
        # локальная модель: длина контекста (num_ctx) и размещение VRAM/RAM
        self.num_ctx = int(num_ctx or 0)
        self.ollama_mode = ollama_mode
        self.ollama_host = ollama_host
        self.keep_alive = keep_alive
        # свои инструменты (data/custom_tools.json) и право ИИ ими управлять
        self.tools_dir = str(tools_dir or "")
        self.allow_tools_edit = bool(allow_tools_edit)
        self.tools_auto = bool(tools_auto)
        # Журнал: кратко (по умолчанию) или полный дамп аргументов.
        self.verbose_tools = bool(verbose_tools)
        # Удалённый доступ: id чата + путь к базе, где лежат ЕГО подключения.
        self.session_id = int(session_id or 0)
        self.remote_db_path = str(remote_db_path or "")
        # Быстрый режим: модель не рассуждает вслух (у Ollama think=false).
        self.fast_mode = bool(fast_mode)

        self.cost_total = 0.0
        self._is_cancelled = False
        self._confirm_event = threading.Event()
        self._confirm_answer = None

    def cancel(self):
        """Вызывается из главного потока по кнопке Стоп."""
        self._is_cancelled = True
        # если агент ждёт подтверждения — отпускаем его, считая что отменили
        self._confirm_answer = None
        self._confirm_event.set()

    # ------------------------------------------------- подтверждения (GUI)
    def _ask_user(self, request: dict):
        """Спросить пользователя из фонового потока: сигнал + ожидание ответа."""
        if self._is_cancelled:
            return None
        self._confirm_answer = None
        self._confirm_event.clear()
        self.confirm_required.emit(dict(request or {}))
        self._confirm_event.wait(timeout=600)
        return self._confirm_answer

    def resolve_confirm(self, answer):
        """Вызывается из главного потока, когда пользователь ответил."""
        self._confirm_answer = answer
        self._confirm_event.set()

    def run(self):
        try:
            balance_text = ""
            # У локальной модели баланса нет: запрос остатка только для платных
            # провайдеров (иначе в чате висело бесполезное окно баланса).
            if self.share_balance and not (
                    providers.get(self.provider).local
                    or providers.is_ollama_url(self.base_url)):
                # Запрос баланса идёт здесь, в фоновом потоке: интерфейс не ждёт сеть.
                try:
                    balance = balance_tools.fetch_balance(
                        self.api_key, self.base_url, self.balance_url)
                except Exception as error:
                    balance = balance_tools.Balance(
                        ok=False, message=f"{type(error).__name__}: {error}")
                self.balance_ready.emit(balance)
                balance_text = balance_tools.build_balance_block(
                    balance, self.low_balance_threshold)

            agent = AgentCore(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                project_root=self.project_root,
                instructions=self.instructions,
                max_iterations=self.max_iterations,
                log_callback=self.log_ready.emit,
                cancel_check=lambda: self._is_cancelled,
                project_roots=self.project_roots,
                repository_root=self.repository_root,
                allow_network=self.allow_network,
                allow_python=self.allow_python,
                balance_url=self.balance_url,
                balance_text=balance_text,
                low_balance_threshold=self.low_balance_threshold,
                thinking_callback=self.thinking_ready.emit,
                provider=self.provider,
                economy=self.economy,
                disabled_tools=self.disabled_tools,
            tools_auto=self.tools_auto,
                confirm_callback=self._ask_user,
                github_token=self.github_token,
                github_author=self.github_author,
                github_open_after=self.github_open_after,
                rub_per_usd=self.rub_per_usd,
                price_override=self.price_override,
                cost_callback=self._on_cost,
                num_ctx=self.num_ctx,
                ollama_mode=self.ollama_mode,
                ollama_host=self.ollama_host,
                keep_alive=self.keep_alive,
                context_callback=self.context_ready.emit,
                tools_dir=self.tools_dir,
                allow_tools_edit=self.allow_tools_edit,
                verbose_tools=self.verbose_tools,
                remote_store=self._remote_store(),
                session_id=self.session_id,
                fast_mode=self.fast_mode,
            )


            result = agent.run(self.messages, on_tool_log=self.log_ready.emit)
            self.log_ready.emit(
                f"[Cost] запросов к модели за этот ход: {agent.request_count} | "
                f"токенов: {agent.total_tokens}")

            # Если отмена была запрошена, сообщаем об этом, а не о результате
            if self._is_cancelled:
                self.message_ready.emit("[Отменено пользователем]")
            else:
                self.message_ready.emit(result)

        except Exception as e:
            self.error_ready.emit(redact_text(f"{type(e).__name__}: {e}", self.api_key))

    def _on_cost(self, total: float, last: float):
        self.cost_total = float(total or 0)
        self.cost_ready.emit((self.cost_total, float(last or 0)))

    def _remote_store(self):
        """Хранилище профилей подключений этого чата (или None, если базы нет)."""
        if not self.remote_db_path:
            return None
        try:
            from app.config.remote_store import RemoteStore

            return RemoteStore(self.remote_db_path)
        except Exception as error:  # noqa: BLE001 - без базы удалённый доступ просто выключен
            self.log_ready.emit(f"[Remote] профили подключений недоступны: {error}")
            return None
