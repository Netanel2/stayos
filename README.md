# STAYOS — Luxury Hotel Guest Experience (Pilot demo)

Mobile guest portal + hotel admin with QR access passes, room amenities, activities, menu ordering, room folio, and reception chat.

## Run locally

```bash
pip install -r requirements.txt
python server.py
```

Open http://localhost:8765. Default local demo password: `stayos-demo` (**never use in production**).

## Deploy on Railway

See [DEPLOYMENT.md](DEPLOYMENT.md). Provide a strong password via the `STAYOS_ADMIN_PASSWORD` environment variable.

## Testing

```bash
python tests/verify_api.py
python tests/verify_v21.py
python tests/verify_v22.py
```

## Safety

Demonstration software only. Keep the repository private for now. No real guest records or financial data should be stored until independent security and reliability reviews.
