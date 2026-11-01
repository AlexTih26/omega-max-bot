#!/usr/bin/env python3
"""Собрать страницы помощи из markdown-файлов docs/.

Страницы кладутся в web/ и раздаются Nginx: инструкции и гид-хаб открываются
с телефона. Источник — markdown, поэтому правки вносятся в один файл, а
страницы пересобираются этим скриптом.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Склад Мастер — инструкция</title>
  <link rel="stylesheet" href="/style.css">
  <link rel="stylesheet" href="/sklad-master.css">
  <style>
    .sm-help {{ max-width: 760px; margin: 0 auto; padding: 16px 14px 40px; }}
    .sm-help h1 {{ font-size: 22px; margin: 8px 0 14px; }}
    .sm-help h2 {{ font-size: 18px; margin: 22px 0 10px; }}
    .sm-help h3 {{ font-size: 16px; margin: 16px 0 8px; }}
    .sm-help p {{ margin: 8px 0; line-height: 1.5; }}
    .sm-help hr {{ border: 0; border-top: 1px solid rgba(255,255,255,.12); margin: 18px 0; }}
    .sm-help table {{ width: 100%; border-collapse: collapse; margin: 10px 0 14px; font-size: 14px; }}
    .sm-help th, .sm-help td {{ border: 1px solid rgba(255,255,255,.14); padding: 7px 9px; text-align: left; vertical-align: top; }}
    .sm-help th {{ background: rgba(255,255,255,.07); font-weight: 700; }}
    .sm-help blockquote {{ margin: 10px 0; padding: 10px 12px; border-left: 3px solid var(--sm-accent, #d2a24c); background: rgba(255,255,255,.05); border-radius: 0 10px 10px 0; }}
    .sm-help .li {{ margin: 5px 0; padding-left: 4px; }}
    .sm-help .back {{ display: inline-block; margin-bottom: 10px; color: var(--sm-accent, #d2a24c); text-decoration: none; }}
  </style>
</head>
<body>
  <div class="sm-help">
    <a class="back" href="/sklad-master.html">← Вернуться в Склад Мастер</a>
{body}
  </div>
</body>
</html>
"""


def inline(text: str) -> str:
    """Экранировать и превратить разметку внутри строки."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    # Ссылки markdown: [подпись](адрес) — превращаются в обычный anchor.
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        escaped,
    )
    escaped = re.sub(
        r"\[([^\]]+)\]\((?!https?://)([^)\s]+)\.md\)",
        r'<span class="md-link">\1</span>',
        escaped,
    )
    escaped = re.sub(
        r"(?<![\"=])(https?://[^\s<\"]+)",
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        escaped,
    )
    return escaped


def convert(markdown: str) -> str:
    out: list[str] = []
    table_open = False
    header_done = False
    for raw in markdown.split("\n"):
        line = raw.rstrip()
        if line.startswith("|"):
            cells = [piece.strip() for piece in line.strip("|").split("|")]
            if all(set(piece) <= set("-: ") for piece in cells if piece):
                continue
            if not table_open:
                out.append("<table>")
                table_open = True
                header_done = False
            tag = "th" if not header_done else "td"
            row = "".join(f"<{tag}>{inline(piece)}</{tag}>" for piece in cells)
            out.append(f"<tr>{row}</tr>")
            header_done = True
            continue
        if table_open:
            out.append("</table>")
            table_open = False
        if not line:
            continue
        if line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("---"):
            out.append("<hr>")
        elif line.startswith("> "):
            out.append(f"<blockquote>{inline(line[2:])}</blockquote>")
        elif re.match(r"^\d+\.\s", line) or line.startswith("- "):
            out.append(f'<p class="li">{inline(line)}</p>')
        else:
            out.append(f"<p>{inline(line)}</p>")
    if table_open:
        out.append("</table>")
    return "\n    ".join(out)


def build(source: str, target: str, title: str, back: str) -> Path:
    path = ROOT / "web" / target
    markdown = (ROOT / "docs" / source).read_text(encoding="utf-8")
    page = (
        TEMPLATE.format(body=convert(markdown))
        .replace("<title>Склад Мастер — инструкция</title>", f"<title>{html.escape(title)}</title>")
        .replace('href="/sklad-master.html">← Вернуться в Склад Мастер</a>', back)
    )
    path.write_text(page, encoding="utf-8")
    return path


def main() -> int:
    pages = (
        (
            "sklad-master-guide.md",
            "sklad-master-help.html",
            "Склад Мастер — инструкция",
            '<a class="back" href="/sklad-master.html">← Вернуться в Склад Мастер</a>',
        ),
        (
            "README.md",
            "help.html",
            "Инструкции и документация",
            '<a class="back" href="/">← На главную</a>',
        ),
    )
    for source, target, title, back in pages:
        path = build(source, target, title, back)
        print(f"Собрано: {path} ({path.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
