"""Парк вагонов, planned_zone и рейсы Кодар — расширение taksimo_store."""

from __future__ import annotations

import json
import sqlite3
import time

from taksimo_wagon_logic import analyze_fleet, analyze_wagon

import taksimo_store as store

_connect = store._connect
WAGON_DEAD_ENDS = store.WAGON_DEAD_ENDS
WAGON_ZONES = store.WAGON_ZONES
MAX_WAGON_SLABS = store.MAX_WAGON_SLABS
MAX_WAGONS_PER_DEAD_END = store.MAX_WAGONS_PER_DEAD_END
KODAR_TRANSIT_ZONE = store.KODAR_TRANSIT_ZONE
CUSTOMER_DELIVERED_ZONE = store.CUSTOMER_DELIVERED_ZONE
DEFAULT_CUSTOMER = store.DEFAULT_CUSTOMER
_slot_slabs = store._slot_slabs
_normalize_expected_blocks = store._normalize_expected_blocks
_slot_is_complete = store._slot_is_complete
_slab_label_compact = store._slab_label_compact

MAX_FLEET_WAGONS = 54
CYCLE_DESTINATION = "Кодар"
WAGON_STAGE_LABELS = {
    "available": "В парке",
    "at_slot": "В слоте",
    "ready": "Готов",
    "departed": "В Кодар",
    "returning": "Порожний · обратно",
}


def migrate_wagon_pool_fleet(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(wagon_pool)")}
    if "stage" not in cols:
        conn.execute(
            "ALTER TABLE wagon_pool ADD COLUMN stage TEXT NOT NULL DEFAULT 'available'"
        )
    if "planned_zone" not in cols:
        conn.execute(
            "ALTER TABLE wagon_pool ADD COLUMN planned_zone TEXT NOT NULL DEFAULT ''"
        )
    if "slot_id" not in cols:
        conn.execute("ALTER TABLE wagon_pool ADD COLUMN slot_id INTEGER")
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE wagon_pool ADD COLUMN updated_at REAL")


def _row_fleet_wagon(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    keys = row.keys()
    slot_id = row["slot_id"] if "slot_id" in keys else None
    slot_label = ""
    if slot_id:
        slot = conn.execute(
            "SELECT zone, slot_index FROM wagon_slots WHERE id = ?",
            (slot_id,),
        ).fetchone()
        if slot:
            slot_label = f"{slot['zone']} №{slot['slot_index']}"
    stage = (row["stage"] if "stage" in keys else "available") or "available"
    planned = (row["planned_zone"] if "planned_zone" in keys else "") or ""
    return {
        "number": row["number"],
        "active": bool(row["active"]),
        "sort_order": int(row["sort_order"]),
        "stage": stage,
        "stage_label": WAGON_STAGE_LABELS.get(stage, stage),
        "planned_zone": planned,
        "slot_id": slot_id,
        "slot_label": slot_label,
        "updated_at": row["updated_at"] if "updated_at" in keys else None,
    }


def list_wagon_fleet() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM wagon_pool
            WHERE active = 1
            ORDER BY sort_order, number
            """
        ).fetchall()
        return [_row_fleet_wagon(conn, row) for row in rows]


def add_fleet_wagons(
    numbers: list[str],
    *,
    stage: str = "available",
    planned_zone: str = "",
) -> tuple[int, str]:
    stage = (stage or "available").strip()
    zone = (planned_zone or "").strip().upper()
    if zone and zone not in WAGON_ZONES:
        return 0, "Тупик: ТУРАН или ГРУЗОВОЙ"
    added = 0
    now = time.time()
    with _connect() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM wagon_pool WHERE active = 1").fetchone()[0]
        for raw in numbers:
            num = "".join(ch for ch in (raw or "").strip() if ch.isdigit())
            if len(num) != 8:
                continue
            if existing + added >= MAX_FLEET_WAGONS:
                break
            cur = conn.execute(
                """
                INSERT INTO wagon_pool (number, active, sort_order, stage, planned_zone, updated_at)
                VALUES (
                    ?, 1,
                    (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM wagon_pool),
                    ?, ?, ?
                )
                ON CONFLICT(number) DO UPDATE SET
                    active = 1,
                    stage = excluded.stage,
                    planned_zone = CASE
                        WHEN excluded.planned_zone != '' THEN excluded.planned_zone
                        ELSE wagon_pool.planned_zone
                    END,
                    updated_at = excluded.updated_at
                """,
                (num, stage, zone, now),
            )
            if cur.rowcount:
                added += 1
        conn.commit()
    if added <= 0:
        return 0, "Ни одного вагона не добавлено"
    return added, f"Добавлено вагонов: {added}"


def update_wagon_planned_zone(wagon_number: str, planned_zone: str) -> dict:
    wagon_number = (wagon_number or "").strip()
    zone = (planned_zone or "").strip().upper()
    if zone not in WAGON_ZONES:
        raise ValueError("Тупик: ТУРАН или ГРУЗОВОЙ")
    if not wagon_number:
        raise ValueError("Укажите номер вагона")
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM wagon_pool WHERE number = ? AND active = 1",
            (wagon_number,),
        ).fetchone()
        if not row:
            raise ValueError(f"Вагон {wagon_number} нет в парке")
        stage = str(row["stage"] or "available")
        if stage == "at_slot":
            raise ValueError("Вагон в слоте — сначала освободите слот")
        if stage == "departed":
            raise ValueError("Вагон в пути в Кодар")
        if row["slot_id"]:
            raise ValueError("Вагон привязан к слоту")
        conn.execute(
            "UPDATE wagon_pool SET planned_zone = ?, updated_at = ? WHERE number = ?",
            (zone, now, wagon_number),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM wagon_pool WHERE number = ?", (wagon_number,)
        ).fetchone()
        return _row_fleet_wagon(conn, row)


def _sync_fleet_from_slots(conn: sqlite3.Connection) -> None:
    now = time.time()
    slots = conn.execute(
        "SELECT id, zone, wagon_number FROM wagon_slots WHERE wagon_number != ''"
    ).fetchall()
    active_numbers = {(s["wagon_number"] or "").strip() for s in slots}
    active_numbers.discard("")
    for s in slots:
        num = (s["wagon_number"] or "").strip()
        if not num:
            continue
        conn.execute(
            """
            INSERT INTO wagon_pool (number, active, sort_order, stage, planned_zone, slot_id, updated_at)
            VALUES (
                ?, 1,
                (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM wagon_pool),
                'at_slot', ?, ?, ?
            )
            ON CONFLICT(number) DO UPDATE SET
                stage = 'at_slot',
                planned_zone = excluded.planned_zone,
                slot_id = excluded.slot_id,
                updated_at = excluded.updated_at
            """,
            (num, s["zone"], s["id"], now),
        )
    conn.execute(
        """
        UPDATE wagon_pool
        SET stage = 'available', slot_id = NULL, updated_at = ?
        WHERE active = 1 AND stage = 'at_slot' AND number NOT IN (
            SELECT wagon_number FROM wagon_slots WHERE wagon_number != ''
        )
        """,
        (now,),
    )


def _apply_slot_wagon(
    conn: sqlite3.Connection,
    *,
    zone: str,
    slot_id: int,
    wagon_number: str,
    previous: str = "",
) -> None:
    now = time.time()
    prev = (previous or "").strip()
    new = (wagon_number or "").strip()
    if prev and prev != new:
        conn.execute(
            """
            UPDATE wagon_pool
            SET stage = 'available', slot_id = NULL, updated_at = ?
            WHERE number = ?
            """,
            (now, prev),
        )
    if new:
        conn.execute(
            """
            INSERT INTO wagon_pool (number, active, sort_order, stage, planned_zone, slot_id, updated_at)
            VALUES (
                ?, 1,
                (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM wagon_pool),
                'at_slot', ?, ?, ?
            )
            ON CONFLICT(number) DO UPDATE SET
                stage = 'at_slot',
                planned_zone = excluded.planned_zone,
                slot_id = excluded.slot_id,
                updated_at = excluded.updated_at
            """,
            (new, zone, slot_id, now),
        )


def _assignable_wagons_for_slot(
    conn: sqlite3.Connection,
    *,
    zone: str,
    slot_id: int,
    current_number: str = "",
) -> list[dict]:
    current = (current_number or "").strip()
    rows = conn.execute(
        """
        SELECT * FROM wagon_pool
        WHERE active = 1
          AND stage IN ('available', 'returning')
          AND (slot_id IS NULL OR slot_id = ?)
          AND (planned_zone = '' OR planned_zone = ?)
        ORDER BY sort_order, number
        """,
        (slot_id, zone),
    ).fetchall()
    out = []
    for row in rows:
        num = row["number"]
        if num == current:
            out.append({"number": num, "stage": row["stage"], "planned_zone": row["planned_zone"]})
            continue
        busy = conn.execute(
            "SELECT id FROM wagon_slots WHERE wagon_number = ? AND id != ?",
            (num, slot_id),
        ).fetchone()
        if busy:
            continue
        out.append(
            {
                "number": num,
                "stage": row["stage"],
                "planned_zone": row["planned_zone"] or "",
            }
        )
    if current and not any(w["number"] == current for w in out):
        out.insert(0, {"number": current, "stage": "at_slot", "planned_zone": zone})
    return out


def _wagons_in_dead_end(conn: sqlite3.Connection, zone: str) -> list[str]:
    rows = conn.execute(
        "SELECT wagon_number FROM wagon_slots WHERE zone = ? AND wagon_number != '' ORDER BY slot_index",
        (zone,),
    ).fetchall()
    return [(r["wagon_number"] or "").strip() for r in rows if (r["wagon_number"] or "").strip()]


def _yard_slabs_for_logistics(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, letter, number, pos_x, pos_y
        FROM slabs
        WHERE platform_zone = 'ХРАНЕНИЯ' AND on_yard = 1
        ORDER BY letter, number, id
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "letter": row["letter"],
            "number": row["number"],
            "label": f"{row['letter']} {row['number']}".strip(),
        }
        for row in rows
    ]


def wagon_plan() -> dict:
    with _connect() as conn:
        _sync_fleet_from_slots(conn)
        conn.commit()
        rows = conn.execute(
            """
            SELECT id, zone, slot_index, wagon_number, expected_blocks, updated_at
            FROM wagon_slots
            ORDER BY zone, slot_index
            """
        ).fetchall()
        dead_ends: dict[str, list[dict]] = {z: [] for z in WAGON_DEAD_ENDS}
        assignable: dict[str, list[dict]] = {z: [] for z in WAGON_DEAD_ENDS}
        wagons_in_zone: dict[str, list[str]] = {z: [] for z in WAGON_DEAD_ENDS}
        all_slots: list[dict] = []
        yard_slabs = _yard_slabs_for_logistics(conn)
        for row in rows:
            expected = _normalize_expected_blocks(row["expected_blocks"])
            wagon_number = (row["wagon_number"] or "").strip()
            zone = str(row["zone"])
            slot_id = int(row["id"])
            slabs = _slot_slabs(conn, zone, wagon_number)
            logistics = analyze_wagon(slabs, max_slabs=MAX_WAGON_SLABS, wagon_number=wagon_number)
            manual_complete = _slot_is_complete(expected, slabs)
            slot_data = {
                "id": slot_id,
                "zone": zone,
                "slot_index": row["slot_index"],
                "wagon_number": wagon_number,
                "expected_blocks": expected,
                "slabs": slabs,
                "slab_count": len(slabs),
                "is_complete": logistics["is_complete"] or manual_complete,
                "logistics": logistics,
                "updated_at": row["updated_at"],
            }
            dead_ends[zone].append(slot_data)
            all_slots.append(slot_data)
            assignable[zone].append(
                {
                    "slot_id": slot_id,
                    "slot_index": row["slot_index"],
                    "current_number": wagon_number,
                    "options": _assignable_wagons_for_slot(
                        conn,
                        zone=zone,
                        slot_id=slot_id,
                        current_number=wagon_number,
                    ),
                }
            )
            wagons_in_zone[zone] = _wagons_in_dead_end(conn, zone)
        fleet = [_row_fleet_wagon(conn, row) for row in conn.execute(
            "SELECT * FROM wagon_pool WHERE active = 1 ORDER BY sort_order, number"
        )]
        logistics_fleet = analyze_fleet(all_slots, yard_slabs, max_slabs=MAX_WAGON_SLABS)
    return {
        "max_wagons_per_dead_end": MAX_WAGONS_PER_DEAD_END,
        "max_slabs_per_wagon": MAX_WAGON_SLABS,
        "max_fleet_wagons": MAX_FLEET_WAGONS,
        "dead_ends": dead_ends,
        "fleet": fleet,
        "wagon_pool": fleet,
        "assignable": assignable,
        "wagons_in_zone": wagons_in_zone,
        "logistics": logistics_fleet,
        "stage_labels": WAGON_STAGE_LABELS,
        "cycle_destination": CYCLE_DESTINATION,
        "kodar_in_transit": list_kodar_in_transit(),
    }


def update_wagon_slot(
    slot_id: int,
    *,
    wagon_number: str | None = None,
    expected_blocks: list[str] | str | None = None,
) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, zone, slot_index, wagon_number FROM wagon_slots WHERE id = ?",
            (slot_id,),
        ).fetchone()
        if not row:
            raise KeyError("slot not found")
        prev_wagon = (row["wagon_number"] or "").strip()
        updates: list[str] = []
        params: list = []
        new_wagon = prev_wagon
        if wagon_number is not None:
            new_wagon = wagon_number.strip()
            if new_wagon:
                taken = conn.execute(
                    """
                    SELECT id FROM wagon_slots
                    WHERE zone = ? AND wagon_number = ? AND id != ?
                    """,
                    (row["zone"], new_wagon, slot_id),
                ).fetchone()
                if taken:
                    raise ValueError(f"Вагон {new_wagon} уже в другом слоте {row['zone']}")
                pool = conn.execute(
                    "SELECT stage, planned_zone FROM wagon_pool WHERE number = ? AND active = 1",
                    (new_wagon,),
                ).fetchone()
                if pool:
                    pz = (pool["planned_zone"] or "").strip()
                    if pz and pz != row["zone"]:
                        raise ValueError(
                            f"Вагон {new_wagon} запланирован в тупик {pz}, не {row['zone']}"
                        )
                    if pool["stage"] == "departed":
                        raise ValueError(f"Вагон {new_wagon} в пути в Кодар")
            updates.append("wagon_number = ?")
            params.append(new_wagon)
        if expected_blocks is not None:
            normalized = _normalize_expected_blocks(expected_blocks)
            updates.append("expected_blocks = ?")
            params.append(json.dumps(normalized, ensure_ascii=False))
        if not updates:
            raise ValueError("Нечего обновлять")
        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(slot_id)
        conn.execute(
            f"UPDATE wagon_slots SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        if wagon_number is not None:
            _apply_slot_wagon(
                conn,
                zone=str(row["zone"]),
                slot_id=slot_id,
                wagon_number=new_wagon,
                previous=prev_wagon,
            )
        conn.commit()
    plan = wagon_plan()
    for zone_slots in plan["dead_ends"].values():
        for slot in zone_slots:
            if slot["id"] == slot_id:
                return slot
    raise KeyError("slot not found")


def _msk_label_from_ts(ts: float | None) -> str:
    if not ts:
        return ""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.fromtimestamp(float(ts), ZoneInfo("Europe/Moscow")).strftime(
        "%d.%m.%Y %H:%M"
    )


def _parse_dispatch_blocks(raw: str) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _row_wagon_dispatch(row: sqlite3.Row) -> dict:
    blocks = _parse_dispatch_blocks(row["blocks_json"])
    vehicles = sorted({b.get("vehicle_plate", "").strip() for b in blocks if b.get("vehicle_plate")})
    labels = [b.get("label", "") for b in blocks if b.get("label")]
    return {
        "id": int(row["id"]),
        "wagon_number": row["wagon_number"],
        "slot_zone": row["slot_zone"],
        "slot_index": int(row["slot_index"]),
        "slab_count": int(row["slab_count"]),
        "blocks": blocks,
        "block_labels": labels,
        "vehicles": vehicles,
        "dispatched_at": row["dispatched_at"],
        "dispatched_at_label": _msk_label_from_ts(row["dispatched_at"]),
        "dispatched_by": row["dispatched_by"],
        "received_at": row["received_at"],
        "received_at_label": _msk_label_from_ts(row["received_at"]),
        "received_by": row["received_by"],
        "customer": row["customer"] or DEFAULT_CUSTOMER,
        "status": row["status"],
    }


def get_wagon_dispatch(dispatch_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM wagon_dispatches WHERE id = ?",
            (dispatch_id,),
        ).fetchone()
    return _row_wagon_dispatch(row) if row else None


def list_kodar_in_transit() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM wagon_dispatches
            WHERE status = 'in_transit'
            ORDER BY dispatched_at DESC
            """
        ).fetchall()
    return [_row_wagon_dispatch(row) for row in rows]


def list_wagon_dispatch_history(*, limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM wagon_dispatches
            ORDER BY dispatched_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_wagon_dispatch(row) for row in rows]


def dispatch_wagon_to_kodar(slot_id: int, *, operator: str = "") -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, zone, slot_index, wagon_number FROM wagon_slots WHERE id = ?",
            (slot_id,),
        ).fetchone()
        if not row:
            raise KeyError("slot not found")
        zone = str(row["zone"])
        slot_index = int(row["slot_index"])
        wagon_number = (row["wagon_number"] or "").strip()
        if not wagon_number:
            raise ValueError("В слоте нет вагона")

        slabs = _slot_slabs(conn, zone, wagon_number)
        if len(slabs) != MAX_WAGON_SLABS:
            raise ValueError(
                f"Нужно 9/9 блоков — сейчас {len(slabs)}/{MAX_WAGON_SLABS}"
            )

        logistics = analyze_wagon(
            slabs, max_slabs=MAX_WAGON_SLABS, wagon_number=wagon_number
        )
        if not logistics["is_complete"]:
            raise ValueError(
                "Вагон неполный — нужно кольцо 9/9 перед отправкой в Кодар"
            )

        in_flight = conn.execute(
            """
            SELECT id FROM wagon_dispatches
            WHERE wagon_number = ? AND status = 'in_transit'
            """,
            (wagon_number,),
        ).fetchone()
        if in_flight:
            raise ValueError(f"Вагон {wagon_number} уже в пути в Кодар")

        now = time.time()
        slab_payload: list[dict] = []
        slab_ids: list[int] = []
        for s in slabs:
            label = _slab_label_compact(s["letter"], s["number"])
            slab_payload.append(
                {
                    "id": s["id"],
                    "label": label,
                    "vehicle_plate": (s.get("vehicle_plate") or "").strip(),
                    "unload_date": s.get("unload_date") or "",
                }
            )
            slab_ids.append(int(s["id"]))

        cur = conn.execute(
            """
            INSERT INTO wagon_dispatches (
                wagon_number, slot_zone, slot_index, slab_count, blocks_json,
                dispatched_at, dispatched_by, customer, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'in_transit')
            """,
            (
                wagon_number,
                zone,
                slot_index,
                len(slab_ids),
                json.dumps(slab_payload, ensure_ascii=False),
                now,
                operator,
                DEFAULT_CUSTOMER,
            ),
        )
        dispatch_id = int(cur.lastrowid)

        for sid in slab_ids:
            conn.execute(
                """
                UPDATE slabs
                SET platform_zone = ?, on_yard = 0, wagon_dispatch_id = ?
                WHERE id = ?
                """,
                (KODAR_TRANSIT_ZONE, dispatch_id, sid),
            )

        conn.execute(
            "UPDATE wagon_slots SET wagon_number = '', updated_at = ? WHERE id = ?",
            (now, slot_id),
        )
        conn.execute(
            """
            UPDATE wagon_pool
            SET slot_id = NULL, stage = 'departed', updated_at = ?
            WHERE number = ?
            """,
            (now, wagon_number),
        )
        conn.commit()

    result = get_wagon_dispatch(dispatch_id)
    if result is None:
        raise RuntimeError("dispatch not found after save")
    return result


def confirm_kodar_received(
    wagon_number: str,
    *,
    operator: str = "",
    dispatch_id: int | None = None,
) -> dict:
    wagon_number = (wagon_number or "").strip()
    if not wagon_number and dispatch_id is None:
        raise ValueError("Укажите номер вагона")

    with _connect() as conn:
        if dispatch_id is not None:
            row = conn.execute(
                "SELECT * FROM wagon_dispatches WHERE id = ? AND status = 'in_transit'",
                (dispatch_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM wagon_dispatches
                WHERE wagon_number = ? AND status = 'in_transit'
                ORDER BY dispatched_at DESC LIMIT 1
                """,
                (wagon_number,),
            ).fetchone()
        if not row:
            raise ValueError(f"Нет вагона {wagon_number or dispatch_id} в пути в Кодар")

        did = int(row["id"])
        wagon_number = str(row["wagon_number"])
        now = time.time()

        conn.execute(
            """
            UPDATE slabs
            SET platform_zone = ?, customer = ?, on_yard = 0
            WHERE wagon_dispatch_id = ? AND platform_zone = ?
            """,
            (CUSTOMER_DELIVERED_ZONE, DEFAULT_CUSTOMER, did, KODAR_TRANSIT_ZONE),
        )
        conn.execute(
            """
            UPDATE wagon_dispatches
            SET status = 'delivered', received_at = ?, received_by = ?
            WHERE id = ?
            """,
            (now, operator, did),
        )
        conn.execute(
            """
            UPDATE wagon_pool
            SET stage = 'returning', updated_at = ?
            WHERE number = ?
            """,
            (now, wagon_number),
        )
        conn.commit()

    result = get_wagon_dispatch(did)
    if result is None:
        raise RuntimeError("dispatch not found after receive")
    return result
