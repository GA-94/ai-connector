"""Path safety with support for MULTIPLE project roots.

`allowed_root` may be a single path (old behaviour, kept for tests and compatibility)
or a list of paths. A path is considered safe when it resolves inside ANY of the
roots and its extension is not blocked.
"""
import os
from pathlib import Path

BLOCKED_EXTENSIONS = {
    '.exe', '.bat', '.cmd', '.ps1', '.vbs', '.dll', '.scr', '.msi', '.com'
}

# Files that must never be *produced* by write_file, but may legitimately arrive
# from a repository archive: a Forge/Fabric MDK really does ship gradlew.bat.
EXECUTABLE_EXTENSIONS = {'.exe', '.msi', '.ps1', '.vbs', '.scr', '.dll', '.com'}
SCRIPT_EXTENSIONS = {'.bat', '.cmd', '.sh'}


def normalize_roots(allowed_root) -> list:
    """Accept str | Path | iterable of them, return a de-duplicated list of Paths."""
    if allowed_root is None:
        return []
    if isinstance(allowed_root, (str, os.PathLike)):
        items = [allowed_root]
    else:
        try:
            items = list(allowed_root)
        except TypeError:
            items = [allowed_root]

    out, seen = [], set()
    for item in items:
        if not item:
            continue
        try:
            path = Path(item).expanduser().resolve()
        except (OSError, ValueError):
            continue
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def is_inside(target, root) -> bool:
    """True when `target` is `root` itself or lives below it."""
    try:
        target = Path(target).expanduser().resolve()
        root = Path(root).expanduser().resolve()
    except (OSError, ValueError):
        return False
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def find_root(target, roots):
    """Return the root that contains `target`, or None."""
    for root in normalize_roots(roots):
        if is_inside(target, root):
            return root
    return None


def is_safe_path(requested_path: str, allowed_root) -> bool:
    if not requested_path or not allowed_root:
        return False
    roots = normalize_roots(allowed_root)
    if not roots:
        return False
    try:
        target = Path(requested_path).expanduser().resolve()
    except (OSError, ValueError):
        return False
    if find_root(target, roots) is None:
        return False
    if target.suffix.lower() in BLOCKED_EXTENSIONS:
        return False
    return True


def is_blocked_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in BLOCKED_EXTENSIONS
