import os
from PyQt5.QtCore import QSettings

from app.config.secrets import SecretStore
from app.runtime_env import clean_text, normalize_path

DEFAULT_BASE_URL = "https://routerai.ru/api/v1"
DEFAULT_MASTER_MODEL = "deepseek/deepseek-v4-pro-0813"
DEFAULT_WORKER_MODEL = "deepseek/deepseek-v4-pro-0813"

class AppSettings:
    def __init__(self, settings_path: str = "", secrets_path: str = ""):
        # Жестко привязываем файл настроек к корню проекта
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.project_dir = base_dir
        self.settings_path = settings_path or os.path.join(base_dir, "settings.ini")

        self.qsettings = QSettings(self.settings_path, QSettings.IniFormat)
        # Ключ платный, поэтому он хранится вне папки проекта:
        # пусто -> default_secrets_path() (профиль пользователя) или AI_CONNECTOR_SECRETS
        self.secrets = SecretStore(secrets_path)

    def get_base_url(self) -> str:
        val = self.qsettings.value("api/base_url", DEFAULT_BASE_URL)
        return str(val).strip() if val else DEFAULT_BASE_URL

    def set_base_url(self, url: str):
        self.qsettings.setValue("api/base_url", url)
        self.qsettings.sync()

    def get_api_key(self) -> str:
        self._migrate_legacy_key()
        return self.secrets.get()

    def set_api_key(self, key: str):
        self.secrets.set(key)
        # в файле внутри проекта ключа быть не должно
        self.qsettings.remove("api/key")
        self.qsettings.sync()

    def get_api_key_source(self) -> str:
        """'env' | 'file' | 'none' — откуда реально берётся ключ."""
        self._migrate_legacy_key()
        return self.secrets.source()

    def get_api_key_location(self) -> str:
        self._migrate_legacy_key()
        return self.secrets.describe()

    def _migrate_legacy_key(self):
        """Старые версии хранили ключ в settings.ini рядом с кодом — переносим."""
        legacy = str(self.qsettings.value("api/key", "") or "").strip()
        if not legacy:
            return
        if not self.secrets.get_env() and not self.secrets.get_file():
            self.secrets.set(legacy)
        self.qsettings.remove("api/key")
        self.qsettings.sync()

    def get_master_model(self) -> str:
        val = self.qsettings.value("models/master", DEFAULT_MASTER_MODEL)
        return str(val).strip() if val else DEFAULT_MASTER_MODEL

    def set_master_model(self, model_id: str):
        self.qsettings.setValue("models/master", model_id)
        self.qsettings.sync()

    def get_worker_model(self) -> str:
        val = self.qsettings.value("models/worker", DEFAULT_WORKER_MODEL)
        return str(val).strip() if val else DEFAULT_WORKER_MODEL

    def set_worker_model(self, model_id: str):
        self.qsettings.setValue("models/worker", model_id)
        self.qsettings.sync()

    def get_use_two_models(self) -> bool:
        val = self.qsettings.value("models/use_two", False)
        # QSettings может вернуть строку "true" или "false", поэтому приводим к bool
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_use_two_models(self, value: bool):
        self.qsettings.setValue("models/use_two", value)
        self.qsettings.sync()

    def get_project_root(self) -> str:
        # Путь нормализуется: без ^M, с прямыми слэшами (Linux понимает только их).
        val = self.qsettings.value("project/root", "")
        return normalize_path(val) if val else ""

    def set_project_root(self, path: str):
        self.qsettings.setValue("project/root", normalize_path(path))
        self.qsettings.sync()

    def get_repository_root(self) -> str:
        val = self.qsettings.value("repository/root", "")
        return normalize_path(val) if val else ""

    def set_repository_root(self, path: str):
        self.qsettings.setValue("repository/root", normalize_path(path))
        self.qsettings.sync()

    def get_allow_network(self) -> bool:
        val = self.qsettings.value("repository/allow_network", True)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_allow_network(self, value: bool):
        self.qsettings.setValue("repository/allow_network", value)
        self.qsettings.sync()

    def get_allow_python(self) -> bool:
        """Разрешён ли агенту запуск Python (инструмент python_run)."""
        val = self.qsettings.value("tools/allow_python", True)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_allow_python(self, value: bool):
        self.qsettings.setValue("tools/allow_python", value)
        self.qsettings.sync()

    # ------------------------------------------------------------- баланс

    def get_balance_url(self) -> str:
        """Явный URL баланса; пусто = Base URL + '/credits'."""
        val = self.qsettings.value("balance/url", "")
        return str(val).strip() if val else ""

    def set_balance_url(self, url: str):
        self.qsettings.setValue("balance/url", (url or "").strip())
        self.qsettings.sync()

    def get_low_balance_threshold(self) -> float:
        """Ниже этого остатка агент работает в режиме экономии (0 = выкл)."""
        val = self.qsettings.value("balance/low_threshold", 50.0)
        try:
            return float(val)
        except (TypeError, ValueError):
            return 50.0

    def set_low_balance_threshold(self, value):
        try:
            number = max(0.0, float(value))
        except (TypeError, ValueError):
            number = 50.0
        self.qsettings.setValue("balance/low_threshold", number)
        self.qsettings.sync()

    def get_share_balance(self) -> bool:
        """Передавать ли остаток агенту и включать режим экономии."""
        val = self.qsettings.value("balance/share_with_agent", True)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_share_balance(self, value: bool):
        self.qsettings.setValue("balance/share_with_agent", value)
        self.qsettings.sync()

    # ----------------------------------------------------------- оформление

    def get_theme(self) -> str:
        """Имя темы: dark | midnight | light."""
        val = self.qsettings.value("ui/theme", "dark")
        return str(val).strip() if val else "dark"

    def set_theme(self, name: str):
        self.qsettings.setValue("ui/theme", (name or "dark").strip())
        self.qsettings.sync()

    def get_show_background(self) -> bool:
        """Показывать фоновое изображение (галочка в меню «Вид»)."""
        val = self.qsettings.value("ui/show_background", True)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_show_background(self, value: bool):
        self.qsettings.setValue("ui/show_background", bool(value))
        self.qsettings.sync()

    def get_transparent_top_bar(self) -> bool:
        """Верхняя панель прозрачная: сквозь неё видно фоновую картинку.

        По умолчанию включено — панель выглядит частью окна, а не отдельной
        серой полосой. Выключено — плотная плашка в цвет темы.
        """
        val = self.qsettings.value("ui/transparent_top_bar", True)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_transparent_top_bar(self, value: bool):
        self.qsettings.setValue("ui/transparent_top_bar", bool(value))
        self.qsettings.sync()

    def get_hide_window_buttons(self) -> bool:
        """Скрывать кнопки окна (свернуть/развернуть/закрыть) в полосе заголовка.

        Нужно тайловым оконным менеджерам на Linux: они не умеют работать с
        кнопками внутри клиентской области окна.
        """
        val = self.qsettings.value("ui/hide_window_buttons", False)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_hide_window_buttons(self, value: bool):
        self.qsettings.setValue("ui/hide_window_buttons", bool(value))
        self.qsettings.sync()

    def get_background_image(self) -> str:
        """Путь к картинке на фоне окна; пусто = градиент по теме."""
        val = self.qsettings.value("ui/background_image", "")
        return normalize_path(val) if val else ""

    def set_background_image(self, path: str):
        self.qsettings.setValue("ui/background_image", normalize_path(path))
        self.qsettings.sync()

    def get_background_dim(self) -> int:
        """Затемнение фона в процентах (0..90), чтобы текст читался."""
        val = self.qsettings.value("ui/background_dim", 45)
        try:
            return max(0, min(90, int(float(val))))
        except (TypeError, ValueError):
            return 45

    def set_background_dim(self, value):
        try:
            number = max(0, min(90, int(value)))
        except (TypeError, ValueError):
            number = 45
        self.qsettings.setValue("ui/background_dim", number)
        self.qsettings.sync()




    def get_instructions(self) -> str:
        val = self.qsettings.value("project/instructions", "")
        return clean_text(val).strip() if val else ""

    def set_instructions(self, text: str):
        self.qsettings.setValue("project/instructions", clean_text(text))
        self.qsettings.sync()

    # --------------------------------------------------- скругления и рамка

    def get_corner_mode(self) -> int:
        """Режим скругления углов: 1 - всё, 2 - кнопки, 3 - панели, 4 - максимум."""
        val = self.qsettings.value("ui/corner_mode", 1)
        try:
            number = int(str(val).strip())
        except (TypeError, ValueError):
            return 1
        return number if number in (1, 2, 3, 4) else 1

    def set_corner_mode(self, value):
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            number = 1
        self.qsettings.setValue("ui/corner_mode", number if number in (1, 2, 3, 4) else 1)
        self.qsettings.sync()

    def get_square_console(self) -> bool:
        """Прямые углы у консоли (независимо от общего режима скругления)."""
        val = self.qsettings.value("ui/console_square", False)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_square_console(self, value: bool):
        self.qsettings.setValue("ui/console_square", bool(value))
        self.qsettings.sync()

    def get_frameless(self) -> bool:
        """Своя полоса заголовка вместо системной рамки (окрашивается темой)."""
        val = self.qsettings.value("ui/frameless", True)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_frameless(self, value: bool):
        self.qsettings.setValue("ui/frameless", bool(value))
        self.qsettings.sync()

    # ------------------------------------------------------- мысли агента

    def get_show_thinking(self) -> bool:
        """Показывать в чате, что агент думает по ходу задачи."""
        val = self.qsettings.value("chat/show_thinking", True)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def set_show_thinking(self, value: bool):
        self.qsettings.setValue("chat/show_thinking", bool(value))
        self.qsettings.sync()

    # ------------------------------------------------- провайдеры ИИ

    def _flag(self, key: str, default: bool = False) -> bool:
        val = self.qsettings.value(key, default)
        if isinstance(val, str):
            return val.lower() == "true"
        return bool(val)

    def _number(self, key: str, default: float) -> float:
        val = self.qsettings.value(key, default)
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def get_provider(self) -> str:
        """Активный провайдер: routerai | openai | xai | gemini | ollama | ..."""
        from app.agent import providers
        return providers.normalize(self.qsettings.value("api/provider", providers.DEFAULT_PROVIDER))

    def set_provider(self, key: str):
        from app.agent import providers
        self.qsettings.setValue("api/provider", providers.normalize(key))
        self.qsettings.sync()

    def get_api_key_for(self, provider: str) -> str:
        """Ключ конкретного провайдера (у локальной Ollama ключа нет)."""
        from app.agent import providers
        info = providers.get(provider)
        if not info.needs_key:
            return ""
        key = self.secrets.get_value("api/" + info.key)
        if key:
            return key
        # старый единый ключ считаем ключом RouterAI
        if info.key == providers.DEFAULT_PROVIDER:
            return self.secrets.get()
        return ""

    def set_api_key_for(self, provider: str, key: str):
        from app.agent import providers
        info = providers.get(provider)
        self.secrets.set_value("api/" + info.key, key)
        if info.key == providers.DEFAULT_PROVIDER:
            self.secrets.set(key)

    def has_api_key_for(self, provider: str) -> bool:
        return bool(self.get_api_key_for(provider))

    def get_balance_url_for(self, provider: str) -> str:
        """Ссылка «где посмотреть баланс» по провайдеру (своя важнее)."""
        from app.agent import providers
        custom = str(self.qsettings.value("balance/url_" + providers.normalize(provider), "") or "")
        return providers.balance_url_for(provider, custom)

    def set_balance_url_for(self, provider: str, url: str):
        from app.agent import providers
        self.qsettings.setValue("balance/url_" + providers.normalize(provider),
                                (url or "").strip())
        self.qsettings.sync()

    def get_ollama_host(self) -> str:
        val = self.qsettings.value("ollama/host", "http://localhost:11434")
        return str(val).strip() if val else "http://localhost:11434"

    def set_ollama_host(self, host: str):
        self.qsettings.setValue("ollama/host", (host or "http://localhost:11434").strip())
        self.qsettings.sync()

    def get_ollama_mode(self) -> str:
        """Куда грузить локальную модель: auto | vram | ram.

        vram = все слои на видеокарту, ram = только процессор. Именно из-за
        отсутствия этой настройки модель целиком уезжала в оперативную память.
        """
        from app.agent import ollama
        # По умолчанию просим видеокарту: иначе Ollama нередко грузит модель
        # в оперативную память, даже когда GPU свободна.
        return ollama.normalize_mode(self.qsettings.value("ollama/mode", "vram"))

    def set_ollama_mode(self, mode: str):
        from app.agent import ollama
        self.qsettings.setValue("ollama/mode", ollama.normalize_mode(mode))
        self.qsettings.sync()

    def get_ollama_num_ctx(self) -> int:
        """Длина контекста локальной модели (num_ctx), 0 = решает Ollama."""
        try:
            value = int(float(self.qsettings.value("ollama/num_ctx", 8192)))
        except (TypeError, ValueError):
            return 8192
        return max(0, min(262144, value))

    def set_ollama_num_ctx(self, value):
        try:
            number = max(0, min(262144, int(float(value))))
        except (TypeError, ValueError):
            number = 8192
        self.qsettings.setValue("ollama/num_ctx", number)
        self.qsettings.sync()

    def get_ollama_keep_alive(self) -> str:
        """Сколько держать модель в памяти после ответа (keep_alive)."""
        val = self.qsettings.value("ollama/keep_alive", "30m")
        return str(val).strip() if val else "30m"

    def set_ollama_keep_alive(self, value: str):
        self.qsettings.setValue("ollama/keep_alive", (value or "30m").strip())
        self.qsettings.sync()

    def get_ollama_flags(self) -> str:
        """Дополнительные аргументы `ollama serve` (может быть пусто).

        Внимание: в свежих сборках Ollama флага --embeddings уже нет — сервер
        падает с «unknown flag». Эмбеддинги включаются скачиванием модели
        nomic-embed-text, поэтому по умолчанию аргументов нет.
        """
        val = self.qsettings.value("ollama/flags", "")
        return str(val).strip() if val else ""

    def set_ollama_flags(self, text: str):
        self.qsettings.setValue("ollama/flags", (text or "").strip())
        self.qsettings.sync()

    def get_ollama_autostart(self) -> bool:
        """Поднимать сервер Ollama при старте программы (если он не работает)."""
        return self._flag("ollama/autostart", True)

    def set_ollama_autostart(self, value: bool):
        self.qsettings.setValue("ollama/autostart", bool(value))
        self.qsettings.sync()

    def get_ollama_embed_model(self) -> str:
        """Какая модель эмбеддингов используется для поиска по смыслу."""
        val = self.qsettings.value("ollama/embed_model", "nomic-embed-text")
        return str(val).strip() if val else "nomic-embed-text"

    def set_ollama_embed_model(self, name: str):
        self.qsettings.setValue("ollama/embed_model",
                                (name or "nomic-embed-text").strip())
        self.qsettings.sync()

    # ------------------------------------------------------- быстрый режим

    def get_fast_mode(self) -> bool:
        """Быстрый режим: модель не рассуждает вслух, отвечает сразу.

        У локальной Ollama это think=false (реально экономит время), у облачных
        провайдеров — запрос пониженного «усилия рассуждений» (если сервер
        такое поле не принимает, запрос повторяется без него).
        """
        return self._flag("chat/fast_mode", False)

    def set_fast_mode(self, value: bool):
        self.qsettings.setValue("chat/fast_mode", bool(value))
        self.qsettings.sync()

    # ------------------------------------------------------ деньги/экономия

    def get_rub_per_usd(self) -> float:
        return self._number("money/rub_per_usd", 95.0)

    def set_rub_per_usd(self, value):
        self.qsettings.setValue("money/rub_per_usd", self._number("money/rub_per_usd", 95.0)
                                if value in (None, "") else value)
        self.qsettings.sync()

    def get_price_override(self) -> str:
        """'₽ за 1M входных, выходных' — пусто = считать по таблице."""
        val = self.qsettings.value("money/price_override", "")
        return str(val).strip() if val else ""

    def set_price_override(self, text: str):
        self.qsettings.setValue("money/price_override", (text or "").strip())
        self.qsettings.sync()

    def get_economy_mode(self) -> bool:
        """Экономный режим: меньше итераций, без лишних сборок и тестов."""
        return self._flag("money/economy_mode", False)

    def set_economy_mode(self, value: bool):
        self.qsettings.setValue("money/economy_mode", bool(value))
        self.qsettings.sync()

    # ------------------------------------------------- инструменты агента

    def get_disabled_tools(self) -> list:
        """Список выключенных групп и отдельных инструментов."""
        raw = self.qsettings.value("tools/disabled", "")
        if isinstance(raw, (list, tuple)):
            items = [str(item) for item in raw]
        else:
            items = str(raw or "").split(",")
        return [item.strip() for item in items if str(item).strip()]

    def set_disabled_tools(self, items):
        clean = ",".join(sorted({str(item).strip() for item in (items or []) if str(item).strip()}))
        self.qsettings.setValue("tools/disabled", clean)
        self.qsettings.sync()

    def get_tools_auto(self) -> bool:
        """Скрывать группы инструментов, бесполезные в текущем проекте.

        java_* в Python-проекте не нужны, а схема каждого инструмента уходит
        модели в каждом запросе — это токены и деньги.
        """
        return self._flag("tools/auto", True)

    def set_tools_auto(self, value: bool):
        self.qsettings.setValue("tools/auto", bool(value))
        self.qsettings.sync()

    def get_declined_db(self) -> str:
        """База чатов другой версии, от импорта которой уже отказались."""
        return str(self.qsettings.value("db/declined", "") or "")

    def set_declined_db(self, marker: str):
        self.qsettings.setValue("db/declined", str(marker or ""))
        self.qsettings.sync()

    def get_ai_can_edit_tools(self) -> bool:
        """Разрешение по умолчанию: ИИ может создавать/менять/удалять инструменты.

        В каждом чате это можно переопределить (настройки чата).
        """
        return self._flag("tools/ai_can_edit", False)

    def set_ai_can_edit_tools(self, value: bool):
        self.qsettings.setValue("tools/ai_can_edit", bool(value))
        self.qsettings.sync()

    def get_tools_dir(self) -> str:
        """Папка, где лежит data/custom_tools.json (свои инструменты)."""
        return self.project_dir

    def get_verbose_tool_log(self) -> bool:
        """Журнал инструментов: True — полный дамп аргументов, False — кратко."""
        return self._flag("tools/verbose_log", False)

    def set_verbose_tool_log(self, value: bool):
        self.qsettings.setValue("tools/verbose_log", bool(value))
        self.qsettings.sync()


    # ---------------------------------------------------------- GitHub

    def get_github_token(self) -> str:
        return self.secrets.get_value("github/token")

    def set_github_token(self, token: str):
        self.secrets.set_value("github/token", token)

    def get_github_author(self) -> str:
        val = self.qsettings.value("github/author", "")
        return str(val).strip() if val else ""

    def set_github_author(self, name: str):
        self.qsettings.setValue("github/author", (name or "").strip())
        self.qsettings.sync()

    def get_github_open_after(self) -> bool:
        return self._flag("github/open_after", True)

    def set_github_open_after(self, value: bool):
        self.qsettings.setValue("github/open_after", bool(value))
        self.qsettings.sync()

    # -------------------------------------------------------- верстка

    def get_sidebar_width(self) -> int:
        try:
            return max(160, min(640, int(self._number("ui/sidebar_width", 250))))
        except (TypeError, ValueError):
            return 250

    def set_sidebar_width(self, value):
        try:
            number = max(160, min(640, int(value)))
        except (TypeError, ValueError):
            number = 250
        self.qsettings.setValue("ui/sidebar_width", number)
        self.qsettings.sync()