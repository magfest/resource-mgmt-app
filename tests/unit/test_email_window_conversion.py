"""US Eastern dates to naive UTC instants, across both DST transitions."""
from datetime import date, datetime

import pytest

from app.services.email_windows import (
    eastern_date_to_utc_end, eastern_date_to_utc_start, utc_to_eastern_date,
)


@pytest.mark.parametrize("d,expected", [
    (date(2027, 3, 1), datetime(2027, 3, 1, 5, 0, 0)),     # EST, UTC-5
    (date(2027, 3, 20), datetime(2027, 3, 20, 4, 0, 0)),   # EDT, UTC-4
])
def test_start_of_day(d, expected):
    assert eastern_date_to_utc_start(d) == expected


@pytest.mark.parametrize("d,expected", [
    # End of day in Eastern falls on the NEXT UTC day, both times.
    (date(2027, 3, 8), datetime(2027, 3, 9, 4, 59, 59)),
    (date(2027, 3, 20), datetime(2027, 3, 21, 3, 59, 59)),
])
def test_end_of_day(d, expected):
    assert eastern_date_to_utc_end(d) == expected


@pytest.mark.parametrize("d", [
    date(2027, 3, 1), date(2027, 3, 8), date(2027, 3, 14),   # DST begins
    date(2027, 3, 20), date(2027, 11, 7),                    # DST ends
])
def test_round_trip(d):
    assert utc_to_eastern_date(eastern_date_to_utc_start(d)) == d
    assert utc_to_eastern_date(eastern_date_to_utc_end(d)) == d


def test_none_round_trips_as_none():
    assert utc_to_eastern_date(None) is None


def test_a_window_spanning_the_spring_transition_is_one_second_short_of_a_day():
    """The stored instants are what matter, not the local clock reading.

    2027-03-14 loses an hour at 02:00 Eastern, so this window is 23 hours
    less a second. A naive date-arithmetic implementation returns 24.
    """
    start = eastern_date_to_utc_start(date(2027, 3, 14))
    end = eastern_date_to_utc_end(date(2027, 3, 14))
    assert (end - start).total_seconds() == 23 * 3600 - 1
