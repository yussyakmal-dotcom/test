-- Odoo BI - Tahap 1 | PostgreSQL 14+
-- Jalankan sekali: psql -d odoo_bi -f schema.sql  (aman dijalankan ulang)

CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS dwh;
CREATE SCHEMA IF NOT EXISTS etl;

-- ============================================================
-- ETL control
-- ============================================================
CREATE TABLE IF NOT EXISTS etl.state (
    model            text PRIMARY KEY,
    last_success_at  timestamp NOT NULL           -- UTC
);

CREATE TABLE IF NOT EXISTS etl.run_log (
    run_id       bigserial PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL DEFAULT 'running', -- running | success | partial | failed
    details      jsonb
);

-- ============================================================
-- STAGING: satu tabel per model, bentuk seragam
-- ============================================================
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'pos_order','pos_order_line','pos_payment','pos_session',
    'sale_order','sale_order_line','purchase_order','purchase_order_line',
    'product_product','product_category','pos_category','pos_config',
    'res_partner','res_users','pos_payment_method','stock_location',
    'stock_picking_type','stock_scrap',
    'product_supplierinfo','mrp_bom','mrp_bom_line',
    'uom_uom','uom_uom_conv','account_move','account_move_line','purchase_order_line_uom'
  ] LOOP
    EXECUTE format($f$
      CREATE TABLE IF NOT EXISTS stg.%I (
        odoo_id      bigint PRIMARY KEY,
        write_date   timestamp,
        payload      jsonb NOT NULL,
        extracted_at timestamptz NOT NULL DEFAULT now()
      )$f$, t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON stg.%I (extracted_at)', t || '_ext_idx', t);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON stg.%I (write_date)',   t || '_wd_idx',  t);
  END LOOP;
END $$;

-- ============================================================
-- DWH: DIMENSI
-- ============================================================
CREATE TABLE IF NOT EXISTS dwh.dim_date (
    date_key    int PRIMARY KEY,                  -- YYYYMMDD
    full_date   date NOT NULL UNIQUE,
    year        int, quarter int, month int, month_name text,
    week_iso    int, day int, dow_iso int, day_name text,
    is_weekend  boolean
);
INSERT INTO dwh.dim_date
SELECT to_char(d,'YYYYMMDD')::int, d::date,
       extract(year from d), extract(quarter from d), extract(month from d), to_char(d,'FMMonth'),
       extract(week from d), extract(day from d), extract(isodow from d), to_char(d,'FMDay'),
       extract(isodow from d) >= 6
FROM generate_series('2020-01-01'::date, '2035-12-31'::date, interval '1 day') d
ON CONFLICT (date_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS dwh.dim_product_category (
    category_id   int PRIMARY KEY,
    name          text, complete_name text, parent_id int
);
CREATE TABLE IF NOT EXISTS dwh.dim_product (
    product_id     int PRIMARY KEY,
    name           text, sku text, barcode text,
    category_id    int, uom_id int,
    list_price     numeric(18,4), standard_price numeric(18,4),
    is_active      boolean,
    updated_at     timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dwh.dim_store (
    store_id    int PRIMARY KEY,                  -- pos.config.id
    name        text, company_id int, is_active boolean
);
ALTER TABLE dwh.dim_store ADD COLUMN IF NOT EXISTS location_id int;  -- lokasi stok toko (dari operation type POS)

CREATE TABLE IF NOT EXISTS dwh.dim_partner (
    partner_id    int PRIMARY KEY,
    name text, ref text, city text,
    is_customer boolean, is_supplier boolean, is_active boolean
);
CREATE TABLE IF NOT EXISTS dwh.dim_user (
    user_id int PRIMARY KEY, name text, login text, is_active boolean
);
CREATE TABLE IF NOT EXISTS dwh.dim_payment_method (
    payment_method_id int PRIMARY KEY, name text, type text, is_active boolean
);
CREATE TABLE IF NOT EXISTS dwh.dim_location (
    location_id int PRIMARY KEY, complete_name text, usage text,
    warehouse_id int, is_active boolean
);

-- ============================================================
-- DWH: FAKTA
-- Waktu lokal = Asia/Jakarta (kolom *_ts, date_key)
-- ============================================================
CREATE TABLE IF NOT EXISTS dwh.fact_pos_order (
    order_id      int PRIMARY KEY,
    order_name    text,
    order_ts      timestamp,
    date_key      int,
    store_id      int, session_id int, cashier_id int, partner_id int,
    state         text,
    amount_total  numeric(18,4), amount_tax numeric(18,4),
    amount_paid   numeric(18,4),
    is_refund     boolean,
    updated_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fpo_date_store ON dwh.fact_pos_order (date_key, store_id);

CREATE TABLE IF NOT EXISTS dwh.fact_pos_order_line (
    line_id         int PRIMARY KEY,
    order_id        int NOT NULL,
    order_ts        timestamp,
    date_key        int,
    store_id        int,
    product_id      int,
    qty             numeric(18,4),
    price_unit      numeric(18,4),
    discount_pct    numeric(9,4),
    net_amount      numeric(18,4),                -- tanpa pajak
    gross_amount    numeric(18,4),                -- termasuk pajak
    unit_cost_est   numeric(18,4),                -- standard_price saat pertama dimuat
    cost_amount_est numeric(18,4),
    updated_at      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fpol_date_store ON dwh.fact_pos_order_line (date_key, store_id);
CREATE INDEX IF NOT EXISTS ix_fpol_product    ON dwh.fact_pos_order_line (product_id);

CREATE TABLE IF NOT EXISTS dwh.fact_pos_payment (
    payment_id        int PRIMARY KEY,
    order_id          int,
    payment_ts        timestamp,
    date_key          int,
    store_id          int,
    payment_method_id int,
    amount            numeric(18,4),
    updated_at        timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fpp_date_store ON dwh.fact_pos_payment (date_key, store_id);

CREATE TABLE IF NOT EXISTS dwh.fact_pos_session (
    session_id int PRIMARY KEY,
    name text, store_id int, cashier_id int, state text,
    start_ts timestamp, stop_ts timestamp, date_key int,
    updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dwh.fact_sale_order_line (
    line_id         int PRIMARY KEY,
    order_id        int NOT NULL,
    order_name      text,
    order_ts        timestamp,
    date_key        int,
    partner_id      int, salesperson_id int, warehouse_id int,
    product_id      int,
    order_state     text,
    qty_ordered     numeric(18,4), qty_delivered numeric(18,4), qty_invoiced numeric(18,4),
    price_unit      numeric(18,4), discount_pct numeric(9,4),
    net_amount      numeric(18,4), gross_amount numeric(18,4),
    unit_cost_est   numeric(18,4), cost_amount_est numeric(18,4),
    updated_at      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fsol_date ON dwh.fact_sale_order_line (date_key);

CREATE TABLE IF NOT EXISTS dwh.fact_purchase_line (
    line_id         int PRIMARY KEY,
    order_id        int NOT NULL,
    order_name      text,
    order_ts        timestamp,
    date_key        int,
    supplier_id     int, product_id int,
    order_state     text, receipt_status text,
    qty_ordered     numeric(18,4), qty_received numeric(18,4), qty_invoiced numeric(18,4),
    price_unit      numeric(18,4), net_amount numeric(18,4), gross_amount numeric(18,4),
    date_planned    timestamp,
    updated_at      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fpl_date ON dwh.fact_purchase_line (date_key);

CREATE TABLE IF NOT EXISTS dwh.fact_scrap (
    scrap_id        int PRIMARY KEY,
    scrap_name      text,
    scrap_ts        timestamp,
    date_key        int,
    location_id     int,
    store_id        int,                          -- NULL = lokasi bukan toko
    product_id      int,
    lot_id          int,
    qty             numeric(18,4),
    state           text,
    origin          text,
    unit_cost_est   numeric(18,4),
    cost_amount_est numeric(18,4),
    updated_at      timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fscrap_date_store ON dwh.fact_scrap (date_key, store_id);

-- ============================================================
-- SNAPSHOT HARIAN (tidak bisa direkonstruksi dari Odoo)
-- ============================================================
CREATE TABLE IF NOT EXISTS dwh.snapshot_stock_daily (
    snapshot_date      date NOT NULL,
    product_id         int  NOT NULL,
    location_id        int  NOT NULL,
    lot_id             int  NOT NULL DEFAULT 0,
    quantity           numeric(18,4),
    reserved_quantity  numeric(18,4),
    PRIMARY KEY (snapshot_date, product_id, location_id, lot_id)
);
CREATE TABLE IF NOT EXISTS dwh.snapshot_product_cost_daily (
    snapshot_date  date NOT NULL,
    product_id     int  NOT NULL,
    standard_price numeric(18,4),
    PRIMARY KEY (snapshot_date, product_id)
);

-- ============================================================
-- VIEW: penjualan gabungan POS + Sales Order
-- ============================================================
CREATE OR REPLACE VIEW dwh.v_sales_unified AS
SELECT 'POS'::text AS source, l.line_id, l.order_id, l.date_key, l.store_id,
       o.partner_id, l.product_id, l.qty,
       l.net_amount, l.gross_amount, l.cost_amount_est
FROM dwh.fact_pos_order_line l
JOIN dwh.fact_pos_order o ON o.order_id = l.order_id
WHERE o.state IN ('paid','done','invoiced')
UNION ALL
SELECT 'SO', line_id, order_id, date_key, NULL,
       partner_id, product_id, qty_ordered,
       net_amount, gross_amount, cost_amount_est
FROM dwh.fact_sale_order_line
WHERE order_state IN ('sale','done');


-- ============================================================
-- MASTER DATA TAMBAHAN: produk lengkap, supplier, BOM, komponen
-- ============================================================
ALTER TABLE dwh.dim_product ADD COLUMN IF NOT EXISTS template_id int;
ALTER TABLE dwh.dim_product ADD COLUMN IF NOT EXISTS product_type text;
ALTER TABLE dwh.dim_product ADD COLUMN IF NOT EXISTS is_storable boolean;
ALTER TABLE dwh.dim_product ADD COLUMN IF NOT EXISTS sale_ok boolean;
ALTER TABLE dwh.dim_product ADD COLUMN IF NOT EXISTS purchase_ok boolean;
ALTER TABLE dwh.dim_product ADD COLUMN IF NOT EXISTS available_in_pos boolean;
ALTER TABLE dwh.dim_partner ADD COLUMN IF NOT EXISTS street text;
ALTER TABLE dwh.dim_partner ADD COLUMN IF NOT EXISTS vat text;
ALTER TABLE dwh.dim_partner ADD COLUMN IF NOT EXISTS is_company boolean;

CREATE TABLE IF NOT EXISTS dwh.dim_pos_category (
    pos_category_id int PRIMARY KEY, name text, parent_id int
);

CREATE TABLE IF NOT EXISTS dwh.dim_product_supplier (      -- daftar harga beli per supplier
    supplierinfo_id       int PRIMARY KEY,
    supplier_id           int,
    template_id           int,
    product_id            int,                              -- NULL = berlaku untuk semua varian template
    supplier_product_name text, supplier_product_code text,
    min_qty               numeric(18,4), price numeric(18,4),
    currency_id           int, lead_days int,
    date_start            date, date_end date,
    updated_at            timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dps_supplier ON dwh.dim_product_supplier (supplier_id);
CREATE INDEX IF NOT EXISTS ix_dps_tmpl     ON dwh.dim_product_supplier (template_id);

CREATE TABLE IF NOT EXISTS dwh.dim_bom (
    bom_id       int PRIMARY KEY,
    template_id  int,
    product_id   int,                                       -- NULL = semua varian template
    product_qty  numeric(18,4),                             -- jumlah hasil per resep
    uom_id       int,
    bom_type     text,                                      -- normal | phantom
    code         text, sequence int, company_id int,
    is_active    boolean,
    updated_at   timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dwh.dim_bom_line (
    line_id      int PRIMARY KEY,
    bom_id       int NOT NULL,
    component_id int NOT NULL,
    qty          numeric(18,4),
    uom_id       int,
    updated_at   timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dbl_bom ON dwh.dim_bom_line (bom_id);
CREATE INDEX IF NOT EXISTS ix_dbl_comp ON dwh.dim_bom_line (component_id);

-- BOM utama tiap produk: BOM khusus varian diutamakan, lalu sequence, lalu id terkecil
CREATE OR REPLACE VIEW dwh.v_product_bom AS
SELECT DISTINCT ON (p.product_id) p.product_id, b.bom_id
FROM dwh.dim_product p
JOIN dwh.dim_bom b
  ON b.is_active AND b.bom_type = 'normal'
 AND (b.product_id = p.product_id OR (b.product_id IS NULL AND b.template_id = p.template_id))
ORDER BY p.product_id, (b.product_id IS NULL), b.sequence, b.bom_id;

-- BOM diurai bertingkat (komponen yang punya BOM sendiri ikut diurai, maks 6 level)
-- qty_per_unit = kebutuhan per 1 unit produk jadi. Satuan (UoM) belum dikonversi.
CREATE OR REPLACE VIEW dwh.v_bom_exploded AS
WITH RECURSIVE x AS (
  SELECT vb.product_id AS root_product_id, vb.bom_id AS root_bom_id, 1 AS level,
         l.component_id,
         l.qty / NULLIF(b.product_qty, 0) AS qty_per_unit,
         ARRAY[vb.product_id, l.component_id] AS path
  FROM dwh.v_product_bom vb
  JOIN dwh.dim_bom b ON b.bom_id = vb.bom_id
  JOIN dwh.dim_bom_line l ON l.bom_id = b.bom_id
  UNION ALL
  SELECT x.root_product_id, x.root_bom_id, x.level + 1, l.component_id,
         x.qty_per_unit * l.qty / NULLIF(b2.product_qty, 0),
         x.path || l.component_id
  FROM x
  JOIN dwh.v_product_bom vb ON vb.product_id = x.component_id
  JOIN dwh.dim_bom b2 ON b2.bom_id = vb.bom_id
  JOIN dwh.dim_bom_line l ON l.bom_id = b2.bom_id
  WHERE x.level < 6 AND NOT (l.component_id = ANY (x.path))
)
SELECT x.root_product_id, rp.name AS root_name, rp.sku AS root_sku,
       x.root_bom_id, x.level, x.component_id, cp.name AS component_name, cp.sku AS component_sku,
       x.qty_per_unit, cp.standard_price AS component_unit_cost,
       x.qty_per_unit * cp.standard_price AS component_cost_per_unit,
       NOT EXISTS (SELECT 1 FROM dwh.v_product_bom v WHERE v.product_id = x.component_id) AS is_leaf
FROM x
JOIN dwh.dim_product rp ON rp.product_id = x.root_product_id
LEFT JOIN dwh.dim_product cp ON cp.product_id = x.component_id;

-- Harga beli per supplier dengan nama
CREATE OR REPLACE VIEW dwh.v_supplier_price AS
SELECT ps.supplierinfo_id, ps.supplier_id, pa.name AS supplier_name,
       coalesce(p.product_id, vp.product_id) AS product_id,
       coalesce(p.name, vp.name) AS product_name,
       ps.supplier_product_code, ps.supplier_product_name,
       ps.min_qty, ps.price, ps.lead_days, ps.date_start, ps.date_end
FROM dwh.dim_product_supplier ps
LEFT JOIN dwh.dim_partner pa ON pa.partner_id = ps.supplier_id
LEFT JOIN dwh.dim_product p ON p.product_id = ps.product_id
LEFT JOIN dwh.dim_product vp ON ps.product_id IS NULL AND vp.template_id = ps.template_id;


-- ============================================================
-- HARGA BELI DARI FAKTUR SUPPLIER (vendor bill)
-- ============================================================
CREATE TABLE IF NOT EXISTS dwh.dim_uom (
    uom_id int PRIMARY KEY, name text, is_active boolean
);

CREATE TABLE IF NOT EXISTS dwh.fact_vendor_bill_line (
    line_id       int PRIMARY KEY,
    move_id       int NOT NULL,
    bill_no       text,
    bill_ref      text,                               -- nomor faktur dari supplier
    invoice_date  date,
    date_key      int,
    supplier_id   int,
    product_id    int,
    line_uom_id   int,                                -- satuan beli pada faktur
    qty           numeric(18,4),                      -- dalam satuan beli
    price_unit    numeric(18,4),
    discount      numeric(9,4),
    amount        numeric(18,4),                      -- balance: mata uang perusahaan, tanpa pajak, setelah diskon
    po_line_id    int,
    po_ratio      numeric(18,6),                      -- 1 satuan beli = po_ratio satuan stok (dari baris PO)
    move_type     text,
    move_state    text,
    updated_at    timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fvbl_prod_date ON dwh.fact_vendor_bill_line (product_id, invoice_date);

ALTER TABLE dwh.dim_uom ADD COLUMN IF NOT EXISTS relative_factor numeric(24,8);   -- kolom "Contains"
ALTER TABLE dwh.dim_uom ADD COLUMN IF NOT EXISTS relative_uom_id int;                  -- kolom "Reference Unit"

-- Rantai satuan sampai satuan akar: cum = jumlah satuan akar dalam 1 satuan ini.
-- Contoh: Galon (19.000 Gram) -> root Gram, cum 19000.
CREATE OR REPLACE VIEW dwh.v_uom_root AS
WITH RECURSIVE r AS (
  SELECT uom_id, uom_id AS cur, 1::numeric AS cum, 0 AS depth FROM dwh.dim_uom
  UNION ALL
  SELECT r.uom_id, c.relative_uom_id, r.cum * c.relative_factor, r.depth + 1
  FROM r
  JOIN dwh.dim_uom c ON c.uom_id = r.cur
  WHERE c.relative_uom_id IS NOT NULL AND c.relative_uom_id <> c.uom_id
    AND c.relative_factor IS NOT NULL AND c.relative_factor > 0 AND r.depth < 6
)
SELECT r.uom_id, r.cur AS root_uom_id, r.cum
FROM r
WHERE NOT EXISTS (SELECT 1 FROM dwh.dim_uom c
                  WHERE c.uom_id = r.cur AND c.relative_uom_id IS NOT NULL AND c.relative_uom_id <> c.uom_id);

-- Harga per satuan kecil (satuan stok produk). Rasio satuan beli -> satuan stok:
--   satuan faktur = satuan stok produk -> 1; selain itu dari tabel konversi (rantai satuan, sama-sama ke satuan akar).
--   Tidak ada konversi -> baris tidak dihitung (usable = false). Rasio dari PO tidak dipakai.
DROP VIEW IF EXISTS dwh.v_purchase_price;
CREATE VIEW dwh.v_purchase_price AS
WITH base AS (
  SELECT f.line_id, f.move_id, f.bill_no, f.bill_ref, f.invoice_date, f.date_key, f.supplier_id,
         f.product_id, f.line_uom_id, f.qty, f.price_unit, f.discount, f.amount, f.po_line_id,
         p.uom_id AS stock_uom_id,
         CASE WHEN f.line_uom_id IS NOT NULL AND f.line_uom_id = p.uom_id THEN 1::numeric
              WHEN ra.cum IS NOT NULL AND rb.cum IS NOT NULL AND rb.cum <> 0 THEN ra.cum / rb.cum
         END AS ratio,
         CASE WHEN f.line_uom_id IS NOT NULL AND f.line_uom_id = p.uom_id THEN 'satuan sama'
              WHEN ra.cum IS NOT NULL AND rb.cum IS NOT NULL AND rb.cum <> 0 THEN 'tabel konversi'
         END AS ratio_source
  FROM dwh.fact_vendor_bill_line f
  JOIN dwh.dim_product p ON p.product_id = f.product_id
  LEFT JOIN dwh.v_uom_root ra ON ra.uom_id = f.line_uom_id
  LEFT JOIN dwh.v_uom_root rb ON rb.uom_id = p.uom_id AND rb.root_uom_id = ra.root_uom_id
  WHERE f.move_type = 'in_invoice' AND f.move_state = 'posted' AND f.invoice_date IS NOT NULL
)
SELECT b.*,
       b.qty * b.ratio AS qty_small,
       CASE WHEN b.ratio IS NULL THEN 'tanpa rasio satuan'
            WHEN b.qty <= 0 OR b.amount <= 0 THEN 'qty atau nilai nol' END AS skip_reason,
       (b.ratio IS NOT NULL AND b.qty > 0 AND b.amount > 0) AS usable,
       CASE WHEN b.qty > 0 THEN b.amount / b.qty END AS price_purchase,
       CASE WHEN b.ratio > 0 AND b.qty > 0 THEN b.amount / (b.qty * b.ratio) END AS price_small
FROM base b;


-- Pemeriksa kebijakan "BOM selalu satuan kecil": baris yang satuannya beda dari satuan stok produk.
CREATE OR REPLACE VIEW dwh.v_bom_uom_check AS
SELECT l.bom_id, 'komponen'::text AS kind, l.component_id AS product_id, l.line_id AS ref_id,
       l.uom_id, p.uom_id AS stock_uom_id,
       (l.uom_id IS NULL OR p.uom_id IS NULL OR l.uom_id = p.uom_id) AS uom_ok
FROM dwh.dim_bom_line l
JOIN dwh.dim_product p ON p.product_id = l.component_id
UNION ALL
SELECT b.bom_id, 'hasil', fp.product_id, b.bom_id, b.uom_id, fp.uom_id,
       (b.uom_id IS NULL OR fp.uom_id IS NULL OR b.uom_id = fp.uom_id)
FROM dwh.dim_bom b
JOIN dwh.dim_product fp ON (b.product_id IS NOT NULL AND fp.product_id = b.product_id)
                        OR (b.product_id IS NULL AND fp.template_id = b.template_id);
