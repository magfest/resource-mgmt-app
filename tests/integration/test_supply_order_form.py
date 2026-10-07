"""The supply order page's single form: every line, pickup, and spaces in one POST."""
from app import db
from app.models import (
    SupplyOrderLineDetail,
    WorkLine,
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
)


def _order_with_two_lines(seed_workflow_data):
    wt = _seed_supply(seed_workflow_data)
    cycle = seed_workflow_data["cycle"]
    dept = seed_workflow_data["department"]
    category = _seed_category(approval_group=_seed_approval_group(wt))
    work_item = _make_draft_order(wt, cycle, dept)
    tape = _seed_item(category, name="Gaffer Tape")
    pens = _seed_item(category, name="Sharpie Markers")
    line1 = _add_line(work_item, tape, quantity=1, quantity_confidence=None)
    line2 = _add_line(work_item, pens, quantity=1, quantity_confidence=None)
    return cycle, dept, work_item, line1, line2


def _form(line1, line2, **extra):
    data = {
        f"line-{line1.line_number}-item": str(line1.supply_detail.item_id),
        f"line-{line1.line_number}-qty": "4",
        f"line-{line1.line_number}-confidence": "ESTIMATE",
        f"line-{line1.line_number}-notes": "stage left",
        f"line-{line2.line_number}-item": str(line2.supply_detail.item_id),
        f"line-{line2.line_number}-qty": "9",
        f"line-{line2.line_number}-confidence": "GUESS",
        f"line-{line2.line_number}-notes": "",
        "pickup_time": PICKUP_TIME_OPTIONS[0],
        "additional_notes": "",
    }
    data.update(extra)
    return data


def _detail(line):
    return SupplyOrderLineDetail.query.filter_by(work_line_id=line.id).one()


def _url(cycle, dept, work_item):
    return f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/save"


class TestSupplyOrderFormSave:

    def test_save_draft_writes_every_line_and_pickup(self, app, client, seed_workflow_data):
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        response = client.post(_url(cycle, dept, work_item),
                               data=_form(line1, line2, action="save"))

        assert response.status_code == 302
        assert (_detail(line1).quantity_requested, _detail(line1).quantity_confidence,
                _detail(line1).requester_notes) == (4, "ESTIMATE", "stage left")
        assert (_detail(line2).quantity_requested, _detail(line2).quantity_confidence) == (9, "GUESS")
        db.session.refresh(work_item)
        assert work_item.supply_order_detail.pickup_time == PICKUP_TIME_OPTIONS[0]
        assert work_item.status == WORK_ITEM_STATUS_DRAFT

    def test_tampered_value_saves_nothing(self, app, client, seed_workflow_data):
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        client.post(_url(cycle, dept, work_item), data=_form(
            line1, line2, action="save",
            **{f"line-{line2.line_number}-confidence": "PSYCHIC"}))

        assert _detail(line1).quantity_requested == 1
        db.session.refresh(work_item)
        assert work_item.supply_order_detail.pickup_time is None

    def test_stale_line_saves_nothing(self, app, client, seed_workflow_data):
        """A line number can be reused after a delete and re-add in another tab."""
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        client.post(_url(cycle, dept, work_item), data=_form(
            line1, line2, action="save",
            **{f"line-{line2.line_number}-item": str(line1.supply_detail.item_id)}))

        assert _detail(line1).quantity_requested == 1

    def test_remove_keeps_other_edits_and_line_numbers(self, app, client, seed_workflow_data):
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        client.post(_url(cycle, dept, work_item), data=_form(
            line1, line2, remove_line=str(line1.line_number)))

        remaining = WorkLine.query.filter_by(work_item_id=work_item.id).all()
        assert [l.line_number for l in remaining] == [2]
        assert _detail(line2).quantity_requested == 9

    def test_add_more_items_saves_then_opens_catalog(self, app, client, seed_workflow_data):
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        response = client.post(_url(cycle, dept, work_item),
                               data=_form(line1, line2, action="catalog"))

        assert "/catalog" in response.headers["Location"]
        assert _detail(line1).quantity_requested == 4

    def test_rejected_once_submitted(self, app, client, seed_workflow_data):
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)
        work_item.status = WORK_ITEM_STATUS_SUBMITTED
        db.session.commit()

        _login(client, "test:admin")
        response = client.post(_url(cycle, dept, work_item),
                               data=_form(line1, line2, action="save"))

        assert response.status_code == 302
        assert _detail(line1).quantity_requested == 1
        follow = client.get(response.headers["Location"]).get_data(as_text=True)
        assert "already submitted" in follow

    def test_enter_key_default_is_save(self, app, client, seed_workflow_data):
        """Browsers press the first submit button naming the form, in page order."""
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        html = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
        ).get_data(as_text=True)

        first_submit = html.index('type="submit"')
        assert html.find('value="save"', first_submit) < html.find('value="catalog"', first_submit)
        assert html.find('value="save"', first_submit) < html.index('name="remove_line"')


class TestSupplyOrderFormSubmit:

    def test_submit_saves_then_submits(self, app, client, seed_workflow_data):
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        client.post(_url(cycle, dept, work_item), data=_form(line1, line2, action="submit"))

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_SUBMITTED
        assert _detail(line1).quantity_confidence == "ESTIMATE"

    def test_failed_submit_still_keeps_what_was_entered(self, app, client, seed_workflow_data):
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        client.post(_url(cycle, dept, work_item),
                    data=_form(line1, line2, action="submit", pickup_time=""))

        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT
        assert _detail(line2).quantity_requested == 9

    def test_empty_order_names_what_is_missing(self, app, client, seed_workflow_data):
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)
        work_item.lines.clear()
        db.session.commit()

        _login(client, "test:admin")
        response = client.post(_url(cycle, dept, work_item),
                               data={"pickup_time": PICKUP_TIME_OPTIONS[0], "action": "submit"},
                               follow_redirects=True)

        html = response.get_data(as_text=True)
        assert "Add at least one item" in html
        assert "You cannot submit" not in html
        db.session.refresh(work_item)
        assert work_item.status == WORK_ITEM_STATUS_DRAFT

    def test_submit_button_is_never_disabled(self, app, client, seed_workflow_data):
        """It saves first, so errors from page load may already be fixed."""
        cycle, dept, work_item, line1, line2 = _order_with_two_lines(seed_workflow_data)

        _login(client, "test:admin")
        html = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
        ).get_data(as_text=True)

        submit_button = html[html.index('value="submit"') - 200:html.index('value="submit"')]
        assert "disabled" not in submit_button
