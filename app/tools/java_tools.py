"""Java/JVM toolkit: project scan, class outline, symbol search, build+log parsing.

Written for Gradle/Architectury Minecraft mods (common/fabric/forge), but works
for any JVM project. All tools are read-only except java_build.
"""
import fnmatch
import json
import os
import re
import subprocess

from app.runtime_env import silent_run
import time
from pathlib import Path

from .textio import read_text

SKIP_DIRS = {"build", ".gradle", ".git", "out", "target", "run", "logs", "log",
             ".idea", "node_modules", "__pycache__", ".venv", "venv", ".mvn",
             ".settings", "crash-reports"}

JAVA_EXTS = {".java"}
RESOURCE_EXTS = {".json", ".toml", ".properties", ".mcmeta"}
MAX_SCAN_FILES = 12_000
TODO_SCAN_BUDGET = 20_000_000
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def _iter_files(root, exts=None, limit=MAX_SCAN_FILES):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if exts and Path(name).suffix.lower() not in exts:
                continue
            yield Path(dirpath) / name
            count += 1
            if count >= limit:
                return


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _cap(items, n, note="more"):
    items = list(items)
    if len(items) <= n:
        return items
    return items[:n] + [f"... {len(items) - n} {note}"]


# ---------------------------------------------------------------- build system

def _modules(root: Path):
    mods = []
    for name in ("settings.gradle", "settings.gradle.kts"):
        f = root / name
        if not f.is_file():
            continue
        text, _ = read_text(f)
        if not text:
            continue
        for m in re.finditer(r"include\s*\(?\s*([^)\n]+)", text):
            for part in m.group(1).split(","):
                part = part.strip().strip("'\"")
                if part and not part.startswith(":"):
                    mods.append(part)
                elif part:
                    mods.append(part.strip(":"))
    return sorted(set(mods))


def _gradle_props(root: Path):
    props = {}
    for name in ("gradle.properties",):
        f = root / name
        if not f.is_file():
            continue
        text, _ = read_text(f) or ("", None)
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "!")) or "=" not in line:
                continue
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    return props


def _loaders(root: Path):
    found = set()
    for path in _iter_files(root, {".json", ".toml"}, limit=20000):
        if path.name == "fabric.mod.json":
            found.add("fabric")
        elif path.name == "mods.toml":
            found.add("forge")
        elif path.name == "neoforge.mods.toml":
            found.add("neoforge")
    if (root / "common" / "build.gradle").is_file() or (root / "common" / "build.gradle.kts").is_file():
        found.add("architectury-common")
    order = ["fabric", "forge", "neoforge", "architectury-common"]
    return [name for name in order if name in found]


# ------------------------------------------------------------ resource / mixin

def _json_errors(root: Path, limit=40):
    errors = []
    for path in _iter_files(root, {".json"}, limit=4000):
        text, meta = read_text(path)
        if text is None:
            continue
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            lines = text.splitlines()
            snippet = lines[e.lineno - 1].strip()[:160] if 0 < e.lineno <= len(lines) else ""
            errors.append(f"{_rel(path, root)}:{e.lineno}:{e.colno}: {e.msg} | {snippet}")
        if len(errors) >= limit:
            break
    return errors


def _flatten(data, prefix=""):
    out = {}
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, full))
        else:
            out[full] = value
    return out


def _lang_report(root: Path):
    report = []
    lang_files = [p for p in _iter_files(root, {".json"}, limit=4000)
                  if "lang" in p.as_posix().split("/") and p.name.endswith(".json")]
    by_dir = {}
    for p in lang_files:
        by_dir.setdefault(p.parent, []).append(p)

    for folder, files in sorted(by_dir.items()):
        keysets = {}
        for p in files:
            text, _ = read_text(p)
            if text is None:
                continue
            try:
                keysets[p.name] = set(_flatten(json.loads(text)))
            except json.JSONDecodeError:
                report.append(f"{_rel(p, root)}: invalid JSON")
        if "en_us.json" in keysets:
            base = keysets["en_us.json"]
            for name, keys in keysets.items():
                if name == "en_us.json":
                    continue
                missing = base - keys
                extra = keys - base
                if missing or extra:
                    report.append(f"{_rel(folder, root)}: {name} missing={len(missing)} extra={len(extra)}" +
                                  (f" e.g. {sorted(missing)[:5]}" if missing else ""))
        else:
            report.append(f"{_rel(folder, root)}: no en_us.json ({len(keysets)} lang files)")
    return report


def _mixin_report(root: Path, java_fqcns):
    report = []
    for path in _iter_files(root, {".json"}, limit=4000):
        if not path.name.endswith(".mixins.json"):
            continue
        text, _ = read_text(path)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            report.append(f"{_rel(path, root)}: INVALID JSON at {e.lineno}:{e.colno} - {e.msg}")
            continue
        package = data.get("package", "")
        entries = []
        for key in ("mixins", "client", "server", "injectors", "plugin"):
            value = data.get(key)
            if isinstance(value, list):
                entries.extend((key, str(v)) for v in value)
            elif isinstance(value, str):
                entries.append((key, value))
        missing = []
        for key, entry in entries:
            fq = f"{package}.{entry}" if package else entry
            short = fq.rsplit(".", 1)[-1]
            if fq not in java_fqcns and short not in java_fqcns:
                missing.append(f"{key}:{entry}")
        status = "OK" if not missing else f"missing {len(missing)}"
        report.append(f"{_rel(path, root)}: {len(entries)} entries, package={package or '?'}, {status}" +
                      (f" -> {_cap(missing, 8)}" if missing else ""))
    return report


def _accesswidener_report(root: Path):
    report = []
    for path in _iter_files(root, {".aw", ".accesswidener"}, limit=200):
        text, _ = read_text(path)
        lines = [l for l in (text or "").splitlines() if l.strip() and not l.strip().startswith("#")]
        header = lines[0] if lines else "?"
        report.append(f"{_rel(path, root)}: {len(lines)} entries, {header}")
    return report


# ------------------------------------------------------------------- scanning

def _collect_java_info(root: Path):
    info = {"files": [], "fqcns": set(), "dupes": {}, "mismatch": [], "lines": 0,
            "largest": [], "todo": 0, "todo_skipped": False}
    first_seen = {}
    scanned_bytes = 0
    for path in _iter_files(root, JAVA_EXTS, limit=6000):
        rel = _rel(path, root)
        info["files"].append(rel)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        info["largest"].append((size, rel))
        text, meta = read_text(path)
        if text is None:
            continue
        info["lines"] += text.count("\n") + 1
        m = PACKAGE_RE.search(text[:4000])
        package = m.group(1) if m else ""
        cls = path.stem
        fq = f"{package}.{cls}" if package else cls
        if fq in first_seen:
            info["dupes"].setdefault(fq, [first_seen[fq]]).append(rel)
        else:
            first_seen[fq] = rel
        info["fqcns"].add(fq)
        info["fqcns"].add(cls)
        marker = "/java/"
        if marker in rel:
            expected = rel.split(marker, 1)[1][:-len(".java")].replace("/", ".")
            if package and expected != f"{package}.{cls}":
                info["mismatch"].append(f"{rel} -> package {package}")
        if scanned_bytes < TODO_SCAN_BUDGET:
            info["todo"] += len(re.findall(r"\b(TODO|FIXME|XXX)\b", text))
            scanned_bytes += size
        else:
            info["todo_skipped"] = True
    info["largest"].sort(reverse=True)
    return info


def java_scan(path: str = ".") -> str:
    root = Path(path).resolve()
    if not root.is_dir():
        return f"Error: {root} is not a directory"

    out = [f"JAVA SCAN: {root}"]

    build_files = sorted({p.name for p in root.glob("*")
                          if p.name in ("build.gradle", "build.gradle.kts", "settings.gradle",
                                        "settings.gradle.kts", "pom.xml", "gradle.properties")})
    wrapper = next(iter(root.glob("gradle/wrapper/gradle-wrapper.properties")), None)
    gradle_version = "?"
    if wrapper:
        text, _ = read_text(wrapper)
        m = re.search(r"gradle-([\d.]+)-(bin|all)", text or "")
        if m:
            gradle_version = m.group(1)
    out.append(f"BUILD: {', '.join(build_files) or 'none'} | gradle {gradle_version}")

    modules = _modules(root)
    out.append(f"MODULES: {', '.join(modules) if modules else '(single module)'}")

    props = _gradle_props(root)
    interesting = ("minecraft_version", "mod_version", "archives_base_name", "mod_id",
                   "loader", "fabric_version", "forge_version", "terrablender_version",
                   "architectury_version", "java_version", "mappings")
    shown = [f"{k}={props[k]}" for k in interesting if k in props]
    if shown:
        out.append("PROPS: " + ", ".join(shown))

    loaders = _loaders(root)
    if loaders:
        out.append(f"LOADERS: {', '.join(loaders)}")

    info = _collect_java_info(root)
    out.append(f"SOURCES: {len(info['files'])} java files, ~{info['lines']} lines")
    if info["largest"]:
        tops = [f"{rel}={size // 1024}kb" for size, rel in info["largest"][:5]]
        out.append("LARGEST: " + ", ".join(tops))
    out.append(f"MARKERS: {info['todo']} TODO/FIXME" + (" (partial scan)" if info["todo_skipped"] else ""))

    if info["dupes"]:
        out.append("DUPLICATE FQCN:")
        for fq, paths in list(info["dupes"].items())[:15]:
            out.append(f"  {fq}: {', '.join(paths)}")
    if info["mismatch"]:
        out.append("PACKAGE/PATH MISMATCH:")
        out.extend("  " + s for s in _cap(info["mismatch"], 10))

    mixins = _mixin_report(root, info["fqcns"])
    if mixins:
        out.append("MIXINS:")
        out.extend("  " + s for s in _cap(mixins, 12))

    aw = _accesswidener_report(root)
    if aw:
        out.append("ACCESS WIDENER:")
        out.extend("  " + s for s in _cap(aw, 6))

    lang = _lang_report(root)
    if lang:
        out.append("LANG:")
        out.extend("  " + s for s in _cap(lang, 12))

    bad = _json_errors(root)
    if bad:
        out.append(f"INVALID JSON ({len(bad)}):")
        out.extend("  " + s for s in _cap(bad, 15))

    return "\n".join(out)


# -------------------------------------------------------------- class outline
_DECL_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|native|"
    r"default|sealed|non-sealed|strictfp|transient|volatile)\s+)*"
    r"(?:(?:class|interface|enum|record|@interface)\s+(\w+)|"
    r"([\w.<>\[\], ?]+?)\s+(\w+)\s*\(([^;{]*)\)\s*(?:throws[\w., \s]+)?\s*[{;])"
)
_TYPE_RE = re.compile(r"\b(class|interface|enum|record|@interface)\s+(\w+)([^\n{]*)")


def java_read_class(path: str, root: str = ".") -> str:
    root_path = Path(root).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root_path / path
    if not target.is_file():
        return f"Error: not found: {target}"

    text, meta = read_text(target)
    if text is None:
        return f"Error: {path} is binary"

    lines = text.splitlines()
    pkg = PACKAGE_RE.search(text[:4000])
    imports = re.findall(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", text, re.MULTILINE)

    out = [f"FILE: {_rel(target, root_path)} ({len(lines)} lines, {meta['encoding']}, eol {meta['newline']!r})"]
    out.append(f"PACKAGE: {pkg.group(1) if pkg else '(none)'}")
    groups = {}
    for imp in imports:
        top = imp.split(".")[0] if "." in imp else imp
        groups[top] = groups.get(top, 0) + 1
    top_groups = sorted(groups.items(), key=lambda kv: -kv[1])[:6]
    out.append(f"IMPORTS: {len(imports)} (" +
                 ", ".join(f"{k}:{v}" for k, v in top_groups) + ")")

    depth = 0
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*", "/*")):
            depth += line.count("{") - line.count("}")
            continue
        if depth <= 1:
            m = _DECL_RE.match(line)
            if m:
                if m.group(1):
                    out.append(f"  L{idx:<5} type {m.group(1)}")
                else:
                    ret = (m.group(2) or "").strip()
                    name = m.group(3)
                    if name not in ("if", "for", "while", "switch", "catch", "return", "new", "else"):
                        params = re.sub(r"\s+", " ", m.group(4) or "").strip()
                        if len(params) > 100:
                            params = params[:97] + "..."
                        out.append(f"  L{idx:<5} {ret} {name}({params})")
        depth += line.count("{") - line.count("}")
        if depth < 0:
            depth = 0

    if len(out) > 400:
        out = out[:400] + ["  ... outline truncated"]
    return "\n".join(out)


# -------------------------------------------------------------- symbol search
_DECL_HINT = re.compile(r"\b(class|interface|enum|record|@interface|void)\b")
_DECL_WORDS = ("public ", "private ", "protected ", "static ", "final ", "abstract ",
               "default ", "class ", "interface ", "enum ", "record ", "synchronized ")


def _looks_decl(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith(("//", "*", "/*", "return ", "if (", "if(", "for ", "while ",
                              "} ", "import ", "package ", "@")):
        return False
    if not (_DECL_HINT.search(s) or ("(" in s and ")" in s and ("{" in s or ";" in s))):
        return False
    return any(word in s for word in _DECL_WORDS)


def java_find(pattern: str, root: str = ".", glob: str = "**/*.java",
              kind: str = "any", limit: int = 60) -> str:
    root_path = Path(root).resolve()
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"Error: bad regex: {e}"

    hits, per_file, total = [], {}, 0
    scanned = 0
    for path in _iter_files(root_path, None, limit=8000):
        rel = _rel(path, root_path)
        if not fnmatch.fnmatch(rel, glob):
            continue
        text, meta = read_text(path)
        if text is None:
            continue
        scanned += 1
        if len(text) > 800_000:
            text = text[:800_000]
        for idx, line in enumerate(text.splitlines(), 1):
            if not rx.search(line):
                continue
            decl = _looks_decl(line)
            if kind == "def" and not decl:
                continue
            if kind == "ref" and decl:
                continue
            total += 1
            per_file[rel] = per_file.get(rel, 0) + 1
            if len(hits) < limit:
                hits.append(f"{rel}:{idx}: {line.strip()[:180]}")

    if not total:
        return (f"No matches for {pattern!r} in {glob} ({scanned} files scanned). "
                f"The symbol probably does not exist - do not use it.")

    out = [f"MATCHES: {total} in {len(per_file)} file(s), scanned {scanned} files"]
    out.extend(hits)
    if total > len(hits):
        out.append(f"... {total - len(hits)} more")
    out.append("PER FILE: " + ", ".join(f"{k}({v})" for k, v in
                                         sorted(per_file.items(), key=lambda kv: -kv[1])[:8]))
    return "\n".join(out)


# ------------------------------------------------------------ build / log parse
_JAVAC_RE = re.compile(r"^(?P<file>[^\s][^:]*\.java):(?P<line>\d+):\s*"
                       r"(?P<kind>error|warning|note):\s*(?P<msg>.*)$", re.MULTILINE)
_MAVEN_RE = re.compile(r"^\[ERROR\]\s+(?P<file>\S+\.java):\[(?P<line>\d+),(?P<col>\d+)\]\s*"
                       r"(?P<msg>.*)$", re.MULTILINE)
_GRADLE_TASK_RE = re.compile(r"Execution failed for task '([^']+)'")
_GRADLE_WRONG_RE = re.compile(r"\* What went wrong:\s*\n(.+?)(?:\n\*|\Z)", re.DOTALL)
_DETAIL_RE = re.compile(r"^\s*(symbol|location|required|found|cause|reason):\s*(.+)$")


def parse_build_errors(log_text: str) -> str:
    if not log_text:
        return "Empty log."
    entries = []
    for m in _JAVAC_RE.finditer(log_text):
        entries.append({"file": m.group("file"), "line": int(m.group("line")),
                        "kind": m.group("kind"), "msg": m.group("msg").strip(),
                        "detail": ""})
    for m in _MAVEN_RE.finditer(log_text):
        entries.append({"file": m.group("file"), "line": int(m.group("line")),
                        "kind": "error", "msg": m.group("msg").strip(), "detail": ""})

    lines = log_text.splitlines()
    for idx, line in enumerate(lines):
        dm = _DETAIL_RE.match(line)
        if not dm or not entries:
            continue
        prev = entries[-1]
        if abs(prev["line"]) and dm.group(1) in ("symbol", "location", "required", "found"):
            prev["detail"] = (prev["detail"] + f" {dm.group(1)}={dm.group(2).strip()}").strip()

    out = [f"PARSED: {len(entries)} diagnostic(s)"]
    if entries:
        grouped = {}
        for e in entries:
            if e["kind"] != "error":
                continue
            key = (e["msg"], e["detail"])
            grouped.setdefault(key, []).append(e)
        if grouped:
            out.append(f"ERRORS: {sum(len(v) for v in grouped.values())} in {len(grouped)} kind(s)")
            for (msg, detail), items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
                first = items[0]
                out.append(f"  x{len(items)} [{first['file']}:{first['line']}] {msg}")
                if detail:
                    out.append(f"        {detail}")
                if len(items) > 1:
                    others = ", ".join(f"{i['file']}:{i['line']}" for i in items[1:4])
                    out.append(f"        also: {others}")
    warnings = [e for e in entries if e["kind"] == "warning"]
    if warnings:
        out.append(f"WARNINGS: {len(warnings)} (first: {warnings[0]['file']}:{warnings[0]['line']} {warnings[0]['msg'][:80]})")

    task = _GRADLE_TASK_RE.search(log_text)
    if task:
        out.append(f"FAILED TASK: {task.group(1)}")
    wrong = _GRADLE_WRONG_RE.search(log_text)
    if wrong:
        first_line = " ".join(wrong.group(1).strip().splitlines()[:3])
        out.append(f"WHAT WENT WRONG: {first_line[:400]}")
    if not entries and not task and "error" not in log_text.lower():
        out.append("No javac/gradle errors detected - the build probably succeeded.")
    return "\n".join(out)


def _find_wrapper(root: Path):
    for base in (root, root.parent, root.parent.parent):
        for name in ("gradlew.bat", "gradlew", "mvnw.cmd", "mvnw"):
            candidate = base / name
            if candidate.is_file():
                return candidate
    return None


def java_build(tasks: str = "compileJava", timeout: int = 600, extra_args: str = "",
               root: str = ".") -> str:
    root_path = Path(root).resolve()
    wrapper = _find_wrapper(root_path)
    if not wrapper:
        return ("Error: gradlew/mvnw not found in project root or its parents. "
                "Run the build manually and paste the log into java_parse_log.")

    is_maven = wrapper.name.lower().startswith("mvnw")
    args = tasks.split() if tasks else []
    if not is_maven and "--console" not in tasks:
        args += ["--console=plain"]
    if extra_args:
        args += extra_args.split()

    if wrapper.suffix.lower() in (".bat", ".cmd"):
        cmd = ["cmd", "/c", str(wrapper)] + args
    else:
        cmd = [str(wrapper)] + args

    timeout = max(30, min(int(timeout), 1800))
    started = time.time()
    try:
        proc = silent_run(cmd, cwd=str(wrapper.parent), capture_output=True,
                              text=True, errors="replace", timeout=timeout)
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired as e:
        partial = ""
        for stream in (e.stdout, e.stderr):
            if stream:
                partial += stream if isinstance(stream, str) else stream.decode("utf-8", "replace")
        return (f"TIMEOUT after {timeout}s ({wrapper.name} {' '.join(args)}).\n"
                f"Partial output:\n{partial[-4000:]}")
    except OSError as e:
        return f"Error launching build: {e}"

    elapsed = time.time() - started
    summary = parse_build_errors(output)
    tail = output.strip()[-2500:] if output.strip() else "(no output)"
    return (f"COMMAND: {wrapper.name} {' '.join(args)}\n"
            f"EXIT: {code} in {elapsed:.1f}s\n"
            f"--- SUMMARY ---\n{summary}\n"
            f"--- TAIL ---\n{tail}")


def java_parse_log(log_text: str = "", path: str = "", root: str = ".") -> str:
    if not log_text and path:
        target = Path(path)
        if not target.is_absolute():
            target = Path(root).resolve() / path
        if not target.is_file():
            return f"Error: log not found: {target}"
        text, _ = read_text(target)
        log_text = text or ""
    if not log_text:
        return "Error: pass log_text or path."
    if len(log_text) > 2_000_000:
        log_text = log_text[-2_000_000:]
    return parse_build_errors(log_text)
