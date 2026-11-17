"""Multi-project workspace + external asset repository.

Earlier versions of AI-Connector restricted the agent to one project root. A
Workspace keeps an ordered list of roots (the first one is the primary root,
where new files land by default) and an optional repository folder holding
reusable assets: templates, MDK archives, libraries, single files.

Path rules used by every tool:
  * "src/main/java/Foo.java"  - relative; searched in the primary root first,
                                then in the other roots (must be unambiguous).
  * "lib:src/Foo.java"        - explicit project, `lib` is the folder name of
                                the root (see describe()).
  * "repo:mdk/forge.zip"      - the repository (read only).
  * "C:/abs/path/Foo.java"    - absolute; allowed only inside a known root.
"""
import os
import re
from pathlib import Path

from .safety import find_root, is_blocked_extension, is_inside, normalize_roots

_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class WorkspaceError(ValueError):
    """Raised with a human (and agent) readable explanation."""


def _dedupe_names(paths):
    names, used = {}, set()
    for path in paths:
        base = path.name or str(path)
        name, index = base, 2
        while name.lower() in used:
            name = f"{base}{index}"
            index += 1
        used.add(name.lower())
        names[name] = path
    return names


class Workspace:
    def __init__(self, roots=None, primary="", repository="", allow_network=True):
        raw = []
        if primary:
            raw.append(primary)
        if roots is None:
            roots = []
        elif isinstance(roots, (str, os.PathLike)):
            roots = [roots]
        raw.extend(roots)

        self.roots = normalize_roots(raw)
        self.names = _dedupe_names(self.roots)
        self.allow_network = bool(allow_network)

        self.repository = None
        if repository:
            try:
                self.repository = Path(repository).expanduser().resolve()
            except (OSError, ValueError):
                self.repository = Path(repository)

    # ------------------------------------------------------------------ basics

    @property
    def primary(self):
        return self.roots[0] if self.roots else None

    def describe_names(self) -> str:
        return ", ".join(self.names) or "(нет проектов)"

    def name_of(self, path) -> str:
        root = find_root(path, self.roots)
        if root is None:
            return "?"
        for name, candidate in self.names.items():
            if candidate == root:
                return name
        return root.name

    def _known_name(self, value):
        if not value:
            return None
        low = str(value).strip().lower()
        for name in self.names:
            if name.lower() == low:
                return name
        return None

    def split_prefix(self, path: str):
        """Return (root_name | 'repo' | None, remainder)."""
        if not path:
            return None, path
        if _DRIVE_RE.match(path) or path.startswith(("/", "\\")):
            return None, path
        prefix, sep, rest = path.partition(":")
        if not sep or not rest or len(prefix) > 40 or "/" in prefix or "\\" in prefix:
            return None, path
        name = self._known_name(prefix)
        if name:
            return name, rest.lstrip("/\\")
        if prefix.strip().lower() == "repo" and self.repository:
            return "repo", rest.lstrip("/\\")
        return None, path

    # --------------------------------------------------------------- resolving

    def _checked(self, path: Path, must_exist=False, for_write=False) -> Path:
        try:
            target = Path(path).expanduser().resolve()
        except (OSError, ValueError):
            raise WorkspaceError(f"Некорректный путь: {path}")

        if find_root(target, self.roots) is None:
            raise WorkspaceError(
                f"Access denied: {target} находится вне известных проектов "
                f"({self.describe_names()}).")
        if for_write and is_blocked_extension(target.name):
            raise WorkspaceError(f"Extension blocked: {target}")
        if must_exist and not target.is_file():
            raise WorkspaceError(f"Файл не найден: {target}")
        return target

    def resolve(self, path, project=None, must_exist=False, for_write=False,
                allow_repo=False) -> Path:
        raw = (path or ".").strip() or "."
        root_name, rel = self.split_prefix(raw)

        if project and root_name is None:
            root_name = self._known_name(project)
            if root_name is None:
                raise WorkspaceError(
                    f"Неизвестный проект {project!r}. Доступные: {self.describe_names()}")

        if root_name == "repo":
            if not self.repository:
                raise WorkspaceError("Репозиторий не настроен (Настройки -> Репозиторий).")
            if for_write:
                raise WorkspaceError(
                    "В репозиторий писать нельзя: он только для чтения. "
                    "Копируй нужное в проект через repo_import_file / repo_extract.")
            if not allow_repo:
                raise WorkspaceError(
                    "Ссылка repo: разрешена только для чтения файлов. "
                    "Для операций используй инструменты repo_list / repo_import_file / repo_extract.")
            target = (self.repository / rel).resolve() if rel else self.repository
            if not is_inside(target, self.repository):
                raise WorkspaceError(f"Access denied: {target} вне репозитория.")
            if must_exist and not target.exists():
                raise WorkspaceError(f"В репозитории нет: {rel}")
            return target

        if root_name:
            base = self.names[root_name]
            return self._checked(base / rel if rel else base,
                                 must_exist=must_exist, for_write=for_write)

        if _DRIVE_RE.match(raw) or Path(raw).is_absolute():
            return self._checked(raw, must_exist=must_exist, for_write=for_write)

        if not self.roots:
            raise WorkspaceError(
                "Не задан ни один корень проекта. Открой Настройки чата и добавь папку проекта.")

        head = self.roots[0] / raw
        existing = []
        for root in self.roots:
            candidate = root / raw
            if candidate.exists():
                existing.append(candidate)

        if len(existing) == 1:
            return self._checked(existing[0], must_exist=must_exist, for_write=for_write)
        if len(existing) > 1:
            head_res = head.resolve()
            for candidate in existing:
                if candidate.resolve() == head_res:
                    return self._checked(candidate, must_exist=must_exist, for_write=for_write)
            listing = "\n".join(f"  {self.name_of(c)}: {c}" for c in existing)
            raise WorkspaceError(
                f"Путь {raw!r} существует в нескольких проектах:\n{listing}\n"
                f"Уточни project=<имя> или используй префикс 'имя:{raw}'.")

        if for_write or not must_exist:
            return self._checked(head, must_exist=False, for_write=for_write)
        raise WorkspaceError(
            f"Файл не найден ни в одном проекте: {raw!r}. Проекты: {self.describe_names()}")

    def project_dir(self, project=None) -> Path:
        if project:
            name = self._known_name(project)
            if name is None:
                raise WorkspaceError(
                    f"Неизвестный проект {project!r}. Доступные: {self.describe_names()}")
            return self.names[name]
        if not self.primary:
            raise WorkspaceError("Не задан корень проекта.")
        return self.primary

    # ------------------------------------------------------------- prompt help

    def describe(self) -> str:
        if not self.roots and not self.repository:
            return ""
        lines = ["РАБОЧЕЕ ПРОСТРАНСТВО (несколько проектов + репозиторий):"]
        if self.roots:
            first = True
            for name, path in self.names.items():
                marks = []
                if first:
                    marks.append("основной")
                    first = False
                if not path.is_dir():
                    marks.append("ПАПКА НЕ СУЩЕСТВУЕТ")
                suffix = f" [{', '.join(marks)}]" if marks else ""
                lines.append(f"  - {name}: {path}{suffix}")
            lines.append(
                '  Относительные пути ищутся в основном проекте, затем в остальных. '
                'Чтобы явно указать проект: project="имя" или путь "имя:src/файл.py".')
        else:
            lines.append("  (проекты не заданы)")

        if self.repository:
            lines.append(f"  - repo (только чтение): {self.repository}")
            lines.append(
                '  Репозиторий - склад готовых материалов (шаблоны, MDK, архивы). '
                'Смотри repo_list/repo_search, читай repo_read_file, копируй в проект '
                'через repo_import_file, распаковывай архивы через repo_extract '
                '(dry_run=true сначала, чтобы увидеть план).')
        else:
            lines.append("  - repo: не настроен (в настройках программы)")
        return "\n".join(lines)
