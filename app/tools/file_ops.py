import datetime
import difflib
import os
import re
import shutil
import zipfile
from pathlib import Path

from .safety import find_root, is_blocked_extension, is_safe_path, normalize_roots
from .textio import read_meta, read_text, write_text

MAX_READ_BYTES = 1_500_000
DEFAULT_HEAD_LINES = 400
IGNORED_DIR_NAMES = {"build", ".gradle", ".git", "out", "target", "run", "logs",
                     ".idea", "node_modules", "__pycache__", ".venv", "venv"}
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _roots(allowed_root) -> list:
    roots = normalize_roots(allowed_root)
    if not roots:
        raise PermissionError("No project root configured")
    return roots


def _resolve_path(path: str, allowed_root, must_exist: bool = False,
                  for_write: bool = False) -> str:
    """Resolve `path` against one of the project roots (multi-project aware).

    Relative paths prefer the primary (first) root; if the file exists only in
    another root, that one is used. Ambiguous matches raise instead of guessing.
    """
    roots = _roots(allowed_root)
    raw = (path or "").strip() or "."

    if _DRIVE_RE.match(raw) or Path(raw).is_absolute():
        target = Path(raw).expanduser()
        try:
            target = target.resolve()
        except OSError:
            pass
        if not is_safe_path(target, roots):
            raise PermissionError(f"Access denied (outside project root or blocked): {target}")
        if must_exist and not target.is_file():
            raise FileNotFoundError(f"File not found: {target}")
        return str(target)

    head = roots[0] / raw
    existing = []
    for root in roots:
        candidate = root / raw
        if candidate.exists():
            existing.append(candidate)

    if len(existing) == 1:
        target = existing[0]
    elif len(existing) > 1:
        head_key = os.path.normcase(str(head.resolve()))
        match = [c for c in existing if os.path.normcase(str(c.resolve())) == head_key]
        if not match:
            listing = "\n".join(
                f"  {(find_root(c, roots) or roots[0]).name}: {c}" for c in existing)
            raise PermissionError(
                f"Путь {raw!r} существует в нескольких проектах:\n{listing}\n"
                f"Укажи project=<имя> или префикс 'имя:{raw}'.")
        target = match[0]
    else:
        if for_write or not must_exist:
            target = head
        else:
            names = ", ".join(root.name for root in roots)
            raise FileNotFoundError(f"File not found in any project ({names}): {raw}")

    try:
        target = target.resolve()
    except OSError:
        pass
    if not is_safe_path(target, roots):
        raise PermissionError(f"Access denied (outside project root or blocked): {target}")
    if must_exist and not target.is_file():
        raise FileNotFoundError(f"File not found: {target}")
    return str(target)


def read_file(path: str, allowed_root, start_line: int = 0, end_line: int = 0) -> str:
    path = _resolve_path(path, allowed_root, must_exist=True)
    meta = read_meta(path)
    if meta.get("binary"):
        return (f"[BINARY FILE] {path} ({meta.get('bytes', 0)} bytes). "
                f"Content not shown; use java_scan or list_files for metadata.")

    text, meta = read_text(path, max_bytes=MAX_READ_BYTES)
    if meta.get("truncated"):
        lines = text.splitlines()
        if not start_line:
            head = "\n".join(lines[:DEFAULT_HEAD_LINES])
            return (f"[AI-Connector] {path}: {meta['bytes']} bytes (truncated), "
                    f"showing first {DEFAULT_HEAD_LINES} of {len(lines)} lines. "
                    f"Use start_line/end_line to read a slice.\n{head}")

    if start_line or end_line:
        lines = text.splitlines()
        total = len(lines)
        start = max(0, (start_line or 1) - 1)
        end = end_line if end_line else total
        chunk = "\n".join(lines[start:end])
        return (f"[AI-Connector] {path} lines {start + 1}-{min(end, total)} of {total} "
                f"(encoding {meta['encoding']})\n{chunk}")

    return text


def write_file(path: str, content: str, allowed_root) -> str:
    path = _resolve_path(path, allowed_root, must_exist=False, for_write=True)
    if is_blocked_extension(path):
        raise PermissionError(f"Extension blocked: {path}")
    note = write_text(path, content)
    return f"Written: {path}" + (f" [{note}]" if note else "")


def deactivate_file(path: str, allowed_root) -> str:
    """Безопасное удаление: переименовывает файл в *.deactivated"""
    path = _resolve_path(path, allowed_root, must_exist=False)
    if not os.path.exists(path):
        return f"File not found: {path}"

    if os.path.isdir(path):
        return f"Error: {path} is a directory. Use backup_and_remove_dir for directories."

    if is_blocked_extension(path):
        raise PermissionError(f"Extension blocked: {path}")

    try:
        if path.endswith(".deactivated"):
            new_path = path + f".{int(datetime.datetime.now().timestamp())}"
        else:
            new_path = path + ".deactivated"

        os.rename(path, new_path)
        return f"Deactivated (renamed to): {os.path.basename(new_path)}"
    except Exception as e:
        return f"Failed to deactivate {path}: {e}"


def backup_and_remove_dir(path: str, allowed_root) -> str:
    """Создает ZIP-бэкап папки и удаляет её."""
    roots = _roots(allowed_root)
    path = _resolve_path(path, roots, must_exist=False)
    if not os.path.exists(path):
        return f"Directory not found: {path}"

    if not os.path.isdir(path):
        return f"Error: {path} is not a directory. Use deactivate_file for files."

    if os.path.basename(path) in ("src", "common", "fabric", "forge", "app"):
        return f"Error: Refusing to delete critical directory: {path}"

    try:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{os.path.basename(path)}_{timestamp}.zip"
        backup_path = os.path.join(str(roots[0]), backup_name)

        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, path)
                    zf.write(file_path, arcname)

        shutil.rmtree(path)
        return f"Backed up to {backup_name} and deleted directory: {path}"
    except Exception as e:
        return f"Failed to backup/delete {path}: {e}"


def _fuzzy_regex(text: str) -> str:
    lines = [l.strip() for l in text.strip("\n").splitlines()]
    parts = []
    for line in lines:
        tokens = [re.escape(t) for t in re.split(r"\s+", line) if t]
        parts.append(r"\s+".join(tokens))
    return r"[ \t]*" + r"\s*\n\s*".join(parts) + r"[ \t]*"


def _indent_of(content: str, pos: int) -> str:
    line_start = content.rfind("\n", 0, pos) + 1
    line = content[line_start:pos]
    return re.match(r"[ \t]*", line).group(0)


def _reindent(new_text: str, indent: str) -> str:
    lines = new_text.splitlines()
    if len(lines) <= 1 or not indent:
        return new_text
    base = re.match(r"[ \t]*", lines[0]).group(0)
    out = [lines[0]]
    for line in lines[1:]:
        if not line.strip():
            out.append("")
            continue
        current = re.match(r"[ \t]*", line).group(0)
        if current.startswith(base):
            out.append(indent + current[len(base):])
        else:
            out.append(indent + line)
    return "\n".join(out)


def edit_file(path: str, old_text: str, new_text: str, allowed_root) -> str:
    path = _resolve_path(path, allowed_root, must_exist=True)
    if is_blocked_extension(path):
        raise PermissionError(f"Extension blocked: {path}")

    text, meta = read_text(path)
    if text is None:
        raise ValueError(f"{path} is binary, refusing to edit")
    if meta["bytes"] > 5_000_000:
        raise ValueError(f"{path} is {meta['bytes']} bytes - too large to edit safely, "
                         f"use read_file with a line range")

    hits = text.count(old_text)
    if hits == 1:
        content = text.replace(old_text, new_text, 1)
        strategy = "exact"
    elif hits > 1:
        raise ValueError(
            f"old_text appears {hits} times in {path}. Add surrounding context "
            f"(whole line or method signature) so the match is unique.")
    else:
        try:
            rx = re.compile(_fuzzy_regex(old_text), re.MULTILINE)
        except re.error as e:
            raise ValueError(f"Could not build tolerant match: {e}")
        found = list(rx.finditer(text))
        if len(found) == 1:
            m = found[0]
            indent = _indent_of(text, m.start())
            replacement = _reindent(new_text, indent)
            content = text[:m.start()] + replacement + text[m.end():]
            strategy = "whitespace-tolerant"
        elif len(found) > 1:
            raise ValueError(
                f"old_text matches {len(found)} places in {path} even with tolerant "
                f"matching (first at line {text[:found[0].start()].count(chr(10)) + 1}). "
                f"Include more surrounding lines.")
        else:
            lines = text.splitlines()
            probe = (old_text.splitlines() or [""])[0].strip()
            near = [f"{i + 1}: {l.strip()[:120]}" for i, l in enumerate(lines)
                    if probe and probe[:24] in l][:5]
            hint = ("\nSimilar lines:\n" + "\n".join(near)) if near else ""
            raise ValueError(
                f"old_text not found in {path} ({len(lines)} lines, encoding "
                f"{meta['encoding']}, eol {meta['newline']!r}).{hint}")

    note = write_text(path, content, encoding=meta["encoding"], newline=meta["newline"])
    msg = f"Edited: {path} (match: {strategy}, {hits or 1} candidate)"
    return msg + (f" [{note}]" if note else "")


def list_files(path: str, allowed_root) -> str:
    roots = _roots(allowed_root)
    path = _resolve_path(path, roots, must_exist=False)
    if not os.path.isdir(path):
        return f"Error: {path} is not a directory."

    try:
        names = sorted(os.listdir(path), key=lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower()))
    except OSError as e:
        return f"Error listing directory: {e}"

    out = []
    for name in names:
        full = os.path.join(path, name)
        if os.path.isdir(full):
            try:
                count = len(os.listdir(full))
            except OSError:
                count = -1
            mark = "  <- build/cache, skipped by java tools" if name in IGNORED_DIR_NAMES else ""
            shown = f"({count} entries)" if count >= 0 else ""
            out.append(f"[DIR]  {name}/{shown}{mark}")
        else:
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append(f"[FILE] {name}  ({size} b)")

    header = []
    if len(roots) > 1:
        owner = find_root(path, roots)
        header.append(f"PROJECT: {owner.name if owner else '?'} ({path})"
                      f" | all projects: {', '.join(r.name for r in roots)}")
    return "\n".join(header + (out if out else ["(empty directory)"]))


def batch_write_files(files_data: list, allowed_root) -> str:
    ok, errors, notes = 0, [], []
    for item in files_data:
        path = item.get("path")
        content = item.get("content")
        if not path or content is None:
            errors.append(f"Skipped invalid item: {item}")
            continue
        try:
            result = write_file(path, content, allowed_root)
            ok += 1
            if "[" in result:
                notes.append(result)
        except Exception as e:
            errors.append(f"Failed {path}: {type(e).__name__}: {e}")

    parts = [f"Written: {ok} file(s)."]
    if notes:
        parts.append("Notes:\n" + "\n".join(notes))
    if errors:
        parts.append("Errors:\n" + "\n".join(errors))
    return "\n".join(parts)


def run_deployer_cli(allowed_root, cli_path: str = "") -> str:
    from .project_dump import dump_project
    roots = normalize_roots(allowed_root)
    return dump_project(roots[0] if roots else ".")
