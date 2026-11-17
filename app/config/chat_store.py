import json
import sqlite3
from pathlib import Path

# Ограничение длинны названия чата: длинные заголовки ломали вёрстку колонки.
TITLE_LIMIT = 48


def clamp_title(title: str, limit: int = TITLE_LIMIT) -> str:
    """Обрезать название до лимита (по словам, если получается)."""
    text = " ".join(str(title or "").split())
    if not text:
        return "Новый чат"
    if len(text) <= limit:
        return text
    # оставляем место под многоточие, чтобы итог был ровно в лимите
    room = max(1, limit - 1)
    cut = text[:room].rstrip()
    # не рвём слово посередине, если рядом есть пробел
    space = cut.rfind(" ")
    if space > room * 0.6:
        cut = cut[:space].rstrip()
    return cut.rstrip(" .,:;-—") + "…"


def _dump_roots(roots) -> str:
    """Serialize the list of project roots for the DB."""
    if roots is None:
        return ""
    if isinstance(roots, str):
        roots = [roots]
    clean = [str(r).strip() for r in roots if r and str(r).strip()]
    return json.dumps(clean, ensure_ascii=False)


def _load_roots(raw, fallback: str = "") -> list:
    """Read the serialized roots; older rows only have project_root."""
    roots = []
    if raw:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            value = None
        if isinstance(value, list):
            roots = [str(v) for v in value if v]
        elif isinstance(value, str) and value.strip():
            roots = [value]
    if not roots and fallback:
        roots = [fallback]
    seen, out = set(), []
    for item in roots:
        key = item.lower() if isinstance(item, str) else item
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


class ChatStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    project_root TEXT DEFAULT '',
                    project_roots TEXT DEFAULT '',
                    instructions TEXT DEFAULT '',
                    master_model TEXT DEFAULT 'deepseek/deepseek-v4-pro-0813',
                    worker_model TEXT DEFAULT 'deepseek/deepseek-v4-pro-0813',
                    use_two INTEGER DEFAULT 0,
                    max_iterations INTEGER DEFAULT 15,
                    show_thinking INTEGER DEFAULT 1,
                    num_ctx INTEGER DEFAULT 0,
                    ollama_mode TEXT DEFAULT '',
                    ai_tools INTEGER DEFAULT 0,
                    fast_mode INTEGER DEFAULT 0,
                    ctx_used INTEGER DEFAULT 0,
                    ctx_limit INTEGER DEFAULT 0,
                    tokens_total INTEGER DEFAULT 0,
                    archived INTEGER DEFAULT 0,

                    cost_rub REAL DEFAULT 0,
                    provider TEXT DEFAULT '',
                    balance_url TEXT DEFAULT '',
                    succeeded_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")

            # --- миграции старых баз ---------------------------------------
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            migrations = {
                "project_roots": "ALTER TABLE sessions ADD COLUMN project_roots TEXT DEFAULT ''",
                "show_thinking": "ALTER TABLE sessions ADD COLUMN show_thinking INTEGER DEFAULT 1",
                # длина контекста локальной модели (у облачных не используется)
                "num_ctx": "ALTER TABLE sessions ADD COLUMN num_ctx INTEGER DEFAULT 0",
                # размещение локальной модели: auto / vram / ram (своё для чата)
                "ollama_mode": "ALTER TABLE sessions ADD COLUMN ollama_mode TEXT DEFAULT ''",
                # разрешение ИИ менять набор инструментов (своё для чата)
                "ai_tools": "ALTER TABLE sessions ADD COLUMN ai_tools INTEGER DEFAULT 0",
                # быстрый режим: без рассуждений модели (своё для чата)
                "fast_mode": "ALTER TABLE sessions ADD COLUMN fast_mode INTEGER DEFAULT 0",
                # счётчик контекста: занято/лимит последнего запроса и всего токенов
                "ctx_used": "ALTER TABLE sessions ADD COLUMN ctx_used INTEGER DEFAULT 0",
                "ctx_limit": "ALTER TABLE sessions ADD COLUMN ctx_limit INTEGER DEFAULT 0",
                "tokens_total": "ALTER TABLE sessions ADD COLUMN tokens_total INTEGER DEFAULT 0",

                "archived": "ALTER TABLE sessions ADD COLUMN archived INTEGER DEFAULT 0",
                "cost_rub": "ALTER TABLE sessions ADD COLUMN cost_rub REAL DEFAULT 0",
                "provider": "ALTER TABLE sessions ADD COLUMN provider TEXT DEFAULT ''",
                "balance_url": "ALTER TABLE sessions ADD COLUMN balance_url TEXT DEFAULT ''",
            }
            for name, sql in migrations.items():
                if name not in columns:
                    conn.execute(sql)

    # ------------------------------------------------------------- сессии

    def create_session(self, title: str, project_root: str, instructions: str,
                       master_model: str, worker_model: str, use_two: bool,
                       max_iterations: int = 15, project_roots=None,
                       show_thinking: bool = True, provider: str = "",
                       balance_url: str = "", num_ctx: int = 0,
                       ollama_mode: str = "", ai_tools: bool = False,
                       fast_mode: bool = False) -> int:
        roots = _load_roots(_dump_roots(project_roots), fallback=project_root)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO sessions (title, project_root, project_roots, instructions, "
                "master_model, worker_model, use_two, max_iterations, show_thinking, "
                "provider, balance_url, num_ctx, ollama_mode, ai_tools, fast_mode) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (clamp_title(title), project_root, _dump_roots(roots), instructions,
                 master_model, worker_model, int(use_two), max_iterations,
                 int(bool(show_thinking)), provider or "", balance_url or "",
                 int(num_ctx or 0), str(ollama_mode or ""), int(bool(ai_tools)),
                 int(bool(fast_mode)))
            )
            return cur.lastrowid


    def get_session(self, session_id: int) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                res = dict(row)
                res["use_two"] = bool(res["use_two"])
                res["show_thinking"] = bool(res.get("show_thinking", 1))
                res["ai_tools"] = bool(res.get("ai_tools", 0))
                res["fast_mode"] = bool(res.get("fast_mode", 0))
                res["archived"] = bool(res.get("archived", 0))
                res["cost_rub"] = float(res.get("cost_rub") or 0.0)
                # Счётчик токенов: сохранён, чтобы не сбрасывался при
                # перезапуске программы и переключении чатов.
                res["ctx_used"] = int(res.get("ctx_used") or 0)
                res["ctx_limit"] = int(res.get("ctx_limit") or 0)
                res["tokens_total"] = int(res.get("tokens_total") or 0)
                res["project_roots"] = _load_roots(res.get("project_roots"),
                                                   fallback=res.get("project_root") or "")
                return res
            return None

    def update_session_settings(self, session_id: int, project_root: str, instructions: str,
                               master_model: str, worker_model: str, use_two: bool,
                               max_iterations: int = 15, project_roots=None,
                               show_thinking: bool = True, provider: str = None,
                               balance_url: str = None, num_ctx: int = None,
                               ollama_mode: str = None, ai_tools: bool = None,
                               fast_mode: bool = None):

        roots = _load_roots(_dump_roots(project_roots), fallback=project_root)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET project_root = ?, project_roots = ?, instructions = ?, "
                "master_model = ?, worker_model = ?, use_two = ?, max_iterations = ?, "
                "show_thinking = ? WHERE id = ?",
                (project_root, _dump_roots(roots), instructions, master_model,
                 worker_model, int(use_two), max_iterations, int(bool(show_thinking)),
                 session_id)
            )
            if provider is not None:
                conn.execute("UPDATE sessions SET provider = ? WHERE id = ?",
                             (provider or "", session_id))
            if balance_url is not None:
                conn.execute("UPDATE sessions SET balance_url = ? WHERE id = ?",
                             (balance_url or "", session_id))
            if num_ctx is not None:
                conn.execute("UPDATE sessions SET num_ctx = ? WHERE id = ?",
                             (int(num_ctx or 0), session_id))
            if ollama_mode is not None:
                conn.execute("UPDATE sessions SET ollama_mode = ? WHERE id = ?",
                             (str(ollama_mode or ""), session_id))
            if ai_tools is not None:
                conn.execute("UPDATE sessions SET ai_tools = ? WHERE id = ?",
                             (int(bool(ai_tools)), session_id))
            if fast_mode is not None:
                conn.execute("UPDATE sessions SET fast_mode = ? WHERE id = ?",
                             (int(bool(fast_mode)), session_id))


    def update_session_title(self, session_id: int, title: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET title = ? WHERE id = ?",
                         (clamp_title(title), session_id))

    def delete_session(self, session_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # -------------------------------------------------------------- архив

    def set_archived(self, session_id: int, value: bool = True):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET archived = ? WHERE id = ?",
                         (int(bool(value)), session_id))

    def set_show_thinking(self, session_id: int, value: bool):
        """Тумблер «показывать, что ИИ думает» для одного чата."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET show_thinking = ? WHERE id = ?",
                         (int(bool(value)), session_id))

    def set_fast_mode(self, session_id: int, value: bool):
        """Тумблер «быстрый режим» (без рассуждений модели) для одного чата."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET fast_mode = ? WHERE id = ?",
                         (int(bool(value)), session_id))

    # ------------------------------------------------- счётчик токенов

    def save_token_stats(self, session_id: int, used: int = 0, limit: int = 0,
                         add_tokens: int = 0):
        """Запомнить занятый контекст и прибавить израсходованные токены.

        Хранится в базе, а не только в памяти окна: счётчик не сбрасывается при
        перезапуске программы и у каждого чата свой.
        """
        try:
            used_value = max(0, int(used or 0))
            limit_value = max(0, int(limit or 0))
            extra = max(0, int(add_tokens or 0))
        except (TypeError, ValueError):
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sessions SET ctx_used = ?, ctx_limit = ?, "
                "tokens_total = COALESCE(tokens_total, 0) + ? WHERE id = ?",
                (used_value, limit_value, extra, session_id))

    def get_token_stats(self, session_id: int) -> dict:
        """{'used', 'limit', 'total'} — счётчик токенов этого чата."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT ctx_used, ctx_limit, tokens_total FROM sessions WHERE id = ?",
                (session_id,)).fetchone()
        if not row:
            return {"used": 0, "limit": 0, "total": 0}
        return {"used": int(row[0] or 0), "limit": int(row[1] or 0),
                "total": int(row[2] or 0)}

    def list_archived(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, updated_at, cost_rub FROM sessions "
                "WHERE archived = 1 ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------- деньги

    def add_cost(self, session_id: int, amount: float):
        """Прибавить стоимость хода к счётчику чата (₽)."""
        try:
            value = max(0.0, float(amount or 0))
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET cost_rub = COALESCE(cost_rub, 0) + ? "
                         "WHERE id = ?", (value, session_id))

    def set_cost(self, session_id: int, value: float):
        """Записать суммарную стоимость чата (₽) — пересчёт из окна."""
        try:
            amount = max(0.0, float(value or 0))
        except (TypeError, ValueError):
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET cost_rub = ? WHERE id = ?",
                         (amount, session_id))

    def get_cost(self, session_id: int) -> float:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT cost_rub FROM sessions WHERE id = ?",
                               (session_id,)).fetchone()
        return float(row[0] or 0.0) if row else 0.0

    # ---------------------------------------------------------- сообщения

    def add_message(self, session_id: int, role: str, content: str, tool_calls: str = None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?, ?, ?, ?)",
                (session_id, role, content, tool_calls)
            )
            conn.execute("UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (session_id,))

    def get_messages(self, session_id: int) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content, tool_calls FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_sessions(self) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, updated_at, cost_rub, provider FROM sessions "
                "WHERE COALESCE(archived, 0) = 0 ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
