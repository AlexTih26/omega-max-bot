"""Логистика вагонов: ★ по вагону, ★★ по допам парка."""

from __future__ import annotations

import re

RING_LETTERS = ("A", "B", "C", "D", "E", "F", "K")
EXTRA_LETTERS = ("A", "B", "C", "D", "E", "F")
SCHEME3_LETTERS = EXTRA_LETTERS
RING_EXTRAS = 2
SCHEME1_WAGON_SLOTS = 9
SCHEME2_K_GOAL = 16
SCHEME3_WAGON_SLOTS = 8
SCHEME_CODE_DEFAULT = "scheme1"
SCHEME_CODES = frozenset({"scheme1", "scheme2", "scheme3"})
WAGON_DEAD_ENDS = ("ГРУЗОВОЙ", "ТУРАН")

_SCHEME_LABELS = {
    "scheme1": "Схема 1",
    "scheme2": "Схема 2",
    "scheme3": "Схема 3",
}


def normalize_scheme_code(value) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "1": "scheme1",
        "2": "scheme2",
        "3": "scheme3",
        "scheme1": "scheme1",
        "scheme2": "scheme2",
        "scheme3": "scheme3",
        "схема1": "scheme1",
        "схема2": "scheme2",
        "схема3": "scheme3",
    }
    code = aliases.get(raw, raw or SCHEME_CODE_DEFAULT)
    if code not in SCHEME_CODES:
        raise ValueError("Тип схемы: 1, 2 или 3")
    return code


def scheme_max_slabs(scheme_code: str) -> int:
    code = normalize_scheme_code(scheme_code)
    if code == "scheme2":
        return SCHEME2_K_GOAL
    if code == "scheme3":
        return SCHEME3_WAGON_SLOTS
    return SCHEME1_WAGON_SLOTS


def scheme_label(scheme_code: str) -> str:
    return _SCHEME_LABELS.get(normalize_scheme_code(scheme_code), "Схема 1")


def validate_slab_letter_for_scheme(letter: str, scheme_code: str) -> None:
    letter = (letter or "").strip().upper()
    code = normalize_scheme_code(scheme_code)
    if code == "scheme2" and letter != "K":
        raise ValueError("Схема 2: только блоки K")
    if code == "scheme3" and letter == "K":
        raise ValueError("Схема 3: блок K не допускается")
    if code == "scheme3" and letter not in SCHEME3_LETTERS:
        raise ValueError("Схема 3: только буквы A–F")


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


def _scheme3_letter_counts(slabs: list[dict]) -> dict[str, int]:
    counts = {letter: 0 for letter in SCHEME3_LETTERS}
    for slab in slabs:
        letter = (slab.get("letter") or "").strip().upper()
        if letter in counts:
            counts[letter] += 1
    return counts


def missing_scheme3_letters(slabs: list[dict]) -> list[str]:
    counts = _scheme3_letter_counts(slabs)
    return [letter for letter in SCHEME3_LETTERS if counts[letter] < 1]


def scheme3_is_complete(slabs: list[dict]) -> bool:
    if len(slabs) != SCHEME3_WAGON_SLOTS:
        return False
    counts = _scheme3_letter_counts(slabs)
    if any((slab.get("letter") or "").strip().upper() == "K" for slab in slabs):
        return False
    return all(counts[letter] >= 1 for letter in SCHEME3_LETTERS)


def scheme2_is_complete(slabs: list[dict]) -> bool:
    if len(slabs) != SCHEME2_K_GOAL:
        return False
    return all((slab.get("letter") or "").strip().upper() == "K" for slab in slabs)


def wagon_scheme_is_complete(slabs: list[dict], scheme_code: str) -> bool:
    code = normalize_scheme_code(scheme_code)
    if code == "scheme2":
        return scheme2_is_complete(slabs)
    if code == "scheme3":
        return scheme3_is_complete(slabs)
    return ring_is_complete(slabs, max_slabs=SCHEME1_WAGON_SLOTS)


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


def _analyze_wagon_scheme2(
    slabs: list[dict],
    *,
    wagon_number: str = "",
) -> dict:
    max_slabs = SCHEME2_K_GOAL
    n = len(slabs)
    has_wagon = bool((wagon_number or "").strip())
    non_k = [
        (slab.get("letter") or "").strip().upper()
        for slab in slabs
        if (slab.get("letter") or "").strip().upper() != "K"
    ]
    k_count = n - len(non_k)
    is_complete = scheme2_is_complete(slabs)
    if is_complete:
        return {
            "scheme": "scheme2",
            "scheme_code": "scheme2",
            "is_complete": True,
            "show_hint_star": False,
            "slab_count": n,
            "max_slabs": max_slabs,
            "k_count": k_count,
            "ring_letters_missing": [],
            "hints": [],
            "summary": f"{max_slabs}/{max_slabs} ✓",
        }
    hints: list[str] = []
    if non_k:
        hints.append("Только K на вагон (схема 2)")
    if n == 0:
        hints.append(f"Схема 2: {max_slabs} блоков K")
    elif n < max_slabs:
        hints.append(f"Нужно ещё {max_slabs - n} K")
    elif n == max_slabs and non_k:
        hints.insert(0, "Уберите лишние блоки — только K")
    return {
        "scheme": "scheme2",
        "scheme_code": "scheme2",
        "is_complete": False,
        "show_hint_star": has_wagon,
        "slab_count": n,
        "max_slabs": max_slabs,
        "k_count": k_count,
        "ring_letters_missing": [],
        "hints": hints[:3],
        "summary": f"{n}/{max_slabs}",
    }


def _analyze_wagon_scheme3(
    slabs: list[dict],
    *,
    wagon_number: str = "",
) -> dict:
    max_slabs = SCHEME3_WAGON_SLOTS
    n = len(slabs)
    has_wagon = bool((wagon_number or "").strip())
    missing = missing_scheme3_letters(slabs)
    has_k = any((slab.get("letter") or "").strip().upper() == "K" for slab in slabs)
    is_complete = scheme3_is_complete(slabs)
    if is_complete:
        return {
            "scheme": "scheme3",
            "scheme_code": "scheme3",
            "is_complete": True,
            "show_hint_star": False,
            "slab_count": n,
            "max_slabs": max_slabs,
            "k_count": 0,
            "ring_letters_missing": [],
            "hints": ["Ящик с крепежом — возврат"],
            "summary": f"{max_slabs}/{max_slabs} ✓",
        }
    hints: list[str] = []
    if has_k:
        hints.append("Схема 3: без K")
    if n == 0:
        hints.append("8 блоков A–F: по одному + 2 запасных")
    else:
        for letter in missing:
            if len(hints) >= 3:
                break
            hints.append(f"Нужен {letter}")
        if len(hints) < 3 and not missing and not has_k and n < max_slabs:
            hints.append(f"Ещё {max_slabs - n} из A–F")
        if len(hints) < 3 and n == max_slabs and missing:
            need = ", ".join(missing[:3])
            hints.insert(0, f"Довезите {need}")
    return {
        "scheme": "scheme3",
        "scheme_code": "scheme3",
        "is_complete": False,
        "show_hint_star": has_wagon,
        "slab_count": n,
        "max_slabs": max_slabs,
        "k_count": 0,
        "ring_letters_missing": missing,
        "hints": hints[:3],
        "summary": f"{n}/{max_slabs}",
    }


def analyze_wagon(
    slabs: list[dict],
    *,
    max_slabs: int | None = None,
    scheme_code: str = SCHEME_CODE_DEFAULT,
    wagon_number: str = "",
) -> dict:
    """★ Подсказки по одному вагону — без номеров плит и без сводки по двору."""
    code = normalize_scheme_code(scheme_code)
    if code == "scheme2":
        return _analyze_wagon_scheme2(slabs, wagon_number=wagon_number)
    if code == "scheme3":
        return _analyze_wagon_scheme3(slabs, wagon_number=wagon_number)

    max_slabs = max_slabs or SCHEME1_WAGON_SLOTS
    n = len(slabs)
    counts = _letter_counts(slabs)
    k_count = counts.get("K", 0)
    has_wagon = bool((wagon_number or "").strip())
    missing_ring = missing_ring_letters(slabs)
    is_complete = ring_is_complete(slabs, max_slabs=max_slabs)

    if is_complete:
        return {
            "scheme": "scheme1",
            "scheme_code": "scheme1",
            "is_complete": True,
            "show_hint_star": False,
            "slab_count": n,
            "max_slabs": max_slabs,
            "k_count": k_count,
            "ring_letters_missing": [],
            "hints": [],
            "summary": f"{max_slabs}/{max_slabs} ✓",
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
        "scheme": "scheme1",
        "scheme_code": "scheme1",
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


def _format_pool_counts(pool: dict[str, int]) -> str:
    parts = [f"{letter}×{pool[letter]}" for letter in EXTRA_LETTERS if pool[letter] > 0]
    return ", ".join(parts) if parts else "нет"


def _dispatch_blocks_to_slabs(dispatch: dict) -> list[dict]:
    slabs: list[dict] = []
    for block in dispatch.get("blocks") or []:
        label = (block.get("label") or "").strip().upper()
        match = re.match(r"^([A-ZА-Я])\s*([0-9]+)$", label)
        if not match:
            continue
        slabs.append({"letter": match.group(1), "number": match.group(2)})
    return slabs


def analyze_fleet_extras(
    slots: list[dict],
    *,
    max_slabs: int = SCHEME1_WAGON_SLOTS,
    zone: str | None = None,
    dispatches: list[dict] | None = None,
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
    filtered_dispatches = [
        dispatch
        for dispatch in (dispatches or [])
        if (dispatch.get("wagon_number") or "").strip()
        and (zone is None or str(dispatch.get("slot_zone") or "") == zone)
    ]
    filtered_dispatches.sort(
        key=lambda item: float(item.get("dispatched_at") or 0),
        reverse=True,
    )

    wagon_extras: list[dict] = []
    pool = {letter: 0 for letter in EXTRA_LETTERS}
    complete_rings = 0
    departed_complete_rings = 0
    hints: list[str] = []

    for slot in filtered:
        slabs = slot.get("slabs") or []
        decomposed = decompose_wagon_ring(slabs, max_slabs=max_slabs)
        if decomposed["ring_complete"]:
            complete_rings += 1
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

    cycle_dispatches: list[dict] = []
    cycle_room_left = max(0, SCHEME2_K_GOAL - complete_rings)
    if cycle_room_left:
        cycle_dispatches = filtered_dispatches[:cycle_room_left]
    for dispatch in cycle_dispatches:
        slabs = _dispatch_blocks_to_slabs(dispatch)
        decomposed = decompose_wagon_ring(slabs, max_slabs=max_slabs)
        if not decomposed["ring_complete"]:
            continue
        departed_complete_rings += 1
        for letter in decomposed["extras"]:
            pool[letter] += 1

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
            hints.append("Допы в слотах: " + " | ".join(parts))

    if complete_rings or departed_complete_rings:
        hints.append(
            f"По циклу: в слотах {complete_rings} · ушло {departed_complete_rings}"
        )

    if any(pool.values()):
        hints.append("Накоплено допов A–F: " + _format_pool_counts(pool))

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

    k_pool = complete_rings + departed_complete_rings
    k_left = max(0, SCHEME2_K_GOAL - k_pool)

    if k_pool >= SCHEME2_K_GOAL:
        hints.append("Готовь отправку: 16 K в следующем вагоне")
    elif k_pool > 0:
        hints.append(f"K для схемы 16: {k_pool}/{SCHEME2_K_GOAL} · не хватает {k_left}")

    kits_complete = complete_rings

    return {
        "wagon_extras": wagon_extras,
        "pool": pool,
        "spare_rings_complete": spare_rings_complete,
        "missing_spare_ring": missing_spare,
        "complete_rings": complete_rings,
        "kits_complete": kits_complete,
        "departed_complete_rings": departed_complete_rings,
        "k_pool": k_pool,
        "k_goal": SCHEME2_K_GOAL,
        "k_left": k_left,
        "hints": hints[:7],
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
