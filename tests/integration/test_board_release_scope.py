"""The board hold is BUDGET-only.

compute_line_status_summary serves every work type. Only BUDGET writes
board_released_at, so the held-status derivation must be scoped or every
finalized TechOps and Supply item reads as waiting on the board.
"""
from app import db
from app.models import WorkType, WorkTypeConfig


def test_budget_opts_into_board_release(app, seed_workflow_data):
    config = (
        WorkTypeConfig.query
        .filter_by(url_slug="budget")
        .first()
    )
    assert config is not None
    assert config.uses_board_release is True


def test_non_budget_work_type_defaults_to_no_board_release(app, seed_workflow_data):
    """Guard the column default, not just BUDGET's seeded override.

    Builds a non-budget WorkTypeConfig without setting the flag and reads it
    back from the database. Catches a wrong default at either the model or
    the migration's server_default.
    """
    work_type = WorkType(code="TEST_WT", name="Test Work Type", is_active=True)
    db.session.add(work_type)
    db.session.flush()

    config = WorkTypeConfig(
        work_type_id=work_type.id,
        url_slug="test-worktype",
        public_id_prefix="TWT",
        line_detail_type="test",
    )
    db.session.add(config)
    db.session.commit()

    # Drop the identity map so the read below is a real SELECT, not the
    # in-memory value assigned before the INSERT.
    db.session.expunge_all()

    reloaded = WorkTypeConfig.query.filter_by(url_slug="test-worktype").first()
    assert reloaded is not None
    assert reloaded.uses_board_release is False
