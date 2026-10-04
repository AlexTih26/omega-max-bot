"""Read-only чтение старой SQLite-Таксимо для нового контура.

Новая PWA Таксимо подтягивает отсюда только справочники (вагоны, машины) и
оперативную картину площадки (слоты, плиты, сессии, историю отправок), пока
операторы ещё работают в старой версии.

Здесь НЕТ импортов старого слоя Таксимо (``taksimo_store`` и т.п.) и НЕТ ни
одной записи: подключение открывается строго ``mode=ro`` и сразу закрывается.
Старая Таксимо остаётся рабочей и нетронутой.

После «даты X» перехода и перевода старой базы в архив этот модуль можно
заменить архивным снимком, не меняя интерфейс функций.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "taksimo.db"


def _connect_ro() -> sqlite3.Connection | None:
    """Открыть короткое read-only подключение; None, если базы нет.

    ``mode=ro`` гарантирует отсутствие записи и, вместе с немедленным
    ``close()`` в вызывающем коде, не держит долгих блокировок на рабочей БД.
    """
    if not DB_PATH.is_file():
        logger.warning("Таксимо legacy: база %s не найдена", DB_PATH)
        return None
    connection = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro",
        uri=True,
        timeout=2,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _rows(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    connection = _connect_ro()
    if connection is None:
        return []
    try:
        return [dict(row) for row in connection.execute(query, params)]
    finally:
        connection.close()


def list_legacy_vehicles(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Справочник машин: plate / brand / driver / active, порядок как в старой."""
    where = "" if include_inactive else " WHERE active = 1"
    return _rows(
        f"""SELECT plate, brand, driver, active, sort_order
            FROM vehicles{where} ORDER BY sort_order, id"""
    )


def list_legacy_wagons(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Справочник вагонов: number / active / stage / planned_zone / slot_id."""
    where = "" if include_inactive else " WHERE active = 1"
    return _rows(
        f"""SELECT number, active, sort_order, stage, planned_zone, slot_id
            FROM wagon_pool{where} ORDER BY sort_order, id"""
    )


def list_legacy_wagon_slots() -> list[dict[str, Any]]:
    """Позиции вагонов: зона + слот + номер вагона (тупики старой площадки)."""
    return _rows(
        """SELECT zone, slot_index, wagon_number, scheme_code, has_box
           FROM wagon_slots ORDER BY zone, slot_index"""
    )


def list_legacy_slabs() -> list[dict[str, Any]]:
    """Плиты на площадке: буква/номер, координаты, зона, вагон."""
    return _rows(
        """SELECT id, letter, number, suffix, pos_x, pos_y, platform_zone,
                   wagon_number, on_yard
           FROM slabs ORDER BY platform_zone, pos_y, pos_x"""
    )


def list_legacy_yard_slabs() -> list[dict[str, Any]]:
    """Плиты, лежащие на площадке старой Таксимо (on_yard = 1).

    Используется новой площадкой, чтобы показать живую картину старой площадки,
    пока операторы ещё работают в старой версии.
    """
    return _rows(
        """SELECT letter, number, pos_x, pos_y, platform_zone, wagon_number
           FROM slabs WHERE on_yard = 1 ORDER BY platform_zone, pos_y, pos_x"""
    )


def list_legacy_sessions() -> list[dict[str, Any]]:
    """Сессии выгрузки старой Таксимо (текущая работа площадки)."""
    return _rows(
        """SELECT id, unload_date, trn, vehicle_id, driver, status, operator,
                   crane_start, crane_end
           FROM unload_sessions ORDER BY id DESC"""
    )


def list_legacy_wagon_history() -> list[dict[str, Any]]:
    """Стартовый слепок истории отправок вагонов (цикл Таксимо -> Кодар -> БТС).

    Дальше жизненный цикл вагона новая Таксимо ведёт сама (tn_events), не
    копируя эту историю.
    """
    return _rows(
        """SELECT id, wagon_number, slot_zone, slot_index, slab_count,
                   customer, status, return_status, return_target_zone,
                   return_actual_zone, dispatched_at, received_at
           FROM wagon_dispatches ORDER BY dispatched_at DESC, id DESC"""
    )