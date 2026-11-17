"""Encoding- and line-ending-aware text IO.

Java projects on Windows are frequently cp1251/cp866 (Russian comments and
string literals) and CRLF. Reading them as utf-8 with errors='replace' and
writing back silently destroys the file (U+FFFD everywhere, LF everywhere).
Everything here exists to make read -> edit -> write byte-stable.
"""
import os
import re
from pathlib import Path

_BOM = b"\xef\xbb\xbf"
_CYRILLIC = re.compile(r"[\u0400-\u04ff]")
_CANDIDATES = ("cp1251", "cp866", "koi8-r", "iso-8859-5")

BINARY_PROBE = 8192
_CONTROL = bytes(list(range(0, 9)) + list(range(14, 32)))


def looks_binary(raw: bytes) -> bool:
    head = raw[:BINARY_PROBE]
    if not head:
        return False
    if b"\x00" in head:
        return True
    bad = sum(1 for b in head if b in _CONTROL)
    return bad / len(head) > 0.05


def detect_encoding(raw: bytes):
    """Return (encoding, has_bom). Prefers utf-8, then the cyrillic codepage
    that produces the most cyrillic letters."""
    if raw.startswith(_BOM):
        return "utf-8-sig", True
    try:
        raw.decode("utf-8")
        return "utf-8", False
    except UnicodeDecodeError:
        pass
    best, best_score = "cp1251", -1
    for enc in _CANDIDATES:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        score = len(_CYRILLIC.findall(text))
        if score > best_score:
            best, best_score = enc, score
    return best, False


def detect_newline(raw: bytes) -> str:
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def read_text(path, max_bytes=0):
    """Return (text|None, meta). text is None for binary files.

    Only the first 8 KB are inspected to classify the file, so a 300 MB jar
    is never dragged into memory. max_bytes>0 reads a truncated slice.
    """
    p = Path(path)
    size = p.stat().st_size
    with open(p, "rb") as f:
        head = f.read(BINARY_PROBE)
        if looks_binary(head):
            return None, {"binary": True, "encoding": None, "newline": None,
                          "bytes": size, "bom": False, "truncated": False}
        if max_bytes and size > max_bytes:
            raw = head + f.read(max(0, max_bytes - len(head)))
            truncated = True
        else:
            raw = head + f.read()
            truncated = False
    enc, bom = detect_encoding(raw)
    text = raw.decode(enc, errors="replace")
    return text, {"binary": False, "encoding": enc, "newline": detect_newline(raw),
                  "bytes": size, "bom": bom, "truncated": truncated}


def read_meta(path):
    try:
        return read_text(path)[1]
    except OSError as e:
        return {"binary": False, "encoding": "utf-8", "newline": "\n",
                "bytes": 0, "bom": False, "error": str(e)}


def write_text(path, text, encoding="utf-8", newline="\n", atomic=True):
    """Write text, preserving the file's existing encoding and EOL style.

    Returns a short note describing what was preserved or converted
    (empty string when nothing noteworthy happened).
    """
    p = Path(path)
    notes = []
    if p.exists():
        meta = read_meta(p)
        if meta.get("encoding"):
            if meta["encoding"] != encoding:
                notes.append(f"encoding kept: {meta['encoding']}")
            encoding = meta["encoding"]
        if meta.get("newline") and meta["newline"] != newline:
            notes.append(f"eol kept: {meta['newline']!r}")
            newline = meta["newline"]

    text = text.replace("\r\n", "\n")
    if newline == "\r\n":
        text = text.replace("\n", "\r\n")

    try:
        data = text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        notes.append(f"content not representable in {encoding}, written as utf-8")
        encoding = "utf-8"
        data = text.encode("utf-8", errors="replace")

    p.parent.mkdir(parents=True, exist_ok=True)
    if not atomic:
        p.write_bytes(data)
    else:
        tmp = p.with_name(p.name + ".ai_tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
    return "; ".join(notes)
