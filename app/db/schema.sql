-- GroundedBI schema. Money is stored as integer cents, never floats.
-- STRICT tables make SQLite reject values of the wrong type (e.g. 12.5 in an INTEGER column).

CREATE TABLE products (
    product_id       INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL UNIQUE,
    category         TEXT    NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents > 0)
) STRICT;

CREATE TABLE regions (
    region_id INTEGER PRIMARY KEY,
    name      TEXT    NOT NULL UNIQUE
) STRICT;

-- Each sale records the price actually charged, so later list-price changes
-- never rewrite historical revenue.
CREATE TABLE sales (
    sale_id          INTEGER PRIMARY KEY,
    sale_date        TEXT    NOT NULL
                             CHECK (sale_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    product_id       INTEGER NOT NULL REFERENCES products (product_id),
    region_id        INTEGER NOT NULL REFERENCES regions (region_id),
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents > 0),
    revenue_cents    INTEGER NOT NULL CHECK (revenue_cents = quantity * unit_price_cents)
) STRICT;

CREATE INDEX idx_sales_sale_date  ON sales (sale_date);
CREATE INDEX idx_sales_product_id ON sales (product_id);
CREATE INDEX idx_sales_region_id  ON sales (region_id);
