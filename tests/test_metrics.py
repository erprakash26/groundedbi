from decimal import Decimal

import pytest

from app.services.metrics import pct_change, share


def test_pct_change_known_case():
    assert pct_change(200, 150) == Decimal("-25.0")
    assert pct_change(100, 100) == Decimal("0.0")
    assert pct_change(100, 250) == Decimal("150.0")


def test_pct_change_rounds_to_one_decimal():
    assert pct_change(3, 1) == Decimal("-66.7")


def test_pct_change_from_zero_is_none():
    assert pct_change(0, 500) is None
    assert pct_change(0, 0) is None


def test_share_known_cases():
    assert share(718, 1000) == Decimal("71.8")
    assert share(1, 3) == Decimal("33.3")


def test_share_rounds_half_up():
    # 1/16 = 6.25%. Half-up gives 6.3; banker's rounding (Python's round default) would give 6.2.
    assert share(1, 16) == Decimal("6.3")


def test_share_signs():
    assert share(-50, -100) == Decimal("50.0")  # part of a decline, same direction
    assert share(50, -100) == Decimal("-50.0")  # moved against the total


def test_share_of_zero_total_is_none():
    assert share(5, 0) is None


def test_results_are_decimal():
    assert isinstance(pct_change(200, 150), Decimal)
    assert isinstance(share(1, 3), Decimal)


@pytest.mark.parametrize("args", [(1.5, 2), (100, 2.0), (True, 2), ("100", 50)])
def test_non_integer_cents_raise_type_error(args):
    with pytest.raises(TypeError):
        pct_change(*args)
    with pytest.raises(TypeError):
        share(*args)
