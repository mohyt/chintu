"""Chintu's internal adapter protocol, with no provider execution on import."""

from .types import Adapter, AdapterCapabilities, AdapterEvent, TaskCommand
from .scripted import ScriptedAdapter
from .local import ClaudeAdapter, CodexAdapter, RuntimeEvidence
from .agent_sdk import ClaudeAgentAdapter

__all__ = ["Adapter", "AdapterCapabilities", "AdapterEvent", "TaskCommand", "ScriptedAdapter",
           "ClaudeAdapter", "CodexAdapter", "RuntimeEvidence", "ClaudeAgentAdapter"]
