from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..utils.data_path import get_data_dir

LOGGER = logging.getLogger(__name__)

STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"
_VALID_STATES = frozenset({STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED})

_INTERNAL_SLUGS: frozenset[str] = frozenset({
    "homeassistant_runtime_guide",
})


def _usage_path() -> Path:
    return get_data_dir() / "skill_usage.json"


def _archive_dir() -> Path:
    return get_data_dir() / "skills" / ".archive"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_usage() -> dict[str, Any]:
    path = _usage_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError) as err:
        LOGGER.debug("Failed to read skill_usage.json: %s", err)
    return {}


def _save_usage(data: dict[str, Any]) -> None:
    path = _usage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=".skill_usage_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as err:
        LOGGER.debug("Failed to save skill_usage.json: %s", err, exc_info=True)


def _ensure_entry(data: dict[str, Any], slug: str) -> dict[str, Any]:
    if slug not in data:
        data[slug] = {
            "created_at": _now_iso(),
            "state": STATE_ACTIVE,
            "pinned": False,
            "use_count": 0,
            "view_count": 0,
            "patch_count": 0,
            "last_activity_at": None,
        }
    return data[slug]


def bump_use(slug: str) -> None:
    data = _load_usage()
    entry = _ensure_entry(data, slug)
    entry["use_count"] = int(entry.get("use_count", 0)) + 1
    entry["last_activity_at"] = _now_iso()
    _save_usage(data)


async def async_bump_use(hass: HomeAssistant, slug: str) -> None:
    await hass.async_add_executor_job(bump_use, slug)


def bump_view(slug: str) -> None:
    data = _load_usage()
    entry = _ensure_entry(data, slug)
    entry["view_count"] = int(entry.get("view_count", 0)) + 1
    entry["last_activity_at"] = _now_iso()
    _save_usage(data)


async def async_bump_view(hass: HomeAssistant, slug: str) -> None:
    await hass.async_add_executor_job(bump_view, slug)


def bump_patch(slug: str) -> None:
    data = _load_usage()
    entry = _ensure_entry(data, slug)
    entry["patch_count"] = int(entry.get("patch_count", 0)) + 1
    entry["last_activity_at"] = _now_iso()
    _save_usage(data)


async def async_bump_patch(hass: HomeAssistant, slug: str) -> None:
    await hass.async_add_executor_job(bump_patch, slug)


def set_state(slug: str, state: str) -> None:
    if state not in _VALID_STATES:
        raise ValueError(f"Invalid state: {state!r}")
    data = _load_usage()
    entry = _ensure_entry(data, slug)
    entry["state"] = state
    _save_usage(data)


def set_pinned(slug: str, pinned: bool) -> None:
    data = _load_usage()
    entry = _ensure_entry(data, slug)
    entry["pinned"] = bool(pinned)
    _save_usage(data)


async def async_set_pinned(hass: HomeAssistant, slug: str, pinned: bool) -> None:
    await hass.async_add_executor_job(set_pinned, slug, pinned)


def get_usage(slug: str) -> dict[str, Any]:
    data = _load_usage()
    return dict(data.get(slug, {}))


def is_agent_created(slug: str) -> bool:
    return slug not in _INTERNAL_SLUGS


def archive_skill(slug: str) -> tuple[bool, str]:
    from ..storage.skill_store import _skills_dir
    import shutil

    src = _skills_dir() / slug
    if not src.is_dir():
        flat = _skills_dir() / f"{slug}.md"
        if flat.is_file():
            src = flat
        else:
            return False, f"Skill not found: {slug}"

    archive = _archive_dir()
    archive.mkdir(parents=True, exist_ok=True)
    dst = archive / slug
    if dst.exists():
        return False, f"Already archived: {slug}"

    try:
        shutil.move(str(src), str(dst))
    except OSError as err:
        return False, f"Archive failed: {err}"

    set_state(slug, STATE_ARCHIVED)
    return True, f"Archived: {slug}"


def restore_skill(slug: str) -> tuple[bool, str]:
    from ..storage.skill_store import _skills_dir

    archive = _archive_dir()
    import shutil

    for candidate in sorted(archive.rglob(slug)):
        if candidate.is_dir() or candidate.is_file():
            dst = _skills_dir() / slug
            if dst.exists():
                return False, f"Skill already exists at {dst}"
            try:
                shutil.move(str(candidate), str(dst))
            except OSError as err:
                return False, f"Restore failed: {err}"
            set_state(slug, STATE_ACTIVE)
            return True, f"Restored: {slug}"
    return False, f"Not found in archive: {slug}"


def agent_created_report() -> list[dict[str, Any]]:
    from ..storage.skill_store import _iter_skill_entries

    data = _load_usage()
    rows: list[dict[str, Any]] = []
    for slug, path in _iter_skill_entries():
        if not is_agent_created(slug):
            continue
        entry = data.get(slug, {})
        rows.append({
            "name": slug,
            "state": entry.get("state", STATE_ACTIVE),
            "pinned": bool(entry.get("pinned", False)),
            "use_count": int(entry.get("use_count", 0)),
            "view_count": int(entry.get("view_count", 0)),
            "patch_count": int(entry.get("patch_count", 0)),
            "activity_count": (
                int(entry.get("use_count", 0))
                + int(entry.get("view_count", 0))
                + int(entry.get("patch_count", 0))
            ),
            "last_activity_at": entry.get("last_activity_at"),
            "created_at": entry.get("created_at"),
        })
    return rows


async def async_agent_created_report(hass: HomeAssistant) -> list[dict[str, Any]]:
    return await hass.async_add_executor_job(agent_created_report)
