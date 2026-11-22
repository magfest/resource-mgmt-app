"""Data step of migration sr4k8w2q6n1d."""
import importlib.util
from pathlib import Path

from app import db
from app.models import WorkType, WorkTypeConfig

_PATH = Path(__file__).resolve().parents[2] / "migrations/versions/sr4k8w2q6n1d_supply_review_admin.py"


def _step():
    spec = importlib.util.spec_from_file_location("sr4k8w2q6n1d", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.set_review_session_flags


def _cfg(code):
    wt = WorkType.query.filter_by(code=code).one()
    return WorkTypeConfig.query.filter_by(work_type_id=wt.id).one()


def test_uses_review_sessions_false_for_supply_and_techops_true_for_budget(app, seed_workflow_data):
    for code, slug in (("SUPPLY", "supply"), ("TECHOPS", "techops")):
        wt = WorkType(code=code, name=code, is_active=True)
        db.session.add(wt)
        db.session.flush()
        db.session.add(WorkTypeConfig(
            work_type_id=wt.id, url_slug=slug, public_id_prefix=code[:3],
            line_detail_type=slug,
        ))
    db.session.commit()
    assert _cfg("SUPPLY").uses_review_sessions is True  # column default before the data step
    _step()(db.session.connection())
    db.session.commit()
    db.session.expire_all()
    assert _cfg("BUDGET").uses_review_sessions is True
    assert _cfg("SUPPLY").uses_review_sessions is False
    assert _cfg("TECHOPS").uses_review_sessions is False


def test_migration_data_step_is_noop_without_work_types(app):
    assert WorkType.query.count() == 0
    _step()(db.session.connection())


def test_bootstrap_seeds_review_sessions_off_for_supply_and_techops(app):
    from app.seeds.bootstrap import seed_work_type_configs, seed_work_types

    seed_work_type_configs(seed_work_types())
    db.session.commit()
    assert _cfg("BUDGET").uses_review_sessions is True
    assert _cfg("SUPPLY").uses_review_sessions is False
    assert _cfg("TECHOPS").uses_review_sessions is False
