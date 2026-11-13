"""Bounded, lazy resources leased exclusively for a complete operation."""

from queue import Empty, Queue
import logging
from threading import local


class ResourcePool:
    def __init__(self, size, factory, close):
        self._slots = Queue(size)
        for _ in range(size):
            self._slots.put([None])
        self._local = local()
        self._factory = factory
        self._close = close

    def __enter__(self):
        if getattr(self._local, 'slot', None) is not None:
            raise RuntimeError('Resource leases cannot be nested')
        self._local.slot = self._slots.get()
        return self

    def get(self):
        slot = getattr(self._local, 'slot', None)
        if slot is None:
            raise RuntimeError('Resource access requires a lease')
        if slot[0] is None:
            slot[0] = self._factory()
        return slot[0]

    def __exit__(self, *exc):
        slot = self._local.slot
        self._local.slot = None
        self._slots.put(slot)

    def close(self):
        # Close idle resources only; never close a browser during a navigation.
        slots = []
        try:
            while True:
                try:
                    slot = self._slots.get_nowait()
                except Empty:
                    break
                slots.append(slot)
                if slot[0] is not None:
                    try:
                        self._close(slot[0])
                    except Exception:
                        logging.getLogger(__name__).warning('Resource cleanup failed')
                    finally:
                        slot[0] = None
        finally:
            for slot in slots:
                self._slots.put(slot)
