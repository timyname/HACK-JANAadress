import sqlite3

from tools.public_corpus import make_cases


def catalog():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE streets(id INTEGER, city TEXT, province TEXT, name TEXT, street_type TEXT);
        CREATE TABLE address_points(source_id TEXT, street_id INTEGER, house TEXT, district TEXT);
        INSERT INTO streets VALUES (1, 'Астана', 'Астана', 'Үлгі', 'улица');
        INSERT INTO address_points VALUES ('node/1', 1, '12/3', 'Сынақ');
    """)
    return connection


def test_public_corruptions_preserve_exact_house_and_dirty_street_surface():
    rows = make_cases(catalog(), count=4, seed="fixed")
    assert len(rows) == 4
    for row in rows:
        assert row["expected"]["house"] == "12/3"
        assert row["expected"]["street_name"] in row["input"]["raw_address"]
        assert row["source_url"] == "https://www.openstreetmap.org/node/1"
        assert row["kind"] == "reconstructed_from_public_osm"
    assert len({row["input"]["raw_address"] for row in rows}) == 4


def test_public_cohort_is_reproducible_and_reports_available_count():
    assert make_cases(catalog(), count=4, seed="fixed") == make_cases(catalog(), count=4, seed="fixed")
    assert len(make_cases(catalog(), count=1000, seed="fixed")) == 4


def test_public_cohort_balances_corruptions_before_count_limit():
    connection = catalog()
    connection.executemany("INSERT INTO address_points VALUES (?, 1, ?, 'Сынақ')",
                           [(f"node/{i}", str(i),) for i in range(2, 1002)])
    rows = make_cases(connection, count=1000, seed="fixed")
    assert {item["variant"] for item in rows} == {0, 1, 2, 3}
    assert len({item["source_id"] for item in rows}) == 1000


def test_province_type_is_not_duplicated():
    connection = catalog()
    connection.execute("UPDATE streets SET province = 'Акмолинская область'")
    for item in make_cases(connection, count=4, seed="fixed"):
        assert "область область" not in item["input"]["raw_address"]


def test_prefix_province_and_suffix_district_are_not_duplicated():
    connection = catalog()
    connection.execute("UPDATE streets SET province = 'Область Абай'")
    connection.execute("UPDATE address_points SET district = 'Есильский район'")
    for item in make_cases(connection, count=4, seed="fixed"):
        assert "Область Абай область" not in item["input"]["raw_address"]
        assert "район Есильский район" not in item["input"]["raw_address"]


def test_district_oracle_omits_type_marker_required_by_organizer():
    connection = catalog()
    for canonical, public_name in (("район Алматы", "Алматы"),
                                   ("Есильский район", "Есильский"),
                                   ("Жаңа аудан", "Жаңа аудан")):
        connection.execute("UPDATE address_points SET district = ?", (canonical,))
        for item in make_cases(connection, count=4, seed="fixed"):
            assert item["expected"]["district"] == public_name
            assert canonical in item["input"]["raw_address"]
