"""Pure metric helpers. No database access.

Percentages are Decimal, never float: integer cents convert to Decimal exactly, so the
only rounding is the explicit one to 1 decimal place.
"""

from decimal import ROUND_HALF_UP, Decimal

_ONE_DECIMAL = Decimal("0.1")


def _require_int(name: str, value: int) -> None:
    # Floats would bring binary rounding errors into Decimal; bool is an int subclass but not an amount.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer number of cents, got {value!r}")


def _percent(numerator: int, denominator: int) -> Decimal:
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(_ONE_DECIMAL, rounding=ROUND_HALF_UP)


def pct_change(old_cents: int, new_cents: int) -> Decimal | None:
    """Percentage change from old to new, rounded to 1 decimal place.

    Decimal("-25.0") means a 25% decrease. Returns None when old_cents is 0: there is
    no baseline, which is different from "no change".
    """
    _require_int("old_cents", old_cents)
    _require_int("new_cents", new_cents)
    if old_cents == 0:
        return None
    return _percent(new_cents - old_cents, old_cents)


def share(part_cents: int, total_cents: int) -> Decimal | None:
    """part as a percentage of total, rounded to 1 decimal place.

    Decimal("71.8") means 71.8%. When part and total have opposite signs the share is
    negative (that part moved against the total). Returns None when total_cents is 0.
    """
    _require_int("part_cents", part_cents)
    _require_int("total_cents", total_cents)
    if total_cents == 0:
        return None
    return _percent(part_cents, total_cents)
