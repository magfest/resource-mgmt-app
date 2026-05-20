"""Tests for EventCycleWorkTypeDeadline model."""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import EventCycleWorkTypeDeadline


class TestEventCycleWorkTypeDeadlineModel:
    def test_round_trip(self, app, seed_workflow_data):
        row = EventCycleWorkTypeDeadline(
            event_cycle_id=seed_workflow_data["cycle"].id,
            work_type_id=seed_workflow_data["work_type"].id,
            submission_deadline=date(2026, 6, 1),
        )
        db.session.add(row)
        db.session.commit()

        fetched = db.session.get(EventCycleWorkTypeDeadline, row.id)
        assert fetched.submission_deadline == date(2026, 6, 1)
        assert fetched.event_cycle.code == "TST2026"
        assert fetched.work_type.code == "BUDGET"

    def test_unique_constraint_event_worktype(self, app, seed_workflow_data):
        row1 = EventCycleWorkTypeDeadline(
            event_cycle_id=seed_workflow_data["cycle"].id,
            work_type_id=seed_workflow_data["work_type"].id,
            submission_deadline=date(2026, 6, 1),
        )
        db.session.add(row1)
        db.session.commit()

        row2 = EventCycleWorkTypeDeadline(
            event_cycle_id=seed_workflow_data["cycle"].id,
            work_type_id=seed_workflow_data["work_type"].id,
            submission_deadline=date(2026, 7, 1),
        )
        db.session.add(row2)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
