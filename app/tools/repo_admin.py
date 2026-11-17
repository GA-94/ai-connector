"""Repository maintenance for the UI panel (the agent itself gets read-only access).

Every destructive action is reversible: deleted entries are moved to
`<repository>/_trash/<timestamp>/`, never unlinked.
"""
import datetime
import re
import shutil
from pathlib import Path

from .repo_status import is_archive, human_size, TRASH_DIR
from .repo_tools import fetch_to_repo, repo_archive_list, repo_extract
from .textio import read_text, write_text
from .workspace import Workspace

MAX_TEXT_BYTES = 800_000
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class RepoAdminError(ValueError):
    """Raised with a message that can be shown to the user as is."""


def repo_root(value: str) -> Path:
    raw = (value or "").strip()
    if not raw:
        raise RepoAdminError(
            "Репозиторий не настроен: укажи папку в «Глобальные настройки» → Репозиторий.")
    root = Path(raw).expanduser()
    try:
        root = root.resolve()
    except OSError:
        pass
    if not root.is_dir():
        raise RepoAdminError(f"Папка репозитория не существует: {root}")
    return root


def _resolve(root: Path, rel: str) -> Path:
    cleaned = (rel or "").replace("\\", "/").strip().lstrip("/")
    target = (root / cleaned).resolve() if cleaned else root
    try:
        target.relative_to(root)
    except ValueError:
        raise RepoAdminError(f"Путь вне репозитория: {rel}")
    return target


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name in (".", ".."):
        raise RepoAdminError("Имя не может быть пустым")
    if _BAD_NAME.search(name):
        raise RepoAdminError('Имя содержит недопустимые символы: < > : " / \\ | ? *')
    return name


# --------------------------------------------------------------------- browse

def list_entries(repo_root_value: str, rel: str = "") -> list:
    root = repo_root(repo_root_value)
    folder = _resolve(root, rel)
    if not folder.is_dir():
        raise RepoAdminError(f"Это не папка: {rel or '/'}")
    try:
        children = sorted(folder.iterdir(),
                          key=lambda item: (not item.is_dir(), item.name.lower()))
    except OSError as error:
        raise RepoAdminError(f"Не удалось прочитать папку: {error}")

    entries = []
    for child in children:
        is_dir = child.is_dir()
        try:
            size = 0 if is_dir else child.stat().st_size
        except OSError:
            size = 0
        entries.append({
            "name": child.name,
            "rel": child.relative_to(root).as_posix(),
            "is_dir": is_dir,
            "is_archive": (not is_dir) and is_archive(child.name),
            "size": size,
        })
    return entries


def read_entry(repo_root_value: str, rel: str) -> str:
    root = repo_root(repo_root_value)
    target = _resolve(root, rel)
    if not target.is_file():
        raise RepoAdminError(f"Файл не найден: {rel}")
    if is_archive(target.name):
        raise RepoAdminError("Это архив — открой его предпросмотр или распакуй в проект")
    limit = MAX_TEXT_BYTES if target.stat().st_size > MAX_TEXT_BYTES else 0
    text, meta = read_text(target, max_bytes=limit)
    if text is None:
        raise RepoAdminError("Файл бинарный — открыть в редакторе нельзя")
    return text


def archive_preview(repo_root_value: str, rel: str, limit: int = 200) -> str:
    root = repo_root(repo_root_value)
    archive = _resolve(root, rel)
    if not archive.is_file():
        raise RepoAdminError(f"Файл не найден: {rel}")
    if not is_archive(archive.name):
        raise RepoAdminError(f"{archive.name} — не архив")
    ws = Workspace(repository=str(root))
    return repo_archive_list(ws, archive.relative_to(root).as_posix(), limit=limit)


# --------------------------------------------------------------------- modify

def write_entry(repo_root_value: str, rel: str, text: str) -> str:
    root = repo_root(repo_root_value)
    target = _resolve(root, rel)
    if target.is_dir():
        raise RepoAdminError(f"Это папка: {rel}")
    if is_archive(target.name):
        raise RepoAdminError("Это архив — редактировать его как текст нельзя")
    notes = write_text(target, text)
    size = target.stat().st_size if target.exists() else 0
    message = f"Сохранено: {rel} ({human_size(size)})"
    if notes:
        message += f" [{notes}]"
    return message


def create_dir(repo_root_value: str, parent_rel: str, name: str) -> str:
    root = repo_root(repo_root_value)
    parent = _resolve(root, parent_rel)
    if not parent.is_dir():
        raise RepoAdminError(f"Это не папка: {parent_rel or '/'}")
    name = _check_name(name)
    target = parent / name
    if target.exists():
        raise RepoAdminError(f"Уже существует: {name}")
    target.mkdir(parents=True)
    return f"Создана папка: {target.relative_to(root).as_posix()}"


def rename_entry(repo_root_value: str, rel: str, new_name: str) -> str:
    root = repo_root(repo_root_value)
    target = _resolve(root, rel)
    if target == root:
        raise RepoAdminError("Нельзя переименовать корень репозитория")
    new_name = _check_name(new_name)
    if target.name == new_name:
        return "Имя не изменилось"
    destination = target.with_name(new_name)
    if destination.exists():
        raise RepoAdminError(f"Уже существует: {new_name}")
    target.rename(destination)
    return f"Переименовано: {target.name} -> {new_name}"


def delete_entry(repo_root_value: str, rel: str) -> str:
    root = repo_root(repo_root_value)
    target = _resolve(root, rel)
    if target == root:
        raise RepoAdminError("Нельзя удалить корень репозитория")
    if target.name == TRASH_DIR:
        raise RepoAdminError(f"Папка {TRASH_DIR} служебная, её не трогаем")

    trash = root / TRASH_DIR / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    trash.mkdir(parents=True, exist_ok=True)
    destination = trash / target.name
    index = 2
    while destination.exists():
        destination = trash / f"{target.name} ({index})"
        index += 1
    shutil.move(str(target), str(destination))
    return (f"Удалено в корзину: {target.name} -> "
            f"{destination.relative_to(root).as_posix()}")


def save_file(repo_root_value: str, source_path: str, dest_rel: str = "",
              overwrite: bool = False) -> str:
    root = repo_root(repo_root_value)
    source = Path(source_path or "").expanduser()
    if not source.is_file():
        raise RepoAdminError(f"Файл не найден: {source}")
    destination_dir = _resolve(root, dest_rel)
    if destination_dir.is_file():
        raise RepoAdminError(f"Это файл, а не папка: {dest_rel}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / source.name
    if target.exists() and not overwrite:
        raise RepoAdminError(f"Уже существует: {target.name}. Включи перезапись.")
    shutil.copy2(source, target)
    return (f"Сохранено в репозиторий: {target.relative_to(root).as_posix()} "
            f"({human_size(target.stat().st_size)})")


# ------------------------------------------------------------------ archives

def extract_to_project(repo_root_value: str, rel: str, dest_dir: str,
                       overwrite: bool = False, include_executables: bool = False,
                       dry_run: bool = False, strip_components=None) -> str:
    root = repo_root(repo_root_value)
    archive = _resolve(root, rel)
    if not archive.is_file():
        raise RepoAdminError(f"Архив не найден: {rel}")
    if not is_archive(archive.name):
        raise RepoAdminError(f"{archive.name} — не архив")

    destination = Path(dest_dir or "").expanduser()
    try:
        destination = destination.resolve()
    except OSError:
        pass
    if not destination.is_dir():
        raise RepoAdminError(f"Папка назначения не существует: {destination}")

    ws = Workspace(roots=[str(destination)], primary=str(destination),
                   repository=str(root))
    return repo_extract(
        ws,
        archive=archive.relative_to(root).as_posix(),
        dest=".",
        strip_components=strip_components,
        overwrite=overwrite,
        include_executables=include_executables,
        dry_run=dry_run,
    )


def download_into_repo(repo_root_value: str, url: str, filename: str = "",
                       overwrite: bool = False, allow_network: bool = True) -> str:
    root = repo_root(repo_root_value)
    if not allow_network:
        raise RepoAdminError(
            "Скачивание отключено: включи «Разрешить скачивание архивов по ссылке».")
    return fetch_to_repo(root, url, filename=filename, overwrite=overwrite)
