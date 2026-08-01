"""Поведение ИИ-помощника OMEGA: промпт, заглушка, быстрые ответы."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from taksimo_notify import format_daily_report
from taksimo_store import search_slabs, stats_for_date

WORK_BOT_REPLY = (
    "Я OMEGA — рабочий бот.\n"
    "🏗 Таксимо: https://avtmsk.ru/taksimo.html\n"
    "🔧 FOTON: https://avtmsk.ru/work.html\n"
    "🤖 Сайт: https://max.avtmsk.ru/\n\n"
    "Вопрос по площадке — в чат отчётов.\n"
    "ИИ: напишите /ai и вопрос.\n"
    "Примеры: «Где плита A4495?» · «Как записать выгрузку?» · «Сводка за сегодня»"
)

AI_HELP = (
    "ИИ OMEGA — служебный режим.\n"
    "Формат: /ai ваш вопрос\n\n"
    "Примеры:\n"
    "• /ai Где плита A4495?\n"
    "• /ai Как записать выгрузку?\n"
    "• /ai Сводка за сегодня"
)

DEFAULT_SYSTEM_PROMPT = """Ты OMEGA — служебный помощник OMEGA AI LAB.

Сфера: логистика ЖБИ на площадке Таксимо, выгрузки, реестр, Excel, сервис FOTON, рабочие сервисы avtmsk.ru и max.avtmsk.ru.

Тон: сухой, деловой, без эмоций и шуток. Отвечай по-русски, кратко (3–6 предложений или список).

Делай:
- инструкции по записи выгрузки в Таксимо (машина, ТРН, плиты, X/Y, кран, стропальщики);
- пояснения по пометкам плит (к, а, тк, скол);
- навигация: taksimo.html, work.html, меню avtmsk.ru, Excel-экспорт.

Не делай:
- свободная болтовня, творчество, политика, медицина, юриспруденция;
- не выдумывай номера плит, координаты X/Y, данные выгрузок — если факт не передан в вопросе, скажи открыть Таксимо или чат отчётов;
- не притворяйся человеком.

Если вопрос вне сферы — один отказ и ссылки на сервисы."""

_SLAB_QUERY = re.compile(
    r"(?:где\s+(?:плита\s+)?|найди\s+(?:плиту\s+)?|плита\s+)"
    r"([A-FK]\s*\d+|\d{3,})",
    re.IGNORECASE,
)
_SUMMARY_QUERY = re.compile(
    r"сводк\w*|итог\w*\s+за\s+(?:сегодня|день)|отч[её]т\s+за\s+(?:сегодня|день)",
    re.IGNORECASE,
)
_HOWTO_QUERY = re.compile(
    r"как\s+(?:записать|внести|оформить|сохранить)\s+выгрузк",
    re.IGNORECASE,
)


def _today_iso() -> str:
    try:
        tz = ZoneInfo("Europe/Moscow")
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date().isoformat()


def _normalize_slab_query(raw: str) -> str:
    q = raw.strip().upper().replace("ПЛИТА", "").strip()
    m = re.match(r"^([A-FK])\s*(\d+)$", q)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return q


def try_builtin_answer(text: str) -> str | None:
    t = text.strip()
    if not t:
        return None

    if _HOWTO_QUERY.search(t):
        return (
            "Запись выгрузки в Таксимо:\n"
            "1. Откройте https://avtmsk.ru/taksimo.html\n"
            "2. Вкладка «Выгрузка» — дата, машина, ТРН, водитель\n"
            "3. Плиты: буква, номер, X и Y (1–10), пометка при необходимости\n"
            "4. Кран: время с/до; стропальщики — сумма\n"
            "5. «Сохранить выгрузку» — отчёт уйдёт в чат MAX\n"
            "Место на плане: вкладка «План X/Y», тап по ячейке."
        )

    if _SUMMARY_QUERY.search(t):
        today = _today_iso()
        parts = format_daily_report(today)
        return parts[0] if parts else "Данных за сегодня нет."

    m = _SLAB_QUERY.search(t)
    if m:
        q = _normalize_slab_query(m.group(1))
    elif re.match(r"^[A-FK]\s*\d{3,}$", t, re.I):
        q = _normalize_slab_query(t)
    else:
        q = ""

    if q:
        hits = search_slabs(q, limit=8)
        if not hits:
            return f"Плита {q}: на площадке не найдена (on_yard). Проверьте Таксимо или журнал."
        lines = [f"Плита {q} — на площадке:"]
        for s in hits:
            place = f"{s['pos_x']}/{s['pos_y']}"
            if s.get("suffix"):
                place += s["suffix"]
            lines.append(
                f"• {s['letter']}{s['number']} → {place}, "
                f"выгрузка {s.get('unload_date') or '—'}, "
                f"{s.get('vehicle_plate') or '—'}"
            )
        return "\n".join(lines)

    if re.fullmatch(r"стат\w*|сводк\w*", t, re.I):
        today = _today_iso()
        st = stats_for_date(today)
        return f"Сегодня ({today}): {st['sessions']} выгрузок, {st['slabs']} плит."

    return None
