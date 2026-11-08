"""Schema checks for unit names and typed fields.

The seed test drives `flask db upgrade` on a scratch SQLite file, because
create_all() runs no data migrations.
"""
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import InvUnit

REPO = Path(__file__).resolve().parents[2]


def _flask(db_path, *args):
    env = dict(os.environ, FLASK_SKIP_DOTENV="1", APP_ENV="development", SECRET_KEY="x",
               DATABASE_URL=f"sqlite:///{db_path}", FLASK_APP="app:create_app")
    env.pop("FLASK_DEBUG", None)
    result = subprocess.run([str(REPO / ".venv/bin/flask"), *args], cwd=REPO, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]


def _rows(db_path, sql):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _kind_codes(db_path):
    return [r[0] for r in _rows(db_path, "SELECT code FROM inv_gear_kinds ORDER BY position")]


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    path = tmp_path_factory.mktemp("mig") / "m.db"
    _flask(path, "db", "upgrade")
    return path


EXPECTED = {
    "TV": [("BRAND", "TEXT", None, []), ("SIZE", "NUMBER", "in", []),
           ("RESOLUTION", "CHOICE", None, ["720p", "1080p", "4k"]),
           ("HDMI", "BOOL", None, []), ("DISPLAYPORT", "BOOL", None, [])],
    "MONITOR": [("SIZE", "NUMBER", "in", []),
                ("RESOLUTION", "CHOICE", None,
                 ["1920x1080", "1920x1200", "2560x1440", "4k"]),
                ("HDMI", "BOOL", None, []), ("DISPLAYPORT", "BOOL", None, [])],
    "PRINTER": [("TONER", "TEXT", None, []), ("COLOR", "BOOL", None, [])],
    "LABEL_PRINTER": [("MEDIA", "TEXT", None, [])],
    "LAMINATOR": [],
}


def _seeded(db_path):
    seeded = {}
    for kind_id, code in _rows(db_path, "SELECT id, code FROM inv_gear_kinds ORDER BY position"):
        fields = []
        for field_id, fcode, ftype, unit in _rows(
                db_path, "SELECT id, code, field_type, unit_label FROM inv_fields "
                         f"WHERE gear_kind_id = {kind_id} ORDER BY position"):
            labels = [r[0] for r in _rows(
                db_path, "SELECT label FROM inv_field_choices "
                         f"WHERE field_id = {field_id} ORDER BY position")]
            fields.append((fcode, ftype, unit, labels))
        seeded[code] = fields
    return seeded


def test_migration_seeds_gear_kinds_and_fields(migrated):
    assert _seeded(migrated) == EXPECTED
    assert _kind_codes(migrated) == list(EXPECTED)


def test_upgrade_is_safe_on_a_database_that_already_has_the_seed(tmp_path):
    path = tmp_path / "m.db"
    _flask(path, "db", "upgrade")
    _flask(path, "db", "downgrade", "iv7p3q9r2s4t")
    _flask(path, "db", "upgrade")
    codes = _kind_codes(path)
    assert len(codes) == len(set(codes)) == 5
    assert _seeded(path) == EXPECTED


def _unit(inv, key, condition="IN_SERVICE"):
    unit = InvUnit(item_id=inv.laptop.id, condition=condition, name=key, name_key=key)
    db.session.add(unit)
    db.session.flush()
    return unit


def test_two_live_units_cannot_share_a_name_key(inv):
    _unit(inv, "tv 1")
    with pytest.raises(IntegrityError):
        _unit(inv, "tv 1")
    db.session.rollback()


def test_a_retired_unit_does_not_hold_its_name_key(inv):
    _unit(inv, "tv 2", condition="RETIRED")
    _unit(inv, "tv 2")
    db.session.rollback()
