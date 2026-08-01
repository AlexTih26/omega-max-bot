"""Логистика вагонов: ★ по вагону, ★★ по допам парка."""

from __future__ import annotations

RING_LETTERS = ("A", "B", "C", "D", "E", "F", "K")
EXTRA_LETTERS = ("A", "B", "C", "D", "E", "F")
RING_EXTRAS = 2
SCHEME1_WAGON_SLOTS = 9
SCHEME2_K_GOAL = 16
WAGON_DEAD_ENDS = ("ГРУЗОВОЙ", "ТУРАН")


def _letter_counts(slabs: list[dict]) -> dict[str, int]:
    counts = {letter: 0 for letter in RING_LETTERS}
    for slab in slabs:
        letter = (slab.get("letter") or "").strip().upper()
        if letter in counts:
            counts[letter] += 1
    return counts


def ring_is_complete(
    slabs: list[dict],
    *,
    max_slabs: int = SCHEME1_WAGON_SLOTS,
) -> bool:
    """Железное кольцо: 9 блоков, по одному A–K, ровно один K."""
    n = len(slabs)
    if n != max_slabs:
        return False
    counts = _letter_counts(slabs)
    if counts.get("K", 0) != 1:
        return False
    return all(counts[letter] >= 1 for letter in RING_LETTERS)


def missing_ring_letters(slabs: list[dict]) -> list[str]:
    counts = _letter_counts(slabs)
    return [letter for letter in RING_LETTERS if counts[letter] < 1]


def decompose_wagon_ring(
    slabs: list[dict],
    *,
    max_slabs: int = SCHEME1_WAGON_SLOTS,
) -> dict:
    """Целое кольцо A–K + допы A–F (только при полном кольце)."""
    counts = _letter_counts(slabs)
    n = len(slabs)
    k_count = counts.get("K", 0)
    missing = missing_ring_letters(slabs)
    complete = ring_is_complete(slabs, max_slabs=max_slabs)

    if not complete:
        return {
            "ring_complete": False,
            "extras": [],
            "missing_ring": missing,
            "slab_count": n,
            "k_count": k_count,
        }

    remaining = dict(counts)
    for letter in RING_LETTERS:
        remaining[letter] -= 1

    extras: list[str] = []
    for letter in EXTRA_LETTERS:
        extras.extend([letter] * remaining[letter])

    return {
        "ring_complete": True,
        "extras": extras,
        "missing_ring": [],
        "slab_count": n,
        "k_count": k_count,
    }


def analyze_wagon(
    slabs: list[dict],
    *,
    max_slabs: int = SCHEME1_WAGON_SLOTS,
    wagon_number: str = "",
) -> dict:
    """★ Подсказки по одному вагону — без номеров плит и без сводки по двору."""
    n = len(slabs)
    counts = _letter_counts(slabs)
    k_count = counts.get("K", 0)
    has_wagon = bool((wagon_number or "").strip())
    missing_ring = missing_ring_letters(slabs)
    is_complete = ring_is_complete(slabs, max_slabs=max_slabs)

    if is_complete:
        return {
            "scheme": "ring",
            "is_complete": True,
            "show_hint_star": False,
            "slab_count": n,
            "max_slabs": max_slabs,
            "k_count": k_count,
            "ring_letters_missing": [],
            "hints": [],
            "summary": "9/9 ✓",
        }

    hints: list[str] = []

    if k_count > 1:
        hints.append("Только один K на вагон")

    if n == 0:
        hints.append("До 9 блоков — кольцо A–K и 2 запасных из A–F")
    else:
        for letter in missing_ring:
            if len(hints) >= 3:
                break
            hints.append(f"Нужен {letter}")

        if len(hints) < 3 and not missing_ring and k_count == 1 and n < max_slabs:
            left = max_slabs - n
            hints.append(f"Ещё {left} запасных из A–F")

        if len(hints) < 3 and n == max_slabs and missing_ring:
            need = ", ".join(missing_ring[:3])
            hints.insert(0, f"Довезите {need} до полного кольца")

    return {
        "scheme": "ring",
        "is_complete": False,
        "show_hint_star": has_wagon,
        "slab_count": n,
        "max_slabs": max_slabs,
        "k_count": k_count,
        "ring_letters_missing": missing_ring,
        "hints": hints[:3],
        "summary": f"{n}/{max_slabs}",
    }


def _format_extra_letters(extras: list[str]) -> str:
    if not extras:
        return "—"
    return ", ".join(extras)


def _pool_missing_for_next_ring(pool: dict[str, int]) -> list[str]:
    return [letter for letter in EXTRA_LETTERS if pool[letter] < 1]


def analyze_fleet_extras(
    slots: list[dict],
    *,
    max_slabs: int = SCHEME1_WAGON_SLOTS,
    zone: str | None = None,
) -> dict:
    """★★ Допы по парку: целые кольца вычитаем, считаем запасные A–F и K до 16."""
    filtered = [
        slot
        for slot in slots
        if (slot.get("wagon_number") or "").strip()
        and (zone is None or str(slot.get("zone") or "") == zone)
    ]
    filtered.sort(
        key=lambda s: (str(s.get("zone") or ""), int(s.get("slot_index") or 0))
    )

    wagon_extras: list[dict] = []
    pool = {letter: 0 for letter in EXTRA_LETTERS}
    complete_rings = 0
    total_k = 0
    k_in_complete_rings = 0
    hints: list[str] = []

    for slot in filtered:
        slabs = slot.get("slabs") or []
        decomposed = decompose_wagon_ring(slabs, max_slabs=max_slabs)
        counts = _letter_counts(slabs)
        total_k += counts.get("K", 0)

        if decomposed["ring_complete"]:
            complete_rings += 1
            k_in_complete_rings += 1
            extras = decomposed["extras"]
            wagon_extras.append(
                {
                    "slot_index": int(slot.get("slot_index") or 0),
                    "zone": str(slot.get("zone") or ""),
                    "wagon_number": (slot.get("wagon_number") or "").strip(),
                    "extras": extras,
                    "extras_label": _format_extra_letters(extras),
                }
            )
            for letter in extras:
                pool[letter] += 1
        elif slabs:
            wagon_extras.append(
                {
                    "slot_index": int(slot.get("slot_index") or 0),
                    "zone": str(slot.get("zone") or ""),
                    "wagon_number": (slot.get("wagon_number") or "").strip(),
                    "extras": [],
                    "extras_label": "кольцо не целое",
                    "missing_ring": decomposed["missing_ring"],
                }
            )

    spare_rings_complete = (
        min(pool[letter] for letter in EXTRA_LETTERS) if any(pool.values()) else 0
    )
    leftover = {
        letter: pool[letter] - spare_rings_complete for letter in EXTRA_LETTERS
    }
    missing_spare = _pool_missing_for_next_ring(leftover)

    if wagon_extras:
        parts = [
            f"№{w['slot_index']}: {w['extras_label']}"
            for w in wagon_extras
            if w.get("extras")
        ]
        if parts:
            hints.append("Допы по вагонам: " + " | ".join(parts))

    if spare_rings_complete:
        hints.append(
            f"Запасных колец A–F (без K): {spare_rings_complete}"
        )

    if any(pool.values()):
        if missing_spare:
            hints.append(
                "Для следующего кольца A–F из допов не хватает: "
                + ", ".join(missing_spare)
            )
        else:
            hints.append("Из допов можно собрать ещё одно кольцо A–F")

    k_pool = max(0, total_k - k_in_complete_rings)
    k_left = max(0, SCHEME2_K_GOAL - k_pool)

    if k_pool >= SCHEME2_K_GOAL:
        hints.append("Готовь отправку: 16 K в следующем вагоне")
    elif k_pool > 0 or total_k > 0:
        hints.append(f"K для схемы 16: {k_pool}/{SCHEME2_K_GOAL} · не хватает {k_left}")

    kits_complete = complete_rings

    return {
        "wagon_extras": wagon_extras,
        "pool": pool,
        "spare_rings_complete": spare_rings_complete,
        "missing_spare_ring": missing_spare,
        "complete_rings": complete_rings,
        "kits_complete": kits_complete,
        "k_pool": k_pool,
        "k_goal": SCHEME2_K_GOAL,
        "k_left": k_left,
        "hints": hints[:6],
        "show_hint_star": bool(hints),
    }


def analyze_fleet(
    slots: list[dict],
    yard_slabs: list[dict] | None = None,
    *,
    max_slabs: int = SCHEME1_WAGON_SLOTS,
) -> dict:
    """Сводка для API: кольца + ★★ по допам на каждом тупике."""
    _ = yard_slabs
    by_zone = {
        zone: analyze_fleet_extras(slots, max_slabs=max_slabs, zone=zone)
        for zone in WAGON_DEAD_ENDS
    }

    wagons_filled = sum(
        1
        for slot in slots
        if (slot.get("wagon_number") or "").strip() and (slot.get("slabs") or [])
    )
    complete_rings = sum(zone_data["complete_rings"] for zone_data in by_zone.values())
    k_pool = sum(zone_data["k_pool"] for zone_data in by_zone.values())

    return {
        "scheme1": {
            "kits_complete": complete_rings,
            "wagons_with_blocks": wagons_filled,
            "complete_rings": complete_rings,
        },
        "scheme2": {
            "k_pool": k_pool,
            "k_goal": SCHEME2_K_GOAL,
            "k_left": max(0, SCHEME2_K_GOAL - k_pool),
        },
        "by_zone": by_zone,
        "hints": [],
        "show_hint_star": any(zone_data["show_hint_star"] for zone_data in by_zone.values()),
    }
