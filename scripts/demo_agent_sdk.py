"""Invoke the Claude Agent SDK adapter directly for one cheap inspect-profile instruction.

This performs real inference with the owner's Claude Code login. It prints the
normalized adapter events, never credentials. Usage from the repository root:

    .venv/bin/python scripts/demo_agent_sdk.py [instruction]
"""

import asyncio
import json
from pathlib import Path
import sys

from chintu_adapters import ClaudeAgentAdapter, TaskCommand


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INSTRUCTION = "List the top-level directories in this repo in one sentence"


async def main() -> int:
    instruction = " ".join(sys.argv[1:]) or DEFAULT_INSTRUCTION
    config = json.loads((ROOT / "var/gateway.local.json").read_text())["runtimes"]["claude-agent"]
    adapter = ClaudeAgentAdapter(config["executable"], model=config["model"], profiles=tuple(config["profiles"]),
                                 load_user_settings=config["load_user_settings"], timeout_seconds=300)
    caps = adapter.capabilities()
    print("capabilities:", json.dumps(caps.__dict__))
    if not caps.available:
        return 2
    command = TaskCommand("demo-task", "demo-attempt", str(ROOT), instruction, "inspect")
    outcome = "interrupted"
    async for event in adapter.run(command):
        outcome = event.kind
        print(json.dumps({"kind": event.kind, "session": event.provider_session_id, "payload": event.payload}))
    return 0 if outcome == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
