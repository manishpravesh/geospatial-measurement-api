import math
from datetime import date, datetime
from decimal import Decimal

MAX_PROPERTY_CHARS = 10_000


def clip_text(value: str, limit: int = MAX_PROPERTY_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def json_safe(value):
    """Turn a shapefile attribute into something JSON can store."""
    if value is None:
        return None
    if isinstance(value, str):
        return clip_text(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, Decimal):
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return clip_text(value.decode("utf-8", errors="replace"))
    if hasattr(value, "item") and callable(value.item):
        try:
            return json_safe(value.item())
        except Exception:
            pass
    try:
        import pandas as pd

        if not isinstance(value, (list, dict, tuple)) and bool(pd.isna(value)):
            return None
    except Exception:
        pass
    return clip_text(str(value))
