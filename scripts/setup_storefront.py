from pathlib import Path
from secrets import token_urlsafe

from dotenv import dotenv_values, set_key


root = Path(__file__).resolve().parents[1]
backend_env = root / ".env"
frontend_env = root / "web" / ".env.local"

if not backend_env.exists():
    backend_env.write_text((root / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")

values = dotenv_values(backend_env, interpolate=False)
for key in ("STOREFRONT_API_SECRET", "ADMIN_API_SECRET", "ADMIN_PASSWORD", "SESSION_SECRET"):
    if not values.get(key):
        values[key] = token_urlsafe(18 if key == "ADMIN_PASSWORD" else 32)
        set_key(backend_env, key, values[key])

for key in ("STOREFRONT_API_SECRET", "ADMIN_API_SECRET", "SESSION_SECRET"):
    set_key(frontend_env, key, values[key])

if not dotenv_values(frontend_env).get("PORTPAIR_API_URL"):
    set_key(frontend_env, "PORTPAIR_API_URL", "http://127.0.0.1:8010")

print("Local storefront configured. Read ADMIN_PASSWORD from the ignored root .env for seller sign-in.")
