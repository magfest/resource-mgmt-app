"""Where a supply order will be used: the space checklist and its submit rule."""
from app import db
from app.models import (
    Space,
    SpaceAssignment,
    SupplyOrderSpace,
    Venue,
    SPACE_KIND_ROOM,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.supply.form_utils import PICKUP_TIME_OPTIONS
from tests.integration.test_supply_submit import (
    _add_line,
    _login,
    _make_draft_order,
    _seed_approval_group,
    _seed_category,
    _seed_item,
    _seed_supply,
    _set_pickup_details,
)


def _seed_venue(cycle, dept):
    """Point the cycle at a venue with one room assigned to dept and one not."""
    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()
    cycle.venue_id = venue.id
    assigned = Space(venue_id=venue.id, name="Assigned Room", code="ASSIGNED",
                     kind=SPACE_KIND_ROOM, is_active=True)
    other = Space(venue_id=venue.id, name="Other Room", code="OTHER",
                  kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add_all([assigned, other])
    db.session.flush()
    db.session.add(SpaceAssignment(
        space_id=assigned.id, event_cycle_id=cycle.id, department_id=dept.id))
    db.session.commit()
    return assigned, other


def _ready_order(seed_workflow_data):
    """A draft order that passes every submit check except spaces."""
    wt = _seed_supply(seed_workflow_data)
    cycle = seed_workflow_data["cycle"]
    dept = seed_workflow_data["department"]
    category = _seed_category(approval_group=_seed_approval_group(wt))
    work_item = _make_draft_order(wt, cycle, dept)
    _add_line(work_item, _seed_item(category))
    _set_pickup_details(work_item)
    return cycle, dept, work_item


def _save_details(client, cycle, dept, work_item, space_ids):
    return client.post(
        f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/details",
        data={"pickup_time": PICKUP_TIME_OPTIONS[0], "additional_notes": "",
              "space_ids": [str(i) for i in space_ids]},
    )


class TestSupplyOrderSpacesSave:

    def test_saves_assigned_and_picked_spaces_with_labels(
        self, app, client, seed_workflow_data
    ):
        cycle, dept, work_item = _ready_order(seed_workflow_data)
        assigned, other = _seed_venue(cycle, dept)

        _login(client, "test:admin")
        _save_details(client, cycle, dept, work_item, [assigned.id, other.id])

        rows = SupplyOrderSpace.query.filter_by(work_item_id=work_item.id).all()
        assert {(r.space_id, r.space_label) for r in rows} == {
            (assigned.id, "Assigned Room"), (other.id, "Other Room"),
        }

        # Unchecking a space removes it; the other row is kept.
        _save_details(client, cycle, dept, work_item, [other.id])
        rows = SupplyOrderSpace.query.filter_by(work_item_id=work_item.id).all()
        assert [r.space_id for r in rows] == [other.id]

    def test_rejects_space_not_offered_at_this_venue(
        self, app, client, seed_workflow_data
    ):
        cycle, dept, work_item = _ready_order(seed_workflow_data)
        assigned, other = _seed_venue(cycle, dept)
        elsewhere_venue = Venue(code="HIL", name="A Different Hotel")
        db.session.add(elsewhere_venue)
        db.session.flush()
        elsewhere = Space(venue_id=elsewhere_venue.id, name="Room Elsewhere",
                          code="ELSEWHERE", kind=SPACE_KIND_ROOM, is_active=True)
        db.session.add(elsewhere)
        db.session.commit()

        _login(client, "test:admin")
        _save_details(client, cycle, dept, work_item, [assigned.id, elsewhere.id])

        assert SupplyOrderSpace.query.filter_by(work_item_id=work_item.id).count() == 0


class TestSupplyOrderSpacesSubmitRule:

    def _submit(self, client, cycle, dept, work_item):
        return client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/submit"
        )

    def test_no_space_blocks_submit_when_venue_offers_spaces(
        self, app, client, seed_workflow_data
    ):
        cycle, dept, work_item = _ready_order(seed_workflow_data)
        _seed_venue(cycle, dept)

        _login(client, "test:admin")
        self._submit(client, cycle, dept, work_item)

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT

    def test_event_without_venue_submits_with_no_space(
        self, app, client, seed_workflow_data
    ):
        """A missing venue setup must not stop a department ordering."""
        cycle, dept, work_item = _ready_order(seed_workflow_data)
        assert cycle.venue_id is None

        _login(client, "test:admin")
        self._submit(client, cycle, dept, work_item)

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_SUBMITTED


class TestSupplyOrderSpacesPage:

    def test_order_page_shows_checklist_picker_and_pickup_notice(
        self, app, client, seed_workflow_data
    ):
        cycle, dept, work_item = _ready_order(seed_workflow_data)
        assigned, other = _seed_venue(cycle, dept)

        _login(client, "test:admin")
        html = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
        ).get_data(as_text=True)

        assert f'type="checkbox" name="space_ids" value="{assigned.id}"' in html
        assert f'<option value="{other.id}">Other Room</option>' in html
        assert "Plan to pick up this order" in html
