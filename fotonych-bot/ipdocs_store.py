"""Хранение документов ИП (счёт, акт) для ООО «Омега-М»."""

from __future__ import annotations

import json
import logging
import re
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from super_admin import is_super_admin

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "data" / "ipdocs_config.json"
_DOCS_PATH = _ROOT / "data" / "ipdocs_documents.json"
_TZ = ZoneInfo("Asia/Irkutsk")

DOC_TYPES = {
    "invoice": {"label": "Счёт", "prefix": "С"},
    "act": {"label": "Акт", "prefix": "А"},
}

STATUS_LABELS = {
    "draft": "Черновик",
    "sent": "Отправлен",
    "paid": "Оплачен",
}


def _now_label() -> str:
    return datetime.now(_TZ).strftime("%d.%m.%Y %H:%M")


def _today_label() -> str:
    return datetime.now(_TZ).strftime("%d.%m.%Y")


def _load_json(path: Path, default: dict) -> dict:
    if not path.is_file():
        return deepcopy(default)
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else deepcopy(default)
    except Exception:
        logger.exception("ipdocs: read %s", path)
        return deepcopy(default)


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_config() -> dict:
    return _load_json(_CONFIG_PATH, {"customer": {}, "contractors": [], "tariffs": []})


def load_documents_state() -> dict:
    return _load_json(_DOCS_PATH, {"documents": [], "counters": {}})


def save_documents_state(state: dict) -> None:
    _save_json(_DOCS_PATH, state)


def _contractor_by_user(config: dict, user_id: int) -> dict | None:
    for item in config.get("contractors") or []:
        if not isinstance(item, dict):
            continue
        ids = item.get("max_user_ids") or []
        if user_id in ids:
            return item
    return None


def _is_admin(config: dict, user_id: int) -> bool:
    if is_super_admin(user_id):
        return True
    return user_id in (config.get("admin_max_ids") or [])


def resolve_access(user_id: int) -> dict:
    config = load_config()
    contractor = _contractor_by_user(config, user_id)
    admin = _is_admin(config, user_id)
    if contractor is None and not admin:
        return {
            "allowed": False,
            "role": "guest",
            "contractor": None,
            "customer": config.get("customer") or {},
            "tariffs": config.get("tariffs") or [],
        }
    return {
        "allowed": True,
        "role": "admin" if admin and contractor is None else "contractor",
        "contractor": contractor,
        "customer": config.get("customer") or {},
        "tariffs": config.get("tariffs") or [],
        "admin": admin,
    }


def _counter_key(contractor_id: str, doc_type: str, year: int) -> str:
    return f"{contractor_id}:{doc_type}:{year}"


def _next_number(state: dict, contractor_id: str, doc_type: str, year: int) -> int:
    counters = state.setdefault("counters", {})
    key = _counter_key(contractor_id, doc_type, year)
    current = int(counters.get(key) or 0)
    nxt = current + 1
    counters[key] = nxt
    return nxt


def _parse_amount(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(" ", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def _calc_amount(tariff_id: str, quantity: float, unit_price: float, amount: float) -> float:
    if tariff_id == "manual":
        return round(max(0.0, amount), 2)
    if tariff_id in {"ton", "km", "shoulder"}:
        if quantity > 0 and unit_price > 0:
            return round(quantity * unit_price, 2)
    return round(max(0.0, amount), 2)


def _format_money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _month_key(date_label: str) -> str | None:
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", (date_label or "").strip())
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}"


def create_document(
    user_id: int,
    *,
    doc_type: str,
    date: str,
    route: str,
    tariff_id: str,
    quantity: float,
    unit_price: float,
    amount: float,
    period: str = "",
    note: str = "",
    status: str = "draft",
) -> tuple[dict | None, str]:
    access = resolve_access(user_id)
    contractor = access.get("contractor")
    if not access.get("allowed") or contractor is None:
        return None, "Нет доступа к документам ИП"

    doc_type = (doc_type or "").strip().lower()
    if doc_type not in DOC_TYPES:
        return None, "Неизвестный тип документа"

    tariff_id = (tariff_id or "manual").strip().lower()
    tariffs = {t["id"]: t for t in access.get("tariffs") or [] if isinstance(t, dict)}
    tariff = tariffs.get(tariff_id) or tariffs.get("manual") or {"id": "manual", "unit": ""}

    qty = max(0.0, _parse_amount(quantity))
    price = max(0.0, _parse_amount(unit_price))
    total = _calc_amount(tariff_id, qty, price, _parse_amount(amount))
    if total <= 0:
        return None, "Укажите сумму больше нуля"

    date = (date or _today_label()).strip() or _today_label()
    route = (route or "перевозка груза").strip()
    period = (period or "").strip()
    note = (note or "").strip()
    status = status if status in STATUS_LABELS else "draft"

    year = datetime.now(_TZ).year
    m = re.search(r"(\d{4})$", date)
    if m:
        year = int(m.group(1))

    state = load_documents_state()
    contractor_id = str(contractor.get("id") or "")
    number = _next_number(state, contractor_id, doc_type, year)

    entry = {
        "id": str(uuid.uuid4()),
        "contractor_id": contractor_id,
        "max_user_id": user_id,
        "doc_type": doc_type,
        "number": number,
        "year": year,
        "date": date,
        "period": period,
        "route": route,
        "tariff_id": tariff.get("id"),
        "tariff_label": tariff.get("label") or tariff_id,
        "quantity": qty,
        "unit": tariff.get("unit") or "",
        "unit_price": price,
        "amount": total,
        "amount_label": _format_money(total),
        "status": status,
        "note": note,
        "created_at": _now_label(),
        "updated_at": _now_label(),
    }
    state.setdefault("documents", []).insert(0, entry)
    save_documents_state(state)
    return entry, "Документ сохранён"


def update_document_status(user_id: int, doc_id: str, status: str) -> tuple[dict | None, str]:
    access = resolve_access(user_id)
    contractor = access.get("contractor")
    if not access.get("allowed") or contractor is None:
        return None, "Нет доступа"

    status = (status or "").strip().lower()
    if status not in STATUS_LABELS:
        return None, "Неверный статус"

    state = load_documents_state()
    contractor_id = str(contractor.get("id") or "")
    for item in state.get("documents") or []:
        if not isinstance(item, dict):
            continue
        if item.get("id") != doc_id:
            continue
        if item.get("contractor_id") != contractor_id:
            return None, "Документ не найден"
        item["status"] = status
        item["updated_at"] = _now_label()
        save_documents_state(state)
        return item, "Статус обновлён"
    return None, "Документ не найден"


def list_documents(user_id: int, *, month: str = "") -> list[dict]:
    access = resolve_access(user_id)
    contractor = access.get("contractor")
    if not access.get("allowed") or contractor is None:
        return []

    contractor_id = str(contractor.get("id") or "")
    month = (month or "").strip()
    out: list[dict] = []
    for item in load_documents_state().get("documents") or []:
        if not isinstance(item, dict):
            continue
        if item.get("contractor_id") != contractor_id:
            continue
        if month and _month_key(str(item.get("date") or "")) != month:
            continue
        out.append(public_doc(item))
    return out


def get_document(user_id: int, doc_id: str) -> dict | None:
    access = resolve_access(user_id)
    contractor = access.get("contractor")
    if not access.get("allowed") or contractor is None:
        return None
    contractor_id = str(contractor.get("id") or "")
    for item in load_documents_state().get("documents") or []:
        if isinstance(item, dict) and item.get("id") == doc_id:
            if item.get("contractor_id") == contractor_id:
                return item
    return None


def public_doc(item: dict) -> dict:
    doc_type = str(item.get("doc_type") or "")
    meta = DOC_TYPES.get(doc_type, {"label": doc_type, "prefix": ""})
    return {
        "id": item.get("id"),
        "doc_type": doc_type,
        "doc_type_label": meta["label"],
        "number": item.get("number"),
        "year": item.get("year"),
        "number_label": f"{meta['prefix']}-{item.get('number')}/{item.get('year')}",
        "date": item.get("date"),
        "period": item.get("period"),
        "route": item.get("route"),
        "tariff_id": item.get("tariff_id"),
        "tariff_label": item.get("tariff_label"),
        "quantity": item.get("quantity"),
        "unit": item.get("unit"),
        "unit_price": item.get("unit_price"),
        "amount": item.get("amount"),
        "amount_label": item.get("amount_label"),
        "status": item.get("status"),
        "status_label": STATUS_LABELS.get(str(item.get("status")), ""),
        "created_at": item.get("created_at"),
    }


def profile_payload(user_id: int) -> dict:
    access = resolve_access(user_id)
    contractor = access.get("contractor")
    customer = access.get("customer") or {}
    tariffs = access.get("tariffs") or []

    if not access.get("allowed"):
        return {
            "allowed": False,
            "role": "guest",
            "message": "Доступ только для ИП Кудрук и ИП Патели (заказчик — ООО «Омега-М»).",
        }

    if contractor is None and access.get("admin"):
        return {
            "allowed": True,
            "role": "admin",
            "message": "Админ: откройте под учётной записью ИП или заполните реквизиты в config.",
            "customer": customer,
            "tariffs": tariffs,
            "doc_types": [
                {"id": key, "label": val["label"]} for key, val in DOC_TYPES.items()
            ],
            "statuses": [
                {"id": key, "label": val} for key, val in STATUS_LABELS.items()
            ],
        }

    docs = list_documents(user_id)
    month_total = sum(float(d.get("amount") or 0) for d in docs)
    return {
        "allowed": True,
        "role": "contractor",
        "contractor": {
            "id": contractor.get("id"),
            "short_name": contractor.get("short_name"),
            "full_name": contractor.get("full_name"),
            "inn": contractor.get("inn"),
            "ogrnip": contractor.get("ogrnip"),
            "requisites_ready": bool((contractor.get("inn") or "").strip()),
        },
        "customer": {
            "name": customer.get("name"),
            "requisites_ready": bool((customer.get("inn") or "").strip()),
        },
        "tariffs": tariffs,
        "doc_types": [{"id": key, "label": val["label"]} for key, val in DOC_TYPES.items()],
        "statuses": [{"id": key, "label": val} for key, val in STATUS_LABELS.items()],
        "stats": {
            "documents_count": len(docs),
            "month_amount_label": _format_money(month_total),
        },
    }
