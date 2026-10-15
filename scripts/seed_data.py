"""Generate deterministic synthetic sales data for a consumer electronics retailer.

Planted patterns (month 1 = start month):
  a. Month 3 revenue is 15-20% below month 2.
  b. Most of that decline comes from a StudioBook 16 stockout in West from day 8 of month 3.
  c. Wearables & Tablets revenue grows about 5% every month.
  d. Month 4 revenue recovers to within 5% of month 2.

Usage:
    python -m scripts.seed_data [--db PATH] [--seed N] [--start YYYY-MM] [--months N]
"""

import argparse
import calendar
import random
import re
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

from app.db.connection import get_connection

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "app" / "db" / "schema.sql"
DEFAULT_DB_PATH = Path("data") / "groundedbi.db"

# --- Pattern targets. Month positions are 1-based: month 1 = start month. -----------------
REFERENCE_MONTH = 2  # the dip and the recovery are both measured against this month
DIP_MONTH = REFERENCE_MONTH + 1  # month of the revenue drop and the stockout (patterns a, b)
RECOVERY_MONTH = DIP_MONTH + 1  # month revenue returns to the reference level (pattern d)
MIN_MONTHS = RECOVERY_MONTH  # every pattern needs data up to the recovery month

REFERENCE_LIFT = 0.02  # steady products: REFERENCE_MONTH units vs month 1
DIP_DECLINE = 0.175  # total revenue drop in DIP_MONTH vs REFERENCE_MONTH; middle of the 15-20% band (a)
RECOVERY_RATIO = 1.0  # total revenue in RECOVERY_MONTH / REFERENCE_MONTH; pattern d allows +/-5%
LATE_GROWTH = 0.01  # steady products: monthly growth after RECOVERY_MONTH

GROWTH_CATEGORY = "Wearables & Tablets"  # grows every month and is excluded from the dip/recovery factors (c)
GROWTH_RATE = 0.05  # GROWTH_CATEGORY's compounding monthly growth, its only multiplier (c)

STOCKOUT_CELL = ("StudioBook 16", "West")  # (product, region) that runs out of stock in DIP_MONTH (b)
STOCKOUT_FIRST_DAY = 8  # first day of DIP_MONTH with no stock; earlier days sell at the reference daily rate
MAX_UNITS_DURING_STOCKOUT = 2  # "near-zero": at most this many units sell from STOCKOUT_FIRST_DAY on

UNIT_JITTER = 0.015  # +/- random noise applied to each monthly unit target
ORDER_SIZES = (1, 1, 1, 2, 2, 3)  # units per order, sampled uniformly, so most orders are 1 unit

# (name, category, list price in cents)
PRODUCTS = [
    ("UltraBook 14", "Laptops", 119_900),
    ("StudioBook 16", "Laptops", 249_900),
    ("Phone Lite", "Phones", 49_900),
    ("Phone Max", "Phones", 109_900),
    ("Wireless Earbuds", "Audio", 14_900),
    ("Noise-Cancelling Headphones", "Audio", 34_900),
    ("Smartwatch", "Wearables & Tablets", 29_900),
    ("Tablet 11", "Wearables & Tablets", 59_900),
]
REGIONS = ["North", "South", "East", "West"]

# Baseline units sold per month, in REGIONS order (North, South, East, West).
BASE_MONTHLY_UNITS = {
    "UltraBook 14": (60, 45, 65, 70),
    # West is the big creative-pro market, so a StudioBook stockout there hurts the whole business.
    "StudioBook 16": (30, 20, 35, 120),
    "Phone Lite": (120, 100, 130, 110),
    "Phone Max": (70, 50, 75, 80),
    "Wireless Earbuds": (200, 160, 210, 190),
    "Noise-Cancelling Headphones": (80, 60, 85, 90),
    "Smartwatch": (90, 70, 95, 100),
    "Tablet 11": (50, 40, 55, 60),
}

PRICE = {name: price for name, _, price in PRODUCTS}
CATEGORY = {name: category for name, category, _ in PRODUCTS}

# Cells are (product, region) pairs. Lists, not sets: set iteration order depends on
# per-process string hashing, which would make float sums (and so the data) non-deterministic.
CELLS = [(product, region) for product, _, _ in PRODUCTS for region in REGIONS]
GROWTH_CELLS = [cell for cell in CELLS if CATEGORY[cell[0]] == GROWTH_CATEGORY]
STEADY_CELLS = [cell for cell in CELLS if CATEGORY[cell[0]] != GROWTH_CATEGORY]
BASE_UNITS = {
    (product, region): BASE_MONTHLY_UNITS[product][i]
    for product in BASE_MONTHLY_UNITS
    for i, region in enumerate(REGIONS)
}


class SaleRow(NamedTuple):
    sale_date: str  # ISO YYYY-MM-DD
    product: str
    region: str
    quantity: int
    unit_price_cents: int
    revenue_cents: int


def parse_start_month(start: str) -> tuple[int, int]:
    """Parse 'YYYY-MM' into (year, month). Raises ValueError if invalid."""
    # strptime alone would also accept '2026-1', so require the exact shape first.
    if not isinstance(start, str) or not re.fullmatch(r"\d{4}-\d{2}", start):
        raise ValueError(f"start must be in YYYY-MM format, got {start!r}")
    try:
        parsed = datetime.strptime(start, "%Y-%m")
    except ValueError:
        raise ValueError(f"start is not a valid month: {start!r}") from None
    return parsed.year, parsed.month


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + offset
    return index // 12, index % 12 + 1


def _days_in(year_month: tuple[int, int]) -> int:
    return calendar.monthrange(*year_month)[1]


def _revenue(cells: list[tuple[str, str]], factor: float = 1.0) -> float:
    return factor * sum(BASE_UNITS[cell] * PRICE[cell[0]] for cell in cells)


def _growth_factor(position: int) -> float:
    """GROWTH_CATEGORY multiplier for a 1-based month position, compounding from month 1."""
    return (1 + GROWTH_RATE) ** (position - 1)


def monthly_unit_targets(year_months: list[tuple[int, int]]) -> list[dict[tuple[str, str], float]]:
    """Expected units per (product, region) for each month, before random noise.

    Targets are monthly totals, not daily rates, so month length cannot break the patterns.
    The dip and recovery factors are solved from the pattern targets, over STEADY_CELLS only:
    GROWTH_CELLS keep their own compounding trend in every month.
    """
    reference_total = _revenue(GROWTH_CELLS, _growth_factor(REFERENCE_MONTH)) + _revenue(
        STEADY_CELLS, 1 + REFERENCE_LIFT
    )

    # Stockout: STOCKOUT_CELL sells at its reference-month daily rate until STOCKOUT_FIRST_DAY.
    reference_days = _days_in(year_months[REFERENCE_MONTH - 1])
    reference_daily = BASE_UNITS[STOCKOUT_CELL] * (1 + REFERENCE_LIFT) / reference_days
    stockout_units = reference_daily * (STOCKOUT_FIRST_DAY - 1)

    dipping_cells = [cell for cell in STEADY_CELLS if cell != STOCKOUT_CELL]
    dip_factor = (
        (1 - DIP_DECLINE) * reference_total
        - _revenue(GROWTH_CELLS, _growth_factor(DIP_MONTH))
        - stockout_units * PRICE[STOCKOUT_CELL[0]]
    ) / _revenue(dipping_cells)

    # STOCKOUT_CELL restocks in RECOVERY_MONTH: it takes the same factor as every other steady cell.
    recovery_factor = (
        RECOVERY_RATIO * reference_total - _revenue(GROWTH_CELLS, _growth_factor(RECOVERY_MONTH))
    ) / _revenue(STEADY_CELLS)

    targets = []
    for position in range(1, len(year_months) + 1):
        if position < REFERENCE_MONTH:
            steady_factor = 1.0
        elif position == REFERENCE_MONTH:
            steady_factor = 1 + REFERENCE_LIFT
        elif position == DIP_MONTH:
            steady_factor = dip_factor
        else:
            steady_factor = recovery_factor * (1 + LATE_GROWTH) ** (position - RECOVERY_MONTH)

        month = {cell: BASE_UNITS[cell] * steady_factor for cell in STEADY_CELLS}
        month.update({cell: BASE_UNITS[cell] * _growth_factor(position) for cell in GROWTH_CELLS})
        if position == DIP_MONTH:
            month[STOCKOUT_CELL] = stockout_units
        targets.append(month)
    return targets


def _split_into_orders(rng: random.Random, units: int, days: list[date]) -> list[tuple[date, int]]:
    """Split a unit count into small orders on random days."""
    orders = []
    while units > 0:
        quantity = min(units, rng.choice(ORDER_SIZES))
        orders.append((rng.choice(days), quantity))
        units -= quantity
    return orders


def generate_sales(seed: int, start: str, months: int) -> list[SaleRow]:
    """Return synthetic sales rows for `months` months from `start`. Pure: no database access."""
    year, month = parse_start_month(start)
    if not isinstance(months, int) or months < MIN_MONTHS:
        raise ValueError(f"months must be an integer >= {MIN_MONTHS}, got {months!r}")

    year_months = [_add_months(year, month, i) for i in range(months)]
    targets = monthly_unit_targets(year_months)
    rng = random.Random(seed)  # private generator; never the global random module

    rows = []
    for position, (y, m) in enumerate(year_months, start=1):
        days = [date(y, m, d) for d in range(1, _days_in((y, m)) + 1)]
        for product, region in CELLS:
            target = targets[position - 1][product, region]
            units = round(target * rng.uniform(1 - UNIT_JITTER, 1 + UNIT_JITTER))
            if position == DIP_MONTH and (product, region) == STOCKOUT_CELL:
                orders = _split_into_orders(rng, units, days[: STOCKOUT_FIRST_DAY - 1])
                trickle = rng.randint(0, MAX_UNITS_DURING_STOCKOUT)
                orders += _split_into_orders(rng, trickle, days[STOCKOUT_FIRST_DAY - 1 :])
            else:
                orders = _split_into_orders(rng, units, days)

            price = PRICE[product]
            rows.extend(
                SaleRow(day.isoformat(), product, region, quantity, price, quantity * price)
                for day, quantity in orders
            )

    rows.sort()
    return rows


def seed_database(db_path: str | Path, seed: int = 42, start: str = "2026-01", months: int = 6) -> int:
    """Recreate the database at db_path from schema.sql and load synthetic data. Returns the sales row count."""
    # Generate (and validate arguments) before touching any existing database file.
    rows = generate_sales(seed, start, months)

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)

    conn = get_connection(db_path)
    try:
        # executescript is safe here: the SQL comes from our own schema file, never from user input.
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        with conn:  # one transaction: commits on success, rolls back on any error
            conn.executemany(
                "INSERT INTO products (name, category, unit_price_cents) VALUES (?, ?, ?)",
                PRODUCTS,
            )
            conn.executemany("INSERT INTO regions (name) VALUES (?)", [(name,) for name in REGIONS])

            product_ids = {r["name"]: r["product_id"] for r in conn.execute("SELECT product_id, name FROM products")}
            region_ids = {r["name"]: r["region_id"] for r in conn.execute("SELECT region_id, name FROM regions")}

            conn.executemany(
                "INSERT INTO sales (sale_date, product_id, region_id, quantity, unit_price_cents, revenue_cents) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        row.sale_date,
                        product_ids[row.product],
                        region_ids[row.region],
                        row.quantity,
                        row.unit_price_cents,
                        row.revenue_cents,
                    )
                    for row in rows
                ],
            )
    finally:
        conn.close()
    return len(rows)


def format_cents(cents: int) -> str:
    dollars, remainder = divmod(cents, 100)
    return f"${dollars:,}.{remainder:02d}"


def print_summary(db_path: str | Path) -> None:
    """Print row count, date range and revenue per month, read back from the database."""
    conn = get_connection(db_path)
    try:
        totals = conn.execute("SELECT COUNT(*) AS n, MIN(sale_date) AS first, MAX(sale_date) AS last FROM sales").fetchone()
        monthly = conn.execute(
            "SELECT substr(sale_date, 1, 7) AS month, SUM(revenue_cents) AS revenue "
            "FROM sales GROUP BY month ORDER BY month"
        ).fetchall()
    finally:
        conn.close()

    print(f"Seeded {db_path}")
    print(f"  sales rows: {totals['n']:,}")
    print(f"  date range: {totals['first']} to {totals['last']}")
    print("  revenue by month:")
    previous = None
    for row in monthly:
        change = f"  ({(row['revenue'] / previous - 1) * 100:+.1f}%)" if previous else ""
        print(f"    {row['month']}  {format_cents(row['revenue']):>16}{change}")
        previous = row["revenue"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create and seed the GroundedBI SQLite database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="database file path")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument("--start", default="2026-01", help="first month, YYYY-MM")
    parser.add_argument("--months", type=int, default=6, help=f"number of months (>= {MIN_MONTHS})")
    args = parser.parse_args(argv)

    try:
        seed_database(args.db, seed=args.seed, start=args.start, months=args.months)
    except ValueError as exc:
        parser.error(str(exc))
    print_summary(args.db)


if __name__ == "__main__":
    main()
