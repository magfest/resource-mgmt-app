"""Verify AV activity event constants are integrated."""
from app.models.constants import (
    ACTIVITY_AV_SCOPE_PUBLISHED,
    ACTIVITY_AV_REQUEST_SUBMITTED,
    ACTIVITY_AV_CONCERN_RAISED,
)


def test_constants_defined():
    assert ACTIVITY_AV_SCOPE_PUBLISHED == "AV_SCOPE_PUBLISHED"
    assert ACTIVITY_AV_REQUEST_SUBMITTED == "AV_REQUEST_SUBMITTED"
    assert ACTIVITY_AV_CONCERN_RAISED == "AV_CONCERN_RAISED"
