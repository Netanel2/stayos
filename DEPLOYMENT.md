# STAYOS — Railway pilot deployment

**Not yet certified for use with real guest information or actual payment collection.**

1. Upload the *contents* of this folder to your private GitHub repository, retaining the `static/` and `tests/` directories. Do **not** upload `stay.db`, `.env`, credentials or exported customer information.
2. Railway → New Project → Deploy from GitHub → select repository.
3. Railway → Storage → add **persistent volume mounted at `/data`** (required to retain bookings and chat).
4. Railway → Variables → set `STAYOS_DB=/data/stay.db`, `STAYOS_UPLOAD_DIR=/data/uploads`, `STAYOS_ADMIN_PASSWORD=<new unique long password>`, `STAYOS_COOKIE_SECURE=1`.
5. Railway → Networking → Generate Domain. Then set `STAYOS_GUEST_BASE_URL=https://YOUR-DOMAIN.up.railway.app`. Redeploy.
6. Test staff sign-in and QR from a separate browser/mobile network; create a stay and verify a request and chat message reach admin. Restart the service, confirm records/images persist.
7. Before deploying for real guests: conduct security/privacy assessment, add backup/restore and monitoring, access isolation for different hotels, durable auth sessions and an incident-response plan.

Railway usage is billed according to plan and consumption. The code currently supports a **single demo hotel** and uses SQLite, not a multi-tenant production backend.
