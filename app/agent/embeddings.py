"""Поиск по смыслу в проекте: эмбеддинги Ollama (/api/embed) и косинус.

Зачем это здесь: обычный поиск ищет точные слова. В коде может быть написано
calculate_cost, а спросить хочется «где считается стоимость запроса» — по
словам не совпадёт, по вектору совпадёт.

Модуль ничего не знает ни про Qt, ни про агента: только ollama + stdlib.
Индекс кэшируется на диск (data/embed_cache), поэтому второй вопрос не
пересчитывает весь проект заново.
"""

import hashlib
import json
import math
import os
import time

from app.agent import ollama

DEFAULT_MODEL = "nomic-embed-text"
INDEX_VERSION = 1

# Что вообще имеет смысл векторизовать (бинарники и картинки — нет).
TEXT_EXT = (
    ".py", ".pyw", ".java", ".kt", ".kts", ".js", ".jsx", ".ts", ".tsx",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift",
    ".sh", ".bat", ".ps1", ".sql", ".gradle", ".properties", ".toml", ".ini",
    ".json", ".yaml", ".yml", ".md", ".txt", ".html", ".htm", ".css", ".xml",
    ".cfg", ".conf", ".env.example",
)

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__", "node_modules",
    "build", ".gradle", "out", "target", "bin", "obj", "dist", "run",
    "venv", ".venv", "env", ".env", "site-packages", ".pytest_cache",
    ".mypy_cache", "crash_reports", "data", "repo",
}

MAX_FILES = 400
MAX_FILE_BYTES = 48 * 1024
CHUNK_CHARS = 900
OVERLAP_LINES = 3
BATCH = 24


def cosine(a, b) -> float:
    """Косинус между векторами (0 — не связаны, 1 — одно и то же)."""
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for index in range(size):
        left = float(a[index])
        right = float(b[index])
        dot += left * right
        norm_a += left * left
        norm_b += right * right
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _cache_dir() -> str:
    """Куда складывать индекс: <папка программы>/data/embed_cache."""
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "data", "embed_cache")


def _iter_files(root: str) -> list:
    """Текстовые файлы проекта (относительные пути, без мусора и артефактов)."""
    found = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(
            name for name in dirs
            if name.lower() not in SKIP_DIRS and not name.startswith("."))
        for name in sorted(files):
            if name.startswith(".") and not name.startswith(".env"):
                continue
            low = name.lower()
            if not low.endswith(TEXT_EXT):
                continue
            full = os.path.join(folder, name)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append(os.path.relpath(full, root).replace("\\", "/"))
            if len(found) >= MAX_FILES:
                return found
    return found


def _chunks(text: str) -> list:
    """Нарезать файл на куски по ~900 символов с перекрытием в 3 строки.

    Перекрытие нужно, чтобы функция не разрывалась ровно на границе куска.
    Возвращает [(номер первой строки, текст), ...].
    """
    lines = text.splitlines()
    out = []
    index = 0
    while index < len(lines):
        start = index
        size = 0
        buffer = []
        while index < len(lines) and size < CHUNK_CHARS:
            buffer.append(lines[index])
            size += len(lines[index]) + 1
            index += 1
        chunk = "\n".join(buffer).strip()
        if len(chunk) >= 40:
            out.append((start + 1, chunk))
        if index >= len(lines):
            break
        index = max(index - OVERLAP_LINES, start + 1)
    return out


class ProjectIndex:
    """Индекс «кусок текста -> вектор» для одной папки проекта.

    Один экземпляр = один проект + одна модель эмбеддингов. Пересборка
    запускается только если файлы изменились (сравниваем подпись: пути,
    размеры, время правки).
    """

    def __init__(self, root: str, host: str = "", model: str = "",
                 cache_dir: str = ""):
        self.root = os.path.abspath(root) if root else ""
        self.host = ollama.normalize_host(host)
        self.model = str(model or DEFAULT_MODEL)
        self.cache_dir = cache_dir or _cache_dir()
        self.items = []
        self.vectors = []
        self.note = ""
        self.at = 0.0
        self.files = 0

    # ------------------------------------------------------------------ кэш

    def _cache_path(self) -> str:
        raw = f"{self.root}|{self.host}|{self.model}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"index-{digest}.json")

    def _signature(self, files) -> str:
        hasher = hashlib.sha1()
        hasher.update(f"{INDEX_VERSION}|{self.model}".encode("utf-8"))
        for relative in files:
            full = os.path.join(self.root, relative)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            hasher.update(f"{relative}|{stat.st_size}|{int(stat.st_mtime)}".encode("utf-8"))
        return hasher.hexdigest()

    def _load(self, signature: str) -> bool:
        path = self._cache_path()
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return False
        if data.get("signature") != signature:
            return False
        items = data.get("items") or []
        vectors = data.get("vectors") or []
        if not items or len(items) != len(vectors):
            return False
        self.items = items
        self.vectors = vectors
        self.files = int(data.get("files") or 0)
        self.at = float(data.get("at") or 0.0)
        self.note = "из кэша"
        return True

    def _save(self, signature: str) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            path = self._cache_path()
            payload = {
                "version": INDEX_VERSION,
                "signature": signature,
                "model": self.model,
                "files": self.files,
                "at": time.time(),
                "items": self.items,
                "vectors": self.vectors,
            }
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
        except OSError:
            # Кэш — ускорение, а не необходимость: без него просто дольше.
            self.note = "кэш не сохранился"

    # -------------------------------------------------------------- сборка

    def build(self, force: bool = False) -> tuple:
        """Собрать индекс: (True, текст о результате) либо (False, ошибка)."""
        if not self.root or not os.path.isdir(self.root):
            return False, f"Папка проекта не найдена: {self.root}"
        files = _iter_files(self.root)
        if not files:
            return False, "В проекте нет текстовых файлов, которые стоит искать."
        signature = self._signature(files)
        if not force and self.items and self.note != "кэш не сохранился":
            return True, self._describe()
        if not force and self._load(signature):
            return True, self._describe()

        model = self.model
        texts = []
        items = []
        for relative in files:
            try:
                with open(os.path.join(self.root, relative), "r",
                          encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            for line, chunk in _chunks(text):
                items.append({"path": relative, "line": line, "text": chunk})
                texts.append(chunk)
        if not items:
            return False, "Нечего индексировать: файлы пустые."

        vectors = []
        for start in range(0, len(texts), BATCH):
            ok, result = ollama.embed(self.host, model, texts[start:start + BATCH])
            if not ok and start == 0:
                # Возможно, модели с этим именем нет — берём любую скачанную.
                fallback = ollama.pick_embed_model(self.host)
                if fallback and fallback != model:
                    model = fallback
                    ok, result = ollama.embed(self.host, model, texts[start:start + BATCH])
            if not ok:
                return False, str(result)
            if len(vectors) == 0:
                self.model = model      # имя модели могло смениться на найденное
            vectors.extend(result)
        if len(vectors) != len(items):
            return False, "Ollama вернула не то количество векторов — попробуй ещё раз."

        self.items = items
        self.vectors = vectors
        self.files = len(files)
        self.at = time.time()
        self.note = "собран"
        self._save(signature)
        return True, self._describe()

    def _describe(self) -> str:
        if not self.items:
            self.note = self.note or "пусто"
            return "Индекс пуст."
        return (f"Индекс: {len(self.items)} фрагментов из {self.files} файлов "
                f"(модель {self.model}, {self.note}).")

    # ------------------------------------------------------------- поиск

    def search(self, query: str, limit: int = 6) -> tuple:
        """Найти фрагменты, близкие к запросу: (True, текст с результатами)."""
        query = str(query or "").strip()
        if not query:
            return False, "Пустой запрос"
        ok, text = self.build()
        if not ok:
            return False, str(text)
        ok, result = ollama.embed(self.host, self.model, [query])
        if not ok:
            return False, str(result)
        target = result[0]
        scored = []
        for item, vector in zip(self.items, self.vectors):
            scored.append((cosine(target, vector), item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        try:
            limit = max(1, min(int(limit or 6), 20))
        except (TypeError, ValueError):
            limit = 6
        lines = [f"Поиск по смыслу: «{query}» · {self._describe()}"]
        for score, item in scored[:limit]:
            excerpt = item["text"].strip()
            if len(excerpt) > 700:
                excerpt = excerpt[:700] + "…"
            lines.append(f"\n--- {item['path']}:{item['line']} "
                         f"(близость {score:.3f}) ---\n{excerpt}")
        if len(scored) < 2:
            lines.append("\n(в индексе мало файлов — возможно, проект ещё пустой)")
        return True, "\n".join(lines)


# Один индекс на (проект, сервер, модель) внутри процесса: второй вопрос в том
# же чате не пересобирает ничего.
_INDEXES = {}


def search_project(root: str, host: str = "", query: str = "", limit: int = 6,
                   rebuild: bool = False, model: str = "",
                   cache_dir: str = "") -> tuple:
    """Готовый вход для инструмента агента: (True, текст) либо (False, ошибка)."""
    root = os.path.abspath(root) if root else ""
    key = (root, ollama.normalize_host(host), str(model or DEFAULT_MODEL))
    index = _INDEXES.get(key)
    if index is None:
        index = ProjectIndex(root, host=host, model=model, cache_dir=cache_dir)
        _INDEXES[key] = index
    if rebuild:
        index.items = []
        index.vectors = []
        index.note = ""
        ok, text = index.build(force=True)
        if not ok:
            return False, str(text)
    return index.search(query, limit=limit)


def describe_index(root: str, host: str = "", model: str = "") -> str:
    """Состояние индекса без обращения к сети (для журнала/диагностики)."""
    root = os.path.abspath(root) if root else ""
    index = _INDEXES.get((root, ollama.normalize_host(host),
                          str(model or DEFAULT_MODEL)))
    if index is None:
        return "Индекс ещё не строился в этом запуске."
    return index._describe()


def clear_cache(cache_dir: str = "") -> int:
    """Удалить файлы индексов с диска. Возвращает, сколько удалили."""
    folder = cache_dir or _cache_dir()
    removed = 0
    try:
        for name in os.listdir(folder):
            if not name.endswith(".json"):
                continue
            try:
                os.remove(os.path.join(folder, name))
                removed += 1
            except OSError:
                continue
    except OSError:
        return 0
    _INDEXES.clear()
    return removed
