import sqlite3

from app.config.chat_store import ChatStore


def _session(store, roots=("/proj/a", "/proj/b")):
    roots = list(roots)
    return store.create_session(
        "Test", roots[0] if roots else "", "", "master", "worker", False, 15, roots)


def test_create_and_get(tmp_path):
    store = ChatStore(str(tmp_path / "test.db"))
    sid = _session(store)
    store.add_message(sid, "user", "hi")
    store.add_message(sid, "assistant", "hello")
    msgs = store.get_messages(sid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["content"] == "hello"


def test_multiple_roots_roundtrip(tmp_path):
    store = ChatStore(str(tmp_path / "test.db"))
    sid = _session(store, ["/proj/a", "/proj/b", "/proj/c"])
    session = store.get_session(sid)
    assert session["project_roots"] == ["/proj/a", "/proj/b", "/proj/c"]
    assert session["project_root"] == "/proj/a"

    store.update_session_settings(sid, "/proj/b", "", "master", "worker", True, 20,
                                  ["/proj/b", "/proj/z"])
    session = store.get_session(sid)
    assert session["project_roots"] == ["/proj/b", "/proj/z"]
    assert session["max_iterations"] == 20
    assert session["use_two"] is True


def test_old_row_without_roots_column(tmp_path):
    """A database created by 0.2.0 has no project_roots column at all."""
    db = str(tmp_path / "old.db")
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                project_root TEXT DEFAULT '',
                instructions TEXT DEFAULT '',
                master_model TEXT DEFAULT '',
                worker_model TEXT DEFAULT '',
                use_two INTEGER DEFAULT 0,
                max_iterations INTEGER DEFAULT 15,
                succeeded_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO sessions (title, project_root) VALUES ('Old', '/legacy/proj')")

    store = ChatStore(db)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert "project_roots" in columns

    session = store.get_session(1)
    assert session["project_roots"] == ["/legacy/proj"]


def test_delete_session_cascades(tmp_path):
    store = ChatStore(str(tmp_path / "test.db"))
    sid = _session(store)
    store.add_message(sid, "user", "hi")
    store.delete_session(sid)
    assert store.get_messages(sid) == []
    assert store.get_session(sid) is None
