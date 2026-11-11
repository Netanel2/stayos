# STAYOS — Luxury Hotel Guest Experience

Hotel guest portal and receptionist dashboard, with QR guest passes, activities, room service, orders, room folio, and chat.

The app source is being prepared for Railway. **Demo only; not yet approved for real guest data.**

### Run locally

`pip install -r requirements.txt`

`python server.py`

### Deploy

See `DEPLOYMENT.md` after uploading the application files. Configure a strong `STAYOS_ADMIN_PASSWORD`, HTTPS public URL, and persistent storage volume for `/data`.

**Never commit** real guest databases (`stay.db`), `.env`, credentials, or payment information. The default local password `stayos-demo` must never be used publicly.
