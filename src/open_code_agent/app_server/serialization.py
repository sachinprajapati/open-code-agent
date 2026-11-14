"""Explicit, secret-safe mappings from application objects to RPC payloads."""

from __future__ import annotations

import json
from typing import cast

from open_code_agent.agent.events import (
    ModelEventReceived,
    ModelStepStarted,
    RunCompleted,
    RunFailed,
    RunLimitReached,
    RunStarted,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from open_code_agent.protocol import ReasoningDelta, TextDelta, ToolCallCompleted
from open_code_agent.protocol.common import JSONObject
from open_code_agent.providers import OpenAICompatibleProfile


def safe_profile(
    profile: OpenAICompatibleProfile, *, is_default: bool = False
) -> JSONObject:
    """Expose configuration identity while never returning secret references."""
    return {
        "profile_id": profile.profile_id,
        "display_name": profile.display_name,
        "base_url": profile.base_url,
        "default_model": profile.default_model,
        "authentication_configured": profile.api_key_secret_ref is not None,
        "is_default": is_default,
    }


def bounded(value: object, maximum: int) -> JSONObject:
    """Return a JSON-object summary bounded by encoded byte size."""
    if isinstance(value, dict):
        candidate = cast(JSONObject, _redact(cast(JSONObject, value)))
    else:
        candidate = {"summary": "result received"}
    encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= maximum:
        return candidate
    return {"summary": "result omitted because it exceeded the event limit"}


def _redact(value: object) -> object:
    """Remove common credential-shaped fields from otherwise safe tool output."""
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if any(
                    term in str(key).lower()
                    for term in ("authorization", "api_key", "token", "secret")
                )
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _bounded_text(text: str, maximum: int) -> JSONObject:
    """Bound a provider text delta without changing the ordering of deltas."""
    encoded = text.encode("utf-8")
    budget = max(1, maximum - 128)
    if len(encoded) <= budget:
        return {"text": text}
    return {
        "text": encoded[:budget].decode("utf-8", errors="ignore"),
        "truncated": True,
    }


def event_payload(event: object, maximum: int) -> tuple[str, JSONObject] | None:
    """Map only known public events; hidden reasoning is intentionally omitted."""
    payload: JSONObject
    if isinstance(event, RunStarted):
        return "run_started", {}
    if isinstance(event, ModelStepStarted):
        return "model_step_started", {"step": event.step or 1}
    if isinstance(event, ModelEventReceived):
        model_event = event.model_event
        if isinstance(model_event, TextDelta):
            return "assistant_text_delta", _bounded_text(model_event.text, maximum)
        if isinstance(model_event, ReasoningDelta):
            return "reasoning_activity", {}
        if isinstance(model_event, ToolCallCompleted):
            event_type = {
                "propose_patch": "patch_proposal_available",
                "run_command": "command_proposal_available",
            }.get(model_event.tool_call.name, "tool_proposal")
            return event_type, {
                "tool_name": model_event.tool_call.name,
                "tool_call_id": model_event.tool_call.id,
            }
        return None
    if isinstance(event, ToolExecutionStarted):
        return "tool_execution_started", {
            "step": event.step or 1,
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
        }
    if isinstance(event, ToolExecutionCompleted):
        payload = {
            "step": event.step or 1,
            "tool_name": event.tool_name,
            "tool_call_id": event.tool_call_id,
            "is_error": event.result.is_error,
            "result": bounded(event.result.content, maximum),
        }
        return "tool_execution_completed", payload
    if isinstance(event, RunCompleted):
        return "run_completed", {
            "usage": event.result.usage.model_dump(mode="json"),
            "model_steps": event.result.model_steps,
            "tool_calls": event.result.total_tool_calls,
        }
    if isinstance(event, RunFailed):
        return "run_failed", {
            "message": "Agent run failed.",
            "model_steps": event.result.model_steps,
        }
    if isinstance(event, RunLimitReached):
        return "run_limit_reached", {
            "reason": event.result.limit_reason or "Agent limit reached.",
            "model_steps": event.result.model_steps,
        }
    return None
