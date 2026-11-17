TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Lists files and directories inside a folder with sizes. Path '.' = root of the selected project. When several projects are open, pass project=<name> to choose one (names are listed in the system prompt).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute directory path. Use '.' for project root."},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads a text file. Encoding (UTF-8/CP1251) and line endings are detected automatically. For files over 1.5 MB only the first 400 lines are returned unless you pass start_line/end_line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute file path"},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"},
                    "start_line": {"type": "integer", "description": "Optional 1-based first line to return"},
                    "end_line": {"type": "integer", "description": "Optional last line to return"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes plain text or code to a file. Existing encoding and CRLF/LF style are preserved. Relative paths are created in the PRIMARY project unless project=<name> is given. Do NOT include Markdown fences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "Plain file content, no Markdown"},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replaces ONE occurrence of old_text with new_text. Matching is exact first, then whitespace/indent tolerant, so you do not need to copy indentation perfectly. Fails if the match is ambiguous - add surrounding lines then. Preferred over write_file for surgical changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "Exact fragment to find; include a full line or two for uniqueness"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"}
                },
                "required": ["path", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "deactivate_file",
            "description": "Safely 'deletes' a file by renaming it to *.deactivated. It does not permanently delete the file. Use this to clean up temporary files or files created by mistake.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to deactivate"},
                    "project": {"type": "string", "description": "Optional project name"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "backup_and_remove_dir",
            "description": "Creates a ZIP backup of a directory in the primary project root, then deletes the directory. Use this only if the user explicitly asks to delete a folder (e.g., build/).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the directory to backup and remove"},
                    "project": {"type": "string", "description": "Optional project name"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "batch_write_files",
            "description": "Writes multiple files at once. Use instead of many write_file calls. Each item may carry its own project name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Relative path to the file"},
                                "content": {"type": "string", "description": "Plain text or code. Do NOT use Markdown."},
                                "project": {"type": "string", "description": "Optional project name for this file"}
                            },
                            "required": ["path", "content"]
                        }
                    }
                },
                "required": ["files"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_entire_project_via_cli",
            "description": "Dumps the PRIMARY project (structure + text contents) in one call. Skips build/, .gradle/, out/, target/, .git/ and inlines metadata only for jars/classes/binaries. Has a hard size budget, so it never hangs.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "java_scan",
            "description": "JVM project health report: Gradle/Maven modules, MC/loader versions from gradle.properties, duplicate FQCNs across common/fabric/forge, package-vs-path mismatches, mixin configs whose classes do not exist, accesswidener summary, lang key diff (en_us vs ru_ru), and every invalid JSON resource with line:column (catches trailing commas). Run this first on any Java project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory to scan, default '.'"},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "java_read_class",
            "description": "Compact outline of a Java file instead of the whole source: package, import groups, nested types, method signatures with line numbers. Use this to learn the real API before writing code (much cheaper than read_file on 900-line classes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to .java file"},
                    "project": {"type": "string", "description": "Optional project name"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "java_find",
            "description": "Regex search across sources with file:line results. kind='def' finds declarations only, kind='ref' finds usages only. ALWAYS use this before referencing a class, method, codec or field - it proves the symbol exists and prevents invented APIs. Returns an explicit warning when nothing is found.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex, e.g. 'CODEC' or 'makeErosion\\s*\\('"},
                    "glob": {"type": "string", "description": "Path glob, default '**/*.java'. Use '**/*.json' for resources."},
                    "kind": {"type": "string", "description": "any | def | ref", "enum": ["any", "def", "ref"]},
                    "limit": {"type": "integer", "description": "Max hits to print, default 60"},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "java_build",
            "description": "Runs gradlew/mvnw (auto-detected in the root or its parents) and returns exit code, parsed compile errors and the log tail. Use to verify Java changes actually compile. Default task 'compileJava'; for Architectury use 'build -x test'. Long builds: raise timeout_seconds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {"type": "string", "description": "Gradle tasks, e.g. ':common:compileJava' or 'build -x test'"},
                    "timeout_seconds": {"type": "integer", "description": "Default 600, max 1800"},
                    "extra_args": {"type": "string", "description": "Extra flags, e.g. '--offline -Dorg.gradle.jvmargs=-Xmx3g'"},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "java_parse_log",
            "description": "Parses a javac/Gradle/Maven build log into deduplicated errors grouped by message, with symbol/location details and the failed task. Pass log_text directly or a path to the log file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "log_text": {"type": "string", "description": "Raw build log text"},
                    "path": {"type": "string", "description": "Or path to a log file inside the project"},
                    "project": {"type": "string", "description": "Optional project name"}
                },
                "required": []
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "Finds relevant code fragments in the project by MEANING, not by exact words, using local Ollama embeddings (/api/embed). Use it when you do not know the file or the exact identifier: 'where is the request cost calculated', 'how are chats stored'. Only works with local Ollama (provider Ollama or a base URL with port 11434) and an installed embedding model such as nomic-embed-text; the first call builds the index and can take a minute. Returns path:line plus the fragments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for, in plain words"},
                    "limit": {"type": "integer", "description": "How many fragments to return, default 6"},
                    "rebuild": {"type": "boolean", "description": "Rebuild the index from scratch (after many file changes)"},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "python_info",
            "description": "Shows which Python interpreter the tools use (path, version, platform). Call it once before python_run if you are unsure about the environment.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "python_run",
            "description": "Runs Python inside the selected project: either inline `code` (python -c) or a `script` file that already exists in the project. Working directory = project root; the result contains exit code, stdout and stderr. Use it to actually verify Python code instead of guessing. Disabled when the user turns off «Разрешить запуск Python» in the settings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Inline Python code to execute"},
                    "script": {"type": "string", "description": "Path to a .py file inside the project (alternative to code)"},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command line arguments passed to the script"
                    },
                    "timeout_seconds": {"type": "integer", "description": "Default 120, max 600"},
                    "project": {"type": "string", "description": "Optional project name when several projects are open"}
                },
                "required": []
            }
        }
    },

    # ------------------------------------------------------------ repository

    {
        "type": "function",
        "function": {
            "name": "repo_list",
            "description": "Lists the asset repository (a folder of reusable materials: templates, MDK archives, libraries). Path '.' = repository root. Archives are marked [ARCHIVE] - use repo_archive_list to look inside, repo_extract to unpack.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder inside the repository, default '' (root)"},
                    "pattern": {"type": "string", "description": "Optional glob filter, e.g. '**/*.zip'"},
                    "recursive": {"type": "boolean", "description": "Walk subfolders, default true"},
                    "limit": {"type": "integer", "description": "Max rows, default 200"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "repo_search",
            "description": "Searches the repository by file name (regex, case-insensitive) or by file content when content=true. Use it to find the right template/archive before importing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Regex or plain text to look for"},
                    "glob": {"type": "string", "description": "Path glob, default '**/*'"},
                    "content": {"type": "boolean", "description": "Also grep inside text files, default false"},
                    "limit": {"type": "integer", "description": "Max hits, default 40"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "repo_archive_list",
            "description": "Shows what is inside a .zip/.jar/.tar/.tar.gz archive from the repository without extracting: file count, unpacked size, common top-level folder (which repo_extract strips automatically), scripts and executables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "archive": {"type": "string", "description": "Archive path relative to the repository, e.g. 'mdk/forge-1.20.1-mdk.zip'"},
                    "limit": {"type": "integer", "description": "Max entries to print, default 200"}
                },
                "required": ["archive"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "repo_read_file",
            "description": "Reads a text file from the repository, or a single member inside an archive of the repository (pass member='path/inside/archive'). Read-only, nothing is copied.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the repository"},
                    "member": {"type": "string", "description": "Optional member path inside the archive"},
                    "start_line": {"type": "integer", "description": "Optional 1-based first line"},
                    "end_line": {"type": "integer", "description": "Optional last line"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "repo_import_file",
            "description": "Copies ONE file from the repository (or one member of an archive) into a project. dest may be a folder or an explicit target path inside the project; empty dest puts the file into the project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the repository"},
                    "dest": {"type": "string", "description": "Target folder or file path inside the project, e.g. 'src/main/java'"},
                    "project": {"type": "string", "description": "Target project name (default: primary project)"},
                    "member": {"type": "string", "description": "When path is an archive: copy this single member"},
                    "overwrite": {"type": "boolean", "description": "Replace existing target file, default false"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "repo_extract",
            "description": "Unpacks a repository archive (e.g. an MDK zip) into a project folder. Automatically strips a single common top-level folder, refuses zip-slip paths, never touches files outside the project, and skips existing files unless overwrite=true. Use dry_run=true first to preview the plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "archive": {"type": "string", "description": "Archive path relative to the repository"},
                    "dest": {"type": "string", "description": "Destination folder inside the project, default '.' (project root)"},
                    "project": {"type": "string", "description": "Target project name (default: primary project)"},
                    "strip_components": {"type": "integer", "description": "How many leading path segments to strip; omit for automatic detection"},
                    "overwrite": {"type": "boolean", "description": "Replace existing files, default false"},
                    "include_executables": {"type": "boolean", "description": "Allow .exe/.msi/.ps1/.vbs/.scr/.dll/.com from the archive, default false"},
                    "dry_run": {"type": "boolean", "description": "Only report what would be extracted"}
                },
                "required": ["archive"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "repo_fetch",
            "description": "Downloads an archive by http/https URL into the repository _cache folder (size capped), so it can then be inspected with repo_archive_list and unpacked with repo_extract. Disabled when downloads are turned off in the program settings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL of a .zip/.jar/.tar/.tar.gz asset"},
                    "filename": {"type": "string", "description": "Optional saved name inside _cache"},
                    "overwrite": {"type": "boolean", "description": "Re-download if the file already exists"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "repo_status",
            "description": "Explains the state of the asset repository: whether the folder is configured, exists, is writable, how many files/archives it holds, and what exactly is wrong when the repo_* tools refuse to work. Call it first if repository access fails.",
            "parameters": {"type": "object", "properties": {}}
        }
    },

    # ------------------------------------------------------------- баланс

    {
        "type": "function",
        "function": {
            "name": "account_balance",
            "description": "Remaining balance (credits) of the current API key, fetched from the provider's /credits endpoint. Use it when the user asks about money or before an expensive multi-step job. Report the numbers exactly as returned - never invent a balance. The same value is already included in the system prompt, so call it only when you need a fresh number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fresh": {"type": "boolean", "description": "Ignore the 60-second cache and query the provider again"}
                },
                "required": []
            }
        }
    }
]

# --- Git / GitHub -----------------------------------------------------------
# Схемы лежат в отдельном модуле, здесь только подмешиваем их в общий список.
from app.tools.git_tools import GIT_TOOLS  # noqa: E402

TOOLS.extend(GIT_TOOLS)

# --- Удалённый доступ (терминал SSH + хранилища) ----------------------------
# Подключения настраиваются отдельно для каждого чата, пароли лежат в реестре
# Windows или в файле конфигов Linux (app/config/remote_store.py).
from app.tools.remote_tools import REMOTE_TOOLS  # noqa: E402

TOOLS.extend(REMOTE_TOOLS)

