"""Test EventCycle.av_ack_sla_days column."""
import pytest

from app import db
from app.models import EventCycle


def test_event_cycle_default_av_ack_sla(app):
    ec = EventCycle(
        code="TEST",
        name="Test Event",
    )
    db.session.add(ec)
    db.session.commit()
    assert ec.av_ack_sla_days == 7  # default


def test_event_cycle_custom_av_ack_sla(app):
    ec = EventCycle(code="TEST2", name="T2", av_ack_sla_days=14)
    db.session.add(ec)
    db.session.commit()
    assert ec.av_ack_sla_days == 14
