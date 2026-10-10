"""Read local Tailscale state only; never scan or contact discovered computers."""

import asyncio
import json
import os
import re
import shutil
import signal
import time


DNS_PATTERN = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net"
SUPPORTED_OS = {"linux": "linux", "macos": "macos", "windows": "windows"}


def unavailable(reason):
    return {"status": "unavailable", "reason": reason, "systems": []}


def canonical_dns(value):
    if not isinstance(value, str):
        return None
    host = value.lower()
    if host.endswith("."):
        host = host[:-1]
    if len(host) > 253 or re.fullmatch(DNS_PATTERN, host, flags=re.ASCII) is None:
        return None
    return host


def computer(record, is_self):
    if not isinstance(record, dict) or not isinstance(record.get("OS"), str):
        return None
    os_name = SUPPORTED_OS.get(record["OS"].lower())
    host = canonical_dns(record.get("DNSName"))
    online = record.get("Online")
    if os_name is None or host is None or type(online) is not bool:
        return None
    name = record.get("HostName")
    if (not isinstance(name, str) or not name.strip() or len(name) > 120 or not any(c.isalnum() for c in name)
            or any(not (c.isalnum() or c in " ._-'()") for c in name)):
        name = host.split(".", 1)[0]
    name = " ".join(name.replace("-", " ").replace("_", " ").split())[:120]
    return {"system_id": host, "name": name, "os": os_name, "online": online,
            "gateway_url": "https://" + host, "is_self": is_self}


def parse_status(value, limit=100):
    if not isinstance(value, dict) or value.get("BackendState") != "Running":
        return unavailable("Tailscale is not connected.")
    peers = value.get("Peer")
    if not isinstance(value.get("Self"), dict) or (peers is not None and not isinstance(peers, dict)):
        return unavailable("Tailscale returned unsupported status data.")
    found = {}
    # Self wins duplicate DNS identities; names/OS/online always remain observed data.
    for record, is_self in [(value["Self"], True), *((peer, False) for peer in (peers or {}).values())]:
        item = computer(record, is_self)
        if item is not None:
            found.setdefault(item["system_id"], item)
    systems = sorted(found.values(), key=lambda item: (not item["is_self"], not item["online"],
                                                      item["name"].casefold(), item["system_id"]))[:limit]
    return {"status": "available", "reason": None, "systems": systems}


class TailscaleDiscovery:
    def __init__(self, executable="tailscale", *, timeout_seconds=3, max_output_bytes=512 * 1024,
                 cache_seconds=5):
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.cache_seconds = cache_seconds
        self._lock = asyncio.Lock()
        self._cached = None
        self._cached_at = 0

    async def discover(self):
        # One bounded local probe at a time; quick refreshes reuse a short-lived snapshot.
        async with self._lock:
            if self._cached is not None and time.monotonic() - self._cached_at < self.cache_seconds:
                return self._cached
            answer = await self._probe()
            self._cached, self._cached_at = answer, time.monotonic()
            return answer

    async def _stop(self, process):
        try:
            if os.name == "posix":
                # Stop descendants too, including a child retaining stdout after
                # its leader has exited. The probe has its own process group.
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        # SIGKILL applies to the dedicated local probe group, never another job.
        async with asyncio.timeout(1):
            # Drain discarded bytes so PIPE backpressure cannot block wait/reaping.
            while await process.stdout.read(8192):
                pass
            await process.wait()

    async def _probe(self):
        executable = shutil.which(self.executable)
        if executable is None:
            return unavailable("Tailscale CLI is unavailable on this gateway.")
        process = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                process = await asyncio.create_subprocess_exec(
                    executable, "status", "--json", stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=os.name == "posix",
                )
                output = bytearray()
                while chunk := await process.stdout.read(8192):
                    if len(output) + len(chunk) > self.max_output_bytes:
                        return unavailable("Tailscale status exceeded the discovery limit.")
                    output.extend(chunk)
                await process.wait()
                if process.returncode != 0:
                    return unavailable("Tailscale status could not be read.")
                return parse_status(json.loads(output))
        except (ValueError, UnicodeError, RecursionError):
            return unavailable("Tailscale returned unsupported status data.")
        except (TimeoutError, OSError):
            return unavailable("Tailscale discovery is temporarily unavailable.")
        finally:
            if process is not None:
                try:
                    await self._stop(process)
                except (OSError, TimeoutError):
                    # No shell output, private paths or daemon diagnostics are exported.
                    pass
