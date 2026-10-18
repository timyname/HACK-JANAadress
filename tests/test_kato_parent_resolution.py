"""Public district lookup preserves conservative KATO parent resolution."""

import pytest

from jana.gazetteer import Gazetteer
from tools.build_gazetteer import AdminRecord, create_database


PROVINCE = "Регион"
DISTRICT = "район Тестовый"
DISTRICT_CODE = "711013100"


def _city(name, code, province=PROVINCE):
    return AdminRecord(
        "city",
        name,
        aliases=(name.lower(), name.upper()),
        kato_code=code,
        province=province,
        city=name,
    )


def _district(city=None):
    return AdminRecord(
        "district",
        DISTRICT,
        kato_code=DISTRICT_CODE,
        province=PROVINCE,
        city=city,
        district=DISTRICT,
    )


@pytest.mark.parametrize(
    "records,requested_city,expected_conflict",
    [
        pytest.param(
            [_city("Outer", "710000000"), _city("Inner", "711010000"), _district()],
            "Inner",
            False,
            id="unique-longest-prefix-selected",
        ),
        pytest.param(
            [_city("Outer", "710000000"), _city("Inner", "711010000"), _district()],
            "Outer",
            True,
            id="shorter-prefix-parent-rejected",
        ),
        pytest.param(
            [_city("First", "711010000"), _city("Second", "711010000"), _district()],
            "First",
            True,
            id="ambiguous-longest-prefix-unresolved",
        ),
        pytest.param(
            [_city("Other", "721010000"), _district()],
            "Other",
            True,
            id="missing-code-ancestor-unresolved",
        ),
        pytest.param(
            [_city("Other", "711010000", province="Другой регион"), _district()],
            "Other",
            True,
            id="matching-code-in-other-province-rejected",
        ),
        pytest.param(
            [_city("Candidate", "711010000"), _district(city="Explicit")],
            "Explicit",
            False,
            id="explicit-district-city-retained",
        ),
        pytest.param(
            [_city("Candidate", "711010000"), _district(city="Explicit")],
            "Candidate",
            True,
            id="inferred-city-cannot-override-explicit-city",
        ),
    ],
)
def test_resolve_district_kato_parent_boundaries(
    tmp_path, records, requested_city, expected_conflict
):
    path = tmp_path / "catalog.sqlite3"
    create_database(path, records, [], [], [])
    with Gazetteer(path) as gazetteer:
        result = gazetteer.resolve_district(
            "Тестовый", city=requested_city, province=PROVINCE
        )

    assert result["source"] == "gazetteer"
    assert result["matched"] is True
    assert result["ambiguous"] is expected_conflict
    if expected_conflict:
        assert result["parent_conflict"] is True
        assert "value" not in result
        assert "kato_code" not in result
    else:
        assert result["parent_match"] is True
        assert result["value"] == DISTRICT
        assert result["kato_code"] == DISTRICT_CODE
        assert not result.get("parent_conflict")
