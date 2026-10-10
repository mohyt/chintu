import argparse
import fcntl
import json
import os
from pathlib import Path
from uuid import UUID

from .app import Settings, create_app
from .contract import DEFAULT_CONTRACT
from .config import load_config
from .errors import ApiError
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Private Chintu text gateway")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path)
    source.add_argument("--config", type=Path, help="Reviewed local operator JSON configuration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    enrollment = subparsers.add_parser("enroll", help="Print one local, expiring enrollment secret")
    enrollment.add_argument("--ttl", type=int, default=300)
    serve = subparsers.add_parser("serve", help="Bind to loopback; use Tailscale Serve for private HTTPS")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    serve.add_argument("--scripted", action="store_true", help="Explicitly enable the no-I/O scripted fixture")
    serve.add_argument("--provider", choices=("claude", "codex", "claude-agent"),
                       help="Explicitly select one enabled real provider (claude-agent uses the Claude Agent SDK)")
    check = subparsers.add_parser("check-config", help="Validate config without provider probes, login checks or inference")
    check.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    reconcile = subparsers.add_parser("reconcile", help="Offline: record externally confirmed stop and clear quarantine")
    reconcile.add_argument("--task-id", required=True, type=UUID)
    reconcile.add_argument("--confirmed-stopped", required=True, action="store_true")
    reconcile.add_argument("--record", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = load_config(args.config, getattr(args, "contract", DEFAULT_CONTRACT)) if args.config else None
        settings = config.settings if config else Settings(args.database, contract_path=getattr(args, "contract", DEFAULT_CONTRACT))
        if args.command == "check-config":
            if config is None:
                raise ValueError("check-config requires --config")
            print(json.dumps({"config_valid": True, "enabled_runtimes": [name for name, value in config.runtimes.items() if value["enabled"]],
                "projects": list(settings.projects), "provider_selected": False,
                "runtime_verified": False, "inference_performed": False}))
            return
        if args.command == "reconcile":
            if not settings.database.is_file():
                raise ValueError("Reconciliation requires an existing gateway database")
            store = Store(settings.database)
            with open(str(store.path) + ".worker.lock", "a+") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("Stop the gateway before recording local reconciliation") from None
                snapshot = store.reconcile(str(args.task_id), args.record)
            print(json.dumps({"task_id": snapshot["task_id"], "state": snapshot["state"],
                              "workspace_quarantine_cleared": True, "task_relaunched": False}))
            return
        if args.command == "serve" and args.provider:
            if args.scripted or config is None:
                raise ValueError("--provider requires --config and cannot be combined with --scripted")
            settings, adapter = config.selected(args.provider)
        else:
            from chintu_adapters import ScriptedAdapter
            adapter = ScriptedAdapter() if getattr(args, "scripted", False) else None
    except (ValueError, OSError, ApiError) as error:
        # Validation exceptions must not dump configuration contents or credentials.
        parser.error(str(error) if isinstance(error, (ValueError, ApiError)) else "Could not access local operator configuration or record")
    if args.command == "enroll":
        print(Store(settings.database).create_enrollment(args.ttl))
        return
    import uvicorn
    try:
        app = create_app(settings, adapter=adapter)
    except (ValueError, OSError) as error:
        parser.error(str(error) if isinstance(error, ValueError) else "Could not access local workspace or evidence record")
    import logging
    # Operator log: task lifecycle, provider progress and pairing go to stdout (see var/gateway.log under launchd).
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("chintu").info("Chintu gateway starting: provider=%s port=%s", getattr(adapter, "provider", "none"), args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
