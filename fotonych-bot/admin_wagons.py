"""Парк ж/д вагонов Таксimo — только супер-админ."""

from __future__ import annotations

from taksimo_store import (
    CYCLE_DESTINATION,
    MAX_FLEET_WAGONS,
    WAGON_STAGE_LABELS,
    add_fleet_wagons,
    list_wagon_fleet,
)


def wagon_fleet_payload() -> dict:
    fleet = list_wagon_fleet()
    return {
        "wagons": fleet,
        "count": len(fleet),
        "max_fleet_wagons": MAX_FLEET_WAGONS,
        "stage_labels": WAGON_STAGE_LABELS,
        "cycle_destination": CYCLE_DESTINATION,
    }


def add_wagons_admin(
    numbers: list[str],
    *,
    stage: str = "available",
    planned_zone: str = "",
) -> tuple[bool, str]:
    added, message = add_fleet_wagons(numbers, stage=stage, planned_zone=planned_zone)
    if added <= 0:
        return False, message
    return True, message
