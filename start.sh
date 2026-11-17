#!/usr/bin/env bash
# Стартер AiConnector7 для Linux/BSD (Arch, Debian, Ubuntu, Fedora, openSUSE).
#
#   ./start.sh              запустить программу
#   bash start.sh           то же, если нет права на запуск файла
#   ./start.sh --check      диагностика окружения без GUI
#   ./start.sh --xcb        принудительно X11 (если на Wayland пустое окно)
#
# Что делает скрипт: находит python3, чистит ^M и обратные слэши в путях,
# проверяет зависимости и запускает start_linux.py.
set -u

cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" || exit 1

PYTHON_BIN="${AI_CONNECTOR_PYTHON:-}"

if [ -z "$PYTHON_BIN" ]; then
    for candidate in python3 python3.12 python3.11 python3.10 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "Python 3 не найден."
    echo "  Debian/Ubuntu: sudo apt install python3 python3-pip python3-venv"
    echo "  Arch/Manjaro:  sudo pacman -S python python-pip"
    echo "  Fedora:        sudo dnf install python3 python3-pip"
    exit 1
fi

if [ -d ".venv" ] && [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
fi

# ^M (CR) в путях ломает Linux: вычищаем из переменных окружения.
if [ -n "${PYTHONPATH:-}" ]; then
    PYTHONPATH=$(printf '%s' "$PYTHONPATH" | tr -d '\r' | tr '\\' '/')
    export PYTHONPATH
fi

# Битый лог Qt на некоторых драйверах - не мешает работе.
export QT_LOGGING_RULES="${QT_LOGGING_RULES:-qt.qpa.xcb.warning=false}"

# Русский текст в консоли.
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

exec "$PYTHON_BIN" "$(pwd)/start_linux.py" "$@"
