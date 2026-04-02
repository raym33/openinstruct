from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition
from typing import Dict, Iterator, List


@dataclass
class LockRecord:
    owner: str
    count: int = 0


class WorkspaceLockManager:
    def __init__(self) -> None:
        self._condition = Condition()
        self._locks: Dict[str, LockRecord] = {}

    @contextmanager
    def hold(self, keys: List[str], owner: str) -> Iterator[None]:
        normalized = sorted({key for key in keys if key})
        self.acquire(normalized, owner)
        try:
            yield
        finally:
            self.release(normalized, owner)

    def acquire(self, keys: List[str], owner: str) -> None:
        if not keys:
            return
        with self._condition:
            while any(key in self._locks and self._locks[key].owner != owner for key in keys):
                self._condition.wait()
            for key in keys:
                record = self._locks.get(key)
                if record and record.owner == owner:
                    record.count += 1
                else:
                    self._locks[key] = LockRecord(owner=owner, count=1)

    def release(self, keys: List[str], owner: str) -> None:
        if not keys:
            return
        with self._condition:
            for key in keys:
                record = self._locks.get(key)
                if not record or record.owner != owner:
                    continue
                record.count -= 1
                if record.count <= 0:
                    self._locks.pop(key, None)
            self._condition.notify_all()

    def snapshot(self) -> Dict[str, str]:
        with self._condition:
            return {key: record.owner for key, record in self._locks.items()}
