"""Analisis perubahan harga beli dari faktur supplier (harga per satuan kecil)."""
from markupsafe import Markup, escape

MODES = {
    "awal_akhir": "Awal ke akhir periode (pembelian pertama vs terakhir)",
    "periode": "Periode ini vs periode sebelumnya (rata-rata tertimbang)",
    "rentang": "Rentang dalam periode (terendah vs tertinggi)",
}
MODE_LABELS = {
    "awal_akhir": ("Harga awal", "Harga akhir"),
    "periode": ("Rata-rata periode lalu", "Rata-rata periode ini"),
    "rentang": ("Terendah", "Tertinggi"),
}

AGG_SQL = """
WITH cur AS (
  SELECT product_id, invoice_date, line_id, supplier_id, price_small, qty_small, amount
  FROM dwh.v_purchase_price
  WHERE usable AND invoice_date BETWEEN %(d1)s AND %(d2)s
), agg AS (
  SELECT product_id, count(*) AS n, count(DISTINCT supplier_id) AS n_sup,
         sum(amount) / NULLIF(sum(qty_small), 0) AS avg_price,
         min(price_small) AS pmin, max(price_small) AS pmax, sum(qty_small) AS qty_total,
         (array_agg(price_small ORDER BY invoice_date, line_id))[1] AS first_price,
         (array_agg(price_small ORDER BY invoice_date DESC, line_id DESC))[1] AS last_price,
         max(invoice_date) AS last_date
  FROM cur GROUP BY product_id
), prev AS (
  SELECT product_id, sum(amount) / NULLIF(sum(qty_small), 0) AS avg_prev
  FROM dwh.v_purchase_price
  WHERE usable AND invoice_date BETWEEN %(p1)s AND %(p2)s
  GROUP BY product_id
)
SELECT a.*, p.name, p.sku, pr.avg_prev, uo.name AS uom
FROM agg a
JOIN dwh.dim_product p ON p.product_id = a.product_id
LEFT JOIN prev pr ON pr.product_id = a.product_id
LEFT JOIN dwh.dim_uom uo ON uo.uom_id = p.uom_id
"""

SKIPPED_SQL = """
SELECT count(*) AS n, count(DISTINCT product_id) AS produk
FROM dwh.v_purchase_price
WHERE NOT usable AND skip_reason = 'tanpa rasio satuan' AND invoice_date BETWEEN %s AND %s
"""

HISTORY_SQL = """
SELECT v.invoice_date, v.bill_no, v.bill_ref, s.name AS supplier, v.qty, uo.name AS uom,
       v.ratio, v.ratio_source, v.price_purchase, v.price_small, v.amount, v.usable, v.skip_reason
FROM dwh.v_purchase_price v
LEFT JOIN dwh.dim_partner s ON s.partner_id = v.supplier_id
LEFT JOIN dwh.dim_uom uo ON uo.uom_id = v.line_uom_id
WHERE v.product_id = %s AND v.invoice_date BETWEEN %s AND %s
ORDER BY v.invoice_date, v.line_id
"""


def compute(rows, mode="awal_akhir", min_n=2, min_pct=0.0, direction="semua", sort="pct"):
    out = []
    for r in rows:
        n = r["n"]
        if mode == "periode":
            base, now = r.get("avg_prev"), r.get("avg_price")
            if base is None or now is None:
                continue
        elif mode == "rentang":
            if n < min_n:
                continue
            base, now = r["pmin"], r["pmax"]
        else:
            if n < min_n:
                continue
            base, now = r["first_price"], r["last_price"]
        if base is None or now is None:
            continue
        base, now = float(base), float(now)
        if base <= 0:
            continue
        delta = now - base
        pct = delta / base * 100
        if abs(pct) < min_pct:
            continue
        if mode != "rentang":
            if direction == "naik" and delta <= 0:
                continue
            if direction == "turun" and delta >= 0:
                continue
        out.append({**r, "base": base, "now": now, "delta": delta, "pct": pct,
                    "dampak": delta * float(r["qty_total"] or 0)})
    key = {"pct": lambda x: abs(x["pct"]), "nilai": lambda x: abs(x["delta"]),
           "dampak": lambda x: abs(x["dampak"])}.get(sort, lambda x: abs(x["pct"]))
    out.sort(key=key, reverse=True)
    return out


def svg_line(points, width=680, height=190):
    """points: [(date, harga)] -> SVG sederhana (aman: hanya angka dan teks yang di-escape)."""
    pts = [(d, float(v)) for d, v in points if v is not None]
    if len(pts) < 2:
        return Markup("")
    pad_l, pad_r, pad_t, pad_b = 64, 14, 14, 26
    ys = [v for _, v in pts]
    lo, hi = min(ys), max(ys)
    if hi == lo:
        hi, lo = hi * 1.05 + 1, lo * 0.95
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(pts)
    xy = [(pad_l + (i / (n - 1)) * w, pad_t + (1 - (v - lo) / (hi - lo)) * h) for i, (_, v) in enumerate(pts)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" style="fill:var(--pri)"/>' for x, y in xy)

    def fmt(v):
        return f"{v:,.0f}".replace(",", ".") if v >= 100 else f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    svg = (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Grafik harga">'
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + h}" style="stroke:var(--bd)"/>'
        f'<line x1="{pad_l}" y1="{pad_t + h}" x2="{pad_l + w}" y2="{pad_t + h}" style="stroke:var(--bd)"/>'
        f'<text x="{pad_l - 6}" y="{pad_t + 4}" text-anchor="end" font-size="11" style="fill:var(--mut)">{escape(fmt(hi))}</text>'
        f'<text x="{pad_l - 6}" y="{pad_t + h}" text-anchor="end" font-size="11" style="fill:var(--mut)">{escape(fmt(lo))}</text>'
        f'<text x="{pad_l}" y="{height - 6}" font-size="11" style="fill:var(--mut)">{escape(str(pts[0][0]))}</text>'
        f'<text x="{pad_l + w}" y="{height - 6}" text-anchor="end" font-size="11" style="fill:var(--mut)">{escape(str(pts[-1][0]))}</text>'
        f'<polyline points="{poly}" fill="none" stroke-width="2" style="stroke:var(--pri)"/>{dots}</svg>')
    return Markup(svg)
