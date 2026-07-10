

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ...const import DOMAIN
from ..core.state import get_adaptive_memory_state

LOGGER = logging.getLogger(__name__)

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.adaptive_memory"
_STORE_KEY = "adaptive_memory_store"
_SAVE_DELAY = 5
_TRACE_LIMIT = 100
_COOLDOWN_MINUTES = 5
_KNOWN_INCOMPATIBLE_TTL_HOURS = 1
_SEVERE_ERROR_SIGNATURES = {
    "content_parts_required",
    "permission_error",
    "messages_dispatch_rejected",
    "server_disconnected",
    "unauthorized",
}
_KNOWN_INCOMPATIBLE_SIGNATURES = {
    "permission_error",
    "messages_dispatch_rejected",
    "unauthorized",
}
_TRANSIENT_ERROR_SIGNATURES = {
    "server_disconnected",
    "transient_service_error",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _ensure_store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get(_STORE_KEY)
    if store is None:
        store = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        domain_data[_STORE_KEY] = store
    return store


def _normalize_state(raw_state: dict[str, Any] | None) -> dict[str, Any]:
    state = raw_state or {}
    agents = state.get("agents")
    traces = state.get("traces")
    return {
        "agents": agents if isinstance(agents, dict) else {},
        "traces": traces if isinstance(traces, list) else [],
    }


def _serialize_state(hass: HomeAssistant) -> dict[str, Any]:
    state = get_adaptive_memory_state(hass)
    return {
        "agents": dict(state.get("agents", {})),
        "traces": list(state.get("traces", []))[-_TRACE_LIMIT:],
    }


def _schedule_save(hass: HomeAssistant) -> None:
    store = hass.data.get(DOMAIN, {}).get(_STORE_KEY)
    if store is None:
        return
    store.async_delay_save(lambda: _serialize_state(hass), _SAVE_DELAY)


def _agent_entry(state: dict[str, Any], agent_id: str) -> dict[str, Any]:
    agents = state.setdefault("agents", {})
    entry = agents.get(agent_id)
    if not isinstance(entry, dict):
        entry = {
            "successes": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "last_error": None,
            "last_error_signature": None,
            "last_failure_at": None,
            "last_success_at": None,
            "cooldown_until": None,
        }
        agents[agent_id] = entry
    return entry


def _trim_traces(state: dict[str, Any]) -> None:
    traces = state.setdefault("traces", [])
    if len(traces) > _TRACE_LIMIT:
        del traces[:-_TRACE_LIMIT]


def _error_signature(error: str | None) -> str:
    if not error:
        return "unknown_error"

    lowered = error.lower()
    if "permission_error" in lowered:
        return "permission_error"
    if "content parts are required" in lowered:
        return "content_parts_required"
    if "server disconnected" in lowered:
        return "server_disconnected"
    if (
        "transient service error" in lowered
        or "timeout" in lowered
        or "timed out" in lowered
        or "connection" in lowered
        or "disconnected" in lowered
        or "reset by peer" in lowered
        or "broken pipe" in lowered
        or "temporarily unavailable" in lowered
        or "rate limit" in lowered
        or "429" in lowered
        or "502" in lowered
        or "503" in lowered
        or "504" in lowered
    ):
        return "transient_service_error"
    if "unauthorized" in lowered or "401" in lowered:
        return "unauthorized"
    if "/v1/messages dispatch" in lowered:
        return "messages_dispatch_rejected"
    if "tool_failure" in lowered:
        return "tool_failure"
    if "error_response" in lowered:
        return "error_response"

    head = lowered.split(":", 1)[0].strip()
    return head[:80] or "unknown_error"


def _in_cooldown(entry: dict[str, Any]) -> bool:
    cooldown_until = entry.get("cooldown_until")
    if not isinstance(cooldown_until, str):
        return False
    try:
        return datetime.fromisoformat(cooldown_until) > _utcnow()
    except ValueError:
        return False


def _score_agent(entry: dict[str, Any]) -> int:
    return (
        int(entry.get("successes", 0)) * 4
        - int(entry.get("failures", 0)) * 2
        - int(entry.get("consecutive_failures", 0)) * 5
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _entry_is_known_incompatible(entry: dict[str, Any]) -> bool:
    signature = entry.get("last_error_signature")
    if not (isinstance(signature, str) and signature in _KNOWN_INCOMPATIBLE_SIGNATURES):
        return False

    last_failure_at = _parse_iso(entry.get("last_failure_at"))
    if last_failure_at is None:
        return False

    last_success_at = _parse_iso(entry.get("last_success_at"))
    if last_success_at is not None and last_success_at >= last_failure_at:
        return False

    return last_failure_at >= (_utcnow() - timedelta(hours=_KNOWN_INCOMPATIBLE_TTL_HOURS))


async def async_setup_adaptive_memory(hass: HomeAssistant) -> None:

    store = _ensure_store(hass)
    stored = await store.async_load()
    runtime_state = get_adaptive_memory_state(hass)
    runtime_state.clear()
    runtime_state.update(_normalize_state(stored))


def prioritize_agents(hass: HomeAssistant, agent_ids: list[str]) -> list[str]:

    if len(agent_ids) <= 2:
        return list(agent_ids)

    state = get_adaptive_memory_state(hass)
    primary = agent_ids[0]
    rest = agent_ids[1:]

    def sort_key(agent_id: str) -> int:
        entry = state.get("agents", {}).get(agent_id, {})
        if not isinstance(entry, dict):
            return 0
        return -_score_agent(entry)

    sorted_rest = sorted(rest, key=sort_key)
    return [primary] + sorted_rest


def should_temporarily_skip_agent(hass: HomeAssistant, agent_id: str) -> bool:

    entry = get_adaptive_memory_state(hass).get("agents", {}).get(agent_id, {})
    if not isinstance(entry, dict):
        return False
    if entry.get("last_error_signature") in _TRANSIENT_ERROR_SIGNATURES:
        return False
    return _in_cooldown(entry)


def is_known_incompatible_agent(hass: HomeAssistant, agent_id: str) -> bool:

    entry = get_adaptive_memory_state(hass).get("agents", {}).get(agent_id, {})
    if not isinstance(entry, dict):
        return False
    return _entry_is_known_incompatible(entry)


async def async_record_agent_success(
    hass: HomeAssistant,
    agent_id: str,
    *,
    conversation_id: str | None = None,
) -> None:

    state = get_adaptive_memory_state(hass)
    entry = _agent_entry(state, agent_id)
    entry["successes"] = int(entry.get("successes", 0)) + 1
    entry["consecutive_failures"] = 0
    entry["cooldown_until"] = None
    entry["last_error"] = None
    entry["last_error_signature"] = None
    entry["last_success_at"] = _iso_now()
    state.setdefault("traces", []).append(
        {
            "timestamp": _iso_now(),
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "outcome": "success",
        }
    )
    _trim_traces(state)
    _schedule_save(hass)


async def async_record_agent_failure(
    hass: HomeAssistant,
    agent_id: str,
    *,
    error: str | None,
    conversation_id: str | None = None,
    stage: str = "response",
) -> None:

    state = get_adaptive_memory_state(hass)
    entry = _agent_entry(state, agent_id)
    signature = _error_signature(error)

    entry["failures"] = int(entry.get("failures", 0)) + 1
    entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0)) + 1
    entry["last_error"] = error
    entry["last_error_signature"] = signature
    entry["last_failure_at"] = _iso_now()

    if (
        signature not in _TRANSIENT_ERROR_SIGNATURES
        and (
            signature in _SEVERE_ERROR_SIGNATURES
            or int(entry["consecutive_failures"]) >= 2
        )
    ):
        entry["cooldown_until"] = (_utcnow() + timedelta(minutes=_COOLDOWN_MINUTES)).isoformat()

    state.setdefault("traces", []).append(
        {
            "timestamp": _iso_now(),
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "outcome": "failure",
            "stage": stage,
            "error_signature": signature,
        }
    )
    _trim_traces(state)
    _schedule_save(hass)


def summarize_agent_learning(hass: HomeAssistant, agent_id: str) -> dict[str, Any]:

    entry = get_adaptive_memory_state(hass).get("agents", {}).get(agent_id, {})
    if not isinstance(entry, dict):
        return {}
    return dict(entry)
