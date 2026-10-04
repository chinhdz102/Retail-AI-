CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE IF NOT EXISTS categories (
    category_code text PRIMARY KEY,
    category_name text NOT NULL,
    department text,
    sub_department text,
    category_hierarchy text
);

CREATE TABLE IF NOT EXISTS products (
    product_id text PRIMARY KEY,
    item_no text UNIQUE,
    barcode text UNIQUE,
    name text NOT NULL,
    brand text,
    category_code text REFERENCES categories(category_code),
    department text,
    sub_department text,
    spicy boolean,
    search_name text NOT NULL,
    search_title text NOT NULL DEFAULT '',
    search_brand text NOT NULL DEFAULT '',
    search_category text NOT NULL DEFAULT '',
    uom text,
    image_url text,
    description text,
    seo_url text,
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE products ADD COLUMN IF NOT EXISTS search_title text NOT NULL DEFAULT '';
UPDATE products
SET search_title = lower(unaccent(replace(replace(name, 'Đ', 'D'), 'đ', 'd')))
WHERE search_title = '';

CREATE TABLE IF NOT EXISTS store_product_snapshots (
    snapshot_id bigserial PRIMARY KEY,
    product_id text NOT NULL REFERENCES products(product_id),
    store_code text NOT NULL,
    dataset_version text NOT NULL,
    regular_price_vnd bigint NOT NULL CHECK (regular_price_vnd >= 0),
    sale_price_vnd bigint NOT NULL CHECK (sale_price_vnd >= 0),
    effective_price_vnd bigint NOT NULL CHECK (effective_price_vnd >= 0),
    physical_stock integer NOT NULL CHECK (physical_stock >= 0),
    shelf text,
    shelf_source text NOT NULL DEFAULT 'SIMULATED',
    discount_promotion text,
    price_quality_status text NOT NULL CHECK (price_quality_status IN ('NORMAL', 'SALE')),
    source_timestamp timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(product_id, store_code, dataset_version)
);

CREATE TABLE IF NOT EXISTS catalog_quarantine_records (
    quarantine_id bigserial PRIMARY KEY,
    dataset_version text NOT NULL,
    product_id text NOT NULL,
    reason text NOT NULL,
    raw_record jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(dataset_version, product_id)
);

CREATE INDEX IF NOT EXISTS ix_products_search_name_trgm ON products USING gin (search_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_products_search_title_trgm ON products USING gin (search_title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_products_search_brand_trgm ON products USING gin (search_brand gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_products_search_category_trgm ON products USING gin (search_category gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_snapshots_latest ON store_product_snapshots(store_code, product_id, source_timestamp DESC);
