# Revit「BIM 工具箱」现状调查与功能增强方案

- 调查日期：2026-10-01
- 调查对象：`E:\bim-toolkit`（Revit 2019 + pyRevit 6.5.5 扩展 `BIMToolkit`）
- 调查方式：DSH 主线测绘 + 三路外部 AI 只读审计（WorkBuddy / Qoder CN / Claude Code）+ 逐条回源核验
- 报告结论口径：**所有结论均带 `文件:行` 证据；外部 AI 的结论一律回源复核，已纠正 3 处误判**

---

## 0. 摘要

**一句话**：这套工具箱的真实能力远超它自己的文档——但它现在有 **4 个必须先堵的洞**，
否则任何功能增强都是在流沙上盖楼。

| 维度 | 现状 |
|---|---|
| 产品形态 | pyRevit 扩展，2 个面板 / **19 个按钮**，另带 revit-mcp 网关与 MCP 适配器 |
| 真实能力 | 只读统计体检 + 批量改参 + DWG 翻模 + AI 代码生成 + **35 条 MCP 命令**（外部 AI 可直接驱动 Revit） |
| 文档口径 | README 只写了 **7 个按钮**，与实际的 19 个相差 12 个 |
| 最大资产 | 把 Revit 变成了一个可被 AI 编程驱动的「建筑操作系统」（bridge + MCP） |
| 最大风险 | **明文 API Key 已进 git**；`execute_code` 全开 + token 明文 = 本机 RCE；部署会删运行时审计 |
| 最要紧的漏洞 | **已部署副本落后于仓库**（`dwg_to_json.py` v2.9 vs v2.12），而桌面全部启动器指向已部署副本 |

**三条最值得先做的事**：
1. 撤销并轮换泄露的 DeepSeek Key，把它从 git 历史和部署产物里彻底摘出去（半天）。
2. 把部署收敛成**唯一一条**、且不破坏运行时状态的通道，并让「装的是什么版本」可验证（一天）。
3. 让自动测试真正覆盖 19 个按钮（现在只覆盖 7 个，且有 3 处"永远通过"的假绿灯）（两~三天）。

---

## 1. 调查方法与调度记录

### 1.1 外部算力调度

桌面 `%USERPROFILE%\Desktop\桌面工具\` 下的 `.bat` 是**转发器**，用绝对路径调用
`E:\py-tools\policy_monitor\tools\agent_runners\run_*.ps1`。开工前先跑体检：

```
E:\py-tools\venv\Scripts\python.exe ...\agent_runners\check_agents.py
→ WorkBuddy PING / Claude Code PING / Qoder CN PONG   全部在线
```

三路各领一份**只读**任务书（`-ReadOnly` 会硬性收窄工具集为 `Read,Grep,Glob`）：

| 通道 | 任务书 | 分工依据 | 耗时 | 状态 |
|---|---|---|---|---|
| WorkBuddy（腾讯额度，不花 DeepSeek） | `_survey\T1b_按钮清点_WorkBuddy.md` | 粗活：19 按钮全量清点 | 284.8 s | ✅ |
| Qoder CN（独立账号） | `_survey\T2_链路与安全_Qoder.md` | 完整链路 + 安全边界 | 158.4 s | ✅ |
| Claude Code（同 DeepSeek Key） | `_survey\T3b_部署与测试_Claude.md` | 机制级复核：部署/测试有效性 | 276.3 s | ✅ |

原始产出留在 `E:\bim-toolkit\_survey\out*.md`，可复查。

### 1.2 过程中的三个工程教训

1. **首次给 WorkBuddy 的任务太宽**（"清点全部按钮 + lib 依赖 + 文档不一致"），它只回了一句
   "I'll start by exploring the extension structure." 就 exit 0 空转。改成**更小、更强制行动**的
   任务书（"你必须真的调用工具读文件，不要只回答我将会去探索"）后正常产出。
   → **经验：给外部 agent 的任务书要小、要有反空转指令。**
2. **三路并行 + 我自己的 shell 同时跑，把宿主机 Job runner 压垮了**（两个作业被强杀，
   `exit 1073807364`，无任何产物）。改为**串行**重发后正常。
   → **经验：外部 CLI 并行数要受控，别和高频本地命令叠加。**
3. **外部 AI 的结论必须回源**。本报告纠正了它们 3 处判断（见 §6.2）。

---

## 2. 资产测绘（现状）

### 2.1 全景

`E:\bim-toolkit` 是一个 git 仓库（81 次提交，**无 remote**），按学习阶段分目录：

| 目录 | 内容 | 成熟度 |
|---|---|---|
| `pyrevit-bim-panel/` | **主战场**：pyRevit 扩展源码 + `deploy.py` + `selftest.py` + `tests/` | 活跃 |
| `mcp/` | `bimtoolkit_mcp.py`（MCP 适配器）、`genbuild/`（配置驱动建模引擎）、`sync_bridge.py` | 活跃 |
| `revit-mcp/` | `revit_mcp_gateway.py`（标准 MCP server）、E2E 测试 | 活跃 |
| `bimtoolkit-release/` | 发布包副本 + `install.bat` | 半活跃 |
| `buildings/` | 6 份建筑 YAML 配方（云弦塔/仙湖别墅/北馨05/四层别墅…） | 活跃 |
| `_tools/dwgtrain/` | AUTOPILOT 自动巡航工作区（~1000 轮迭代、`_bak_round*` 备份） | **历史包袱** |
| `_train/` | YOLO 平面图识别训练集 + 10 个 `.pt` 权重 | 研究态 |
| `_research/` `_snapshots/` `_models/` `analysis/` | DWG 逆向、模型快照、分析产物 | 研究态 |

### 2.2 扩展本体：19 个按钮（README 只写了 7 个）

**面板一：`BIM 工具.panel`（12 个）**

| 按钮 | 行数 | 作用 | 只读/写 | 决定性证据 |
|---|---|---|---|---|
| 楼层清点 | 47 | 按楼层统计构件数 | 只读 | `script.py:29-32` 仅收集器 |
| 一键统计 | 50 | 顶层指标 + 类别 Top30 | 只读 | `script.py:28` `doc.GetWarnings()` |
| 批量改参 | 105 | 按 CSV 改参数 | **写**（确认闸门） | `script.py:87` `revit.Transaction` + `:84-86` 确认框 |
| 构件清单导出 | 93 | 导出 CSV/JSON/MD | 只读（写磁盘） | `script.py:35` 收集器 |
| BIM 体检 | 146 | 警告/族/参数化扫描 + 健康分 | 只读 | `lib/bimhealth.py:65` `doc.GetWarnings()` |
| 进度关联 | 157 | 进度计划 ↔ 构件 4D 对齐 | 只读 | `script.py:57`，无 Transaction |
| DWG翻模 | 490 | DWG→JSON→按规则翻模 | **写**（预览确认） | `script.py:398` → `lib/bimconv_build.py:189/199/205` |
| **翻模体检** | 408 | 列可疑构件、定位、**一键删除** | **写（会删构件）** | `script.py:262` `Transaction(…"翻模体检-删除可疑构件")` `:264` `doc.Delete` |
| 写入诊断 | 399 | 六组受控事务实验，建了再删 | **写** | `script.py:207` `Transaction` `:320` `doc.Delete` |
| AI 指令 | 450 | LLM 生成代码 → Notepad 预览 → 事务执行 | **写** | `script.py:391` `Transaction` `:395` `exec(code_text, ns)` |
| Create Villa | 242 | 原点建 10×8m 两层别墅 | **写** | `script.py:70` `Transaction` `:95` `Wall.Create` |
| MCP Bridge | 27（+3006 行核心） | 起 TCP 桥，对外提供 35 条命令 | **写（按需）** | `bridge_core.py:2314` `COMMAND_HANDLERS` |

**面板二：`AI 智建.panel`（7 个）**

| 按钮 | 作用 | 只读/写 | 实际后端 |
|---|---|---|---|
| 一键建模 | 从 `building_params.json` 建整栋楼 | 写 | `lib/model_builder.py`（规则） |
| 一键演示 | 建模 + 出图 + 体检一条龙 | 写 | `model_builder` + `standards_checker` + `bimhealth` |
| 工程量统计 | 算量 | 只读 | `lib/cost_estimator.py`（规则） |
| 施工方案 | 生成施工方案 | 只读 | `lib/standards_checker.py`（规则） |
| 规范审查 | 5 类规范检查 | 只读 | `lib/standards_checker.py`（规则） |
| 智能出图 | 批量创建平面图 | 写 | `script.py:76` `Transaction` |
| 自动标注 | 墙/门/窗自动标注 | 写 | `script.py:221/234/247` 三个 Transaction |

> **重要发现**：面板叫「AI 智建」，但 **7 个按钮里只有 0 个真的调用大模型**。
> 只有 `BIM 工具` 面板的「AI 指令」和 AutoCAD 侧的 `lib/acad_ai*.py` 真正调用了 DeepSeek。
> 全仓库 `llm_client` 引用只有 4 处（`lib/acad_ai.py:22`、`lib/acad_ai_gui.py:30`、
> `AI 指令/script.py:35`、`lib/llm_client.py` 自身）。

### 2.3 `lib/` 模块（25 个文件）

- **基础设施**：`bimlib.py`(9.3K)、`bimhealth.py`(10K)、`ipy_check.py`、`llm_client.py`(4.3K)
- **翻模引擎（最大块）**：`bimconv_walls.py`(**46.9K**)、`bimconv_build.py`(28.5K)、
  `bimconv_elements.py`(22.8K)、`dwg_to_json.py`(20.8K)、`bimconv_rules.py`(9.2K)、
  `bimconv_geom.py`(4.1K)、`bimconvert.py`(3.3K)
- **DWG 工具链**：`dwg_to_dxf.py`(12.8K)、`dwg_downver.py`(7.8K)
- **业务规则**：`standards_checker.py`(14.2K)、`cost_estimator.py`(5.4K)、`model_builder.py`(23.4K)、
  `door_codes.py`(5.6K)、`door_schedule.py`(6.0K)
- **批处理**：`batch_all.py`(7.3K)、`merge_csv.py`(3.0K)
- **CAD 侧 AI**：`acad_ai.py`(3.2K)、`acad_ai_gui.py`(6.4K)
- **配置**：`building_params.json`、`standards_quick.json`、**`deepseek_config.json`（含明文 Key）**

### 2.4 MCP 控制面：35 条命令

`bridge_core.py:2314-2350` 的 `COMMAND_HANDLERS` 恰好 35 条（已逐行清点）。其中
`WRITE_COMMANDS`（`:2365-2376`）20 条走 `ExternalEvent` 异步通道。**高权限三条**：

| 命令 | 行号 | 风险 |
|---|---|---|
| `execute_code` | `:777`（执行 `:838/:853`） | **任意 IronPython 代码，在 Revit 进程内执行** |
| `delete_elements` | `:1322` | 不可逆删除 |
| `save_document` | `:2046` | 可覆盖任意路径文档 |

链路：
```
外部 AI ──stdio JSON-RPC──> MCP Server（两个可选实现，均零第三方依赖）
                              ├ revit_mcp_gateway.py  :554
                              └ bimtoolkit_mcp.py     :295
        ──TCP 127.0.0.1:9877，换行 JSON──> bridge_core.py（Revit 进程内 IronPython 2.7）
                                          只读命令：Timer tick 内直接执行
                                          写命令：pending 队列 → ExternalEvent.Raise()
        ──Transaction.Commit──> Revit 模型
```

### 2.5 AI 接入面

`%USERPROFILE%\.workbuddy\mcp.json` 已注册两个 MCP server：

```json
"revit":      D:\Python\python.exe                          + E:/bim-toolkit/revit-mcp/revit_mcp_gateway.py
"bimtoolkit": ...\binaries\python\versions\3.13.12\python.exe + E:/bim-toolkit/mcp/bimtoolkit_mcp.py
```

两个脚本都**零第三方依赖**（纯标准库），所以哪怕解释器是空的也能跑。
反过来说，`D:\Python\python.exe` 里 `ezdxf/pythonnet/yaml` 一个都没有——它也只需要标准库。

### 2.6 环境事实

| 项 | 事实 |
|---|---|
| Revit | 2019，`D:\Autodesk\Revit 2019`（`RevitAPI.dll` 存在） |
| pyRevit | 6.5.5.26237+2044 @ `C:\Program Files\pyRevit-Master` |
| **pyRevit CPython 引擎** | **已安装** `bin\cengines\CPY3123\python.exe`（3.12.3） |
| pyRevit CLI | `bin\pyrevit.exe` 可用 |
| Xceed DLL 缺陷 | **已修复**（`bin\Xceed.Wpf.AvalonDock.dll` 在位） |
| 扩展安装位置 | `%APPDATA%\pyRevit\Extensions\BIMToolkit.extension` |
| Python 解释器 | **6 个**（见 §3.9） |

---

## 3. 关键问题清单

### P0 —— 必须优先处理

#### 3.1 明文 DeepSeek API Key 已进入 git

| 项 | 证据 |
|---|---|
| 密钥文件 | `pyrevit-bim-panel/BIMToolkit.extension/lib/deepseek_config.json:6` |
| 内容 | `"api_key": "sk-637d****（完整值已从本报告移除；该 Key 已在 S1 轮换）"` |
| 是否被 git 跟踪 | **是**。`git ls-files` 命中；引入于 commit `df81a3e` |
| `.gitignore` 是否排除 | **否**（`.gitignore` 只忽略 `*.log`、`version.txt`、`last-good/`、`_tools/`、`*.zip` 等） |
| remote | **无** → 尚未外泄到远端，但**已进入本地历史**，一旦 push/打包就会随之外流 |

`llm_client.py:44-46` 其实**已经支持** `DEEPSEEK_API_KEY` 环境变量且优先级更高，
所以这个文件里的密钥**完全没必要存在**。

#### 3.2 `execute_code` 全开 + token 明文 = Revit 进程内的任意代码执行

| 环节 | 证据 | 问题 |
|---|---|---|
| 开关已打开 | `MCP Bridge.pushbutton/mcp_bridge_config.json` → `{"allow_execute_code": true}` | 默认放行 |
| 检查点 | `bridge_core.py:778` | 只看这个布尔量 |
| 执行 | `bridge_core.py:838/853` `exec(code_text, ns)` | `ns` 含 `doc`/`uidoc`/`__revit__` + 全量 `Autodesk.Revit.DB.*`、`UI.*` |
| 载荷长度 | `bridge_core.py:781` `cmd.get("code","")` | **无长度限制、无内容过滤** |
| 监听 | `bridge_core.py:297-298` `HOST="127.0.0.1"` `PORT=9877` | ✅ 只绑 loopback |
| token | `bridge_core.py:266` 双 GUID = 64 hex | ✅ 熵足够 |
| token 存储 | `bridge_core.py:267-268` 明文写 `bridge.token`，与脚本同目录 | ⚠️ 无 ACL，同机任意进程可读 |
| 认证 | `bridge_core.py:2588` `cmd.get("token") == TOKEN` | 逻辑正确，但**读到文件即等于通过认证** |

**结论**：本机任意进程/用户，只要能读 `%APPDATA%\pyRevit\Extensions\...\bridge.token`
并连 `127.0.0.1:9877`，即可在 Revit 进程内执行任意 .NET 代码
（`clr.AddReference("System.IO")` → 文件/进程/网络全权限）。
这不是"理论风险"——**这是当前的实际配置**。

#### 3.3 三条部署路径语义互相冲突，且都会破坏运行时状态

| 路径 | 镜像方式 | 排除规则 | 运行时文件（token/审计/日志）命运 |
|---|---|---|---|
| `pyrevit-bim-panel/deploy.py:70` | `robocopy /MIR /NP /R:1 /W:1` | **无任何 `/XD` `/XF`** | **被删除**（`/MIR` 清掉目标独有文件） |
| `bimtoolkit-release/install.bat:80,82` | 先 `rmdir /s /q` 整体删除目标，再 `robocopy /E` | `/XD __pycache__ /XF *.log *.jsonl bridge.token` | **同样被删除**（rmdir 已先清空，排除规则形同虚设） |
| `mcp/sync_bridge.py:36-57` | 逐文件 `shutil.copyfile` | 仅 `__pycache__` | **保留**（只覆盖 pushbutton 目录，且带三副本哈希校验） |

被删的东西是**审计证据**：`mcp_audit.jsonl`（桥的审计流水）、`mcp_bridge.log`、
`logs_archive\`（历史归档）、`bridge.token`。前两者是排障与安全追溯的唯一凭据。
`deploy.py:86-103` 的 `_backup()` 会在镜像前把整个目标目录备份进 `last-good/`，
所以数据"没丢"，但**散落成 N 份时间戳快照**（且 `last-good/` 被 gitignore），
实际结果是**审计链断裂**。

另外：`deploy.py:33` 把 `version.txt` 写到**仓库目录**（`pyrevit-bim-panel/version.txt`），
**不写入安装目录** ⇒ 光看安装目录无法知道装的是哪一版。

#### 3.4 已部署副本落后于仓库，而桌面所有入口都指向已部署副本

| 项 | 值 |
|---|---|
| 仓库 `version.txt` | `20260911-211605  deploy  ba04b42`（**2026-09-11** 部署） |
| 当前 HEAD | `38d2c06`（2026-09-13） |
| 已部署 vs 仓库（`.py` 逐文件哈希） | **1 个内容不同 + 2 个只存在于仓库** |
| ↳ 内容不同 | `lib/dwg_to_json.py`：仓库 **20 812 B（v2.12 单文件隔离）** vs 已装 **15 766 B（v2.9 目录级批转）** |
| ↳ 只存在于仓库 | `lib/door_codes.py`、`lib/door_schedule.py` |

v2.12 的注释明确写着旧版的问题：

> `v2.12: 单文件隔离转换。旧版把 dwg 所在整个目录喂给 ODA 批转——实测 16 个 DWG（多个 5MB 级）
> 120s 超时跑不动 + .dwl 锁干挠，全线失败。改为把目标 DWG 复制进独立临时目录，转完取回（实测 1.2s/个）。`

**也就是说：仓库里已经修好的翻模性能缺陷，真机上跑的仍然是有缺陷的旧版。**
而 `DWG转JSON.bat`、`DWG降2010版.bat`、`CAD AI指令.bat`、`BIM批量导出.bat`
四个桌面入口，`%LIB%` 全部指向
`%USERPROFILE%\AppData\Roaming\pyRevit\Extensions\BIMToolkit.extension\lib`
——**用户实际执行的就是这份陈旧代码**。

> 漂移是**双向**的：commit `df81a3e` 的提交信息是
> "back-sync deployed extension … from %APPDATA% to E-source"，
> 曾把已部署副本**反向同步回仓库**。两条方向都发生过 ⇒ 不存在单一事实源。

### P1 —— 会造成实际损失

#### 3.5 自动测试只覆盖 19 个按钮中的 7 个，且有 3 处"永远通过"的假绿灯

`selftest.py` 共 5 关（`main()` 于 `:265-301`）：

| 关 | 行号 | 断言什么 | 关键缺陷 |
|---|---|---|---|
| ① 语法 | `:61-75` | 对全部 `.py` 做 `py_compile` | 用 **CPython 3** 编译，但生产是 **IronPython 2.7** ⇒ 假绿灯 |
| ② API 符号 | `:78-98` | 核对 21 个类 + 5 个 BIP 成员 | **假绿灯**：DLL 加载失败则返回 `None`，整关 SKIP 而退出码仍为 0（`:274-277`） |
| ③ 目录结构 | `:101-112` | 只查 `:102` 列出的 **7 个**按钮有无 `script.py`+`icon.png` | 实际 19 个；且只查存在性不查内容 |
| ④ IronPython 实跑 | `:115-135` | 跑 `tests\ironpython_harness.py` | harness `BUTTON_DIRS`（`:158-166`）也只 exec **7 个** |
| ⑤ 翻模转换器 | `:138-262` | `dwg_to_json --selftest` + `plan_from_json` 往返 | **假绿灯**：无 `ezdxf` 时打印 SKIP，`ok` 不变（`:150-154`），整关仍 PASS |

**未覆盖的 12 个按钮**：`MCP Bridge`（最重的 3006 行 bridge 完全在测试之外）、`AI 指令`、
`写入诊断`、`翻模体检`、`Create Villa`，以及 `AI 智建` 面板全部 7 个。

**测试死区**：harness 里 `TaskDialog.Show()` 恒返回 `Cancel`（`ironpython_harness.py:82-84`），
于是「批量改参」的 `if result == TaskDialogResult.Ok`（`批量改参/script.py:86`）**恒假**，
其后的 Transaction 写入代码（`:87-96`）**从未被执行过**。也就是说：
「安全闸门」这段最需要验证的逻辑，**恰好是测试永远碰不到的那段**。

#### 3.6 桥无超时、单线程；MCP 适配器会永久阻塞

- 桥是**单线程**（UI 线程 Timer tick），所有客户端共享一个 `clients` dict。
- 写命令串行排队（`bridge_core.py:2427`）。一个慢命令（`dwg_to_model` 客户端超时设 600 s）
  会阻塞其它客户端的只读查询。
- `bimtoolkit_mcp.py:245-252` `_read_line` 用无 deadline 的 `sock.recv` 循环，
  `call()`（`:266-272`）按 id 死等 ⇒ **桥挂起时客户端永久阻塞，无异常、无错误返回**。
- 桥侧异常被 armor 吞掉（`bridge_core.py:2382-2395`），且异常路径**跳过了 `_send`**
  ⇒ 写命令失败时客户端**一个字节都收不到**，只能等自己的超时。

#### 3.7 若干静默失败点

| 位置 | 场景 | 后果 |
|---|---|---|
| `bridge_core.py:244-245` / `:256-257` | `log()` / `audit()` 写失败 | `except: pass`，审计流水静默丢失 |
| `bridge_core.py:2382-2395` | ExternalEvent 内异常 | 写响应不发出，客户端干等 |
| `bridge_core.py:2567-2573` | `_send` 遇 wouldblock | 只 log，不标 dead，响应丢失但连接"活着" |
| `revit_mcp_gateway.py:163-164` | 响应 JSON 解析失败 | `continue` 静默丢行 |
| `Create Villa/script.py:117-118,128-129,141-142` | 门窗放置异常 | `except: pass` 全吞，用户只看到"已放置" |

#### 3.8 四份副本、同一个版本号

| 副本 | `.py` 数 | 文件总数 | 最新改动 | `extension.json` 版本 |
|---|---|---|---|---|
| `pyrevit-bim-panel/BIMToolkit.extension`（源码） | 42 | 153 | 2026-09-28 | **2.0.0** |
| `bimtoolkit-release/BIMToolkit.extension`（发布包） | 40 | 149 | 2026-09-12 | **2.0.0** |
| `%APPDATA%\...\BIMToolkit.extension`（实际加载） | 40 | 158 | 2026-09-28 | **2.0.0** |
| `last-good/…_20260911-211558`（备份） | 40 | 129 | 2026-09-11 | **2.0.0** |

**四个内容不同的副本全都自称 v2.0.0** ⇒ 版本号完全丧失标识作用。

#### 3.9 六个 Python 解释器，依赖分布不一

| 解释器 | 版本 | 关键依赖 |
|---|---|---|
| `E:\AI-pyenvs\bim-dev\Scripts\python.exe` | 3.13.14 | ezdxf, pythonnet, clr, win32com, PIL, openpyxl, pandas（**无 yaml**） |
| `%USERPROFILE%\.workbuddy\...\envs\default\Scripts\python.exe` | 3.13.14 | ezdxf, pythonnet, win32com, **yaml**, PIL, pandas, ultralytics |
| `%USERPROFILE%\.workbuddy\...\versions\3.13.12\python.exe` | 3.13.12 | **空** |
| `D:\Python\python.exe` | 3.14.4 | **空** |
| `E:\video-gen\venv\Scripts\python.exe` | 3.13.14 | ezdxf, win32com, yaml, PIL, ultralytics |
| `E:\py-tools\venv\Scripts\python.exe` | 3.13.14 | yaml, PIL |

- README 让用户用 `E:\AI-pyenvs\bim-dev` 跑 `selftest.py` / `deploy.py`；
  桌面 `.bat` 用的是 `.workbuddy\...\envs\default`；`.workbuddy/mcp.json` 用的是另外两个。
- **`genbuild` 需要 `yaml`**（`mcp/genbuild/spec.py:18`），而 README 指定的 `bim-dev`
  **恰好没装 PyYAML** ⇒ 按文档操作会直接 `ImportError`。
- `DWG翻模/script.py:51-57` 把解释器路径**硬编码成 6 个候选**（`%USERPROFILE%\...`、
  `D:\Python\python.exe`、`C:\Python39\python.exe`…），换机器必挂。

#### 3.10 文档严重滞后于代码

| 文档位置 | 文档说 | 实际是 |
|---|---|---|
| `pyrevit-bim-panel/README.md:63` | "按钮一览（**共 7 个**）" | **19 个** |
| `README.md:32` | "pyRevit 插件：一键统计/批量改参/楼层清点" | 多出 AI 指令、MCP Bridge、DWG翻模、翻模体检、写入诊断、Create Villa 及整个 AI 智建面板 |
| `README.md:179-185` | "不切 CPython3：**本机未装任何 CPython 引擎**" | **已装** `bin\cengines\CPY3123\python.exe`（3.12.3） |
| `README.md:160-166` | Xceed DLL 缺失是"已知缺陷" | **已修复**（`bin\` 里在位） |
| `README.md:33-34` | 阶段 4 `bim-health-check/` "⬜ 待开始" | `lib/bimhealth.py` + `BIM 体检` 已上线 |
| root `README.md:18` | "Dynamo 1.3 已装，`.addin` 待注册" | 未见后续印证 |

### P2 —— 能力与结构层面的缺口

#### 3.11 「AI 智建」名不副实

7 个按钮里 **0 个**调用大模型，全是确定性规则引擎（`standards_checker` / `cost_estimator` /
`model_builder`）。这个命名会让用户对结果产生错误的信任预期（以为做了智能判断，
实际是硬编码规则表）。全仓库真正调用 LLM 的只有「AI 指令」一个 Revit 按钮。

#### 3.12 最强的能力（genbuild）没有 UI 入口

`mcp/genbuild/` 是这套东西里**设计最好**的部分：YAML 配方 → 静态校验（干跑）→ 顺序发射桥命令，
带退出码语义（`cli.py:8`：0 成功/1 配方错误/2 桥错误/3 构建错误）。`buildings/` 下已有
6 份真实配方（云弦塔 33 K、四层别墅、仙湖别墅…）。

**但它只能命令行调用**（`gen_build.py <yaml>`），Revit 里**没有任何按钮能触达它**。
最好的能力被藏在最不为人知的入口后面。

#### 3.13 「翻模体检」命名与行为不符

名字是"体检"（暗示只读），实际能**删除构件**（`翻模体检/script.py:262-264`）。
好消息是有 Yes/No 确认闸门（`:254-257`）且可 Ctrl+Z，属于"有闸门的写"。
但 README 把它和 BIM 体检一起归入"只读"是**明确的错误描述**。

#### 3.14 `_tools/dwgtrain` 是巨型历史包袱

单目录含 `AUTOPILOT_LOG.md`（**2.58 MB**）、`AUTOPILOT_QUEUE.json`（917 KB）、
`AUTOPILOT.md`（578 KB）、近百个 `_bak_round*` 备份目录、数百个 `_dr*ay_*.md` 分片文档。
它是同济大学联合广场 DWG 翻模的自动巡航产物（`AUTOPILOT_REPORT.md` 记录到 round 173，
200 格验收：78 PASS / 105 N/A / 17 已裁 FAIL）。
**资产价值高**（沉淀了大量 Revit 建模配方），**但组织方式已不可维护**，且与主线代码无接口。

---

## 4. 完善与增强方案

> **A / B / C / D 是分类标签，不是执行顺序。** 真正的执行顺序见 **§5 单一顺序队列**。
> 本节只回答"有哪些事要做、每件怎么做"，不回答"先做哪件"。

### 方向 A：安全与合规收口（标签 A）

| # | 动作 | 具体做法 | 验收 |
|---|---|---|---|
| A1 | **轮换泄露的 Key** | 到 DeepSeek 控制台吊销 `sk-637d5a7f…`，签发新 Key | 旧 Key 调用返回 401 |
| A2 | **从 git 历史摘除** | `git filter-repo --path .../deepseek_config.json --invert-paths`（或 BFG）；`deepseek_config.json` 改用 `.example` 模板 + `.gitignore` | `git log --all -S "sk-637d5a7f"` 无输出 |
| A3 | **统一密钥来源** | 只走环境变量 `DEEPSEEK_API_KEY`（`llm_client.py:44-46` 已支持）；配置文件只留 `endpoint/model/temperature` | 文件内 grep 不到 `sk-` |
| A4 | **补齐 `.gitignore`** | 加 `bridge.token`、`mcp_audit.jsonl`、`mcp_crash.log`、`*.bak-*`、`deepseek_config.json` | `git status` 不再出现运行时文件 |
| A5 | **`execute_code` 默认关闭** | `mcp_bridge_config.json` 改 `false`；用的时候**显式**开 | 默认配置下 `execute_code` 返回错误 |
| A6 | **给 token 加 ACL** | `bridge.token` 写入后设 ACL 仅当前用户可读（`icacls`），或改为**每次启动新生成 + 只走命名管道** | 其他用户 `type bridge.token` 失败 |
| A7 | **加命令白名单分级** | 在 `bridge_core.py:2474` 分发前按 `readonly / write / dangerous` 三档做策略开关，`execute_code`+`delete_elements` 归 dangerous | 配置 `allow_dangerous=false` 时二者被拒 |

### 方向 B：交付与部署一致性（标签 B）

| # | 动作 | 具体做法 | 验收 |
|---|---|---|---|
| B1 | **只保留一条部署通道** | 废弃 `bimtoolkit-release/install.bat` 的部署职责（降级为"打包"）；统一走 `deploy.py`；`sync_bridge.py` 保留为**热修**通道 | 仓库内只有一处写 `%APPDATA%` 的逻辑 |
| B2 | **给 `/MIR` 加排除** | `deploy.py:70` 加 `/XD __pycache__ logs_archive /XF bridge.token mcp_audit.jsonl mcp_bridge.log mcp_crash.log *.bak-*` | 部署后审计日志仍在 |
| B3 | **版本写进安装目录** | `deploy.py:_write_version` 同时写 `DST/version.txt`；内容含 git short hash + 时间 + `extension.json` 版本 | 安装目录可直接读出所装版本 |
| B4 | **加部署后一致性自校验** | `deploy.py` 结尾逐文件哈希比对 SRC↔DST，不一致则 exit≠0（`sync_bridge.py:52-56` 已有同款逻辑，直接复用） | 人为制造差异时部署报错 |
| B5 | **给版本号注入 git 描述** | `extension.json` 的 `version` 由构建脚本写成 `2.0.0+38d2c06` 之类 | 四份副本版本号可区分 |
| B6 | **先补一次同步** | 跑一次修好的部署，把 `dwg_to_json.py` v2.12 与 `door_codes/door_schedule` 推上真机 | 已装副本与仓库哈希全等 |

### 方向 C：能力增强（标签 C，真正提升价值）

按「投入产出比」排序：

**C1. 把 genbuild 接进 Revit（最高价值，2~3 天）**
新增一个 `配置建模.pushbutton`：在 Revit 里选 `buildings/*.yaml` → 先干跑校验并展示问题清单
（复用 `spec.validate`）→ 确认后在 ExternalEvent 上下文里执行 `engine.execute`。
把 `buildings/` 6 份现成配方变成**点两下就能出楼**的能力，而不是敲命令行。
> 这一步同时也解决了方向 A 的 A5：有了正经入口，就不必靠 `execute_code` 直插代码。

**C2. 让「AI 智建」真的用上 AI（2~3 天）**
- 「规范审查」：规则引擎先给出**确定性判定**，再把「不通过项 + 模型上下文」喂给 LLM 生成
  **整改建议与依据条文**（规则负责"判"，AI 负责"解释与建议"），保留规则结论作为唯一裁决。
- 「施工方案」：现有 `standards_checker` 输出结构化事实，LLM 负责组织文本。
- 所有 LLM 输出**必须标注「AI 生成，需人复核」**，且不得回写模型。
- 或者，更诚实的选择：把面板改名为「规则审查」，另建一个「AI 助手」面板。

**C3. 补上测试最后一块短板（2~3 天）**
- 把 `selftest.py:102` 的 7 项 `expected` 与 harness `BUTTON_DIRS`（`:158-166`）
  从**手写清单**改为**遍历 `BIMToolkit.tab` 自动发现**，直接覆盖 19 个。
- 让 `TaskDialog` 桩支持**脚本化返回值**（先 Yes 再 No 各跑一遍），
  把「批量改参」确认后的写入代码（`批量改参/script.py:87-96`）纳入实跑。
- 把三处"永远通过"改成**显式失败**：DLL 加载失败、无 `ezdxf` 时应报 `FAIL` 而非 `SKIP`
  （至少给一个 `--strict` 模式，CI 用 strict）。
- 为 bridge 新增**纯逻辑单测**：把 `COMMAND_HANDLERS` 的 35 个命令在 mock DB 上跑一遍
  （现在 3006 行的 `bridge_core.py` 零测试覆盖）。

**C4. 翻模链路补齐（2~3 天）**
- 梁（README 明确写"本项目跳过"）——用 `bimconv_walls` 的成熟配方做结构梁。
- PDF 光栅图入口：接 `_train/` 的 YOLO 权重（已有 10 个 `.pt`）做平面图识别，
  产出 `model_plan.json` 喂给现有翻模按钮。这是**唯一能把"没有 CAD 只有 PDF"这个真实痛点**
  打通的路径，也是 `_train/` 那些权重的唯一出路。

**C5. 4D 进度从"盘点"走到"播放"（2 天）**
现在「进度关联」只做静态覆盖盘点。下一步：按 `计划开始/完成` 生成时间轴，
用 `apply_step`/`reset_view`（`bridge_core.py:2186/2247`）驱动构件显隐，产出 4D 动画帧。

**C6. 把 AI 指令从「生成代码」升级为「生成配置」（1~2 天）**
现在 `AI 指令` 让 LLM 直接写 IronPython 并 `exec`（`AI 指令/script.py:395`）——
这是最危险也最不可控的一条路。既然已有了 genbuild 的 YAML schema，
更稳的做法是让 LLM **只产出 YAML**，再由校验器把关后执行（对应 `docs/design_generic_builder.md`
里已论证过的"方案 C"）。表达力略降，安全性与可复核性大幅上升。

### 方向 D：工程化与可维护性（标签 D，持续）

| # | 动作 |
|---|---|
| D1 | 收敛 Python 解释器：README、桌面 `.bat`、`.workbuddy/mcp.json` 统一到**一个** venv；`DWG翻模/script.py:51-57` 的硬编码候选改为读配置 |
| D2 | 给 `bim-dev` 装上 PyYAML（否则 genbuild 按文档跑不通），或改用 `.workbuddy` 那个 env 并在文档里写明 |
| D3 | README 重写：19 个按钮、CPython 引擎现状、Xceed 已修复、阶段 4 已完成——一次性对齐 |
| D4 | `bridge_core.py` 的 `BRIDGE_VERSION`（`:210` = `7.11.0`）与代码注释里的 `v7.12.0`（`:825`）不一致，统一 |
| D5 | `_tools/dwgtrain` 归档：把 `_dr*ay_*.md` 合并成一份《Revit 建模配方沉淀》，`_bak_round*` 打包冷存，主干只留 `AUTOPILOT_QUEUE.json` 与最终报告 |
| D6 | 给仓库加 remote + CI：至少跑 `selftest.py --strict` 与 `mcp/tests/test_pure.py` |

---

## 5. 落地路线图（单一顺序队列）

> ### 执行进度（2026-10-01）
>
> | 序 | 项 | 状态 |
> |---|---|---|
> | S1 | 轮换 Key | ✅ 新 Key 生效；旧 Key 已吊销（HTTP 401） |
> | S2 | 清 git 历史 | ✅ `--replace-text` 外科替换；**扫过全部 82 个提交，零命中**；`.git` 319→87 MB |
> | S3 | 部署加排除 / 收敛通道 | ✅ |
> | S4 | 补同步部署 | ✅ |
> | S5 | 关 execute_code | ⏸ 并入 S12（原因见下） |
> | S6 | token ACL 收紧 | ✅ 仅当前用户可读，非继承 |
> | S7 | 测试覆盖 7→19 | ✅ 三处假绿灯已消灭；**测出 2 个真实缺陷** |
> | S8 | 版本留底 + 部署后自校验 | ✅ 安装目录可读版本；自校验 72 文件全等 |
> | S9 | 装 PyYAML | ✅ pyyaml 6.0.3；3 份真实配方校验通过（解除 S11 前置） |
> | S10 | README 重写 + 版本号统一 | ✅ 18 个按钮入档；`BRIDGE_VERSION` 7.11.0 → **7.12.4** |
> | S11 | genbuild 接入 Revit | ✅ 新增「配置建模」按钮（三段式：干跑 → 清场确认 → 构建）；19 个按钮 |
> | S12 | 命令分档 + 策略闸门 | ✅ 35 条分三档；`safe`/`build`/`dev` 策略；genbuild 开建前 fail-fast |
> | S13 | AI 真接入 + 静态护栏 | ✅ 规范审查「规则判 / AI 解释」；AI 指令执行前扫越界代码 |
> | S14 | 翻模补齐 | ⚠️ **梁 ✅**；**PDF/YOLO ❌ 经核实做不出来**（权重 mAP50 仅 0.26~0.32，且不含墙） |
> | S15 | 4D 播放 | ✅ 进度计划 → 导览任务树 → 逐步隔离取景出帧 |
> | S16 | 解释器契约 / 归档 / 本地 CI | ✅ 6 项契约可校验；dwgtrain 归档守恒；`tools/ci.ps1` 三关全绿 |
>
> ### 🏁 队列已走完
>
> **S1~S16 全部处理完毕，无遗留项。** S5 的目标已由 S12 达成。
> **三处方案经核实后纠正**（S12 / S13 / S14），均已在各自记录中说明原因与替代做法。
>
> 一句话总结这轮做了什么：**把一个"功能很猛但没人敢碰"的插件，
> 变成了一个有部署一致性、有安全边界、有 8 关自动门禁、文档与代码一致的工程件**。
>
> **两处方案纠正**：
> - **S12**：「移除 `execute_code` 依赖」不成立 —— genbuild 是 17 处 `execute_code`
>   （vs 7 处专用命令），清场/墙型定型/面层/验收在桥里无对应命令。改为**显式策略选择**。
> - **S13**：「AI 指令改产 YAML」不成立 —— 该按钮的用途是**任意模型编辑**
>   （改墙厚/只读参数/重载歧义），而 YAML 配方表达的是"整栋楼怎么建"。
>   改动它是**能力倒退**。改为**执行前静态护栏**（拦越界、放行本职）。
> - **S14**：「PDF/YOLO 翻模入口」**做不出来** —— 权重 mAP50 只有 0.26~0.32
>   （官方 val：P=0.847 但 **R=0.272**），最新版 v5 还比 v4 退化；
>   且模型只有 5 类、**不含墙**。缺的是数据与训练，不是代码。
>   已改为交付**可复测的评估工具与可用门槛**。>
> **附带成果**：`写入诊断` 按钮退役（S7 测出点击即崩，经决策删除）；
> 测试从 6 关扩到 **8 关**（新增命令分档覆盖校验 + 策略闸门行为测试），
> 且新增按钮**无需改任何测试**（S7 的自动发现生效）。测试现**全绿**（默认与 `--strict` 均 exit=0）。
>
> 明细见 [止血执行记录](止血执行记录-S3-A3-A4-S6.md)、
> [S2 清除 git 历史密钥记录](S2-清除git历史密钥记录.md)、
> [S7 测试覆盖记录](S7-测试覆盖扩展记录.md)、
> [S8 部署自校验与按钮退役记录](S8-部署自校验与按钮退役记录.md)、
> [S9/S10 依赖与文档校正记录](S9-S10-依赖与文档校正记录.md)、
> [S11 配置建模接入记录](S11-配置建模接入记录.md)、
> [S12 命令分档与策略闸门记录](S12-命令分档与策略闸门记录.md)、
> [S13 AI 接入与静态护栏记录](S13-AI接入与静态护栏记录.md)、
> [S14 翻模补齐记录](S14-翻模补齐记录.md)、
> [S15/S16 4D 播放与环境治理记录](S15-S16-4D播放与环境治理记录.md)。

> **§4 的 A/B/C/D 是分类标签，不是执行顺序。** 真正的顺序是下面这条队列：
> 从 S1 顺着做到 S16，每一步的前置都已排好，不需要回头。
>
> 只有两处**必须**跨方向提前，否则会撞车：
> - **D2（装 PyYAML）提到 C1 之前** —— `genbuild/spec.py:18 import yaml`，
>   而 README 指定的 `bim-dev` 环境没装，C1 会直接 `ImportError`。
> - **A5（关 `execute_code`）拆成两步** —— genbuild 的配方**本身就靠 `execute_code` 发射**。
>   所以先在 S5 把**默认值**改成关（挡住无意的外部调用），
>   等 S11 把 genbuild 接成正经入口后，再在 S12 才谈彻底移除这条依赖。

### 第一阶段：止血（第 1~2 天）—— 只堵洞，不动功能

| 序 | 动作 | 标签 | 为什么排在这 | 出口标准 |
|---|---|---|---|---|
| **S1** | 吊销并轮换 DeepSeek Key | A1 | 泄露已在先，轮换必须最先 | 旧 Key 返回 401 |
| **S2** | 清 git 历史 + 密钥只走环境变量 + 补 `.gitignore` | A2 A3 A4 | **必须在 S1 之后**：先换新 Key，清历史才有意义 | `git log --all -S "sk-637d5a7f"` 无输出 |
| **S3** | 收敛部署通道为一条 + 给 `/MIR` 加排除 | B1 B2 | **必须在 S4 之前**：否则补同步时会把审计日志删掉 | 仓库内只有一处写 `%APPDATA%` |
| **S4** | 补跑一次部署，把 `dwg_to_json.py` v2.12 与 `door_codes/door_schedule` 推上真机 | B6 | 依赖 S3 的排除规则 | 已装副本与仓库哈希全等 |
| **S5** | `mcp_bridge_config.json` 的 `allow_execute_code` 默认改 `false` | A5(上) | 只改默认值，不拆机制（见上方说明） | 默认配置下 `execute_code` 被拒 |
| **S6** | `bridge.token` 加 ACL（仅当前用户可读） | A6 | 独立项，随时可做 | 其他用户 `type bridge.token` 失败 |

### 第二阶段：可信（第 3~5 天）—— 先有安全网，再谈改功能

| 序 | 动作 | 标签 | 为什么排在这 | 出口标准 |
|---|---|---|---|---|
| **S7** | 测试覆盖 7→19 个按钮；三处"永远通过"改为显式失败；`TaskDialog` 桩支持脚本化返回值 | C3 | **必须在所有功能改动之前**——否则 S11~S13 全是盲改 | 19/19 进 harness；`--strict` 模式常绿 |
| **S8** | 版本写进安装目录 + 部署后哈希自校验 + 版本号注入 git hash | B3 B4 B5 | 依赖 S4 已同步、S2 已定格历史 | 安装目录可直接读出所装版本 |
| **S9** | 给目标环境装 PyYAML | **D2（提前）** | **C1 的前置**，见上方说明 | `python -c "import yaml"` 通过 |
| **S10** | README 重写 + `BRIDGE_VERSION` 与注释统一 | D3 D4 | 在功能变更前把现状描述校正 | 文档与代码一致 |

### 第三阶段：增值（第 2 周）—— 真正提升价值

| 序 | 动作 | 标签 | 为什么排在这 | 出口标准 |
|---|---|---|---|---|
| **S11** | 新增「配置建模」按钮，把 genbuild 接进 Revit（选 YAML → 干跑校验 → 确认执行） | C1 | 依赖 S9（yaml）、S7（测试网） | 点两下能从 `buildings/*.yaml` 出楼 |
| **S12** | 命令分三档 + 移除对 `execute_code` 的依赖 | **A7** | **必须在 S11 之后**：正经入口就位了才敢撤梯子 | `allow_dangerous=false` 时高危命令被拒 |
| **S13** | 「AI 智建」真接入 LLM（规则判、AI 解释）+ AI 指令改产 YAML 而非直接 exec | C2 C6 | 依赖 S11 定下的 YAML schema | LLM 输出均标注"需人复核"且不回写模型 |
| **S14** | 翻模补齐：结构梁 + PDF/YOLO 入口（复用 `_train/` 的 10 个权重） | C4 | 依赖 S7；与 S11~S13 可并行 | 「只有 PDF 没有 CAD」通路打通 |

### 第四阶段：演进（持续）

| 序 | 动作 | 标签 | 出口标准 |
|---|---|---|---|
| **S15** | 4D 进度从"静态盘点"走到"时间轴播放"（用 `apply_step`/`reset_view` 驱动显隐） | C5 | 产出 4D 动画帧 |
| **S16** | 解释器收敛为一个 + `_tools/dwgtrain` 归档 + 加 remote 与 CI | D1 D5 D6 | 单一 venv；历史包袱冷存；CI 常绿 |

### 一页速览

```
S1 换 Key ──► S2 清历史 ──► S3 部署收敛 ──► S4 补同步 ──► S5 关 execute_code 默认
                                                              │
S6 token ACL（可穿插）                                         ▼
                          S7 测试 7→19 ──► S8 版本可查 ──► S9 装 PyYAML ──► S10 文档校正
                                                              │
                                                              ▼
                          S11 genbuild 接 UI ──► S12 命令分档（撤 execute_code）──► S13 AI 真接入
                                                              │
                          S14 翻模补齐（可并行）◄───────────────┘
                                                              │
                                                              ▼
                          S15 4D 播放 ──► S16 解释器收敛 / 归档 / CI
```

**一句话执行原则**：**先止血（S1~S6）→ 再装安全网（S7）→ 才动功能（S11 起）**。
唯一允许打乱的是 S6 和 S14，它们不与任何一步冲突。

---

## 6. 结论可信度

### 6.1 已亲自回源核验（可直接采信）

- 19 个按钮的清单与读写属性 —— `grep Transaction|GetWarnings` + 逐文件抽读
- `COMMAND_HANDLERS` = **35** 条 —— `bridge_core.py:2314-2350` 逐行清点
- `WRITE_COMMANDS` 内容 —— `bridge_core.py:2365-2376` 通读
- `deploy.py` 的 `/MIR` 且**无排除规则** —— `deploy.py:70` 通读全文
- `install.bat` 先 `rmdir` 再 `robocopy /E` —— `install.bat:79-82` 通读
- `selftest.py` 的 5 关、7 项 `expected`（`:102`）、硬编码 DLL 路径（`:58`，**该路径真实存在**）
- `deepseek_config.json` **确实存在且被 git 跟踪** —— `git ls-files` + `git log -S`
- `version.txt` = `20260911-211605 deploy ba04b42`，HEAD = `38d2c06`
- 4 份副本的文件数/版本号差异 —— 逐目录统计
- 6 个解释器及其依赖分布 —— 逐个 `find_spec` 探测
- CPython 3.12.3 引擎**已安装** —— `bin\cengines\CPY3123\python.exe` 实测存在
- `DWG翻模/script.py:51-57` 硬编码解释器候选；`AI 指令/script.py:391-396` Transaction+exec

### 6.2 纠正的外部 AI 误判（3 处）

| 来源 | 它的结论 | 实际情况 |
|---|---|---|
| Claude | "`install.bat` 用 `/E` + 排除规则，**它保留运行时文件**" | ❌ **错**。`install.bat:80` 在此之前已 `rmdir /s /q` 整体删除目标目录，排除规则不起作用。**它同样会删运行时文件**。Claude 只看了 robocopy 参数，漏了上一行 |
| WorkBuddy | "`lib/deepseek_config.json` 不存在" | ❌ **错**。该文件存在（25 个 lib 文件之一），且**含明文 API Key 并被 git 跟踪**。这条误判会让最严重的安全问题被漏掉 |
| Qoder | "`%USERPROFILE%\.workbuddy\mcp.json` **无法读取**（权限被拒）" | ⚠️ 是**它自己的工具权限**受限，不是文件问题。DSH 侧可正常读取，内容已采信并核实 |

> 这 3 处正是 `_task.md` 里那条经验的实证：
> **"外部 AI 的输出一律不直接采信，最像模像样的那份也可能有错。"**

### 6.3 未核实（UNVERIFIED）

- 真机运行时行为：所有 `Transaction.Commit` 结果、`Wall.Create` 重载、
  `NewFootPrintRoof` 在 Revit 2019 的实际表现 —— 本次调查**全程只读，未启动 Revit**
- `bridge_core.py` 3006 行未逐行通读，仅核验了命令表、认证、分发、写入队列、异常处理等关键段
- `robocopy /MIR` 在文件被占用时（桥正在运行时）的精确行为 —— 代码层无法判定
- `mcp/genbuild/` 引擎与 `macros.py`（42.7 K）未深入审查
- `_tools/dwgtrain` 的 1000 轮配方细节未展开

---

## 附录 A：本次调查的产物

| 文件 | 内容 |
|---|---|
| `E:\bim-toolkit\_survey\T1b_按钮清点_WorkBuddy.md` | 给 WorkBuddy 的任务书 |
| `E:\bim-toolkit\_survey\T2_链路与安全_Qoder.md` | 给 Qoder 的任务书 |
| `E:\bim-toolkit\_survey\T3b_部署与测试_Claude.md` | 给 Claude 的任务书 |
| `E:\bim-toolkit\_survey\out1b_workbuddy.md` | WorkBuddy 原始产出（按钮清点） |
| `E:\bim-toolkit\_survey\out2_qoder.md` | Qoder 原始产出（链路与安全审计） |
| `E:\bim-toolkit\_survey\out3_claude.md` | Claude 原始产出（部署与测试复核） |
| `E:\bim-toolkit\docs\BIM工具箱-现状调查与增强方案.md` | **本报告** |

## 附录 B：关键证据索引（速查）

```
明文密钥          pyrevit-bim-panel/BIMToolkit.extension/lib/deepseek_config.json:6
远端代码执行      .../MCP Bridge.pushbutton/bridge_core.py:778 (开关) / :838,:853 (exec)
                  .../MCP Bridge.pushbutton/mcp_bridge_config.json  {"allow_execute_code": true}
命令表(35条)      .../MCP Bridge.pushbutton/bridge_core.py:2314-2350
写命令(20条)      .../MCP Bridge.pushbutton/bridge_core.py:2365-2376
监听/端口         .../MCP Bridge.pushbutton/bridge_core.py:297-298
token 生成/存储   .../MCP Bridge.pushbutton/bridge_core.py:266-268
认证比对          .../MCP Bridge.pushbutton/bridge_core.py:2588
危险部署          pyrevit-bim-panel/deploy.py:70            (robocopy /MIR，无排除)
危险部署          bimtoolkit-release/install.bat:80,82      (rmdir + robocopy /E)
版本留底          pyrevit-bim-panel/version.txt             (20260911-211605 deploy ba04b42)
测试覆盖面        pyrevit-bim-panel/selftest.py:102          (只有 7 个按钮)
测试假绿灯        pyrevit-bim-panel/selftest.py:274-277,150-154
测试死区          pyrevit-bim-panel/tests/ironpython_harness.py:82-84
翻模体检的写      .../BIM 工具.panel/翻模体检.pushbutton/script.py:262-264 (有确认闸门 :254-257)
AI 代码执行       .../BIM 工具.panel/AI 指令.pushbutton/script.py:391-396
硬编码解释器      .../BIM 工具.panel/DWG翻模.pushbutton/script.py:51-57
genbuild 入口     mcp/genbuild/cli.py:32-59                  (CLI only，无 UI)
```

---

*本报告由 DSH 编排三路外部 AI（WorkBuddy / Qoder CN / Claude Code）并行只读审计，
所有结论经 DSH 逐条回源核验后定稿。*
