"""The room-first service catalog.

PHONE is deactivated rather than deleted, following the BANDWIDTH
precedent already in bootstrap.py: the row stays for historical lines.
"""
from app.models import TechOpsServiceType
from app.seeds.bootstrap import seed_approval_groups, seed_techops_service_types, seed_work_types


def _seed_catalog():
    # seed_techops_service_types takes the approval-group map; it does not
    # look groups up itself. Verified against bootstrap.py:393.
    return seed_techops_service_types(seed_approval_groups(seed_work_types()))


def test_phone_splits_into_number_and_handset(app):
    _seed_catalog()
    by_code = {st.code: st for st in TechOpsServiceType.query.all()}

    assert by_code["PHONE"].is_active is False
    assert by_code["PHONE_NUMBER"].is_active is True
    assert by_code["DESK_PHONE"].is_active is True
    # Same group. The split is about line grain, not routing.
    assert (by_code["PHONE_NUMBER"].default_approval_group.code
            == by_code["DESK_PHONE"].default_approval_group.code
            == "TECHOPS_NET")
    assert by_code["PHONE_NUMBER"].instance_noun == "phone line"
    assert by_code["DESK_PHONE"].instance_noun == "desk phone"


def test_no_services_is_a_reviewable_service(app):
    _seed_catalog()
    st = TechOpsServiceType.query.filter_by(code="NO_SERVICES").one()
    assert st.is_active is True
    assert st.default_approval_group.code == "TECHOPS_GEN"
    # Single-line: one affirmation per space, not a repeating group.
    assert st.instance_noun is None
