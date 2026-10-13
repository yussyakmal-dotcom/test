-- Transformasi staging -> dwh. Idempotent.
-- Parameter: %(run_start)s = waktu mulai run (hanya baris yang diekstrak pada run ini diproses)
-- Waktu lokal: Asia/Jakarta

-- ================= DIMENSI =================
INSERT INTO dwh.dim_product_category (category_id, name, complete_name, parent_id)
SELECT odoo_id, payload->>'name', payload->>'complete_name', (payload->>'parent_id')::int
FROM stg.product_category
ON CONFLICT (category_id) DO UPDATE SET
  name=EXCLUDED.name, complete_name=EXCLUDED.complete_name, parent_id=EXCLUDED.parent_id;

INSERT INTO dwh.dim_product (product_id, name, sku, barcode, category_id, uom_id,
                             list_price, standard_price, template_id, product_type, is_storable,
                             sale_ok, purchase_ok, available_in_pos, is_active, updated_at)
SELECT odoo_id, payload->>'display_name', payload->>'default_code', payload->>'barcode',
       (payload->>'categ_id')::int, (payload->>'uom_id')::int,
       (payload->>'list_price')::numeric, (payload->>'standard_price')::numeric,
       (payload->>'product_tmpl_id')::int, payload->>'type', (payload->>'is_storable')::boolean,
       (payload->>'sale_ok')::boolean, (payload->>'purchase_ok')::boolean,
       (payload->>'available_in_pos')::boolean,
       COALESCE((payload->>'active')::boolean, true), now()
FROM stg.product_product
ON CONFLICT (product_id) DO UPDATE SET
  name=EXCLUDED.name, sku=EXCLUDED.sku, barcode=EXCLUDED.barcode,
  category_id=EXCLUDED.category_id, uom_id=EXCLUDED.uom_id,
  list_price=EXCLUDED.list_price, standard_price=EXCLUDED.standard_price,
  template_id=EXCLUDED.template_id, product_type=EXCLUDED.product_type,
  is_storable=EXCLUDED.is_storable, sale_ok=EXCLUDED.sale_ok, purchase_ok=EXCLUDED.purchase_ok,
  available_in_pos=EXCLUDED.available_in_pos,
  is_active=EXCLUDED.is_active, updated_at=now();

INSERT INTO dwh.dim_store (store_id, name, company_id, is_active)
SELECT odoo_id, payload->>'name', (payload->>'company_id')::int,
       COALESCE((payload->>'active')::boolean, true)
FROM stg.pos_config
ON CONFLICT (store_id) DO UPDATE SET
  name=EXCLUDED.name, company_id=EXCLUDED.company_id, is_active=EXCLUDED.is_active;

INSERT INTO dwh.dim_partner (partner_id, name, ref, city, street, vat, is_company,
                             is_customer, is_supplier, is_active)
SELECT odoo_id, payload->>'name', payload->>'ref', payload->>'city', payload->>'street',
       payload->>'vat', (payload->>'is_company')::boolean,
       COALESCE((payload->>'customer_rank')::int, 0) > 0,
       COALESCE((payload->>'supplier_rank')::int, 0) > 0,
       COALESCE((payload->>'active')::boolean, true)
FROM stg.res_partner
ON CONFLICT (partner_id) DO UPDATE SET
  name=EXCLUDED.name, ref=EXCLUDED.ref, city=EXCLUDED.city, street=EXCLUDED.street,
  vat=EXCLUDED.vat, is_company=EXCLUDED.is_company,
  is_customer=EXCLUDED.is_customer, is_supplier=EXCLUDED.is_supplier, is_active=EXCLUDED.is_active;

INSERT INTO dwh.dim_user (user_id, name, login, is_active)
SELECT odoo_id, payload->>'name', payload->>'login', COALESCE((payload->>'active')::boolean, true)
FROM stg.res_users
ON CONFLICT (user_id) DO UPDATE SET
  name=EXCLUDED.name, login=EXCLUDED.login, is_active=EXCLUDED.is_active;

INSERT INTO dwh.dim_payment_method (payment_method_id, name, type, is_active)
SELECT odoo_id, payload->>'name', payload->>'type', COALESCE((payload->>'active')::boolean, true)
FROM stg.pos_payment_method
ON CONFLICT (payment_method_id) DO UPDATE SET
  name=EXCLUDED.name, type=EXCLUDED.type, is_active=EXCLUDED.is_active;

INSERT INTO dwh.dim_location (location_id, complete_name, usage, warehouse_id, is_active)
SELECT odoo_id, payload->>'complete_name', payload->>'usage',
       (payload->>'warehouse_id')::int, COALESCE((payload->>'active')::boolean, true)
FROM stg.stock_location
ON CONFLICT (location_id) DO UPDATE SET
  complete_name=EXCLUDED.complete_name, usage=EXCLUDED.usage,
  warehouse_id=EXCLUDED.warehouse_id, is_active=EXCLUDED.is_active;

INSERT INTO dwh.dim_pos_category (pos_category_id, name, parent_id)
SELECT odoo_id, payload->>'name', (payload->>'parent_id')::int FROM stg.pos_category
ON CONFLICT (pos_category_id) DO UPDATE SET name=EXCLUDED.name, parent_id=EXCLUDED.parent_id;

-- ================= SUPPLIER, BOM, KOMPONEN (tarik penuh; yang dihapus di Odoo ikut dihapus) =================
INSERT INTO dwh.dim_product_supplier (supplierinfo_id, supplier_id, template_id, product_id,
       supplier_product_name, supplier_product_code, min_qty, price, currency_id, lead_days,
       date_start, date_end, updated_at)
SELECT odoo_id, (payload->>'partner_id')::int, (payload->>'product_tmpl_id')::int,
       (payload->>'product_id')::int, payload->>'product_name', payload->>'product_code',
       (payload->>'min_qty')::numeric, (payload->>'price')::numeric, (payload->>'currency_id')::int,
       (payload->>'delay')::int, (payload->>'date_start')::date, (payload->>'date_end')::date, now()
FROM stg.product_supplierinfo
ON CONFLICT (supplierinfo_id) DO UPDATE SET
  supplier_id=EXCLUDED.supplier_id, template_id=EXCLUDED.template_id, product_id=EXCLUDED.product_id,
  supplier_product_name=EXCLUDED.supplier_product_name, supplier_product_code=EXCLUDED.supplier_product_code,
  min_qty=EXCLUDED.min_qty, price=EXCLUDED.price, currency_id=EXCLUDED.currency_id,
  lead_days=EXCLUDED.lead_days, date_start=EXCLUDED.date_start, date_end=EXCLUDED.date_end, updated_at=now();
DELETE FROM dwh.dim_product_supplier d
WHERE NOT EXISTS (SELECT 1 FROM stg.product_supplierinfo s WHERE s.odoo_id = d.supplierinfo_id);

INSERT INTO dwh.dim_bom (bom_id, template_id, product_id, product_qty, uom_id, bom_type, code,
       sequence, company_id, is_active, updated_at)
SELECT odoo_id, (payload->>'product_tmpl_id')::int, (payload->>'product_id')::int,
       (payload->>'product_qty')::numeric, (payload->>'product_uom_id')::int, payload->>'type',
       payload->>'code', (payload->>'sequence')::int, (payload->>'company_id')::int,
       COALESCE((payload->>'active')::boolean, true), now()
FROM stg.mrp_bom
ON CONFLICT (bom_id) DO UPDATE SET
  template_id=EXCLUDED.template_id, product_id=EXCLUDED.product_id, product_qty=EXCLUDED.product_qty,
  uom_id=EXCLUDED.uom_id, bom_type=EXCLUDED.bom_type, code=EXCLUDED.code, sequence=EXCLUDED.sequence,
  company_id=EXCLUDED.company_id, is_active=EXCLUDED.is_active, updated_at=now();
DELETE FROM dwh.dim_bom d WHERE NOT EXISTS (SELECT 1 FROM stg.mrp_bom s WHERE s.odoo_id = d.bom_id);

INSERT INTO dwh.dim_bom_line (line_id, bom_id, component_id, qty, uom_id, updated_at)
SELECT odoo_id, (payload->>'bom_id')::int, (payload->>'product_id')::int,
       (payload->>'product_qty')::numeric, (payload->>'product_uom_id')::int, now()
FROM stg.mrp_bom_line
WHERE (payload->>'bom_id') IS NOT NULL AND (payload->>'product_id') IS NOT NULL
ON CONFLICT (line_id) DO UPDATE SET
  bom_id=EXCLUDED.bom_id, component_id=EXCLUDED.component_id, qty=EXCLUDED.qty,
  uom_id=EXCLUDED.uom_id, updated_at=now();
DELETE FROM dwh.dim_bom_line d WHERE NOT EXISTS (SELECT 1 FROM stg.mrp_bom_line s WHERE s.odoo_id = d.line_id);

-- ================= FAKTA POS =================
INSERT INTO dwh.fact_pos_order (order_id, order_name, order_ts, date_key, store_id, session_id,
       cashier_id, partner_id, state, amount_total, amount_tax, amount_paid, is_refund, updated_at)
SELECT o.odoo_id, o.payload->>'name', t.ts, to_char(t.ts,'YYYYMMDD')::int,
       (o.payload->>'config_id')::int, (o.payload->>'session_id')::int,
       (o.payload->>'user_id')::int, (o.payload->>'partner_id')::int,
       o.payload->>'state',
       (o.payload->>'amount_total')::numeric, (o.payload->>'amount_tax')::numeric,
       (o.payload->>'amount_paid')::numeric,
       (o.payload->>'amount_total')::numeric < 0, now()
FROM stg.pos_order o
CROSS JOIN LATERAL (
  SELECT ((o.payload->>'date_order')::timestamp AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Jakarta' AS ts
) t
WHERE o.extracted_at >= %(run_start)s
ON CONFLICT (order_id) DO UPDATE SET
  order_name=EXCLUDED.order_name, order_ts=EXCLUDED.order_ts, date_key=EXCLUDED.date_key,
  store_id=EXCLUDED.store_id, session_id=EXCLUDED.session_id, cashier_id=EXCLUDED.cashier_id,
  partner_id=EXCLUDED.partner_id, state=EXCLUDED.state, amount_total=EXCLUDED.amount_total,
  amount_tax=EXCLUDED.amount_tax, amount_paid=EXCLUDED.amount_paid,
  is_refund=EXCLUDED.is_refund, updated_at=now();

-- Biaya estimasi dibekukan saat pertama dimuat; saat upsert hanya dikalikan ulang dengan qty baru.
INSERT INTO dwh.fact_pos_order_line (line_id, order_id, order_ts, date_key, store_id, product_id,
       qty, price_unit, discount_pct, net_amount, gross_amount, unit_cost_est, cost_amount_est, updated_at)
SELECT l.odoo_id, o.order_id, o.order_ts, o.date_key, o.store_id,
       (l.payload->>'product_id')::int,
       (l.payload->>'qty')::numeric, (l.payload->>'price_unit')::numeric,
       (l.payload->>'discount')::numeric,
       (l.payload->>'price_subtotal')::numeric, (l.payload->>'price_subtotal_incl')::numeric,
       COALESCE(p.standard_price, 0),
       (l.payload->>'qty')::numeric * COALESCE(p.standard_price, 0), now()
FROM stg.pos_order_line l
JOIN dwh.fact_pos_order o ON o.order_id = (l.payload->>'order_id')::int
LEFT JOIN dwh.dim_product p ON p.product_id = (l.payload->>'product_id')::int
WHERE l.extracted_at >= %(run_start)s
ON CONFLICT (line_id) DO UPDATE SET
  order_ts=EXCLUDED.order_ts, date_key=EXCLUDED.date_key, store_id=EXCLUDED.store_id,
  product_id=EXCLUDED.product_id, qty=EXCLUDED.qty, price_unit=EXCLUDED.price_unit,
  discount_pct=EXCLUDED.discount_pct, net_amount=EXCLUDED.net_amount,
  gross_amount=EXCLUDED.gross_amount,
  cost_amount_est = EXCLUDED.qty * dwh.fact_pos_order_line.unit_cost_est,
  updated_at=now();

INSERT INTO dwh.fact_pos_payment (payment_id, order_id, payment_ts, date_key, store_id,
       payment_method_id, amount, updated_at)
SELECT p.odoo_id, o.order_id,
       COALESCE(((p.payload->>'payment_date')::timestamp AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Jakarta', o.order_ts),
       o.date_key, o.store_id,
       (p.payload->>'payment_method_id')::int, (p.payload->>'amount')::numeric, now()
FROM stg.pos_payment p
JOIN dwh.fact_pos_order o ON o.order_id = (p.payload->>'pos_order_id')::int
WHERE p.extracted_at >= %(run_start)s
ON CONFLICT (payment_id) DO UPDATE SET
  payment_ts=EXCLUDED.payment_ts, date_key=EXCLUDED.date_key, store_id=EXCLUDED.store_id,
  payment_method_id=EXCLUDED.payment_method_id, amount=EXCLUDED.amount, updated_at=now();

INSERT INTO dwh.fact_pos_session (session_id, name, store_id, cashier_id, state,
       start_ts, stop_ts, date_key, updated_at)
SELECT s.odoo_id, s.payload->>'name', (s.payload->>'config_id')::int, (s.payload->>'user_id')::int,
       s.payload->>'state', t.st, t.sp, to_char(t.st,'YYYYMMDD')::int, now()
FROM stg.pos_session s
CROSS JOIN LATERAL (SELECT
  ((s.payload->>'start_at')::timestamp AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Jakarta' AS st,
  ((s.payload->>'stop_at')::timestamp  AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Jakarta' AS sp) t
WHERE s.extracted_at >= %(run_start)s
ON CONFLICT (session_id) DO UPDATE SET
  name=EXCLUDED.name, store_id=EXCLUDED.store_id, cashier_id=EXCLUDED.cashier_id,
  state=EXCLUDED.state, start_ts=EXCLUDED.start_ts, stop_ts=EXCLUDED.stop_ts,
  date_key=EXCLUDED.date_key, updated_at=now();

-- ================= FAKTA SALES ORDER =================
INSERT INTO dwh.fact_sale_order_line (line_id, order_id, order_name, order_ts, date_key, partner_id,
       salesperson_id, warehouse_id, product_id, order_state, qty_ordered, qty_delivered, qty_invoiced,
       price_unit, discount_pct, net_amount, gross_amount, unit_cost_est, cost_amount_est, updated_at)
SELECT l.odoo_id, o.odoo_id, o.payload->>'name', t.ts, to_char(t.ts,'YYYYMMDD')::int,
       (o.payload->>'partner_id')::int, (o.payload->>'user_id')::int, (o.payload->>'warehouse_id')::int,
       (l.payload->>'product_id')::int, o.payload->>'state',
       (l.payload->>'product_uom_qty')::numeric, (l.payload->>'qty_delivered')::numeric,
       (l.payload->>'qty_invoiced')::numeric,
       (l.payload->>'price_unit')::numeric, (l.payload->>'discount')::numeric,
       (l.payload->>'price_subtotal')::numeric, (l.payload->>'price_total')::numeric,
       COALESCE(p.standard_price, 0),
       (l.payload->>'product_uom_qty')::numeric * COALESCE(p.standard_price, 0), now()
FROM stg.sale_order_line l
JOIN stg.sale_order o ON o.odoo_id = (l.payload->>'order_id')::int
CROSS JOIN LATERAL (
  SELECT ((o.payload->>'date_order')::timestamp AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Jakarta' AS ts) t
LEFT JOIN dwh.dim_product p ON p.product_id = (l.payload->>'product_id')::int
WHERE (l.extracted_at >= %(run_start)s OR o.extracted_at >= %(run_start)s)
  AND l.payload->>'display_type' IS NULL
ON CONFLICT (line_id) DO UPDATE SET
  order_ts=EXCLUDED.order_ts, date_key=EXCLUDED.date_key, partner_id=EXCLUDED.partner_id,
  salesperson_id=EXCLUDED.salesperson_id, warehouse_id=EXCLUDED.warehouse_id,
  product_id=EXCLUDED.product_id, order_state=EXCLUDED.order_state,
  qty_ordered=EXCLUDED.qty_ordered, qty_delivered=EXCLUDED.qty_delivered,
  qty_invoiced=EXCLUDED.qty_invoiced, price_unit=EXCLUDED.price_unit,
  discount_pct=EXCLUDED.discount_pct, net_amount=EXCLUDED.net_amount,
  gross_amount=EXCLUDED.gross_amount,
  cost_amount_est = EXCLUDED.qty_ordered * dwh.fact_sale_order_line.unit_cost_est,
  updated_at=now();

-- ================= FAKTA PEMBELIAN =================
INSERT INTO dwh.fact_purchase_line (line_id, order_id, order_name, order_ts, date_key, supplier_id,
       product_id, order_state, receipt_status, qty_ordered, qty_received, qty_invoiced,
       price_unit, net_amount, gross_amount, date_planned, updated_at)
SELECT l.odoo_id, o.odoo_id, o.payload->>'name', t.ts, to_char(t.ts,'YYYYMMDD')::int,
       (o.payload->>'partner_id')::int, (l.payload->>'product_id')::int,
       o.payload->>'state', o.payload->>'receipt_status',
       (l.payload->>'product_qty')::numeric, (l.payload->>'qty_received')::numeric,
       (l.payload->>'qty_invoiced')::numeric, (l.payload->>'price_unit')::numeric,
       (l.payload->>'price_subtotal')::numeric, (l.payload->>'price_total')::numeric,
       ((l.payload->>'date_planned')::timestamp AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Jakarta', now()
FROM stg.purchase_order_line l
JOIN stg.purchase_order o ON o.odoo_id = (l.payload->>'order_id')::int
CROSS JOIN LATERAL (
  SELECT ((COALESCE(o.payload->>'date_approve', o.payload->>'date_order'))::timestamp AT TIME ZONE 'UTC')
         AT TIME ZONE 'Asia/Jakarta' AS ts) t
WHERE (l.extracted_at >= %(run_start)s OR o.extracted_at >= %(run_start)s)
  AND l.payload->>'display_type' IS NULL
ON CONFLICT (line_id) DO UPDATE SET
  order_ts=EXCLUDED.order_ts, date_key=EXCLUDED.date_key, supplier_id=EXCLUDED.supplier_id,
  product_id=EXCLUDED.product_id, order_state=EXCLUDED.order_state,
  receipt_status=EXCLUDED.receipt_status, qty_ordered=EXCLUDED.qty_ordered,
  qty_received=EXCLUDED.qty_received, qty_invoiced=EXCLUDED.qty_invoiced,
  price_unit=EXCLUDED.price_unit, net_amount=EXCLUDED.net_amount,
  gross_amount=EXCLUDED.gross_amount, date_planned=EXCLUDED.date_planned, updated_at=now();

-- ================= SATUAN & FAKTUR PEMBELIAN =================
INSERT INTO dwh.dim_uom (uom_id, name, is_active)
SELECT odoo_id, payload->>'name', COALESCE((payload->>'active')::boolean, true) FROM stg.uom_uom
ON CONFLICT (uom_id) DO UPDATE SET name=EXCLUDED.name, is_active=EXCLUDED.is_active;

INSERT INTO dwh.dim_uom (uom_id, name, is_active, relative_factor, relative_uom_id)
SELECT odoo_id, payload->>'name', COALESCE((payload->>'active')::boolean, true),
       (payload->>'relative_factor')::numeric, (payload->>'relative_uom_id')::int
FROM stg.uom_uom_conv
ON CONFLICT (uom_id) DO UPDATE SET name=EXCLUDED.name, is_active=EXCLUDED.is_active,
  relative_factor=EXCLUDED.relative_factor, relative_uom_id=EXCLUDED.relative_uom_id;

-- Faktur ikut diproses bila baris atau headernya berubah pada run ini (status faktur ada di header).
INSERT INTO dwh.fact_vendor_bill_line (line_id, move_id, bill_no, bill_ref, invoice_date, date_key,
       supplier_id, product_id, line_uom_id, qty, price_unit, discount, amount,
       po_line_id, po_ratio, move_type, move_state, updated_at)
SELECT l.odoo_id, m.odoo_id, m.payload->>'name', m.payload->>'ref', d.dt, to_char(d.dt,'YYYYMMDD')::int,
       (m.payload->>'partner_id')::int, (l.payload->>'product_id')::int, (l.payload->>'product_uom_id')::int,
       (l.payload->>'quantity')::numeric, (l.payload->>'price_unit')::numeric,
       (l.payload->>'discount')::numeric, (l.payload->>'balance')::numeric,
       pol.odoo_id,
       CASE WHEN (pol.payload->>'product_qty')::numeric > 0
             AND (pol.payload->>'product_uom_qty') IS NOT NULL
            THEN (pol.payload->>'product_uom_qty')::numeric / (pol.payload->>'product_qty')::numeric END,
       m.payload->>'move_type', m.payload->>'state', now()
FROM stg.account_move_line l
JOIN stg.account_move m ON m.odoo_id = (l.payload->>'move_id')::int
CROSS JOIN LATERAL (
  SELECT COALESCE((m.payload->>'invoice_date')::date, (m.payload->>'date')::date) AS dt) d
LEFT JOIN stg.purchase_order_line_uom pol ON pol.odoo_id = (l.payload->>'purchase_line_id')::int
WHERE (l.extracted_at >= %(run_start)s OR m.extracted_at >= %(run_start)s)
  AND (l.payload->>'product_id') IS NOT NULL AND d.dt IS NOT NULL
ON CONFLICT (line_id) DO UPDATE SET
  bill_no=EXCLUDED.bill_no, bill_ref=EXCLUDED.bill_ref, invoice_date=EXCLUDED.invoice_date,
  date_key=EXCLUDED.date_key, supplier_id=EXCLUDED.supplier_id, product_id=EXCLUDED.product_id,
  line_uom_id=EXCLUDED.line_uom_id, qty=EXCLUDED.qty, price_unit=EXCLUDED.price_unit,
  discount=EXCLUDED.discount, amount=EXCLUDED.amount, po_line_id=EXCLUDED.po_line_id,
  po_ratio=EXCLUDED.po_ratio, move_type=EXCLUDED.move_type, move_state=EXCLUDED.move_state,
  updated_at=now();

-- ================= PEMETAAN TOKO -> LOKASI STOK =================
-- Lokasi stok toko = lokasi sumber operation type milik pos.config
UPDATE dwh.dim_store s
SET location_id = (pt.payload->>'default_location_src_id')::int
FROM stg.pos_config c
JOIN stg.stock_picking_type pt ON pt.odoo_id = (c.payload->>'picking_type_id')::int
WHERE c.odoo_id = s.store_id
  AND (pt.payload->>'default_location_src_id') IS NOT NULL;

-- ================= FAKTA SCRAP =================
-- Scrap dianggap milik toko bila lokasi asalnya sama dengan, atau di bawah, lokasi stok toko.
INSERT INTO dwh.fact_scrap (scrap_id, scrap_name, scrap_ts, date_key, location_id, store_id,
       product_id, lot_id, qty, state, origin, unit_cost_est, cost_amount_est, updated_at)
SELECT sc.odoo_id, sc.payload->>'name', t.ts, to_char(t.ts,'YYYYMMDD')::int,
       (sc.payload->>'location_id')::int, m.store_id,
       (sc.payload->>'product_id')::int, (sc.payload->>'lot_id')::int,
       (sc.payload->>'scrap_qty')::numeric, sc.payload->>'state', sc.payload->>'origin',
       COALESCE(p.standard_price, 0),
       (sc.payload->>'scrap_qty')::numeric * COALESCE(p.standard_price, 0), now()
FROM stg.stock_scrap sc
CROSS JOIN LATERAL (
  SELECT (COALESCE((sc.payload->>'date_done')::timestamp, sc.write_date) AT TIME ZONE 'UTC')
         AT TIME ZONE 'Asia/Jakarta' AS ts) t
LEFT JOIN dwh.dim_location sl ON sl.location_id = (sc.payload->>'location_id')::int
LEFT JOIN LATERAL (
  SELECT st.store_id
  FROM dwh.dim_store st
  JOIN dwh.dim_location ls ON ls.location_id = st.location_id
  WHERE sl.complete_name IS NOT NULL
    AND (sl.complete_name = ls.complete_name
         OR starts_with(sl.complete_name, ls.complete_name || '/'))
  ORDER BY length(ls.complete_name) DESC LIMIT 1) m ON true
LEFT JOIN dwh.dim_product p ON p.product_id = (sc.payload->>'product_id')::int
WHERE sc.extracted_at >= %(run_start)s
ON CONFLICT (scrap_id) DO UPDATE SET
  scrap_name=EXCLUDED.scrap_name, scrap_ts=EXCLUDED.scrap_ts, date_key=EXCLUDED.date_key,
  location_id=EXCLUDED.location_id, store_id=EXCLUDED.store_id, product_id=EXCLUDED.product_id,
  lot_id=EXCLUDED.lot_id, qty=EXCLUDED.qty, state=EXCLUDED.state, origin=EXCLUDED.origin,
  cost_amount_est = EXCLUDED.qty * dwh.fact_scrap.unit_cost_est, updated_at=now();

-- ================= SNAPSHOT HARGA POKOK HARIAN =================
INSERT INTO dwh.snapshot_product_cost_daily (snapshot_date, product_id, standard_price)
SELECT (now() AT TIME ZONE 'Asia/Jakarta')::date, product_id, standard_price
FROM dwh.dim_product
ON CONFLICT (snapshot_date, product_id) DO UPDATE SET standard_price = EXCLUDED.standard_price;
