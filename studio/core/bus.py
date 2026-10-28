"""Tiny publish/subscribe bus feeding the server-sent-events stream."""
import json
import queue
import threading


class Bus:
    def __init__(self):
        self._subs = set()
        self._lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self, event: dict):
        data = json.dumps(event)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass   # a stalled client drops events; it will re-sync from /api/state


bus = Bus()
