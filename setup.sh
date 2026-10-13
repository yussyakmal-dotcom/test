#!/bin/sh
# Membuat .env dengan kunci acak. Tidak menimpa .env yang sudah ada.
set -e
if [ -f .env ]; then echo ".env sudah ada, tidak diubah."; exit 0; fi
rand() { head -c "$1" /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n'; }
PGPASS=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 28)
MASTER=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n')   # 32 byte -> kunci Fernet valid
SECRET=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 64)
DOMAIN_IN=${1:-localhost}
COOKIE_SECURE_IN=true
if [ "$DOMAIN_IN" = ":80" ]; then COOKIE_SECURE_IN=false; fi
umask 077
cat > .env <<EOT
POSTGRES_USER=odoobi
POSTGRES_PASSWORD=$PGPASS
POSTGRES_DB=odoo_bi
MASTER_KEY=$MASTER
SECRET_KEY=$SECRET
DOMAIN=$DOMAIN_IN
COOKIE_SECURE=$COOKIE_SECURE_IN
EOT
chmod 600 .env
echo ".env dibuat (izin 600). SIMPAN MASTER_KEY di password manager."
echo "Domain: $DOMAIN_IN  (ubah dengan: ./setup.sh nama.domain.anda, sebelum .env dibuat)"
echo "Lanjut: docker compose up -d --build"
