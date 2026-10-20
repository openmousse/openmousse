"""回出去的字：按渠道的长度分段（不切断代码块）、Markdown 转成渠道认的格式（Telegram 的 HTML、邮件的纯文字），转不了退回纯文字。

分段（chunks）：先把 Markdown 切成不可分的几块（代码块、表格、一段话），一块块往一段里塞；塞不下的那一块再拆：
  代码块在段尾关上、下一段用同样的语言重新打开（照 OpenClaw 的 chunk.ts 和 Hermes 的 balance_fences_across_chunks），
  一段话先在换行、再在句末、再在空格处断，实在不行硬切。长度按 UTF-16 算（Telegram 的 4096 是按它数的）。
  切块的办法改写自 Hermes（NousResearch/hermes-agent，MIT，Copyright (c) 2025 Nous Research）commit cc6b1f29 的
  gateway/platforms/helpers.py（split_markdown_atoms、split_at_paragraph_boundary），许可全文见仓库根目录的 THIRD_PARTY_NOTICES。
Telegram 的 HTML（telegram_html）：markdown-it 解析（requirements 里本来就有），只用 Telegram 认的标签：b i s u code pre a blockquote，
  照 OpenClaw 的 format.ts：标题加粗成一行、列表写成「• 」「1. 」、分隔线「───」、表格放进 pre；链接只认 http(s) / tg / mailto / tel，
  别的只留字；& < > 转义，属性里的 " 也转。Telegram 说 can't parse entities 时退回纯文字（plain）。
纯文字（plain）：邮件和退路用：去掉强调的记号，链接写成「字 (网址)」，代码块原样缩进四格，引用前面加「> 」。
"""
from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt

FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
SENTENCE_END = re.compile(r"[。！？.!?…](?:[”’」』）)\]]*)(?=\s|$)")
LINK_OK = re.compile(r"^(?:https?://|tg://|mailto:|tel:)", re.I)
PARSE_ERROR = re.compile(r"can't parse entities|parse entities|find end of the entity|can't parse InputRichBlock|unsupported start tag", re.I)

_md: MarkdownIt | None = None


def u16(s: str) -> int:
    """按 UTF-16 数的长度（Telegram 的上限按它数：表情这类占两个）。"""
    return len(s.encode("utf-16-le")) // 2


def _prefix_within(s: str, budget: int) -> int:
    """s 里 UTF-16 长度不超过 budget 的最长前缀有几个字（按 Python 的字数）。"""
    if u16(s) <= budget:
        return len(s)
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if u16(s[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


# —— 分段 ————————————————————————————————————————————————————————————————————

def atoms(text: str) -> list[str]:
    """Markdown → 不可分的几块：代码块（``` 或 ~~~，到同样的记号关上为止）、表格（连着的 |…| 行）、一段话（空行隔开）。空行不属于哪一块。"""
    out: list[str] = []
    cur: list[str] = []
    fence: str | None = None

    def flush() -> None:
        if cur and "\n".join(cur).strip():
            out.append("\n".join(cur))
        cur.clear()

    for line in text.split("\n"):
        if fence is not None:
            cur.append(line)
            m = FENCE.match(line)
            if m and m.group(2)[0] == fence[0] and len(m.group(2)) >= len(fence) and not m.group(3).strip():
                fence = None
                flush()
            continue
        m = FENCE.match(line)
        if m:
            flush()
            fence = m.group(2)
            cur.append(line)
        elif not line.strip():
            flush()
        else:
            if cur and _is_row(cur[-1]) != _is_row(line):
                flush()
            cur.append(line)
    flush()
    return out


def _is_row(line: str) -> bool:
    s = line.strip()
    return len(s) > 1 and s.startswith("|") and s.endswith("|")


def _split_plain(text: str, limit: int) -> list[str]:
    """一大段话拆开：窗口里最后一个空行 → 换行 → 句末 → 空格（都要在窗口后半段），都没有就硬切。拼回去和原文一样（只去掉断口的空白）。"""
    out: list[str] = []
    rest = text
    while u16(rest) > limit:
        n = _prefix_within(rest, limit)
        window = rest[:n]
        cut = 0
        for finder in (lambda w: w.rfind("\n\n"), lambda w: w.rfind("\n"),
                       lambda w: max((m.end() for m in SENTENCE_END.finditer(w)), default=-1), lambda w: w.rfind(" ")):
            pos = finder(window)
            if pos >= n // 2:
                cut = pos
                break
        if cut <= 0:
            cut = n
        head, rest = rest[:cut].rstrip(), rest[cut:].lstrip(" \n")
        if head:
            out.append(head)
    if rest.strip():
        out.append(rest)
    return out


def _split_fence(block: str, limit: int) -> list[str]:
    """太长的代码块：每段都用原来那一行开头（带语言）、用同样的记号关上，中间按行塞（一行比一段还长的硬切）。"""
    lines = block.split("\n")
    opener = lines[0]
    m = FENCE.match(opener)
    marker = m.group(2) if m else "```"
    body = lines[1:]
    if body and FENCE.match(body[-1]) and not FENCE.match(body[-1]).group(3).strip():
        body = body[:-1]
    room = limit - u16(opener) - u16(marker) - 2
    if room < 20:  # 连开头关尾都放不下：不管代码块了，当一段话拆
        return _split_plain(block, limit)
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for line in body:
        pieces = [line] if u16(line) <= room else [line[i:i + _prefix_within(line[i:], room)] for i in _offsets(line, room)]
        for p in pieces:
            add = u16(p) + (1 if cur else 0)
            if cur and size + add > room:
                out.append("\n".join([opener, *cur, marker]))
                cur, size = [], 0
                add = u16(p)
            cur.append(p)
            size += add
    if cur or not out:
        out.append("\n".join([opener, *cur, marker]))
    return out


def _offsets(line: str, room: int) -> list[int]:
    out, i = [], 0
    while i < len(line):
        out.append(i)
        i += max(1, _prefix_within(line[i:], room))
    return out


def chunks(text: str, limit: int) -> list[str]:
    """Markdown 分成每段不超过 limit（UTF-16）的几段，代码块不切断（太长的关上、下一段重新打开）。空的 → []。"""
    text = (text or "").replace("\r\n", "\n").strip("\n")
    if not text.strip():
        return []
    if u16(text) <= limit:
        return [text]
    out: list[str] = []
    cur = ""
    for a in atoms(text):
        cand = f"{cur}\n\n{a}" if cur else a
        if u16(cand) <= limit:
            cur = cand
            continue
        if cur:
            out.append(cur)
            cur = ""
        if u16(a) <= limit:
            cur = a
            continue
        parts = _split_fence(a, limit) if FENCE.match(a.split("\n", 1)[0]) else _split_plain(a, limit)
        out += parts[:-1]
        cur = parts[-1] if parts else ""
    if cur:
        out.append(cur)
    return out


# —— Markdown → Telegram 的 HTML / 纯文字 ————————————————————————————————————————

def parser() -> MarkdownIt:
    global _md
    if _md is None:
        _md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(["table", "strikethrough"])
    return _md


def esc(s: str) -> str:
    return html.escape(s, quote=False)


class _Render:
    """markdown-it 的 token → Telegram HTML（html=True）或纯文字（html=False）。"""

    def __init__(self, as_html: bool, bullet: str = "• ") -> None:
        self.html, self.bullet = as_html, bullet
        self.out: list[str] = []
        self.lists: list[list] = []   # [是不是有序, 下一个号]
        self.quote = 0
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None

    def w(self, s: str) -> None:
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.out.append(s)

    def block_end(self, n: int = 2) -> None:
        if self.cell is not None:
            return
        text = "".join(self.out)
        trail = len(text) - len(text.rstrip("\n"))
        if trail < n:
            self.out.append("\n" * (n - trail))

    def inline(self, children: list) -> str:
        parts: list[str] = []
        links: list[bool] = []
        for t in children or []:
            ty = t.type
            if ty == "text":
                parts.append(esc(t.content) if self.html else t.content)
            elif ty in ("softbreak", "hardbreak"):
                parts.append("\n")
            elif ty == "code_inline":
                parts.append(f"<code>{esc(t.content)}</code>" if self.html else t.content)
            elif ty in ("strong_open", "strong_close"):
                parts.append(("<b>" if ty.endswith("open") else "</b>") if self.html else "")
            elif ty in ("em_open", "em_close"):
                parts.append(("<i>" if ty.endswith("open") else "</i>") if self.html else "")
            elif ty in ("s_open", "s_close"):
                parts.append(("<s>" if ty.endswith("open") else "</s>") if self.html else "")
            elif ty == "link_open":
                href = str(t.attrGet("href") or "")
                ok = bool(LINK_OK.match(href))
                links.append(ok)
                if self.html and ok:
                    parts.append(f'<a href="{html.escape(href, quote=True)}">')
                elif not self.html:
                    parts.append("\x00" + href + "\x00")  # 记下网址，收尾时变成「字 (网址)」
            elif ty == "link_close":
                ok = links.pop() if links else False
                if self.html and ok:
                    parts.append("</a>")
                elif not self.html:
                    parts.append("\x01")
            elif ty == "image":
                alt = "".join(c.content for c in t.children or [] if c.type == "text") or "image"
                parts.append(esc(f"[{alt}]") if self.html else f"[{alt}]")
            elif ty in ("html_inline",):
                parts.append(esc(t.content) if self.html else t.content)
            else:
                parts.append(esc(t.content) if self.html and t.content else t.content or "")
        s = "".join(parts)
        if not self.html:
            s = re.sub(r"\x00([^\x00]*)\x00([^\x01]*)\x01", lambda m: m.group(2) if not m.group(1) or m.group(1) in m.group(2) or
                       m.group(2) == m.group(1) else f"{m.group(2)} ({m.group(1)})", s)
        return s

    def render(self, tokens: list) -> str:
        i = 0
        while i < len(tokens):
            t = tokens[i]
            ty = t.type
            if ty == "heading_open":
                text = self.inline(tokens[i + 1].children)
                self.w(f"<b>{text}</b>" if self.html else text)
                self.block_end()
                i += 3
                continue
            if ty == "paragraph_open":
                text = self.inline(tokens[i + 1].children)
                if self.quote and not self.html:
                    text = "\n".join("> " + ln for ln in text.split("\n"))
                if self.lists:  # 列表项里第二段以后：缩进到记号后面
                    pad = "  " * len(self.lists)
                    text = pad + text.replace("\n", "\n" + pad)
                self.w(text)
                self.block_end(1 if self.lists else 2)
                i += 3
                continue
            if ty in ("bullet_list_open", "ordered_list_open"):
                start = int(t.attrGet("start") or 1) if ty == "ordered_list_open" else 0
                self.lists.append([ty == "ordered_list_open", start])
            elif ty in ("bullet_list_close", "ordered_list_close"):
                self.lists.pop()
                if not self.lists:
                    self.block_end()
            elif ty == "list_item_open":
                ordered, n = self.lists[-1]
                indent = "  " * (len(self.lists) - 1)
                self.w(indent + (f"{n}. " if ordered else self.bullet))
                if ordered:
                    self.lists[-1][1] = n + 1
                # 列表项里的第一段直接接在记号后面
                if i + 2 < len(tokens) and tokens[i + 1].type == "paragraph_open":
                    text = self.inline(tokens[i + 2].children)
                    self.w(text.replace("\n", "\n" + indent + "  "))
                    self.block_end(1)
                    i += 4
                    continue
            elif ty == "list_item_close":
                self.block_end(1)
            elif ty in ("fence", "code_block"):
                lang = (t.info or "").strip().split()[0] if ty == "fence" and (t.info or "").strip() else ""
                code = t.content.rstrip("\n")
                if self.html:
                    cls = f' class="language-{html.escape(re.sub(r"[^A-Za-z0-9_+#.-]", "", lang), quote=True)}"' if lang else ""
                    self.w(f"<pre><code{cls}>{esc(code)}</code></pre>")
                else:
                    self.w("\n".join("    " + ln if ln else "" for ln in code.split("\n")))
                self.block_end()
            elif ty == "blockquote_open":
                self.quote += 1
                if self.html:
                    self.w("<blockquote>")
            elif ty == "blockquote_close":
                self.quote -= 1
                if self.html:
                    self.out[-1] = self.out[-1].rstrip("\n") if self.out else ""
                    self.w("</blockquote>")
                self.block_end()
            elif ty == "hr":
                self.w("───")
                self.block_end()
            elif ty == "table_open":
                self.table = []
            elif ty == "tr_open" and self.table is not None:
                self.table.append([])
            elif ty in ("th_open", "td_open"):
                self.cell = []
            elif ty in ("th_close", "td_close"):
                if self.table is not None and self.cell is not None:
                    self.table[-1].append("".join(self.cell).strip())
                self.cell = None
            elif ty == "inline" and self.cell is not None:
                self.cell.append(self.inline(t.children))
            elif ty == "table_close":
                rows = [" | ".join(r) for r in self.table or []]
                body = "\n".join(rows)
                self.table = None
                self.w(f"<pre>{body}</pre>" if self.html else body)
                self.block_end()
            elif ty == "html_block":
                self.w(esc(t.content) if self.html else t.content)
                self.block_end()
            i += 1
        return re.sub(r"\n{3,}", "\n\n", "".join(self.out)).strip()


def _clean(md: str) -> str:
    return re.sub(r"[\x00\x01]", "", (md or "").replace("\r\n", "\n"))


def telegram_html(md: str) -> str:
    """Markdown → Telegram 的 HTML（parse_mode HTML）。解析出错（不该有）就整段转义成纯文字。"""
    try:
        return _Render(True).render(parser().parse(_clean(md)))
    except Exception:  # noqa: BLE001 — 解析器出了怪错：退回转义的原文，至少发得出去
        return esc(md or "")


def plain(md: str, bullet: str = "• ") -> str:
    """Markdown → 纯文字（Telegram 不认格式时的退路；邮件用 bullet="- "）。"""
    try:
        return _Render(False, bullet).render(parser().parse(_clean(md)))
    except Exception:  # noqa: BLE001
        return md or ""


def md_escape(s: str) -> str:
    """拼进 Markdown 的别人的字（卡的标题、名字）：把会变成格式的记号转义掉。"""
    return re.sub(r"([\\`*_\[\]()#+!|~>])", r"\\\1", str(s or ""))


def clip(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"
