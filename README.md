# Claw Dashboard Config

[Claw Assistant](https://github.com/ha-china/ha_claw) 的仪表盘配置面板。

通过此集成，你可以在 Home Assistant 仪表盘上直接查看和修改 Claw Assistant 的全部配置选项，
无需进入集成配置流。

## 功能

### 传感器

- **sensor.claw_config** — 以传感器属性形式展示 Claw Assistant 当前配置

### 服务

| 服务 | 说明 |
|------|------|
| `claw_plus.set_option` | 修改任意 Claw Assistant 配置项（key/value） |
| `claw_plus.list_workspace` | 列出工作区资源（skills / docs / plugins） |
| `claw_plus.read_workspace_file` | 读取工作区文件内容 |
| `claw_plus.write_workspace_file` | 写入工作区文件 |

## 安装

### HACS（推荐）

1. 在 HACS 中添加此仓库为自定义仓库
2. 搜索 "Claw Dashboard Config" 并安装
3. 重启 Home Assistant

### 手动安装

1. 将 `custom_components/claw_plus` 目录复制到 Home Assistant 的 `custom_components` 目录
2. 重启 Home Assistant

## 配置

1. 前往 **设置 → 设备与服务 → 添加集成**
2. 搜索 "Claw Dashboard Config" 并添加
3. 集成会自动注册，无需额外配置

## 使用

### 仪表盘

配合 `html-pro-card`（ha-china/ha-card-pro）使用，效果最佳。

最新仪表盘卡片代码已内置于 `custom_components/claw_plus/dashboard.html`。
可直接作为 `custom:html-pro-card` 的 `content` 字段使用。



### 自动化示例

```yaml
service: claw_plus.set_option
data:
  key: enable_web_search
  value: true
```

```yaml
service: claw_plus.list_workspace
data:
  category: all
```

```yaml
service: claw_plus.read_workspace_file
data:
  path: SOUL.md
```

```yaml
service: claw_plus.write_workspace_file
data:
  path: notes.md
  content: |
    # My Notes
    Some content here
```

## 依赖

- Home Assistant 2025.1 或更高版本
- 已安装并配置 [Claw Assistant](https://github.com/ha-china/ha_claw) 集成

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.1.0 | 2026-05-29 | 新增 list_workspace / read_workspace_file / write_workspace_file 服务；内嵌仪表盘卡片代码 |
| 1.0.0 | 2026-05-28 | 初始版本，sensor.claw_config + set_option 服务 |
