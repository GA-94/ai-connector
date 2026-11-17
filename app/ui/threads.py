"""Учёт фоновых QThread: чтобы программа не падала при закрытии.

Причина вылетов без traceback: если объект QThread уничтожается, пока поток
ещё работает, Qt вызывает qFatal("QThread: Destroyed while thread is still
running") -> abort(). В pythonw/на Linux без терминала это выглядит как
мгновенный вылет и пустая папка краш-репортов, потому что Python-исключения
тут вообще нет.

Правило: перед выходом все известные потоки останавливаем и ждём. Если поток
не успел завершиться (висит на сетевом запросе), мы НЕ даём Qt его уничтожить:
держим ссылку и выходим через os._exit, помечая выход нормальным.

Модуль намеренно не зависит от остального приложения, кроме app.crash.
"""

import os
import sys

from app.qt_compat.QtCore import QThread

_live = {}       # id(поток) -> поток (сильная ссылка, чтобы не удалился раньше времени)
_survivors = []  # потоки, которые не удалось остановить: живут до конца процесса


def _is_running(thread) -> bool:
    try:
        return bool(thread.isRunning())
    except RuntimeError:
        # Обёртка C++ уже удалена — значит и потока нет.
        return False
    except Exception:  # noqa: BLE001
        return False


def track(thread):
    """Взять поток на учёт (обязательно для любого QThread в программе)."""
    if thread is None:
        return thread
    try:
        _live[id(thread)] = thread
        thread.finished.connect(lambda: untrack(thread))
    except Exception:  # noqa: BLE001
        pass
    return thread


def untrack(thread) -> None:
    try:
        _live.pop(id(thread), None)
    except Exception:  # noqa: BLE001
        pass


def is_running(thread) -> bool:
    """Публичная проверка «поток ещё работает» (для интерфейса)."""
    return _is_running(thread)


def live_threads() -> list:
    """Все учтённые потоки, которые ещё работают."""
    result = []
    for thread in list(_live.values()):
        if _is_running(thread):
            result.append(thread)
        else:
            untrack(thread)
    return result


def _cancel(thread) -> bool:
    """Попросить поток остановиться тем способом, который у него есть."""
    for name in ("cancel", "stop", "abort"):
        method = getattr(thread, name, None)
        if callable(method):
            try:
                method()
                return True
            except Exception:  # noqa: BLE001
                pass
    try:
        thread.requestInterruption()
        return True
    except Exception:  # noqa: BLE001
        return False


def stop_threads(threads, timeout_ms: int = 3000) -> list:
    """Остановить список потоков. -> те, что не успели завершиться."""
    threads = [item for item in threads if item is not None and _is_running(item)]
    if not threads:
        return []
    for thread in threads:
        _cancel(thread)
    per_thread = max(200, int(timeout_ms / max(1, len(threads))))
    survivors = []
    for thread in threads:
        try:
            if not thread.wait(per_thread):
                survivors.append(thread)
        except Exception:  # noqa: BLE001
            survivors.append(thread)
    for thread in survivors:
        # Держим ссылку: иначе Python удалит объект, и Qt сделает abort().
        if thread not in _survivors:
            _survivors.append(thread)
        # Отвязываем от родителя: иначе при уничтожении диалога Qt удалит
        # дочерний поток вместе с ним, хотя он ещё работает -> abort().
        try:
            thread.setParent(None)
        except Exception:  # noqa: BLE001
            pass
        untrack(thread)
    return survivors


def stop_all(timeout_ms: int = 6000) -> list:
    """Остановить все учтённые потоки. -> те, что остались живыми."""
    return stop_threads(live_threads(), timeout_ms)


def stop_for(widget, timeout_ms: int = 3000) -> list:
    """Остановить QThread-детей виджета (диалоги со своими потоками)."""
    if widget is None:
        return []
    try:
        children = widget.findChildren(QThread)
    except Exception:  # noqa: BLE001
        children = []
    return stop_threads(children, timeout_ms)


def survivors() -> list:
    return list(_survivors)


def hard_exit(code: int = 0) -> None:
    """Выйти, не давая Qt уничтожить работающие потоки.

    Выход помечается нормальным, чтобы сторож краш-репортов не принял его
    за падение.
    """
    try:
        from app import crash
        crash.mark_clean()
    except Exception:  # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:  # noqa: BLE001
            pass
    os._exit(int(code))
