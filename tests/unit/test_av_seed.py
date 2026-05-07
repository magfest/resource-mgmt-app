"""Test that AV worktype seed produces the expected approval group + config."""
from app import db
from app.models import WorkType, WorkTypeConfig, ApprovalGroup
from app.seeds.bootstrap import (
    seed_work_types, seed_approval_groups, seed_work_type_configs,
)


def test_av_team_approval_group_seeded(app):
    work_types = seed_work_types()
    seed_approval_groups(work_types)
    db.session.commit()

    av_wt = db.session.query(WorkType).filter_by(code="AV").first()
    assert av_wt is not None

    av_team = db.session.query(ApprovalGroup).filter_by(
        work_type_id=av_wt.id, code="AV_TEAM",
    ).first()
    assert av_team is not None
    assert av_team.is_active is True


def test_av_work_type_config_default_approval_group(app):
    work_types = seed_work_types()
    seed_approval_groups(work_types)
    seed_work_type_configs(work_types)
    db.session.commit()

    av_wt = db.session.query(WorkType).filter_by(code="AV").first()
    config = av_wt.config
    assert config is not None
    assert config.default_approval_group_id is not None
    av_team = db.session.query(ApprovalGroup).filter_by(
        work_type_id=av_wt.id, code="AV_TEAM",
    ).first()
    assert config.default_approval_group_id == av_team.id
