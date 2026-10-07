# BIMToolkit —— pyRevit 扩展（Revit 2019）

挂在 Revit 2019 + pyRevit 6.5.5 里的工具箱：**19 个按钮 / 2 个面板**，
外加一个能把 Revit 交给外部 AI 操作的 **MCP 桥**。

> 新人或很久没碰？先看 **[上手文档](上手文档.md)**（安装 / 常用按钮 / 常见坑）。
>
> 本文件在 2026-10-01 重写过一次：此前它写的是"共 7 个按钮"，
> 而实际已有 19 个（多出的按钮从未进过文档）。

---

## 1. 这套东西能做什么

| 能力 | 入口 |
|---|---|
| 统计 / 清点 / 体检 / 清单导出（**只读**） | `BIM 工具` 面板 |
| 按 CSV 批量改参数（**默认只预览，确认才写**） | `批量改参` |
| DWG 图纸翻模成 Revit 构件（**预览确认才写**） | `DWG翻模` + `翻模体检` |
| 进度计划 ↔ 模型构件 4D 关联盘点（只读） | `进度关联` |
| **让外部 AI（Claude/Cursor/WorkBuddy）直接操作 Revit** | `MCP Bridge`（35 条命令） |
| 用自然语言让 LLM 写 IronPython 并在模型上执行 | `AI 指令` |
| 建模 / 出图 / 标注 / 算量 / 规范审查 | `AI 智建` 面板（7 个） |

> ⚠️ `AI 智建` 面板里，**判定逻辑全是确定性规则引擎**
> （`standards_checker` / `cost_estimator` / `model_builder`）——这是刻意的：
> 判定必须可复现、可追责。
> 其中**「规范审查」在规则判定之后追加一节 AI 解释**（`lib/ai_advisor.py`）：
> 规则负责判，AI 只负责把"哪条不达标"讲成整改建议与规范依据，
> **输出标注"AI 生成、需人复核"，且永不回写模型**。其余 6 个按钮不调大模型。
> 真正让 LLM **写代码**的只有 `AI 指令`（AutoCAD 侧另有 `acad_ai*`）。

---

## 2. 安装与生效

扩展装在 pyRevit 的用户扩展目录：

```
%APPDATA%\pyRevit\Extensions\BIMToolkit.extension\
```

源码（唯一编辑点）在仓库：

```
E:\bim-toolkit\pyrevit-bim-panel\BIMToolkit.extension\
```

**部署**（推荐，见 §6）：

```bash
E:\AI-pyenvs\bim-dev\Scripts\python.exe deploy.py
```

**生效方式**：重启 Revit（或 pyRevit 里 Reload），出现 **BIMToolkit → BIM 工具 / AI 智建** 两个面板。

---

## 3. 按钮一览（19 个）

### 3.1 `BIM 工具.panel`（12 个）

| 按钮 | 行数 | 作用 | 安全性 |
|---|---|---|---|
| 楼层清点 | 47 | 按楼层汇总构件数量，输出 HTML 表 | 只读 |
| 一键统计 | 50 | 构件总数/楼层/视图/图纸/族/警告 + 类别 Top30 | 只读 |
| 构件清单导出 | 93 | 导出全部构件 CSV/JSON/MD（4D 关联底表） | 只读（写磁盘） |
| **BIM 体检** | 146 | 警告/错误扫描 + 族体检 + 参数化体检 + 综合健康分 | 只读 |
| **进度关联** | 157 | 进度计划 ↔ 构件按 楼层/类别/类型 对齐，出覆盖率/缺漏/游离 | 只读 |
| 批量改参 | 105 | 按 `batch_params.csv` 改参数 | **默认只预览**，确认才写（可 Ctrl+Z） |
| DWG翻模 | 490 | `model_plan.json` → 轴网/墙/柱/门/窗/房/板 | **预览确认才写**；**已有墙的文档硬阻断** |
| 翻模体检 | 408 | 列可疑构件、定位、可一键删除 | **写**（删除有 Yes/No 确认，可 Ctrl+Z） |
| AI 指令 | 450 | LLM 生成 IronPython → Notepad 预览 → 确认后在事务里执行 | **写** |
| Create Villa | 242 | 原点建 10×8m 两层别墅（演示用） | **写** |
| **MCP Bridge** | 27（+ `bridge_core.py` 3006 行） | 起 TCP 桥，对外提供 35 条命令 | **按需写**（见 §4） |
| **配置建模** | 272 | 选 `buildings/*.yaml` → 干跑校验 → 清场确认 → genbuild 构建 | **写**（**会先清场**，见 §9） |

### 3.2 `AI 智建.panel`（7 个）

| 按钮 | 行数 | 作用 | 安全性 |
|---|---|---|---|
| 一键建模 | 130 | 读 `lib/building_params.json` 建整栋楼 | 写 |
| 一键演示 | 302 | 建模 + 出图 + 体检 一条龙 | 写 |
| 工程量统计 | 82 | 算量（`cost_estimator`） | 只读 |
| 规范审查 | 117 | 5 类规范检查（防火/抗震/节能/结构/面积）+ **AI 整改建议**（可选、可失败） | 只读，AI 建议不回写模型 |
| 施工方案 | 217 | 生成施工方案文本 | 只读 |
| 智能出图 | 142 | 批量创建平面视图 | 写 |
| 自动标注 | 279 | 墙/门/窗自动标注 | 写 |

> **新增按钮不需要改测试**：`selftest.py` 第 3 关与 `tests/ironpython_harness.py`
> 会自动发现 `BIMToolkit.tab` 下所有带 `script.py` 的 `.pushbutton`。

---

## 4. MCP Bridge —— 让外部 AI 直接操作 Revit

这是本扩展最有价值、也最需要小心的部分。

```
外部 AI(Claude/Cursor/WorkBuddy)
   │ stdio JSON-RPC
   ▼
MCP Server（二选一，均零第三方依赖）
   ├ revit-mcp/revit_mcp_gateway.py
   └ mcp/bimtoolkit_mcp.py
   │ TCP 127.0.0.1:9877，换行 JSON，UTF-8
   ▼
bridge_core.py（Revit 进程内 IronPython 2.7）
   只读命令：UI 线程 Timer tick 内直接执行
   写命令：pending 队列 → ExternalEvent.Raise() → Transaction
```

- **35 条命令**（`bridge_core.py` 的 `COMMAND_HANDLERS`），其中 20 条写命令走 ExternalEvent。
- 监听 **仅本机** `127.0.0.1:9877`（硬编码）。
- 认证：`bridge.token`（64 hex，双 GUID），明文存在按钮目录。**部署脚本会把它收紧为"仅当前用户可读"**。
- `ping` 会回报 `BRIDGE_VERSION`，可用来判断已装的是哪一版。

### 三条高权限命令（务必知情）

| 命令 | 风险 |
|---|---|
| `execute_code` | **在 Revit 进程内执行任意 IronPython**（可 `clr.AddReference` 加载任意 .NET 程序集 → 等同该用户的完全代码执行） |
| `delete_elements` | 不可逆删除 |
| `save_document` | 可覆盖任意路径文档 |

### 命令分档与策略（v7.13.0）

桥把 35 条命令分三档，由 `mcp_bridge_config.json` 的 `policy` 控制：

| 档 | 命令数 | 内容 |
|---|---|---|
| `read` | 17 | `ping` / `get_*` / `query_elements` / `get_quantities` / `run_report` 等 |
| `write` | 16 | `create_*` / `set_parameters` / `set_material` / `save_document` 等 |
| `dangerous` | 2 | `execute_code`、`delete_elements` |

| 策略 | 允许档 |
|---|---|
| `safe` | read |
| `build` | read + write |
| `dev` | read + write + dangerous |

```json
{ "policy": "dev", "allow_execute_code": true }
```

- **当前是 `dev`**，因为 genbuild（§9 配置建模）的配方是 17 处 `execute_code`，
  其中清场/墙型定型/面层/验收在桥里**没有对应命令**，撤不掉。详见 §10。
- 不用 genbuild 时，把 `policy` 降到 `build` 或 `safe` 即可 —— 改完**必须重新点一次
  MCP Bridge 按钮**才生效。
- `ping` 会回报当前 `policy`；genbuild 在开建前先查它，**在开建之前**就拒绝并给出修法，
  而不是建到一半才失败。
- 未分档的命令在**任何**策略下都被拒（fail-closed）；
  第 7/8 关会机械校验分档表覆盖完整、且闸门行为符合预期。
- 旧的 `allow_execute_code` 仍被识别：配置里没写 `policy` 时按它推导（true→dev，false→build）。

---

## 5. AI 指令（LLM 生成代码）

流程：读模型上下文 → 弹输入框 → 调 DeepSeek → 提取 ```python 代码块
→ **静态护栏扫描** → Notepad 预览 + 确认 → 在名为「AI 指令」的 Transaction 里 `exec`。

### 执行前的静态护栏（`lib/ai_code_guard.py`）

LLM 生成的代码在**执行前**会被扫一遍：

| 级别 | 命中内容 | 处理 |
|---|---|---|
| **阻断** | `open()` 文件读写、`__import__`、`import os/sys/subprocess/socket/…`、`clr.AddReference`、`System.IO/.Diagnostics/.Net/.Reflection`、`eval/exec/compile`、启动进程 | **拒绝执行**，并说明越界在哪 |
| **告警** | `doc.Delete()`、`SaveAs`/`Save`、`doc.Close`、自己开 `Transaction`、`TaskDialog`/`MessageBox`、`PickObject` | 在确认框里显著列出，仍可执行 |

**为什么是"收窄"而不是"砍掉"**：`AI 指令` 的价值就是"对模型做任意编辑"
（如"把所有标高1的墙厚度改成 200mm"）。把它改成只能产出 genbuild 的 YAML
是**能力倒退**——YAML 表达的是"整栋楼怎么建"，表达不了"改现有构件"。
所以这里只限制**越出操作模型**的那部分能力。

> 局限：这是文本模式匹配，不是沙箱。它能挡住明显的越界意图，
> 挡不住刻意绕过的写法。真正的隔离要靠进程/权限边界。

**密钥来源**（`lib/llm_client.py`，优先级从高到低）：

1. 环境变量 `DEEPSEEK_API_KEY` ← **推荐，也是当前的配置方式**
2. `lib/deepseek_config.json` 的 `api_key` 字段 ← 该文件**已被 `.gitignore` 排除**，勿再填真钥

> 2026-10-01：此前 `deepseek_config.json` 里的真实 API Key 曾被提交进 git（commit `df81a3e`）。
> 文件层已清干净、密钥已轮换，且新增 `deepseek_config.json.example` 作为模板。

---

## 6. 部署与运维

### 6.1 一键部署（`deploy.py`，五步）

```bash
E:\AI-pyenvs\bim-dev\Scripts\python.exe deploy.py            # 正式部署
E:\AI-pyenvs\bim-dev\Scripts\python.exe deploy.py --dry      # 干跑预览，不写盘
E:\AI-pyenvs\bim-dev\Scripts\python.exe deploy.py --no-tag   # 不打 git 标签
```

1. **备份**当前已装版本到 `last-good/BIMToolkit.extension_<时间戳>/`
2. **镜像** `robocopy /MIR`，并排除运行时状态：
   `__pycache__`、`logs_archive\`、`bridge.token`、`mcp_audit.jsonl`、
   `mcp_bridge.log`、`mcp_crash.log`、`*.bak-*`
   → **不加排除会把审计流水删掉**（2026-10-01 修）
3. **收紧** `bridge.token` 的 ACL 为"仅当前用户可读"
4. **写版本留底**：仓库 + **安装目录**都写 `version.txt`
   （格式 `时间  deploy  短commit  v版本`）
5. **部署后自校验**：逐文件 MD5 比对 SRC↔DST，不一致则 **exit=1 且不打标签**；
   通过后打 `deploy-<时间戳>` git 标签

> 本脚本是**开发机上的唯一部署通道**。
> `bimtoolkit-release\install.bat` 是给全新机器的安装器，
> **两者的排除表必须保持一致**（见各自文件里的注释）。

### 6.2 已装版本怎么看

```bash
type "%APPDATA%\pyRevit\Extensions\BIMToolkit.extension\version.txt"
# 20261001-180642  deploy  38d2c06  v2.0.0
```

### 6.3 pyRevit 缺 DLL（历史上踩过，现已修复）

若 Revit 启动报缺 `Xceed.Wpf.AvalonDock, Version=2.0.19.10`：这是 pyRevit 安装包打包缺陷
（`bin\` 没带该 DLL）。先跑 `fix_pyrevit_dll.bat`（只读检测，不要管理员权限）确认哪几个引擎目录缺，
再自己取一份正确版本交给脚本：`fix_pyrevit_dll.bat "<那份 dll 的完整路径>"`。
**仓库不内置这颗 dll**——它是 Xceed 的第三方程序集，公开分发要带许可原文，理由与校验值见
`tools\README.md`。脚本会核对程序集版本号，对不上直接拒绝（Revit 2019 自带的是 2.0.19.4，不是这一版）。

> 本机 2026-10-01 实测：`C:\Program Files\pyRevit-Master\bin\Xceed.Wpf.AvalonDock.dll` **已在位**。

---

## 7. Headless 校验（不需要 Revit GUI）

```bash
E:\AI-pyenvs\bim-dev\Scripts\python.exe selftest.py            # 常规，exit=0
E:\AI-pyenvs\bim-dev\Scripts\python.exe selftest.py --strict   # 严格：SKIP 一律 FAIL
```

**八关**：

| 关 | 内容 | 说明 |
|---|---|---|
| 1 | 语法（`py_compile` 全部 `.py`） | |
| 2 | Revit API 符号 | pythonnet 加载**真实** `D:/Autodesk/Revit 2019/RevitAPI.dll` 核对 |
| 3 | 目录结构 | **自动发现**每个 `.pushbutton` 都要有 `script.py` + `icon.png` |
| 4 | IronPython 2.7 实跑 | `tests/ironpython_harness.py`，**覆盖全部 19 个按钮脚本** |
| 5 | 翻模转换器 | `dwg_to_json --selftest` + `bimconvert.plan_from_json` 往返 |
| 6 | 部署自校验器的自测 | 确认 `deploy._verify_mirror` 真能抓出不一致 |
| 7 | 命令分档表覆盖 | `COMMAND_TIERS` 恰好覆盖 `COMMAND_HANDLERS`（含自测） |
| 8 | 策略闸门行为 | 在桩环境 exec 真实 `bridge_core.py`，断言放行/拒绝矩阵 |

**`--strict` 的意义**：默认模式下"依赖缺失导致的 SKIP"不算失败；
`--strict` 下一律 FAIL。做门禁时用 `--strict`。

### 测试台的能力边界（重要）

第 4 关证明的是**「脚本在 IronPython 2.7 下能跑起来」**，**不是**「业务逻辑正确」。
它用了"要什么给什么"的宽松桩（`clr` / `System.*` / WinForms），
每轮结束会**分类打印被自动补齐的符号**：

- `Autodesk.*` —— 被测域，必须人工对照真实 API（第 2 关只校验白名单）
- `pyrevit.*` —— mock 未覆盖
- `System.*` / `clr` —— 宿主管道，非被测对象

测试台**绝不发真实网络请求**（启动即摘掉 `DEEPSEEK_API_KEY`）。

```bash
# 单跑测试台
E:\AI-pyenvs\bim-dev\Scripts\python.exe tests\ironpython_harness.py
```

---

## 8. 踩坑与排错

### 8.1 IronPython 2.7 不兼容（Revit 2019 的 pyRevit 跑的仍是 IronPython 2.7）

| 错误写法（会崩） | 兼容写法 |
|---|---|
| `open(path, "w", newline="", encoding="utf-8-sig")` | `write_csv_unicode(path, headers, rows)` |
| `list(csv.DictReader(open(path, encoding="utf-8-sig")))` | `read_csv_unicode(path)` |
| `collector.Count` | `len(to_element_list(collector))` |
| `list(collector)` / `for e in collector` | `to_element_list(collector)` |
| f-string | `.format()` / `%` |

### 8.2 引擎决策：为什么还留在 IronPython 2.7

2026-09-04 的评估列了 5 条理由，其中**第 ① 条已经过时**：

> ① 本机未装任何 CPython 引擎、也无 `pythonnet`，切引擎需先 `pyrevit engines install cpython`

**2026-10-01 实测：`C:\Program Files\pyRevit-Master\bin\cengines\CPY3123\python.exe` 已存在**
（CPython 3.12.3）。所以"装不了"不再是障碍，这条决策**值得重新评估**。

其余理由仍然成立：pyRevit 的 CPython 引擎对 `pyrevit.forms` / WPF 支持较弱，
而多个按钮依赖 `forms` 做"确认才写入"的安全闸门。

### 8.3 扫描异常不再静默成 0

各 scan 函数用 `try/except` 兜底，但真机抛异常会把错误写进结果的 `error` 字段，
`BIM 体检` 输出窗顶部显示「⚠️ 扫描异常提示」，不再把崩溃伪装成「0/空」。

---

## 9. 配置驱动建模（genbuild）

`mcp/genbuild/` 是"一份 YAML 描述建筑 → 引擎校验后发射桥命令"的引擎。
`buildings/` 下有 6 份真实配方（云弦塔 / 仙湖别墅 / 北馨05 / 四层别墅…）。

### 9.1 在 Revit 里用（推荐）

点 **`配置建模`** 按钮 → 选配方 → 三段式流程：

```
【1】干跑：静态校验（开洞必须落墙、梯段反算、层高一致性）+ ping 桥
        不通过就停在这里，模型一个字节都不动
【2】清场确认：列出本次**实际**会被删除的类别（从引擎现读，不硬编码，避免漂移）
        用户点「是」才继续
【3】正式构建：清场 + 标高 → 轴网 → 墙 → 板 → 楼梯 → 屋顶 → 栏杆 → 门窗 → 材质
```

> ⚠️ `genbuild.config.WIPE_CATS` 里的类别（墙/门/窗/楼板/屋顶/楼梯/轴网/柱…）
> 会被**删除后重建**。这是配置驱动建模的设计行为。建议先存盘或另存副本。

路径由按钮目录下的 `genbuild_config.json` 指定（`repo_root` / `python` / `recipes_dir`）：

```json
{ "repo_root": "E:/bim-toolkit", "python": "", "recipes_dir": "buildings" }
```

`python` 留空则自动探测（候选表第一个可用且装了 PyYAML 的解释器）。

### 9.2 命令行直接用

```bash
E:\AI-pyenvs\bim-dev\Scripts\python.exe E:\bim-toolkit\mcp\gen_build.py buildings\四层别墅.yaml --dry
```

退出码：`0` 成功 / `1` 配方错误 / `2` 桥错误 / `3` 构建错误 / `4` 验收未达标。

- **依赖 PyYAML**（`genbuild/spec.py`）。2026-10-01 已装进 `bim-dev`（pyyaml 6.0.3）。
- `--dry` 只做静态校验 + ping 桥，**需要 Revit 里先点开 MCP Bridge**。
- **为什么按钮走外部子进程**：引擎是 CPython 3 + PyYAML，
  而 Revit 2019 的 pyRevit 是 IronPython 2.7（无 yaml）。
  与 `DWG翻模` 把 ezdxf 的活丢给外部 CPython 是同一套路。

---

## 10. 已知问题与遗留

| 项 | 说明 |
|---|---|
| `lib/door_codes.py` / `lib/door_schedule.py` | **死代码**：全仓库无任何文件 import 它们（2026-10-01 核实） |
| genbuild 依赖 `dangerous` 档 | 17 处 `execute_code`（清场/墙型定型/面层/验收在桥里无对应命令）。"撤掉该依赖"= 把 17 个宏搬成桥命令并解冻 `bridge_core.py`，是另一个量级的重构，且与"配方走 execute_code、桥不再改"的既定架构冲突。**现改为显式可审计的策略选择**，见 §4 |
| `AI 智建` 面板命名 | 7 个按钮里只有「规范审查」会调 LLM（且只用于**解释**，判定仍是规则引擎）；其余 6 个是纯规则。面板名容易让人以为 7 个都"智能判断" |
| `AI 指令` 的护栏是模式匹配 | 能挡明显越界，挡不住刻意绕过；不是沙箱 |
| 桌面 `.bat` 入口 | `DWG转JSON.bat` / `DWG降2010版.bat` / `CAD AI指令.bat` / `BIM批量导出.bat` 都指向**安装目录**的 `lib\`，即部署后的副本 |
