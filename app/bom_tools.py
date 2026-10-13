"""Fungsi murni untuk laporan bahan baku (where-used) dan kalkulator harga jual."""
import math


def tokens(q: str) -> list[str]:
    return [t for t in q.replace("%", " ").replace("_", " ").split() if t][:6]


def like_where(toks: list[str], alias: str = "p"):
    """Setiap kata harus ada di nama atau SKU (urutan bebas)."""
    cond = " AND ".join(
        f"({alias}.name ILIKE %s OR coalesce({alias}.sku, '') ILIKE %s)" for _ in toks)
    params: list = []
    for t in toks:
        params += [f"%{t}%", f"%{t}%"]
    return cond, params


def parse_num(v, default: float = 0.0) -> float:
    """Terima 12.5 atau 12,5 (jangan pakai pemisah ribuan)."""
    s = str(v or "").strip().replace(" ", "").replace("Rp", "").replace("rp", "")
    if not s:
        return default
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        x = float(s)
    except ValueError:
        return default
    return x if math.isfinite(x) and abs(x) < 1e12 else default


def calc_price(rows: list[dict], extra: float, margin: float, ppn: float, round_to: float) -> dict:
    """Margin dihitung atas harga jual: harga = HPP / (1 - margin)."""
    bahan = sum((r["qty"] or 0) * (r["price"] or 0) for r in rows)
    hpp = bahan + max(extra, 0)
    margin = min(max(margin, 0.0), 95.0)
    ppn = min(max(ppn, 0.0), 100.0)
    harga = hpp / (1 - margin / 100) if hpp > 0 else 0.0
    dengan_ppn = harga * (1 + ppn / 100)
    final = math.ceil(dengan_ppn / round_to - 1e-9) * round_to if round_to > 0 else dengan_ppn
    sebelum_ppn = final / (1 + ppn / 100) if final else 0.0
    laba = sebelum_ppn - hpp
    return dict(
        bahan=bahan, extra=max(extra, 0), hpp=hpp, margin=margin, ppn=ppn,
        harga_sebelum_ppn=harga, harga_final=final, laba=laba,
        margin_riil=(laba / sebelum_ppn * 100) if sebelum_ppn > 0 else 0.0,
        markup=((harga - hpp) / hpp * 100) if hpp > 0 else 0.0,
    )


# Penelusuran ke atas: bahan -> BOM yang memakainya -> produk jadi -> BOM lain yang memakai produk jadi itu ...
# Semua BOM (aktif maupun arsip, normal maupun phantom) ikut agar tidak ada yang tertinggal.
UP_SQL = """
WITH RECURSIVE up AS (
  SELECT l.line_id, l.bom_id, l.component_id AS via_id, 1 AS level,
         l.qty AS line_qty, b.product_qty AS bom_qty, b.is_active, b.bom_type, b.code,
         fp.product_id AS finished_id, ARRAY[fp.product_id] AS path
  FROM dwh.dim_bom_line l
  JOIN dwh.dim_bom b ON b.bom_id = l.bom_id
  JOIN dwh.dim_product fp ON (b.product_id IS NOT NULL AND fp.product_id = b.product_id)
                          OR (b.product_id IS NULL AND fp.template_id = b.template_id)
  WHERE l.component_id = %s
  UNION ALL
  SELECT l.line_id, l.bom_id, l.component_id, up.level + 1,
         l.qty, b.product_qty, b.is_active, b.bom_type, b.code,
         fp.product_id, up.path || fp.product_id
  FROM up
  JOIN dwh.dim_bom_line l ON l.component_id = up.finished_id
  JOIN dwh.dim_bom b ON b.bom_id = l.bom_id
  JOIN dwh.dim_product fp ON (b.product_id IS NOT NULL AND fp.product_id = b.product_id)
                          OR (b.product_id IS NULL AND fp.template_id = b.template_id)
  WHERE up.level < 6 AND NOT (fp.product_id = ANY (up.path))
)
SELECT up.level, up.bom_id, up.code, up.bom_type, up.is_active,
       fp.name AS finished_name, fp.sku AS finished_sku,
       vp.name AS via_name, up.line_qty / NULLIF(up.bom_qty, 0) AS qty_per_unit
FROM up
JOIN dwh.dim_product fp ON fp.product_id = up.finished_id
JOIN dwh.dim_product vp ON vp.product_id = up.via_id
ORDER BY up.level, fp.name, up.bom_id
"""

# Dampak penggantian pada BOM utama (bahan baku akhir saja)
IMPACT_SQL = """
WITH tot AS (
  SELECT root_product_id,
         sum(component_cost_per_unit) FILTER (WHERE is_leaf) AS hpp
  FROM dwh.v_bom_exploded GROUP BY 1)
SELECT e.root_product_id, e.root_name, e.root_sku,
       sum(e.qty_per_unit) AS qty_per_unit, t.hpp
FROM dwh.v_bom_exploded e
JOIN tot t ON t.root_product_id = e.root_product_id
WHERE e.component_id = %s AND e.is_leaf
GROUP BY 1, 2, 3, 5
ORDER BY e.root_name
"""
