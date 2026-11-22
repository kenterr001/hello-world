# Smart Cloud: Sales & Onboarding Portal

This is an internal web portal shared by **Melita** and **Smart Technologies** for Smart Cloud deals. It covers each deal from first lead to a connected, live customer.

- **Dashboard** shows pipeline value, weighted value and service mix (VPS, Backup, VPS + Backup, Bespoke). It also shows how many deals are at each stage (Pre-Sales, Technical Review, Onboarding, Finalization, Networking Call, Live) and has a filterable deal table with probability, value, close date and next step.
- **Pipeline board** is a Kanban view of every deal by stage.
- **Leads** record the customer, service type, originating partner, owner, probability %, monthly and one-off value, term, next step, notes and a comment and activity log.
- **Technical onboarding sheet** is filled in when the deal is confirmed. It covers customer and contacts, VPS specs (one row per server, with package auto-fill), backup, networking and security, bespoke and compliance, handover and commercials. Submitting it marks the deal as won and sends it to the technical team.
- **Technical review queue**: the technical team approves the sheet or sends it back with comments. Approving it starts onboarding.
- **Checklists** for Onboarding, Finalization and the Networking Call are created automatically. A deal moves to the next stage only when its checklist is complete.
- **Process page** and a **printable blank onboarding sheet** can be printed or saved as PDF to share with the teams.
- **Admin**: invite users and manage roles. The **catalogue** of VPS packages, OS list, backup products and checklists is editable as JSON.
- **CSV export** of the whole pipeline.

All data stays on the VPS. It uses a SQLite database in `data/`, has no external services or CDNs, and emails only if you configure your own SMTP server.

> **Before go-live:** the VPS packages, OS list and backup products in `catalogue.default.json` are **placeholders**. Update them in **Admin → Catalogue** to match the packages published on Smart Cloud and on the Melita Business cloud hosting page. Also confirm the real email domains in `ALLOWED_EMAIL_DOMAINS`.

---

## Security model

| Control | How |
|---|---|
| Who can get in | Only email addresses on `ALLOWED_EMAIL_DOMAINS` (Melita + Smart Technologies) can be invited or sign in. There is no self-registration. |
| Secure link | An admin invites a user, and the portal generates a **one-time secure link** (valid 72 h) where the user sets their own password (min. 12 characters). |
| Transport | HTTPS only, via nginx with HSTS. Session cookies are `Secure`, `HttpOnly` and `SameSite`. |
| Network (optional) | You can restrict nginx to the two companies' office or VPN IP ranges (see `deploy/nginx.conf`). |
| App hardening | CSRF tokens on every form, login rate limiting, strict Content-Security-Policy, no-index headers, sessions that expire after 10 h, and passwords hashed with scrypt/pbkdf2. |
| Roles | **Sales**: leads and sheets. **Technical**: review and checklists. **Viewer**: read only. **Admin**: everything plus users and catalogue. |
| Audit | Every change, stage move, checklist tick and comment is written to the deal's activity log. |

---

## Deploy on a Smart Cloud VPS

You need an Ubuntu 22.04/24.04 or Debian 12 VPS (1 vCPU / 1 GB RAM is plenty) and a DNS name such as `onboarding.<your-domain>` pointing at it.

### Option A: Docker (recommended)

```bash
# 1. copy the archive to the VPS and extract it
tar xzf smartcloud-onboarding.tar.gz && cd smartcloud-onboarding

# 2. install Docker + nginx (if not already present)
apt-get update && apt-get install -y docker.io docker-compose-v2 nginx sqlite3

# 3. configure
cp .env.example .env && nano .env          # set APP_BASE_URL, ALLOWED_EMAIL_DOMAINS, TECH_TEAM_EMAILS, SMTP_*
mkdir -p data && chown 10001:10001 data     # container runs as non-root uid 10001

# 4. start
docker compose up -d --build

# 5. HTTPS: edit the hostname in deploy/nginx.conf, then
cp deploy/nginx.conf /etc/nginx/sites-available/smartcloud-portal
ln -s /etc/nginx/sites-available/smartcloud-portal /etc/nginx/sites-enabled/
apt-get install -y certbot python3-certbot-nginx && certbot --nginx -d onboarding.example.com.mt
nginx -t && systemctl reload nginx

# 6. create the first administrator. This prints a secure link to set the password.
docker compose exec portal python app.py create-admin --email you@melita.com --name "Your Name" --company Melita
```

### Option B: without Docker (systemd)

```bash
tar xzf smartcloud-onboarding.tar.gz && cd smartcloud-onboarding
sudo bash deploy/install.sh          # installs to /opt/smartcloud-portal and starts the service
sudo nano /opt/smartcloud-portal/.env && sudo systemctl restart smartcloud-portal
# then step 5 (nginx/TLS) and the create-admin command printed by the installer
```

### Optional: demo data for the review session

```bash
docker compose exec portal python app.py seed-demo     # 11 fictional deals across all stages
```

To clear it afterwards, stop the portal, delete `data/portal.db`, start it again and re-run `create-admin`.

### Backups

```bash
# nightly at 02:15, keeps 30 days
15 2 * * * /path/to/smartcloud-onboarding/deploy/backup.sh /path/to/smartcloud-onboarding/data /var/backups/smartcloud-portal
```

Everything that matters is in `data/` (`portal.db`, `catalogue.json` and `.secret_key`).

### Updating

Extract the new version over the old folder (keep `data/` and `.env`), then run `docker compose up -d --build`, or with Option B, `systemctl restart smartcloud-portal`.

---

## Using the portal

1. **Admin → Users**: invite colleagues and send each one their secure link.
2. **Sales** creates a lead (**+ New lead**) and keeps probability, value and next step up to date.
3. When the customer confirms, sales clicks **Deal confirmed – start onboarding sheet**, completes it and clicks **Submit to technical team**.
4. **Technical** opens **Technical review**, then approves the sheet or requests changes.
5. Technical works the **Onboarding** checklist, then **Finalization** (credentials handed over, client connecting, networking call booked), then the **Networking Call** checklist, then **Live**.

The workflow is described in full on the in-app **Process** page and in `docs/PROCESS.md`.

## Customising

- **Packages, OS list, backup products, retention options and checklists**: Admin → Catalogue. Changes apply to new sheets and checklists.
- **Stages and sheet fields** are defined in `app.py` (`STAGES`, `SHEET_TEXT_FIELDS`) and in the templates `templates/sheet_form.html` and `templates/_sheet_sections.html`.
- **Colours and branding**: `static/style.css` (`:root` variables).

## Local test run

```bash
pip install -r requirements.txt
COOKIE_SECURE=0 python app.py create-admin --email you@melita.com --name You --company Melita
COOKIE_SECURE=0 python app.py seed-demo
COOKIE_SECURE=0 python app.py run      # http://127.0.0.1:8000, then open the link printed by create-admin
```
