"""Pure value-normalisation helpers. No I/O, no database: easy to unit test.

Every function returns ``None`` when the input is invalid, so callers decide
whether that means "reject the row" or "null the field and warn".
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# --------------------------------------------------------------------- headers
_HEADER_RUNS = re.compile(r"[_\s]+")


def normalize_header(header: str) -> str:
    """lower-case, trim, then collapse every run of ``_``/whitespace to one space."""
    return _HEADER_RUNS.sub(" ", header.lower().strip())


# ---------------------------------------------------------------------- phones
_NON_DIGIT = re.compile(r"[^0-9]")


def normalize_phone(raw: object) -> Optional[str]:
    """Indian mobile -> ``+91XXXXXXXXXX`` or None.

    Strip non-digits; 12 digits starting 91 -> drop 91; 11 digits starting 0 ->
    drop 0; valid iff exactly 10 digits starting with 6-9.
    """
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", str(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


# ----------------------------------------------------------------------- dates
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ISO = re.compile(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})")
_DMY = re.compile(r"([0-9]{1,2})[/-]([0-9]{1,2})[/-]([0-9]{4})")
_DMON = re.compile(r"([0-9]{1,2})-([A-Za-z]{3})-([0-9]{4})")


def parse_date(raw: str) -> Optional[str]:
    """Accept YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY (day-first). -> ISO date."""
    s = raw.strip()
    try:
        if m := _ISO.fullmatch(s):
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
        elif m := _DMY.fullmatch(s):
            d, mo, y = int(m[1]), int(m[2]), int(m[3])
        elif m := _DMON.fullmatch(s):
            mon = _MONTHS.get(m[2].lower())
            if mon is None:
                return None
            d, mo, y = int(m[1]), mon, int(m[3])
        else:
            return None
        return date(y, mo, d).isoformat()
    except ValueError:  # impossible date such as 31/02
        return None


# --------------------------------------------------------------------- decimal
_DECIMAL = re.compile(r"(?:₹|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.[0-9]+)?|\.[0-9]+)", re.IGNORECASE)
_TWO_PLACES = Decimal("0.01")


def parse_decimal(raw: str) -> Optional[str]:
    """Optional currency prefix, comma thousands separators, >= 0, 2 places (half-up)."""
    m = _DECIMAL.fullmatch(raw.strip())
    if not m:
        return None
    try:
        value = Decimal(m[1].replace(",", ""))
    except InvalidOperation:
        return None
    return str(value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------ timestamps
def parse_timestamp(raw: object) -> Optional[datetime]:
    """UTC (``Z``), offset (``+05:30``) or naive (= IST) -> aware datetime in UTC."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(UTC)


def to_micros(dt: datetime) -> int:
    """Aware datetime -> integer microseconds since the epoch (exact, sortable)."""
    return (dt - EPOCH) // timedelta(microseconds=1)


def from_micros(us: int) -> datetime:
    return EPOCH + timedelta(microseconds=us)
