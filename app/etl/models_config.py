"""Daftar model Odoo yang ditarik pada Tahap 1.

mode:
  incremental -> hanya write_date >= watermark - lookback
  full        -> tarik semua tiap malam (data master, kecil), termasuk record diarsipkan
Jika ada field yang tidak ada di instance Anda, API akan menolak; hapus field itu dari daftar.
"""

INCREMENTAL, FULL = "incremental", "full"

MODELS = [
    # ---- master (ditarik dulu supaya dimensi lengkap) ----
    dict(model="product.category", table="product_category", mode=FULL,
         fields=["name", "complete_name", "parent_id", "write_date"]),
    dict(model="pos.category", table="pos_category", mode=FULL,
         fields=["name", "parent_id", "write_date"]),
    dict(model="product.product", table="product_product", mode=FULL,
         fields=["display_name", "default_code", "barcode", "categ_id", "uom_id",
                 "list_price", "standard_price", "product_tmpl_id", "type", "is_storable",
                 "sale_ok", "purchase_ok", "available_in_pos", "active", "write_date"]),
    dict(model="pos.config", table="pos_config", mode=FULL,
         fields=["name", "company_id", "picking_type_id", "active", "write_date"]),
    dict(model="stock.picking.type", table="stock_picking_type", mode=FULL,
         fields=["name", "code", "warehouse_id", "default_location_src_id",
                 "default_location_dest_id", "active", "write_date"]),
    dict(model="res.partner", table="res_partner", mode=FULL,
         fields=["name", "ref", "city", "street", "vat", "is_company", "customer_rank",
                 "supplier_rank", "active", "write_date"]),
    dict(model="product.supplierinfo", table="product_supplierinfo", mode=FULL,
         fields=["partner_id", "product_tmpl_id", "product_id", "product_name", "product_code",
                 "min_qty", "price", "currency_id", "delay", "date_start", "date_end",
                 "write_date"]),
    dict(model="mrp.bom", table="mrp_bom", mode=FULL,
         fields=["product_tmpl_id", "product_id", "product_qty", "product_uom_id", "type",
                 "code", "sequence", "company_id", "active", "write_date"]),
    dict(model="mrp.bom.line", table="mrp_bom_line", mode=FULL,
         fields=["bom_id", "product_id", "product_qty", "product_uom_id", "write_date"]),
    dict(model="res.users", table="res_users", mode=FULL,
         fields=["name", "login", "active", "write_date"]),
    dict(model="pos.payment.method", table="pos_payment_method", mode=FULL,
         fields=["name", "type", "active", "write_date"]),
    dict(model="stock.location", table="stock_location", mode=FULL,
         fields=["complete_name", "usage", "warehouse_id", "active", "write_date"]),

    # ---- transaksi POS ----
    dict(model="pos.session", table="pos_session", mode=INCREMENTAL,
         fields=["name", "config_id", "user_id", "state", "start_at", "stop_at", "write_date"]),
    dict(model="pos.order", table="pos_order", mode=INCREMENTAL,
         fields=["name", "pos_reference", "date_order", "session_id", "config_id", "user_id",
                 "partner_id", "company_id", "amount_total", "amount_tax", "amount_paid",
                 "amount_return", "state", "write_date"]),
    dict(model="pos.order.line", table="pos_order_line", mode=INCREMENTAL,
         fields=["order_id", "product_id", "full_product_name", "qty", "price_unit", "discount",
                 "price_subtotal", "price_subtotal_incl", "write_date"]),
    dict(model="pos.payment", table="pos_payment", mode=INCREMENTAL,
         fields=["pos_order_id", "payment_method_id", "amount", "payment_date",
                 "session_id", "write_date"]),

    dict(model="uom.uom", table="uom_uom", mode=FULL, fields=["name", "active", "write_date"]),
    # konversi kemasan per SKU: 1 satuan ini memuat relative_factor x relative_uom_id (kolom "Contains" dan "Reference Unit")
    dict(model="uom.uom", table="uom_uom_conv", mode=FULL,
         fields=["name", "relative_factor", "relative_uom_id", "active", "write_date"]),

    # ---- faktur pembelian (vendor bill): harga acuan analisis harga beli ----
    dict(model="account.move", table="account_move", mode=INCREMENTAL,
         domain=[["move_type", "in", ["in_invoice", "in_refund"]]],
         fields=["name", "ref", "invoice_date", "date", "partner_id", "move_type", "state",
                 "currency_id", "amount_total", "write_date"]),
    dict(model="account.move.line", table="account_move_line", mode=INCREMENTAL,
         domain=[["move_id.move_type", "in", ["in_invoice", "in_refund"]],
                 ["display_type", "=", "product"]],
         fields=["move_id", "product_id", "product_uom_id", "quantity", "price_unit", "discount",
                 "balance", "price_subtotal", "purchase_line_id", "write_date"]),
    # rasio konversi satuan beli -> satuan stok diambil dari baris PO (dipisah agar kegagalan field tidak
    # mengganggu data pembelian utama)
    dict(model="purchase.order.line", table="purchase_order_line_uom", mode=INCREMENTAL,
         fields=["product_qty", "product_uom_qty", "write_date"]),

    # ---- scrap ----
    dict(model="stock.scrap", table="stock_scrap", mode=INCREMENTAL,
         fields=["name", "product_id", "scrap_qty", "product_uom_id", "location_id",
                 "scrap_location_id", "lot_id", "state", "date_done", "origin", "company_id",
                 "write_date"]),

    # ---- sales order ----
    dict(model="sale.order", table="sale_order", mode=INCREMENTAL,
         fields=["name", "date_order", "partner_id", "user_id", "warehouse_id", "company_id",
                 "state", "amount_untaxed", "amount_tax", "amount_total", "invoice_status",
                 "write_date"]),
    dict(model="sale.order.line", table="sale_order_line", mode=INCREMENTAL,
         fields=["order_id", "product_id", "name", "product_uom_qty", "qty_delivered",
                 "qty_invoiced", "price_unit", "discount", "price_subtotal", "price_total",
                 "display_type", "write_date"]),

    # ---- pembelian (gudang pusat) ----
    dict(model="purchase.order", table="purchase_order", mode=INCREMENTAL,
         fields=["name", "date_order", "date_approve", "partner_id", "user_id", "company_id",
                 "state", "receipt_status", "amount_untaxed", "amount_tax", "amount_total",
                 "write_date"]),
    dict(model="purchase.order.line", table="purchase_order_line", mode=INCREMENTAL,
         fields=["order_id", "product_id", "name", "product_qty", "qty_received", "qty_invoiced",
                 "price_unit", "price_subtotal", "price_total", "date_planned",
                 "display_type", "write_date"]),
]

# Snapshot stok: diambil penuh tiap malam, lokasi internal saja
STOCK_QUANT = dict(
    model="stock.quant",
    domain=[["location_id.usage", "=", "internal"]],
    fields=["product_id", "location_id", "lot_id", "quantity", "reserved_quantity"],
)
