import copy
import time
from collections import OrderedDict


class TTLCache:
    """Bounded process-local cache, used only on the event-loop thread.

    Caller keys must include owner, KB revision, model/embedding signature and
    policy version. Values are copied so no request can mutate another's data.
    """
    def __init__(self, capacity=256, ttl=300):
        self.capacity, self.ttl = capacity, ttl
        self.values = OrderedDict()

    def get(self, key):
        record = self.values.get(key)
        if not record:
            return None
        expires, value = record
        if time.monotonic() >= expires:
            del self.values[key]
            return None
        self.values.move_to_end(key)
        return copy.deepcopy(value)

    def set(self, key, value):
        if self.capacity <= 0 or self.ttl <= 0:
            return
        self.values[key] = (time.monotonic() + self.ttl, copy.deepcopy(value))
        self.values.move_to_end(key)
        while len(self.values) > self.capacity:
            self.values.popitem(last=False)
