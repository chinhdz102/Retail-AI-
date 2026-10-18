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

-- A dataset is comparable for "newly listed" questions only after the full
-- source file has been processed and its snapshots committed successfully.
CREATE TABLE IF NOT EXISTS catalog_import_runs (
    store_code text NOT NULL,
    dataset_version text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    source_record_count integer NOT NULL CHECK (source_record_count >= 0),
    accepted_record_count integer NOT NULL CHECK (accepted_record_count >= 0),
    quarantined_record_count integer NOT NULL CHECK (quarantined_record_count >= 0),
    source_timestamp_min timestamptz,
    source_timestamp_max timestamptz,
    is_complete boolean NOT NULL DEFAULT false,
    PRIMARY KEY (store_code, dataset_version),
    CHECK (accepted_record_count + quarantined_record_count <= source_record_count),
    CHECK (
        NOT is_complete OR
        accepted_record_count + quarantined_record_count = source_record_count
    )
);
ALTER TABLE catalog_import_runs
    ADD COLUMN IF NOT EXISTS is_complete boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS retail_organizations (
    organization_code text PRIMARY KEY,
    legal_name text NOT NULL,
    display_name text NOT NULL,
    organization_type text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS retail_stores (
    store_code text PRIMARY KEY,
    organization_code text REFERENCES retail_organizations(organization_code),
    store_name text NOT NULL,
    address text NOT NULL,
    phone text,
    opening_hours jsonb,
    timezone text NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    latitude double precision CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision CHECK (longitude BETWEEN -180 AND 180),
    source_name text NOT NULL,
    source_uri text,
    verified_at timestamptz NOT NULL,
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);

CREATE TABLE IF NOT EXISTS retail_kiosks (
    kiosk_id text PRIMARY KEY,
    store_code text NOT NULL REFERENCES retail_stores(store_code),
    active boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Feed state distinguishes "no records" from "no trustworthy feed".
CREATE TABLE IF NOT EXISTS retail_data_feeds (
    store_code text NOT NULL REFERENCES retail_stores(store_code),
    feed_name text NOT NULL CHECK (feed_name IN ('PROMOTIONS', 'RECEIVING', 'NEARBY_PLACES')),
    provider text NOT NULL,
    status text NOT NULL CHECK (status IN ('ACTIVE', 'STALE', 'DISABLED')),
    last_success_at timestamptz,
    coverage_start_at timestamptz,
    coverage_end_at timestamptz,
    coverage_complete boolean NOT NULL DEFAULT false,
    source_uri text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (store_code, feed_name),
    CHECK (coverage_end_at IS NULL OR coverage_start_at IS NULL OR coverage_end_at >= coverage_start_at)
);
ALTER TABLE retail_data_feeds
    ADD COLUMN IF NOT EXISTS coverage_complete boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS promotion_campaigns (
    campaign_id text PRIMARY KEY,
    organization_code text REFERENCES retail_organizations(organization_code),
    store_code text REFERENCES retail_stores(store_code),
    campaign_name text NOT NULL,
    description text,
    terms text NOT NULL CHECK (btrim(terms) <> ''),
    eligibility jsonb NOT NULL DEFAULT '{}'::jsonb,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'ENDED')),
    source_name text NOT NULL,
    source_uri text,
    verified_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at)
);

CREATE TABLE IF NOT EXISTS promotion_campaign_products (
    campaign_id text NOT NULL REFERENCES promotion_campaigns(campaign_id) ON DELETE CASCADE,
    product_id text NOT NULL REFERENCES products(product_id),
    PRIMARY KEY (campaign_id, product_id)
);

CREATE TABLE IF NOT EXISTS inventory_receipts (
    event_id text PRIMARY KEY,
    store_code text NOT NULL REFERENCES retail_stores(store_code),
    product_id text NOT NULL REFERENCES products(product_id),
    quantity integer NOT NULL CHECK (quantity > 0),
    received_at timestamptz NOT NULL,
    receiving_step text NOT NULL,
    source_name text NOT NULL,
    source_document_id text NOT NULL,
    source_uri text,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    voided_at timestamptz
);

CREATE TABLE IF NOT EXISTS store_nearby_places (
    place_id text PRIMARY KEY,
    store_code text NOT NULL REFERENCES retail_stores(store_code),
    place_name text NOT NULL,
    place_category text NOT NULL,
    address text,
    latitude double precision CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision CHECK (longitude BETWEEN -180 AND 180),
    distance_m integer CHECK (distance_m >= 0),
    provider text NOT NULL,
    provider_place_id text,
    source_uri text,
    attribution text NOT NULL,
    captured_at timestamptz NOT NULL,
    valid_until timestamptz NOT NULL,
    UNIQUE (store_code, provider, provider_place_id),
    CHECK ((latitude IS NULL) = (longitude IS NULL)),
    CHECK (valid_until > captured_at)
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
CREATE INDEX IF NOT EXISTS ix_catalog_import_runs_latest ON catalog_import_runs(store_code, imported_at DESC);
CREATE INDEX IF NOT EXISTS ix_promotion_campaigns_active ON promotion_campaigns(store_code, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS ix_inventory_receipts_recent ON inventory_receipts(store_code, received_at DESC) WHERE voided_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_store_nearby_places_valid ON store_nearby_places(store_code, valid_until, distance_m);
