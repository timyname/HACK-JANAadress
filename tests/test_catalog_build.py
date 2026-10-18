"""Atomic SQLite construction against source-normalization edge cases."""

import sqlite3
from contextlib import closing

import pytest

from tools.build_gazetteer import AdminRecord, StreetRecord, create_database


def test_admin_aliases_deduplicate_normalized_keys_within_each_entity(tmp_path):
    path = tmp_path / "catalog.sqlite"
    admins = [
        AdminRecord("city", "Астана", aliases=("астана", "АСТАНА", "г.Астана", "г. Астана")),
        AdminRecord("city", "Астана", aliases=("астана",), province="Другая область"),
    ]
    create_database(path, admins, [], [], [])
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM admin_records").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM admin_aliases").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM admin_aliases WHERE normalized='астана'").fetchone()[0] == 2


def test_street_aliases_deduplicate_normalized_keys_without_merging_streets(tmp_path):
    path = tmp_path / "catalog.sqlite"
    streets = [
        StreetRecord("way/1", "Арман", aliases=("АРМАН", "ул. Арман", "ул Арман")),
        StreetRecord("way/2", "Арман", aliases=("арман",)),
    ]
    create_database(path, [], streets, [], [])
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM streets").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM street_aliases").fetchone()[0] == 3


def test_failed_build_keeps_previous_database_and_closes_temporary_file(tmp_path):
    path = tmp_path / "catalog.sqlite"
    create_database(path, [AdminRecord("city", "Сынақ")], [], [], [])
    original = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
        create_database(path, [AdminRecord("not-an-admin-field", "Ошибка")], [], [], [])
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".catalog.sqlite.*")) == []
