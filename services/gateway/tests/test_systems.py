import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chintu_gateway import Settings, create_app
from chintu_gateway.contract import Contract
from chintu_gateway.systems import TailscaleDiscovery, canonical_dns, parse_status
from starlette.testclient import TestClient


def peer(host="cobra-ubuntu.example.ts.net.", name="Cobra-Ubuntu", os_name="linux", online=True):
    return {"DNSName": host, "HostName": name, "OS": os_name, "Online": online,
            "TailscaleIPs": ["100.100.0.8"], "PublicKey": "PRIVATE_PEER_KEY", "UserID": 123}


def status():
    return {"BackendState": "Running", "Self": peer("my-mac.example.ts.net.", "My Mac", "macOS"),
            "Peer": {"key1": peer(), "key2": peer("old-windows.example.ts.net.", "Old Windows", "windows", False),
                     "phone": peer("phone.example.ts.net.", "Phone", "android")},
            "AuthURL": "https://login.example/PRIVATE_TOKEN", "User": {"123": {"LoginName": "PRIVATE_LOGIN"}}}


class ParseSystemsTests(unittest.TestCase):
    def setUp(self):
        self.contract = Contract()

    def test_self_peers_offline_and_privacy(self):
        answer = parse_status(status())
        self.contract.validate("Systems", answer)
        self.assertEqual(answer["status"], "available")
        self.assertEqual(len(answer["systems"]), 3)
        own, cobra, windows = answer["systems"]
        self.assertTrue(own["is_self"])
        self.assertEqual(own["os"], "macos")
        self.assertEqual(cobra, {"system_id": "cobra-ubuntu.example.ts.net", "name": "Cobra Ubuntu", "os": "linux",
                                 "online": True, "gateway_url": "https://cobra-ubuntu.example.ts.net", "is_self": False})
        self.assertFalse(windows["online"])
        self.assertNotIn("PRIVATE", json.dumps(answer))
        self.assertNotIn("100.100", json.dumps(answer))

    def test_safe_https_dns_candidates_and_no_url_interpretation(self):
        for value in ("https://safe.example.ts.net", "safe.example.ts.net:443", "safe.example.ts.net/path",
                      "user@safe.example.ts.net", "safe.example.ts.net?secret=x", "safe.example.ts.net.evil.com",
                      "100.100.0.8", "evil.com", "ts.net", "-bad.example.ts.net", "a..example.ts.net",
                      "safe.example.ts.net\n", "safe.example.ts.net..", "ｓafe.example.ts.net", "a" * 64 + ".example.ts.net"):
            with self.subTest(value=value):
                self.assertIsNone(canonical_dns(value))
                data = {"BackendState": "Running", "Self": peer(value), "Peer": {}}
                answer = parse_status(data)
                self.assertEqual(answer["systems"], [])
                self.contract.validate("Systems", answer)
        self.assertEqual(canonical_dns("COBRA.Example.TS.NET."), "cobra.example.ts.net")

    def test_names_are_bounded_non_unique_display_data_and_bad_peer_skipped(self):
        data = status()
        data["Peer"].update({"duplicate": peer("MY-MAC.EXAMPLE.TS.NET", "Impersonated alias"),
                             "broken": "not an object", "invalid-online": peer("bad.example.ts.net", online="true"),
                             "controls": peer("control.example.ts.net", "Cobra\nDO SOMETHING"),
                             "separators": peer("separators.example.ts.net", "---___"),
                             "huge-name": peer("long.example.ts.net", "x" * 121)})
        answer = parse_status(data)
        self.contract.validate("Systems", answer)
        self.assertEqual(len([s for s in answer["systems"] if s["system_id"] == "my-mac.example.ts.net"]), 1)
        self.assertEqual(answer["systems"][0]["name"], "My Mac")
        self.assertTrue(answer["systems"][0]["is_self"])
        self.assertEqual(next(s["name"] for s in answer["systems"] if s["system_id"].startswith("control.")), "control")
        self.assertEqual(next(s["name"] for s in answer["systems"] if s["system_id"].startswith("separators.")), "separators")
        self.assertFalse(any(s["system_id"].startswith("bad.") for s in answer["systems"]))

    def test_available_empty_differs_from_disconnected_or_unsupported(self):
        empty = parse_status({"BackendState": "Running", "Self": peer(os_name="android"), "Peer": None})
        self.assertEqual(empty, {"status": "available", "reason": None, "systems": []})
        for value in ({"BackendState": "NeedsLogin"}, {"BackendState": "Stopped"}, {}, None,
                      {"BackendState": "Running", "Self": None},
                      {"BackendState": "Running", "Self": peer(), "Peer": []}):
            answer = parse_status(value)
            self.assertEqual(answer["status"], "unavailable")
            self.assertTrue(answer["reason"])
            self.assertEqual(answer["systems"], [])
            self.contract.validate("Systems", answer)

    def test_bounded_hundred_computers(self):
        data = status()
        data["Peer"] = {str(i): peer(f"computer-{i}.example.ts.net", f"Computer {i}") for i in range(150)}
        answer = parse_status(data)
        self.assertEqual(len(answer["systems"]), 100)
        self.assertTrue(answer["systems"][0]["is_self"])
        self.contract.validate("Systems", answer)


FIXTURE = '''#!/usr/bin/env python3
import json, os, pathlib, sys, time
root=pathlib.Path(__file__).parent
if sys.argv[1:] != ["status", "--json"]:
    sys.exit(4)
(root/"probe.pid").write_text(str(os.getpid()))
mode=(root/"mode").read_text()
if mode == "hang":
    while True: time.sleep(.1)
if mode == "flood":
    while True: os.write(1,b"x"*8192)
if mode == "failure":
    print("PRIVATE_STDOUT")
    print("PRIVATE_STDERR",file=sys.stderr)
    sys.exit(1)
if mode == "malformed":
    print("PRIVATE_MALFORMED")
elif mode == "invalid-utf8":
    os.write(1,b"\\xff")
else:
    print((root/"status.json").read_text())
'''


class ProbeSystemsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executable = self.root / "tailscale"
        self.executable.write_text(FIXTURE)
        self.executable.chmod(0o700)
        (self.root / "status.json").write_text(json.dumps(status()))
        (self.root / "mode").write_text("ok")
        self.contract = Contract()

    def discovery(self, **kwargs):
        return TailscaleDiscovery(str(self.executable), cache_seconds=0, **kwargs)

    async def test_exact_argv_local_cli_and_schema(self):
        answer = await self.discovery().discover()
        self.assertEqual(answer["status"], "available")
        self.contract.validate("Systems", answer)
        self.assertTrue((self.root / "probe.pid").exists())

    async def test_absent_cli_and_failed_or_bad_json_do_not_leak_diagnostics(self):
        answer = await TailscaleDiscovery(str(self.root / "missing")).discover()
        self.assertEqual(answer["status"], "unavailable")
        for mode in ("failure", "malformed", "invalid-utf8"):
            (self.root / "mode").write_text(mode)
            answer = await self.discovery().discover()
            self.assertEqual(answer["status"], "unavailable")
            self.assertNotIn("PRIVATE", json.dumps(answer))
            self.contract.validate("Systems", answer)

    async def test_timeout_and_output_limit_stop_and_reap_probe(self):
        for mode in ("hang", "flood"):
            (self.root / "mode").write_text(mode)
            processes = []
            original = asyncio.create_subprocess_exec

            async def capture(*args, **kwargs):
                process = await original(*args, **kwargs)
                processes.append(process)
                return process

            with patch("asyncio.create_subprocess_exec", capture):
                answer = await self.discovery(timeout_seconds=.2, max_output_bytes=16384).discover()
            self.assertEqual(answer["status"], "unavailable")
            self.contract.validate("Systems", answer)
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].returncode)

    async def test_concurrent_refresh_uses_one_bounded_cached_probe(self):
        discovery = TailscaleDiscovery(str(self.executable))
        original = asyncio.create_subprocess_exec
        with patch("asyncio.create_subprocess_exec", wraps=original) as spawn:
            answers = await asyncio.gather(*(discovery.discover() for _ in range(5)))
        self.assertEqual(spawn.call_count, 1)
        self.assertTrue(all(answer == answers[0] for answer in answers))


class AuthenticatedSystemsTests(unittest.TestCase):
    def test_auth_protocol_revocation_and_response_contract(self):
        class Discovery:
            calls = 0

            async def discover(self):
                self.calls += 1
                return parse_status(status())

        discovery = Discovery()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Settings(Path(directory) / "db", enable_worker=False), systems_discovery=discovery)
            with TestClient(app) as client:
                for headers, code in (({}, 426), ({"X-Chintu-Protocol": "1"}, 401)):
                    response = client.get("/api/v1/systems", headers=headers)
                    self.assertEqual(response.status_code, code)
                    app.state.contract.validate("Error", response.json())
                self.assertEqual(discovery.calls, 0)
                secret = app.state.store.create_enrollment()
                paired = client.post("/api/v1/pair", headers={"X-Chintu-Protocol": "1"},
                    json={"enrollment_secret": secret, "client_name": "Systems fixture"}).json()
                headers = {"X-Chintu-Protocol": "1", "Authorization": "Bearer " + paired["token"]}
                response = client.get("/api/v1/systems", headers=headers)
                self.assertEqual(response.status_code, 200)
                app.state.contract.validate("Systems", response.json())
                self.assertEqual(discovery.calls, 1)
                client.delete("/api/v1/clients/me", headers=headers)
                self.assertEqual(client.get("/api/v1/systems", headers=headers).status_code, 401)
                self.assertEqual(discovery.calls, 1)


if __name__ == "__main__":
    unittest.main()
