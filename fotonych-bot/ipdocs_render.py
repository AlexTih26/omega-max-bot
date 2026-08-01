"""HTML-превью счёта и акта для печати / копирования."""

from __future__ import annotations

from html import escape

from ipdocs_store import DOC_TYPES, load_config


def _line(label: str, value: str) -> str:
    val = (value or "").strip()
    if not val:
        val = "—"
    return f"<tr><td class=\"lbl\">{escape(label)}</td><td>{escape(val)}</td></tr>"


def _party_rows(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(label, value) for label, value in pairs if (value or "").strip()]


def _party_block(title: str, rows: list[tuple[str, str]]) -> str:
    filtered = _party_rows(rows)
    body = "".join(_line(k, v) for k, v in filtered)
    return f"<section class=\"party\"><h3>{escape(title)}</h3><table>{body}</table></section>"


def render_document_html(doc: dict) -> str:
    config = load_config()
    customer = config.get("customer") or {}
    contractor = None
    for item in config.get("contractors") or []:
        if isinstance(item, dict) and item.get("id") == doc.get("contractor_id"):
            contractor = item
            break
    if contractor is None:
        contractor = {}

    doc_type = str(doc.get("doc_type") or "invoice")
    meta = DOC_TYPES.get(doc_type, {"label": "Документ", "prefix": "Д"})
    number_label = f"{meta['prefix']}-{doc.get('number')}/{doc.get('year')}"

    route = str(doc.get("route") or "перевозка груза")
    template = str(contractor.get("service_template") or "Услуги ({route})")
    service = template.format(route=route)

    qty = doc.get("quantity")
    unit = str(doc.get("unit") or "")
    unit_price = doc.get("unit_price")
    amount_label = str(doc.get("amount_label") or doc.get("amount") or "")

    qty_line = ""
    if qty and float(qty) > 0 and unit:
        qty_line = (
            f"<p class=\"qty\">Количество: {escape(str(qty))} {escape(unit)}"
            f"{f' × {escape(str(unit_price))} ₽' if unit_price else ''}</p>"
        )

    period = str(doc.get("period") or "").strip()
    period_html = f"<p class=\"period\">Период: {escape(period)}</p>" if period else ""
    note = str(doc.get("note") or "").strip()
    note_html = f"<p class=\"note\">{escape(note)}</p>" if note else ""

    seller_rows = [
        ("Наименование", str(contractor.get("full_name") or contractor.get("short_name") or "")),
        ("ИНН", str(contractor.get("inn") or "")),
        ("ОГРНИП", str(contractor.get("ogrnip") or "")),
        ("Юр. адрес", str(contractor.get("address") or "")),
        ("Почтовый адрес", str(contractor.get("postal_address") or "")),
        ("Тел.", str(contractor.get("phone") or "")),
        ("E-mail", str(contractor.get("email") or "")),
        ("Банк", str(contractor.get("bank") or "")),
        ("Р/с", str(contractor.get("account") or "")),
        ("БИК", str(contractor.get("bik") or "")),
        ("К/с", str(contractor.get("corr_account") or "")),
    ]
    buyer_rows = [
        ("Наименование", str(customer.get("name") or "")),
        ("ИНН", str(customer.get("inn") or "")),
        ("КПП", str(customer.get("kpp") or "")),
        ("ОГРН", str(customer.get("ogrn") or "")),
        ("Юр. адрес", str(customer.get("address") or "")),
        ("Почтовый адрес", str(customer.get("postal_address") or "")),
        ("Тел.", str(customer.get("phone") or "")),
        ("E-mail", str(customer.get("email") or "")),
        ("Банк", str(customer.get("bank") or "")),
        ("Р/с", str(customer.get("account") or "")),
        ("БИК", str(customer.get("bik") or "")),
        ("К/с", str(customer.get("corr_account") or "")),
    ]

    title = meta["label"].upper()
    if doc_type == "invoice":
        body_intro = (
            "<p class=\"lead\">Просим оплатить следующие услуги по договору перевозки.</p>"
        )
        sum_title = "Итого к оплате"
    else:
        body_intro = (
            "<p class=\"lead\">Настоящий акт подтверждает выполнение услуг и отсутствие претензий по объёму и качеству.</p>"
        )
        sum_title = "Стоимость услуг"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>{escape(title)} {escape(number_label)}</title>
  <style>
    body {{
      font: 14px/1.45 "Times New Roman", serif;
      color: #111;
      margin: 24px;
      max-width: 760px;
    }}
    h1 {{
      font-size: 20px;
      margin: 0 0 6px;
      text-align: center;
      letter-spacing: 0.04em;
    }}
    .meta {{
      text-align: center;
      color: #444;
      margin-bottom: 18px;
    }}
    .party {{
      margin: 14px 0;
      border: 1px solid #ccc;
      padding: 10px 12px;
    }}
    .party h3 {{
      margin: 0 0 8px;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    td {{
      padding: 3px 0;
      vertical-align: top;
    }}
    .lbl {{
      width: 110px;
      color: #555;
      padding-right: 10px;
    }}
    .service {{
      margin: 18px 0;
      padding: 12px;
      border: 1px solid #222;
    }}
    .sum {{
      font-size: 16px;
      font-weight: 700;
      margin-top: 12px;
    }}
    .sign {{
      margin-top: 36px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
    }}
    .sign p {{
      margin: 0 0 28px;
      border-bottom: 1px solid #111;
      padding-bottom: 4px;
    }}
    @media print {{
      body {{ margin: 12mm; }}
    }}
  </style>
</head>
<body>
  <h1>{escape(title)} № {escape(number_label)}</h1>
  <p class="meta">от {escape(str(doc.get("date") or ""))}</p>
  {body_intro}
  {_party_block("Исполнитель", seller_rows)}
  {_party_block("Заказчик", buyer_rows)}
  <div class="service">
    <strong>Наименование услуги</strong>
    <p>{escape(service)}</p>
    {period_html}
    {qty_line}
    <p class="sum">{escape(sum_title)}: {escape(amount_label)} ₽</p>
  </div>
  {note_html}
  <div class="sign">
    <div>
      <p>Исполнитель</p>
      <small>{escape(str(contractor.get("short_name") or ""))}</small>
    </div>
    <div>
      <p>Заказчик</p>
      <small>{escape(str(customer.get("name") or ""))}</small>
    </div>
  </div>
</body>
</html>"""
