"""Built-in project dumper - replacement for the external CodeDeployer CLI.

Design goals:
  * never touch build/, .gradle/, out/, target/, .git/
  * never inline jars/classes/nbt (metadata only)
  * hard character budget so a dump always returns instead of timing out
"""
import os
from pathlib import Path

SKIP_DIRS = {"build", ".gradle", ".git", "out", "target", "run", "logs", "log",
             ".idea", "node_modules", "__pycache__", ".venv", "venv", "bin", "obj",
             ".mvn", ".settings", "crash-reports"}

TEXT_EXTS = {
    ".java", ".kt", ".kts", ".gradle", ".properties", ".json", ".json5", ".toml",
    ".xml", ".yml", ".yaml", ".md", ".txt", ".cfg", ".conf", ".mcmeta", ".aw",
    ".accesswidener", ".py", ".js", ".ts", ".tsx", ".html", ".css", ".sh",
    ".bat", ".cmd", ".snippet", ".lang", ".gitignore", ".gitattributes",
}
BINARY_META_EXTS = {
    ".jar", ".class", ".zip", ".war", ".ear", ".jmod", ".aar", ".dll", ".exe",
    ".so", ".dylib", ".pdb", ".nbt", ".ogg", ".png", ".jpg", ".jpeg", ".gif",
    ".psd", ".mp3", ".mp4", ".bin", ".dat", ".lock",
}

MAX_SINGLE_FILE = 300_000
MAX_LINES_PER_FILE = 600
DEFAULT_BUDGET = 90_000


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _tree(root: Path, max_depth: int = 3) -> str:
    out = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = len(rel.parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        indent = "  " * depth
        if depth:
            out.append(f"{indent}{rel.name}/")
        for name in sorted(filenames):
            out.append(f"{indent}  {name}")
        if len(out) > 1200:
            out.append("... (tree truncated)")
            break
    return "\n".join(out)


def dump_project(root, budget: int = DEFAULT_BUDGET) -> str:
    root = Path(root).resolve()
    if not root.is_dir():
        return f"Error: {root} is not a directory"

    parts = [f"PROJECT DUMP: {root}", "", "=== STRUCTURE ===", _tree(root), "", "=== FILES ==="]
    used = sum(len(p) for p in parts)
    files = list(_walk(root))
    skipped_files, binary_meta = [], 0

    for path in files:
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        try:
            size = path.stat().st_size
        except OSError:
            continue

        if ext in BINARY_META_EXTS or size > MAX_SINGLE_FILE and ext not in TEXT_EXTS:
            binary_meta += 1
            parts.append(f"[META] {rel} ({size} bytes)")
            continue

        if ext not in TEXT_EXTS:
            parts.append(f"[META] {rel} ({size} bytes, non-text)")
            continue

        if used > budget:
            skipped_files.append(rel)
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            parts.append(f"[ERROR] {rel}: {e}")
            continue

        lines = text.splitlines()
        if len(lines) > MAX_LINES_PER_FILE:
            body = "\n".join(lines[:MAX_LINES_PER_FILE])
            body += f"\n... ({len(lines) - MAX_LINES_PER_FILE} more lines)"
        else:
            body = text

        block = f"\n--- {rel} ({size} b) ---\n{body}"
        used += len(block)
        parts.append(block)

    if skipped_files:
        parts.append("\n=== NOT INCLUDED (budget) ===\n" + "\n".join(skipped_files[:200]))
    parts.append(f"\n=== TOTALS ===\nfiles: {len(files)}, binary/meta: {binary_meta}, "
                 f"chars: {used}, budget: {budget}")

    result = "\n".join(parts)
    if len(result) > budget * 2:
        result = result[: budget * 2] + "\n... [TRUNCATED BY AI-CONNECTOR]"
    return result
