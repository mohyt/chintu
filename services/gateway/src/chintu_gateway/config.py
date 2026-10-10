"""Strict local operator config; contains references/evidence, never credentials."""

import json
import re
from dataclasses import dataclass, fields
from pathlib import Path

from chintu_adapters import ClaudeAdapter, ClaudeAgentAdapter, CodexAdapter, RuntimeEvidence
from chintu_adapters.agent_sdk import DEFAULT_MODEL, PROFILE_POLICIES

from .app import Settings
from .auth_env import assert_auth_environment
from .contract import Contract, DEFAULT_CONTRACT


LOGIN_METHODS = {"subscription", "api_key"}
CLI_PROVIDERS = {"claude", "codex"}
AGENT_PROVIDER = "claude-agent"
PROVIDERS = CLI_PROVIDERS | {AGENT_PROVIDER}
CLI_PROFILES = frozenset({"inspect", "develop"})
PROJECT_PROFILES = frozenset(PROFILE_POLICIES)


def object_fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError("Operator config has missing or unknown fields")


def text(value, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value:
        raise ValueError("Operator config requires a nonempty, bounded text value")
    return value


def profiles(value, allowed=CLI_PROFILES):
    if (not isinstance(value, list) or not value or len(value) > len(allowed)
            or any(not isinstance(v, str) or v not in allowed for v in value)
            or len(set(value)) != len(value)):
        raise ValueError("Operator config has invalid execution profiles")
    return tuple(value)


def agent_runtime(runtime):
    """Validate the claude-agent block. The desktop profile must be listed explicitly to exist."""
    object_fields(runtime, ("enabled", "executable", "model", "profiles", "load_user_settings"),
                  ("max_turns", "timeout_seconds", "persona"))
    persona = runtime.get("persona")
    if persona is not None and (not isinstance(persona, str) or not 1 <= len(persona) <= 4000):
        raise ValueError("claude-agent persona must be a string of 1 to 4000 characters")
    if type(runtime["enabled"]) is not bool or type(runtime["load_user_settings"]) is not bool:
        raise ValueError("claude-agent enabled and load_user_settings must be booleans")
    text(runtime["executable"])
    text(runtime["model"], 100)
    profiles(runtime["profiles"], PROJECT_PROFILES)
    max_turns = runtime.get("max_turns")
    if max_turns is not None and (type(max_turns) is not int or not 1 <= max_turns <= 1000):
        raise ValueError("claude-agent max_turns must be an integer between 1 and 1000")
    timeout = runtime.get("timeout_seconds")
    if timeout is not None and (type(timeout) is not int or not 30 <= timeout <= 86400):
        raise ValueError("claude-agent timeout_seconds must be an integer between 30 and 86400")


def review_file(value, base):
    path = Path(text(value)).expanduser()
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file() or path.stat().st_size > 65536 or not path.read_bytes().strip():
        raise ValueError("Review record must be an existing, nonempty file of at most 64 KiB")
    return path


@dataclass(frozen=True)
class OperatorConfig:
    settings: Settings
    runtimes: dict
    directory: Path

    def selected(self, provider):
        """Construct only the explicitly selected adapter. Never performs inference/probes."""
        runtime = self.runtimes.get(provider)
        if provider not in PROVIDERS or runtime is None or runtime["enabled"] is not True:
            raise ValueError("Selected real provider is not explicitly enabled in local config")
        if provider == AGENT_PROVIDER:
            return self._selected_agent(runtime)
        if runtime["login_method"] not in LOGIN_METHODS or runtime["evidence"] is None:
            raise ValueError("Selected login method and reviewed runtime evidence are required")
        assert_auth_environment(provider)
        value = runtime["evidence"]
        record = review_file(value["record"], self.directory)
        evidence = RuntimeEvidence(**{**value, "execution_profiles": tuple(value["execution_profiles"]),
                                       "record": str(record)})
        cls = ClaudeAdapter if provider == "claude" else CodexAdapter
        adapter = cls(runtime["executable"], evidence=evidence)
        if not adapter.capabilities().available:
            raise ValueError("Selected runtime is unavailable; reviewed auth/protocol/confinement/cancel checks are required")
        settings = Settings(database=self.settings.database, projects=self.settings.projects, notes_dir=self.settings.notes_dir,
            contract_path=self.settings.contract_path, queue_limit=self.settings.queue_limit,
            runtime_evidence=evidence, login_method=runtime["login_method"])
        return settings, adapter

    def _selected_agent(self, runtime):
        """The SDK provider needs no CLI protocol evidence: an explicit enable and profile list suffice."""
        assert_auth_environment(AGENT_PROVIDER)
        extra = {key: runtime[key] for key in ("max_turns", "timeout_seconds", "persona") if runtime.get(key) is not None}
        from chintu_adapters.agent_sdk import DEFAULT_PERSONA
        notes_dir = self.settings.notes_dir or Path(self.settings.database).resolve().parent / "notes"
        extra["persona"] = (extra.get("persona") or DEFAULT_PERSONA) + (
            f" Voice notes the owner dictates from the phone are saved, without any processing, as Markdown files in {notes_dir} "
            "(newest by file name). When asked about their notes, read the relevant files there.")
        adapter = ClaudeAgentAdapter(runtime["executable"], model=runtime["model"],
                                     profiles=tuple(runtime["profiles"]),
                                     load_user_settings=runtime["load_user_settings"], **extra)
        caps = adapter.capabilities()
        if not caps.available:
            raise ValueError("Selected claude-agent runtime is unavailable: " + (caps.reason or "unknown reason"))
        return self.settings, adapter


def load_config(path, contract_path=DEFAULT_CONTRACT):
    path = Path(path).resolve()
    if path.stat().st_size > 65536:
        raise ValueError("Operator config exceeds 64 KiB")
    value = json.loads(path.read_text())
    object_fields(value, ("version", "database", "projects", "runtimes"), ("queue_limit", "notes_dir"))
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Operator config version must be 1")
    database = Path(text(value["database"])).expanduser()
    if not database.is_absolute():
        database = path.parent / database
    notes_dir = None
    if value.get("notes_dir") is not None:
        notes_dir = Path(text(value["notes_dir"], 1024)).expanduser()
        if not notes_dir.is_absolute():
            notes_dir = path.parent / notes_dir
        notes_dir = notes_dir.resolve()
    queue_limit = value.get("queue_limit", 10)
    if type(queue_limit) is not int or not 1 <= queue_limit <= 100:
        raise ValueError("queue_limit must be an integer between 1 and 100")
    if not isinstance(value["projects"], list) or len(value["projects"]) > 100:
        raise ValueError("Operator config projects must be an array of at most 100 entries")
    registered = {}
    contract = Contract(contract_path)
    for project in value["projects"]:
        object_fields(project, ("project_id", "display_name", "path", "execution_profiles"))
        public = {key: project[key] for key in ("project_id", "display_name", "execution_profiles")}
        contract.request("Project", public)
        profiles(project["execution_profiles"], PROJECT_PROFILES)
        workspace = Path(text(project["path"])).expanduser()
        if not workspace.is_absolute():
            raise ValueError("Registered workspace paths must be absolute")
        if project["project_id"] in registered:
            raise ValueError("Registered project IDs must be unique")
        registered[project["project_id"]] = {"display_name": project["display_name"],
            "path": workspace.resolve(), "execution_profiles": project["execution_profiles"]}
    runtimes = value["runtimes"]
    object_fields(runtimes, (), ("claude", "codex", AGENT_PROVIDER))
    evidence_fields = {f.name for f in fields(RuntimeEvidence)}
    for name, runtime in runtimes.items():
        if name == AGENT_PROVIDER:
            agent_runtime(runtime)
            continue
        object_fields(runtime, ("enabled", "executable", "login_method", "evidence"))
        if type(runtime["enabled"]) is not bool:
            raise ValueError("Runtime enabled must be a boolean")
        text(runtime["executable"])
        if runtime["login_method"] is not None and (not isinstance(runtime["login_method"], str)
                                                    or runtime["login_method"] not in LOGIN_METHODS):
            raise ValueError("Unknown login method")
        evidence = runtime["evidence"]
        if evidence is not None:
            object_fields(evidence, evidence_fields)
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", text(evidence["version"], 100)):
                raise ValueError("Evidence must pin an exact runtime version")
            profiles(evidence["execution_profiles"])
            text(evidence["record"])
            for key in evidence_fields - {"version", "execution_profiles", "record"}:
                if type(evidence[key]) is not bool:
                    raise ValueError("Evidence flags must be booleans")
    return OperatorConfig(Settings(database.resolve(), registered, Path(contract_path), queue_limit, notes_dir=notes_dir), runtimes, path.parent)
