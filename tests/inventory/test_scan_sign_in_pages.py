"""The scanner sign-in screen, the signed-in page and the card guards."""
import os
import shutil
import subprocess

import pytest

from app import db
from app.inventory.resolver import resolve
from app.inventory.shifts import current_shift, start_shift
from app.inventory.sign_in import new_card, new_pin
from app.models import User
from app.routes.inventory.scan_api import _can_create_tag
from tests.inventory.helpers import enrolled_client, login


@pytest.fixture
def app_ctx(inv):
    return inv


def test_sign_in_screen_has_a_keypad_and_no_names(client, inv):
    new_pin(db.session.get(User, "test:invadmin")); db.session.commit()
    enrolled_client(client, station_code="COUNT")
    page = client.get("/inv/scan/who").get_data(as_text=True)
    assert "Scan your card" in page and 'inputmode="numeric"' in page
    assert "Invadmin" not in page and "person_name" not in page


def test_sign_in_screen_uses_the_shell_without_flag_or_sign_out(client, inv):
    enrolled_client(client, station_code="COUNT")
    page = client.get("/inv/scan/who").get_data(as_text=True)
    assert 'id="scan-input"' in page and "inventory/scan.js" in page
    assert 'data-sign-in-url="/inv/scan/api/sign-in"' in page
    assert 'data-signed-in-url="/inv/scan/signed-in"' in page
    assert 'id="flag-btn"' not in page and 'id="done-btn"' not in page
    assert "No card? Type your PIN" in page and 'type="password"' in page
    assert "Not signed in" in page


def test_an_operator_on_the_sign_in_screen_gets_no_keypad(client, inv):
    login(client, "test:operator")
    page = client.get("/inv/scan/who").get_data(as_text=True)
    assert "you do not need to" in page and 'inputmode="numeric"' not in page


def test_signed_in_page_names_the_person(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    start_shift(device, "test:operator"); db.session.commit()
    page = client.get("/inv/scan/signed-in").get_data(as_text=True)
    assert "Signed in as" in page and "Operator" in page and "Not me? Sign out" in page
    assert 'id="flag-btn"' not in page and 'id="done-btn"' not in page


def test_signed_in_page_with_nobody_signed_in_goes_to_sign_in(client, inv):
    enrolled_client(client, station_code="COUNT")
    r = client.get("/inv/scan/signed-in")
    assert r.status_code == 302 and r.headers["Location"].endswith("/inv/scan/who")


def test_resolve_names_a_card(client, inv):
    card = new_card(db.session.get(User, "test:operator")); db.session.commit()
    enrolled_client(client, station_code="LOOKUP")
    assert client.post("/inv/scan/api/resolve", json={"code": card}).get_json()["kind"] == "CARD"


def test_a_card_shape_resolves_as_a_card_before_any_other_rank(inv):
    assert resolve(" op-abcdefghjkmn ").kind == "CARD"
    assert resolve("OP-ABCDEFGHJKM").kind != "CARD"


def test_card_code_never_becomes_an_asset_tag(app_ctx):
    assert _can_create_tag("OP-ABCDEFGHJKMN") is False


def test_label_options_refuse_a_card(client, inv):
    login(client, "test:operator")
    r = client.post("/inv/scan/api/label/options", json={"code": "OP-ABCDEFGHJKMN"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": False, "message": "That is a sign-in card."}


def test_count_find_refuses_a_card(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    start_shift(device, "test:operator"); db.session.commit()
    r = client.post("/inv/scan/api/count/find", json={"code": "OP-ABCDEFGHJKMN"}).get_json()
    assert r == {"ok": False, "message": "That is a sign-in card."}


def test_lookup_needs_no_sign_in(client, inv):
    enrolled_client(client, station_code="LOOKUP")
    assert client.get("/inv/scan/").status_code == 200


def test_scanner_pages_say_sign_out(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    start_shift(device, "test:operator"); db.session.commit()
    page = client.get("/inv/scan/count").get_data(as_text=True)
    assert "Sign out" in page and "Done for now" not in page


def test_a_switch_by_pin_replaces_the_shift(client, inv):
    pin = new_pin(db.session.get(User, "test:invadmin")); db.session.commit()
    device = enrolled_client(client, station_code="COUNT")
    start_shift(device, "test:operator"); db.session.commit()
    asked = client.post("/inv/scan/api/sign-in", json={"pin": pin}).get_json()
    assert asked["switch_to"] == "Invadmin" and asked["current_first"] == "Operator"
    assert client.post("/inv/scan/api/sign-in",
                       json={"pin": pin, "switch": True}).get_json()["ok"] is True
    db.session.rollback()
    assert current_shift(device).user_id == "test:invadmin"


SIGN_IN_SCRIPT = r"""
const assert = require('assert');
const S = require(process.env.SCAN_JS);
const {isCardCode, switchPromptText, MESSAGES} = S;
assert.strictEqual(isCardCode('OP-ABCDEFGHJKMN'), true);
assert.strictEqual(isCardCode(' op-0123456789vz \n'), true);
assert.strictEqual(isCardCode('OP-ABCDEFGHJKM'), false);      // 11 characters
assert.strictEqual(isCardCode('OP-ABCDEFGHJKMNP'), false);    // 13 characters
assert.strictEqual(isCardCode('OP-ABCDEFGHIJKL'), false);     // I and L are not Crockford
assert.strictEqual(isCardCode('XOP-ABCDEFGHJKMN'), false);
assert.strictEqual(isCardCode('FEST10001'), false);
assert.strictEqual(isCardCode(null), false);
const ask = {switch_to: 'Sam Rivera', switch_first: 'Sam', current_first: 'Alex',
             message: "This is Sam Rivera's card."};
assert.deepStrictEqual(switchPromptText(ask, false), {
  text: "This is Sam Rivera's card.", go: 'Switch to Sam', keep: 'Keep Alex signed in'});
assert.deepStrictEqual(switchPromptText(ask, true), {
  text: 'Switch to Sam Rivera?', go: 'Switch to Sam', keep: 'Keep Alex signed in'});
assert.strictEqual(MESSAGES.signedInAlready, 'You are signed in already.');
assert.strictEqual(MESSAGES.scanCard, 'Scan your sign-in card, or type your PIN.');
console.log('sign-in ok');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_card_shape_and_switch_wording_under_node(tmp_path):
    """Runs the pure card check and prompt wording under node; it does not drive a browser."""
    script = tmp_path / "sign_in_check.js"
    script.write_text(SIGN_IN_SCRIPT)
    path = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static",
                        "inventory", "scan.js")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True,
                            env={**os.environ, "SCAN_JS": os.path.abspath(path)})
    assert result.returncode == 0, result.stderr
    assert "sign-in ok" in result.stdout


LOOKUP_CARD_SCRIPT = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const {MESSAGES} = require(process.env.SCAN_JS);
let shown = null, flagged = 'unset', opts = null, answer = null;
const S = {esc: s => String(s), showMessage: t => { shown = t; }, showPanel() {}, feedback() {},
  choose() {}, messages: MESSAGES, setFlagSubject: s => { flagged = s; },
  start: o => { opts = o; }, read: () => Promise.resolve(answer)};
const document = {currentScript: {dataset: {resolveUrl: '/r'}},
  getElementById: () => ({appendChild() {}}), createElement: () => ({addEventListener() {}})};
vm.runInNewContext(fs.readFileSync(process.env.LOOKUP_JS, 'utf8'),
  {window: {InvScan: S, InvLabel: {reset() {}}}, document});
(async () => {
  answer = {ok: true, kind: 'CARD', code: 'OP-ABCDEFGHJKMN', prefix: null};
  assert.strictEqual((await opts.onScan('OP-ABCDEFGHJKMN')).ok, false);
  assert.strictEqual(shown, 'That is a sign-in card.');
  assert.strictEqual(flagged, null);
  console.log('lookup card ok');
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_look_up_names_a_typed_card_under_node(tmp_path):
    """Runs station_lookup.js on a stub page under node; it does not drive a browser."""
    script = tmp_path / "lookup_card_check.js"
    script.write_text(LOOKUP_CARD_SCRIPT)
    static = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True,
        env={**os.environ, "SCAN_JS": os.path.abspath(os.path.join(static, "scan.js")),
             "LOOKUP_JS": os.path.abspath(os.path.join(static, "station_lookup.js"))})
    assert result.returncode == 0, result.stderr
    assert "lookup card ok" in result.stdout
