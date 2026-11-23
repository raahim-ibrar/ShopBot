import hashlib
import json
from dataclasses import dataclass
from typing import Literal


DEMO_STORE = "tech-accessories"


@dataclass(frozen=True)
class Store:
    channel: Literal["website", "grading"]
    store_id: str = DEMO_STORE


@dataclass(frozen=True)
class Shopper:
    store: Store
    user_id: str
    thread_id: str

    @property
    def customer_key(self) -> str:
        return self._key(self.user_id)

    @property
    def thread_key(self) -> str:
        return self._key(self.user_id, self.thread_id)

    def _key(self, *parts: str) -> str:
        identity = [self.store.channel, self.store.store_id, *parts]
        encoded = json.dumps(identity, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()
