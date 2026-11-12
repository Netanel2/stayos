# STAYOS v4.0 — Real hotel pilot / operational guest platform

STAYOS manages individual hotel accounts with a staff administration dashboard and guest QR application. **It does not process online payments.** It records chargeable services, meals and paid activities against a guest stay; reception collects the money with the hotel's own terminal at checkout, then records the collection in STAYOS.

## No fake hotels or demo content by default

- New installations start with **zero hotels, zero rooms, zero menu products and zero guest passes**. The owner creates a hotel and adds actual authorized content.
- On an existing data volume, previously seeded `demo=1` concept hotels are automatically **deactivated and hidden**, not erased. Their historical records are preserved for recovery.
- Set `STAYOS_DEMO_SEED=0` in Railway. Even if it is mistakenly set to 1, concept seeding is disabled in Railway/cloud environments.
- Images are blank until uploaded by the hotel; a neutral placeholder is used instead of passing off AI photography as the hotel's own.

## Modules

- **Platform owner:** create/suspend real hotel accounts, manage hotels and provision hotel staff.
- **Hotel manager:** edit rooms, room types, hotel photographs, menus, prices, amenities, activities, branding and access permissions.
- **Guest app:** time-limited personal QR, food deliveries to room/pool/lobby/beach/restaurant/garden, service requests, activities, chat and guest charge history.
- **Reception:** live activity feed while the page is open, status updates, room history search and checkout per stay.
- **Checkout:** open/paid/void charges, delivery details, free activities and complimentary requests, external payment record with confirmation and receipt reference, and automatic guest-pass closure after collection. An empty account can be closed without recording payment.

## Deploy to Railway

Read `UPGRADE_v4_HE.md`. The app needs a persistent `/data` Volume, `STAYOS_PLATFORM_DB=/data/platform.sqlite`, `STAYOS_MEDIA_DIR=/data/media`, a unique `STAYOS_ADMIN_PASSWORD`, and `STAYOS_COOKIE_SECURE=1`. **Back up your current `/data/platform.sqlite` first.** Use one replica while using SQLite.

## Tests

```
pip install -r requirements-dev.txt
python tests/test_platform.py
python tests/test_notifications.py
python tests/test_guest_flows.py
python tests/test_room_history.py
python tests/test_real_pilot_checkout.py
python tests/test_checkout_browser.py
```

The suite uses disposable test hotels. Some tests explicitly enable concept data for regression; this does **not** seed the deployed app.

## Not yet suitable for unrestricted real guest rollout

A technical pilot is not an independent security certification. Before storing real guest data, complete a security/privacy review, staff onboarding, automated off-site backups and restore testing, monitoring, load testing, accessibility review, incident response and operational acceptance testing on physical phones. Browser notifications require the receptionist page open; offline push is not supported. The system is not a hotel PMS, payment processor, fiscal invoice provider or replacement for hotel accounting.
