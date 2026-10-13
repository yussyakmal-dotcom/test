-- Modul Request Order (RO) Toko. Aman dijalankan berulang (CREATE IF NOT EXISTS, seed ON CONFLICT DO NOTHING).
-- Nama kolom di PostgreSQL otomatis huruf kecil (KODE_DAPUR = kode_dapur).
CREATE SCHEMA IF NOT EXISTS app;

-- ===================== MASTER =====================
CREATE TABLE IF NOT EXISTS app.master_dapur (
    kode_dapur  VARCHAR PRIMARY KEY,
    dapur       VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS app.master_toko (
    kode_toko   INT PRIMARY KEY,
    toko        VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS app.master_jadwal (
    kode_jadwal INT PRIMARY KEY,
    jadwal      VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS app.master_template_ro (
    id          SERIAL PRIMARY KEY,
    nama_produk VARCHAR NOT NULL,
    kode_gink   VARCHAR,                                   -- opsional
    kode_odoo   VARCHAR NOT NULL,
    kode_dapur  VARCHAR NOT NULL REFERENCES app.master_dapur (kode_dapur) ON UPDATE CASCADE,
    CONSTRAINT uq_template_dapur_kode UNIQUE (kode_dapur, kode_odoo)   -- satu SKU satu kali per dapur
);
CREATE INDEX IF NOT EXISTS ix_template_dapur ON app.master_template_ro (kode_dapur);

-- ===================== TRANSAKSI =====================
CREATE TABLE IF NOT EXISTS app.ro_header (
    id          SERIAL PRIMARY KEY,
    kode_toko   INT     NOT NULL REFERENCES app.master_toko (kode_toko),
    tgl_pesan   DATE    NOT NULL DEFAULT CURRENT_DATE,
    tgl_kirim   DATE    NOT NULL,
    kode_dapur  VARCHAR NOT NULL REFERENCES app.master_dapur (kode_dapur),
    kode_jadwal INT     NOT NULL REFERENCES app.master_jadwal (kode_jadwal),
    status      INT     NOT NULL DEFAULT 1,                -- 1 = DRAFT, 2 = KIRIM DATA
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    -- tambahan di luar spesifikasi (boleh NULL): jejak pengguna
    created_by  VARCHAR,
    updated_at  TIMESTAMP,
    updated_by  VARCHAR,
    CONSTRAINT ck_ro_status    CHECK (status IN (1, 2)),
    CONSTRAINT ck_ro_tgl_kirim CHECK (tgl_kirim >= tgl_pesan + 1),
    -- Satu RO per toko + tgl kirim + dapur + jadwal, agar tidak terjadi RO ganda yang melipatgandakan produksi.
    -- Hapus constraint ini bila memang boleh lebih dari satu RO untuk kombinasi yang sama.
    CONSTRAINT uq_ro_kombinasi UNIQUE (kode_toko, tgl_kirim, kode_dapur, kode_jadwal)
);
CREATE INDEX IF NOT EXISTS ix_ro_header_kirim ON app.ro_header (tgl_kirim, kode_dapur);

CREATE TABLE IF NOT EXISTS app.ro_detail (
    id          SERIAL PRIMARY KEY,
    ro_id       INT     NOT NULL REFERENCES app.ro_header (id) ON DELETE CASCADE,
    kode_odoo   VARCHAR NOT NULL,
    nama_produk VARCHAR NOT NULL,
    qty         NUMERIC(18,4) NOT NULL,
    CONSTRAINT ck_ro_qty    CHECK (qty > 0),               -- hanya produk yang dipesan disimpan
    CONSTRAINT uq_ro_detail UNIQUE (ro_id, kode_odoo)
);

-- ===================== SEED MASTER =====================
INSERT INTO app.master_dapur (kode_dapur, dapur) VALUES
    ('GRG','GORENGAN'), ('CKS','CAKE SLICE'), ('CKL','CAKE LOYANG'), ('ROTI','ROTI'),
    ('TART','TART'), ('JP','JAJAN PASAR'), ('ASN','ASIN')
ON CONFLICT (kode_dapur) DO NOTHING;

INSERT INTO app.master_toko (kode_toko, toko) VALUES
    (1,'TOKO SUDIRMAN'), (2,'TOKO KEDATON'), (3,'TOKO METRO'),
    (4,'TOKO UNILA'), (5,'TOKO RS AM'), (6,'TOKO RS AIRAN')
ON CONFLICT (kode_toko) DO NOTHING;

INSERT INTO app.master_jadwal (kode_jadwal, jadwal) VALUES
    (1,'KIRIM SUBUH'), (2,'KIRIM PAGI'), (3,'KIRIM SIANG'),
    (4,'TAMBAHAN SUBUH'), (5,'TAMBAHAN PAGI'), (6,'TAMBAHAN SIANG')
ON CONFLICT (kode_jadwal) DO NOTHING;
