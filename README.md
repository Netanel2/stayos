> **v3.2:** See [`UPGRADE_v3.2_HE.md`](UPGRADE_v3.2_HE.md) for live reception alerts, room history, photo replacement and Railway update instructions.

# STAYOS v3.2 — Multi-Hotel Pilot Platform

A premium hotel guest experience platform with a **platform owner console**, **separate hotel management workspaces**, **granular staff permissions**, and **mobile guest portal**.

> **Pilot / demonstration software.** This is not production-certified for real guest data, payment processing, or multiple server replicas. Security, regulatory/privacy, accessibility, support and operational reviews are required before an actual hotel rollout.

## What changed in v3.1

- **Working live ordering demo:** the Bayview workspace now has **Try guest demo** for logged-in authorized staff. It opens an active time-limited guest QR session. Public previews remain read-only so anonymous visitors cannot create charges.
- **Guest cart fixed:** changing quantities does not reset the delivery destination, table/sunbed or notes; location details are required away from the room; the button clearly reflects whether the guest pass allows orders.
- **Reception event feed:** requests, food orders, activity bookings and guest messages are recorded in a permission-scoped live feed polled every 10 seconds while the page is open. In-app notifications are automatic; optional sound / browser notifications must be enabled by the user. This is **not background Push**.
- **Activity Bookings:** new reception view; registrations and prices reach the desk and room folio.
- **Coverage:** `python tests/test_platform.py`, `python tests/test_notifications.py`, `python tests/test_guest_flows.py`, `python tests/test_browser.py`.

## What's inside

- **Owner / Super Admin:** create and suspend hotels; enter each hotel's workspace; assign its first manager.
- **Hotel Manager:** manage room types, physical rooms, photos, menus, prices, amenities/quotas, activities, facility/services content, branding, and staff.
- **Permissions:** a hotel manager can create staff accounts and grant No access, View only or View & edit for each module; staff do not inherit editing rights by default. Server enforces permissions and hotel isolation.
- **Guest web app:** QR-based time-limited room access, dining orders with six delivery areas (room/pool/lobby/beach/restaurant/garden), amenities requests, activities bookings, two-way reception chat, room folio, and Hebrew/English.
- **Reception:** QR creation/revocation, requests, delivery statuses, guest chat, and a checkout folio which is marked paid **only after payment is collected outside STAYOS**.
- **Media:** photos uploaded to hotel-specific directories and transcoded to WebP; persistent storage on the Railway volume.
- **PWA:** per-stay manifest points to the guest's room on relaunch; Android installation where supported; iOS Safari needs manual Add to Home Screen.
- **Notifications:** manager-side polling and user-enabled sound while the page is open. No push notification service yet.

## Included example property

**Bayview Hotel — Concept Demo** (`/h/bayview-demo/preview`) is a **fictional, unofficial presentation of the hotel we contacted**, preloaded with illustrative AI resort photography, sample rooms, menu, services, activities, and a fictitious demo stay. It is **not** the hotel's official website, not associated with its management, and no actual hotel inventory, pricing, or guest records are used.

## Local quick start

Requires Python 3.10+.

```bash
python -m pip install -r requirements.txt
export STAYOS_ADMIN_PASSWORD='REPLACE-WITH-UNIQUE-LONG-PASSWORD'
python server.py
```

Open `http://localhost:8765`, sign in with username `owner` and the password defined in `STAYOS_ADMIN_PASSWORD`.

- Owner dashboard: `/owner` (after sign in)
- Bayview guest concept: `/h/bayview-demo/preview`
- Generated QR guest links: `/h/bayview-demo/guest?token=...`

Default local database: `local-data/platform.sqlite`; uploads: `local-data/media`. **Do not upload these to GitHub.**

## Deploy to Railway

See [DEPLOY_RAILWAY_HE.md](DEPLOY_RAILWAY_HE.md) for steps tailored to the existing Railway service, and [.env.example](.env.example) for variables.

**New v3 database:** `STAYOS_PLATFORM_DB=/data/platform.sqlite`. Previous v2 `stay.db` stays untouched; v3 does **not** migrate the old data automatically. Take a volume backup first.

Only run **one app replica** against the SQLite database. For larger-scale deployments move to managed PostgreSQL, and include automated backups, restore testing, monitoring, a notification queue and a full security/privacy review.

## Run tests

```bash
python -m pip install -r requirements-dev.txt
python tests/test_platform.py
python tests/test_browser.py
```

The browser test uses Chromium from `/usr/bin/chromium` and a local HTTP test proxy to accommodate this build environment; adjust if running elsewhere. The original API suite covers 41 integration cases, with additional notification and interactive guest-flow regressions, including view-only versus editing, cross-hotel access checks, reservations, charges, and access revocation. A Chromium UI smoke test passed on desktop/mobile-width layouts, but no real mobile device installation or full accessibility audit has been performed.

## Security & limitations

- Store `STAYOS_ADMIN_PASSWORD` only in Railway Variables. The owner username is `owner`.
- The initial owner password is read **on first creation** of the v3 database; use the UI's Change password action to rotate it afterward.
- Turn the GitHub repo **private** before uploading business code. Never commit live guest data, volumes, local `.env`, payment details or passwords.
- QR links are private bearer credentials. Share only with the appropriate room guest and revoke at checkout.
- Uploaded images are public hotel marketing assets, not a place to upload sensitive guest records.
- Marking a folio paid is a record of payment taken externally, **not** charging a card or integrating with a hotel PMS.
- Dashboard uses polling when open, not mobile push notifications. Domain-specific privacy notices, backups, support procedures and MFA are not included.
- Content saves publish immediately. Draft approval/rollback is not implemented.
