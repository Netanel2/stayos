# Railway deployment instructions

This is a **pilot demo**, not production software for actual guests.

1. Upload all app files to the repository root (server.py, requirements.txt, static/, tests/).
2. Railway: **New Project → Deploy from GitHub** → select `Netanel2/stayos`.
3. Add persistent volume at `/data`.
4. Add Railway Variables: `STAYOS_DB=/data/stay.db`, `STAYOS_UPLOAD_DIR=/data/uploads`, `STAYOS_ADMIN_PASSWORD` (long random secret), and `STAYOS_COOKIE_SECURE=1`.
5. Generate an HTTPS domain in Railway Networking and set `STAYOS_GUEST_BASE_URL` to that complete URL.
6. Redeploy; check QR, chat, requests, order folio and persistence across restart.

Keep repo **private**, do not upload stay.db or .env, and do not use real guest data before security review. Database and uploaded assets need backups.
