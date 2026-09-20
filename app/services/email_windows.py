"""US Eastern to naive UTC for send-window dates.

zoneinfo appears here and nowhere else. A stored window is an INSTANT, fixed
when an operator saved it, not a date with a timezone rule attached: if the
United States changes its daylight-saving rules, existing windows keep meaning
what the person who set them meant.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")


def eastern_date_to_utc_start(d: date) -> datetime:
    """00:00:00 Eastern on this date, as a naive UTC datetime."""
    local = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=_EASTERN)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def eastern_date_to_utc_end(d: date) -> datetime:
    """23:59:59 Eastern on this date, as a naive UTC datetime.

    This lands on the NEXT UTC day. An end of 2027-03-08 stores as
    2027-03-09 04:59:59.
    """
    local = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=_EASTERN)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_eastern_date(dt: datetime | None) -> date | None:
    """The Eastern calendar date a stored instant falls on."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(_EASTERN).date()
