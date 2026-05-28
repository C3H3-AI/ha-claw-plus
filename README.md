# Claw Dashboard Config

[Claw Assistant](https://github.com/ha-china/ha_claw) 的仪表盘配置面板。

通过此集成，你可以在 Home Assistant 仪表盘上直接查看和修改 Claw Assistant 的全部配置选项，
无需进入集成配置流。

## 功能

- **sensor.claw_config** — 以传感器属性形式展示 Claw Assistant 当前配置
- **claw_plus.set_option** — 服务，用于修改任意 Claw Assistant 配置项（key/value）

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

配合 `html-pro-card`（ha-china/ha-card-pro）仪表盘卡片使用，效果最佳。

卡片模板示例：参见 [claw_dashboard_card.yaml](docs/claw_dashboard_card.yaml)

也可在自动化中直接调用服务：

```yaml
service: claw_plus.set_option
data:
  key: enable_web_search
  value: true
```

## 依赖

- Home Assistant 2025.1 或更高版本
- 已安装并配置 [Claw Assistant](https://github.com/ha-china/ha_claw) 集成