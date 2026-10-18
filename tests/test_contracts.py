import math

import pytest

from jana.contracts import PUBLIC_FIELDS, InputError, validate_batch, validate_output


def test_input_preserves_duplicate_ids_unicode_and_order():
    records = [{"id": "x", "raw_address": "  Ә\u200bғ  "}, {"id": "x", "raw_address": ""}]
    assert validate_batch(records) == records


@pytest.mark.parametrize("value", [{}, None, [None], [{"id": 1, "raw_address": "a"}], [{"id": "x", "raw_address": []}]])
def test_bad_input_has_no_payload_in_diagnostic(value):
    with pytest.raises(InputError) as exc:
        validate_batch(value)
    assert "raw_address" not in str(exc.value)


def test_resource_limits_are_explicit():
    with pytest.raises(InputError):
        validate_batch([{"id": "x", "raw_address": "a"}] * 10001)
    with pytest.raises(InputError):
        validate_batch([{"id": "x", "raw_address": "a" * 8193}])


def test_output_contract_rejects_internal_fields_and_nonfinite_confidence():
    inputs = [{"id": "x", "raw_address": "unchanged"}]
    record = dict.fromkeys(PUBLIC_FIELDS)
    record.update(inputs[0], confidence=0.5)
    assert validate_output(inputs, [record]) == [record]
    with pytest.raises(ValueError):
        validate_output(inputs, [{**record, "debug": True}])
    for number in [math.nan, math.inf, -1, 2, True]:
        with pytest.raises(ValueError):
            validate_output(inputs, [{**record, "confidence": number}])


def test_output_cannot_lose_positions_or_rewrite_original():
    inputs = [{"id": "x", "raw_address": " a "}]
    record = dict.fromkeys(PUBLIC_FIELDS)
    record.update(id="x", raw_address="a", confidence=0.1)
    with pytest.raises(ValueError):
        validate_output(inputs, [record])
    with pytest.raises(ValueError):
        validate_output(inputs, [])
