from __future__ import annotations

import json
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

_VFAB_URL = f"/api/claw_plus/voice_fab.js?v=1"

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


# ── Native section grouping (matches claw_assistant config_flow.py) ──
# conv_dialog / conv_display / conv_runtime use HA `section()` groupings that
# are NOT expressed in translations JSON, so we mirror them here to render the
# same collapsible groups the native options flow shows.
_SECTION_DEFS = {
    "conv_dialog": [
        {"key": "reply_policy", "title": "回复策略", "collapsed": False,
         "fields": ["conversation_mode", "enable_web_search"]},
    ],
    "conv_display": [
        {"key": "chat_window", "title": "聊天窗口", "collapsed": False,
         "fields": ["enable_sidebar_dock", "continuous_conversation", "enable_sound_notifications"]},
        {"key": "message_display", "title": "消息显示", "collapsed": True,
         "fields": ["enable_file_upload", "enable_rich_markdown", "enable_activity_tracking"]},
        {"key": "diagnostics", "title": "诊断与工具", "collapsed": False,
         "fields": ["enable_tool_details", "enable_tool_progress", "enable_context_status_bar"]},
        {"key": "voice_fab", "title": "悬浮按钮", "collapsed": False,
         "fields": ["enable_voice_fab"]},
    ],
    "conv_runtime": [
        {"key": "tool_loop", "title": "工具循环", "collapsed": False,
         "fields": ["max_tool_repeat", "identical_call_warn", "identical_call_stop"]},
        {"key": "pipeline", "title": "流水线", "collapsed": True,
         "fields": ["pipeline_timeout"]},
    ],
}

# 8 standard workspace docs (mirrors claw_assistant workspace_store._DOC_NAMES)
_DOC_ORDER = ["AGENTS", "BOOTSTRAP", "HEARTBEAT", "IDENTITY", "MEMORY", "SOUL", "TOOLS", "USER"]
_DOC_INFO = {
    "AGENTS": "定义各工作区文档的职责分工和 AI 的操作约束规则",
    "BOOTSTRAP": "首次引导流程，引导 AI 完成初始化对话（如收集用户名、偏好等）",
    "HEARTBEAT": "定时跟进任务，AI 按设定周期自动执行跟进",
    "IDENTITY": "助手身份设定：名称、生物类型、性格标签和代表 Emoji",
    "MEMORY": "用户偏好记忆，长期记住的用户偏好和习惯",
    "SOUL": "语气与风格，定义 AI 的说话方式和性格基调",
    "TOOLS": "环境与工具备注，记录设备/服务信息，AI 执行时参考",
    "USER": "用户基本信息，AI 据此个性化回复",
}

# Fallback menu tree — used when the native claw_assistant translation file
# cannot be read (defensive: the panel MUST always render its tabs AND fields).
# Mirrors native options.step structure (init menu_options + sub-steps) WITH the
# real field lists, so every leaf page shows its controls even without translation.
_FALLBACK_OPT_STEPS = {
    "init": {
        "title": "Claw 配置",
        "description": "配置 Claw Assistant 的各项能力",
        "menu_options": {
            "agent_settings": "配置智能代理 ｜ 首要、后备、总结链路",
            "conversation_settings": "调整对话风格 ｜ 显示、流式与维度",
            "workspace_editor": "编辑工作文档 ｜ 主提示词与技能资料",
            "skill_editor": "管理安装技能 ｜ 动态查看与编辑",
            "plugin_manager": "管理安装插件 ｜ Hermes 兼容插件",
        },
    },
    "agent_settings": {
        "title": "配置智能代理",
        "description": "配置 AI 对话的调度链路，系统按顺序尝试各代理直到获得有效回复。\n\n▸ 首要 AI — 默认优先使用的对话代理\n▸ 后备 AI — 首要 AI 无法完成任务时自动切换\n▸ 总结 AI — 可选，配置后前两个 AI 分别回答，由该 AI 汇总",
        "data": {
            "primary_agent": "首要 AI 实体",
            "fallback_agent": "后备 AI 实体",
            "secondary_fallback_agent": "总结 AI 实体（可选）",
        },
        "data_description": {
            "primary_agent": "默认优先响应的对话代理实体。",
            "fallback_agent": "首要 AI 失败时自动接管的对话代理实体。",
            "secondary_fallback_agent": "可选；配置后前两个 AI 分别回答，由该 AI 汇总。",
        },
    },
    "conversation_settings": {"title": "调整对话风格", "menu_options": {
        "conv_dialog": "对话策略 ｜ 回复如何生成",
        "conv_display": "聊天体验 ｜ 窗口如何使用",
        "conv_runtime": "执行控制 ｜ 任务如何运行",
        "user_mapping": "用户关联 ｜ 通道身份映射",
    }},
    "conv_dialog": {
        "title": "回复策略",
        "description": "设置 AI 生成回复时使用的策略。",
        "data": {"conversation_mode": "对话模式", "enable_web_search": "联网搜索"},
        "data_description": {
            "conversation_mode": "精简模式只显示最终回复；标注来源会在回复前标注 AI 名称；多模型对比让所有 AI 同时回答。",
            "enable_web_search": "启用后 AI 可在需要时自动补充联网搜索结果。",
        },
    },
    "conv_display": {
        "title": "显示设置",
        "description": "设置聊天界面的交互和显示方式。",
        "data": {
            "enable_sidebar_dock": "AI 侧边栏",
            "continuous_conversation": "连续聊天窗口",
            "enable_sound_notifications": "提示音",
            "enable_file_upload": "文件上传",
            "enable_rich_markdown": "富文本增强",
            "enable_activity_tracking": "操作感知",
            "enable_tool_details": "工具调用详情",
            "enable_tool_progress": "工具调用进度",
            "enable_context_status_bar": "上下文状态栏",
        },
        "data_description": {
            "enable_sidebar_dock": "启用后 AI 对话以右侧固定侧边栏展示，关闭则用浮动弹窗。",
            "continuous_conversation": "启用后重新打开聊天框沿用同一对话，直到 /new 才切换。",
            "enable_sound_notifications": "关键时刻播放提示音。",
            "enable_file_upload": "显示附件按钮，支持拖拽/粘贴/点击上传。",
            "enable_rich_markdown": "保留 Markdown 格式转为富文本。",
            "enable_activity_tracking": "AI 感知界面操作以更好理解上下文。",
            "enable_tool_details": "显示完整工具调用卡片。",
            "enable_tool_progress": "实时显示工具执行进度。",
            "enable_context_status_bar": "显示上下文 token、耗时等状态。",
        },
    },
    "conv_runtime": {
        "title": "运行时",
        "description": "设置 AI 执行任务时的控制规则。",
        "data": {
            "max_tool_repeat": "工具重复上限",
            "identical_call_warn": "相同调用警告阈值",
            "identical_call_stop": "相同调用终止阈值",
            "pipeline_timeout": "等待时长 (分钟)",
        },
        "data_description": {
            "max_tool_repeat": "AI 对同一工具的最大重复调用次数。",
            "identical_call_warn": "相同参数调用达到此次数时警告 AI 换思路。",
            "identical_call_stop": "相同参数调用达到此次数时要求 AI 停止。",
            "pipeline_timeout": "对话窗口等待 AI 完成的时长（分钟）。",
        },
    },
    "workspace_editor": {"title": "编辑工作文档", "menu_options": {
        "ws_agents": "AGENTS ｜ 智能体协作指南", "ws_bootstrap": "BOOTSTRAP ｜ 启动引导",
        "ws_heartbeat": "HEARTBEAT ｜ 周期心跳", "ws_identity": "IDENTITY ｜ 身份设定",
        "ws_memory": "MEMORY ｜ 长期记忆", "ws_soul": "SOUL ｜ 性格灵魂",
        "ws_tools": "TOOLS ｜ 工具清单", "ws_user": "USER ｜ 用户档案",
    }},
    "ws_agents": {"title": "AGENTS", "description": "定义各工作区文档的职责分工和 AI 的操作约束规则。", "data": {"content": "Markdown 编辑器"}},
    "ws_bootstrap": {"title": "BOOTSTRAP", "description": "首次引导流程，引导 AI 完成初始化对话。", "data": {"bootstrap_active": "启用引导流程", "content": "Markdown 编辑器"}, "data_description": {"bootstrap_active": "开启后 AI 下次对话重新执行引导流程。"}},
    "ws_heartbeat": {"title": "HEARTBEAT", "description": "定时跟进任务，AI 按设定周期自动执行。", "data": {"content": "Markdown 编辑器"}},
    "ws_identity": {"title": "IDENTITY", "description": "助手身份设定：名称、生物类型、性格标签和代表 Emoji。", "data": {"content": "Markdown 编辑器"}},
    "ws_memory": {"title": "MEMORY", "description": "用户偏好记忆，长期记住的用户偏好和习惯。", "data": {"content": "Markdown 编辑器"}},
    "ws_soul": {"title": "SOUL", "description": "语气与风格，定义 AI 的说话方式和性格基调。", "data": {"content": "Markdown 编辑器"}},
    "ws_tools": {"title": "TOOLS", "description": "环境与工具备注，记录设备/服务信息。", "data": {"content": "Markdown 编辑器"}},
    "ws_user": {"title": "USER", "description": "用户基本信息，AI 据此个性化回复。", "data": {"content": "Markdown 编辑器"}},
    "skill_editor": {"title": "管理安装技能", "data": {}},
    "plugin_manager": {"title": "管理安装插件", "data": {}},
    "user_mapping": {"title": "用户关联", "description": "把外部 IM 用户映射到 HA 成员", "menu_options": {
        "um_pick_channel": "选择通道", "um_pick_identity": "选择外部用户", "um_pick_member": "选择 HA 成员",
    }},
    "um_pick_channel": {"title": "选择通道", "description": "第 1 步：选择已接入的 IM 通道。", "data": {"provider": "通道平台"}},
    "um_pick_identity": {"title": "选择外部用户", "description": "第 2 步：选择该通道下的外部用户。", "data": {"ext_id": "外部用户", "ext_id_manual": "手动填写 ID"}, "data_description": {"ext_id": "从 cn_im_hub 与近期对话自动识别", "ext_id_manual": "选手动输入时填写，不含通道前缀"}},
    "um_pick_member": {"title": "选择 HA 成员", "description": "第 3 步：选择要绑定的 HA 家庭成员。", "data": {"ha_user": "关联到"}, "data_description": {"ha_user": "选择已创建的 HA 用户账号"}},
    "um_remove": {"title": "删除关联", "description": "解除外部身份与家庭成员的绑定。", "data": {"remove_key": "要删除的映射"}},
}

# Selector options used by mode_select controls (conversation_mode).
_FALLBACK_SELECTORS = {
    "conversation_mode": {
        "options": {
            "no_name": "精简模式",
            "add_name": "标注来源",
            "detailed": "多模型对比",
        }
    }
}

# Authoritative field→type map. Decouples rendering from live sensor attrs so
# every known field always gets a correct control, even when the native config
# entry has no stored value yet or the translation file cannot be read.
FIELD_TYPE_HINTS = {
    "primary_agent": "agent_select",
    "fallback_agent": "agent_select",
    "secondary_fallback_agent": "agent_select",
    "conversation_mode": "mode_select",
    "enable_web_search": "toggle",
    "enable_sidebar_dock": "toggle",
    "continuous_conversation": "toggle",
    "enable_sound_notifications": "toggle",
    "enable_context_status_bar": "toggle",
    "enable_file_upload": "toggle",
    "enable_rich_markdown": "toggle",
    "enable_activity_tracking": "toggle",
    "enable_tool_details": "toggle",
    "enable_tool_progress": "toggle",
    "bootstrap_active": "toggle",
    "max_tool_repeat": "slider",
    "identical_call_warn": "slider",
    "identical_call_stop": "slider",
    "pipeline_timeout": "slider",
    "enable_voice_fab": "toggle",
}

# Built-in page/section descriptions. Guarantees every area shows a helpful
# intro even when the native claw_assistant translation file omits it (the
# server's installed version may differ from the one we studied). Native
# descriptions still win when present; these are the fallback.
_PAGE_DESC = {
    "agent_settings": "配置 AI 对话的调度链路，系统按顺序尝试各代理直到获得有效回复。\n\n"
                      "▸ 首要 AI — 默认优先使用的对话代理\n"
                      "▸ 后备 AI — 首要 AI 无法完成任务时自动切换\n"
                      "▸ 总结 AI — 可选，配置后前两个 AI 分别回答，由该 AI 汇总",
    "conversation_settings": "调整 AI 的对话策略、聊天体验、执行控制与用户关联。",
    "workspace_editor": "**使用说明：**\n这些文档定义了 AI 助手的**核心人格与行为**，使用 Markdown 格式编写。\n"
                        "选择一个文档进行查看或编辑，修改保存后**立即生效**，无需重启。",
    "skill_editor": "选择一个技能查看其 Markdown 全文，或直接在下一步进行编辑/删除。\n"
                    "列表实时从技能目录读取，新装/卸载技能即刻反映。",
    "plugin_manager": "感谢 Hermes Agent 项目，本功能特别支持 **Hermes 兼容的扩展模块**。\n"
                      "您可以到 GitHub 或其他平台寻找任何兼容的插件，为 AI 提供新能力。\n插件支持热加载，无需重启。",
    "user_mapping": "将飞书、微信、QQ 等 IM 通道里的外部身份，绑定到 HA 家庭成员。\n"
                    "绑定后，每位成员拥有独立的 persona 与对话记忆，互不干扰。",
    "conv_dialog": "设置 AI 生成回复时使用的策略。\n\n"
                   "▸ 对话模式 — 精简 / 标注来源 / 多模型对比\n"
                   "▸ 联网搜索 — 开启后 AI 可自动补充联网结果提升回答质量",
    "conv_display": "设置聊天界面的交互和显示方式。\n\n"
                   "▸ 侧边栏停靠 — 以固定侧栏形式并排展示\n"
                   "▸ 工具详情 / 进度 — 控制工具调用信息的展示粒度\n"
                   "▸ 上下文状态栏 — 显示 token、耗时等信息",
    "conv_runtime": "设置 AI 执行任务时的控制规则。\n\n"
                    "▸ 工具重复上限 — 防止 AI 陷入无效循环\n"
                    "▸ 流水线超时 — 复杂任务请调大，避免被打断",
    "ws_agents": "**文件角色与操作规范**\n定义各工作区文档的职责分工和 AI 的操作约束规则。\n类似于项目的 README，告诉 AI 每个文件是干什么的、什么该做什么不该做。\n\n使用 Markdown 格式，保存后立即生效。",
    "ws_bootstrap": "**首次引导流程**\n仅在首次启动时生效，引导 AI 完成初始化对话（如收集用户名、偏好等）。\n当 IDENTITY 和 USER 信息填写完成后，引导流程自动结束。\n\n使用 Markdown 格式，保存后立即生效。",
    "ws_heartbeat": "**定时跟进任务**\n定义心跳机制的行为规则，AI 会按设定周期自动执行跟进任务。\n例如：定时检查设备状态、发送提醒、执行自动化等。\n\n使用 Markdown 格式，保存后立即生效。",
    "ws_identity": "**助手身份设定**\n定义 AI 助手的名称、生物类型、性格标签和代表 Emoji 等身份信息。\n这些信息会影响 AI 的自我认知和回复风格。\n\n格式示例：\n`- **Name:** 小助手`\n`- **Creature:** AI管家`\n`- **Vibe:** 冷静、高效、幽默`\n\n使用 Markdown 格式，保存后立即生效。",
    "ws_memory": "**用户偏好记忆**\n存储 AI 需要长期记住的用户偏好和习惯，每行一条。\nAI 会在对话时根据上下文自动匹配相关记忆条目。\n\n格式示例：\n`- 用户喜欢简短回复，少废话`\n`- preferred_address: 老板`\n`- timezone: Asia/Shanghai`\n\n使用 Markdown 格式，保存后立即生效。",
    "ws_soul": "**语气与风格**\n定义 AI 助手的说话方式和性格基调。\n例如：冷静直接、幽默轻松、专业正式等。\n\n格式示例：\n`- Be calm, direct, practical, and concise.`\n`- Sound like a reliable operator, not a generic chatbot.`\n\n使用 Markdown 格式，保存后立即生效。",
    "ws_tools": "**环境与工具备注**\n记录设备信息、服务名称等，AI 执行操作时自动参考。\n\n**访问令牌（可选）：** 若 AI 无法读日志/执行命令，添加长期令牌启用 REST 穿透。\n个人资料页底部创建令牌，格式：`ha_token: eyJ...`\n\n保存后立即生效。",
    "ws_user": "**用户基本信息**\n存储用户的基本资料，AI 会据此个性化回复。\n\n格式示例：\n`- **Name:** 张三`\n`- **What to call them:** 老板`\n`- **Timezone:** Asia/Shanghai`\n\n使用 Markdown 格式，保存后立即生效。",
    "um_pick_channel": "第 1 步 / 共 3 步：选择已在 cn_im_hub 中接入的 IM 通道。",
    "um_pick_identity": "第 2 步 / 共 3 步：选择该通道下的外部用户。",
    "um_pick_member": "第 3 步 / 共 3 步：选择要绑定的 HA 家庭成员。",
    "um_remove": "删除通道绑定，解除外部身份与家庭成员的关联。",
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ── Voice FAB 静态路径 ──
    js_path = Path(__file__).parent / "www" / "voice_fab.js"
    if js_path.is_file():
        await hass.http.async_register_static_paths([
            StaticPathConfig(f"/api/{DOMAIN}/voice_fab.js", str(js_path), cache_headers=False),
        ])

    # ── Service: set_option ──
    async def handle_set_option(call: ServiceCall) -> None:
        key = call.data["key"]
        value = call.data["value"]
        _LOGGER.debug("CLAW_PLUS set_option: %s = %s (type=%s)", key, value, type(value).__name__)

        # Voice FAB toggle → 注入/移除 JS
        if key == "enable_voice_fab":
            from homeassistant.config_entries import ConfigEntryState
            vf_entries = hass.config_entries.async_entries("voice_fab")
            if any(e.state is ConfigEntryState.LOADED for e in vf_entries):
                await hass.services.async_call(
                    "voice_fab", "set_fab_enabled", {"enabled": bool(value)})
            else:
                if bool(value):
                    frontend.add_extra_js_url(hass, _VFAB_URL)
                else:
                    frontend.remove_extra_js_url(hass, _VFAB_URL)

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

    # ── 初始 Voice FAB 状态 ──
    if js_path.is_file() and entry.options.get("enable_voice_fab", False):
        from homeassistant.config_entries import ConfigEntryState
        vf_entries = hass.config_entries.async_entries("voice_fab")
        if any(e.state is ConfigEntryState.LOADED for e in vf_entries):
            await hass.services.async_call("voice_fab", "set_fab_enabled", {"enabled": True})
        else:
            frontend.add_extra_js_url(hass, _VFAB_URL)

    return True


async def _register_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register the built-in panel and API view for Claw dashboard."""
    from homeassistant.components import frontend

    # Register dashboard JSON API view (its GET also serves the iframe HTML,
    # so we don't depend on the version-specific static-path registration).
    hass.http.register_view(ClawDashboardView)

    # Register sidebar panel (iframe to the dashboard view, which serves HTML).
    # For an "iframe" panel, the embedded URL MUST go in config={"url": ...}.
    frontend.async_register_built_in_panel(
        hass,
        "iframe",
        "Claw Assistant Control",
        "mdi:robot-happy",
        "claw",
        config={"url": f"/api/{DOMAIN}/dashboard"},
        require_admin=True,
    )

    # Best-effort: also expose /api/claw_plus/www/* static (legacy deploy path).
    # Wrapped so a failure here can NEVER block the view/panel registration above.
    www_path = Path(__file__).parent / "www"
    if www_path.is_dir():
        try:
            await hass.http.async_register_static_paths([
                StaticPathConfig(f"/api/{DOMAIN}/www", str(www_path), True),
            ])
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("CLAW_PLUS static path registration skipped: %s", err)

    _LOGGER.info("CLAW_PLUS dashboard panel registered")


class ClawDashboardView(HomeAssistantView):
    """Serve and handle the Claw dashboard (GET = HTML, POST = JSON API)."""

    url = f"/api/{DOMAIN}/dashboard"
    name = f"{DOMAIN}:dashboard"
    # NOTE: must be False. HA's ha-panel-iframe is a bare <iframe> that does NOT
    # forward the auth token, so requires_auth=True always yields 401 inside the
    # panel. Data is rendered server-side from hass (no client-side token fetch
    # needed). Cross-site POST is still blocked by HA's default CORS policy, and
    # the whole instance sits behind the auth proxy, so exposure is limited.
    requires_auth = False

    async def _get_sensor_data(self, hass):
        """Collect all data from the claw_config sensor.

        The sensor entity_id is stored in hass.data by sensor.py at registration
        time because HA may generate an entity_id like
        "sensor.claw_dashboard_config_claw_config" (from device_name + entity_name)
        that is NOT predictable from DOMAIN alone.
        """
        entity_id = hass.data.get(DOMAIN, {}).get(
            "claw_config_entity_id",
            # Fallback (old naming convention — might not exist)
            f"sensor.{DOMAIN}_claw_config",
        )
        state = hass.states.get(entity_id)
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
        """Serve the dashboard HTML (iframe panel entry point).

        GET /api/claw_plus/dashboard → the control-panel HTML.
        This avoids depending on HA's version-specific static-path registration.
        """
        from aiohttp import web

        www_file = Path(__file__).parent / "www" / "claw_control.html"
        if www_file.is_file():
            try:
                text = www_file.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                text = "<h1>Claw dashboard file could not be read</h1>"
            return web.Response(
                text=text,
                content_type="text/html",
                charset="utf-8",
                headers={"Cache-Control": "no-store, max-age=0"},
            )

        # Fallback: return live data as JSON
        hass = request.app["hass"]
        data = await self._get_sensor_data(hass)
        return web.json_response(data)

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

        if action == "read_file":
            path = body.get("path", "")
            try:
                result = await hass.services.async_call(
                    DOMAIN, "read_workspace_file",
                    {"path": path},
                    blocking=True,
                    return_response=True,
                )
                return web.json_response(result)
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "write_file":
            path = body.get("path", "")
            content = body.get("content", "")
            try:
                result = await hass.services.async_call(
                    DOMAIN, "write_workspace_file",
                    {"path": path, "content": content},
                    blocking=True,
                    return_response=True,
                )
                return web.json_response(result)
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "list_workspace":
            category = body.get("category", "all")
            try:
                result = await hass.services.async_call(
                    DOMAIN, "list_workspace",
                    {"category": category},
                    blocking=True,
                    return_response=True,
                )
                return web.json_response(result)
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "reload_conversation":
            try:
                await hass.services.async_call(
                    "conversation", "reload", {},
                    blocking=True,
                )
                data = await self._get_sensor_data(hass)
                return web.json_response({"ok": True, **data})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "list_users":
            """Return HA users and available IM identities for dropdowns."""
            users = []
            for user in await hass.auth.async_get_users():
                users.append({
                    "id": user.id,
                    "name": user.name or user.id[:12],
                    "is_active": user.is_active,
                })
            # Get IM users from the sensor's user_mappings provider context
            im_identities = []
            # Check cn_im_hub if available
            ih_state = hass.states.get("select.cn_im_hub_im_hub")
            if ih_state:
                im_identities = list(ih_state.attributes.get("options", []))
            # Also check IM channel entities
            for state in hass.states.async_all("select"):
                if "cn_im_hub" in state.entity_id and "channel" in state.entity_id:
                    im_identities.extend(list(state.attributes.get("options", [])))
            data = await self._get_sensor_data(hass)
            return web.json_response({
                "ok": True,
                "users": users,
                "im_identities": sorted(set(im_identities)) if im_identities else [],
                **data,
            })

        if action == "get_labels":
            """Return labels & descriptions from claw_assistant translations."""
            import json as _json, os as _os
            trans_path = _os.path.join(
                hass.config.config_dir,
                "custom_components", "claw_assistant",
                "translations", "zh-Hans.json",
            )
            labels = {}
            if _os.path.isfile(trans_path):
                try:
                    with open(trans_path, "r", encoding="utf-8") as f:
                        trans = _json.load(f)
                    # Options flow steps
                    for step_name, step_data in trans.get("options", {}).get("step", {}).items():
                        d = step_data.get("data", {})
                        desc = step_data.get("data_description", {})
                        if d:
                            labels[step_name] = {"data": d, "desc": desc}
                    # Selectors
                    sel = trans.get("selector", {})
                    if sel:
                        labels["_selectors"] = sel
                except Exception:
                    pass
            return web.json_response({"ok": True, "labels": labels})

        if action == "get_option_schema":
            """Build the dashboard schema from claw_assistant's translation menu
            tree (options.step.init.menu_options) plus the live stored options.

            The sidebar is generated from the SAME menu tree the integration's
            config flow uses — so the first layer always matches
            (配置智能代理 / 调整对话风格 / 编辑工作文档 / 管理安装技能 / 管理安装插件 …),
            including submenus, and auto-updates when the integration adds areas.
            No hardcoded menu text.
            """
            import json as _json, os as _os

            # Read current options and agent list
            entity_id = hass.data.get(DOMAIN, {}).get(
                "claw_config_entity_id",
                f"sensor.{DOMAIN}_claw_config",
            )
            state = hass.states.get(entity_id)
            attrs = dict(state.attributes) if state else {}

            agents = sorted(
                e.split(".", 1)[1] for e in hass.states.async_entity_ids("conversation")
                if e != "conversation.claw_assistant"
            )
            agent_ids = [f"conversation.{a}" for a in agents]

            # Read translations (source of truth for menu tree + labels).
            # Resolve path robustly: try hass.config.config_dir first, then
            # the conventional /config mount (Supervisor/container setups).
            trans = {}
            trans_path = ""
            trans_found = False

            def _read_trans():
                nonlocal trans_path, trans_found
                cands = [
                    _os.path.join(hass.config.config_dir, "custom_components",
                                  "claw_assistant", "translations", "zh-Hans.json"),
                    "/config/custom_components/claw_assistant/translations/zh-Hans.json",
                ]
                for p in cands:
                    if _os.path.isfile(p):
                        trans_path = p
                        trans_found = True
                        with open(p, encoding="utf-8") as _f:
                            return _json.loads(_f.read())
                trans_path = cands[0]
                return {}

            try:
                trans = await hass.async_add_executor_job(_read_trans)
            except Exception:
                trans = {}

            opt_steps = trans.get("options", {}).get("step", {})
            # Defensive: if native translation is unavailable, use built-in menu
            if not opt_steps:
                opt_steps = _FALLBACK_OPT_STEPS

            # Labels & descriptions per step (for field rendering)
            field_meta = {}
            for step_name, step_data in opt_steps.items():
                d = step_data.get("data", {})
                desc = step_data.get("data_description", {})
                if d:
                    field_meta[step_name] = {"data": d, "desc": desc}
            field_meta["_selectors"] = trans.get("selector", {}) or _FALLBACK_SELECTORS

            # ── 注入 claw_plus 自定义字段的标签和说明（原生翻译不含这些字段）──
            _CUSTOM_FIELD_DESC = {
                "enable_voice_fab": ("语音助手悬浮按钮", "在 HA 所有页面显示可拖动的语音助手按钮，轻触唤醒语音对话。"),
            }
            for fkey, (label, desc_text) in _CUSTOM_FIELD_DESC.items():
                for step_name, meta in list(field_meta.items()):
                    if fkey in meta.get("data", {}):
                        if desc_text:
                            meta["desc"][fkey] = desc_text
                        break
                else:
                    # 字段不在任何现有 step 中 → 注入到第一个有 section 的 step
                    for step_name in _SECTION_DEFS:
                        for sec in _SECTION_DEFS[step_name]:
                            if fkey in sec.get("fields", []):
                                fm = field_meta.setdefault(step_name, {"data": {}, "desc": {}})
                                fm["data"].setdefault(fkey, label)
                                fm["desc"][fkey] = desc_text
                                break

            # ── Build the menu tree from translation menu_options ──
            # Keys that are pure config-flow navigation controls, not stored options
            NAV_KEYS = {"back", "save_and_exit", "next_step",
                        "provider", "remove_key", "skill_slug", "plugin_key"}

            def _split_menu(val):
                # menu_options values look like "标题 ｜ 副标题"
                if "｜" in val:
                    t, s = val.split("｜", 1)
                    return t.strip(), s.strip()
                return val.strip(), ""

            def _build_node(key, menu_val=None):
                s = opt_steps.get(key, {})
                mo = s.get("menu_options")
                node = {"key": key}
                if menu_val is not None:
                    title, subtitle = _split_menu(menu_val)
                    node["title"] = title
                    node["subtitle"] = subtitle
                else:
                    node["title"] = s.get("title", key)
                    node["subtitle"] = ""
                # Prefer native description (rich, with format examples for ws_*);
                # fall back to curated _PAGE_DESC when native contains unresolved
                # template placeholders (e.g. user_mapping's {cn_im_hub_status}).
                import re as _re
                native_desc = s.get("description")
                desc = native_desc or _PAGE_DESC.get(key, "")
                if _re.search(r"\{[a-zA-Z_]+\}", desc or ""):
                    fb = _PAGE_DESC.get(key, "")
                    if fb:
                        desc = fb
                node["description"] = desc
                if mo:
                    node["children"] = {k: _build_node(k, mo[k]) for k in mo}
                else:
                    data = s.get("data", {})
                    node["fields"] = [k for k in data if k not in NAV_KEYS]
                return node

            menu_tree = _build_node("init")

            # ── Infer types for real stored options (flat config values) ──
            option_types = {}
            slider_config = {}
            SKIP_KEYS = {"friendly_name", "icon", "skills", "docs", "plugins",
                         "user_mappings", "skills_count", "docs_count",
                         "plugins_count", "user_mappings_count"}

            for key, value in attrs.items():
                if key in SKIP_KEYS:
                    continue
                if isinstance(value, bool):
                    otype = "toggle"
                elif isinstance(value, str) and key == "conversation_mode":
                    otype = "mode_select"
                elif isinstance(value, str) and value in agent_ids:
                    otype = "agent_select"
                elif isinstance(value, str):
                    otype = "display"
                elif isinstance(value, (int, float)):
                    otype = "slider"
                    if key == "pipeline_timeout":
                        slider_config[key] = {"min": 5, "max": 360, "div60": True}
                    elif key == "max_tool_repeat":
                        slider_config[key] = {"min": 3, "max": 50}
                    elif isinstance(value, int) and value > 0:
                        slider_config[key] = {"min": 1, "max": max(value * 3, 30)}
                    else:
                        slider_config[key] = {"min": 0, "max": 100}
                else:
                    continue
                option_types[key] = otype

            # ── Ensure every field referenced in the menu tree is typed ──
            # (so the panel renders ALL controls even when the live sensor has
            #  no stored value, or the translation could not be read → fallback).
            all_fields = []
            def _collect(node):
                if "fields" in node:
                    all_fields.extend(node["fields"])
                for c in (node.get("children") or {}).values():
                    _collect(c)
            _collect(menu_tree)
            # 同时收集 _SECTION_DEFS 中的字段（如 enable_voice_fab 等自定义字段）
            for _secs in _SECTION_DEFS.values():
                for _sec in _secs:
                    all_fields.extend(_sec.get("fields", []))

            for key in all_fields:
                if key in option_types:
                    continue
                hint = FIELD_TYPE_HINTS.get(key)
                if hint:
                    option_types[key] = hint
                    if hint == "slider" and key not in slider_config:
                        slider_config[key] = {"min": 1, "max": 50}
                else:
                    # Unknown field without a hint → render as read-only display
                    option_types[key] = "display"

            # Read claw_assistant version from its manifest
            _ca_version = "unknown"
            _ca_path = _os.path.join(hass.config.config_dir,
                                     "custom_components", "claw_assistant", "manifest.json")
            if _os.path.isfile(_ca_path):
                try:
                    with open(_ca_path, encoding="utf-8") as _f:
                        _ca_version = _json.load(_f).get("version", "unknown")
                except Exception:
                    pass

            return web.json_response({
                "ok": True,
                "menu_tree": menu_tree,
                "field_meta": field_meta,
                "labels": field_meta,
                "option_types": option_types,
                "slider_config": slider_config,
                "sections": _SECTION_DEFS,
                "agents": agents,
                "selectors": trans.get("selector", {}) or _FALLBACK_SELECTORS,
                "claw_assistant_version": _ca_version,
                "_debug_trans_path": trans_path,
                "_debug_trans_found": trans_found,
            })

        # ─────────────────────────────────────────────────────────────
        # Skills (reuse native claw_assistant.runtime.storage.skill_store)
        # ─────────────────────────────────────────────────────────────
        if action == "get_skills":
            try:
                from custom_components.claw_assistant.runtime.storage.skill_store import (
                    list_installed_skills, _INTERNAL_SKILL_SLUGS,
                )
                skills = await hass.async_add_executor_job(list_installed_skills)
                items = []
                for s in skills:
                    slug = s.get("slug") or s.get("file") or s.get("name") or ""
                    if slug in _INTERNAL_SKILL_SLUGS:
                        continue
                    items.append({
                        "name": s.get("name", slug),
                        "slug": slug,
                        "file": s.get("file", slug + ".md"),
                        "chars": int(s.get("chars", 0) or 0),
                        "description": s.get("description", ""),
                        "version": s.get("version", ""),
                        "category": s.get("category", ""),
                    })
                return web.json_response({"ok": True, "skills": items})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "read_skill":
            slug = body.get("slug", "")
            try:
                from custom_components.claw_assistant.runtime.storage.skill_store import (
                    async_get_installed_skill, async_read_skill_markdown,
                )
                meta = await async_get_installed_skill(hass, slug)
                raw = await async_read_skill_markdown(hass, slug)
                return web.json_response({
                    "ok": True, "slug": slug,
                    "name": meta.get("name", slug),
                    "file": meta.get("file", slug + ".md"),
                    "description": meta.get("description", ""),
                    "chars": len(raw), "content": raw,
                })
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "save_skill":
            slug = body.get("slug", "")
            content = body.get("content", "")
            try:
                from custom_components.claw_assistant.runtime.storage.skill_store import async_install_skill
                await async_install_skill(hass, slug, content, overwrite=True,
                                          actor="claw_plus", reason="edited via control panel")
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "delete_skill":
            slug = body.get("slug", "")
            try:
                from custom_components.claw_assistant.runtime.storage.skill_store import async_delete_skill
                await async_delete_skill(hass, slug, actor="claw_plus", reason="deleted via control panel")
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ─────────────────────────────────────────────────────────────
        # Plugins (reuse native claw_assistant.runtime.storage.plugin_store)
        # ─────────────────────────────────────────────────────────────
        if action == "get_plugins":
            try:
                from custom_components.claw_assistant.runtime.storage.plugin_store import list_installed_plugins
                plugins = await hass.async_add_executor_job(list_installed_plugins)
                items = []
                for p in plugins:
                    valid = p.get("valid", True)
                    loaded = p.get("loaded", False)
                    load_error = p.get("load_error")
                    if not valid:
                        status = "INVALID"
                    elif loaded:
                        status = "RUNNING"
                    elif load_error:
                        status = "FAILED"
                    else:
                        status = "STOPPED"
                    items.append({
                        "name": p.get("name", ""),
                        "key": p.get("key", ""),
                        "version": p.get("version", ""),
                        "description": p.get("description", ""),
                        "author": p.get("author", ""),
                        "valid": valid, "loaded": loaded,
                        "load_error": load_error,
                        "tools_count": p.get("tools_count", 0),
                        "status": status,
                    })
                return web.json_response({"ok": True, "plugins": items})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "plugin_enable":
            key = body.get("key", "")
            try:
                from custom_components.claw_assistant.runtime.storage.plugin_store import hot_load_plugin
                r = await hass.async_add_executor_job(hot_load_plugin, hass, key)
                return web.json_response({"ok": bool(r.get("success", False)), "result": r})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "plugin_disable":
            key = body.get("key", "")
            try:
                from custom_components.claw_assistant.runtime.storage.plugin_store import hot_unload_plugin
                r = await hass.async_add_executor_job(hot_unload_plugin, hass, key)
                return web.json_response({"ok": bool(r.get("success", False)), "result": r})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "plugin_delete":
            key = body.get("key", "")
            try:
                import shutil
                from custom_components.claw_assistant.runtime.storage.plugin_store import hot_unload_plugin
                from custom_components.claw_assistant.plugins import plugins_dir
                await hass.async_add_executor_job(hot_unload_plugin, hass, key)
                pp = await hass.async_add_executor_job(plugins_dir)
                await hass.async_add_executor_job(shutil.rmtree, str(pp / key), True)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ─────────────────────────────────────────────────────────────
        # Workspace docs (reuse native claw_assistant.runtime.storage.workspace_store)
        # ─────────────────────────────────────────────────────────────
        if action == "get_docs":
            try:
                from custom_components.claw_assistant.runtime.storage.workspace_store import get_workspace_doc
                items = []
                for name in _DOC_ORDER:
                    doc = await hass.async_add_executor_job(get_workspace_doc, name)
                    md = doc.get("markdown") or ""
                    items.append({
                        "name": name,
                        "title": name.capitalize(),
                        "desc": _DOC_INFO.get(name, ""),
                        "has_content": bool(md.strip()),
                        "chars": len(md),
                        "active": doc.get("active", True),
                    })
                return web.json_response({"ok": True, "docs": items})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "read_doc":
            name = body.get("name", "")
            try:
                from custom_components.claw_assistant.runtime.storage.workspace_store import get_workspace_doc
                doc = await hass.async_add_executor_job(get_workspace_doc, name)
                return web.json_response({
                    "ok": True, "name": name,
                    "content": doc.get("markdown", ""),
                    "active": doc.get("active", True),
                })
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "save_doc":
            name = body.get("name", "")
            content = body.get("content", "")
            try:
                from custom_components.claw_assistant.runtime.storage.workspace_store import async_save_workspace_doc
                await async_save_workspace_doc(hass, name, content)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "set_bootstrap":
            active = bool(body.get("active", False))
            try:
                from custom_components.claw_assistant.runtime.storage.workspace_store import async_set_bootstrap_active
                await async_set_bootstrap_active(hass, active)
                return web.json_response({"ok": True})
            except Exception as e:
                return web.json_response({"error": str(e)})

        # ─────────────────────────────────────────────────────────────
        # User mappings (reuse native MappingStore + im_channel_helpers)
        # ─────────────────────────────────────────────────────────────
        if action == "get_mappings":
            try:
                from custom_components.claw_assistant.runtime.storage.user_mapping import MappingStore
                mappings = await hass.async_add_executor_job(MappingStore.load)
                return web.json_response({"ok": True, "mappings": mappings})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "add_mapping":
            provider = body.get("provider", "")
            ext_id = body.get("ext_id", "")
            ha_user_id = body.get("ha_user_id", "")
            try:
                from custom_components.claw_assistant.runtime.storage.user_mapping import MappingStore
                ok = await hass.async_add_executor_job(MappingStore.set, provider, ext_id, ha_user_id)
                return web.json_response({"ok": bool(ok)})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "remove_mapping":
            provider = body.get("provider", "")
            ext_id = body.get("ext_id", "")
            try:
                from custom_components.claw_assistant.runtime.storage.user_mapping import MappingStore
                ok = await hass.async_add_executor_job(MappingStore.remove, provider, ext_id)
                return web.json_response({"ok": bool(ok)})
            except Exception as e:
                return web.json_response({"error": str(e)})

        if action == "get_mapping_options":
            provider = body.get("provider") or None
            try:
                from custom_components.claw_assistant.runtime.storage.im_channel_helpers import (
                    get_configured_provider_keys, collect_provider_targets, build_ext_id_options,
                )
                provider_keys = await get_configured_provider_keys(hass)
                targets = await collect_provider_targets(hass)
                ext_options = {}
                if provider:
                    ext_options = await hass.async_add_executor_job(
                        lambda: build_ext_id_options(hass, provider, targets, manual_label="手动填写 ID"))
                users = []
                for u in await hass.auth.async_get_users():
                    users.append({"id": u.id, "name": u.name or u.id[:12], "is_active": u.is_active})
                return web.json_response({
                    "ok": True, "provider_keys": provider_keys,
                    "targets": targets, "ext_options": ext_options, "users": users,
                })
            except Exception as e:
                return web.json_response({"error": str(e)})

        return web.json_response({"error": "unknown_action"})



def _escape_html(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _b(v) -> bool:
    return v is True or v == "true" or v == 1 or v == "1"


def _build_interactive_html(data: dict) -> str:
    """Build the dashboard matching the original Claw Control design (full-page layout with sidebar + color-coded card grid).

    The original is a 62KB html-pro-card in lovelace.claw_control that uses
    hass.callService/WS. We adapt its design for the iframe panel: initial data
    is server-rendered, and JS uses POST fetch for live interactions.
    """
    o = data["attrs"]
    agents = data["agents"]
    skills = data["skills"]
    docs = data["docs"]
    plugins = data["plugins"]

    def _b(v):
        return v is True or v == "true" or v == 1 or v == "1"

    def eh(t):
        return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

    # Pre-build agent options HTML
    def agent_opts(sel):
        h = '<option value="">(自动)</option>'
        for a in agents:
            sel_a = ' selected' if sel == f"conversation.{a}" else ""
            h += f'<option value="conversation.{a}"{sel_a}>{eh(a)}</option>'
        return h

    # Pre-build toggle row HTML (reusable)
    def tg_html(key, name, desc, icon, checked):
        chk = ' checked' if _b(o.get(key, checked)) else ''
        return (f'<div class="tr"><div class="tr-info"><div class="tr-nm">'
                f'<ha-icon icon="{icon}"></ha-icon>{eh(name)}</div>'
                f'<div class="tr-ds">{eh(desc)}</div></div>'
                f'<label class="tg"><input type="checkbox"{chk} data-claw-key="{key}">'
                f'<span class="tg-sl"></span></label></div>')

    def fmt_size(b):
        if not b: return '0B'
        if b < 1024: return str(b)+'B'
        if b < 1048576: return f"{b/1024:.1f}KB"
        return f"{b/1048576:.1f}MB"

    cm = o.get("conversation_mode", "add_name") or "add_name"

    return f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claw Assistant</title>
<style>
.cp{{height:100vh;display:flex;flex-direction:column;background:var(--primary-background-color);font-family:var(--primary-font-family);overflow:hidden}}
.top{{flex-shrink:0;display:flex;align-items:center;justify-content:space-between;padding:18px 28px;background:var(--card-background-color);border-bottom:1px solid var(--divider-color)}}
.brand{{display:flex;align-items:center;gap:14px}}
.brand h1{{margin:0;font-size:1.5rem;font-weight:700;color:var(--primary-text-color)}}
.brand p{{margin:0;font-size:.9rem;color:var(--secondary-text-color)}}
.si{{display:flex;align-items:center;gap:10px}}
.si-dot{{width:10px;height:10px;border-radius:50%;animation:pulse 2s infinite}}
.si-on{{background:#4CAF50}}.si-off{{background:var(--secondary-text-color)}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.5}}}}
.si-txt{{font-size:.9rem;font-weight:500}}
.main{{flex:1;display:flex;overflow-y:auto}}
.side{{width:250px;flex-shrink:0;background:var(--secondary-background-color);border-right:1px solid var(--divider-color);display:flex;flex-direction:column}}
.side-sec{{padding:16px;border-bottom:1px solid var(--divider-color)}}
.side-t{{font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:var(--secondary-text-color);margin-bottom:10px}}
.sb{{display:flex;align-items:center;gap:10px;padding:10px;border-radius:8px;margin-bottom:6px}}
.sb:last-child{{margin-bottom:0}}
.sb-ic{{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;flex-shrink:0}}
.sb-ic.blue{{background:rgba(33,150,243,.15);color:#2196F3}}
.sb-ic.green{{background:rgba(76,175,80,.15);color:#4CAF50}}
.sb-ic.orange{{background:rgba(255,152,0,.15);color:#FF9800}}
.sb-ic.purple{{background:rgba(156,39,176,.15);color:#9C27B0}}
.sb-ic.teal{{background:rgba(38,166,154,.15);color:#26A69A}}
.sb-ic ha-icon{{--mdc-icon-size:18px}}
.sb-info{{flex:1;min-width:0}}
.sb-val{{font-size:1.1rem;font-weight:700;color:var(--primary-text-color);overflow-wrap:break-word;word-break:break-word}}
.sb-lbl{{font-size:.78rem;color:var(--secondary-text-color)}}
.qa{{display:flex;flex-direction:column;gap:6px}}
.qb{{display:flex;align-items:center;gap:8px;padding:10px 14px;border-radius:8px;border:1px solid var(--divider-color);background:var(--primary-background-color);color:var(--primary-text-color);cursor:pointer;transition:all .15s;font-size:.85rem;font-weight:500;text-align:left;font-family:inherit}}
.qb:hover{{border-color:var(--primary-color);background:rgba(var(--rgb-primary-color),.05);transform:translateX(3px)}}
.qb ha-icon{{--mdc-icon-size:18px;color:var(--primary-color)}}
.cnt{{flex:1;display:flex;flex-direction:column;padding:16px;gap:14px}}
.cols{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}
.cd{{background:var(--card-background-color);border-radius:10px;border:1px solid var(--divider-color);overflow:hidden;display:flex;flex-direction:column}}
.cd.c-blue{{border-top:3px solid #5B9BD5}}.cd.c-blue .ch{{background:linear-gradient(135deg,transparent,rgba(91,155,213,.08))}}
.cd.c-green{{border-top:3px solid #4CAF50}}.cd.c-green .ch{{background:linear-gradient(135deg,transparent,rgba(76,175,80,.08))}}
.cd.c-purple{{border-top:3px solid #AB47BC}}.cd.c-purple .ch{{background:linear-gradient(135deg,transparent,rgba(171,71,188,.08))}}
.cd.c-orange{{border-top:3px solid #FF9800}}.cd.c-orange .ch{{background:linear-gradient(135deg,transparent,rgba(255,152,0,.08))}}
.cd.c-teal{{border-top:3px solid #26A69A}}.cd.c-teal .ch{{background:linear-gradient(135deg,transparent,rgba(38,166,154,.08))}}
.cd.c-red{{border-top:3px solid #EF5350}}.cd.c-red .ch{{background:linear-gradient(135deg,transparent,rgba(239,83,80,.08))}}
.cd.c-indigo{{border-top:3px solid #5C6BC0}}.cd.c-indigo .ch{{background:linear-gradient(135deg,transparent,rgba(92,107,192,.08))}}
.cd.c-amber{{border-top:3px solid #FFB300}}.cd.c-amber .ch{{background:linear-gradient(135deg,transparent,rgba(255,179,0,.08))}}
.cd.c-cyan{{border-top:3px solid #00BCD4}}.cd.c-cyan .ch{{background:linear-gradient(135deg,transparent,rgba(0,188,212,.08))}}
.ch{{padding:12px 16px;border-bottom:1px solid var(--divider-color);display:flex;align-items:center;gap:8px}}
.ch ha-icon{{--mdc-icon-size:16px;color:var(--primary-color)}}
.ch span{{font-size:.92rem;font-weight:600;color:var(--primary-text-color)}}
.cb{{padding:10px 14px;flex:1}}
.ar{{padding:7px 0}}.ar+.ar{{border-top:1px solid var(--divider-color)}}
.al{{font-size:.82rem;font-weight:600;color:var(--primary-text-color);display:flex;align-items:center;gap:5px;margin-bottom:2px}}
.al ha-icon{{--mdc-icon-size:14px;color:var(--primary-color)}}
.ad{{font-size:.72rem;color:var(--secondary-text-color);margin-bottom:3px}}
.sel{{width:100%;padding:7px 10px;border-radius:6px;border:1px solid var(--divider-color);background:var(--primary-background-color);color:var(--primary-text-color);font-size:.82rem;cursor:pointer;font-family:inherit;-webkit-appearance:none;appearance:none}}
.sel:focus{{outline:none;border-color:var(--primary-color)}}
.tg{{position:relative;width:36px;height:20px;cursor:pointer;display:inline-block;flex-shrink:0}}
.tg input{{opacity:0;width:0;height:0;position:absolute}}
.tg-sl{{position:absolute;inset:0;background:var(--divider-color);border-radius:10px;transition:background .25s}}
@media(prefers-color-scheme:dark){{.tg-sl{{background:#3a3a3c}}}}
.tg input:checked+.tg-sl{{background:var(--primary-color);opacity:.85}}
.tg-sl:before{{position:absolute;content:'';height:16px;width:16px;left:2px;bottom:2px;background:#fff;border-radius:50%;transition:.25s cubic-bezier(.4,0,.2,1);box-shadow:0 1px 2px rgba(0,0,0,.15)}}
.tg input:checked+.tg-sl:before{{transform:translateX(16px)}}
.tr{{display:flex;align-items:center;justify-content:space-between;padding:6px 8px;border-radius:6px;background:var(--secondary-background-color);margin-bottom:4px;gap:8px}}
.tr:last-child{{margin-bottom:0}}
.tr-info{{flex:1;min-width:0}}
.tr-nm{{font-size:.76rem;font-weight:500;display:flex;align-items:center;gap:5px;color:var(--primary-text-color)}}
.tr-nm ha-icon{{--mdc-icon-size:14px;color:var(--primary-color)}}
.tr-ds{{font-size:.62rem;color:var(--secondary-text-color)}}
.mb{{display:flex;gap:3px;flex-wrap:wrap}}
.mbtn{{padding:5px 12px;border-radius:6px;border:1px solid var(--divider-color);font-size:.78rem;font-weight:500;cursor:pointer;transition:all .15s;background:transparent;color:var(--primary-text-color);font-family:inherit}}
.mbtn.on{{background:var(--primary-color);color:#fff;border-color:var(--primary-color)}}
.mbtn:hover:not(.on){{border-color:var(--primary-color)}}
.tg2{{display:grid;grid-template-columns:1fr 1fr;gap:6px}}
.bot{{flex-shrink:0;display:flex;align-items:center;justify-content:space-between;padding:12px 24px;background:var(--card-background-color);border-top:1px solid var(--divider-color)}}
.fi{{display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.fi-item{{display:flex;align-items:center;gap:4px;font-size:.8rem;color:var(--secondary-text-color)}}
.fi-item ha-icon{{--mdc-icon-size:14px}}
.toast{{position:fixed;bottom:60px;left:50%;transform:translateX(-50%);padding:8px 20px;border-radius:10px;font-size:.82rem;font-weight:500;z-index:1000;transition:opacity .3s;pointer-events:none;background:rgba(0,0,0,.8);color:#fff}}
@media(max-width:1200px){{.side{{width:200px}}.cols{{grid-template-columns:1fr 1fr}}}}
@media(max-width:768px){{.top{{padding:12px 14px}}.brand h1{{font-size:1rem}}.brand p{{display:none}}.main{{flex-direction:column}}.side{{width:100%;border-right:none;border-bottom:1px solid var(--divider-color)}}.cnt{{padding:10px;gap:10px}}.cols{{grid-template-columns:1fr}}.bot{{flex-direction:column;gap:8px;padding:10px 14px}}}}
</style>
</head>
<body>
<div class="cp">
  <div class="top">
    <div class="brand">
      <div><h1>Claw Assistant</h1><p>智能助手控制面板</p></div>
    </div>
    <div class="si">
      <div class="si-dot si-on" id="sDot"></div>
      <span class="si-txt" id="sTxt">系统在线</span>
      <button class="qb" onclick="refreshNow()" style="margin-left:8px;padding:4px 8px;font-size:.7rem">
        <ha-icon icon="mdi:refresh"></ha-icon><span>刷新</span>
      </button>
    </div>
  </div>
  <div class="main">
    <div class="side">
      <div class="side-sec">
        <div class="side-t">状态概览</div>
        <div class="sb"><div class="sb-ic blue"><ha-icon icon="mdi:brain"></ha-icon></div><div class="sb-info"><div class="sb-val" id="svPa">-</div><div class="sb-lbl">主力智能体</div></div></div>
        <div class="sb"><div class="sb-ic purple"><ha-icon icon="mdi:swap-horizontal"></ha-icon></div><div class="sb-info"><div class="sb-val" id="svFa">-</div><div class="sb-lbl">备用智能体</div></div></div>
        <div class="sb"><div class="sb-ic green"><ha-icon icon="mdi:toggle-switch-outline"></ha-icon></div><div class="sb-info"><div class="sb-val" id="svOn">-</div><div class="sb-lbl">已启用功能</div></div></div>
      </div>
      <div class="side-sec">
        <div class="side-t">资源概览</div>
        <div class="sb"><div class="sb-ic teal"><ha-icon icon="mdi:lightning-bolt"></ha-icon></div><div class="sb-info"><div class="sb-val" id="svSk">-</div><div class="sb-lbl">已安装技能</div></div></div>
        <div class="sb"><div class="sb-ic orange"><ha-icon icon="mdi:file-document-outline"></ha-icon></div><div class="sb-info"><div class="sb-val" id="svDc">-</div><div class="sb-lbl">工作区文档</div></div></div>
        <div class="sb"><div class="sb-ic purple"><ha-icon icon="mdi:puzzle"></ha-icon></div><div class="sb-info"><div class="sb-val" id="svPg">-</div><div class="sb-lbl">已安装插件</div></div></div>
      </div>
    </div>
    <div class="cnt" id="body"><div class="cols" id="cardGrid"></div></div>
  </div>
  <div class="bot">
    <div class="fi">
      <div class="fi-item"><ha-icon icon="mdi:information"></ha-icon><span>Claw Dashboard v1.3</span></div>
      <div class="fi-item"><ha-icon icon="mdi:clock"></ha-icon><span id="btTime">-</span></div>
      <div class="fi-item"><ha-icon icon="mdi:lightning-bolt"></ha-icon><span id="btRes">-</span></div>
    </div>
  </div>
</div>
<div id="toast" class="toast" style="opacity:0"></div>

<script>
var API = window.location.pathname;
var _data = {json.dumps(data)};

function $(id) {{ return document.getElementById(id); }}
function b(v) {{ return v===true || v==='true' || v===1 || v==='1'; }}

function agName(eid) {{
  if (!eid) return '未设置';
  var a = _data.agents;
  for (var i=0; i<a.length; i++) {{ if ('conversation.'+a[i]===eid) return a[i]; }}
  return eid.replace('conversation.','');
}}

function agList(sel) {{
  var h = '<option value="">(自动)</option>';
  var a = _data.agents;
  for (var i=0; i<a.length; i++) {{
    var eid = 'conversation.'+a[i];
    h += '<option value="'+eid+'"'+(sel===eid?' selected':'')+'>'+a[i]+'</option>';
  }}
  return h;
}}

function setOpt(key, value) {{
  fetch(API, {{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{action:'set_option',key:key,value:value}})
  }}).then(function(r){{return r.json()}}).then(function(d){{if(d.error)console.error(d.error)}}).catch(function(e){{}});
  var t=$('toast'); if(t){{t.textContent=key+' = '+value;t.style.opacity='1';setTimeout(function(){{t.style.opacity='0'}},2000)}}
}}

function tgHtml(key,name,desc,icon,checked) {{
  return '<div class="tr"><div class="tr-info"><div class="tr-nm"><ha-icon icon="'+icon+'"></ha-icon>'+name+'</div><div class="tr-ds">'+desc+'</div></div><label class="tg"><input type="checkbox"'+(checked?' checked':'')+' data-claw-key="'+key+'"><span class="tg-sl"></span></label></div>';
}}

function fmtSize(v){{if(!v)return'0B';if(v<1024)return v+'B';if(v<1048576)return(v/1024).toFixed(1)+'KB';return(v/1048576).toFixed(1)+'MB'}}
function eh(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\x22/g,'&quot;')}}

function buildGrid(o) {{
  var sk = o.skills||[], dc = o.docs||[], pg = o.plugins||[];
  var cm = o.conversation_mode||'add_name';
  var h = '<div class="cols">';

  // Col 1: AI / 对话 / 通知
  h += '<div class="cd c-blue"><div class="ch"><ha-icon icon="mdi:brain"></ha-icon><span>AI 智能体</span></div><div class="cb">';
  h += '<div class="ar"><div class="al"><ha-icon icon="mdi:star"></ha-icon>主力智能体</div><select class="sel" data-claw-key="primary_agent">'+agList(o.primary_agent)+'</select></div>';
  h += '<div class="ar"><div class="al"><ha-icon icon="mdi:swap-horizontal"></ha-icon>备用智能体</div><select class="sel" data-claw-key="fallback_agent">'+agList(o.fallback_agent)+'</select></div>';
  h += '<div class="ar"><div class="al"><ha-icon icon="mdi:source-branch"></ha-icon>二级备用</div><select class="sel" data-claw-key="secondary_fallback_agent">'+agList(o.secondary_fallback_agent)+'</select></div>';
  h += '</div></div>';

  h += '<div class="cd c-indigo"><div class="ch"><ha-icon icon="mdi:cog"></ha-icon><span>对话设置</span></div><div class="cb">';
  h += '<div class="ar"><div class="al"><ha-icon icon="mdi:format-text"></ha-icon>对话模式</div><div class="mb">';
  [['no_name','简单'],['add_name','带名字'],['detailed','详细']].forEach(function(m){{h+='<button class="mbtn'+(cm===m[0]?' on':'')+'" data-claw-mode="'+m[0]+'">'+m[1]+'</button>'}});
  h += '</div></div>';
  h += tgHtml('continuous_conversation','持续对话','保持上下文连续性','mdi:repeat',b(o.continuous_conversation));
  h += tgHtml('enable_web_search','联网搜索','对话中自动搜索网络','mdi:web',b(o.enable_web_search));
  h += '</div></div>';

  h += '<div class="cd c-purple"><div class="ch"><ha-icon icon="mdi:bell-outline"></ha-icon><span>通知与追踪</span></div><div class="cb"><div class="tg2">';
  h += tgHtml('enable_sidebar_dock','侧边栏','侧边栏停靠','mdi:dock-left',b(o.enable_sidebar_dock));
  h += tgHtml('enable_context_status_bar','状态栏','上下文状态栏','mdi:information-outline',b(o.enable_context_status_bar));
  h += tgHtml('enable_sound_notifications','声音','声音通知','mdi:bell-ring-outline',b(o.enable_sound_notifications));
  h += tgHtml('enable_activity_tracking','追踪','活动追踪','mdi:chart-timeline-variant',b(o.enable_activity_tracking));
  h += '</div></div></div>';

  // Col 2: 功能 / 其他 / 扩展
  h += '<div class="cd c-amber"><div class="ch"><ha-icon icon="mdi:toggle-switch"></ha-icon><span>功能开关</span></div><div class="cb">';
  h += tgHtml('enable_file_upload','文件上传','允许上传文件给 AI','mdi:file-upload-outline',b(o.enable_file_upload));
  h += tgHtml('enable_rich_markdown','富文本 Markdown','美化 Markdown 渲染','mdi:markdown',b(o.enable_rich_markdown));
  h += tgHtml('enable_tool_details','工具详情','显示工具调用细节','mdi:code-json',b(o.enable_tool_details));
  h += tgHtml('enable_tool_progress','工具进度','显示工具执行进度','mdi:progress-check',b(o.enable_tool_progress));
  h += tgHtml('enable_streaming_effect','流式效果','AI 实时流式输出','mdi:waveform',b(o.enable_streaming_effect));
  h += '</div></div>';

  h += '<div class="cd c-orange"><div class="ch"><ha-icon icon="mdi:tune-vertical"></ha-icon><span>运行时参数</span></div><div class="cb">';
  [{{key:'max_tool_repeat',lbl:'\u6700\u5927\u5de5\u5177\u91cd\u590d\u6b21\u6570',min:3,max:50}},{{key:'identical_call_warn',lbl:'\u76f8\u540c\u8c03\u7528\u8b66\u544a\u9608\u503c',min:5,max:30}},{{key:'identical_call_stop',lbl:'\u76f8\u540c\u8c03\u7528\u505c\u6b62\u9608\u503c',min:5,max:30}},{{key:'pipeline_timeout',lbl:'\u6d41\u6c34\u7ebf\u8d85\u65f6(\u5206\u949f)',min:5,max:360}}].forEach(function(f){{
    var raw=parseInt(o[f.key]);if(isNaN(raw))raw=f.key==='pipeline_timeout'?120:10;
    var dv=f.key==='pipeline_timeout'?Math.round(raw/60):raw;
    h += '<div style="margin-bottom:6px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px"><span style="font-size:.78rem;font-weight:500;color:var(--primary-text-color)">'+f.lbl+'</span><span style="font-size:.85rem;font-weight:700;color:var(--primary-color)" id="v_'+f.key+'">'+dv+'</span></div><input type="range" min="'+f.min+'" max="'+f.max+'" value="'+dv+'" data-claw-key="'+f.key+'" style="-webkit-appearance:none;width:100%;height:4px;border-radius:2px;background:var(--divider-color);outline:none" oninput="var l=document.getElementById(\'v_'+f.key+'\');if(l)l.textContent=this.value"></div>';
  }});
  h += '</div></div>';

  h += '<div class="cd c-red"><div class="ch"><ha-icon icon="mdi:plus-circle-outline"></ha-icon><span>扩展开关</span></div><div class="cb">';
  h += tgHtml('enable_streaming','流式输出','启用流式响应','mdi:play-circle-outline',b(o.enable_streaming));
  h += tgHtml('enable_voice_response','语音响应','语音播报回复','mdi:microphone',b(o.enable_voice_response));
  h += tgHtml('enable_auto_summary','自动摘要','自动生成对话摘要','mdi:text-summary',b(o.enable_auto_summary));
  h += tgHtml('enable_privacy_mode','隐私模式','不记录对话历史','mdi:incognito',b(o.enable_privacy_mode));
  h += tgHtml('enable_dark_mode','深色主题','跟随系统深色模式','mdi:theme-light-dark',b(o.enable_dark_mode));
  h += '</div></div>';

  // Col 3: 技能 / 文档 / 插件
  h += '<div class="cd c-green"><div class="ch"><ha-icon icon="mdi:lightning-bolt"></ha-icon><span>已安装技能</span></div><div class="cb">';
  if(sk.length){{h+='<select class="sel"><option value="">选择技能...</option>';sk.forEach(function(s){{h+='<option value="'+eh(s.path)+'">'+eh(s.name)+' ('+fmtSize(s.size)+')</option>'}});h+='</select>'}}
  else h += '<div style="font-size:.78rem;color:var(--secondary-text-color);padding:8px">暂无技能</div>';
  h += '</div></div>';

  h += '<div class="cd c-teal"><div class="ch"><ha-icon icon="mdi:puzzle"></ha-icon><span>已安装插件</span></div><div class="cb">';
  if(pg.length){{h+='<select class="sel"><option value="">选择插件...</option>';pg.forEach(function(p){{h+='<option value="">'+eh(p.name)+' ('+fmtSize(p.size)+')</option>'}});h+='</select>'}}
  else h += '<div style="font-size:.78rem;color:var(--secondary-text-color);padding:8px">暂无插件</div>';
  h += '</div></div>';

  h += '<div class="cd c-cyan"><div class="ch"><ha-icon icon="mdi:file-document-outline"></ha-icon><span>工作区文档</span></div><div class="cb">';
  if(dc.length){{h+='<select class="sel"><option value="">选择文档...</option>';dc.forEach(function(d){{h+='<option value="">'+eh(d.name)+' ('+fmtSize(d.size)+')</option>'}});h+='</select>'}}
  else h += '<div style="font-size:.78rem;color:var(--secondary-text-color);padding:8px">暂无文档</div>';
  h += '</div></div>';

  h += '</div>';
  return h;
}}

function updateAll(o) {{
  if (!o) return;
  // Sidebar stats
  var pa = $('svPa'); if(pa) pa.textContent = agName(o.primary_agent);
  var fa = $('svFa'); if(fa) fa.textContent = agName(o.fallback_agent);
  var on = $('svOn');
  if(on){{var tks=['enable_web_search','continuous_conversation','enable_streaming_effect','enable_sidebar_dock','enable_sound_notifications','enable_file_upload','enable_rich_markdown','enable_activity_tracking','enable_tool_details','enable_tool_progress','enable_context_status_bar'];on.textContent=tks.filter(function(k){{return b(o[k])}}).length+'/'+tks.length}}
  var sk=o.skills||[],dc=o.docs||[],pg=o.plugins||[];
  var e1=$('svSk');if(e1)e1.textContent=sk.length;
  var e2=$('svDc');if(e2)e2.textContent=dc.length;
  var e3=$('svPg');if(e3)e3.textContent=pg.length;
  // Footer
  var bt=$('btTime');if(bt)bt.textContent=new Date().toLocaleTimeString('zh-CN',{{hour:'2-digit',minute:'2-digit'}});
  var br=$('btRes');if(br)br.textContent=sk.length+'技能 / '+dc.length+'文档 / '+pg.length+'插件';
  // Card grid
  var grid=$('cardGrid');if(grid)grid.innerHTML=buildGrid(o);
}}

function refreshNow() {{
  fetch(API, {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:'read'}})}})
  .then(function(r){{return r.json()}})
  .then(function(d){{if(d.attrs){{_data=d;updateAll(d.attrs)}}}});
}}

// Initial render
updateAll(_data.attrs);

// Auto-refresh every 30s
setInterval(refreshNow, 30000);

// Event delegation for all interactive elements
document.addEventListener('change', function(e) {{
  var el = e.target;
  var key = el.getAttribute('data-claw-key');
  if (!key) return;
  if (el.tagName==='SELECT') setOpt(key, el.value);
  else if (el.tagName==='INPUT'&&el.type==='checkbox') setOpt(key, el.checked);
  else if (el.tagName==='INPUT'&&el.type==='range') {{
    var v = parseInt(el.value);
    if (key==='pipeline_timeout') v = v * 60;
    setOpt(key, v);
  }}
}});

document.addEventListener('click', function(e) {{
  var mb = e.target.closest('[data-claw-mode]');
  if (mb) {{
    var m = mb.getAttribute('data-claw-mode');
    var bar = mb.parentElement;
    bar.querySelectorAll('.mbtn').forEach(function(b){{b.classList.remove('on')}});
    mb.classList.add('on');
    setOpt('conversation_mode', m);
    return;
  }}
}});
</script>
</body>
</html>"""


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # 清除 Voice FAB JS 注入
    frontend.remove_extra_js_url(hass, _VFAB_URL)
    from homeassistant.config_entries import ConfigEntryState
    vf_entries = hass.config_entries.async_entries("voice_fab")
    if any(e.state is ConfigEntryState.LOADED for e in vf_entries):
        await hass.services.async_call("voice_fab", "set_fab_enabled", {"enabled": False})

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok