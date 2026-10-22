"""The migrations job seeds bootstrap at a pinned revision, before later columns exist."""
from app.models import SupplyItem, WorkType


def test_bootstrap_can_skip_supply_items_and_add_them_later(app):
    runner = app.test_cli_runner()

    result = runner.invoke(args=["seed", "bootstrap", "--skip-supply-items"])
    assert result.exit_code == 0, result.output
    assert WorkType.query.count() > 0
    assert SupplyItem.query.count() == 0

    result = runner.invoke(args=["seed", "bootstrap"])
    assert result.exit_code == 0, result.output
    assert SupplyItem.query.count() > 0
