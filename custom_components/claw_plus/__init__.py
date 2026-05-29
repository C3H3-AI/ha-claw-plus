from __future__ import annotations

import logging
import os

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, CLAW_DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

SERVICE_SET_OPTION = "set_option"
SERVICE_LIST_WORKSPACE = "list_workspace"
SERVICE_READ_FILE = "read_workspace_file"
SERVICE_WRITE_FILE = "write_workspace_file"

SET_OPTION_SCHEMA = vol.Schema({
    vol.Required("key"): cv.string,
    vol.Required("value"): vol.Any(bool, int, float, str, None),
})

LIST_WORKSPACE_SCHEMA = vol.Schema({
    vol.Optional("category"): vol.In(["skills", "docs", "plugins", "all"]),
})

READ_FILE_SCHEMA = vol.Schema({
    vol.Required("path"): cv.string,
})

WRITE_FILE_SCHEMA = vol.Schema({
    vol.Required("path"): cv.string,
    vol.Required("content"): cv.string,
})


def _get_claw_base(hass: HomeAssistant) -> str | None:
    """Find Claw Assistant base directory."""
    config_dir = hass.config.config_dir
    base = os.path.join(config_dir, "custom_components", "claw_assistant")
    if os.path.isdir(base):
        return base
    return None


def _list_skills(base: str) -> list[dict]:
    """List skill files from data/skills/."""
    skills_dir = os.path.join(base, "data", "skills")
    items = []
    if not os.path.isdir(skills_dir):
        return items
    for entry in sorted(os.scandir(skills_dir), key=lambda e: e.name):
        if entry.is_file() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "skills/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            skill_md = os.path.join(entry.path, "SKILL.md")
            items.append({
                "name": entry.name,
                "path": "skills/" + entry.name,
                "type": "directory",
                "has_skill_md": os.path.isfile(skill_md),
            })
    return items


def _list_docs(base: str) -> list[dict]:
    """List workspace documents."""
    ws_dir = os.path.join(base, "data", "workspace")
    items = []
    if not os.path.isdir(ws_dir):
        return items
    for entry in sorted(os.scandir(ws_dir), key=lambda e: e.name):
        if entry.is_file() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "workspace/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            for sub in sorted(os.scandir(entry.path), key=lambda e: e.name):
                if sub.is_file() and sub.name.endswith('.md'):
                    items.append({"name": entry.name + "/" + sub.name, "path": "workspace/" + entry.name + "/" + sub.name, "size": sub.stat().st_size})
    return items


def _list_plugins(base: str) -> list[dict]:
    """List plugins."""
    plugins_dir = os.path.join(base, "plugins")
    items = []
    if not os.path.isdir(plugins_dir):
        return items
    for entry in sorted(os.scandir(plugins_dir), key=lambda e: e.name):
        if entry.is_file() and entry.name.endswith('.py') and not entry.name.startswith('__'):
            items.append({"name": entry.name[:-3], "path": "plugins/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "plugins/" + entry.name, "type": "directory"})
    return items


def _read_file(base: str, rel_path: str) -> dict:
    """Read a file from the workspace (blocking I/O)."""
    actual_rel = rel_path
    if rel_path.startswith("skills/"):
        actual_rel = "data/" + rel_path
    elif rel_path.startswith("workspace/"):
        actual_rel = "data/" + rel_path
    file_path = os.path.normpath(os.path.join(base, actual_rel))
    if not file_path.startswith(os.path.normpath(base)):
        return {"error": "path_traversal_denied"}
    if not os.path.isfile(file_path):
        return {"error": "file_not_found", "path": rel_path}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"path": rel_path, "content": content, "size": len(content)}
    except Exception as e:
        return {"error": str(e), "path": rel_path}


def _write_file(base: str, rel_path: str, content: str) -> dict:
    """Write a file to the workspace (blocking I/O)."""
    actual_rel = rel_path
    if rel_path.startswith("skills/"):
        actual_rel = "data/" + rel_path
    elif rel_path.startswith("workspace/"):
        actual_rel = "data/" + rel_path
    file_path = os.path.normpath(os.path.join(base, actual_rel))
    if not file_path.startswith(os.path.normpath(base)):
        return {"error": "path_traversal_denied"}
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        _LOGGER.info("CLAW_PLUS wrote file: %s (%d bytes)", rel_path, len(content))
        return {"success": True, "path": rel_path, "size": len(content)}
    except Exception as e:
        return {"error": str(e), "path": rel_path}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── Service: set_option ──
    async def handle_set_option(call: ServiceCall) -> None:
        key = call.data["key"]
        value = call.data["value"]
        _LOGGER.debug("CLAW_PLUS set_option: %s = %s (type=%s)", key, value, type(value).__name__)
        claw_entries = hass.config_entries.async_entries(CLAW_DOMAIN)
        if not claw_entries:
            _LOGGER.warning("No claw_assistant config entry found")
            return
        claw_entry = claw_entries[0]
        new_options = {**claw_entry.options, key: value}
        hass.config_entries.async_update_entry(claw_entry, options=new_options)
        for sensor in hass.data.get(DOMAIN, {}).get('_sensors', []):
            sensor.async_write_ha_state()

    hass.services.async_register(DOMAIN, SERVICE_SET_OPTION, handle_set_option, schema=SET_OPTION_SCHEMA)
    
    # ── Service: refresh_sensor ──
    async def handle_refresh_sensor(call: ServiceCall) -> None:
        _LOGGER.debug("CLAW_PLUS refresh_sensor called")
        for sensor in hass.data.get(DOMAIN, {}).get('_sensors', []):
            await sensor.async_update()
            sensor.async_write_ha_state()
        _LOGGER.info("CLAW_PLUS sensor refreshed")
    
    hass.services.async_register(DOMAIN, "refresh_sensor", handle_refresh_sensor)

    # ── Service: list_workspace ──
    async def handle_list_workspace(call: ServiceCall) -> ServiceResponse:
        category = call.data.get("category", "all")
        base = _get_claw_base(hass)
        if not base:
            return {"error": "claw_assistant_not_found"}
        result = {}
        if category in ("skills", "all"):
            result["skills"] = await hass.async_add_executor_job(_list_skills, base)
        if category in ("docs", "all"):
            result["docs"] = await hass.async_add_executor_job(_list_docs, base)
        if category in ("plugins", "all"):
            result["plugins"] = await hass.async_add_executor_job(_list_plugins, base)
        result["base_path"] = base
        return result

    hass.services.async_register(DOMAIN, SERVICE_LIST_WORKSPACE, handle_list_workspace, schema=LIST_WORKSPACE_SCHEMA, supports_response=True)

    # ── Service: read_workspace_file ──
    async def handle_read_file(call: ServiceCall) -> ServiceResponse:
        rel_path = call.data["path"]
        base = _get_claw_base(hass)
        if not base:
            return {"error": "claw_assistant_not_found"}
        return await hass.async_add_executor_job(_read_file, base, rel_path)

    hass.services.async_register(DOMAIN, SERVICE_READ_FILE, handle_read_file, schema=READ_FILE_SCHEMA, supports_response=True)

    # ── Service: write_workspace_file ──
    async def handle_write_file(call: ServiceCall) -> ServiceResponse:
        rel_path = call.data["path"]
        content = call.data["content"]
        base = _get_claw_base(hass)
        if not base:
            return {"error": "claw_assistant_not_found"}
        return await hass.async_add_executor_job(_write_file, base, rel_path, content)

    hass.services.async_register(DOMAIN, SERVICE_WRITE_FILE, handle_write_file, schema=WRITE_FILE_SCHEMA, supports_response=True)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok