"""Variable help must survive a work-type prefix on the template key.

EMAIL_TEMPLATE_VARIABLES is keyed by kind. A key like budget_submitted has to
resolve to the submitted entry, or every renamed template's edit screen shows
no variable list.
"""
from app.services.email_templates import (
    EMAIL_TEMPLATE_VARIABLES,
    variables_for_template_key,
)


def test_a_bare_key_resolves_to_its_own_entry():
    assert variables_for_template_key("submitted") == EMAIL_TEMPLATE_VARIABLES["submitted"]


def test_a_work_type_prefix_resolves_to_the_kind_behind_it():
    assert variables_for_template_key("budget_submitted") == EMAIL_TEMPLATE_VARIABLES["submitted"]


def test_an_unknown_work_type_prefix_is_not_stripped():
    """`submission_confirmation` splits into `submission` + `confirmation`.
    `submission` is not a work type, so the whole key is the lookup."""
    assert variables_for_template_key("submission_confirmation") == (
        EMAIL_TEMPLATE_VARIABLES["submission_confirmation"]
    )


def test_an_unknown_key_returns_an_empty_dict():
    assert variables_for_template_key("not_a_real_key") == {}


def test_every_existing_key_still_resolves_to_itself():
    """The seven keys shipped today must not change behaviour."""
    for key in EMAIL_TEMPLATE_VARIABLES:
        assert variables_for_template_key(key) == EMAIL_TEMPLATE_VARIABLES[key]
