import json

from openai import (
    OpenAI, APIStatusError, APIConnectionError, APITimeoutError,
    AuthenticationError, NotFoundError, RateLimitError
)
from app.tools.registry import TOOLS
from app.tools import repo_tools
from app.tools import python_tools
from app.tools import balance_tools
from app.tools import tooling
from app.tools import custom_tools
from app.tools import git_tools
from app.tools import remote_tools

from app.agent import providers
from app.agent import pricing
from app.agent import ollama as ollama_mod
from app.agent.ollama_client import OllamaClient, OllamaError
from app.agent import embeddings
from app.tools import repo_status as repo_status_tools

from app.tools.file_ops import (

    read_file, write_file, edit_file, list_files,
    batch_write_files, run_deployer_cli,
    deactivate_file, backup_and_remove_dir
)
from app.tools.java_tools import (
    java_scan, java_read_class, java_find, java_build, java_parse_log
)
from app.tools.workspace import Workspace, WorkspaceError
from app.tools.safety import find_root
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.java_prompt import JAVA_WORKFLOW_PROMPT
from app.config.secrets import redact_text
from app import runtime_env

# поля, в которых провайдеры возвращают «размышления» модели
_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "reasoning_text")


class AgentCore:
    def __init__(self, api_key: str, base_url: str, model: str, project_root: str = "",
                 instructions: str = "", max_iterations: int = 20,
                 log_callback=None, cancel_check=None, project_roots=None,
                 repository_root: str = "", allow_network: bool = True,
                 allow_python: bool = True, balance_url: str = "",
                 balance_text: str = "", low_balance_threshold: float = 0.0,
                 thinking_callback=None, provider: str = "", economy: bool = False,
                 disabled_tools=None, confirm_callback=None, github_token: str = "",
                 github_author: str = "", github_open_after: bool = True,
                 rub_per_usd: float = 95.0, price_override: str = "",
                 cost_callback=None, num_ctx: int = 0, ollama_mode: str = "auto",
                 ollama_host: str = "", keep_alive: str = "30m",
                 context_callback=None, tools_dir: str = "",
                 allow_tools_edit: bool = False, tools_auto: bool = True,
                 verbose_tools: bool = False, remote_store=None,
                 session_id: int = 0, fast_mode: bool = False):


        # Провайдер определяем до клиента: у локальной Ollama свой транспорт.
        self.provider = providers.normalize(provider)
        # Локальная Ollama: либо выбран сервис «Ollama», либо адрес с портом
        # 11434 (бывает, что сервис оставили «свой сервер», а адрес — Ollama).
        self.is_local = (providers.get(self.provider).local
                         or providers.is_ollama_url(base_url))

        self.num_ctx = max(0, int(num_ctx or 0))
        self.ollama_mode = ollama_mod.normalize_mode(ollama_mode)
        self.ollama_host = ollama_mod.normalize_host(ollama_host or base_url)
        self.keep_alive = str(keep_alive or "30m")
        # Быстрый режим: модель не рассуждает вслух, отвечает сразу.
        # У локальной Ollama это think=false в /api/chat (реально экономит
        # время), у облачных провайдеров — пониженное «усилие рассуждений»
        # (если сервер такого поля не принимает, запрос повторяется без него).
        self.fast_mode = bool(fast_mode)
        self.fast_mode_unsupported = False
        if self.is_local:
            # Нативный /api/chat: только там работают num_ctx (длина контекста) и
            # num_gpu (грузить в VRAM, а не в RAM). Через /v1 они не доходят.
            self.client = OllamaClient(
                host=self.ollama_host, mode=self.ollama_mode,
                num_ctx=self.num_ctx, keep_alive=self.keep_alive,
                think=False if self.fast_mode else None,
            )
        else:
            self.client = OpenAI(
                # Локальные модели работают без ключа, но клиент OpenAI
                # требует непустую строку — подставляем заглушку.
                api_key=api_key or "local-no-key",
                base_url=base_url,
                timeout=120.0,
                max_retries=2
            )
        self.model = model
        self._api_key = api_key  # нужен, чтобы вырезать ключ из текстов ошибок
        self._base_url = base_url
        self.balance_url = balance_url or ""
        self.low_balance_threshold = float(low_balance_threshold or 0)
        self.max_iterations = max_iterations
        self.log = log_callback or (lambda msg: None)
        self.cancel_check = cancel_check or (lambda: False)
        self.allow_python = bool(allow_python)
        self.request_count = 0  # сколько запросов к модели ушло (стоимость хода)
        self.on_thinking = thinking_callback or (lambda text: None)

        # --- экономия, инструменты, деньги ---------------------------------
        self.economy = bool(economy)
        self.disabled_tools = list(disabled_tools or [])
        # Удалённый доступ: профили подключений и пароли живут в базе чатов и в
        # реестре Windows / файле конфигов Linux. Каждый чат видит только свои.
        self.remote_store = remote_store
        self.session_id = int(session_id or 0)
        self.confirm = confirm_callback or (lambda request: None)
        self.github_token = str(github_token or "")
        self.github_author = str(github_author or "")
        self.github_open_after = bool(github_open_after)
        self.rub_per_usd = rub_per_usd
        self.manual_price = pricing.parse_manual_price(price_override)
        self.on_cost = cost_callback or (lambda total, last: None)
        self.cost_rub = 0.0
        self.total_tokens = 0
        # Контекст: у локальной модели лимит = num_ctx из настроек чата,
        # у облачных провайдеров лимит неизвестен (показываем только расход).
        self.on_context = context_callback or (lambda used, limit: None)
        self.context_used = 0
        self.context_limit = self.num_ctx if self.is_local else 0
        # Лимит берём из настроек чата (num_ctx). Если там «как решит Ollama»,
        # при первом ответе спросим карточку модели — иначе счётчик показывал бы
        # «осталось неизвестно».
        self._context_limit_checked = bool(self.context_limit)
        # Выключенные группы инструментов не отправляем модели вовсе:
        # каждый лишний инструмент — это токены (то есть деньги) в каждом запросе.
        # Плюс свои инструменты из data/custom_tools.json и (если пользователь
        # разрешил) инструменты управления ими.
        self.tools_dir = str(tools_dir or "")
        self.allow_tools_edit = bool(allow_tools_edit)
        # Журнал инструментов: кратко (путь/команда) или полный дамп аргументов.
        self.verbose_tools = bool(verbose_tools)
        # Авто-отбор инструментов по типу проекта (экономия токенов).
        # project_root/repository_root нужны уже здесь: _build_tools ниже
        # решает по ним, какие группы вообще не отправлять модели.
        self.project_root = str(project_root or "")
        self.repository_root = str(repository_root or "")
        self.tools_auto = bool(tools_auto)
        self.auto_disabled = []
        self.tools_summary = ""
        self.custom_tools = (custom_tools.load_tools(self.tools_dir)
                             if self.tools_dir else [])
        self.tools = self._build_tools()
        self.log(f"[Tools] {self.tools_summary}")
        # Модель может вообще не видеть инструментов: Ollama выбрасывает
        # параметр tools, если в шаблоне модели нет блока инструментов. Тогда
        # она уверенно отвечает «файл создан», хотя ничего не делала. Если
        # проверка уже делалась (кнопка в настройках чата), говорим сразу.
        self.tools_delivered = None
        if self.is_local:
            ok, note = ollama_mod.cached_tools_support(self.ollama_host, self.model)
            if ok is not None:
                self.tools_delivered = bool(ok)
            if ok is False:
                self.log("[ВНИМАНИЕ] " + note)

        if self.economy:
            # Экономный режим: меньше шагов, чтобы не гонять модель по кругу.
            self.max_iterations = max(3, min(int(max_iterations or 20), 6))




        # --- multi-project workspace + asset repository ---------------------
        self.workspace = Workspace(
            roots=list(project_roots or []),
            primary=project_root or "",
            repository=repository_root or "",
            allow_network=allow_network,
        )
        self.write_roots = [str(path) for path in self.workspace.roots]
        self.read_roots = list(self.write_roots)
        if self.workspace.repository:
            self.read_roots.append(str(self.workspace.repository))
        self.project_root = str(self.workspace.primary) if self.workspace.primary else (project_root or "")

        self.system_prompt = SYSTEM_PROMPT + "\n\n" + JAVA_WORKFLOW_PROMPT
        # Программа и агент должны понимать, на какой ОС работают: от этого
        # зависят разделители путей, имена интерпретатора и команды оболочки.
        self.system_prompt += "\n\n" + runtime_env.describe_for_prompt()
        workspace_block = self.workspace.describe()
        if workspace_block:
            self.system_prompt += "\n\n" + workspace_block
        # Удалённый доступ: профили и живые SSH-сессии именно этого чата.
        self.remote = remote_tools.RemoteContext(
            store=remote_store, session_id=self.session_id,
            workspace=self.workspace, log=self.log,
            cancel_check=self.cancel_check)
        remote_block = self._remote_prompt_block()
        if remote_block:
            self.system_prompt += "\n\n" + remote_block
        # Ассортимент репозитория: агент сразу видит, что уже скачано/лежит,
        # и не просит пользователя искать архивы вручную.
        self.repo_catalog = ""
        try:
            self.repo_catalog = repo_tools.repo_catalog(self.workspace)
        except Exception:  # noqa: BLE001 - битая настройка не должна мешать
            self.repo_catalog = ""
        if self.repo_catalog:
            self.system_prompt += "\n\n" + self.repo_catalog
        # Свои инструменты и право ими управлять.
        if self.custom_tools:
            self.system_prompt += "\n\n" + custom_tools.describe_tools(self.custom_tools)
        if self.allow_tools_edit:
            self.system_prompt += (
                "\n\nСВОИ ИНСТРУМЕНТЫ (пользователь разрешил ими управлять):\n"
                "- tool_list — что уже есть у агента;\n"
                "- tool_create / tool_update / tool_delete — создать, поправить, удалить.\n"
                "- Создавай инструмент только если штатных действительно не хватает,\n"
                "  не дублируй существующие и объясни пользователю, зачем он нужен.\n"
                "  Созданный инструмент сразу доступен в этой же сессии.")
        else:
            self.system_prompt += (
                "\n\nСВОИ ИНСТРУМЕНТЫ: менять набор нельзя — в настройках чата выключено\n"
                "«Разрешить ИИ изменять, добавлять, удалять инструменты». Если инструментов\n"
                "не хватает, скажи об этом пользователю и предложи включить разрешение.")

        # Остаток на счёте и режим экономии: блок готовит вызывающая сторона
        # (app/ui/worker.py), чтобы не делать сетевой запрос в этом конструкторе.
        if balance_text:
            self.system_prompt += "\n\n" + balance_text
        provider_info = providers.get(self.provider)
        self.system_prompt += (f"\n\nПровайдер: {provider_info.label} | "
                               f"модель: {self.model}.")
        if self.is_local:
            self.system_prompt += ("\nМодель локальная (Ollama): запросы бесплатны, "
                                   "но она слабее облачных — работай короткими шагами.")
            if self.context_limit:
                self.system_prompt += (
                    f"\nДлина контекста модели: {self.context_limit} токенов — "
                    "держи историю и ответы короткими, не пересказывай файлы целиком.")
        if self.economy:
            self.system_prompt += (
                "\n\nЭКОНОМНЫЙ РЕЖИМ (включён пользователем, API платный):\n"
                "- не запускай сборки, тесты и повторные проверки без прямой просьбы;\n"
                "- правь файлы пачками (batch_write_files / несколько edit_file в одном шаге);\n"
                "- не читай один и тот же файл дважды, не делай лишних list_files;\n"
                "- отвечай коротко: что изменил и что осталось проверить.")
        if self.fast_mode:
            self.system_prompt += (
                "\n\nБЫСТРЫЙ РЕЖИМ (включён пользователем):\n"
                "- не рассуждай вслух и не расписывай план — сразу действуй;\n"
                "- ответ короткий: что сделал и что осталось проверить;\n"
                "- не пересказывай содержимое файлов, не повторяй уже сказанное.")
        if self.disabled_tools:
            self.system_prompt += ("\n\nОтключённые инструменты (их нет в этой сессии): "
                                   + tooling.describe_disabled(self.disabled_tools))
        if self.github_token:
            self.system_prompt += (
                "\n\nGitHub: вход выполнен. Для публикации проекта используй "
                "github_publish (repo_name латиницей); пользователь подтвердит "
                "публикацию в отдельном окне.")
        else:
            self.system_prompt += (
                "\n\nGitHub: токен не задан — github_publish не сработает. "
                "Скажи пользователю войти в GitHub в настройках чата.")
        if instructions:
            self.system_prompt += f"\n\nДополнительные инструкции для этого чата:\n{instructions}"

        # Схема инструментов — это токены в каждом запросе: 40 инструментов
        # стоят ~5.5k. Если контекст не задан, Ollama берёт 4096 и молча режет
        # промпт вместе с инструментами: модель отвечает «файл создан», а файла
        # нет. Предупреждаем сразу, не дожидаясь загадочного поведения.
        self.context_need = 0
        self._warn_context_budget()


    # ------------------------------------------------------------------ helpers

    def _warn_context_budget(self):
        """Хватит ли контекста на системную инструкцию и схемы инструментов.

        Считаем без обращения к серверу (это не должно тормозить открытие чата):
        оценка по длине текста, кириллица дороже латиницы, поэтому 3.2 символа
        на токен. Если длина контекста в настройках чата не задана, Ollama
        берёт 4096 — при такой схеме инструментов это гарантированная обрезка.
        """
        if not self.is_local:
            return
        text = self.system_prompt
        try:
            text += json.dumps(self.tools, ensure_ascii=False)
        except (TypeError, ValueError):
            pass
        need = int(len(text) / 3.2)
        self.context_need = need
        limit = int(self.num_ctx or 0) or 4096  # 0 = столько Ollama берёт сама
        if need <= limit * 0.8:
            return
        advice = ((need // 4096) + 1) * 4096
        self.log(
            "[ВНИМАНИЕ] Системная инструкция и %d инструментов занимают ~%d токенов, "
            "а контекст модели %d. Ollama обрежет запрос: модель перестанет видеть "
            "инструменты и будет уверять, что всё сделала. Поставь в настройках чата "
            "длину контекста %d или больше."
            % (len(self.tools), need, limit, advice)
        )

    def _build_tools(self) -> list:
        """Схемы для модели: штатные + свои + (если разрешено) управление ими."""
        schemas = list(TOOLS)
        for tool in self.custom_tools:
            if tool.get("enabled", True):
                schemas.append(custom_tools.schema_for(tool))
        if self.allow_tools_edit:
            schemas.extend(custom_tools.management_schemas())
        disabled = list(self.disabled_tools)
        self.auto_disabled = []
        if self.tools_auto:
            # java_* в Python-проекте (и наоборот) — лишние килобайты схемы
            # в каждом запросе, то есть потраченные деньги на каждой итерации.
            self.auto_disabled = tooling.auto_disable(
                self.project_root, self.repository_root, bool(self.github_token))
            disabled += self.auto_disabled
        # Удалённые инструменты не отправляем модели, если в этом чате нечего
        # подключать или группа выключена галочкой: это токены на каждой итерации.
        disabled += self._remote_disabled_groups()
        # Поиск по смыслу работает только через /api/embed локальной Ollama.
        # У облачного провайдера такого механизма нет — схема была бы мёртвым
        # грузом в каждом запросе, а запросы платные.
        if not getattr(self, "is_local", True):
            disabled.append("search")
        self.tools_summary = tooling.describe_selection(schemas, disabled)
        return tooling.filter_tools(schemas, disabled)

    def _remote_disabled_groups(self) -> list:
        """Какие группы удалённого доступа скрывать в этом чате."""
        if self.remote_store is None:
            return ["terminal", "storage"]
        try:
            flags = self.remote_store.get_chat_flags(self.session_id)
            has_terminal = bool(self.remote_store.list_connections(self.session_id, "terminal"))
            has_storage = bool(self.remote_store.list_connections(self.session_id, "storage"))
        except Exception:  # noqa: BLE001 - битая база не должна ломать чат
            return ["terminal", "storage"]
        hidden = []
        if not (has_terminal and flags.get("terminal_enabled")):
            hidden.append("terminal")
        if not (has_storage and flags.get("storage_enabled")):
            hidden.append("storage")
        return hidden

    def _remote_prompt_block(self) -> str:
        """Что агент знает об удалённом доступе в этом чате (без паролей)."""
        if self.remote_store is None:
            return ""
        try:
            summary = self.remote_store.describe_chat(self.session_id)
        except Exception:  # noqa: BLE001
            return ""
        if not summary:
            return (
                "УДАЛЁННЫЙ ДОСТУП: в этом чате не настроены ни терминал, ни хранилища.\n"
                "Если пользователь просит подключиться к серверу или файловому хранилищу, "
                "попроси открыть «Инструменты → Удалённый доступ» и ввести адрес, логин и "
                "пароль (пароли хранятся в реестре Windows или в файле конфигов Linux, "
                "в чат и в базу они не попадают).")
        return ("УДАЛЁННЫЙ ДОСТУП (только этого чата): " + summary + "\n"
                "Логин и пароль подставляются автоматически из хранилища секретов — "
                "никогда не проси пользователя присылать пароль в чат.")

    def _rebuild_tools(self):
        """Перечитать свои инструменты (после создания/правки/удаления)."""
        if self.tools_dir:
            self.custom_tools = custom_tools.load_tools(self.tools_dir)
        else:
            self.custom_tools = []
        self.tools = self._build_tools()



    def _path(self, args: dict, key: str = "path", default: str = ".",
              must_exist: bool = False, for_write: bool = False,
              allow_repo: bool = False) -> str:
        value = args.get(key) or default
        return str(self.workspace.resolve(
            value,
            project=args.get("project"),
            must_exist=must_exist,
            for_write=for_write,
            allow_repo=allow_repo,
        ))

    def _project(self, args: dict):
        """Directory of the project the tool should work on."""
        project = args.get("project")
        if project:
            return self.workspace.project_dir(project)
        path = args.get("path")
        if path and path not in (".", "./", ""):
            try:
                return self.workspace.resolve(path)
            except WorkspaceError:
                pass
        return self.workspace.project_dir(None)

    def _execute_tool(self, name: str, args: dict) -> str:
        if self.cancel_check():
            return "[Отменено пользователем]"

        try:
            # ---------------------------------------------------------- files
            if name == "list_files":
                return list_files(self._path(args, "path"), self.write_roots)

            if name == "read_file":
                return read_file(
                    self._path(args, "path", must_exist=True, allow_repo=True),
                    self.read_roots,
                    start_line=int(args.get("start_line") or 0),
                    end_line=int(args.get("end_line") or 0),
                )

            if name == "write_file":
                return write_file(
                    self._path(args, "path", for_write=True),
                    args["content"], self.write_roots,
                )

            if name == "edit_file":
                return edit_file(
                    self._path(args, "path", must_exist=True),
                    args["old_text"], args["new_text"], self.write_roots,
                )

            if name == "deactivate_file":
                return deactivate_file(self._path(args, "path"), self.write_roots)

            if name == "backup_and_remove_dir":
                return backup_and_remove_dir(self._path(args, "path"), self.write_roots)

            if name == "batch_write_files":
                items = []
                for item in args.get("files") or []:
                    entry = dict(item)
                    if entry.get("path"):
                        entry["path"] = self._path(
                            {"path": entry["path"],
                             "project": entry.get("project") or args.get("project")},
                            "path", for_write=True,
                        )
                    items.append(entry)
                return batch_write_files(items, self.write_roots)

            if name == "read_entire_project_via_cli":
                target = self._project(args)
                return run_deployer_cli(str(target))

            # ----------------------------------------------------------- java
            if name == "java_scan":
                return java_scan(str(self._project(args)))

            if name == "java_read_class":
                target = self._path(args, "path", must_exist=True)
                owner = find_root(target, self.workspace.roots) or self.workspace.primary
                return java_read_class(target, str(owner))

            if name == "java_find":
                return java_find(
                    args["pattern"],
                    root=str(self._project(args)),
                    glob=args.get("glob") or "**/*.java",
                    kind=args.get("kind") or "any",
                    limit=int(args.get("limit") or 60),
                )

            if name == "java_build":
                return java_build(
                    tasks=args.get("tasks") or "compileJava",
                    timeout=int(args.get("timeout_seconds") or 600),
                    extra_args=args.get("extra_args") or "",
                    root=str(self._project(args)),
                )

            if name == "java_parse_log":
                return java_parse_log(
                    log_text=args.get("log_text") or "",
                    path=args.get("path") or "",
                    root=str(self._project(args)),
                )

            # --------------------------------------------------------- python
            if name == "python_info":
                return python_tools.python_info()

            if name == "python_run":
                return python_tools.python_run(
                    root=str(self._project(args)),
                    code=args.get("code") or "",
                    script=args.get("script") or "",
                    args=args.get("args") or [],
                    timeout_seconds=int(args.get("timeout_seconds") or 120),
                    allow=self.allow_python,
                )

            # ---------------------------------------------- поиск по смыслу
            if name == "semantic_search":
                return self._semantic_search(args)

            # ----------------------------------------------------- repository

            if name == "repo_list":
                return repo_tools.repo_list(
                    self.workspace,
                    path=args.get("path") or "",
                    pattern=args.get("pattern") or "",
                    recursive=args.get("recursive", True) is not False,
                    limit=int(args.get("limit") or 200),
                )

            if name == "repo_search":
                return repo_tools.repo_search(
                    self.workspace,
                    query=args.get("query") or "",
                    glob=args.get("glob") or "**/*",
                    content=bool(args.get("content")),
                    limit=int(args.get("limit") or 40),
                )

            if name == "repo_archive_list":
                return repo_tools.repo_archive_list(
                    self.workspace,
                    archive=args.get("archive") or args.get("path") or "",
                    limit=int(args.get("limit") or 200),
                )

            if name == "repo_read_file":
                return repo_tools.repo_read_file(
                    self.workspace,
                    path=args.get("path") or "",
                    member=args.get("member") or "",
                    start_line=int(args.get("start_line") or 0),
                    end_line=int(args.get("end_line") or 0),
                )

            if name == "repo_import_file":
                return repo_tools.repo_import_file(
                    self.workspace,
                    path=args.get("path") or "",
                    dest=args.get("dest") or "",
                    project=args.get("project"),
                    member=args.get("member") or "",
                    overwrite=bool(args.get("overwrite")),
                )

            if name == "repo_extract":
                strip = args.get("strip_components")
                return repo_tools.repo_extract(
                    self.workspace,
                    archive=args.get("archive") or args.get("path") or "",
                    dest=args.get("dest") or ".",
                    project=args.get("project"),
                    strip_components=None if strip in (None, "") else int(strip),
                    overwrite=bool(args.get("overwrite")),
                    include_executables=bool(args.get("include_executables")),
                    dry_run=bool(args.get("dry_run")),
                )

            if name == "repo_fetch":
                return repo_tools.repo_fetch(
                    self.workspace,
                    url=args.get("url") or "",
                    filename=args.get("filename") or "",
                    overwrite=bool(args.get("overwrite")),
                )

            if name == "repo_status":
                repository = str(self.workspace.repository or "")
                return repo_status_tools.check_repository(repository).as_text()

            # --------------------------------------------------------- баланс
            if name == "account_balance":
                return balance_tools.fetch_balance(
                    self._api_key, self._base_url, self.balance_url,
                    fresh=bool(args.get("fresh")),
                ).as_text(self.low_balance_threshold)

            # ------------------------------------------- удалённый доступ
            if name in remote_tools.ALL_NAMES:
                return self.remote.handle(name, args)

            # ---------------------------------------------------------- git
            if name == "git_status":
                return git_tools.status(str(self._project(args)))

            if name == "git_commit":
                ok, text = git_tools.commit_all(
                    str(self._project(args)),
                    message=str(args.get("message") or "AI-Connector: изменения"),
                    author=str(args.get("author") or self.github_author),
                )
                return ("OK\n" if ok else "Ошибка\n") + text

            if name == "github_publish":
                return redact_text(self._publish_to_github(args), self.github_token)

            # ------------------------------------- свои инструменты (custom_tools)
            if name == "tool_list":
                return custom_tools.describe_tools(self.custom_tools)

            if name in ("tool_create", "tool_update", "tool_delete"):
                return self._manage_tools(name, args)

            own = custom_tools.find_tool(self.custom_tools, name)
            if own is not None:
                self.log(f"[Tool] свой инструмент {name} ({own.get('kind')})")
                return custom_tools.run_tool(
                    own, args, cwd=self.project_root or "",
                    allow=self.allow_python,
                )

            return f"Unknown tool: {name}"



        except WorkspaceError as e:
            return f"Path error: {e}"
        except Exception as e:
            return f"Tool error: {type(e).__name__}: {e}"

    def _semantic_search(self, args: dict) -> str:
        """Поиск по смыслу в проекте через эмбеддинги локальной Ollama.

        Зачем агенту: не гадать имя файла по памяти, а спросить индекс —
        «где считается стоимость запроса» находит calculate_cost, хотя слова
        в запросе другие.
        """
        if not getattr(self, "is_local", True):
            return ("semantic_search доступен только с локальной Ollama: "
                    "эмбеддинги считает /api/embed на своём сервере. "
                    "Выбери провайдера Ollama или Base URL с портом 11434.")
        query = str(args.get("query") or "").strip()
        if not query:
            return "semantic_search: не указан параметр query"
        root = str(self._project(args))
        model = ollama_mod.pick_embed_model(self.ollama_host)
        if not model:
            return ("semantic_search: не найдена модель эмбеддингов. "
                    "Скачай её: ollama pull nomic-embed-text (около 270 МБ), "
                    "потом повтори запрос.")
        self.log(f"[Tool] semantic_search · модель {model}")
        ok, text = embeddings.search_project(
            root=root,
            host=self.ollama_host,
            query=query,
            limit=int(args.get("limit") or 6),
            rebuild=bool(args.get("rebuild")),
            model=model,
        )
        return text if ok else f"semantic_search: {text}"

    # ------------------------------------------------------- мысли агента

    def _manage_tools(self, name: str, args: dict) -> str:
        """tool_create / tool_update / tool_delete: только с разрешения пользователя."""
        if not self.allow_tools_edit:
            return ("Изменять набор инструментов нельзя: в настройках чата выключено "
                    "«Разрешить ИИ изменять, добавлять, удалять инструменты».")
        if not self.tools_dir:
            return "Неизвестна папка программы — инструменты сохранять негде."
        try:
            if name == "tool_create":
                ok, message = custom_tools.create_tool(self.tools_dir, {
                    "name": args.get("name"),
                    "description": args.get("description"),
                    "kind": args.get("kind") or "python",
                    "body": args.get("body"),
                    "parameters": args.get("parameters"),
                    "timeout_seconds": args.get("timeout_seconds"),
                    "created_by": "ai",
                })
            elif name == "tool_update":
                changes = {key: args.get(key) for key in
                           ("description", "kind", "body", "parameters",
                            "timeout_seconds")}
                ok, message = custom_tools.update_tool(
                    self.tools_dir, str(args.get("name") or ""), changes)
            else:
                ok, message = custom_tools.delete_tool(
                    self.tools_dir, str(args.get("name") or ""))
            self._rebuild_tools()
            self.log("[Tools] " + message)
            names = ", ".join(tool["name"] for tool in self.custom_tools) or "(нет)"
            return message + f"\nСейчас доступны свои инструменты: {names}"
        except Exception as error:  # noqa: BLE001 - модель должна увидеть причину
            return f"Ошибка управления инструментами: {type(error).__name__}: {error}"

    def _extract_reasoning(self, msg) -> str:

        """Вытащить «размышления» модели: у разных провайдеров своё поле."""
        for key in _REASONING_KEYS:
            value = getattr(msg, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (list, tuple)):
                parts = []
                for item in value:
                    if isinstance(item, dict):
                        parts.append(str(item.get("text") or item.get("content") or ""))
                    elif item:
                        parts.append(str(item))
                text = "\n".join(part for part in parts if part.strip())
                if text.strip():
                    return text.strip()
        extra = getattr(msg, "model_extra", None)
        if isinstance(extra, dict):
            for key in _REASONING_KEYS:
                value = extra.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    def _emit_thinking(self, msg, iteration: int):
        """Показать пользователю, что агент думает/планирует на этом шаге."""
        if not self.on_thinking:
            return
        text = self._extract_reasoning(msg)
        plan = (getattr(msg, "content", "") or "").strip() if getattr(msg, "tool_calls", None) else ""
        if plan and plan not in text:
            text = (text + "\n\n" + plan).strip() if text else plan
        if not text:
            return
        try:
            self.on_thinking(f"шаг {iteration}: {text}")
        except Exception:  # noqa: BLE001 - UI не должен ронять агента
            pass

    # -------------------------------------------------------------------- loop


    # -------------------------------------------------------------- деньги

    def _track_context(self, prompt_tokens: int):
        """Сколько контекста занял последний запрос и сколько влезает всего."""
        try:
            self.context_used = int(prompt_tokens or 0)
        except (TypeError, ValueError):
            self.context_used = 0
        if self.is_local and not self._context_limit_checked:
            self._context_limit_checked = True
            try:
                from app.agent import ollama_service
                self.context_limit = (ollama_service.model_context_length(
                    self.ollama_host, self.model, self.num_ctx) or 0)
            except Exception:  # noqa: BLE001 - лимит просто останется нулевым
                self.context_limit = 0
        for call in (
            lambda: self.on_context(self.context_used, self.context_limit,
                                    self.total_tokens),
            lambda: self.on_context(self.context_used, self.context_limit),
        ):
            try:
                call()
                break
            except TypeError:
                continue
            except Exception:  # noqa: BLE001 - UI не должен ронять агента
                break

    def _account_usage(self, response):
        """Посчитать стоимость запроса по usage провайдера и отдать наружу."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        if prompt_tokens or completion_tokens:
            self.total_tokens += prompt_tokens + completion_tokens
        self._track_context(prompt_tokens)
        cost = pricing.estimate_rub(
            self.model, prompt_tokens, completion_tokens,
            rub_per_usd=self.rub_per_usd, manual=self.manual_price,
            local=self.is_local,
        )
        if cost > 0:
            self.cost_rub += cost
        try:
            self.on_cost(self.cost_rub, cost)
        except Exception:  # noqa: BLE001 - UI не должен ронять агента
            pass

    # ------------------------------------------------------------- github

    def _publish_to_github(self, args: dict) -> str:
        """Публикация в GitHub только после подтверждения пользователем."""
        if not self.github_token:
            return ("Токен GitHub не задан. Попроси пользователя открыть настройки "
                    "чата и войти в GitHub (personal access token, права repo).")
        request = {
            "action": "github_publish",
            "path": str(self._project(args)),
            "repo_name": str(args.get("repo_name") or "").strip(),
            "author": str(args.get("author") or self.github_author or "").strip(),
            "description": str(args.get("description") or "").strip(),
            "private": bool(args.get("private")),
            "open_after": self.github_open_after,
        }
        if not request["repo_name"]:
            return "Не указано имя репозитория (repo_name)."
        answer = self.confirm(request)
        if not answer:
            return "Пользователь отклонил публикацию на GitHub."
        if answer.get("action") == "cancel":
            return "Пользователь отклонил публикацию на GitHub."

        repo = str(answer.get("repo_name") or request["repo_name"]).strip()
        author = str(answer.get("author") or request["author"]).strip()
        description = str(answer.get("description") or request["description"])
        private = bool(answer.get("private", request["private"]))
        open_after = bool(answer.get("open_after", request["open_after"]))

        ok, text = git_tools.publish(
            request["path"], repo, self.github_token, author=author,
            description=description, private=private,
        )
        if not ok:
            return "Не удалось опубликовать: " + text
        if open_after:
            url = text.split("Готово:")[-1].strip()
            if url.startswith("http"):
                try:
                    import webbrowser
                    webbrowser.open(url)
                except Exception:  # noqa: BLE001
                    pass
        return text

    def _create(self, conversation):
        """Запрос к модели. В быстром режиме — без «рассуждений» провайдера.

        У локальной Ollama размышления отключает think=false (параметр
        /api/chat). У облачных моделей единого стандарта нет: просим пониженное
        усилие рассуждений (reasoning_effort). Если сервер такого поля не знает,
        он отвечает 400 — тогда повторяем запрос без него, а не ломаем диалог.
        """
        payload = {"model": self.model, "messages": conversation,
                   "tools": self.tools}
        if not (self.fast_mode and not self.is_local) or self.fast_mode_unsupported:
            return self.client.chat.completions.create(**payload)
        try:
            return self.client.chat.completions.create(
                **payload, extra_body={"reasoning_effort": "low"})
        except APIStatusError as error:
            status = int(getattr(error, "status_code", 0) or 0)
            if status not in (400, 404, 422):
                raise
            self.fast_mode_unsupported = True
            self.log("[Fast] провайдер не принял отключение рассуждений — "
                     "повторяю запрос без этого параметра")
            return self.client.chat.completions.create(**payload)

    def run(self, messages: list, on_tool_log=None) -> str:
        # Ключ платный: провайдеры любят возвращать его в тексте ошибки 401,
        # поэтому наружу (в чат и консоль) уходит только обезвреженный текст.
        return redact_text(self._run(messages, on_tool_log), self._api_key)

    def _run(self, messages: list, on_tool_log=None) -> str:
        conversation = [{"role": "system", "content": self.system_prompt}] + messages

        iteration = 0
        while iteration < self.max_iterations:
            if self.cancel_check():
                return "[Отменено пользователем]"

            iteration += 1
            self.request_count += 1
            try:
                response = self._create(conversation)
            except AuthenticationError as e:
                return f"[ERROR 401] Authentication failed: {e.message}"
            except NotFoundError as e:
                return f"[ERROR 404] Model not found ({self.model}): {e.message}"
            except RateLimitError as e:
                return f"[ERROR 429] Rate limit: {e.message}"
            except APITimeoutError:
                return "[ERROR TIMEOUT] Request timed out"
            except APIConnectionError as e:
                if self.is_local:
                    from app.agent import ollama
                    return ("[ERROR CONN] Локальная модель не отвечает: "
                            + ollama.start_hint())
                return f"[ERROR CONN] Connection failed: {e.message}"
            except APIStatusError as e:
                return f"[ERROR {e.status_code}] {e.message}"
            except OllamaError as e:
                return f"[ERROR OLLAMA] {e}"
            except Exception as e:
                return f"[UNKNOWN] {type(e).__name__}: {e}"

            self._account_usage(response)

            if not response.choices:
                return "[ERROR] Provider returned no choices."

            msg = response.choices[0].message
            conversation.append(msg)

            self._emit_thinking(msg, iteration)

            if not msg.tool_calls:
                text = msg.content or ""
                # Проверено, что модель не видит инструментов: её ответ про
                # «файл создан» — выдумка. Говорим об этом прямо в чате, иначе
                # человек ищет файл, которого нет, и не понимает, что не так.
                if self.tools_delivered is False:
                    text += ("\n\n⚠ Модель не принимает инструменты (это проверено, "
                             "а не предположение): файлы не создавались и команды "
                             "не выполнялись. Выбери другую модель в настройках "
                             "чата — кнопка «Проверить инструменты» там же.")
                return text

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                    if on_tool_log:
                        on_tool_log(f"[WARN] Failed to parse arguments for {tc.function.name}")

                if on_tool_log:
                    if self.verbose_tools:
                        args_str = json.dumps(args, ensure_ascii=False)
                        if len(args_str) > 150:
                            args_str = args_str[:147] + "..."
                        on_tool_log(f"[Tool] {tc.function.name}({args_str})")
                    else:
                        # Короткая подпись вместо простыни JSON (кнопка «Кратко»).
                        on_tool_log(tooling.short_tool_call(tc.function.name, args))

                result = self._execute_tool(tc.function.name, args)

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result
                })

        return "[ERROR] Достигнут лимит итераций вызова инструментов. Агент зациклился."
