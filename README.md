# Odoo BI (Docker) - Tahap 1

Aplikasi web + scheduler + PostgreSQL + proxy HTTPS untuk menarik data Odoo 19 SaaS setiap malam.
Dokumen PRD lengkap ada di Claude Docs ("PRD BI Server Odoo 19 - Tahap 1").

## Struktur
- `docker-compose.yml`  4 container: `db`, `app`, `scheduler`, `proxy`
- `app/web.py`          login, ganti password, user, pengaturan terenkripsi, status ETL, laporan
- `app/scheduler.py`    menjalankan job antrian dan jadwal harian
- `app/etl/`            klien Odoo `/json/2`, daftar model/field, runner ETL
- `app/sql/`            `schema.sql` (stg, dwh, etl), `app_schema.sql` (app), `transform.sql`

## Menjalankan
    sh setup.sh app.domainanda.com      # membuat .env berisi kunci acak (atau tanpa argumen untuk localhost)
    docker compose up -d --build
    docker compose logs app            # cari baris PASSWORD untuk admin awal

1. Buka `https://app.domainanda.com`, login `admin` dengan password dari log, lalu ganti password.
2. **Pengaturan**: isi URL Odoo + API key user BI (read-only), klik **Test koneksi**, atur jadwal, simpan.
3. **User**: buat user `reporting` (password sementara tampil sekali).
4. **Status ETL**: pilih *Backfill*, isi tanggal awal, Mulai. Setelah selesai, ETL jalan otomatis sesuai jadwal.

Uji lokal tanpa domain/HTTPS: di `.env` set `DOMAIN=:80` dan `COOKIE_SECURE=false`, lalu buka `http://IP-server`.

## Yang disimpan di mana
| Lokasi | Isi |
|---|---|
| `.env` (izin 600, jangan di Git) | password Postgres, `MASTER_KEY`, `SECRET_KEY`, domain |
| DB, terenkripsi (Fernet) | API key Odoo |
| DB, biasa | URL Odoo, lookback, batch, jadwal |

**Simpan `MASTER_KEY` di password manager.** Jika hilang, API key Odoo cukup diisi ulang di Pengaturan.

## Operasional
- Log: `docker compose logs -f scheduler`
- Pembaruan kode: `docker compose up -d --build` (migrasi skema otomatis)
- Cadangan: snapshot VPS harian bergilir 7 hari di provider (jam setelah ETL, mis. 04.00). Tidak ada modul backup di aplikasi.
- Akses database untuk dashboard (Metabase/Superset): buat user baca-saja ke schema `dwh`, mis.
      docker compose exec db psql -U odoobi -d odoo_bi -c "CREATE ROLE bi_reader LOGIN PASSWORD '...'; GRANT USAGE ON SCHEMA dwh TO bi_reader; GRANT SELECT ON ALL TABLES IN SCHEMA dwh TO bi_reader; ALTER DEFAULT PRIVILEGES IN SCHEMA dwh GRANT SELECT ON TABLES TO bi_reader;"
  Database tidak membuka port; jalankan Metabase sebagai container di jaringan `backend` yang sama bila ingin terhubung.

## Catatan
- Nama field Odoo ada di `app/etl/models_config.py`; hapus field yang ditolak instance Anda.
- Kode belum diuji terhadap Odoo/PostgreSQL asli; uji dulu dengan backfill satu hari.


## UAT dan produksi (dua instalasi terpisah)
Odoo UAT dan produksi punya database dan ID record berbeda, jadi BI-nya juga dipisah:

| | BI UAT | BI Produksi |
|---|---|---|
| Folder / stack | `odoo_bi_uat` | `odoo_bi_prod` (salinan baru dari zip yang sama) |
| `.env` | `sh setup.sh bi-uat.domain` | `sh setup.sh bi.domain` (kunci dan password BARU) |
| Pengaturan > Odoo | URL dan API key UAT | URL dan API key produksi |
| Label lingkungan | `UAT` (kuning) | `PRODUKSI` (merah) |
| Fungsi | menguji perubahan BI lebih dulu | hanya menerima yang sudah disetujui |

Aturan: jangan mengganti URL Odoo pada satu instalasi. ETL menolak berjalan bila URL berbeda dari asal data.
Bila sebuah instalasi memang akan dipindah ke Odoo lain: `docker compose exec app python -m app.reset`, lalu ganti URL dan backfill ulang.
Dua stack di satu server berebut port 80/443; letakkan produksi di server terpisah, atau minta konfigurasi satu proxy untuk dua stack.

## Modul Tema Desktop

Menu **Tema Desktop** tersedia bagi semua pengguna yang sudah login. Pemilih
juga tersedia pada halaman login, Form RO, dan Master RO. Pilihan diterapkan
langsung tanpa memuat ulang halaman:

- **Ikuti sistem**: mengikuti perubahan mode terang/gelap perangkat.
- **Terang**, **Gelap**, atau **Biru Laut**: pilihan manual.

Preferensi tersimpan di `localStorage` browser, bertahan setelah login ulang,
dan tersinkron antar-tab pada alamat aplikasi yang sama. Preferensi berlaku
per browser, bukan per akun; browser/perangkat lain memilih temanya sendiri.
Jika penyimpanan browser diblokir, pilihan hanya berlaku pada halaman saat ini.
Aset tema disajikan aplikasi sendiri; modul tema tidak memerlukan CDN.
Halaman RO bawaan masih menggunakan Bootstrap dari jsDelivr.

### Jalankan lokal dengan Docker

Prasyarat: Docker Engine dan Docker Compose v2.

```sh
sh setup.sh :80
docker compose up -d --build
docker compose ps
docker compose logs app  # password admin awal ditampilkan sekali
```

Buka `http://localhost`, login sebagai `admin`, lalu ganti password awal.
`setup.sh :80` mengatur cookie untuk HTTP lokal. Untuk HTTPS produksi gunakan
`sh setup.sh bi.domainanda.com` pada instalasi baru. Skrip tidak menimpa `.env`
yang sudah ada; untuk konfigurasi lama, periksa `DOMAIN` dan `COOKIE_SECURE`.
Data Odoo memerlukan URL dan API key yang Anda isi melalui menu Pengaturan;
modul tema dapat digunakan tanpa koneksi Odoo. Jangan commit `.env`.

### Uji tema di browser

Dengan aplikasi berjalan:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/test_theme.py --url http://localhost
```

Tes memeriksa seluruh pilihan, penyimpanan setelah reload, sinkronisasi antar-tab,
perubahan tema sistem, dan nilai preferensi yang tidak valid.

Untuk pengujian halaman setelah login, tes menerima nama pengguna dan password
akun uji melalui `ODOOBI_TEST_USERNAME` dan `ODOOBI_TEST_PASSWORD`. Gunakan akun
uji admin untuk memeriksa halaman Master RO; jangan simpan kredensial dalam kode.
Jika Chromium sudah tersedia di sistem, set `CHROMIUM_PATH` ke lokasi binarinya.

Validasi versi ini: build image Docker, migrasi PostgreSQL, endpoint `/health`,
login akun uji, halaman tema, Form RO, Master Template, dan Master Jadwal berhasil.
Tes browser juga lulus ketika penyimpanan browser diblokir. Sinkronisasi ETL ke
Odoo belum diuji karena URL dan API key instance belum diberikan.
