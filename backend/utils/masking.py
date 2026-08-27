"""Deterministic sensitive-data masking for database and tool outputs."""

import re

SENSITIVE_COLUMN_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"access[_-]?key|credential|credit[_-]?card|card[_-]?number|"
    r"id[_-]?card|identity[_-]?card|ssn|social[_-]?security)",
    re.IGNORECASE,
)

EMAIL_COLUMN_RE = re.compile(r"(^|_)(email|mail)(_|$)", re.IGNORECASE)
PHONE_COLUMN_RE = re.compile(
    r"(phone|mobile|telephone|tel|contact|cellphone)", re.IGNORECASE
)
ID_CARD_COLUMN_RE = re.compile(
    r"(id[_-]?card|identity[_-]?card|idcard|id[_-]?no)", re.IGNORECASE
)
BANK_CARD_COLUMN_RE = re.compile(
    r"(credit[_-]?card|bank[_-]?card|card[_-]?number|card[_-]?no)", re.IGNORECASE
)

EMAIL_RE = re.compile(r"(?i)([A-Z0-9._%+-]+)@([A-Z0-9.-]+\.[A-Z]{2,})")
PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[0-9Xx]?)(?!\d)")
BANK_CARD_RE = re.compile(r"(?<!\d)(\d{4,6})\d{4,9}(\d{4})(?!\d)")


def is_sensitive_column(column_name: str) -> bool:
    return bool(SENSITIVE_COLUMN_RE.search(column_name or ""))


def get_excluded_columns() -> set[str]:
    """Return configured columns that should never be value-masked."""
    try:
        from config import settings
        raw = settings.masking_exclude_columns or ""
    except Exception:
        raw = "id,quantity,amount,count,stock"
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def mask_email(value: str) -> str:
    match = EMAIL_RE.match(value)
    if not match:
        return value
    local, domain = match.group(1), match.group(2)
    if len(local) <= 2:
        visible = local[0] + "***"
    else:
        visible = local[0] + "***" + local[-1]
    return f"{visible}@{domain}"


def mask_phone(value: str) -> str:
    return PHONE_RE.sub(r"\1****\3", value)


def mask_id_card(value: str) -> str:
    return ID_CARD_RE.sub(r"\1********\3", value)


def mask_bank_card(value: str) -> str:
    return BANK_CARD_RE.sub(r"\1********\2", value)


def mask_text(text: str) -> str:
    """Mask common PII patterns inside free-form text."""
    if not isinstance(text, str):
        return text
    text = EMAIL_RE.sub(
        lambda m: mask_email(m.group(0)), text
    )
    text = PHONE_RE.sub(r"\1****\3", text)
    text = ID_CARD_RE.sub(r"\1********\3", text)
    text = BANK_CARD_RE.sub(r"\1********\2", text)
    return text


def mask_value(value, column_name: str):
    """Mask a single cell. Sensitive columns are always fully masked."""
    if value is None:
        return None
    if is_sensitive_column(column_name):
        return "***"

    column_lower = (column_name or "").lower()
    if column_lower in get_excluded_columns():
        return value

    if isinstance(value, str):
        if EMAIL_COLUMN_RE.search(column_lower):
            return mask_email(value)
        if PHONE_COLUMN_RE.search(column_lower):
            return mask_phone(value)
        if ID_CARD_COLUMN_RE.search(column_lower):
            return mask_id_card(value)
        if BANK_CARD_COLUMN_RE.search(column_lower):
            return mask_bank_card(value)
    return value


def mask_rows(columns: list[str], rows: list[list]) -> list[list]:
    """Mask a column-oriented result set."""
    return [
        [mask_value(value, columns[idx]) for idx, value in enumerate(row)]
        for row in rows
    ]


def mask_dict_rows(rows: list[dict]) -> list[dict]:
    """Mask a list of dict rows."""
    return [
        {key: mask_value(value, key) for key, value in row.items()}
        for row in rows
    ]
