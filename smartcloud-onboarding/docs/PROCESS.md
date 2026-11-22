# Smart Cloud: Sales & Onboarding Process

**Shared by Melita and Smart Technologies · Draft for team review.** The target times below are proposals for the teams to agree.

```
 PRE-SALES ──► TECHNICAL REVIEW ──► ONBOARDING ──► FINALIZATION ──► NETWORKING CALL ──► LIVE
   Sales          Technical           Technical      Technical+Sales     Network eng.       Support
                     │
                     └── changes requested ──► back to Sales (sheet re-submitted as a new version)
```

## 1. Pre-Sales (owner: Sales)
- Register every real opportunity in the portal. Record the customer, contact, **service type** (VPS, Backup, VPS + Backup or Bespoke), **originating partner** (Melita or Smart Technologies) and owner.
- Record the **monthly recurring value**, **one-off value**, **term** and **probability %**. The dashboard calculates total contract value (TCV) and weighted value.
- Keep the **next step**, notes and comments current, and update the probability after every customer interaction.
- If the deal is lost, mark it **Lost** with a reason. It can be re-opened later.
- **Exit:** the customer confirms with a signed order or PO.

## 2. Deal confirmed: Technical Onboarding Sheet (owner: Sales)
Sales completes the sheet in the portal. A printable blank version is also available for customer meetings.

| Section | Content |
|---|---|
| 1 Customer & contacts | Legal name, reg./VAT no., billing address, billing / technical / escalation contacts, PO |
| 2 Services & contract | VPS / Backup / Bespoke, requested start date, term, SLA level |
| 3 VPS | One row per server: hostname, package, vCPU, RAM, disk, storage tier, OS, licence, qty, role. Also management level, public IPs, IPv6, private network, monitoring, AV, snapshots |
| 4 Backup | Product, source, protected count, quota (TB), frequency, retention, window, RPO/RTO, encryption, immutability, test restore |
| 5 Networking & security | Connectivity type, bandwidth, firewall, customer IPs, VPN peer, internal subnets, ports, DNS, call availability |
| 6 Bespoke & compliance | Non-standard requirements, regulatory requirements, data residency |
| 7 Access & handover | Customer users needing access, credential handover method, go-live dependencies |
| 8 Commercials | Agreed MRC / one-off, quote reference |

Submitting the sheet marks the deal as won (100%), locks the sheet and notifies the technical team.

## 3. Technical Review (owner: Technical · target 2 working days)
- The technical team checks the sheet against the catalogue and available capacity.
- **Approve:** onboarding starts and its checklist is created automatically.
- **Request changes:** the sheet goes back to sales with comments. Sales updates it and resubmits.

## 4. Onboarding (owner: Technical · target 5 working days for standard deals, agreed date for bespoke)
Customer account created · VPS(s) provisioned · public IPs and rDNS · firewall rules · backup jobs · monitoring · portal credentials · internal QA and test restore.

## 5. Finalization (owner: Technical with Sales · target 3 working days)
The service has been handed over and the client only needs to connect.
Welcome pack and credentials sent · client confirms receipt · client connected · **networking call scheduled**.

## 6. Networking Call (owner: Technical / network engineer · within 5 working days of finalization)
Call held · VPN / private connectivity configured · end-to-end test · client sign-off · billing activated.

## 7. Live
The deal is closed in the portal. Changes and incidents go through normal support.

## Roles (RACI)
| Activity | Sales | Technical | Admin |
|---|---|---|---|
| Register / update leads | R | I | – |
| Complete & submit onboarding sheet | R | C | – |
| Review / approve sheet | I | R | – |
| Provision, configure, QA | I | R | – |
| Credential handover | C | R | – |
| Networking call & sign-off | I | R | – |
| Users, catalogue, checklists | – | – | R |

## Points for the teams to confirm
1. The real VPS packages, OS images and backup products, so they can be loaded into Admin → Catalogue.
2. The email domains allowed to access the portal, and the technical team mailbox.
3. The stage target times above.
4. Whether onboarding, finalization and networking checklist items should be added or removed.
5. The hostname for the portal on Smart Cloud, and whether to restrict access to office / VPN IP ranges.
