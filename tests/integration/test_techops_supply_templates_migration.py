"""The nine rows TechOps and Supply need once BUDGET stops using bare keys.

PR 1 renamed BUDGET's templates, which deleted the bare keys
resolve_template_key falls back to. Any kind a live work type can emit now
needs a row of its own, or it queues nothing and logs one line per recipient.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import EmailTemplate
from app.services.email_templates import get_template

EXPECTED = (
    "techops_submitted",
    "techops_needs_attention",
    "techops_response_received",
    "techops_submission_confirmation",
    "supply_submitted",
    "supply_needs_attention",
    "supply_response_received",
    "supply_finalized",
    "supply_submission_confirmation",
)


def _run_seed():
    from migrations.versions.ws8841d5f27c_techops_supply_email_templates import (
        seed_work_type_templates,
    )
    seed_work_type_templates(db.session.connection())
    db.session.commit()


def test_every_expected_row_is_created(app):
    _run_seed()

    for key in EXPECTED:
        assert get_template(key) is not None, f"{key} missing"


def test_every_row_is_active_and_has_a_body(app):
    _run_seed()

    for key in EXPECTED:
        row = get_template(key)
        assert row.is_active is True, f"{key} inactive"
        assert row.subject.strip(), f"{key} has an empty subject"
        assert row.body_text.strip(), f"{key} has an empty body"


def test_subjects_name_their_work_type(app):
    _run_seed()

    for key in EXPECTED:
        expected = "[MAGFest TechOps]" if key.startswith("techops_") else "[MAGFest Supply]"
        assert get_template(key).subject.startswith(expected), (
            f"{key} subject is {get_template(key).subject!r}"
        )


def test_techops_wording_never_mentions_dispatch(app):
    """TechOps has uses_dispatch=False. Telling a reviewer to dispatch sends
    them looking for a step that does not exist."""
    _run_seed()

    for key in EXPECTED:
        if not key.startswith("techops_"):
            continue
        body = get_template(key).body_text.lower()
        assert "dispatch" not in body, f"{key} mentions dispatch"


def test_supply_wording_never_mentions_money(app):
    """Supply is requester-facing and never shows prices (supply/portfolio.py:28)."""
    _run_seed()

    for key in EXPECTED:
        if not key.startswith("supply_"):
            continue
        body = get_template(key).body_text
        assert "$" not in body, f"{key} shows a price"
        assert "total_requested_dollars" not in body, f"{key} renders a total"


def test_the_seed_is_idempotent(app):
    """It runs after `flask seed` on a fresh database and again on a retried
    deploy. template_key is unique, so an unguarded insert raises."""
    _run_seed()
    _run_seed()

    assert EmailTemplate.query.filter_by(
        template_key="techops_submitted").count() == 1


def test_an_existing_row_is_left_alone(app):
    """An admin may have edited the wording. A re-run must not overwrite it."""
    db.session.add(EmailTemplate(
        template_key="techops_submitted", name="Edited by an admin",
        subject="[MAGFest TechOps] Edited", body_text="Edited body",
        is_active=True,
    ))
    db.session.commit()

    _run_seed()

    row = get_template("techops_submitted")
    assert row.name == "Edited by an admin"
    assert row.body_text == "Edited body"


def test_no_body_renders_a_variable_its_kind_does_not_supply(app):
    """Render each body against only the context its own kind actually gets.

    Jinja's default Undefined renders empty rather than raising, so a body that
    names a variable nobody passes shows a blank line in the real email while
    looking right on the admin preview, which injects line_count regardless
    (email_templates.py:337). Checking the text for non-empty bodies cannot
    catch that; rendering can.
    """
    from jinja2 import Environment, StrictUndefined

    _run_seed()

    # notify_work_item_submitted, needs_attention, response_received and
    # finalized all call _enqueue_emails with no extra_context; only
    # submission_confirmation supplies line_count.
    base = {"work_item": object(), "base_url": "https://example.test"}
    env = Environment(undefined=StrictUndefined)

    for key in EXPECTED:
        kind = key.split("_", 1)[1]
        context = dict(base)
        if kind == "submission_confirmation":
            context["line_count"] = 3
        source = get_template(key).body_text
        names = env.parse(source).find_all(__import__("jinja2").nodes.Name)
        used = {n.name for n in names}
        missing = used - set(context)
        assert not missing, (
            f"{key} uses {sorted(missing)}, which nothing passes for kind "
            f"{kind!r}"
        )
