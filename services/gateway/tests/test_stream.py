import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from uuid import uuid4

from chintu_adapters import ScriptedAdapter
from chintu_gateway import Settings, create_app
from chintu_gateway.errors import ApiError
from chintu_gateway.stream import EventHub, StreamRegistry, frame
from starlette.testclient import TestClient


def uid():
    return str(uuid4())


def frames(body):
    """Parsed SSE frames: dicts of field -> value, with comments under ':'."""
    parsed = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        fields = {}
        for line in block.split("\n"):
            name, _, value = (":", "", line[1:]) if line.startswith(":") else line.partition(":")
            fields.setdefault(name, "")
            fields[name] += value[1:] if value.startswith(" ") else value
        parsed.append(fields)
    return parsed


class EventStreamTests(unittest.TestCase):
    def start(self, **overrides):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = Settings(Path(self.temp.name) / "gateway.sqlite3", **overrides)
        self.app = create_app(settings, adapter=ScriptedAdapter())
        self.client = TestClient(self.app).__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = self.pair()

    def pair(self):
        response = self.client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"}, json={
            "enrollment_secret": self.app.state.store.create_enrollment(), "client_name": "Phone"})
        return {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + response.json()["token"]}

    def submit(self, instruction="hello", headers=None):
        response = self.client.post("/api/v1/tasks", headers=headers or self.headers, json={
            "submission_key": uid(), "project_id": "fixture", "provider": "scripted",
            "execution_profile": "inspect", "instruction": instruction})
        self.assertEqual(response.status_code, 202, response.text)
        task_id = response.json()["task"]["task_id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self.app.state.store.get(task_id)["state"] != "succeeded":
            time.sleep(.01)
        return task_id

    def stream(self, query="", headers=None, expected=200):
        response = self.client.get("/api/v1/events/stream" + query, headers={**self.headers, **(headers or {})})
        self.assertEqual(response.status_code, expected, response.text)
        if expected != 200:
            self.app.state.contract.validate("Error", response.json())
            return None
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-store")
        return frames(response.text)

    def test_replays_after_the_cursor_in_order_then_ends_at_its_lifetime(self):
        self.start(stream_heartbeat_seconds=30)
        first, second = self.submit("one"), self.submit("two")
        received = self.stream("?after=0&max_seconds=1")
        self.assertEqual(received[0], {":": "chintu event stream", "retry": "2000"})
        ready = json.loads(received[1]["data"])
        self.assertEqual((received[1]["event"], ready["cursor"], ready["max_seconds"]), ("ready", 0, 1))
        events = [json.loads(item["data"]) for item in received[2:]]
        stored = self.app.state.store.events_page(0, 100)["events"]
        self.assertEqual(events, stored)
        for item, event in zip(received[2:], events):
            self.app.state.contract.validate("Event", event)
            self.assertEqual((int(item["id"]), item["event"]), (event["event_id"], event["kind"]))
        self.assertEqual({event["task_id"] for event in events}, {first, second})
        # Last-Event-ID resumes where the client stopped and wins over the query cursor.
        middle = events[3]["event_id"]
        resumed = self.stream("?after=0&max_seconds=1", {"Last-Event-ID": str(middle)})
        self.assertEqual([json.loads(item["data"])["event_id"] for item in resumed[2:]],
                         [event["event_id"] for event in events if event["event_id"] > middle])
        only = self.stream(f"?after=0&max_seconds=1&task_id={second}")
        self.assertEqual({json.loads(item["data"])["task_id"] for item in only[2:]}, {second})

    def test_pushes_new_events_as_they_commit(self):
        # With no periodic check inside the stream's lifetime, only the commit wake-up can deliver the task.
        self.start(stream_heartbeat_seconds=30, stream_check_seconds=60)
        received = {}
        reader = threading.Thread(target=lambda: received.update(frames=self.stream("?after=0&max_seconds=2")))
        reader.start()
        time.sleep(.4)
        task_id = self.submit("pushed")
        reader.join(5)
        kinds = [item.get("event") for item in received["frames"] if json.loads(item.get("data", "{}")).get("task_id") == task_id]
        self.assertEqual(kinds, ["task_accepted", "started", "started", "progress", "output", "completed"])

    def test_heartbeats_and_other_clients_events_stay_private(self):
        self.start(stream_heartbeat_seconds=.2)
        other = self.pair()
        self.submit("someone else's", headers=other)
        received = self.stream("?after=0&max_seconds=1")
        self.assertGreaterEqual(sum(1 for item in received if item.get(":") == "keepalive"), 2)
        self.assertEqual([item for item in received if "id" in item], [])

    def test_revoking_the_phone_ends_its_stream(self):
        self.start(stream_heartbeat_seconds=.2)
        done = {}

        def read():
            begun = time.monotonic()
            self.stream("?max_seconds=20")
            done["seconds"] = time.monotonic() - begun

        reader = threading.Thread(target=read)
        reader.start()
        time.sleep(.5)
        self.assertEqual(self.client.delete("/api/v1/clients/me", headers=self.headers).status_code, 204)
        reader.join(10)
        self.assertLess(done["seconds"], 5)
        self.stream(expected=401)

    def test_refuses_bad_requests_before_streaming(self):
        self.start()
        self.assertEqual(self.client.get("/api/v1/events/stream", headers={"X-Chintu-Protocol": "1"}).status_code, 401)
        self.stream("?after=999", expected=400)
        self.stream("?task_id=nope", expected=422)
        self.stream("?max_seconds=0", expected=422)
        self.stream("", {"Last-Event-ID": "-3"}, expected=422)


class StreamPartsTests(unittest.TestCase):
    def test_registry_lets_a_phones_newest_stream_win_and_bounds_the_total(self):
        registry = StreamRegistry(per_client=2, total=3)
        first, second = registry.open("phone"), registry.open("phone")
        third = registry.open("phone")
        self.assertTrue(first.evicted)
        self.assertFalse(second.evicted or third.evicted)
        registry.open("tablet")
        with self.assertRaises(ApiError) as refused:
            registry.open("laptop")
        self.assertEqual(refused.exception.status, 503)
        registry.close(second)
        registry.close(second)
        registry.open("laptop")

    def test_hub_wakes_waiters_from_other_threads_and_ignores_a_closed_loop(self):
        hub = EventHub()
        hub.notify()  # Unbound: nothing to wake, no error.

        async def scenario():
            hub.bind(asyncio.get_running_loop())
            begun = asyncio.get_running_loop().time()
            threading.Timer(.1, hub.notify).start()
            await hub.wait(5)
            return asyncio.get_running_loop().time() - begun

        loop = asyncio.new_event_loop()
        self.assertLess(loop.run_until_complete(scenario()), 2)
        loop.close()
        hub.notify()

    def test_store_notifies_after_commits_that_stored_events_only(self):
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(Settings(Path(temp) / "db", enable_worker=False), adapter=ScriptedAdapter())
            store, calls = app.state.store, []
            store.listeners.append(lambda: calls.append(1))
            store.create_enrollment()
            self.assertEqual(calls, [])
            with self.assertRaises(RuntimeError):
                with store.transaction() as db:
                    snapshot = {"task_id": uid(), "attempt_id": None, "task_version": 0, "state": "queued", "provider": "scripted"}
                    store._event(db, snapshot, "task_accepted", {"state": "queued"})
                    raise RuntimeError("roll back")
            self.assertEqual(calls, [])
            client_id = store.pair(store.create_enrollment(), "Phone")["client_id"]
            store.submit(client_id, {"submission_key": uid(), "project_id": "fixture", "provider": "scripted",
                                     "execution_profile": "inspect", "instruction": "hi"}, lambda body: None)
            self.assertEqual(calls, [1])

    def test_frames_are_single_line_data(self):
        self.assertEqual(frame(event="output", data={"text": "a\nb"}, event_id=7),
                         b'id: 7\nevent: output\ndata: {"text":"a\\nb"}\n\n')
        self.assertEqual(frame(comment="keepalive"), b": keepalive\n\n")


if __name__ == "__main__":
    unittest.main()
