#!/usr/bin/env bash
# Bare-metal install on Ubuntu 22.04/24.04 or Debian 12 (no Docker). Run as root from the extracted folder.
set -euo pipefail
DEST=/opt/smartcloud-portal
apt-get update
apt-get install -y python3 python3-venv nginx sqlite3
id portal >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin portal
mkdir -p "$DEST"
cp -r app.py seed_demo.py catalogue.default.json requirements.txt templates static deploy "$DEST"/
[ -f "$DEST/.env" ] || cp .env.example "$DEST/.env"
python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install --upgrade pip -q
"$DEST/venv/bin/pip" install -r "$DEST/requirements.txt" -q
mkdir -p "$DEST/data"
chown -R portal:portal "$DEST/data"
chmod 600 "$DEST/.env"
cp deploy/smartcloud-portal.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now smartcloud-portal
echo
echo "Installed. Next steps:"
echo "  1. Edit $DEST/.env (APP_BASE_URL, ALLOWED_EMAIL_DOMAINS, SMTP...) then: systemctl restart smartcloud-portal"
echo "  2. Configure nginx + TLS using deploy/nginx.conf"
echo "  3. Create the first admin:"
echo "     cd $DEST && sudo -u portal env \$(grep -v '^#' .env | xargs) DATA_DIR=$DEST/data venv/bin/python app.py create-admin --email you@melita.com --name 'Your Name' --company Melita"
