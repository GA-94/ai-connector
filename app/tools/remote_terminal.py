"""Удалённый терминал по SSH для конкретного чата.

Сессия живёт в памяти процесса и привязана к паре (чат, подключение), поэтому
два чата работают независимо: у каждого свои сессии, свой cwd и свои команды.
По умолчанию используется интерактивный shell — тогда `cd`, экспорт переменных
и прочее сохраняются между командами, как в настоящем терминале.
"""

import re
import threading
import time

from app.tools.remote_common import RemoteError, ssh_client

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[=>]")

# (session_id, conn_id) -> SshSession
TERMINALS = {}
_LOCK = threading.Lock()
_MARK = "AICONNSEP"


def clean_output(text: str) -> str:
    """Убрать escape-последовательности, чтобы ответ агента читался."""
    text = ANSI_RE.sub("", str(text or ""))
    return text.replace("\r\n", "\n").replace("\r", "")


class SshSession:
    """Одна SSH-сессия: либо интерактивный shell, либо разовые exec-команды."""

    def __init__(self, profile: dict, credentials: dict, timeout: float = 20.0,
                 interactive: bool = True, log=None):
        self.profile = dict(profile or {})
        self.credentials = dict(credentials or {})
        self.timeout = float(timeout or 20.0)
        self.interactive = bool(interactive)
        self.log = log or (lambda msg: None)
        self.client = None
        self.chan = None
        self.counter = 0
        self.buffer = ""
        # Одна сессия может использоваться и окном терминала, и агентом —
        # команды не должны перемешиваться в одном канале.
        self._lock = threading.RLock()

    # ------------------------------------------------------------ соединение
    def open(self):
        self.client = ssh_client(self.profile, self.credentials, self.timeout)
        transport = self.client.get_transport()
        if transport is not None:
            transport.set_keepalive(30)
        if self.interactive:
            self.chan = self.client.invoke_shell(term="xterm", width=140, height=48)
            self.chan.settimeout(1.0)
            # приветствие оболочки читаем сразу: иначе оно попадёт в первую команду
            self._collect(deadline=time.time() + min(3.0, self.timeout))
        return self

    def close(self):
        for obj in (self.chan, self.client):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        self.chan = None
        self.client = None

    @property
    def alive(self) -> bool:
        if self.client is None:
            return False
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            return False
        if self.chan is not None and self.chan.closed:
            return False
        return True

    def describe(self) -> str:
        host = str(self.profile.get("host") or "?")
        port = int(self.profile.get("port") or 22)
        user = str(self.profile.get("username") or "?").strip()
        return f"{user}@{host}:{port}"

    # --------------------------------------------------------------- команды
    def run(self, command: str, timeout: float = 60.0) -> dict:
        command = str(command or "").strip()
        if not command:
            raise RemoteError("пустая команда")
        with self._lock:
            if not self.alive:
                raise RemoteError(
                    "соединение закрыто — открой терминал заново (remote_terminal_open)")
            if self.chan is None:
                return self._run_exec(command, timeout)
            return self._run_shell(command, timeout)

    def interrupt(self) -> bool:
        """Послать Ctrl+C в интерактивный shell: прервать текущую команду."""
        if self.chan is None or self.chan.closed:
            return False
        try:
            self.chan.send(b"\x03")
            return True
        except Exception:  # noqa: BLE001 - канал мог закрыться
            return False

    def _run_exec(self, command: str, timeout: float) -> dict:
        try:
            _stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"команда не выполнилась: {exc}") from exc
        return {"stdout": clean_output(out), "stderr": clean_output(err),
                "exit_code": int(code)}

    def _run_shell(self, command: str, timeout: float) -> dict:
        self.counter += 1
        marker = f"{_MARK}{self.counter}{int(time.time()) % 100000}"
        payload = (command + "\n"
                   + 'printf "\\n' + marker + ':%s\\n" "$?"\n')
        self.buffer = ""
        try:
            self.chan.send(payload)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось отправить команду: {exc}") from exc

        deadline = time.time() + max(5.0, float(timeout or 60.0))
        text = ""
        while time.time() < deadline:
            text += self._collect(deadline=min(deadline, time.time() + 1.0))
            match = re.search(re.escape(marker) + r":(-?\d+)", text)
            if match:
                body = text[:match.start()]
                body = body.rstrip("\r\n")
                return {"stdout": clean_output(body), "stderr": "",
                        "exit_code": int(match.group(1))}
            if self.chan is not None and self.chan.closed:
                break
            if self.chan is not None and self.chan.exit_status_ready():
                text += self._collect(deadline=time.time() + 0.5)
                break
        return {"stdout": clean_output(text), "stderr": "", "exit_code": -1,
                "note": "команда не завершилась за отведённое время; вывод может быть неполным"}

    def _collect(self, deadline: float) -> str:
        chunks = []
        while time.time() < deadline:
            if self.chan is None or self.chan.closed:
                break
            try:
                if not self.chan.recv_ready():
                    time.sleep(0.05)
                    continue
                data = self.chan.recv(65536)
            except Exception:  # noqa: BLE001 - таймаут канала
                break
            if not data:
                break
            chunks.append(data.decode("utf-8", "replace"))
            if len(chunks) > 400:
                break
        return "".join(chunks)


# ------------------------------------------------------------------ реестр

def _key(session_id, conn_id):
    return (int(session_id or 0), int(conn_id or 0))


def open_session(session_id, conn_id, profile: dict, credentials: dict,
                 timeout: float = 20.0, interactive: bool = True, log=None) -> SshSession:
    """Открыть (или вернуть уже открытую) SSH-сессию этого чата."""
    key = _key(session_id, conn_id)
    with _LOCK:
        session = TERMINALS.get(key)
        if session is not None and session.alive:
            return session
        if session is not None:
            session.close()
        session = SshSession(profile, credentials, timeout=timeout,
                             interactive=interactive, log=log)
        session.open()
        TERMINALS[key] = session
        return session


def get_session(session_id, conn_id):
    with _LOCK:
        session = TERMINALS.get(_key(session_id, conn_id))
    if session is not None and session.alive:
        return session
    return None


def close_session(session_id, conn_id) -> bool:
    key = _key(session_id, conn_id)
    with _LOCK:
        session = TERMINALS.pop(key, None)
    if session is None:
        return False
    session.close()
    return True


def interrupt_session(session_id, conn_id) -> bool:
    """Прервать текущую команду (Ctrl+C) в сессии этого чата."""
    session = get_session(session_id, conn_id)
    if session is None:
        return False
    return session.interrupt()


def close_chat(session_id) -> int:
    """Закрыть все терминалы чата (например, при удалении чата)."""
    with _LOCK:
        keys = [key for key in TERMINALS if key[0] == int(session_id or 0)]
        closed = [TERMINALS.pop(key) for key in keys]
    for session in closed:
        session.close()
    return len(closed)
