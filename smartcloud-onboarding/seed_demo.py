"""Fictional demo data so the teams can review the portal. All customers are made up.

Remove it later with:  rm data/portal.db  (then restart; the database is re-created empty)
"""
import json
import os
from datetime import datetime, timedelta, timezone

DOMAINS = [d.strip() for d in os.environ.get("ALLOWED_EMAIL_DOMAINS", "melita.com,smarttech.com.mt").split(",") if d.strip()]


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def seed(db, now, ensure_tasks):
    d1, d2 = DOMAINS[0], DOMAINS[-1]
    users = [
        ("demo.sales1@" + d1, "Demo Sales (Melita)", "Melita", "sales"),
        ("demo.sales2@" + d2, "Demo Sales (Smart)", "Smart Technologies", "sales"),
        ("demo.tech@" + d2, "Demo Technical", "Smart Technologies", "technical"),
    ]
    ids = []
    for email, name, company, role in users:
        row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        ids.append(row[0] if row else db.execute(
            "INSERT INTO users (email, name, company, role, created_at) VALUES (?,?,?,?,?)",
            (email, name, company, role, now())).lastrowid)
    s1, s2, tech = ids

    deals = [
        # customer, title, service, partner, owner, prob, mrc, otc, term, stage, age, next step
        ("Harbour Logistics Ltd", "Move ERP to cloud", "VPS", "Melita", s1, 60, 420, 300, 36, "presales", 12, "Send revised proposal with 2nd VPS"),
        ("Bluewave Dental Clinics", "Off-site backup for 4 clinics", "Backup", "Smart Technologies", s2, 75, 180, 0, 24, "presales", 6, "Decision meeting Thursday"),
        ("Valletta Legal Partners", "DMS hosting + backup", "VPS + Backup", "Melita", s1, 40, 650, 500, 36, "presales", 20, "Technical workshop with IT provider"),
        ("Gozo Retail Group", "POS backend VPS", "VPS", "Smart Technologies", s2, 25, 260, 0, 12, "presales", 3, "Discovery call booked"),
        ("Mediterra Insurance", "DR site & replication", "Bespoke", "Melita", s1, 50, 1450, 2500, 36, "presales", 30, "Awaiting RFP clarifications"),
        ("Coastline Hotels", "M365 backup", "Backup", "Melita", s1, 90, 95, 0, 12, "presales", 2, "Contract out for signature"),
        ("Pixel Forge Studios", "Render node VPS", "VPS", "Smart Technologies", s2, 100, 540, 150, 24, "tech_review", 2, ""),
        ("Northport Shipping", "VPS + Veeam backup", "VPS + Backup", "Melita", s1, 100, 880, 400, 36, "onboarding", 5, ""),
        ("St Julian's Accountants", "File server backup", "Backup", "Smart Technologies", s2, 100, 140, 0, 24, "finalization", 3, ""),
        ("Orion Fintech", "Private VPS cluster", "Bespoke", "Melita", s1, 100, 2100, 1800, 36, "networking", 1, ""),
        ("Sliema Pharmacy Chain", "Backup & VPS", "VPS + Backup", "Smart Technologies", s2, 100, 310, 200, 24, "live", 10, ""),
    ]
    for (cust, title, svc, partner, owner, prob, mrc, otc, term, stage, age, nxt) in deals:
        if db.execute("SELECT 1 FROM deals WHERE customer=?", (cust,)).fetchone():
            continue
        created = _ago(age + 25)
        close = (datetime.now() + timedelta(days=(100 - prob) // 2 + 7)).date().isoformat() if stage == "presales" else None
        did = db.execute(
            """INSERT INTO deals (customer, title, contact_name, contact_email, contact_phone, service_type, partner,
               lead_source, owner_id, probability, value_mrc, value_otc, term_months, expected_close, next_step, notes,
               stage, stage_changed_at, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cust, title, "Demo Contact", "it@example.com", "+356 2100 0000", svc, partner, "Inbound enquiry", owner,
             prob, mrc, otc, term, close, nxt, "Fictional demo record.", stage, _ago(age), owner, created, _ago(age))).lastrowid
        db.execute("INSERT INTO activity (deal_id, user_id, kind, message, created_at) VALUES (?,?,?,?,?)",
                   (did, owner, "created", f"Lead created ({svc}, 20%)", created))
        if stage != "presales":
            sheet = {"legal_name": cust, "tech_name": "Demo Contact", "tech_email": "it@example.com",
                     "tech_phone": "+356 2100 0000", "billing_email": "accounts@example.com", "contract_term": str(term),
                     "requested_start": (datetime.now() + timedelta(days=14)).date().isoformat(),
                     "net_connectivity": "Site-to-site IPsec VPN", "sla_level": "Standard",
                     "services": {"VPS": ["vps"], "Backup": ["backup"], "VPS + Backup": ["vps", "backup"], "Bespoke": ["vps", "bespoke"]}[svc],
                     "vps": [{"hostname": "app01", "package": "VPS Medium", "vcpu": "4", "ram_gb": "8", "storage_gb": "160",
                              "storage_tier": "SSD", "os": "Windows Server 2022 Standard", "licence": "Provided by Smart Cloud",
                              "qty": "1", "role": "Application server"}] if svc != "Backup" else [],
                     "bk_product": "Off-site backup (Veeam Cloud Connect)" if "Backup" in svc else "",
                     "bk_quota_tb": "2" if "Backup" in svc else "", "bk_retention": "30 days + 12 monthly" if "Backup" in svc else "",
                     "bespoke": "Dedicated private network between two VPS clusters, customer-managed firewall." if svc == "Bespoke" else ""}
            status = "submitted" if stage == "tech_review" else "approved"
            db.execute("""INSERT INTO sheets (deal_id, data, status, version, submitted_by, submitted_at, reviewed_by, reviewed_at, updated_at)
                          VALUES (?,?,?,?,?,?,?,?,?)""",
                       (did, json.dumps(sheet), status, 1, owner, _ago(age + 3), None if status == "submitted" else tech,
                        None if status == "submitted" else _ago(age + 2), _ago(age + 3)))
            order = ["onboarding", "finalization", "networking", "live"]
            for st in order[: order.index(stage) + 1] if stage in order else []:
                if st == "live":
                    break
                ensure_tasks(db, did, st)
                if st != stage:
                    db.execute("UPDATE tasks SET done=1, done_by=?, done_at=? WHERE deal_id=? AND stage=?", (tech, _ago(age), did, st))
            if stage == "onboarding":
                db.execute("""UPDATE tasks SET done=1, done_by=?, done_at=? WHERE deal_id=? AND stage='onboarding'
                              AND position < 304""", (tech, _ago(1), did))
            if stage == "networking":
                db.execute("UPDATE deals SET networking_call_at=? WHERE id=?",
                           ((datetime.now() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0).isoformat(timespec="minutes"), did))
