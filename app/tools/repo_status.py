"""Diagnostics for the asset repository setting.

Instead of a bare "репозиторий не настроен" the program explains exactly what
is missing: the path is empty, the folder does not exist, it is a file, there
is no permission, the folder is empty or it holds no archives.

Used by:
  * the agent tool `repo_status` (so the model sees the real reason);
  * «Глобальные настройки» -> группа «Репозиторий»;
  * «Настройки чата» (label under the project list);
  * the repository panel (app/ui/repository_dialog.py).
"""
import os
from pathlib import Path

ARCHIVE_SUFFIXES = (".zip", ".jar", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
TRASH_DIR = "_trash"
CACHE_DIR = "_cache"
MAX_WALK_FILES = 20_000


def is_archive(name: str) -> bool:
    low = (name or "").lower()
    return any(low.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def human_size(size) -> str:
    value = float(size or 0)
    for unit in ("b", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "b" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class RepoStatus:
    """Result of check_repository(): problems, warnings, hints and counters."""

    def __init__(self, raw: str = ""):
        self.raw = (raw or "").strip()
        self.path = ""
        self.problems = []
        self.warnings = []
        self.hints = []
        self.files = 0
        self.archives = 0
        self.dirs = 0
        self.bytes = 0
        self.writable = False
        self.exists = False

    @property
    def ok(self) -> bool:
        return not self.problems

    def short(self) -> str:
        if not self.raw:
            return "не настроен: папка репозитория не указана"
        if self.problems:
            return "проблемы: " + "; ".join(self.problems)
        text = (f"OK — {human_size(self.bytes)}, файлов {self.files} "
                f"(архивов {self.archives})")
        if self.warnings:
            text += " | " + "; ".join(self.warnings)
        return text

    def as_text(self) -> str:
        out = ["РЕПОЗИТОРИЙ",
               f"  Папка: {self.path or self.raw or '(не задана)'}"]
        if self.problems:
            out.append(f"  Состояние: ЕСТЬ ПРОБЛЕМЫ ({len(self.problems)})")
        else:
            out.append("  Состояние: OK")
        if self.exists:
            out.append(f"  Содержимое: файлов {self.files}, архивов {self.archives}, "
                       f"папок {self.dirs}, объём {human_size(self.bytes)}")
            out.append(f"  Запись: {'да' if self.writable else 'НЕТ (только чтение)'}")
        if self.problems:
            out.append("  ПРОБЛЕМЫ:")
            out.extend(f"    - {item}" for item in self.problems)
        if self.warnings:
            out.append("  ПРЕДУПРЕЖДЕНИЯ:")
            out.extend(f"    - {item}" for item in self.warnings)
        if self.hints:
            out.append("  ЧТО ДЕЛАТЬ:")
            out.extend(f"    - {item}" for item in self.hints)
        return "\n".join(out)


def check_repository(raw_path: str = "") -> RepoStatus:
    """Inspect the repository setting and explain what is wrong with it."""
    status = RepoStatus(raw_path)
    if not status.raw:
        status.problems.append("папка репозитория не указана")
        status.hints.append(
            "Открой «Глобальные настройки» → группа «Репозиторий» → кнопка «...» "
            "и выбери папку (например C:/assets/repo).")
        status.hints.append(
            "Без репозитория repo_list / repo_read_file / repo_import_file / repo_extract "
            "не работают: агенту неоткуда брать шаблоны и архивы.")
        return status

    path = Path(status.raw).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    status.path = str(path)

    if not path.exists():
        status.problems.append(f"папка не существует: {path}")
        status.hints.append("Проверь путь (диск, опечатка) или создай эту папку.")
        return status
    if not path.is_dir():
        status.problems.append(f"это файл, а не папка: {path}")
        status.hints.append("Выбери именно папку — репозиторий это каталог с материалами.")
        return status

    status.exists = True
    status.writable = os.access(str(path), os.W_OK)

    try:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d != TRASH_DIR]
            status.dirs += len(dirnames)
            for name in filenames:
                status.files += 1
                try:
                    status.bytes += (Path(dirpath) / name).stat().st_size
                except OSError:
                    pass
                if is_archive(name):
                    status.archives += 1
            if status.files >= MAX_WALK_FILES:
                status.warnings.append(f"очень много файлов, учтено {status.files}")
                break
    except OSError as error:
        status.problems.append(f"не удалось прочитать папку: {error}")
        return status

    if not status.writable:
        status.warnings.append(
            "нет прав на запись: скачивание и правка файлов из панели не сработают")
    if status.files == 0:
        status.warnings.append("папка пуста — агенту здесь нечего взять")
        status.hints.append(
            "Положи в папку архив MDK/шаблоны или скачай по ссылке кнопкой "
            "«Скачать по ссылке» в панели «Репозиторий».")
    elif status.archives == 0:
        status.warnings.append(
            "архивов (.zip/.jar/.tar.gz) нет — repo_extract будет нечего распаковывать")

    return status
