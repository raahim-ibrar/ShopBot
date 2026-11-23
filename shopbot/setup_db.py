from pathlib import Path

import psycopg
from psycopg import sql

from .catalog import device_key
from .db import Database, database_url
from .identity import DEMO_STORE
from .memory import memory_backends


USB_C_PHONES = ("iPhone 15", "iPhone 16", "Pixel 8", "Pixel 9", "Galaxy S24", "Galaxy S25", "USB-C")
LIGHTNING_PHONES = ("iPhone 13", "iPhone 14", "Lightning")
LAPTOPS = ("MacBook Air", "MacBook Pro", "USB-C laptop", "USB-C")
PRODUCTS = (
    ("CHG-USB20", "20W USB-C Wall Charger", "charger", "Compact USB-C PD wall charger; cable sold separately.", USB_C_PHONES, 1499, 18),
    ("CHG-USB30", "30W USB-C Wall Charger", "charger", "USB-C PD with faster charging on supported devices.", USB_C_PHONES, 2299, 12),
    ("CHG-USB65", "65W USB-C GaN Charger", "charger", "Dual-port USB-C charger for compatible laptops and phones.", LAPTOPS + USB_C_PHONES, 3999, 8),
    ("CHG-QI2", "Qi2 Magnetic Wireless Charger", "charger", "Magnetic 15W wireless charging pad; USB-C power input.", ("iPhone 15", "iPhone 16", "Qi2"), 2999, 0),
    ("CBL-C2C1", "USB-C to USB-C Cable, 1 m", "cable", "Braided USB-C power and data cable rated up to 60W.", USB_C_PHONES + LAPTOPS, 799, 30),
    ("CBL-C2C2", "USB-C to USB-C Cable, 2 m", "cable", "USB-C power and data cable rated up to 100W.", USB_C_PHONES + LAPTOPS, 1299, 16),
    ("CBL-LIGHT", "USB-C to Lightning Cable, 1 m", "cable", "Charging cable for Lightning iPhones.", LIGHTNING_PHONES, 1199, 10),
    ("PB-10K", "10,000 mAh USB-C Power Bank", "power bank", "Pocket power bank with 20W USB-C output.", USB_C_PHONES, 2499, 14),
    ("PB-20K", "20,000 mAh USB-C Power Bank", "power bank", "High-capacity power bank with 30W USB-C output.", USB_C_PHONES, 4499, 5),
    ("HUB-5IN1", "5-in-1 USB-C Hub", "hub", "HDMI, two USB-A ports, SD, and USB-C power pass-through.", LAPTOPS, 3299, 7),
    ("AUD-USBC", "Wired USB-C Earbuds", "audio", "In-line mic and volume controls for USB-C devices.", USB_C_PHONES, 1799, 13),
    ("AUD-BT", "Bluetooth Earbuds", "audio", "Bluetooth earbuds with charging case and in-line controls.", USB_C_PHONES + LIGHTNING_PHONES, 3499, 9),
)


def setup_database(db: Database) -> None:
    with psycopg.connect(db.base_conninfo, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(db.schema)))

    schema_sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.execute(schema_sql)
        with conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO products (
                    channel, store_id, sku, name, category, description, compatible_devices, price_cents, stock
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (channel, store_id, sku) DO NOTHING
                """,
                [
                    (channel, DEMO_STORE, sku, name, category, description,
                     sorted({device_key(device) for device in devices}), price, stock)
                    for channel in ("website", "grading")
                    for sku, name, category, description, devices, price, stock in PRODUCTS
                ],
            )

    with memory_backends(db) as (checkpointer, store):
        checkpointer.setup()
        store.setup()


if __name__ == "__main__":
    url = database_url()
    if url is None:
        raise SystemExit("Set DATABASE_URL in .env or the environment first.")
    setup_database(Database(url))
    print(f"PortPair schema ready. Seeded {len(PRODUCTS)} demo products per channel.")
