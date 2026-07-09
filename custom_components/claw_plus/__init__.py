from __future__ import annotations

import logging
import os
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.components import frontend
from homeassistant.components.http import HomeAssistantView, StaticPathConfig

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

    # ── Register dashboard panel ──
    await _register_dashboard(hass, entry)

    return True


async def _register_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the built-in panel and API view for Claw dashboard."""
    # Serve static assets from www/
    www_path = Path(__file__).parent / "www"
    if www_path.is_dir():
        await hass.http.async_register_static_paths([
            StaticPathConfig(f"/{DOMAIN}/www", str(www_path), False),
        ])

    # Register dashboard JSON API view
    hass.http.register_view(ClawDashboardView)

    # Register sidebar panel (iframe to the dashboard HTML)
    # The panel loads an inline HTML page rendered by the API view
    from homeassistant.components import frontend
    frontend.async_register_built_in_panel(
        hass,
        "iframe",
        "Claw",
        "mdi:tune",
        f"/api/{DOMAIN}/dashboard",
        require_admin=True,
    )

    _LOGGER.info("CLAW_PLUS dashboard panel registered")


class ClawDashboardView(HomeAssistantView):
    """Serve the Claw dashboard HTML page (inline, no external deps)."""

    url = f"/api/{DOMAIN}/dashboard"
    name = f"{DOMAIN}:dashboard"
    requires_auth = True

    async def get(self, request):
        """Render the dashboard page."""
        hass = request.app["hass"]
        
        # Read sensor state
        state = hass.states.get(f"sensor.{DOMAIN}_claw_config")
        sensor_attrs = dict(state.attributes) if state else {}
        
        skills = sensor_attrs.get("skills", [])
        docs = sensor_attrs.get("docs", [])
        plugins = sensor_attrs.get("plugins", [])
        user_mappings = sensor_attrs.get("user_mappings", [])
        
        # Collect Claw options (exclude convenience counts)
        claw_opts = {k: v for k, v in sensor_attrs.items()
                     if k not in ("skills", "docs", "plugins", "user_mappings",
                                  "skills_count", "docs_count", "plugins_count",
                                  "user_mappings_count")}
        
        html = _build_dashboard_html(
            skills=skills, docs=docs, plugins=plugins,
            user_mappings=user_mappings, claw_opts=claw_opts,
        )
        from aiohttp import web
        return web.Response(text=html, content_type="text/html; charset=utf-8")


def _escape_html(text: str) -> str:
    """Simple HTML escape."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_item_rows(items: list[dict], icon: str) -> str:
    """Build HTML table rows for a list of items."""
    rows = ""
    for item in items:
        name = _escape_html(item.get("name", "?"))
        size = item.get("size", "")
        size_str = f" ({size}B)" if size else ""
        rows += f'<tr><td>{icon}</td><td>{name}{size_str}</td></tr>\n'
    return rows


def _build_dashboard_html(
    skills: list[dict], docs: list[dict], plugins: list[dict],
    user_mappings: list[dict], claw_opts: dict,
) -> str:
    """Build the complete dashboard HTML page."""
    skills_rows = _build_item_rows(skills, "⚡")
    docs_rows = _build_item_rows(docs, "📄")
    plugins_rows = _build_item_rows(plugins, "🔌")

    # User mappings
    mappings_rows = ""
    for m in user_mappings:
        provider = _escape_html(m.get("provider", "?"))
        ext_id = _escape_html(m.get("ext_id", "?"))
        ha_user = _escape_html(m.get("ha_user_id", "?")[:12])
        mappings_rows += f'<tr><td>🔗</td><td>{provider}</td><td><code>{ext_id}</code></td><td>→ {ha_user}</td></tr>\n'
    
    # Claw options
    opts_rows = ""
    for k, v in sorted(claw_opts.items()):
        val = _escape_html(str(v)) if v is not None else ""
        opts_rows += f'<tr><td>{_escape_html(str(k))}</td><td>{val}</td></tr>\n'

    # Colors matching HA dark theme
    return f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claw Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #111; color: #e0e0e0; padding: 16px; max-width: 960px; margin: 0 auto;
  }}
  h1 {{ font-size: 1.4rem; margin: 16px 0 8px; color: #58a6ff; }}
  h2 {{ font-size: 1.1rem; margin: 20px 0 8px; color: #8b949e; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }}
  .card {{
    background: #1c1c1c; border: 1px solid #303030; border-radius: 8px; padding: 12px;
  }}
  .card h3 {{ font-size: 0.95rem; color: #58a6ff; margin-bottom: 8px; }}
  .count {{ font-size: 1.8rem; font-weight: 700; color: #f0f0f0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #282828; }}
  th {{ color: #8b949e; font-weight: 600; }}
  td {{ font-family: "SF Mono", Consolas, monospace; }}
  code {{ background: #282828; padding: 1px 4px; border-radius: 3px; font-size: 0.8rem; }}
  .refresh {{ font-size: 0.75rem; color: #666; margin: 4px 0; }}
</style>
</head>
<body>
<h1>🤖 Claw Dashboard</h1>
<p class="refresh">自动刷新 · 数据来自 sensor.claw_config</p>

<div class="grid">
  <div class="card">
    <h3>⚡ 技能</h3>
    <div class="count">{len(skills)}</div>
  </div>
  <div class="card">
    <h3>📄 文档</h3>
    <div class="count">{len(docs)}</div>
  </div>
  <div class="card">
    <h3>🔌 插件</h3>
    <div class="count">{len(plugins)}</div>
  </div>
  <div class="card">
    <h3>🔗 用户映射</h3>
    <div class="count">{len(user_mappings)}</div>
  </div>
</div>

<h2>📋 明细</h2>
<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px;">
  <div class="card">
    <h3>⚡ 技能 ({len(skills)})</h3>
    <table>{"<tr><th></th><th>名称</th></tr>" if skills_rows else ""}{skills_rows or "<p style='color:#666;'>无</p>"}</table>
  </div>
  <div class="card">
    <h3>📄 文档 ({len(docs)})</h3>
    <table>{"<tr><th></th><th>名称</th></tr>" if docs_rows else ""}{docs_rows or "<p style='color:#666;'>无</p>"}</table>
  </div>
  <div class="card">
    <h3>🔌 插件 ({len(plugins)})</h3>
    <table>{"<tr><th></th><th>名称</th></tr>" if plugins_rows else ""}{plugins_rows or "<p style='color:#666;'>无</p>"}</table>
  </div>
  <div class="card">
    <h3>🔗 用户映射 ({len(user_mappings)})</h3>
    <table>{"<tr><th></th><th>平台</th><th>外部ID</th><th>HA用户</th></tr>" if mappings_rows else ""}{mappings_rows or "<p style='color:#666;'>无</p>"}</table>
  </div>
</div>

<h2>⚙️ Claw 选项</h2>
<div class="card">
  <table>{"<tr><th>键</th><th>值</th></tr>" if opts_rows else ""}{opts_rows or "<p style='color:#666;'>无</p>"}</table>
</div>

<script>
  // Auto-refresh every 60 seconds
  setTimeout(function() {{ location.reload(); }}, 60000);
</script>
</body>
</html>"""


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok