"""Server-sent events: push each stored task event to the phone the moment it commits.

The stream is an optimisation over ``GET /api/v1/events``, never a second source of
truth: it replays from the client's cursor, sends the same envelopes in event-ID
order, and a client that loses it falls back to polling without losing anything.
"""

import asyncio
import json

from .errors import ApiError


class EventHub:
    """Wakes stream readers when events commit. ``notify`` is safe from any thread."""

    def __init__(self):
        self._loop = None
        self._event = asyncio.Event()

    def bind(self, loop):
        if self._loop is not loop:
            self._loop = loop
            self._event = asyncio.Event()

    def notify(self):
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._wake()
        else:
            try:
                loop.call_soon_threadsafe(self._wake)
            except RuntimeError:
                pass  # The loop closed between the check and the call.

    def _wake(self):
        current, self._event = self._event, asyncio.Event()
        current.set()

    async def wait(self, timeout):
        event = self._event
        try:
            await asyncio.wait_for(event.wait(), max(0.0, timeout))
        except asyncio.TimeoutError:
            pass


class Ticket:
    __slots__ = ("client_id", "evicted")

    def __init__(self, client_id):
        self.client_id, self.evicted = client_id, False


class StreamRegistry:
    """Bounded concurrent streams: a phone's newest stream replaces its oldest; the gateway refuses beyond its total."""

    def __init__(self, per_client=3, total=32):
        self.per_client, self.total = per_client, total
        self.open_tickets = []

    def open(self, client_id):
        mine = [ticket for ticket in self.open_tickets if ticket.client_id == client_id]
        # A reconnect usually races its own dead predecessor; let the new one win.
        while len(mine) >= self.per_client:
            oldest = mine.pop(0)
            oldest.evicted = True
            self.open_tickets.remove(oldest)
        if len(self.open_tickets) >= self.total:
            raise ApiError(503, "unavailable", "Too many open event streams; poll instead.", retryable=True)
        ticket = Ticket(client_id)
        self.open_tickets.append(ticket)
        return ticket

    def close(self, ticket):
        if ticket in self.open_tickets:
            self.open_tickets.remove(ticket)


def frame(event=None, data=None, event_id=None, comment=None, retry=None):
    """One SSE frame. JSON data is single-line, so it never needs splitting across ``data:`` lines."""
    lines = []
    if comment is not None:
        lines.append(": " + comment)
    if retry is not None:
        lines.append("retry: %d" % retry)
    if event_id is not None:
        lines.append("id: %d" % event_id)
    if event is not None:
        lines.append("event: " + event)
    if data is not None:
        lines.append("data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return ("\n".join(lines) + "\n\n").encode()
