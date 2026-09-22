"""Canonical Agent Event Schema — normalizes events from different agent runtimes.

All downstream components (State Engine, triggers, Jev) consume canonical events,
never raw runtime-specific events. Tool names are mapped to capabilities so that
`bash`, `execute`, `shell`, and `terminal` all resolve to `shell_execution`.
"""

from dataclasses import dataclass, field
from typing import Optional


class Capability(str, Enum := __import__("enum").Enum):
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_EDIT = "file_edit"
    SHELL_EXECUTION = "shell_execution"
    SEARCH = "search"
    UNKNOWN = "unknown"


TOOL_NAME_TO_CAPABILITY = {
    # Pi
    "read": Capability.FILE_READ,
    "write": Capability.FILE_WRITE,
    "edit": Capability.FILE_EDIT,
    "bash": Capability.SHELL_EXECUTION,
    # Codex
    "shell": Capability.SHELL_EXECUTION,
    "terminal": Capability.SHELL_EXECUTION,
    # Custom agents
    "execute": Capability.SHELL_EXECUTION,
    "run": Capability.SHELL_EXECUTION,
    "cat": Capability.FILE_READ,
    "view": Capability.FILE_READ,
    "create": Capability.FILE_WRITE,
    "modify": Capability.FILE_EDIT,
    "patch": Capability.FILE_EDIT,
    "grep": Capability.SEARCH,
    "find": Capability.SEARCH,
}


@dataclass
class CanonicalEvent:
    type: str = ""
    tool_name: str = ""
    capability: str = ""
    args: dict = field(default_factory=dict)
    is_error: bool = False
    usage: dict = field(default_factory=dict)
    raw_event: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "tool_name": self.tool_name,
            "capability": self.capability,
            "args": self.args,
            "is_error": self.is_error,
            "usage": self.usage,
        }


def resolve_capability(tool_name: str) -> Capability:
    return TOOL_NAME_TO_CAPABILITY.get(tool_name, Capability.UNKNOWN)


def normalize_event(raw: dict, source: str = "pi") -> CanonicalEvent:
    """Normalize a raw agent event into a CanonicalEvent.

    source: "pi", "codex", or "custom" — controls which raw schema to parse.
    """
    if source == "pi":
        return _normalize_pi(raw)
    elif source == "codex":
        return _normalize_codex(raw)
    elif source == "custom":
        return _normalize_custom(raw)
    else:
        return _normalize_pi(raw)


def _normalize_pi(raw: dict) -> CanonicalEvent:
    et = raw.get("type", "")
    event = CanonicalEvent(type=et, raw_event=raw)

    if et == "tool_execution_end":
        event.tool_name = raw.get("toolName", "")
        event.capability = resolve_capability(event.tool_name).value
        event.args = raw.get("args", {})
        event.is_error = raw.get("isError", False)

    elif et == "tool_execution_start":
        event.tool_name = raw.get("toolName", "")
        event.capability = resolve_capability(event.tool_name).value
        event.args = raw.get("args", {})

    elif et == "message_update":
        event.usage = raw.get("usage", {})

    return event


def _normalize_codex(raw: dict) -> CanonicalEvent:
    et = raw.get("event_type", "")
    event = CanonicalEvent(raw_event=raw)

    if et == "tool_complete":
        event.type = "tool_execution_end"
        event.tool_name = raw.get("tool", "")
        event.capability = resolve_capability(event.tool_name).value
        event.args = raw.get("input", {})
        event.is_error = raw.get("status", "") == "error"

    elif et == "run_started":
        event.type = "agent_start"
    elif et == "run_finished":
        event.type = "agent_end"
    elif et == "retry":
        event.type = "auto_retry_start"
    elif et == "llm_usage":
        event.type = "message_update"
        event.usage = {"totalTokens": raw.get("tokens", 0), "cost": {"total": raw.get("cost", 0)}}
    else:
        event.type = et

    return event


def _normalize_custom(raw: dict) -> CanonicalEvent:
    kind = raw.get("kind", "")
    event = CanonicalEvent(raw_event=raw)

    if kind == "tool_result":
        event.type = "tool_execution_end"
        event.tool_name = raw.get("name", "")
        event.capability = resolve_capability(event.tool_name).value
        event.args = raw.get("params", {})
        event.is_error = not raw.get("ok", True)

    elif kind == "session_begin":
        event.type = "agent_start"
    elif kind == "session_end":
        event.type = "agent_end"
    elif kind == "retry_attempt":
        event.type = "auto_retry_start"
    elif kind == "token_count":
        event.type = "message_update"
        event.usage = {"totalTokens": raw.get("count", 0), "cost": {"total": raw.get("cost", 0)}}
    else:
        event.type = kind

    return event
