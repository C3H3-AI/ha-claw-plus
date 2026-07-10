from __future__ import annotations

import logging
import os

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta

from .const import DOMAIN, CLAW_DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=1)


def _get_claw_base(hass: HomeAssistant) -> str | None:
    config_dir = hass.config.config_dir
    base = os.path.join(config_dir, "custom_components", "claw_assistant")
    if os.path.isdir(base):
        return base
    return None


def _list_skills(base: str) -> list[dict]:
    skills_dir = os.path.join(base, "data", "skills")
    items = []
    if not os.path.isdir(skills_dir):
        return items
    for entry in sorted(os.scandir(skills_dir), key=lambda e: e.name):
        if entry.is_file() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "skills/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            skill_md = os.path.join(entry.path, "SKILL.md")
            items.append({"name": entry.name, "path": "skills/" + entry.name, "has_skill_md": os.path.isfile(skill_md)})
    return items


def _list_docs(base: str) -> list[dict]:
    ws_dir = os.path.join(base, "data", "workspace")
    items = []
    if not os.path.isdir(ws_dir):
        return items
    for entry in sorted(os.scandir(ws_dir), key=lambda e: e.name):
        if entry.is_file() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "workspace/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            for sub in sorted(os.scandir(entry.path), key=lambda e: e.name):
                if sub.is_file():
                    items.append({"name": entry.name + "/" + sub.name, "path": "workspace/" + entry.name + "/" + sub.name, "size": sub.stat().st_size})
    return items


def _list_plugins(base: str) -> list[dict]:
    plugins_dir = os.path.join(base, "plugins")
    items = []
    if not os.path.isdir(plugins_dir):
        return items
    for entry in sorted(os.scandir(plugins_dir), key=lambda e: e.name):
        if entry.is_file() and entry.name.endswith('.py') and not entry.name.startswith('__'):
            items.append({"name": entry.name[:-3], "path": "plugins/" + entry.name, "size": entry.stat().st_size})
        elif entry.is_dir() and not entry.name.startswith('.') and not entry.name.startswith('__'):
            items.append({"name": entry.name, "path": "plugins/" + entry.name})
    return items


def _load_user_mappings(hass: HomeAssistant) -> list[dict]:
    """Load IM user mappings from Claw's workspace/user_mapping.yaml."""
    try:
        import yaml
    except ImportError:
        return []
    mapping_path = os.path.join(
        hass.config.config_dir,
        ".storage", "claw_assistant", "workspace", "user_mapping.yaml",
    )
    if not os.path.isfile(mapping_path):
        return []
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            entries = data.get("mappings")
            if isinstance(entries, list):
                return entries
    except Exception as exc:
        _LOGGER.warning("Failed to load user mappings: %s", exc)
    return []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    sensor = ClawConfigSensor(hass, entry)
    async_add_entities([sensor])

    hass.data.setdefault(DOMAIN, {})
    hass.data.setdefault('_sensors', [])
    hass.data[DOMAIN]['_sensors'] = hass.data[DOMAIN].get('_sensors', [])
    hass.data[DOMAIN]['_sensors'].append(sensor)
    # Store the actual entity_id so the dashboard view can read it correctly.
    # HA auto-generates entity_id as "sensor.{device_name}_{entity_name}" (here:
    # "Claw Dashboard Config" + "Claw Config" = "sensor.claw_dashboard_config_claw_config"),
    # which is NOT predictable from DOMAIN alone.
    hass.data[DOMAIN]['claw_config_entity_id'] = sensor.entity_id
    _LOGGER.info("CLAW_PLUS sensor registered as %s", sensor.entity_id)

    return True


class ClawConfigSensor(SensorEntity):

    _attr_has_entity_name = True
    _attr_icon = "mdi:claw"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_claw_config"
        self._attr_name = "Claw Config"
        self._attr_native_value = "OK"
        self._skills = []
        self._docs = []
        self._plugins = []
        self._user_mappings = []

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Claw Assistant",
            manufacturer="Claw",
            model="Claw Plus",
        )

    @property
    def extra_state_attributes(self) -> dict:
        entries = self.hass.config_entries.async_entries(CLAW_DOMAIN)
        attrs = {}
        if entries:
            attrs = dict(entries[0].options)
        attrs["skills"] = self._skills
        attrs["docs"] = self._docs
        attrs["plugins"] = self._plugins
        attrs["skills_count"] = len(self._skills)
        attrs["docs_count"] = len(self._docs)
        attrs["plugins_count"] = len(self._plugins)
        attrs["user_mappings"] = self._user_mappings
        attrs["user_mappings_count"] = len(self._user_mappings)
        return attrs

    async def async_update(self) -> None:
        base = await self.hass.async_add_executor_job(_get_claw_base, self.hass)
        if not base:
            return
        self._skills = await self.hass.async_add_executor_job(_list_skills, base)
        self._docs = await self.hass.async_add_executor_job(_list_docs, base)
        self._plugins = await self.hass.async_add_executor_job(_list_plugins, base)
        self._user_mappings = await self.hass.async_add_executor_job(
            _load_user_mappings, self.hass
        )
        _LOGGER.info(
            "CLAW_PLUS sensor updated: %d skills, %d docs, %d plugins, %d user_mappings",
            len(self._skills), len(self._docs), len(self._plugins), len(self._user_mappings),
        )