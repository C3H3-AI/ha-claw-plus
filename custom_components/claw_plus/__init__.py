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
    """Serve and handle the Claw dashboard (GET = HTML, POST = JSON API)."""

    url = f"/api/{DOMAIN}/dashboard"
    name = f"{DOMAIN}:dashboard"
    requires_auth = True

    async def _get_sensor_data(self, hass):
        """Collect all data from sensor.claw_config."""
        state = hass.states.get(f"sensor.{DOMAIN}_claw_config")
        attrs = dict(state.attributes) if state else {}

        agents = sorted(
            e.split(".", 1)[1] for e in hass.states.async_entity_ids("conversation")
            if e != "conversation.claw_assistant"
        )

        return {
            "attrs": attrs,
            "agents": agents,
            "skills": attrs.get("skills", []),
            "docs": attrs.get("docs", []),
            "plugins": attrs.get("plugins", []),
            "user_mappings": attrs.get("user_mappings", []),
        }

    def _get_opt(self, attrs, key, default=""):
        v = attrs.get(key, default)
        return v if v is not None else default

    async def get(self, request):
        """Render the full interactive dashboard HTML."""
        hass = request.app["hass"]
        data = await self._get_sensor_data(hass)
        html = _build_interactive_html(data)
        from aiohttp import web
        return web.Response(text=html, content_type="text/html; charset=utf-8")

    async def post(self, request):
        """API endpoint: read state or set an option."""
        hass = request.app["hass"]
        from aiohttp import web

        try:
            body = await request.json()
        except Exception:
            # POST with no JSON body = just return state data
            data = await self._get_sensor_data(hass)
            return web.json_response(data)

        action = body.get("action", "read")

        if action == "set_option":
            key = body.get("key")
            value = body.get("value")
            if key:
                await hass.services.async_call(
                    DOMAIN, "set_option",
                    {"key": key, "value": value},
                    blocking=True,
                )
            data = await self._get_sensor_data(hass)
            return web.json_response({"ok": True, **data})

        if action == "read":
            data = await self._get_sensor_data(hass)
            return web.json_response(data)

        return web.json_response({"error": "unknown_action"})


def _escape_html(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _b(v) -> bool:
    return v is True or v == "true" or v == 1 or v == "1"


def _build_interactive_html(data: dict) -> str:
    """Build the complete interactive dashboard (matching the original html-pro-card layout)."""
    o = data["attrs"]
    agents = data["agents"]
    skills = data["skills"]
    docs = data["docs"]
    plugins = data["plugins"]

    def agent_options(sel):
        opts = ""
        for a in agents:
            sel_a = ' selected' if sel == f"conversation.{a}" else ""
            opts += f'<option value="conversation.{a}"{sel_a}>{a}</option>'
        return opts

    def checked(key):
        return 'checked' if _b(o.get(key)) else ''

    # Workspace docs: use live count from sensor
    ws_count = len(docs)
    ws_names = "".join(f'<span>{_escape_html(d.get("name","?"))}</span>' for d in docs[:15])

    # Skills
    skill_names = "".join(f'<span>{_escape_html(s.get("name","?"))}</span>' for s in skills[:15])

    # Plugins
    plugin_names = "".join(f'<span>{_escape_html(p.get("name","?"))}</span>' for p in plugins[:15])

    return f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claw Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    padding: 20px; color: var(--ctxt, #1c1c1e);
    background: transparent;
  }}
  :root {{
    --cbg: var(--ha-card-background, var(--card-background-color, #fff));
    --ctxt: var(--primary-text-color, #1c1c1e);
    --csec: var(--secondary-text-color, #8e8e93);
    --cacc: var(--accent-color, #007aff);
    --cbd: var(--divider-color, rgba(60,60,67,0.12));
    --cr: 12px;
  }}
  .hdr {{ font-size: 22px; font-weight: 700; margin: 0 0 20px; display: flex; align-items: center; gap: 10px; letter-spacing: -0.3px; }}
  .hdr .st {{ font-size: 12px; font-weight: 400; color: var(--csec); }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }}
  @media (max-width: 700px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
  .card {{ background: var(--cbg); border: 1px solid var(--cbd); border-radius: var(--cr); padding: 16px; }}
  .card + .card {{ margin-top: 14px; }}
  .ct {{ font-size: 11px; font-weight: 600; color: var(--csec); text-transform: uppercase; letter-spacing: 0.7px; margin-bottom: 12px; display: flex; align-items: center; gap: 6px; }}
  .row {{ display: flex; align-items: center; justify-content: space-between; padding: 9px 0; gap: 12px; }}
  .row + .row {{ border-top: 1px solid var(--cbd); }}
  .rl {{ min-width: 0; flex: 1; }}
  .rn {{ font-size: 14px; font-weight: 500; }}
  .rd {{ font-size: 11px; color: var(--csec); margin-top: 1px; }}
  .rc {{ flex-shrink: 0; display: flex; align-items: center; gap: 8px; }}
  select.rc {{ font-size: 13px; border-radius: 8px; border: 1px solid var(--cbd); background: var(--cbg); color: var(--ctxt); padding: 6px 28px 6px 10px; cursor: pointer; max-width: 190px; -webkit-appearance: none; appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238e8e93'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 8px center; }}
  .tog {{ position: relative; width: 42px; height: 24px; cursor: pointer; display: inline-block; flex-shrink: 0; }}
  .tog input {{ opacity: 0; width: 0; height: 0; position: absolute; }}
  .tog-track {{ position: absolute; inset: 0; background: #e9e9ea; border-radius: 12px; transition: .25s; }}
  .tog input:checked + .tog-track {{ background: var(--cacc); opacity: .85; }}
  .tog-knob {{ position: absolute; top: 2px; left: 2px; width: 20px; height: 20px; background: #fff; border-radius: 50%; transition: .25s cubic-bezier(.4,0,.2,1); box-shadow: 0 1px 3px rgba(0,0,0,.15); }}
  .tog input:checked + .tog-track .tog-knob {{ transform: translateX(18px); }}
  .btn-group {{ display: flex; gap: 4px; }}
  .btn-group button {{ padding: 5px 14px; border: 1px solid var(--cbd); border-radius: 8px; background: transparent; color: var(--ctxt); font-size: 12px; cursor: pointer; transition: .15s; }}
  .btn-group button.active {{ background: var(--cacc); color: #fff; border-color: var(--cacc); }}
  .btn-group button:hover:not(.active) {{ border-color: var(--cacc); }}
  .tog-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
  @media (max-width: 700px) {{ .tog-grid {{ grid-template-columns: 1fr; }} }}
  .tog-cell {{ display: flex; align-items: center; justify-content: space-between; background: var(--cbg); border: 1px solid var(--cbd); border-radius: 10px; padding: 10px 12px; }}
  .tog-cell .rn {{ font-size: 13px; }}
  .tog-cell .rd {{ font-size: 10px; }}
  .info-grid {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }}
  @media (max-width: 700px) {{ .info-grid {{ grid-template-columns: 1fr; }} }}
  .info-card {{ background: var(--cbg); border: 1px solid var(--cbd); border-radius: var(--cr); padding: 14px; }}
  .info-card .count {{ font-size: 28px; font-weight: 700; color: var(--cacc); line-height: 1; margin-bottom: 4px; }}
  .info-card .label {{ font-size: 13px; font-weight: 500; margin-bottom: 6px; }}
  .info-card .items {{ font-size: 11px; color: var(--csec); line-height: 1.6; }}
  .info-card .items span {{ display: inline-block; background: var(--cbd); border-radius: 4px; padding: 1px 6px; margin: 1px 2px; font-size: 10px; }}
  .ftr {{ text-align: center; font-size: 11px; color: var(--csec); padding-top: 14px; margin-top: 14px; border-top: 1px solid var(--cbd); }}

  .conv-modes {{
    display: flex; gap: 4px; align-items: center;
  }}
  .conv-modes button {{
    padding: 5px 14px; border: 1px solid var(--cbd); border-radius: 8px;
    background: transparent; color: var(--ctxt); font-size: 12px; cursor: pointer; transition: .15s;
  }}
  .conv-modes button.active {{
    background: var(--cacc); color: #fff; border-color: var(--cacc);
  }}
  .conv-modes button:hover:not(.active) {{ border-color: var(--cacc); }}
</style>
</head>
<body>
<div class="hdr">🤖 Claw Assistant <span class="st" id="status">•</span></div>
<div id="body">
  <div class="grid2">
    <div class="card">
      <div class="ct">🤖 AI 智能体</div>
      <div class="row">
        <div class="rl"><div class="rn">主力智能体</div><div class="rd">主要对话 AI</div></div>
        <select class="rc" onchange="setOpt('primary_agent',this.value)" id="sel_primary">{"<option value=''>（禁用）</option>" + agent_options(o.get("primary_agent",""))}</select>
      </div>
      <div class="row">
        <div class="rl"><div class="rn">备用智能体</div><div class="rd">主力不可用时自动切换</div></div>
        <select class="rc" onchange="setOpt('fallback_agent',this.value)" id="sel_fallback">{"<option value=''>（禁用）</option>" + agent_options(o.get("fallback_agent",""))}</select>
      </div>
      <div class="row">
        <div class="rl"><div class="rn">第三智能体</div><div class="rd">可选，汇总模式使用</div></div>
        <select class="rc" onchange="setOpt('secondary_fallback_agent',this.value)" id="sel_secondary">{"<option value=''>（禁用）</option>" + agent_options(o.get("secondary_fallback_agent",""))}</select>
      </div>
    </div>
    <div class="card">
      <div class="ct">💬 对话设置</div>
      <div class="row">
        <div class="rl"><div class="rn">对话模式</div></div>
        <div class="conv-modes" id="conv_modes">
          <button class="{'active' if o.get('conversation_mode','add_name')=='no_name' else ''}" data-mode="no_name">简单</button>
          <button class="{'active' if o.get('conversation_mode','add_name')=='add_name' else ''}" data-mode="add_name">带名字</button>
          <button class="{'active' if o.get('conversation_mode','add_name')=='detailed' else ''}" data-mode="detailed">详细</button>
        </div>
      </div>
      <div class="row">
        <div class="rl"><div class="rn">联网搜索</div></div>
        <label class="tog"><input type="checkbox" {checked('enable_web_search')} onchange="setOpt('enable_web_search',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label>
      </div>
      <div class="row">
        <div class="rl"><div class="rn">持续对话</div><div class="rd">保持上下文不中断</div></div>
        <label class="tog"><input type="checkbox" {checked('continuous_conversation')} onchange="setOpt('continuous_conversation',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label>
      </div>
      <div class="row">
        <div class="rl"><div class="rn">流式效果</div></div>
        <label class="tog"><input type="checkbox" {checked('enable_streaming_effect')} onchange="setOpt('enable_streaming_effect',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="ct">⚙️ 功能开关</div>
    <div class="tog-grid">
      <div class="tog-cell"><div class="rl"><div class="rn">联网搜索</div><div class="rd">对话中自动搜索网络</div></div><label class="tog"><input type="checkbox" {checked('enable_web_search')} onchange="setOpt('enable_web_search',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">流式效果</div><div class="rd">实时流式输出</div></div><label class="tog"><input type="checkbox" {checked('enable_streaming_effect')} onchange="setOpt('enable_streaming_effect',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">文件上传</div><div class="rd">允许上传文件给 AI</div></div><label class="tog"><input type="checkbox" {checked('enable_file_upload')} onchange="setOpt('enable_file_upload',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">富文本 Markdown</div><div class="rd">美化 Markdown 渲染</div></div><label class="tog"><input type="checkbox" {checked('enable_rich_markdown')} onchange="setOpt('enable_rich_markdown',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">工具详情</div><div class="rd">显示工具调用细节</div></div><label class="tog"><input type="checkbox" {checked('enable_tool_details')} onchange="setOpt('enable_tool_details',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">工具进度</div><div class="rd">显示工具执行进度</div></div><label class="tog"><input type="checkbox" {checked('enable_tool_progress')} onchange="setOpt('enable_tool_progress',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">侧边栏</div><div class="rd">显示 Claw 侧边栏</div></div><label class="tog"><input type="checkbox" {checked('enable_sidebar_dock')} onchange="setOpt('enable_sidebar_dock',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">状态栏</div><div class="rd">显示上下文状态栏</div></div><label class="tog"><input type="checkbox" {checked('enable_context_status_bar')} onchange="setOpt('enable_context_status_bar',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">活动追踪</div><div class="rd">追踪用户活动</div></div><label class="tog"><input type="checkbox" {checked('enable_activity_tracking')} onchange="setOpt('enable_activity_tracking',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
      <div class="tog-cell"><div class="rl"><div class="rn">声音通知</div><div class="rd">操作提示音</div></div><label class="tog"><input type="checkbox" {checked('enable_sound_notifications')} onchange="setOpt('enable_sound_notifications',this.checked)"><span class="tog-track"><span class="tog-knob"></span></span></label></div>
    </div>
  </div>

  <div class="info-grid">
    <div class="info-card">
      <div class="count" id="cnt_docs">{len(docs)}</div>
      <div class="label">📄 工作区文档</div>
      <div class="items">{ws_names or '<span style="background:transparent">暂无</span>'}</div>
    </div>
    <div class="info-card">
      <div class="count" id="cnt_skills">{len(skills)}</div>
      <div class="label">🌟 已安装技能</div>
      <div class="items">{skill_names or '<span style="background:transparent">暂无</span>'}</div>
    </div>
    <div class="info-card">
      <div class="count" id="cnt_plugins">{len(plugins)}</div>
      <div class="label">🔗 已安装插件</div>
      <div class="items">{plugin_names or '<span style="background:transparent">暂无</span>'}</div>
    </div>
  </div>

  <div class="ftr">
    ⚡ 更改即时生效 · 在 Claw Assistant 配置流中编辑工作区/技能/插件 · v1.2.0
  </div>
</div>

<script>
// REST API helper — POST to the same URL
var API = window.location.pathname;

function setOpt(key, value) {{
  fetch(API, {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ action: 'set_option', key: key, value: value }})
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(d) {{ if (d.error) console.error(d.error); }});
}}

// Conversation mode buttons
document.getElementById('conv_modes').addEventListener('click', function(e) {{
  var btn = e.target.closest('button');
  if (!btn || !btn.dataset.mode) return;
  btn.parentElement.querySelectorAll('button').forEach(function(b) {{ b.classList.remove('active'); }});
  btn.classList.add('active');
  setOpt('conversation_mode', btn.dataset.mode);
}});

// Status timer
(function updateStatus() {{
  document.getElementById('status').textContent = '\\u2022 ' + new Date().toLocaleTimeString('zh-CN', {{ hour:'2-digit', minute:'2-digit' }});
  setTimeout(updateStatus, 30000);
}})();

// Auto-refresh sensor data every 30 seconds
function refreshData() {{
  fetch(API, {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: '{{"action":"read"}}' }})
  .then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    if (d.attrs) {{
      var o = d.attrs;
      // Update conversation mode buttons
      var curMode = o.conversation_mode || 'add_name';
      var modeBtns = document.getElementById('conv_modes');
      if (modeBtns) {{
        modeBtns.querySelectorAll('button').forEach(function(b) {{
          b.classList.toggle('active', b.dataset.mode === curMode);
        }});
      }}
      // Update counts
      if (d.docs) document.getElementById('cnt_docs').textContent = d.docs.length;
      if (d.skills) document.getElementById('cnt_skills').textContent = d.skills.length;
      if (d.plugins) document.getElementById('cnt_plugins').textContent = d.plugins.length;
    }}
  }});
}}
setInterval(refreshData, 30000);
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