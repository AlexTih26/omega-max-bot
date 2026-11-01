"""Доставка выгрузок Склад Мастер в личные сообщения MAX.

Файл уходит тому, кто нажал кнопку в mini-app: нативные методы MAX в WebView
не всегда открывают скачивание, а вложение в переписке открывается штатно и
остаётся в истории чата.
"""

from __future__ import annotations

import logging

from aiohttp import ClientSession, FormData
from maxapi import Bot
from maxapi.enums.upload_type import UploadType
from maxapi.types.attachments.upload import AttachmentPayload, AttachmentUpload

logger = logging.getLogger(__name__)

_bot: Bot | None = None

KIND_LABELS = {
    "summary": "сводка по складу: приход, расход и остаток",
    "full": "полная выгрузка: сводка, приходы, история, перемещения",
}

# Книга xlsx — это zip-контейнер, и по содержимому он совпадает с docx и pptx.
# Готовый загрузчик берёт первое совпадение и отдаёт файл как документ Word,
# поэтому MIME и расширение задаются явно.
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def bot_ready() -> bool:
    return _bot is not None


async def _upload_xlsx(bot: Bot, payload: bytes, file_name: str) -> str:
    """Загрузить книгу как Excel, не доверяя автоопределению типа."""
    upload = await bot.get_upload_url(UploadType.FILE)
    url = getattr(upload, "url", None)
    if not url:
        raise RuntimeError("MAX не выдал адрес загрузки")
    form = FormData(quote_fields=False)
    form.add_field(
        name="data",
        value=payload,
        filename=file_name,
        content_type=XLSX_MIME,
    )
    session = getattr(bot, "session", None)
    if session is not None and not session.closed:
        async with session.post(url=url, data=form) as response:
            return await response.text()
    async with ClientSession() as temp_session:
        async with temp_session.post(url=url, data=form) as response:
            return await response.text()


async def send_export_to_user(
    *,
    max_id: int,
    kind: str,
    file_name: str,
) -> tuple[bool, str]:
    """Собрать файл и отправить его в личные сообщения нажавшему.

    Возвращает пару «успех» и текст ошибки для ответа API.
    """
    import json

    bot = _bot
    if bot is None:
        logger.warning("Склад Мастер: бот не инициализирован для отправки файла")
        return False, "Бот ещё не готов, попробуйте через минуту"
    try:
        import sklad_master_export as export
    except Exception:
        logger.exception("Склад Мастер: модуль экспорта недоступен")
        return False, "Экспорт в Excel не настроен на сервере"
    try:
        if kind == "full":
            payload = export.build_movements_workbook()
        else:
            payload = export.build_summary_workbook()
    except Exception:
        logger.exception("Склад Мастер: не удалось собрать файл для отправки")
        return False, "Не удалось сформировать файл"
    if not file_name.lower().endswith(".xlsx"):
        file_name = f"{file_name}.xlsx"
    try:
        raw = await _upload_xlsx(bot, payload, file_name)
        uploaded = json.loads(raw)
        token = uploaded.get("token")
        if not token:
            raise RuntimeError(f"MAX не вернул токен: {raw[:200]}")
    except Exception:
        logger.exception("Склад Мастер: не удалось загрузить файл в MAX")
        return False, "MAX не принял файл, попробуйте ещё раз"
    attachment = AttachmentUpload(
        type=UploadType.FILE, payload=AttachmentPayload(token=token)
    )
    label = KIND_LABELS.get(kind, "выгрузка Склад Мастер")
    try:
        await bot.send_message(
            user_id=int(max_id),
            text=f"📊 Склад Мастер · {label}",
            attachments=[attachment],
        )
    except Exception:
        logger.exception("Склад Мастер: не удалось отправить файл user_id=%s", max_id)
        return False, "MAX не принял файл, попробуйте ещё раз"
    logger.info("Склад Мастер: выгрузка %s отправлена в личку max_id=%s", kind, max_id)
    return True, ""

