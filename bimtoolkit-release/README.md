# BIMToolkit v0.1 安装包

BIM 工具箱 + AI 智建 + Revit MCP Agent 接口（Revit 2019 / pyRevit 6.x 实测）。

## 包含

| 组件 | 说明 |
|------|------|
| `BIMToolkit.extension/` | pyRevit 扩展：BIM 工具（体检/统计/批量改参/DWG翻模等）+ AI 智建（一键建模/一键演示/规范审查/自动标注等）+ MCP Bridge v7.5 |
| `revit-mcp/` | MCP 标准网关（CPython3 stdio JSON-RPC 2.0，零依赖） |
| `install.bat` | 一键安装器 |
| `configure_mcp.py` | WorkBuddy `mcp.json` 合并写入器 |

## 安装

双击 `install.bat`。自动完成：

1. 检查 Revit 2019-2024 / pyRevit（未装会给出指引）
2. 部署扩展到 `%APPDATA%\pyRevit\Extensions\BIMToolkit.extension`
3. 部署网关到 `%APPDATA%\BIMToolkit\revit-mcp\`，无 Python3 时自动下载嵌入式 Python（python.org 主源 + npmmirror 备用源）
4. 合并写入 `~/.workbuddy/mcp.json`（原配置自动备份 `.bak`）

## 首次使用

1. 重启 Revit → 工具栏「BIMToolkit」选项卡
2. 点「MCP Bridge」按钮 → 日志出现 `v7.5 ready`（**pyRevit Reload 后必须重点此按钮**）
3. WorkBuddy 连接器管理页 → Trust `revit`
4. 对话里说：「在 Revit 里建一面 3000×200×3000 的墙」

## 安全说明

- 桥只监听 `127.0.0.1:9877`，token 鉴权（首启自动生成 `bridge.token`）
- AI 写操作仅限原子工具（create_walls / set_parameters），单事务 + 提交核对 + 自动回滚
- 每条命令落审计日志 `mcp_audit.jsonl`
- 任意代码执行（execute_code）**默认关闭**，开关在 `mcp_bridge_config.json`

## 已知限制

- 仅 Revit 2019 充分验证；2020+ 未经测试（IPY 语义差异风险）
- 单 Revit 实例；第二个实例点按钮会明确报 bind 失败
- pyRevit Reload 会停止桥，Reload 后需重新点击 MCP Bridge 按钮
- 自动标注尺寸链为 N-1 段（末段墙长不在链内，Revit API 限制，详见 README 已知边界）
