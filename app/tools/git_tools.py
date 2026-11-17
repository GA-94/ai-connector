"""Git и GitHub: агент может закоммитить работу и выложить проект на GitHub.

Токен НЕ передаётся в аргументах инструмента (иначе он попадёт в чат и в
историю). Он берётся из настроек, а из логов вырезается через redact_text.
"""

import json
import os
import shutil
import subprocess

from app.runtime_env import silent_run
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"

GIT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Показывает состояние git-репозитория проекта: ветку, изменённые и новые файлы. Работает и без GitHub.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Папка проекта (по умолчанию корень проекта)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Инициализирует git (если нужно), добавляет все изменения и делает коммит. Ничего не отправляет в интернет.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "message": {"type": "string", "description": "Сообщение коммита"},
                    "author": {"type": "string", "description": "Имя автора (необязательно)"}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_publish",
            "description": "Публикует код проекта в новый репозиторий GitHub. Требует токен GitHub в настройках чата и подтверждение пользователя в окне программы.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "repo_name": {"type": "string", "description": "Имя репозитория (латиницей, без пробелов)"},
                    "author": {"type": "string", "description": "Автор проекта"},
                    "description": {"type": "string", "description": "Краткое описание репозитория"},
                    "private": {"type": "boolean", "description": "Сделать приватным"}
                },
                "required": ["repo_name"]
            }
        }
    },
]


def _git() -> str:
    return shutil.which("git") or ""


def _run(cmd: list, cwd: str, timeout: float = 120.0) -> tuple:
    """(код возврата, объединённый вывод)."""
    try:
        done = silent_run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout,
                              text=True, encoding="utf-8", errors="replace")
        return done.returncode, (done.stdout or "").strip()
    except FileNotFoundError:
        return 127, f"Команда не найдена: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "Превышено время выполнения: " + " ".join(cmd[:2])
    except Exception as error:  # noqa: BLE001
        return 1, f"{type(error).__name__}: {error}"


def is_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git"))


def status(path: str) -> str:
    if not _git():
        return ("git не установлен. Arch: sudo pacman -S git; Debian/Ubuntu: "
                "sudo apt install git; Windows: https://git-scm.com")
    if not os.path.isdir(path):
        return f"Папка не найдена: {path}"
    if not is_repo(path):
        return "Это ещё не git-репозиторий. Вызови git_commit — он сделает git init."
    code, branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], path)
    _code, changes = _run(["git", "status", "--short"], path)
    _code, remote = _run(["git", "remote", "-v"], path)
    lines = [f"Ветка: {branch if code == 0 else '(нет коммитов)'}"]
    lines.append("Изменения:\n" + (changes or "(нет)"))
    if remote:
        lines.append("Remote:\n" + remote)
    return "\n".join(lines)


def commit_all(path: str, message: str, author: str = "", email: str = "") -> tuple:
    """(ok, текст). Делает git init при необходимости."""
    git = _git()
    if not git:
        return False, ("git не установлен. Arch: sudo pacman -S git; "
                       "Debian/Ubuntu: sudo apt install git; Windows: git-scm.com")
    if not os.path.isdir(path):
        return False, f"Папка не найдена: {path}"

    steps = []
    if not is_repo(path):
        code, out = _run([git, "init"], path)
        steps.append(f"git init: {out or code}")

    name = (author or "").strip() or "AI-Connector"
    mail = (email or "").strip() or "ai-connector@localhost"
    _run([git, "config", "user.name", name], path)
    _run([git, "config", "user.email", mail], path)

    code, out = _run([git, "add", "-A"], path)
    if code != 0:
        return False, "git add не удался: " + out
    code, out = _run([git, "commit", "-m", message or "AI-Connector: изменения"], path)
    steps.append("git commit: " + (out.splitlines()[0] if out else str(code)))
    if code != 0 and "nothing to commit" not in out.lower():
        return False, "\n".join(steps)
    return True, "\n".join(steps)


def _api(method: str, url: str, token: str, payload=None, timeout: float = 30.0):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(request, timeout=timeout)


def check_token(token: str) -> tuple:
    """(True, login) если токен рабочий."""
    token = str(token or "").strip()
    if not token:
        return False, "Токен не задан"
    try:
        with _api("GET", GITHUB_API + "/user", token) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        if error.code == 401:
            return False, "GitHub отклонил токен (401). Проверь срок и права repo."
        return False, f"GitHub ответил HTTP {error.code}"
    except Exception as error:  # noqa: BLE001
        return False, f"Нет связи с GitHub: {type(error).__name__}: {error}"
    login = str(payload.get("login") or "")
    return (True, login) if login else (False, "GitHub не вернул логин")


def publish(path: str, repo_name: str, token: str, author: str = "",
            description: str = "", private: bool = False) -> tuple:
    """Создать репозиторий на GitHub и запушить проект. (ok, текст/URL)."""
    token = str(token or "").strip()
    if not token:
        return False, ("Нет токена GitHub. Открой настройки чата → «GitHub: войти» "
                       "и вставь personal access token (права repo).")
    repo_name = str(repo_name or "").strip()
    if not repo_name:
        return False, "Не указано имя репозитория"
    if not os.path.isdir(path):
        return False, f"Папка не найдена: {path}"
    git = _git()
    if not git:
        return False, "git не установлен (Arch: pacman -S git, Debian: apt install git)"

    ok, login = check_token(token)
    if not ok:
        return False, login

    payload = {"name": repo_name, "private": bool(private), "auto_init": False}
    if description:
        payload["description"] = description
    try:
        with _api("POST", GITHUB_API + "/user/repos", token, payload) as response:
            created = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = json.loads(error.read().decode("utf-8", "replace")).get("message", "")
        except Exception:  # noqa: BLE001
            pass
        if error.code == 422:
            return False, (f"Репозиторий '{repo_name}' уже существует или имя занято. "
                           f"{detail}")
        return False, f"GitHub отказал (HTTP {error.code}): {detail}"
    except Exception as error:  # noqa: BLE001
        return False, f"Не удалось создать репозиторий: {type(error).__name__}: {error}"

    url = str(created.get("html_url") or "")
    clone = str(created.get("clone_url") or "")
    if not clone:
        return False, "GitHub не вернул адрес репозитория"

    commit_ok, commit_text = commit_all(path, "AI-Connector: первая публикация",
                                        author or login)
    if not commit_ok:
        return False, commit_text

    _run([git, "branch", "-M", "main"], path)
    _run([git, "remote", "remove", "origin"], path)
    # токен живёт только на время push: в .git/config он не остаётся
    auth_url = clone.replace("https://", f"https://x-access-token:{token}@")
    code, out = _run([git, "remote", "add", "origin", auth_url], path)
    if code != 0:
        return False, "git remote add: " + out.replace(token, "***")
    code, out = _run([git, "push", "-u", "origin", "main"], path, timeout=300.0)
    _run([git, "remote", "set-url", "origin", clone], path)
    if code != 0:
        return False, "git push не удался:\n" + out.replace(token, "***")
    return True, f"Готово: {url}"
