"""Repository tools: browse an external asset folder, peek inside archives,
import single files and extract ZIP/TAR archives into a project.

Typical use case (as requested): the repository holds `forge-1.20.1-47.4.28-mdk.zip`,
the agent calls repo_extract(dest=".", project="mymod") and the MDK lands in the
project root, ready to be filled with mod code.

Safety rules:
  * reading is limited to the repository + known project roots;
  * nothing is ever written outside a project root (zip-slip / tar-slip checked);
  * .exe/.msi/.ps1/.vbs/.scr/.dll/.com are not extracted unless
    include_executables=true (a .bat/.cmd appears only inside a trusted MDK
    and is reported in the result so the user sees it);
  * archive bombs are capped by file count and total size.
"""
import fnmatch
import os
import re
import tarfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .safety import EXECUTABLE_EXTENSIONS, SCRIPT_EXTENSIONS, is_inside
from .textio import read_text

ARCHIVE_SUFFIXES = (".zip", ".jar", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".gradle", "build", "node_modules"}
MAX_EXTRACT_FILES = 20_000
MAX_EXTRACT_BYTES = 512 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_TEXT_BYTES = 800_000


# --------------------------------------------------------------- small helpers

def _repo_root(ws) -> Path:
    if not ws.repository:
        raise ValueError(
            "Репозиторий не настроен. Настройки -> Репозиторий -> укажи папку "
            "(например C:/assets/repo) и нажми Сохранить. После этого доступны "
            "repo_list, repo_search, repo_read_file, repo_import_file, repo_extract.")
    root = Path(ws.repository)
    if not root.is_dir():
        raise ValueError(f"Папка репозитория не существует: {root}. Проверь настройку.")
    return root


def _is_archive(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def _human(size: int) -> str:
    value = float(size)
    for unit in ("b", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "b" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _norm_member(member: str) -> str:
    """Archive member names are always '/'-separated and relative."""
    return (member or "").replace("\\", "/").strip().lstrip("/")


def _resolve_asset(ws, path: str) -> Path:
    """Resolve a path inside the repository, or inside one of the projects."""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("Укажи path (путь внутри репозитория).")
    repo = _repo_root(ws)
    if raw.lower().startswith("repo:"):
        raw = raw[5:].lstrip("/\\") or "."
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = repo / raw
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = Path(candidate)
    if is_inside(candidate, repo):
        return candidate
    for root in ws.roots:
        if is_inside(candidate, root):
            return candidate
    raise ValueError(f"Access denied: {candidate} вне репозитория и вне проектов.")


def _safe_member(name: str):
    """Normalise an archive member name; None when it tries to escape."""
    cleaned = (name or "").replace("\\", "/").lstrip("/")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts:
        return None
    if any(part == ".." for part in parts):
        return None
    if re.match(r"^[A-Za-z]:$", parts[0]):
        return None
    return "/".join(parts)


def _open_archive(path: Path):
    low = path.name.lower()
    if low.endswith((".zip", ".jar")):
        handle = zipfile.ZipFile(path)
        entries = [(info.filename, info.file_size, info.is_dir(), info)
                   for info in handle.infolist()]
        return "zip", handle, entries
    if low.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        handle = tarfile.open(path)
        entries = [(member.name, member.size, member.isdir(), member)
                   for member in handle.getmembers()]
        return "tar", handle, entries
    raise ValueError(
        f"{path.name}: не архив. Поддерживаются {', '.join(ARCHIVE_SUFFIXES)}.")


def _find_member(entries, member):
    """Locate an archive member by name.

    Tolerant on purpose: accepts backslashes, a missing leading '/', and names
    given without the common top-level folder (agents often copy the path from
    repo_archive_list and drop 'forge-1.20.1-mdk/'). Returns (entry, similar).
    """
    wanted = _norm_member(member)
    if not wanted:
        return None, []
    names = [(entry, _norm_member(entry[0])) for entry in entries]
    for entry, name in names:
        if name == wanted:
            return entry, []
    top = _common_top([name for _, name in names if name and not name.endswith("/")],
                      threshold=0.9)
    if top:
        prefixed = wanted if wanted.startswith(top + "/") else f"{top}/{wanted}"
        for entry, name in names:
            if name == prefixed:
                return entry, []
    similar = [name for _, name in names
               if name and not name.endswith("/")
               and (wanted.casefold() in name.casefold()
                    or name.casefold().endswith(wanted.casefold()))]
    return None, similar


def _read_member(kind: str, handle, entry) -> bytes:
    if kind == "zip":
        return handle.read(entry)
    extracted = handle.extractfile(entry)
    return extracted.read() if extracted else b""


def _common_top(names, threshold: float = 1.0):
    """Top-level directory holding the archive members, or None.

    threshold=1.0 keeps the strict old behaviour (every member must sit inside
    the same folder). A lower threshold tolerates a few loose root files
    (README/.gitignore shipped next to the MDK folder) and returns the folder
    that holds at least `threshold` of all members.
    """
    if not names:
        return None
    nested = [name.split("/")[0] for name in names if "/" in name]
    if not nested:
        return None
    counts = {}
    for top in nested:
        counts[top] = counts.get(top, 0) + 1
    top, count = max(counts.items(), key=lambda item: item[1])
    needed = max(1, int(len(names) * threshold))
    if count < needed or count * 2 <= len(nested):
        return None
    return top


# ------------------------------------------------------------------- read side

def repo_list(ws, path: str = "", pattern: str = "", recursive: bool = True,
              limit: int = 200) -> str:
    repo = _repo_root(ws)
    target = _resolve_asset(ws, path) if path else repo
    if target.is_file():
        if _is_archive(target.name):
            return (f"Это архив: {_rel(target, repo)} ({_human(target.stat().st_size)}).\n"
                    f"Вызови repo_archive_list(path='{_rel(target, repo)}'), "
                    f"чтобы увидеть содержимое.")
        return f"Это файл, а не папка: {_rel(target, repo)} ({_human(target.stat().st_size)})."
    if not target.is_dir():
        return f"Не найдено в репозитории: {path}"

    rows, archives, files = [], [], 0
    limit = max(1, min(int(limit), 2000))
    base_depth = len(target.parts)

    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        depth = len(Path(dirpath).parts) - base_depth
        if not recursive and depth >= 1:
            dirnames[:] = []
        for dirname in dirnames:
            rel = _rel(Path(dirpath) / dirname, repo)
            if pattern and not fnmatch.fnmatch(rel + "/", pattern):
                continue
            rows.append(f"[DIR]  {rel}/")
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            rel = _rel(full, repo)
            if pattern and not fnmatch.fnmatch(rel, pattern):
                continue
            try:
                size = full.stat().st_size
            except OSError:
                size = 0
            if _is_archive(filename):
                rows.append(f"[ARCHIVE] {rel}  ({_human(size)})")
                archives.append(rel)
            else:
                rows.append(f"[FILE] {rel}  ({_human(size)})")
            files += 1
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break

    head = [f"РЕПОЗИТОРИЙ: {repo}",
            f"ПАПКА: {_rel(target, repo) if target != repo else '.'}",
            f"НАЙДЕНО: {files} файл(ов)" + (f" по маске {pattern!r}" if pattern else "")]
    if archives:
        head.append("АРХИВЫ (можно распаковать через repo_extract): " + ", ".join(archives[:10]))
    if not rows:
        head.append("(пусто)")
    return "\n".join(head + rows)


def repo_search(ws, query: str, glob: str = "**/*", content: bool = False,
                limit: int = 40) -> str:
    repo = _repo_root(ws)
    limit = max(1, min(int(limit), 300))
    try:
        rx = re.compile(query, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(query), re.IGNORECASE)

    hits, scanned = [], 0
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            rel = _rel(full, repo)
            if not fnmatch.fnmatch(rel, glob):
                continue
            scanned += 1
            if rx.search(rel):
                hits.append(f"[NAME] {rel}")
            elif content:
                try:
                    if full.stat().st_size > MAX_TEXT_BYTES:
                        continue
                except OSError:
                    continue
                text, meta = read_text(full)
                if text is None:
                    continue
                for index, line in enumerate(text.splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{index}: {line.strip()[:160]}")
                        break
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break

    if not hits:
        return (f"Ничего не найдено по {query!r} в репозитории (просмотрено {scanned} файлов). "
                f"Может, стоит посмотреть repo_list().")
    out = [f"РЕПОЗИТОРИЙ: {repo}", f"СОВПАДЕНИЙ: {len(hits)} (просмотрено {scanned})"]
    return "\n".join(out + hits)


def repo_archive_list(ws, archive: str, limit: int = 200) -> str:
    path = _resolve_asset(ws, archive)
    if not path.is_file():
        return f"Архив не найден: {archive}"
    try:
        kind, handle, entries = _open_archive(path)
    except (zipfile.BadZipFile, tarfile.TarError) as error:
        return f"Ошибка чтения архива {path.name}: {error}"
    except ValueError as error:
        return str(error)

    try:
        files = [entry for entry in entries if not entry[2]]
        total = sum(size for _, size, _, _ in files)
        names = [name for name, _, is_dir, _ in entries if not is_dir]
        top = _common_top(names, threshold=0.9)
        limit = max(1, min(int(limit), 2000))
        out = [f"АРХИВ: {_rel(path, _repo_root(ws))} ({_human(path.stat().st_size)}, {kind})",
               f"СОДЕРЖИМОЕ: {len(files)} файл(ов), распакованный размер ~{_human(total)}"]
        if top:
            out.append(f"ОБЩИЙ ВЕРХНИЙ КАТАЛОГ: {top}/  (repo_extract снимет его автоматически)")
        scripts = [name for name in names if Path(name).suffix.lower() in SCRIPT_EXTENSIONS]
        execs = [name for name in names if Path(name).suffix.lower() in EXECUTABLE_EXTENSIONS]
        if scripts:
            out.append("СКРИПТЫ: " + ", ".join(scripts[:8]))
        if execs:
            out.append("ИСПОЛНЯЕМЫЕ (не распакуются без include_executables=true): "
                       + ", ".join(execs[:8]))
        out.append("--- первые записи ---")
        for name, size, is_dir, _ in entries[:limit]:
            out.append(f"{'[DIR] ' if is_dir else '      '}{name}" +
                       ("" if is_dir else f"  ({_human(size)})"))
        if len(entries) > limit:
            out.append(f"... ещё {len(entries) - limit} записей")
        return "\n".join(out)
    finally:
        handle.close()


def repo_read_file(ws, path: str, member: str = "", start_line: int = 0,
                   end_line: int = 0) -> str:
    target = _resolve_asset(ws, path)
    if member:
        if not _is_archive(target.name):
            return f"{target.name} не архив, member игнорируется. Убери member."
        kind, handle, entries = _open_archive(target)
        try:
            entry, similar = _find_member(entries, member)
            if entry is None:
                hint = ("\nПохожие: " + ", ".join(similar[:10])) if similar else ""
                return f"В архиве нет {member!r}. Посмотри repo_archive_list.{hint}"
            name, size, is_dir, entry = entry
            if is_dir:
                return f"{name} - папка внутри архива."
            raw = _read_member(kind, handle, entry)
        finally:
            handle.close()
        text = raw.decode("utf-8", errors="replace")
        source = f"{target.name}:{name}"
    else:
        if not target.is_file():
            return f"Файл не найден в репозитории: {path}"
        text, meta = read_text(target, max_bytes=MAX_TEXT_BYTES)
        if text is None:
            return (f"[BINARY] {target.name} ({_human(target.stat().st_size)}). "
                    f"Бинарные файлы не читаются; используй repo_import_file/repo_extract.")
        source = _rel(target, _repo_root(ws))

    lines = text.splitlines()
    total = len(lines)
    if start_line or end_line:
        start = max(0, (int(start_line) or 1) - 1)
        stop = int(end_line) if end_line else total
        body = "\n".join(lines[start:stop])
        return f"[{source}] строки {start + 1}-{min(stop, total)} из {total}\n{body}"
    return f"[{source}] {total} строк(и)\n{text}"


# ------------------------------------------------------------------ write side

def _resolve_dest(ws, dest: str, project=None) -> Path:
    try:
        target = ws.resolve(dest or ".", project=project, for_write=True)
    except ValueError as error:
        raise ValueError(str(error))
    return target


def repo_import_file(ws, path: str, dest: str = "", project=None, member: str = "",
                     overwrite: bool = False) -> str:
    repo = _repo_root(ws)
    source = _resolve_asset(ws, path)

    if member:
        if not _is_archive(source.name):
            return f"{source.name} не архив - убери member."
        kind, handle, entries = _open_archive(source)
        try:
            entry, similar = _find_member(entries, member)
            if entry is None:
                hint = ("\nПохожие: " + ", ".join(similar[:10])) if similar else ""
                return f"В архиве {source.name} нет {member!r}. Смотри repo_archive_list.{hint}"
            name, size, is_dir, entry = entry
            if is_dir:
                return f"{name} - папка; используй repo_extract."
            data = _read_member(kind, handle, entry)
        finally:
            handle.close()
        default_name = os.path.basename(name)
    else:
        if not source.is_file():
            return f"Файл не найден в репозитории: {path}"
        data = source.read_bytes()
        size = len(data)
        default_name = source.name

    if Path(default_name).suffix.lower() in EXECUTABLE_EXTENSIONS:
        return (f"Отказано: {default_name} - исполняемый файл "
                f"({', '.join(sorted(EXECUTABLE_EXTENSIONS))}).")

    dest_value = (dest or "").strip()
    target_dir = None
    if dest_value:
        candidate = _resolve_dest(ws, dest_value, project=project)
        looks_like_folder = (candidate.is_dir()
                             or dest_value.endswith(("/", "\\"))
                             or not Path(dest_value).suffix)
        if looks_like_folder:
            target_dir = candidate
    if target_dir is not None:
        target = target_dir / default_name
    elif dest_value:
        target = _resolve_dest(ws, dest_value, project=project)
    else:
        base = ws.project_dir(project)
        target = base / default_name

    if not any(is_inside(target, root) for root in ws.roots):
        return f"Access denied: {target} вне проектов."
    if target.exists() and not overwrite:
        return (f"{target} уже существует. Передай overwrite=true, если нужно заменить.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return (f"Импортировано: {target} ({_human(len(data))}) "
            f"из {_rel(source, repo) if is_inside(source, repo) else source}"
            + (f":{member}" if member else ""))


def repo_extract(ws, archive: str, dest: str = ".", project=None,
                 strip_components=None, overwrite: bool = False,
                 include_executables: bool = False, dry_run: bool = False) -> str:
    repo = _repo_root(ws)
    source = _resolve_asset(ws, archive)
    if not source.is_file():
        return f"Архив не найден: {archive}"
    if not _is_archive(source.name):
        return f"{source.name} не архив. Поддерживаются {', '.join(ARCHIVE_SUFFIXES)}."

    try:
        kind, handle, entries = _open_archive(source)
    except (zipfile.BadZipFile, tarfile.TarError) as error:
        return f"Битый архив {source.name}: {error}"
    except ValueError as error:
        return str(error)

    try:
        files = [(name, size, entry) for name, size, is_dir, entry in entries if not is_dir]
        names = [name for name, _, _ in files]
        auto_top = _common_top(names, threshold=0.9)
        explicit_strip = strip_components is not None
        strip = strip_components
        if strip is None:
            strip = 1 if auto_top else 0
        strip = max(0, int(strip))

        dest_root = _resolve_dest(ws, dest, project=project)
        if dest_root.exists() and not dest_root.is_dir():
            return f"dest должен быть папкой, а не файлом: {dest_root}"

        extracted, skipped, blocked, executable, scripts = [], [], [], [], []
        planned_bytes = 0
        total_bytes = 0
        plan = []

        for name, size, entry in files:
            safe = _safe_member(name)
            if not safe:
                blocked.append(f"{name} (небезопасный путь)")
                continue
            parts = safe.split("/")
            if (strip and len(parts) > strip
                    and (explicit_strip or auto_top is None or parts[0] == auto_top)):
                out_rel = "/".join(parts[strip:])
            else:
                out_rel = safe
            if not out_rel:
                continue
            suffix = Path(out_rel).suffix.lower()
            if suffix in EXECUTABLE_EXTENSIONS and not include_executables:
                blocked.append(f"{out_rel} (исполняемый)")
                continue

            target = dest_root / out_rel
            if not any(is_inside(target, root) for root in ws.roots):
                blocked.append(f"{out_rel} (вне проекта)")
                continue
            if planned_bytes + size > MAX_EXTRACT_BYTES:
                blocked.append(f"{out_rel} (превышен лимит {_human(MAX_EXTRACT_BYTES)})")
                break
            if len(plan) + len(skipped) >= MAX_EXTRACT_FILES:
                blocked.append(f"... лимит {MAX_EXTRACT_FILES} файлов")
                break
            if target.exists() and not overwrite:
                skipped.append(out_rel)
                continue

            planned_bytes += size
            plan.append((out_rel, target, entry, size))

        if dry_run:
            preview = "\n".join(f"  + {rel}" for rel, _, _, _ in plan[:60])
            more = "" if len(plan) <= 60 else f"\n  ... ещё {len(plan) - 60}"
            return (f"ПЛАН (dry_run, ничего не записано)\n"
                    f"АРХИВ: {_rel(source, repo)} ({len(files)} файл(ов))\n"
                    f"КУДА: {dest_root} (проект {ws.name_of(dest_root)})\n"
                    f"СНИМАЕТСЯ ВЕРХНИЙ КАТАЛОГ: {auto_top + '/' if strip and auto_top else 'нет'}\n"
                    f"БУДЕТ РАСПАКОВАНО: {len(plan)}\n"
                    f"ПРОПУЩЕНО (уже есть, overwrite=false): {len(skipped)}\n"
                    f"ЗАБЛОКИРОВАНО: {len(blocked)}\n{preview}{more}")

        for rel, target, entry, size in plan:
            data = _read_member(kind, handle, entry)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            extracted.append(rel)
            total_bytes += len(data)
            suffix = Path(rel).suffix.lower()
            if suffix in SCRIPT_EXTENSIONS:
                scripts.append(rel)
            if suffix in EXECUTABLE_EXTENSIONS:
                executable.append(rel)
    finally:
        handle.close()

    out = [f"РАСПАКОВАНО: {_rel(source, repo)} -> {dest_root} (проект {ws.name_of(dest_root)})"]
    out.append(f"ФАЙЛОВ: {len(extracted)}, объём ~{_human(total_bytes)}")
    if strip and auto_top:
        out.append(f"Снят общий верхний каталог: {auto_top}/")
    if skipped:
        out.append(f"ПРОПУЩЕНО (уже существует, overwrite=false): {len(skipped)} "
                   f"-> {', '.join(skipped[:5])}")
    if blocked:
        out.append(f"ЗАБЛОКИРОВАНО: {len(blocked)} -> {', '.join(blocked[:5])}")
    if executable:
        out.append("РАСПАКОВАНЫ ИСПОЛНЯЕМЫЕ (осторожно): " + ", ".join(executable[:8]))
    if scripts:
        out.append("СКРИПТЫ В ПРОЕКТЕ: " + ", ".join(scripts[:8]) +
                   " (запускай только через java_build, вручную не вызывай)")
    preview = ", ".join(extracted[:8])
    if preview:
        out.append("ПРИМЕРЫ: " + preview + (" ..." if len(extracted) > 8 else ""))
    return "\n".join(out)


def repo_fetch(ws, url: str, filename: str = "", overwrite: bool = False) -> str:
    if not ws.allow_network:
        return ("Скачивание отключено (Настройки -> Репозиторий -> Разрешить скачивание). "
                "Положи архив в папку репозитория вручную.")
    return fetch_to_repo(_repo_root(ws), url, filename=filename, overwrite=overwrite)


def fetch_to_repo(repo, url: str, filename: str = "", overwrite: bool = False) -> str:
    """Download `url` into `<repo>/_cache`.

    Shared by the agent tool repo_fetch and the UI repository panel.
    """
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        return "Разрешены только http/https ссылки."
    repo = Path(repo)
    cache = repo / "_cache"
    cache.mkdir(parents=True, exist_ok=True)

    name = (filename or "").strip() or os.path.basename(urllib.parse.urlparse(url).path)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "asset.zip"
    if not _is_archive(name):
        return (f"Имя {name!r} не похоже на архив. Укажи filename с одним из "
                f"расширений: {', '.join(ARCHIVE_SUFFIXES)}.")
    target = cache / name
    if target.exists() and not overwrite:
        return f"Уже скачано: {target}. Передай overwrite=true для повторной загрузки."

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AI-Connector"})
        with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
            written = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    return f"Файл больше {_human(MAX_DOWNLOAD_BYTES)}, загрузка отменена."
                out.write(chunk)
    except Exception as error:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return f"Не удалось скачать {url}: {type(error).__name__}: {error}"

    return (f"Скачано: {_rel(target, repo)} ({_human(target.stat().st_size)})\n"
            f"Дальше: repo_archive_list(path='{_rel(target, repo)}'), затем "
            f"repo_extract(path='{_rel(target, repo)}', dest='.', project=...).")


# ---------------------------------------------------------------- карта склада

CATALOG_LINES = 22
CATALOG_SCAN = 4000


def repo_catalog(ws, limit_lines: int = CATALOG_LINES, max_depth: int = 2) -> str:
    """Короткая карта репозитория для системного промпта.

    Агент сразу видит, что уже лежит на складе (архивы MDK, шаблоны, файлы),
    и не заставляет пользователя искать это вручную. Полный список по-прежнему
    даёт repo_list.
    """
    if not ws.repository:
        return ""
    root = Path(ws.repository)
    if not root.is_dir():
        return f"РЕПОЗИТОРИЙ: папки нет по пути {root} — repo_* не сработают."
    skip = set(SKIP_DIRS) | {"_cache", "_trash"}
    rows, files, archives, scanned = [], 0, 0, 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = _rel(Path(dirpath), root)
            depth = 0 if rel_dir in (".", "") else len(Path(rel_dir).parts)
            dirnames[:] = sorted(d for d in dirnames if d not in skip)
            if depth >= max_depth:
                dirnames[:] = []
            for dirname in dirnames:
                rows.append(f"  [DIR]  {_rel(Path(dirpath) / dirname, root)}/")
            for filename in sorted(filenames):
                full = Path(dirpath) / filename
                scanned += 1
                if scanned > CATALOG_SCAN:
                    break
                files += 1
                try:
                    size = full.stat().st_size
                except OSError:
                    size = 0
                if _is_archive(filename):
                    archives += 1
                    rows.append(f"  [ARCHIVE] {_rel(full, root)} ({_human(size)})")
                else:
                    rows.append(f"  [FILE] {_rel(full, root)}")
    except OSError as error:
        return f"РЕПОЗИТОРИЙ: не удалось прочитать {root} ({error})."

    head = [f"РЕПОЗИТОРИЙ (склад, только чтение): {root}",
            f"  файлов {files}, архивов {archives}"]
    if not rows:
        head.append("  (пусто — положи архивы/шаблоны в папку репозитория "
                    "или скачай через repo_fetch)")
        return "\n".join(head)
    limit = max(1, int(limit_lines))
    body = rows[:limit]
    if len(rows) > limit:
        body.append(f"  … ещё {len(rows) - limit} записей, полный список: repo_list")
    return "\n".join(head + body)
