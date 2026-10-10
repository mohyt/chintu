"""An in-process, text-only API demo using the no-effects scripted adapter."""

import tempfile
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from chintu_adapters import ScriptedAdapter
from chintu_gateway import Settings, create_app


def main():
    with tempfile.TemporaryDirectory() as temporary:
        app = create_app(
            Settings(database=Path(temporary) / "demo.sqlite"),
            adapter=ScriptedAdapter(),
        )
        with TestClient(app) as client:
            secret = app.state.store.create_enrollment()
            pair = client.post(
                "/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                json={"enrollment_secret": secret, "client_name": "local-demo"},
            )
            pair.raise_for_status()
            headers = {
                "X-Chintu-Protocol": "1",
                "Authorization": f"Bearer {pair.json()['token']}",
            }
            response = client.post(
                "/api/v1/tasks", headers=headers,
                json={
                    "submission_key": str(uuid.uuid4()),
                    "project_id": "fixture",
                    "provider": "scripted",
                    "instruction": "Summarize this project",
                    "execution_profile": "inspect",
                },
            )
            response.raise_for_status()
            task = response.json()["task"]
            print("Scripted fixture demo: no network listener, speech service, or real inference.")
            print(f"Text request accepted as task {task['task_id']}.")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                response = client.get(f"/api/v1/tasks/{task['task_id']}", headers=headers)
                response.raise_for_status()
                task = response.json()
                if task["state"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                    print(f"Recorded outcome: {task['state']}")
                    print(f"Text Android would speak: {task['result']['speech_text']}")
                    return
                time.sleep(0.01)
            raise RuntimeError("Demo did not produce a recorded outcome within 3 seconds.")


if __name__ == "__main__":
    main()
