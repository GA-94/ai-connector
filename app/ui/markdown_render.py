"""Markdown -> HTML для окна чата.

QTextBrowser понимает только подмножество HTML/CSS, поэтому:
  * блоки кода подсвечиваются pygments и пакуются в таблицу (фон + отступы);
  * сообщение целиком - одна карточка (таблица), поэтому подсветка сообщения
    не рвётся на кусках с кодом;
  * HTML из ответа модели не исполняется: текст экранируется.
"""

import html
import re
from functools import lru_cache

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

MONO = "'Cascadia Mono', 'Consolas', 'Courier New', monospace"

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+.#-]*)\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.+)$")
_TABLE_SEP_RE = re.compile(
    r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")

_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_URL_RE = re.compile(r"(?<![\w\"'=])(https?://[^\s<>()\[\]\"']+)")

_ALIASES = {
    "py": "python", "js": "javascript", "ts": "typescript", "sh": "bash",
    "shell": "bash", "yml": "yaml", "c++": "cpp", "c#": "csharp",
    "cs": "csharp", "golang": "go", "kt": "kotlin", "rs": "rust",
    "htm": "html", "txt": "text", "plain": "text", "ps1": "powershell",
    "batch": "batch", "diff": "diff",
}


@lru_cache(maxsize=8)
def _formatter(style: str) -> HtmlFormatter:
    return HtmlFormatter(style=style, noclasses=True, nowrap=True)


def _lexer(lang: str):
    lang = (lang or "").strip().lower()
    if not lang:
        return TextLexer()
    for name in (_ALIASES.get(lang, lang), lang):
        try:
            return get_lexer_by_name(name, stripnl=False, stripall=False)
        except ClassNotFound:
            continue
    return TextLexer()


def _plain_to_pre(fragment: str) -> str:
    """Переводы строк и пробелы -> <br>/&nbsp;, не задевая теги подсветки."""
    out = []
    for part in re.split(r"(<[^>]+>)", fragment):
        if not part:
            continue
        if part.startswith("<"):
            out.append(part)
            continue
        part = part.replace("\t", "    ")
        part = part.replace(" ", "&nbsp;")
        part = part.replace("\n", "<br>")
        out.append(part)
    return "".join(out)


def highlight_code(code: str, lang: str, colors: dict) -> str:
    text = code if code.endswith("\n") else code + "\n"
    try:
        rendered = highlight(text, _lexer(lang),
                             _formatter(colors.get("pygments", "friendly")))
    except Exception:
        rendered = html.escape(text)
    return _plain_to_pre(rendered.rstrip("\n"))


def render_code_block(code: str, lang: str, colors: dict) -> str:
    label = (lang or "text").lower()
    body = highlight_code(code, lang, colors)
    return (
        '<table width="100%" cellspacing="0" cellpadding="10" style="margin:8px 0;">'
        f'<tr><td bgcolor="{colors["code_bg"]}">'
        f'<p style="margin:0 0 6px 0; font-size:8pt; color:{colors["muted"]}; '
        f'font-family:{MONO};">{label}</p>'
        f'<span style="font-family:{MONO}; font-size:9pt; color:{colors["code_text"]};">'
        f'{body}</span></td></tr></table>'
    )


def render_inline(text: str, colors: dict) -> str:
    """Строчная разметка: **жирный**, *курсив*, `код`, ссылки, ~~зачёркнутый~~."""
    if not text:
        return ""
    stash = []

    def keep(piece: str) -> str:
        stash.append(piece)
        return "\x00{0}\x00".format(len(stash) - 1)

    def code_span(match):
        body = html.escape(match.group(1), quote=False)
        return keep(
            f'<span style="font-family:{MONO}; font-size:9pt; '
            f'background-color:{colors["code_bg"]}; color:{colors["code_text"]};">'
            f'&nbsp;{body}&nbsp;</span>')

    work = _INLINE_CODE_RE.sub(code_span, text)
    work = html.escape(work, quote=False)

    def link(match):
        url = html.escape(match.group(2), quote=True)
        return keep(f'<a href="{url}" style="color:{colors["link"]}; '
                    f'text-decoration:none;">{match.group(1)}</a>')

    def bare_url(match):
        url = match.group(1)
        return keep(f'<a href="{html.escape(url, quote=True)}" '
                    f'style="color:{colors["link"]};">{url}</a>')

    work = _LINK_RE.sub(link, work)
    work = _BOLD_RE.sub(lambda m: "<b>{0}</b>".format(m.group(1) or m.group(2)), work)
    work = _STRIKE_RE.sub(lambda m: "<s>{0}</s>".format(m.group(1)), work)
    work = _ITALIC_RE.sub(lambda m: "<i>{0}</i>".format(m.group(1)), work)
    work = _URL_RE.sub(bare_url, work)

    for _ in range(5):
        if "\x00" not in work:
            break
        for index, piece in enumerate(stash):
            work = work.replace("\x00{0}\x00".format(index), piece)
    return work.replace("\n", "<br>")


def _split_table_row(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def _render_table(rows, colors) -> str:
    header, body = rows[0], rows[1:]
    parts = ["<tr>" + "".join(
        f'<td bgcolor="{colors["table_head"]}"><b>{cell}</b></td>'
        for cell in header) + "</tr>"]
    for row in body:
        cells = (row + [""] * len(header))[:len(header)]
        parts.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return ('<table width="100%" border="1" cellspacing="0" cellpadding="6" '
            f'style="margin:8px 0; border-color:{colors["border"]};">'
            + "".join(parts) + "</table>")


def _render_list(items, colors) -> str:
    rows = []
    for level, ordered, number, text in items:
        pad = 12 + min(level, 4) * 16
        bullet = (number + ".") if ordered else "•"
        rows.append(
            f'<tr><td width="{pad}" align="right" valign="top" '
            f'style="color:{colors["accent"]}; padding-right:6px;">{bullet}</td>'
            f'<td valign="top">{text}</td></tr>')
    return ('<table width="100%" cellspacing="0" cellpadding="2" style="margin:6px 0;">'
            + "".join(rows) + "</table>")


def _is_block_start(lines, index: int) -> bool:
    line = lines[index]
    stripped = line.strip()
    if not stripped:
        return True
    if (_FENCE_RE.match(line) or _HEADING_RE.match(stripped)
            or _HR_RE.match(stripped) or _LIST_RE.match(line)
            or stripped.startswith(">")):
        return True
    return (stripped.startswith("|") and index + 1 < len(lines)
            and bool(_TABLE_SEP_RE.match(lines[index + 1])))


def render_markdown(text: str, colors: dict) -> str:
    """Разметка ответа агента: заголовки, списки, код, таблицы, цитаты."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    total = len(lines)
    out = []
    index = 0

    while index < total:
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)[:3]
            lang = fence.group(2)
            index += 1
            code_lines = []
            while index < total and not lines[index].strip().startswith(marker):
                code_lines.append(lines[index])
                index += 1
            if index < total:
                index += 1
            out.append(render_code_block("\n".join(code_lines), lang, colors))
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            level = len(heading.group(1))
            size = {1: 15, 2: 13, 3: 12}.get(level, 11)
            out.append(
                f'<p style="margin:12px 0 6px 0; font-size:{size}pt; font-weight:700; '
                f'color:{colors["heading"]};">{render_inline(heading.group(2), colors)}</p>')
            index += 1
            continue

        if _HR_RE.match(stripped):
            out.append(f'<p style="margin:10px 0; color:{colors["border"]};">'
                       "──────────────</p>")
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines = []
            while index < total and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].lstrip())
                index += 1
            inner = render_markdown("\n".join(quote_lines), colors)
            out.append('<table width="100%" cellspacing="0" cellpadding="8" '
                       'style="margin:8px 0;">'
                       f'<tr><td bgcolor="{colors["quote_bg"]}">{inner}</td></tr></table>')
            continue

        if (stripped.startswith("|") and index + 1 < total
                and _TABLE_SEP_RE.match(lines[index + 1])):
            rows = [_split_table_row(line)]
            index += 2
            while index < total and lines[index].strip().startswith("|"):
                rows.append(_split_table_row(lines[index]))
                index += 1
            out.append(_render_table(rows, colors))
            continue

        if _LIST_RE.match(line):
            items = []
            while index < total:
                match = _LIST_RE.match(lines[index])
                if not match:
                    break
                indent = len(match.group(1).replace("\t", "    "))
                marker = match.group(2)
                ordered = marker[0].isdigit()
                number = marker.rstrip(".)")
                items.append((indent // 2, ordered, number,
                              render_inline(match.group(3), colors)))
                index += 1
            out.append(_render_list(items, colors))
            continue

        paragraph = [stripped]
        index += 1
        while index < total and not _is_block_start(lines, index):
            paragraph.append(lines[index].strip())
            index += 1
        out.append('<p style="margin:6px 0;">'
                   + render_inline("\n".join(paragraph), colors) + "</p>")

    return "".join(out)


def render_message(role: str, text: str, colors: dict) -> str:
    """Сообщение целиком как одна карточка: подсветка не рвётся на коде."""
    is_user = (role == "user")
    background = colors["user_bubble"] if is_user else colors["agent_bubble"]
    accent = colors["accent"] if is_user else colors["success"]
    title = "Вы" if is_user else "AI"
    body = render_markdown(text, colors) or (
        '<p style="margin:0; color:{0};">&nbsp;</p>'.format(colors["muted"]))
    return (
        '<table width="100%" cellspacing="0" cellpadding="12" '
        'style="margin:0 0 10px 0;">'
        f'<tr><td bgcolor="{background}">'
        f'<p style="margin:0 0 6px 0; font-size:9pt; font-weight:700; '
        f'color:{accent};">{title}</p>'
        f'{body}</td></tr></table>'
    )
