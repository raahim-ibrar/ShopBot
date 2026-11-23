import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_setting(key: str) -> str | None:
    local_values = dotenv_values(PROJECT_ROOT / ".env.local", interpolate=False)
    env_values = dotenv_values(PROJECT_ROOT / ".env", interpolate=False)
    return os.getenv(key) or env_values.get(key) or local_values.get(key) or None


def database_url() -> str | None:
    return get_setting("DATABASE_URL_UNPOOLED") or get_setting("DATABASE_URL")


@dataclass(frozen=True)
class Database:
    url: str = field(repr=False)
    schema: str = "shopbot"
    pool: ConnectionPool | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.schema):
            raise ValueError("Invalid database schema name")

    @property
    def base_conninfo(self) -> str:
        settings = conninfo_to_dict(self.url)
        host = settings.get("host", "")
        first_label, dot, domain = host.partition(".")
        # The LangGraph migrations and checkpoints use session settings.
        if host.endswith(".neon.tech") and first_label.endswith("-pooler"):
            settings["host"] = first_label.removesuffix("-pooler") + dot + domain
        settings.setdefault("connect_timeout", "10")
        return make_conninfo(**settings)

    @property
    def conninfo(self) -> str:
        options = conninfo_to_dict(self.base_conninfo).get("options", "")
        return make_conninfo(self.base_conninfo, options=f"{options} -c search_path={self.schema}")

    def with_connection_pool(self) -> "Database":
        # LangGraph manages its own transactions on these autocommit sessions.
        pool = ConnectionPool(
            self.conninfo, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            min_size=1, max_size=6, timeout=12, open=False,
        )
        return replace(self, pool=pool)

    @contextmanager
    def connect(self):
        if self.pool is None:
            with psycopg.connect(self.conninfo, row_factory=dict_row) as conn:
                yield conn
            return

        # Preserve commit and rollback semantics for catalog, orders and budget writes.
        with self.pool.connection() as conn, conn.transaction():
            yield conn
