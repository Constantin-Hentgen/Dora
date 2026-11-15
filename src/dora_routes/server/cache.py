"""Short-lived in-memory store behind GET /v1/loops/{id}.gpx and .png.

The service stays stateless: nothing is persisted and a restart empties it. It only lets a client that got a loop from
POST /v1/loops fetch its GPX or map a moment later without sending the points back."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from dora_routes.loops import Loop


@dataclass
class Entry:
    loop: Loop
    name: str
    expires: float
    png: bytes | None = None


class ResultCache:
    def __init__(self, ttl_s: float, max_size: int) -> None:
        self.ttl_s, self.max_size = ttl_s, max_size
        self._items: OrderedDict[str, Entry] = OrderedDict()

    def put(self, loop: Loop, name: str) -> str:
        self._expire()
        loop_id = uuid.uuid4().hex[:16]
        self._items[loop_id] = Entry(loop, name, time.monotonic() + self.ttl_s)
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)
        return loop_id

    def get(self, loop_id: str) -> Entry | None:
        self._expire()
        entry = self._items.get(loop_id)
        if entry:
            self._items.move_to_end(loop_id)
        return entry

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, e in self._items.items() if e.expires <= now]:
            del self._items[key]

    def __len__(self) -> int:
        return len(self._items)
