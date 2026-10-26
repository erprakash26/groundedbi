import pytest

from app.services.analytics import Period
from app.services.coverage import MAX_LISTED_UNCOVERED_MONTHS, DataCoverage, PeriodCoverage, data_coverage, period_coverage

DATA = DataCoverage("2026-01", "2026-06")


def test_data_coverage_of_seeded_database(seeded_conn):
    assert data_coverage(seeded_conn) == DataCoverage("2026-01", "2026-06")


def test_data_coverage_of_empty_database(empty_conn):
    assert data_coverage(empty_conn) is None


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-01", "2026-06", PeriodCoverage(True, [], 0)),
        ("2026-03", "2026-03", PeriodCoverage(True, [], 0)),
        ("2025-11", "2026-02", PeriodCoverage(False, ["2025-11", "2025-12"], 2)),  # before the data
        ("2026-05", "2026-08", PeriodCoverage(False, ["2026-07", "2026-08"], 2)),  # after the data
        ("2025-12", "2026-07", PeriodCoverage(False, ["2025-12", "2026-07"], 2)),  # both sides
        ("2030-01", "2030-02", PeriodCoverage(False, ["2030-01", "2030-02"], 2)),  # entirely outside
    ],
)
def test_period_coverage(start, end, expected):
    assert period_coverage(Period(start, end), DATA) == expected


def test_long_uncovered_list_is_capped_but_counted():
    result = period_coverage(Period("2000-01", "2099-12"), DATA)
    assert len(result.uncovered_months) == MAX_LISTED_UNCOVERED_MONTHS
    assert result.uncovered_months[0] == "2000-01"
    assert result.uncovered_month_count == 100 * 12 - 6


def test_no_data_means_every_month_is_uncovered():
    assert period_coverage(Period("2026-01", "2026-03"), None) == PeriodCoverage(
        False, ["2026-01", "2026-02", "2026-03"], 3
    )
