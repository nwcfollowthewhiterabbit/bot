from __future__ import annotations

from datetime import date, datetime
from typing import Optional

DATE_FORMAT = "%d.%m.%Y"
DATETIME_FORMAT = "%d.%m.%Y %H:%M"


def parse_date(value, datetime_mode: bool = False):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    formats = [DATETIME_FORMAT, "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y.%m.%d"]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if datetime_mode else parsed.date()
        except ValueError:
            continue
    return None


def format_date(value: date) -> str:
    return value.strftime(DATE_FORMAT)


def format_datetime(value: datetime) -> str:
    return value.strftime(DATETIME_FORMAT)


def parse_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_int(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_user_date(text: str) -> Optional[date]:
    return parse_date(text)


def parse_hours(text: str) -> Optional[float]:
    if text is None:
        return None
    normalized = text.replace(",", ".").strip()
    try:
        value = float(normalized)
    except ValueError:
        return None
    if value < 0:
        return None
    return value
